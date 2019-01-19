import {get_options, set_options} from './options';

function getHost(): HTMLInputElement {
    return ((document.getElementById('host_id'): any): HTMLInputElement);
}

function getToken(): HTMLInputElement {
    return ((document.getElementById('token_id'): any): HTMLInputElement);
}

function getDots(): HTMLInputElement {
    return ((document.getElementById('dots_id'): any): HTMLInputElement);
}

// TODO display it floating

document.addEventListener('DOMContentLoaded', () => {
    get_options(opts => {
        getHost().value = opts.host;
        getDots().checked = opts.dots;
        getToken().value = opts.token;
    });
});
document.getElementById('save_id').addEventListener('click', () => {
    const opts = {
        host: getHost().value,
        dots: getDots().checked,
        token: getToken().value,
    };
    set_options(opts, () => { alert("Saved!"); });
});
