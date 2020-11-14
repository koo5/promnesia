#!/usr/bin/python3
__package__ = 'promnesia'  # ugh. hacky way to make hug work properly...

import os
import sys
import json
from datetime import timedelta, datetime
from pathlib import Path
import logging
from functools import lru_cache
from typing import Collection, List, NamedTuple, Dict, Optional, Any, Tuple

from cachew import NTBinder

import pytz
from pytz.tzinfo import BaseTzInfo # type: ignore

import hug # type: ignore
import hug.types as T # type: ignore

from sqlalchemy import create_engine, MetaData, exists, literal, between, or_, and_, exc # type: ignore
from sqlalchemy import Column, Table, func, types # type: ignore
from sqlalchemy.sql import text # type: ignore


from .common import PathWithMtime, DbVisit, Url, Loc, setup_logger, PathIsh, default_output_dir, python3, get_system_zone
from .cannon import canonify

_ENV_CONFIG = 'PROMNESIA_CONFIG'


# TODO not sure about utc in database... keep orig timezone?

# meh. need this since I don't have hooks in hug to initialize logging properly..
@lru_cache(1)
def get_logger():
    # NOTE: uncomment to log sql queries
    # logging.basicConfig()
    # logging.getLogger('sqlalchemy.engine').setLevel(logging.DEBUG)

    # todo lazy log?
    logger = logging.getLogger('promnesia.server')
    setup_logger(logger, level=logging.DEBUG)
    return logger


def get_version() -> str:
    from pkg_resources import get_distribution
    return get_distribution(__package__).version


class ServerConfig(NamedTuple):
    db: Path
    timezone: BaseTzInfo

    @classmethod
    def make(cls, timezone: str, db: Optional[PathIsh]=None) -> 'ServerConfig':
        tz = pytz.timezone(timezone)

        if db is None:
            dbp = default_db_path()
        else:
            dbp = Path(db)

        return cls(db=dbp, timezone=tz)


@lru_cache(1)
def get_config() -> ServerConfig:
    cfg = os.environ.get(_ENV_CONFIG)
    assert cfg is not None
    return ServerConfig.make(**json.loads(cfg))


# TODO use that?? https://github.com/timothycrosley/hug/blob/develop/tests/test_async.py

# TODO how to return exception in error?

def as_json(v: DbVisit) -> Dict:
    # yep, this is NOT %Y-%m-%d as is seems to be the only format with timezone that Date.parse in JS accepts. Just forget it.
    dts = v.dt.strftime('%d %b %Y %H:%M:%S %z')
    loc = v.locator
    # TODO is locator always present??
    return {
        # TODO do not display year if it's current year??
        'dt': dts,
        # TODO the frontend had some bug with handling empty string as src. fix that later
        'src': v.src or 'unnamed',
        'context': v.context,
        'duration': v.duration,
        'locator': {
            'title': loc.title,
            'href' : loc.href,
        },
        'original_url'  : v.orig_url,
        'normalised_url': v.norm_url,
    }


def get_db_path_nothrow() -> Path:
    return get_config().db

def get_db_path() -> Path:
    db = get_db_path_nothrow()
    assert db.exists(), db
    return db


# TODO maybe, keep db connection? need to recycle it properly..
@lru_cache(1)
# PathWithMtime aids lru_cache in reloading the sqlalchemy binder
def _get_stuff(db_path: PathWithMtime):
    get_logger().info('Reloading DB: %s', db_path)
    # TODO how to open read only?
    engine = create_engine(f'sqlite:///{db_path.path}') # , echo=True)

    binder = NTBinder.make(DbVisit)

    meta = MetaData(engine)
    table = Table('visits', meta, *binder.columns)

    return engine, binder, table


def get_stuff(db_path=None): # TODO better name
    # ok, it will always load from the same db file; but intermediate would be kinda an optional dump.
    if db_path is None:
        db_path = get_db_path()
    return _get_stuff(PathWithMtime.make(db_path))



def search_common(url: str, where):
    logger = get_logger()
    config = get_config()

    logger.info('url: %s', url)
    original_url = url
    url = canonify(url)
    logger.info('normalised url: %s', url)

    visits0: List[Any] = []

    result = {
        'orginal_url'   : original_url,
        'normalised_url': url,
        'visits': visits0
    }

    engine, binder, table = get_stuff()

    query = table.select().where(where(table=table, url=url))
    logger.debug('query: %s', query)

    with engine.connect() as conn:
        try:
            # TODO make more defensive here
            visits = [binder.from_row(row) for row in conn.execute(query)]
        except exc.OperationalError as e:
            if getattr(e, 'msg', None) == 'no such table: visits':
                logger.warn('you may have to run indexer first!')
                #result['visits'] = [{an error with a msg}] # TODO
                #return result
            raise

    logger.debug('got %d visits from db', len(visits))

    vlist: List[DbVisit] = []
    for vis in visits:
        dt = vis.dt
        if dt.tzinfo is None:
            tz = config.timezone
            dt = tz.localize(dt)
            vis = vis._replace(dt=dt)
        vlist.append(vis)

    logger.debug('responding with %d visits', len(vlist))
    # TODO respond with normalised result, then frontent could choose how to present children/siblings/whatever?
    result['visits'] = list(map(as_json, vlist))
    return result


@hug.local()
@hug.get ('/status')
# NOTE: not sure why I used post in the first place... but it was used in the extension so need to keep
@hug.post('/status')
def status():
    '''
    Ideally, status will always respond, regardless the internal state of the backend?
    '''
    # TODO hug stats?

    db_status_msg = 'ok'

    db_path: Optional[str]
    try:
        db_path = str(get_db_path())
        # TODO use 'db_stats' instead? add count or something else
    except Exception as e:
        # TODO not sure how to properly communicate the error to frontend?
        db_path = None
        db_status_msg = f'Database file not found (or unreadable): "{get_db_path_nothrow()}". Run indexer.'

    version: Optional[str]
    try:
        version = get_version()
    except Exception as e:
        version = None

    return {
        'db'     : db_path,
        'version': version,
        'db_status_msg': db_status_msg,
    }
# TODO might be good to include the frontend version in the requests?


@hug.local()
@hug.post('/visits')
def visits(
        url: T.text,
):
    return search_common(
        url=url,
        # odd, doesn't work just with: x or (y and z)
        where=lambda table, url: or_(
            table.c.norm_url == url,  # exact match
            and_(table.c.context != None, table.c.norm_url.startswith(url, autoescape=True)) # + child visits, but only 'interesting' ones
        ),
    )


@hug.local()
@hug.post('/search')
def search(
        url: T.text
):
    # TODO rely on hug logger for query
    return search_common(
        url=url,
        where=lambda table, url: or_(
            # todo hmm. think about it, not sure if I need proper indexer for fuzzy search etc?
            table.c.norm_url     .contains(url, autoescape=True),
            table.c.orig_url     .contains(url, autoescape=True),
            table.c.context      .contains(url, autoescape=True),
            table.c.locator_title.contains(url, autoescape=True),
        ),
    )


@hug.local()
@hug.post('/search_around')
def search_around(
        timestamp: T.number,
):
    utc_timestamp = timestamp # old 'timestamp' name is legacy

    # TODO meh. use count/pagination instead?
    delta_back  = timedelta(hours=3  ).total_seconds()
    delta_front = timedelta(minutes=2).total_seconds()
    # TODO not sure about delta_front.. but it also serves as quick hack to accomodate for all the truncations etc

    return search_common(
        url='http://dummy.org', # NOTE: not used in the where query (below).. perhaps need to get rid of this
        where=lambda table, url: between(
            func.strftime(
                '%s', # NOTE: it's tz aware, e.g. would distinguish +05:00 vs -03:00
                # this is a bit fragile, relies on cachew internal timestamp format, e.g.
                # 2020-11-10T06:13:03.196376+00:00 Europe/London
                func.substr(
                    table.c.dt,
                    1, # substr is 1-indexed
                    # instr finds the first match, but if not found it defaults to 0.. which we hack by concatting with ' '
                    func.instr(func.cast(table.c.dt, types.Unicode).op('||')(' '), ' ') - 1,
                    # for fucks sake.. seems that cast is necessary otherwise it tries to treat ' ' as datetime???
                )
            ) - literal(utc_timestamp),
            literal(-delta_back),
            literal(delta_front),
        ),
    )

# before 0.11.14 (including), extension didn't share the version
# so if it's not shared, assume that version
_NO_VERSION = (0, 11, 14)
_LATEST = (9999, 9999, 9999)

def as_version(version: str) -> Tuple[int, int, int]:
    if version == '':
        return _NO_VERSION
    try:
        [v1, v2, v3] = map(int, version.split('.'))
        return (v1, v2, v3)
    except Exception as e:
        logger = get_logger()
        logger.error('error while parsing version %s', version)
        logger.exception(e)
        return _LATEST


@hug.local()
@hug.post('/visited')
def visited(
        urls,
        client_version: str='',
):
    logger = get_logger()
    version = as_version(client_version)

    nurls = [canonify(u) for u in urls]
    engine, binder, table = get_stuff()

    snurls = list(sorted(set(nurls)))
    # sqlalchemy doesn't seem to support SELECT FROM (VALUES (...)) in its api
    # also doesn't support array binding...
    # https://stackoverflow.com/questions/13190392/how-can-i-bind-a-list-to-a-parameter-in-a-custom-query-in-sqlalchemy
    bstring = ','.join(f'(:b{i})'   for i, _ in enumerate(snurls))
    bdict = {            f'b{i}': v for i, v in enumerate(snurls)}
    # TODO hopefully, visits.* thing only returns one visit??
    query = text(f"""
WITH cte(queried) AS (SELECT * FROM (values {bstring}))
SELECT queried, visits.*
    FROM cte JOIN visits
    ON queried = visits.norm_url
/*  order stuff without contexts last
    this actually doesn't make sense, locially it should be ASC??
    but somehow DESC is the one that actually works..
*/
    ORDER BY visits.context IS NULL DESC
    """).bindparams(**bdict).columns(
        Column('match', types.Unicode),
        *table.columns,
    )
    # TODO ugh, def need to profile this properly...
    # hmm that was quite slow...
    # SELECT queried FROM cte WHERE EXISTS (SELECT 1 FROM visits WHERE queried = visits.norm_url)
    # logger.debug(bdict)
    # logger.debug(query)
    with engine.connect() as conn:
        res = list(conn.execute(query))
        present = {row[0]: binder.from_row(row[1:]) for row in res}
    results = []
    for nu in nurls:
        r = present.get(nu, None)
        results.append(None if r is None else as_json(r))

    if version <= (0, 11, 14):
        # older extension versions expected boolean result here
        results = [r is not None for r in results] # type: ignore[misc]

    return results


def _run(*, port: str, db: Optional[Path]=None, timezone: str, quiet: bool):
    logger = get_logger()
    env = {
        **os.environ,
        # not sure if there is a simpler way to communicate with hug..
        # # TODO here
        _ENV_CONFIG: json.dumps({
            'timezone': timezone,
            **({} if db is None else {'db': str(db)})
        }),
    }
    args = [
        python3(),
        '-m', 'hug', # TODO eh, not sure about this. what if user had it already installed?? it's a mess..
        *(['--silent'] if quiet else []),
        '-p', port,
        '-f', __file__,
    ]
    logger.info('Running server: %s', args)
    logger.info(f'with env {_ENV_CONFIG}={env[_ENV_CONFIG]}')
    os.execvpe(python3(), args, env)


def run(args):
    _run(port=args.port, db=args.db, timezone=args.timezone, quiet=args.quiet)


_DEFAULT_CONFIG = Path('config.py')


def default_db_path() -> Path:
    return default_output_dir() / 'promnesia.sqlite'


# TODO rename to 'backend'?
def setup_parser(p):
    p.add_argument(
        '--port',
        type=str,
        default='13131',
        help='Port for communicating with extension',
    )

    # TODO need to keep consistent with the backend...
    # TODO use output_dir instead?

    p.add_argument(
        '--db',
        type=Path,
        required=False,
        default=None,
        help='Path to the links database (optional, uses user data dir by default)',
    )

    # TODO mm. should add fallback timezone to frontend instead, perhaps?
    p.add_argument(
        '--timezone',
        type=str,
        default=get_system_zone(),
        help='Fallback timezone, defaults to the system timezone if not specified',
    )
    
    p.add_argument(
        '--quiet',
        action='store_true',
        help='Pass to log less',
    )
