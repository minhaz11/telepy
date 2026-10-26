(function () {
    'use strict';

    var CONFIG = window.Tracefox || {path: '/tracefox', recording: true, watchers: []};
    var BASE = (CONFIG.path || '').replace(/\/$/, '');
    var root = document.getElementById('tracefox');

    // ------------------------------------------------------------------ helpers

    function h(tag, props) {
        var el = document.createElement(tag);
        props = props || {};
        Object.keys(props).forEach(function (key) {
            var value = props[key];
            if (value === null || value === undefined || value === false) return;
            if (key === 'class') el.className = value;
            else if (key === 'html') el.innerHTML = value;
            else if (key === 'text') el.textContent = value;
            else if (key.slice(0, 2) === 'on') el.addEventListener(key.slice(2).toLowerCase(), value);
            else el.setAttribute(key, value === true ? '' : value);
        });
        for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
        return el;
    }

    function append(el, child) {
        if (child === null || child === undefined || child === false) return;
        if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); return; }
        el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
    }

    function api(path, options) {
        options = options || {};
        var headers = {'Accept': 'application/json', 'X-Tracefox-Request': '1'};
        if (options.body) headers['Content-Type'] = 'application/json';
        return fetch(BASE + '/api' + path, {method: options.method || 'GET', headers: headers, body: options.body, credentials: 'same-origin'})
            .then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (data) {
                    if (!response.ok) { var e = new Error(data.message || response.statusText); e.status = response.status; throw e; }
                    return data;
                });
            });
    }

    function truncate(value, length) {
        value = value === null || value === undefined ? '' : String(value);
        return value.length > length ? value.slice(0, length) + '…' : value;
    }

    function parseDate(iso) { return iso ? new Date(iso) : null; }

    function timeAgo(iso) {
        var date = parseDate(iso);
        if (!date) return '';
        var s = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
        if (s < 5) return 'Just now';
        if (s < 60) return s + 's ago';
        var m = Math.round(s / 60);
        if (m < 60) return m + 'm ago';
        var hr = Math.round(m / 60);
        if (hr < 24) return hr + 'h ago';
        var d = Math.round(hr / 24);
        return d + 'd ago';
    }

    function fullDate(iso) {
        var date = parseDate(iso);
        if (!date) return '';
        var pad = function (n) { return String(n).padStart(2, '0'); };
        return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' +
            pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
    }

    function timeCell(entry) {
        return h('td', {class: 'time', title: fullDate(entry.created_at), 'data-time': entry.created_at}, timeAgo(entry.created_at));
    }

    function timeValue(entry) {
        return [fullDate(entry.created_at), ' ', h('span', {class: 'sub', style: 'color:var(--muted)', 'data-time': entry.created_at}, '(' + timeAgo(entry.created_at) + ')')];
    }

    function badge(text, variant) { return h('span', {class: 'badge ' + (variant || '')}, text); }

    function statusVariant(status) {
        status = Number(status);
        if (!status) return 'danger';
        if (status < 300) return 'success';
        if (status < 400) return 'info';
        if (status < 500) return 'warning';
        return 'danger';
    }

    function methodVariant() { return 'secondary'; }

    function levelVariant(level) {
        return ({debug: 'secondary', info: 'info', notice: 'info', warning: 'warning', error: 'danger', critical: 'danger', alert: 'danger', emergency: 'danger'})[level] || 'secondary';
    }

    function durationText(ms) { return ms === null || ms === undefined || ms === '' ? '-' : Math.round(Number(ms) * 100) / 100 + 'ms'; }

    function link(path, children, attrs) {
        attrs = attrs || {};
        attrs.href = BASE + path;
        attrs['data-link'] = '';
        return h('a', attrs, children);
    }

    function flatten(list) {
        return (list || []).reduce(function (acc, item) { return acc.concat(Array.isArray(item) ? flatten(item) : [item]); }, []);
    }

    function isEmpty(value) {
        if (value === null || value === undefined || value === '') return true;
        if (Array.isArray(value)) return value.length === 0;
        if (typeof value === 'object') return Object.keys(value).length === 0;
        return false;
    }

    // ------------------------------------------------------------------ icons

    var ICON_PATHS = {
        requests: 'M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418',
        commands: 'm6.75 7.5 3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0 0 21 18V6a2.25 2.25 0 0 0-2.25-2.25H5.25A2.25 2.25 0 0 0 3 6v12a2.25 2.25 0 0 0 2.25 2.25Z',
        schedule: 'M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
        jobs: 'M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 0 1 0 3.75H5.625a1.875 1.875 0 0 1 0-3.75Z',
        batches: 'M6.429 9.75 2.25 12l4.179 2.25m0-4.5 5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0L21.75 12l-4.179 2.25m0 0 4.179 2.25L12 21.75 2.25 16.5l4.179-2.25m11.142 0-5.571 3-5.571-3',
        cache: 'm20.25 7.5-.625 10.632a2.25 2.25 0 0 1-2.247 2.118H6.622a2.25 2.25 0 0 1-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z',
        dumps: 'M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5',
        events: 'm3.75 13.5 10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75Z',
        exceptions: 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z',
        gates: 'M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z',
        'client-requests': 'M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25',
        logs: 'M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z',
        mail: 'M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75',
        models: 'm21 7.5-9-5.25L3 7.5m18 0-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9',
        notifications: 'M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0',
        queries: 'M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125',
        redis: 'M5.25 14.25h13.5m-13.5 0a3 3 0 0 1-3-3m3 3a3 3 0 1 0 0 6h13.5a3 3 0 1 0 0-6m-16.5-3a3 3 0 0 1 3-3h13.5a3 3 0 0 1 3 3m-19.5 0a4.5 4.5 0 0 1 .9-2.7L5.737 5.1a3.375 3.375 0 0 1 2.7-1.35h7.126c1.062 0 2.062.5 2.7 1.35l2.587 3.45a4.5 4.5 0 0 1 .9 2.7m0 0a3 3 0 0 1-3 3m0 3h.008v.008h-.008v-.008Zm0-6h.008v.008h-.008v-.008Zm-3 6h.008v.008h-.008v-.008Zm0-6h.008v.008h-.008v-.008Z',
        views: 'M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
        monitoring: 'M9.568 3H5.25A2.25 2.25 0 0 0 3 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581c.699.699 1.78.872 2.607.33a18.095 18.095 0 0 0 5.223-5.223c.542-.827.369-1.908-.33-2.607L11.16 3.66A2.25 2.25 0 0 0 9.568 3Z M6 6h.008v.008H6V6Z',
        pause: 'M15.75 5.25v13.5m-7.5-13.5v13.5',
        play: 'M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z',
        trash: 'm14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0',
        sun: 'M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z',
        moon: 'M21.752 15.002A9.72 9.72 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z',
        system: 'M9 17.25v1.007a3 3 0 0 1-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0 1 15 18.257V17.25m6-12V15a2.25 2.25 0 0 1-2.25 2.25H5.25A2.25 2.25 0 0 1 3 15V5.25m18 0A2.25 2.25 0 0 0 18.75 3H5.25A2.25 2.25 0 0 0 3 5.25m18 0V12a2.25 2.25 0 0 1-2.25 2.25H5.25A2.25 2.25 0 0 1 3 12V5.25',
        chevron: 'm8.25 4.5 7.5 7.5-7.5 7.5',
        search: 'm21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z',
        refresh: 'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99'
    };

    function icon(name) {
        var span = document.createElement('span');
        span.style.display = 'contents';
        span.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.6" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="' + (ICON_PATHS[name] || '') + '"/></svg>';
        return span.firstChild;
    }

    function logo() {
        return h('img', {src: BASE + '/static/logo.png', alt: '', width: '36', height: '36'});
    }

    // ------------------------------------------------------------------ json viewer

    function jsonView(value, openDepth) {
        openDepth = openDepth === undefined ? 3 : openDepth;
        var container = h('div', {class: 'json'});
        container.appendChild(jsonNode(value, 0, openDepth));
        return container;
    }

    function jsonScalar(value) {
        if (value === null || value === undefined) return h('span', {class: 'z'}, 'null');
        if (typeof value === 'string') return h('span', {class: 's'}, JSON.stringify(value));
        if (typeof value === 'number') return h('span', {class: 'n'}, String(value));
        if (typeof value === 'boolean') return h('span', {class: 'b'}, String(value));
        return h('span', {}, String(value));
    }

    function jsonNode(value, depth, openDepth) {
        if (value === null || typeof value !== 'object') return jsonScalar(value);
        var isArray = Array.isArray(value);
        var keys = isArray ? value.map(function (_, i) { return i; }) : Object.keys(value);
        var open = isArray ? '[' : '{', close = isArray ? ']' : '}';
        if (!keys.length) return h('span', {}, open + close);
        var wrapper = h('span', {});
        var expanded = depth < openDepth;
        var toggle = h('span', {class: 'toggle'}, expanded ? '▾' : '▸');
        var summary = h('span', {class: 'summary'}, ' ' + keys.length + (isArray ? ' items ' : ' keys ') + close);
        var children = h('div', {class: 'children'});
        var rendered = false;

        function render() {
            if (rendered) return;
            rendered = true;
            keys.forEach(function (key, index) {
                var row = h('div', {});
                if (!isArray) row.appendChild(h('span', {class: 'k'}, JSON.stringify(String(key))));
                if (!isArray) row.appendChild(document.createTextNode(': '));
                row.appendChild(jsonNode(value[key], depth + 1, openDepth));
                if (index < keys.length - 1) row.appendChild(document.createTextNode(','));
                children.appendChild(row);
            });
        }

        function set(state) {
            expanded = state;
            toggle.textContent = expanded ? '▾' : '▸';
            if (expanded) render();
            children.style.display = expanded ? '' : 'none';
            closing.style.display = expanded ? '' : 'none';
            summary.style.display = expanded ? 'none' : '';
        }

        var closing = h('div', {}, close);
        toggle.addEventListener('click', function () { set(!expanded); });
        summary.addEventListener('click', function () { set(true); });
        wrapper.appendChild(toggle);
        wrapper.appendChild(document.createTextNode(open));
        wrapper.appendChild(summary);
        wrapper.appendChild(children);
        wrapper.appendChild(closing);
        set(expanded);
        return wrapper;
    }

    function dataView(value) {
        if (value === null || value === undefined || value === '') return h('div', {class: 'empty', style: 'padding:24px'}, 'Nothing here.');
        if (typeof value === 'string') return h('pre', {class: 'code'}, value);
        return jsonView(value);
    }

    // ------------------------------------------------------------------ sql

    var SQL_KEYWORDS = 'SELECT|FROM|WHERE|AND|OR|NOT|IN|IS|NULL|AS|ON|JOIN|INNER|LEFT|RIGHT|OUTER|FULL|CROSS|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|ALTER|DROP|INDEX|DISTINCT|COUNT|SUM|AVG|MIN|MAX|CASE|WHEN|THEN|ELSE|END|UNION|ALL|EXISTS|LIKE|ILIKE|BETWEEN|ASC|DESC|RETURNING|WITH|PRAGMA|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|TRUE|FALSE|CONFLICT|DO|NOTHING|PRIMARY|KEY|DEFAULT|SHOW|DESCRIBE|EXPLAIN';
    var SQL_TOKEN = new RegExp("('(?:[^']|'')*')|(\\b\\d+(?:\\.\\d+)?\\b)|\\b(" + SQL_KEYWORDS + ")\\b", 'gi');
    var SQL_BREAK = /\s+(FROM|WHERE|INNER JOIN|LEFT OUTER JOIN|LEFT JOIN|RIGHT JOIN|CROSS JOIN|JOIN|ORDER BY|GROUP BY|HAVING|LIMIT|OFFSET|VALUES|SET|UNION ALL|UNION|RETURNING|ON CONFLICT)\b/gi;

    function formatSql(sql) {
        if (!sql || sql.length > 20000) return sql || '';
        var parts = sql.split(/('(?:[^']|'')*')/);
        return parts.map(function (part, i) {
            return i % 2 ? part : part.replace(SQL_BREAK, '\n$1').replace(/\s+(AND|OR)\s+/gi, '\n  $1 ');
        }).join('').trim();
    }

    function sqlView(sql) {
        var pre = h('pre', {class: 'code sql'});
        var text = formatSql(sql);
        var last = 0;
        text.replace(SQL_TOKEN, function (match, str, num, kw, offset) {
            if (offset > last) pre.appendChild(document.createTextNode(text.slice(last, offset)));
            pre.appendChild(h('span', {class: str ? 'str' : num ? 'num' : 'kw'}, kw ? match.toUpperCase() : match));
            last = offset + match.length;
            return match;
        });
        if (last < text.length) pre.appendChild(document.createTextNode(text.slice(last)));
        return pre;
    }

    // ------------------------------------------------------------------ cards & widgets

    function card(title, body, extra) {
        return h('div', {class: 'card'},
            title ? h('div', {class: 'card-header'}, h('h2', {}, title), extra || null) : null,
            body);
    }

    function detailsTable(rows) {
        var tbody = h('tbody');
        rows.forEach(function (row) {
            if (!row) return;
            var value = row[1];
            if (value === null || value === undefined || value === '') value = h('span', {style: 'color:var(--subtle)'}, '-');
            tbody.appendChild(h('tr', {}, h('td', {}, row[0]), h('td', {}, value)));
        });
        return h('table', {class: 'details'}, tbody);
    }

    function tabsCard(tabs) {
        tabs = tabs.filter(Boolean);
        if (!tabs.length) return null;
        var body = h('div');
        var buttons = [];
        var bar = h('div', {class: 'tabs'});
        function select(index) {
            buttons.forEach(function (b, i) { b.classList.toggle('active', i === index); });
            body.replaceChildren(typeof tabs[index].content === 'function' ? tabs[index].content() : tabs[index].content);
        }
        tabs.forEach(function (tab, index) {
            var button = h('button', {class: 'tab', type: 'button', onclick: function () { select(index); }}, tab.label,
                tab.count !== undefined ? h('span', {class: 'badge brand'}, tab.count) : null);
            buttons.push(button);
            bar.appendChild(button);
        });
        select(0);
        return h('div', {class: 'card'}, bar, body);
    }

    function codePreview(lines, highlight) {
        var keys = Object.keys(lines || {});
        if (!keys.length) return null;
        var tbody = h('tbody');
        keys.forEach(function (num) {
            tbody.appendChild(h('tr', {class: Number(num) === Number(highlight) ? 'hl' : ''},
                h('td', {class: 'ln'}, num), h('td', {}, lines[num] || ' ')));
        });
        return h('div', {class: 'preview-wrap'}, h('table', {class: 'preview'}, tbody));
    }

    function isVendor(frame) {
        if (typeof frame.vendor === 'boolean') return frame.vendor;
        var file = frame.file || '';
        return /site-packages|dist-packages|lib\/python\d|<frozen/.test(file);
    }

    function stackTrace(trace) {
        trace = trace || [];
        if (!trace.length) return null;
        var showAll = false;
        var wrap = h('div');
        function render() {
            var frames = showAll ? trace : trace.filter(function (f, i) { return i === 0 || !isVendor(f); });
            var tbody = h('tbody');
            frames.forEach(function (frame) {
                tbody.appendChild(h('tr', {class: isVendor(frame) ? 'vendor' : ''},
                    h('td', {},
                        h('div', {class: 'fn'}, frame.function || '(main)'),
                        frame.code ? h('div', {class: 'loc'}, frame.code) : null),
                    h('td', {class: 'loc right where'}, (frame.file || '') + ':' + (frame.line || ''))));
            });
            var hidden = trace.length - frames.length;
            wrap.replaceChildren(h('table', {class: 'table trace'}, tbody),
                (hidden > 0 || showAll) && trace.length > 1 ? h('button', {class: 'load-more', type: 'button', onclick: function () { showAll = !showAll; render(); }},
                    showAll ? 'Hide vendor frames' : 'Show all ' + trace.length + ' frames') : null);
        }
        render();
        return card('Stack Trace', wrap);
    }

    function tagsRow(entry) {
        if (!entry.tags || !entry.tags.length) return null;
        var def = TYPE_BY_TYPE[entry.type];
        return ['Tags', h('div', {class: 'tags'}, entry.tags.map(function (tag) {
            return link('/' + def.slug + '?tag=' + encodeURIComponent(tag), tag, {class: 'badge brand'});
        }))];
    }

    function parentRows(batch) {
        var parents = [['request', 'Request'], ['command', 'Command'], ['schedule', 'Scheduled Task'], ['job', 'Job']];
        for (var i = 0; i < parents.length; i++) {
            var found = (batch || []).filter(function (e) { return e.type === parents[i][0]; })[0];
            if (found) {
                var def = TYPE_BY_TYPE[found.type];
                return [[parents[i][1], link('/' + def.slug + '/' + found.id, def.title(found))]];
            }
        }
        return [];
    }

    function locationText(content) {
        return content.file ? content.file + (content.line ? ':' + content.line : '') : null;
    }

    function userCard(user) {
        if (!user) return null;
        return card('Authenticated User', detailsTable([
            ['ID', user.id], ['Name', user.name], ['Email Address', user.email]
        ]));
    }

    function exceptionCard(exception) {
        if (!exception) return null;
        return [
            card('Exception', h('pre', {class: 'code'}, exception.message || '')),
            exception.line_preview ? card('Location' + (exception.file ? ' — ' + exception.file + ':' + exception.line : ''), codePreview(exception.line_preview, exception.line)) : null,
            stackTrace(exception.trace)
        ];
    }

    // ------------------------------------------------------------------ entry types

    function happened() { return {label: 'Happened', cls: 'right', render: timeCell, raw: true}; }

    var TYPES = [
        {
            slug: 'requests', type: 'request', label: 'Requests', icon: 'requests', watcher: 'requests', related: true,
            title: function (e) { return e.content.method + ' ' + e.content.uri; },
            columns: [
                {label: 'Verb', render: function (e) { return badge(e.content.method, methodVariant(e.content.method)); }},
                {label: 'Path', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.uri, 70)); }},
                {label: 'Status', render: function (e) { return badge(e.content.response_status, statusVariant(e.content.response_status)); }},
                {label: 'Duration', cls: 'nowrap', render: function (e) { return durationText(e.content.duration); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [
                    ['Time', timeValue(e)], ['Hostname', c.hostname], ['Method', badge(c.method)],
                    ['Controller Action', c.controller_action ? h('span', {class: 'mono'}, c.controller_action) : null],
                    ['Middleware', (c.middleware || []).join(', ')], ['Path', h('span', {class: 'mono'}, c.uri)],
                    ['Status', badge(c.response_status, statusVariant(c.response_status))],
                    ['Duration', durationText(c.duration)], ['IP Address', c.ip_address],
                    ['Memory usage', c.memory ? c.memory + ' MB' : null]
                ];
            },
            sections: function (e) {
                var c = e.content;
                return [
                    tabsCard([
                        {label: 'Payload', content: function () { return dataView(c.payload); }},
                        {label: 'Headers', content: function () { return dataView(c.headers); }},
                        {label: 'Session', content: function () { return dataView(c.session); }},
                        {label: 'Response', content: function () { return dataView(c.response); }},
                        {label: 'Response Headers', content: function () { return dataView(c.response_headers); }}
                    ]),
                    userCard(c.user)
                ];
            }
        },
        {
            slug: 'commands', type: 'command', label: 'Commands', icon: 'commands', watcher: 'commands', related: true,
            title: function (e) { return e.content.command; },
            columns: [
                {label: 'Command', render: function (e) { return h('span', {class: 'cell-mono'}, e.content.command); }},
                {label: 'Exit Code', render: function (e) { return badge(e.content.exit_code, e.content.exit_code === 0 ? 'success' : 'danger'); }},
                happened()
            ],
            details: function (e) {
                return [['Time', timeValue(e)], ['Hostname', e.content.hostname], ['Command', h('span', {class: 'mono'}, e.content.command)],
                    ['Exit Code', badge(e.content.exit_code, e.content.exit_code === 0 ? 'success' : 'danger')]];
            },
            sections: function (e) {
                return [tabsCard([
                    {label: 'Arguments', content: function () { return dataView(e.content.arguments); }},
                    {label: 'Options', content: function () { return dataView(e.content.options); }}
                ])];
            }
        },
        {
            slug: 'schedule', type: 'schedule', label: 'Schedule', icon: 'schedule', watcher: 'schedule', related: true,
            title: function (e) { return e.content.command; },
            columns: [
                {label: 'Command', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.command, 70)); }},
                {label: 'Expression', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.expression, 50)); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Description', c.description],
                    ['Command', h('span', {class: 'mono'}, c.command)], ['Expression', h('span', {class: 'mono'}, c.expression)],
                    ['Timezone', c.timezone], ['User', c.user]];
            },
            sections: function (e) { return [e.content.output ? card('Output', h('pre', {class: 'code'}, e.content.output)) : null]; }
        },
        {
            slug: 'jobs', type: 'job', label: 'Jobs', icon: 'jobs', watcher: 'jobs', related: true,
            title: function (e) { return e.content.name; },
            columns: [
                {label: 'Job', render: function (e) {
                    return [h('span', {class: 'cell-mono'}, e.content.name),
                        h('span', {class: 'muted-line'}, 'Connection: ' + (e.content.connection || '-') + ' | Queue: ' + (e.content.queue || '-'))];
                }},
                {label: 'Status', render: function (e) { return jobStatus(e.content.status); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Status', jobStatus(c.status)],
                    ['Job', h('span', {class: 'mono'}, c.name)], ['Connection', c.connection], ['Queue', c.queue],
                    ['Tries', c.tries], ['Timeout', c.timeout],
                    c.batch_id ? ['Batch', link('/batches/' + c.batch_id, c.batch_id)] : null];
            },
            sections: function (e) { return [card('Data', dataView(e.content.data)), exceptionCard(e.content.exception)]; }
        },
        {
            slug: 'batches', type: 'batch', label: 'Batches', icon: 'batches', watcher: 'batches',
            title: function (e) { return e.content.name || e.content.id || e.id; },
            columns: [
                {label: 'Batch', render: function (e) { return [h('span', {}, e.content.name || 'Batch'), h('span', {class: 'muted-line'}, e.content.id || e.id)]; }},
                {label: 'Status', render: function (e) { return batchStatus(e.content); }},
                {label: 'Size', render: function (e) { return e.content.totalJobs; }},
                {label: 'Completion', cls: 'nowrap', render: function (e) { return progressBar(e.content); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Status', batchStatus(c)], ['Batch ID', c.id || e.id],
                    ['Name', c.name], ['Total Jobs', c.totalJobs], ['Pending Jobs', c.pendingJobs], ['Processed Jobs', c.processedJobs],
                    ['Failed Jobs', c.failedJobs], ['Completion', progressBar(c)],
                    ['Jobs', link('/jobs?tag=' + encodeURIComponent('Batch:' + e.id), 'View jobs in this batch')]];
            },
            sections: function (e) { return [card('Options', dataView(e.content.options))]; }
        },
        {
            slug: 'cache', type: 'cache', label: 'Cache', icon: 'cache', watcher: 'cache',
            title: function (e) { return e.content.key; },
            columns: [
                {label: 'Key', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.key, 80)); }},
                {label: 'Action', render: function (e) { return cacheBadge(e.content.type); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Key', h('span', {class: 'mono'}, c.key)],
                    ['Action', cacheBadge(c.type)], c.expiration !== undefined ? ['Expiration', c.expiration === null ? 'Forever' : c.expiration + ' seconds'] : null];
            },
            sections: function (e) { return ['value' in e.content ? card('Value', dataView(e.content.value)) : null]; }
        },
        {
            slug: 'dumps', type: 'dump', label: 'Dumps', icon: 'dumps', watcher: 'dumps', customIndex: true,
            title: function (e) { return truncate(e.content.dump, 60); },
            columns: [
                {label: 'Dump', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.dump, 100)); }},
                happened()
            ],
            details: function (e) {
                return [['Time', timeValue(e)], ['Hostname', e.content.hostname], ['Type', e.content.type], ['Location', locationText(e.content)]];
            },
            sections: function (e) { return [card('Dump', dumpBody(e.content))]; }
        },
        {
            slug: 'events', type: 'event', label: 'Events', icon: 'events', watcher: 'events',
            title: function (e) { return e.content.name; },
            columns: [
                {label: 'Name', render: function (e) { return [h('span', {class: 'cell-mono'}, e.content.name), e.content.broadcast ? [' ', badge('Broadcast', 'info')] : null]; }},
                {label: 'Listeners', render: function (e) { return (e.content.listeners || []).length; }},
                happened()
            ],
            details: function (e) {
                return [['Time', timeValue(e)], ['Hostname', e.content.hostname], ['Event', h('span', {class: 'mono'}, e.content.name)],
                    ['Broadcast', e.content.broadcast ? 'Yes' : 'No']];
            },
            sections: function (e) {
                var listeners = e.content.listeners || [];
                var tbody = h('tbody', {}, listeners.map(function (l) { return h('tr', {}, h('td', {class: 'cell-mono'}, l.name), h('td', {class: 'right'}, l.queued ? badge('Queued', 'info') : '')); }));
                return [card('Payload', dataView(e.content.payload)),
                    card('Listeners', listeners.length ? h('table', {class: 'table'}, tbody) : h('div', {class: 'empty', style: 'padding:24px'}, 'No listeners.'))];
            }
        },
        {
            slug: 'exceptions', type: 'exception', label: 'Exceptions', icon: 'exceptions', watcher: 'exceptions',
            title: function (e) { return e.content.class; },
            columns: [
                {label: 'Type', render: function (e) { return [h('span', {class: 'cell-mono'}, e.content.class), h('span', {class: 'muted-line'}, truncate(e.content.message, 110))]; }},
                {label: '#', cls: 'right', render: function (e) { return e.content.occurrences || 1; }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Type', h('span', {class: 'mono'}, c.class)],
                    ['Location', locationText(c)],
                    ['Occurrences', e.family_hash ? link('/exceptions?family_hash=' + e.family_hash, 'View other occurrences') : c.occurrences],
                    c.previous ? ['Caused by', h('span', {class: 'mono'}, c.previous.class + ': ' + c.previous.message)] : null];
            },
            sections: function (e) {
                var c = e.content;
                return [
                    card('Message', h('pre', {class: 'code'}, c.message)),
                    c.line_preview && Object.keys(c.line_preview).length ? card('Location', codePreview(c.line_preview, c.line), h('span', {class: 'sub mono'}, locationText(c))) : null,
                    stackTrace(c.trace),
                    !isEmpty(c.context) ? card('Context', dataView(c.context)) : null
                ];
            }
        },
        {
            slug: 'gates', type: 'gate', label: 'Gates', icon: 'gates', watcher: 'gates',
            title: function (e) { return e.content.ability; },
            columns: [
                {label: 'Ability', render: function (e) { return h('span', {class: 'cell-mono'}, e.content.ability); }},
                {label: 'Result', render: function (e) { return badge(e.content.result, e.content.result === 'allowed' ? 'success' : 'danger'); }},
                happened()
            ],
            details: function (e) {
                return [['Time', timeValue(e)], ['Hostname', e.content.hostname], ['Ability', h('span', {class: 'mono'}, e.content.ability)],
                    ['Result', badge(e.content.result, e.content.result === 'allowed' ? 'success' : 'danger')], ['Location', locationText(e.content)]];
            },
            sections: function (e) { return [card('Arguments', dataView(e.content.arguments))]; }
        },
        {
            slug: 'client-requests', type: 'client_request', label: 'HTTP Client', icon: 'client-requests', watcher: 'http_client',
            title: function (e) { return e.content.method + ' ' + e.content.uri; },
            columns: [
                {label: 'Verb', render: function (e) { return badge(e.content.method); }},
                {label: 'URI', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.uri, 70)); }},
                {label: 'Status', render: function (e) { return e.content.response_status ? badge(e.content.response_status, statusVariant(e.content.response_status)) : badge('Failed', 'danger'); }},
                {label: 'Duration', cls: 'nowrap', render: function (e) { return durationText(e.content.duration); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Method', badge(c.method)], ['URI', h('span', {class: 'mono'}, c.uri)],
                    ['Status', c.response_status ? badge(c.response_status, statusVariant(c.response_status)) : badge('Failed', 'danger')],
                    ['Duration', durationText(c.duration)]];
            },
            sections: function (e) {
                var c = e.content;
                return [
                    tabsCard([
                        {label: 'Payload', content: function () { return dataView(c.payload); }},
                        {label: 'Headers', content: function () { return dataView(c.headers); }}
                    ]),
                    tabsCard([
                        {label: 'Response', content: function () { return dataView(c.response); }},
                        {label: 'Response Headers', content: function () { return dataView(c.response_headers); }}
                    ])
                ];
            }
        },
        {
            slug: 'logs', type: 'log', label: 'Logs', icon: 'logs', watcher: 'logs',
            title: function (e) { return truncate(e.content.message, 60); },
            columns: [
                {label: 'Message', render: function (e) { return [h('span', {}, truncate(e.content.message, 110)), e.content.channel ? h('span', {class: 'muted-line'}, e.content.channel) : null]; }},
                {label: 'Level', render: function (e) { return badge(e.content.level, levelVariant(e.content.level)); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Level', badge(c.level, levelVariant(c.level))],
                    ['Channel', c.channel], ['Location', c.location ? h('span', {class: 'mono'}, c.location) : null]];
            },
            sections: function (e) {
                return [card('Message', h('pre', {class: 'code'}, e.content.message)), !isEmpty(e.content.context) ? card('Context', dataView(e.content.context)) : null];
            }
        },
        {
            slug: 'mail', type: 'mail', label: 'Mail', icon: 'mail', watcher: 'mail',
            title: function (e) { return e.content.subject || e.content.mailable; },
            columns: [
                {label: 'Mailable', render: function (e) { return [h('span', {}, e.content.mailable || 'Mail'), h('span', {class: 'muted-line'}, 'Subject: ' + (e.content.subject || '-'))]; }},
                {label: 'Recipients', render: function (e) { return truncate((e.content.to || []).join(', '), 60); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                var list = function (v) { return (v || []).join(', ') || null; };
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Mailable', c.mailable], ['Queued', c.queued ? 'Yes' : 'No'],
                    ['From', list(c.from)], ['Reply-To', list(c.replyTo)], ['To', list(c.to)], ['CC', list(c.cc)], ['BCC', list(c.bcc)],
                    ['Subject', c.subject], (c.attachments || []).length ? ['Attachments', list(c.attachments)] : null,
                    c.raw ? ['Download', h('a', {href: BASE + '/api/mail/' + e.id + '/download'}, 'Download .eml file')] : null];
            },
            sections: function (e) {
                var frame = h('iframe', {class: 'mail-frame', sandbox: '', title: 'Mail preview'});
                frame.srcdoc = e.content.html || '<p style="font-family:sans-serif;color:#888">This mail has no HTML body.</p>';
                return [card('Preview', frame, h('a', {class: 'sub', href: BASE + '/api/mail/' + e.id + '/preview', target: '_blank', rel: 'noopener'}, 'Open in new tab'))];
            }
        },
        {
            slug: 'models', type: 'model', label: 'Models', icon: 'models', watcher: 'models',
            title: function (e) { return e.content.model; },
            columns: [
                {label: 'Model', render: function (e) { return h('span', {class: 'cell-mono'}, e.content.model); }},
                {label: 'Action', render: function (e) { return modelBadge(e.content); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Model', h('span', {class: 'mono'}, c.model)],
                    ['Action', modelBadge(c)], c.count ? ['Hydrated', c.count + ' models'] : null];
            },
            sections: function (e) { return [!isEmpty(e.content.changes) ? card('Changes', dataView(e.content.changes)) : null]; }
        },
        {
            slug: 'notifications', type: 'notification', label: 'Notifications', icon: 'notifications', watcher: 'notifications',
            title: function (e) { return e.content.notification; },
            columns: [
                {label: 'Notification', render: function (e) { return [h('span', {class: 'cell-mono'}, e.content.notification), h('span', {class: 'muted-line'}, 'Recipient: ' + (e.content.notifiable || '-'))]; }},
                {label: 'Channel', render: function (e) { return badge(e.content.channel, 'info'); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Channel', c.channel], ['Notification', h('span', {class: 'mono'}, c.notification)],
                    ['Notifiable', c.notifiable], ['Queued', c.queued ? 'Yes' : 'No']];
            },
            sections: function (e) { return [!isEmpty(e.content.response) ? card('Response', dataView(e.content.response)) : null]; }
        },
        {
            slug: 'queries', type: 'query', label: 'Queries', icon: 'queries', watcher: 'queries',
            title: function (e) { return truncate(e.content.sql, 60); },
            columns: [
                {label: 'Query', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.sql, 110)); }},
                {label: 'Duration', cls: 'nowrap', render: function (e) { return e.content.slow ? badge(durationText(e.content.time), 'danger') : durationText(e.content.time); }},
                happened()
            ],
            details: function (e) {
                var c = e.content;
                return [['Time', timeValue(e)], ['Hostname', c.hostname], ['Connection', c.connection],
                    ['Duration', c.slow ? badge(durationText(c.time) + ' (slow)', 'danger') : durationText(c.time)],
                    ['Location', locationText(c) ? h('span', {class: 'mono'}, locationText(c)) : null]];
            },
            sections: function (e) {
                return [card('Query', sqlView(e.content.sql)), !isEmpty(e.content.bindings) ? card('Bindings', dataView(e.content.bindings)) : null];
            }
        },
        {
            slug: 'redis', type: 'redis', label: 'Redis', icon: 'redis', watcher: 'redis',
            title: function (e) { return truncate(e.content.command, 60); },
            columns: [
                {label: 'Command', render: function (e) { return h('span', {class: 'cell-mono'}, truncate(e.content.command, 110)); }},
                {label: 'Duration', cls: 'nowrap', render: function (e) { return durationText(e.content.time); }},
                happened()
            ],
            details: function (e) {
                return [['Time', timeValue(e)], ['Hostname', e.content.hostname], ['Connection', e.content.connection], ['Duration', durationText(e.content.time)]];
            },
            sections: function (e) { return [card('Command', h('pre', {class: 'code'}, e.content.command))]; }
        },
        {
            slug: 'views', type: 'view', label: 'Views', icon: 'views', watcher: 'views',
            title: function (e) { return e.content.name; },
            columns: [
                {label: 'Name', render: function (e) { return [h('span', {class: 'cell-mono'}, e.content.name), e.content.path ? h('span', {class: 'muted-line'}, e.content.path) : null]; }},
                {label: 'Data', render: function (e) { return (e.content.data || []).length + ' keys'; }},
                happened()
            ],
            details: function (e) {
                return [['Time', timeValue(e)], ['Hostname', e.content.hostname], ['View', h('span', {class: 'mono'}, e.content.name)],
                    ['Path', e.content.path ? h('span', {class: 'mono'}, e.content.path) : null]];
            },
            sections: function (e) { return [card('Data', dataView(e.content.data))]; }
        }
    ];

    var TYPE_BY_SLUG = {}, TYPE_BY_TYPE = {};
    TYPES.forEach(function (t) { TYPE_BY_SLUG[t.slug] = t; TYPE_BY_TYPE[t.type] = t; });

    function jobStatus(status) {
        return badge(status, ({pending: 'secondary', processed: 'success', failed: 'danger'})[status] || 'secondary');
    }

    function batchStatus(c) {
        if (c.failedJobs > 0) return badge('Failures', 'danger');
        if (c.pendingJobs <= 0) return badge('Finished', 'success');
        return badge('Pending', 'warning');
    }

    function progressBar(c) {
        var total = c.totalJobs || 0;
        var done = (c.processedJobs || 0) + (c.failedJobs || 0);
        var pct = total ? Math.round(done / total * 100) : 100;
        return [h('span', {class: 'progress'}, h('span', {style: 'width:' + pct + '%'})), pct + '%'];
    }

    function cacheBadge(type) {
        return badge(type, ({hit: 'success', missed: 'warning', set: 'info', forget: 'danger'})[type] || 'secondary');
    }

    function modelBadge(c) {
        var variant = ({created: 'success', updated: 'info', deleted: 'danger', retrieved: 'secondary'})[c.action] || 'secondary';
        return badge(c.action === 'retrieved' && c.count ? c.action + ' (' + c.count + ')' : c.action, variant);
    }

    function dumpBody(c) {
        if (c.value !== null && typeof c.value === 'object') return jsonView(c.value, 2);
        return h('pre', {class: 'code'}, c.dump);
    }

    // ------------------------------------------------------------------ app shell

    var state = {recording: CONFIG.recording !== false, cleanup: [], counts: {}};
    var main, nav, recordButton;

    function enabled(def) { return !CONFIG.watchers || CONFIG.watchers.indexOf(def.watcher) !== -1; }

    function shell() {
        recordButton = h('button', {class: 'icon-btn', type: 'button', onclick: toggleRecording});
        var themeButton = h('button', {class: 'icon-btn', type: 'button', onclick: cycleTheme});
        updateThemeButton(themeButton);
        updateRecordButton();
        nav = h('ul', {class: 'nav'});
        TYPES.forEach(function (def) {
            nav.appendChild(h('li', {}, link('/' + def.slug, [icon(def.icon), h('span', {}, def.label), h('span', {class: 'count', 'data-count': def.type})],
                {class: enabled(def) ? '' : 'disabled', 'data-slug': def.slug, title: enabled(def) ? null : 'This watcher is disabled'})));
        });
        nav.appendChild(h('li', {class: 'nav-sep'}));
        nav.appendChild(h('li', {}, link('/monitored-tags', [icon('monitoring'), h('span', {}, 'Monitoring')], {'data-slug': 'monitored-tags'})));
        main = h('main', {class: 'content'});
        root.replaceChildren(h('div', {class: 'container'},
            h('header', {class: 'header'},
                link('/requests', [logo(), h('span', {class: 'wordmark'}, 'Trace', h('span', {class: 'fox'}, 'Fox')), h('small', {}, 'v' + (CONFIG.version || ''))], {class: 'logo', 'aria-label': 'TraceFox'}),
                h('div', {class: 'header-actions'},
                    recordButton,
                    h('button', {class: 'icon-btn', type: 'button', title: 'Clear all entries', onclick: clearEntries}, icon('trash')),
                    themeButton)),
            h('div', {class: 'layout'}, h('aside', {class: 'sidebar'}, nav), main)));
        refreshCounts();
        setInterval(refreshCounts, 10000);
        setInterval(function () {
            document.querySelectorAll('[data-time]').forEach(function (el) {
                var text = timeAgo(el.getAttribute('data-time'));
                el.textContent = el.classList.contains('sub') ? '(' + text + ')' : text;
            });
        }, 5000);
    }

    function refreshCounts() {
        api('/status').then(function (data) {
            state.counts = data.counts || {};
            if (data.recording !== state.recording) { state.recording = data.recording; updateRecordButton(); }
            nav.querySelectorAll('[data-count]').forEach(function (el) {
                var n = state.counts[el.getAttribute('data-count')];
                el.textContent = n ? (n > 999 ? '999+' : n) : '';
            });
        }).catch(function () {});
    }

    function updateRecordButton() {
        recordButton.replaceChildren(icon(state.recording ? 'pause' : 'play'));
        recordButton.title = state.recording ? 'Pause recording' : 'Resume recording (recording is paused)';
        recordButton.classList.toggle('recording-off', !state.recording);
    }

    function toggleRecording() {
        api('/toggle-recording', {method: 'POST', body: '{}'}).then(function (data) {
            state.recording = data.recording;
            updateRecordButton();
            render();
        });
    }

    function clearEntries() {
        if (!window.confirm('Are you sure you want to delete all TraceFox entries?')) return;
        api('/entries', {method: 'DELETE', body: '{}'}).then(function () { refreshCounts(); render(); });
    }

    function currentTheme() {
        try { return localStorage.getItem('tracefox-theme') || 'system'; } catch (e) { return 'system'; }
    }

    function applyTheme(theme) {
        var dark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
        document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    }

    function updateThemeButton(button) {
        var theme = currentTheme();
        button.replaceChildren(icon(theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'system'));
        button.title = 'Theme: ' + theme;
    }

    function cycleTheme(event) {
        var next = ({system: 'light', light: 'dark', dark: 'system'})[currentTheme()];
        try { localStorage.setItem('tracefox-theme', next); } catch (e) {}
        applyTheme(next);
        updateThemeButton(event.currentTarget);
    }

    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () { applyTheme(currentTheme()); });

    // ------------------------------------------------------------------ router

    function relativePath() {
        var path = window.location.pathname;
        if (BASE && path.indexOf(BASE) === 0) path = path.slice(BASE.length);
        return path.replace(/\/+$/, '') || '/';
    }

    function navigate(url, replace) {
        if (replace) history.replaceState({}, '', url); else history.pushState({}, '', url);
        render();
        window.scrollTo(0, 0);
    }

    document.addEventListener('click', function (event) {
        var a = event.target.closest('a[data-link]');
        if (!a || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        navigate(a.getAttribute('href'));
    });
    window.addEventListener('popstate', render);

    function render() {
        state.cleanup.forEach(function (fn) { fn(); });
        state.cleanup = [];
        var parts = relativePath().split('/').filter(Boolean);
        var params = new URLSearchParams(window.location.search);
        if (!parts.length) { navigate(BASE + '/requests', true); return; }
        var slug = parts[0];
        nav.querySelectorAll('a[data-slug]').forEach(function (a) { a.classList.toggle('active', a.getAttribute('data-slug') === slug); });
        if (slug === 'monitored-tags') { main.replaceChildren(monitoringPage()); return; }
        var def = TYPE_BY_SLUG[slug];
        if (!def) { main.replaceChildren(card('Not Found', h('div', {class: 'empty'}, 'This page does not exist.'))); return; }
        document.title = 'TraceFox - ' + def.label;
        main.replaceChildren(parts[1] ? detailPage(def, parts[1]) : indexPage(def, params));
    }

    // ------------------------------------------------------------------ index page

    function indexPage(def, params) {
        var tag = params.get('tag') || '';
        var familyHash = params.get('family_hash') || '';
        var entries = [];
        var loadingOlder = false;
        var hasMore = false;
        var newest = null;

        var body = h('div', {}, h('div', {class: 'empty'}, h('div', {class: 'spinner'}), 'Scanning…'));
        var newBanner = h('button', {class: 'load-new', type: 'button', style: 'display:none', onclick: function () { load(); }}, 'Load New Entries');
        var input = h('input', {type: 'search', placeholder: 'Search Tag', value: tag});
        var searchTimer = null;
        input.addEventListener('input', function () {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(function () {
                var q = new URLSearchParams(window.location.search);
                if (input.value.trim()) q.set('tag', input.value.trim()); else q.delete('tag');
                history.replaceState({}, '', BASE + '/' + def.slug + (q.toString() ? '?' + q.toString() : ''));
                tag = input.value.trim();
                load();
            }, 350);
        });

        var sub = familyHash ? h('span', {class: 'sub'}, ' — occurrences of one exception · ', link('/' + def.slug, 'show all')) : null;
        var header = h('div', {class: 'card-header'}, h('h2', {}, def.label, sub),
            h('div', {class: 'search'}, icon('search'), input));

        function query(extra) {
            var q = new URLSearchParams({take: '50'});
            if (tag) q.set('tag', tag);
            if (familyHash) q.set('family_hash', familyHash);
            Object.keys(extra || {}).forEach(function (k) { q.set(k, extra[k]); });
            return '/entries/' + def.type + '?' + q.toString();
        }

        function load() {
            newBanner.style.display = 'none';
            return api(query()).then(function (data) {
                entries = data.entries || [];
                hasMore = entries.length >= 50;
                newest = entries.length ? entries[0].sequence : newest;
                draw(data.status);
            }).catch(showError);
        }

        function loadOlder() {
            if (loadingOlder || !entries.length) return;
            loadingOlder = true;
            api(query({before: entries[entries.length - 1].sequence})).then(function (data) {
                var more = data.entries || [];
                entries = entries.concat(more);
                hasMore = more.length >= 50;
                loadingOlder = false;
                draw(data.status);
            }).catch(showError);
        }

        function poll() {
            if (document.hidden) return;
            var q = newest ? {after: newest, take: 1} : {take: 1};
            api(query(q)).then(function (data) {
                if ((data.entries || []).length && (!newest || data.entries[0].sequence > newest)) {
                    if (!entries.length) load(); else newBanner.style.display = '';
                }
            }).catch(function () {});
        }

        function showError(error) {
            body.replaceChildren(h('div', {class: 'empty'}, error.status === 401 ? 'Login required. Reload the page to sign in.' : error.status === 403 ? 'You are not authorized to view TraceFox.' : 'Something went wrong: ' + error.message));
        }

        function draw(status) {
            var nodes = [];
            if (!enabled(def)) nodes.push(h('div', {class: 'notice'}, 'The ' + def.label + ' watcher is disabled in your configuration.'));
            else if (status === 'paused' || !state.recording) nodes.push(h('div', {class: 'notice'}, 'TraceFox is currently paused. New entries will not be recorded.'));
            if (!entries.length) {
                nodes.push(h('div', {class: 'empty'}, icon(def.icon), "We didn't find anything — just empty space."));
                body.replaceChildren.apply(body, nodes);
                return;
            }
            nodes.push(def.customIndex && def.type === 'dump' ? dumpList(entries) : entriesTable(def, entries));
            if (hasMore) nodes.push(h('button', {class: 'load-more', type: 'button', onclick: loadOlder}, 'Load Older Entries'));
            body.replaceChildren.apply(body, nodes);
        }

        load();
        var timer = setInterval(poll, 3000);
        state.cleanup.push(function () { clearInterval(timer); clearTimeout(searchTimer); });
        return h('div', {class: 'card'}, header, newBanner, body);
    }

    function entriesTable(def, entries) {
        var thead = h('thead', {}, h('tr', {}, def.columns.map(function (c) { return h('th', {class: c.cls || ''}, c.label); }), h('th')));
        var tbody = h('tbody');
        entries.forEach(function (entry) {
            var href = BASE + '/' + def.slug + '/' + entry.id;
            var cells = def.columns.map(function (c) {
                return c.raw ? c.render(entry) : h('td', {class: c.cls || ''}, c.render(entry));
            });
            var row = h('tr', {class: 'clickable', onclick: function (event) {
                if (event.target.closest('a')) return;
                if (event.metaKey || event.ctrlKey) { window.open(href, '_blank'); return; }
                navigate(href);
            }}, cells, h('td', {class: 'go'}, h('a', {href: href, 'data-link': '', 'aria-label': 'View entry'}, icon('chevron'))));
            tbody.appendChild(row);
        });
        return h('div', {class: 'table-wrap'}, h('table', {class: 'table'}, thead, tbody));
    }

    function dumpList(entries) {
        return h('div', {}, entries.map(function (entry) {
            return h('div', {class: 'dump-row'},
                h('div', {class: 'dump-meta'},
                    h('span', {class: 'mono'}, locationText(entry.content) || entry.content.type),
                    h('span', {}, link('/dumps/' + entry.id, 'details'), ' · ', h('span', {title: fullDate(entry.created_at), 'data-time': entry.created_at}, timeAgo(entry.created_at)))),
                dumpBody(entry.content));
        }));
    }

    // ------------------------------------------------------------------ detail page

    function detailPage(def, id) {
        var container = h('div', {style: 'display:flex;flex-direction:column;gap:24px'},
            card(def.label.replace(/s$/, '') + ' Details', h('div', {class: 'empty'}, h('div', {class: 'spinner'}), 'Fetching…')));
        api('/entries/' + def.type + '/' + id).then(function (data) {
            var entry = data.entry, batch = data.batch || [];
            var rows = def.details(entry, batch);
            if (def.type !== 'request') rows = rows.concat(parentRows(batch));
            rows.push(tagsRow(entry));
            var nodes = [card(detailTitle(def), detailsTable(rows), link('/' + def.slug, '← Back to ' + def.label, {class: 'sub'}))]
                .concat(flatten(def.sections(entry, batch)));
            if (def.related) nodes.push(relatedEntries(batch));
            container.replaceChildren.apply(container, nodes.filter(Boolean));
        }).catch(function (error) {
            container.replaceChildren(card('Not Found', h('div', {class: 'empty'}, error.status === 404 ? 'This entry no longer exists — it may have been pruned.' : error.message)));
        });
        return container;
    }

    function detailTitle(def) {
        return ({requests: 'Request', commands: 'Command', schedule: 'Scheduled Task', jobs: 'Job', batches: 'Batch', cache: 'Cache',
            dumps: 'Dump', events: 'Event', exceptions: 'Exception', gates: 'Gate', 'client-requests': 'HTTP Client Request', logs: 'Log',
            mail: 'Mail', models: 'Model Action', notifications: 'Notification', queries: 'Query', redis: 'Redis', views: 'View'})[def.slug] + ' Details';
    }

    function relatedEntries(batch) {
        var tabs = [];
        TYPES.forEach(function (def) {
            var items = batch.filter(function (e) { return e.type === def.type; });
            if (!items.length) return;
            tabs.push({label: def.label, count: items.length, content: function () { return entriesTable(def, items); }});
        });
        if (!tabs.length) return null;
        return tabsCard(tabs);
    }

    // ------------------------------------------------------------------ monitoring

    function monitoringPage() {
        document.title = 'TraceFox - Monitoring';
        var list = h('div', {}, h('div', {class: 'empty'}, h('div', {class: 'spinner'})));
        var input = h('input', {type: 'text', placeholder: 'Tag to monitor, e.g. Auth:1 or app.models.User:42'});

        function draw(tags) {
            if (!tags.length) { list.replaceChildren(h('div', {class: 'empty'}, icon('monitoring'), "You're not monitoring any tags.")); return; }
            var tbody = h('tbody', {}, tags.map(function (tag) {
                return h('tr', {},
                    h('td', {}, link('/requests?tag=' + encodeURIComponent(tag), tag, {class: 'badge brand'})),
                    h('td', {class: 'right'}, h('button', {class: 'link-btn', type: 'button', onclick: function () {
                        api('/monitored-tags', {method: 'DELETE', body: JSON.stringify({tag: tag})}).then(function (d) { draw(d.tags); });
                    }}, 'Remove')));
            }));
            list.replaceChildren(h('table', {class: 'table'}, h('thead', {}, h('tr', {}, h('th', {}, 'Tag'), h('th'))), tbody));
        }

        function submit(event) {
            event.preventDefault();
            var tag = input.value.trim();
            if (!tag) return;
            api('/monitored-tags', {method: 'POST', body: JSON.stringify({tag: tag})}).then(function (d) { input.value = ''; draw(d.tags); });
        }

        api('/monitored-tags').then(function (d) { draw(d.tags || []); });
        return h('div', {style: 'display:flex;flex-direction:column;gap:24px'},
            card('Monitoring', h('div', {class: 'card-body'},
                h('p', {style: 'margin:0 0 12px;color:var(--muted)'},
                    'Entries carrying a monitored tag are always recorded — even when the environment filter would drop them.'),
                h('form', {class: 'monitor-form', onsubmit: submit}, input, h('button', {class: 'btn primary', type: 'submit'}, 'Monitor')))),
            card('Monitored Tags', list));
    }

    shell();
    render();
})();
