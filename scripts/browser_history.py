#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
from subprocess import check_output
import filecmp
import logging

from kython.klogging import setup_logzero

Browser = str

CHROME = 'chrome'
FIREFOX = 'firefox'

def get_logger():
    return logging.getLogger('browser-history')


# TODO kython?
def only(it):
    values = list(it)
    assert len(values) == 1
    return values[0]


def get_path(browser: str) -> Path:
    if browser == 'chrome':
        return Path('~/.config/google-chrome/Default/History').expanduser()
    elif browser == 'firefox':
        return only(Path('~/.mozilla/firefox/').expanduser().glob('*/places.sqlite'))
    else:
        raise RuntimeError(f'Unexpected browser {browser}')

def test_get_path():
    get_path('chrome')
    get_path('firefox')


def atomic_copy(src: Path, dest: Path):
    """
    Supposed to handle cases where the file is changed while we were copying it.
    """
    import shutil

    differs = True
    while differs:
        res = shutil.copy(src, dest)
        differs = not filecmp.cmp(str(src), str(res))


def format_dt(dt: datetime) -> str:
    return dt.strftime('%Y%m%d%H%M%S')


def backup_history(browser: Browser, to: Path, pattern=None) -> Path:
    assert to.is_dir()
    logger = get_logger()

    now = format_dt(datetime.utcnow())

    path = get_path(browser)

    pattern = path.stem + '-{}' + path.suffix if pattern is None else pattern
    fname = pattern.format(now)


    res = to / fname
    logger.info('backing up to %s', res)
    # if your chrome is open, database would normally be locked, so you can't just make a snapshot
    # so we'll just copy it till it converge. bit paranoid, but should work
    atomic_copy(path, res)
    logger.info('done!')
    return res


def test_backup_history(tmp_path):
    tdir = Path(tmp_path)
    backup_history(CHROME, tdir)
    backup_history(FIREFOX, tdir)


def guess_db_date(db: Path) -> str:
    maxvisit = check_output([
        'sqlite3',
        '-csv',
        db,
        'SELECT max(datetime(((visits.visit_time/1000000)-11644473600), "unixepoch")) FROM visits;'
    ]).decode('utf8').strip().strip('"');
    return format_dt(datetime.strptime(maxvisit, "%Y-%m-%d %H:%M:%S"))


def test_guess(tmp_path):
    tdir = Path(tmp_path)
    db = backup_history(CHROME, tdir)
    guess_db_date(db)


def main():
    logger = get_logger()
    setup_logzero(logger, level=logging.DEBUG)
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--browser', type=Browser, required=True)
    p.add_argument('--to', type=Path, required=True)
    args = p.parse_args()

    # TODO do I need pattern??
    backup_history(browser=args.browser, to=args.to)


if __name__ == '__main__':
    main()