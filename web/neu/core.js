/* IRL4YOU BOX · neue Oberfläche · Grundfunktionen: Schnittstelle, Daten-Abfrage, kleine Bausteine (Element-Helfer), Dialoge. */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'selected', 'open', 'indeterminate', 'readOnly', 'min', 'max', 'step']);

  /* ---- Elemente bauen. Text wird immer als Text eingesetzt (kein HTML aus Daten). ---- */
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'html') e.innerHTML = v;                 /* nur für selbst erzeugte Bilder (QR-Code), nie für Daten */
      else if (k === 'data') Object.assign(e.dataset, v);
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (PROPS.has(k)) e[k] = v;
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat(3)) { if (kid == null || kid === false) continue; e.append(kid.nodeType ? kid : document.createTextNode(String(kid))); }
    return e;
  }
  function ico(name, size) {
    const s = document.createElementNS(NS, 'svg'); s.setAttribute('class', 'icon'); s.setAttribute('aria-hidden', 'true'); if (size) s.dataset.size = size;
    const u = document.createElementNS(NS, 'use'); u.setAttribute('href', '#i-' + name); s.append(u); return s;
  }
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const loc = () => (window.PB_I18N && window.PB_I18N.locale && window.PB_I18N.locale()) || 'de-DE';
  const fmt = (v, d = 1) => v == null || isNaN(v) ? '–' : Number(v).toLocaleString(loc(), { minimumFractionDigits: d, maximumFractionDigits: d });
  const dur = s => { s = Math.max(0, Math.round(s)); const p = n => String(n).padStart(2, '0'); return `${p(Math.floor(s / 3600))}:${p(Math.floor(s % 3600 / 60))}:${p(s % 60)}`; };

  /* ---- Schnittstelle ---- */
  /* Kennung dieses Browsers für Twitch-Anmeldung und Chat (gleicher Schlüssel wie in der bisherigen Oberfläche) */
  let ownerMem = '';
  function ownerKey() {
    try { const k = localStorage.getItem('pb_owner_key'); if (k && /^[A-Za-z0-9_-]{32,128}$/.test(k)) return k; } catch (e) { /* privat */ }
    if (ownerMem) return ownerMem;
    const b = crypto.getRandomValues(new Uint8Array(32)); let t = ''; b.forEach(x => { t += String.fromCharCode(x); });
    ownerMem = btoa(t).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    try { localStorage.setItem('pb_owner_key', ownerMem); } catch (e) { /* privat */ }
    return ownerMem;
  }
  async function api(method, path, body) {
    const opt = { method, cache: 'no-store' }, hdr = {};
    if (path.indexOf('/api/chat') === 0 || path.indexOf('/api/twitch/log') === 0) hdr['x-pb-owner'] = ownerKey();
    if (path === '/api/twitch/login' && body && body.action === 'start') body.owner = ownerKey();
    if (body !== undefined) { hdr['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    opt.headers = hdr;
    const r = await fetch(path, opt); let d = null;
    try { d = await r.json(); } catch (e) { /* keine JSON-Antwort */ }
    if (r.status === 401) { location.reload(); throw new Error('Nicht angemeldet'); }
    if (!r.ok) throw new Error((d && d.error) || ('Fehler ' + r.status));
    return d;
  }

  /* ---- Gemeinsame Daten: App.data[schlüssel], App.on(schlüssel, fn), App.view = aktuelle Ansicht ---- */
  const App = { data: {}, subs: {}, view: 'live',
    set(k, v) { this.data[k] = v; (this.subs[k] || []).forEach(f => { try { f(v); } catch (e) { console.error(k, e); } }); },
    on(k, f) { (this.subs[k] = this.subs[k] || []).push(f); if (this.data[k] !== undefined) f(this.data[k]); } };
  document.addEventListener('ds:view', e => { App.view = e.detail; const ev = new CustomEvent('neu:view', { detail: e.detail }); document.dispatchEvent(ev); });

  /* Abfrage in festem Takt; pausiert, solange die Seite verdeckt ist oder `when` false liefert. */
  function poll(key, path, ms, when) {
    let busy = false, last = 0;
    const run = async (force) => {
      if (busy || (document.hidden && !force)) return;
      if (!force && when && !when()) return;
      busy = true;
      try { App.set(key, await api('GET', path)); App.set(key + ':ok', true); last = Date.now(); }
      catch (e) { App.set(key + ':ok', false); }
      busy = false;
    };
    setInterval(run, ms); run(true);
    document.addEventListener('neu:view', () => { if (Date.now() - last > 800) run(false); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) run(true); });
    return () => run(true);
  }

  /* ---- Dialoge ---- */
  function ask({ title, text, ok = 'OK', danger = false }) {
    const d = $('#confirm'); $('#cf-t').textContent = title; $('#cf-x').textContent = text || '';
    const b = $('#cf-ok'); b.textContent = ok; b.dataset.variant = danger ? 'danger' : 'primary';
    return new Promise(res => { d.returnValue = ''; d.addEventListener('close', () => res(d.returnValue === 'yes'), { once: true }); d.showModal(); });
  }
  function sheet({ title, body, foot }) {
    const d = $('#sheet'); $('#sh-t').textContent = title;
    const b = $('#sh-b'); b.replaceChildren(...[].concat(body).flat(3).filter(Boolean)); const f = $('#sh-f'); f.replaceChildren(...[].concat(foot || []).flat(3).filter(Boolean));
    f.hidden = !foot || !foot.length; if (!d.open) d.showModal(); return d;
  }
  const closeSheet = () => $('#sheet').close();
  /* Texteingabe im Dialog (statt Browser-Abfrage): liefert den Text oder null */
  function askText({ title, label, value = '', ok = 'Speichern', hint, maxlength = 60 }) {
    return new Promise(res => {
      const i = h('input', { class: 'input', value, maxlength, autocomplete: 'off' }); let done = false;
      const fin = v => { if (done) return; done = true; $('#sheet').removeEventListener('close', onClose); closeSheet(); res(v); };
      const onClose = () => fin(null);
      const go = h('button', { class: 'btn', type: 'button', 'data-variant': 'primary', onclick: () => fin(i.value) }, ok);
      i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); fin(i.value); } });
      sheet({ title, body: [h('div', { class: 'field' }, label && h('label', { class: 'field__label', text: label }), i, hint && h('span', { class: 'field__hint', text: hint }))], foot: [h('button', { class: 'btn', type: 'button', onclick: () => fin(null) }, 'Abbrechen'), go] });
      $('#sheet').addEventListener('close', onClose, { once: true }); setTimeout(() => { i.focus(); i.select(); }, 50);
    });
  }
  const toast = (m, tone) => window.ds.toast(m);

  /* Knopf sperren, solange eine Aktion läuft; Fehler als Meldung zeigen. */
  async function run(btn, fn, okMsg) {
    if (btn) btn.setAttribute('aria-busy', 'true');
    try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
    catch (e) { toast(e.message || 'Fehlgeschlagen'); return undefined; }
    finally { if (btn) btn.removeAttribute('aria-busy'); }
  }

  /* ---- Bausteine ---- */
  const badge = (text, tone, pulse) => h('span', { class: 'badge', data: tone ? { tone } : {} }, tone === 'live' || pulse ? h('span', { class: 'dot', data: { pulse: '' } }) : h('span', { class: 'dot' }), text);
  function btn(text, o = {}) {
    const b = h('button', { class: 'btn', type: 'button', onclick: o.onclick, disabled: o.disabled, data: Object.assign({}, o.variant && { variant: o.variant }, o.size && { size: o.size }, o.icon && { icon: '' }), 'aria-label': o.label }, o.ico && ico(o.ico, 'sm'), text);
    if (o.run) b.addEventListener('click', () => run(b, o.run, o.ok));
    return b;
  }
  function alertBox({ tone = 'info', title, text, actions, icon }) {
    const ic = ico(icon || (tone === 'ok' ? 'check' : tone === 'info' ? 'info' : 'warning')); ic.setAttribute('class', 'icon alert__icon');
    return h('div', { class: 'alert', data: { tone }, role: tone === 'danger' ? 'alert' : null }, ic,
      h('div', { class: 'alert__body' }, title && h('span', { class: 'alert__title', text: title }), text && h('span', { text }), actions && actions.length ? h('div', { class: 'alert__actions' }, actions) : null));
  }
  function card({ title, sub, actions, body, foot, flush }) {
    return h('section', { class: 'card' },
      (title || actions) && h('div', { class: 'card__head' }, h('div', null, title && h('h2', { class: 'card__title', text: title }), sub && h('p', { class: 'card__sub', text: sub })), actions && h('div', { class: 'cluster' }, actions)),
      h('div', { class: 'card__body', data: flush ? { flush: '' } : {}, style: flush ? 'padding-top:var(--space-3)' : null }, body),
      foot && h('div', { class: 'card__foot' }, foot));
  }
  /* Listenzeile. s = Suchtitel (macht die Zeile über die Suche auffindbar), k = Suchwörter. */
  function row({ icon, title, desc, end, onclick, s, k, id }) {
    const main = h('span', { class: 'row__main' }, h('span', { class: 'row__title' }, title), desc != null && desc !== '' && h('span', { class: 'row__desc' }, desc));
    const kids = [icon && h('span', { class: 'row__icon' }, ico(icon)), main, end && h('span', { class: 'row__end' }, end)];
    const e = h(onclick ? 'button' : 'li', { class: 'row', type: onclick ? 'button' : null, onclick, id, data: s ? { s, k: k || '' } : {} }, kids);
    if (onclick) { const li = h('li', null, e); if (s) { li.dataset.s = s; li.dataset.k = k || ''; e.removeAttribute('data-s'); e.removeAttribute('data-k'); } return li; }
    return e;
  }
  /* Knopf nur mit Symbol (zum Beispiel Stift zum Umbenennen); der Name steht als Beschriftung für Screenreader und als Hinweis */
  const iconBtn = (name, label, onclick, o = {}) => h('button', { class: 'btn', type: 'button', onclick, title: label, 'aria-label': label, data: Object.assign({ icon: '', variant: 'ghost', size: 'sm' }, o) }, ico(name, 'sm'));
  /* Name mit Symbol-Knopf dahinter; das letzte Wort bleibt beim Knopf, damit der Stift nie allein in einer Zeile steht */
  const named = (text, b) => { const t = String(text), i = t.lastIndexOf(' '); return h('span', { class: 'row__name' }, i > 0 ? t.slice(0, i + 1) : '', h('span', { class: 'nowrap' }, i > 0 ? t.slice(i + 1) : t, b)); };
  const list = (...rows) => h('ul', { class: 'list' }, rows.flat());
  const sk = (s, k) => s ? { s, k: k || '' } : {};
  function sw({ checked, label, hint, onchange, disabled, aria, s, k }) {
    const i = h('input', { type: 'checkbox', role: 'switch', checked: !!checked, disabled, 'aria-label': aria || (label ? null : 'Schalter'), onchange: e => onchange && onchange(e.target.checked, e.target) });
    return h('label', { class: 'switch', data: sk(s, k) }, i, label && h('span', { class: 'switch__text' }, label, hint && h('small', null, hint)));
  }
  function field({ label, hint, control, id, s, k }) {
    const lid = id || ('f' + Math.random().toString(36).slice(2, 8)); if (control) control.id = lid;
    return h('div', { class: 'field', data: sk(s, k) }, label && h('label', { class: 'field__label', for: lid, text: label }), control, hint && h('span', { class: 'field__hint', text: hint }));
  }
  const input = (o = {}) => h('input', Object.assign({ class: 'input', type: 'text' }, o));
  const select = (opts, value, onchange) => h('select', { class: 'select', onchange: onchange && (e => onchange(e.target.value)) },
    opts.map(o => { const [v, t] = Array.isArray(o) ? o : [o, o]; return h('option', { value: v, selected: String(v) === String(value) }, t); }));
  function seg(name, opts, value, onchange, label) {
    return h('div', { class: 'seg', role: 'radiogroup', 'aria-label': label || name },
      opts.map(([v, t]) => h('label', null, h('input', { type: 'radio', name, value: v, checked: String(v) === String(value), onchange: () => onchange(v) }), h('span', null, t))));
  }
  function range({ label, min, max, step = 1, value, unit = '', onchange, oninput, s, k }) {
    const id = 'r' + Math.random().toString(36).slice(2, 8), out = h('span', { class: 'range__value' }, value + unit);
    const i = h('input', { type: 'range', id, min, max, step, value, 'data-unit': unit });
    const sync = () => { i.style.setProperty('--fill', ((i.value - min) / (max - min) * 100) + '%'); out.textContent = i.value + unit; };
    i.addEventListener('input', () => { sync(); oninput && oninput(+i.value); }); i.addEventListener('change', () => onchange && onchange(+i.value)); sync();
    return h('div', { class: 'range', data: sk(s, k) }, h('div', { class: 'range__head' }, h('label', { for: id, text: label }), out), i);
  }
  const disclosure = (title, ...body) => h('details', { class: 'disclosure' }, h('summary', null, title), h('div', { class: 'disclosure__body' }, body));
  const pageHead = (title, sub, actions) => h('div', { class: 'page-head' }, h('div', null, h('h1', { text: title }), sub != null && h('p', null, sub)), actions && h('div', { class: 'cluster' }, actions));
  const toneOf = l => l === 'ok' || l === 'good' ? 'ok' : l === 'warn' ? 'warn' : l === 'crit' || l === 'bad' || l === 'danger' ? 'danger' : undefined;

  /* Ansicht registrieren: legt <section data-view=...> an. */
  const views = {}, slots = {};
  function view(name, build) {
    const sec = h('section', { class: 'view', 'aria-label': name, data: { view: name }, hidden: true });
    $('#views').append(sec); views[name] = sec; build(sec); return sec;
  }


  /* ---- Weitere Bausteine ---- */
  async function copyText(t) {
    if (navigator.clipboard && window.isSecureContext) { try { await navigator.clipboard.writeText(t); return true; } catch (e) { /* weiter mit Ersatz */ } }
    const ta = h('textarea', { readOnly: true, style: 'position:fixed;left:-9999px;top:0' }); ta.value = t; document.body.append(ta); ta.select(); ta.setSelectionRange(0, t.length);
    let ok = false; try { ok = document.execCommand('copy'); } catch (e) { /* nichts */ } ta.remove(); return ok;
  }
  const copyBtn = (getText, label = 'Kopieren', size = 'sm') => { const b = btn(label, { size, ico: 'link' }); b.addEventListener('click', async () => { const ok = await copyText(typeof getText === 'function' ? getText() : getText); toast(ok ? 'Kopiert' : 'Kopieren nicht möglich: bitte von Hand markieren'); }); return b; };
  function prog(value, text, tone) {
    return h('div', { class: 'stack', style: '--gap:var(--space-1)' }, text ? h('div', { class: 'split' }, h('span', { class: 'muted', style: 'font-size:var(--text-sm)', text }), h('span', { class: 'num', style: 'font-size:var(--text-sm)', text: Math.round(value) + ' %' })) : null,
      h('div', { class: 'meter', data: tone ? { tone } : {} }, h('span', { style: `--value:${Math.max(0, Math.min(100, value))}%` })));
  }
  /* Kleine Markdown-Auswahl (##, -, **fett**, `code`) als Elemente, ohne HTML aus Daten */
  function md(text) {
    const inl = t => { const out = []; let rest = String(t); const re = /\*\*(.+?)\*\*|`([^`]+)`/; let m; while ((m = re.exec(rest))) { if (m.index) out.push(rest.slice(0, m.index)); out.push(m[1] != null ? h('b', null, m[1]) : h('code', null, m[2])); rest = rest.slice(m.index + m[0].length); } out.push(rest); return out; };
    const root = h('div', { class: 'notes-md stack', style: '--gap:var(--space-2);font-size:var(--text-sm)' }); let ul = null;
    String(text || '').split('\n').forEach(raw => { const l = raw.replace(/\s+$/, '');
      if (/^##\s+/.test(l)) { ul = null; root.append(h('h4', { style: 'margin-top:var(--space-2)' }, inl(l.replace(/^##\s+/, '')))); }
      else if (/^\s*[-*]\s+/.test(l)) { if (!ul) { ul = h('ul', { style: 'padding-left:var(--space-5);display:grid;gap:var(--space-1)' }); root.append(ul); } ul.append(h('li', null, inl(l.replace(/^\s*[-*]\s+/, '')))); }
      else if (!l.trim()) ul = null; else if (ul && /^\s+\S/.test(l)) ul.lastChild.append(' ', ...inl(l.trim())); else { ul = null; root.append(h('p', null, inl(l))); } });
    return root;
  }
  /* Reiter innerhalb einer Ansicht: T = tabbed(sec); T.add(id, titel, knoten) */
  function tabbed(sec, label) {
    const list = h('div', { class: 'tabs', role: 'tablist', 'aria-label': label || 'Bereiche' }), panels = h('div');
    sec.append(list, panels); let first = true;
    return { add(id, title, node) {
      list.append(h('button', { class: 'tab', role: 'tab', type: 'button', 'aria-selected': first ? 'true' : 'false', 'aria-controls': id, onclick: () => document.dispatchEvent(new CustomEvent('neu:tab', { detail: id })) }, title));
      panels.append(h('div', { id, hidden: !first, role: 'tabpanel' }, node)); first = false; return node;
    }, list, panels };
  }
  const kv = (label, value) => h('div', { class: 'split', style: 'font-size:var(--text-sm)' }, h('span', { class: 'muted', text: label }), h('span', { class: 'num' }, value));
  const fdate = t => t ? new Date(t * 1000).toLocaleString(loc(), { dateStyle: 'medium', timeStyle: 'short' }) : '–';
  const ftime = t => t ? new Date(t * 1000).toLocaleTimeString(loc(), { hour: '2-digit', minute: '2-digit' }) : '–';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  /* Aktion mit Rückmeldung im Bereich (statt Hinweisfenster) */
  const note = (tone = 'muted') => { const e = h('p', { class: tone, style: 'font-size:var(--text-sm)', role: 'status' }); e.set = (t, c) => { e.textContent = t || ''; e.style.color = c === 'bad' ? 'var(--danger)' : c === 'ok' ? 'var(--ok)' : ''; }; return e; };

  /* Zu einer Ansicht (und optional zu einem Reiter) springen */
  function goto(viewName, tabId) {
    const a = document.querySelector(`[data-view-link="${viewName}"]`); if (a) a.click();
    if (tabId) setTimeout(() => { const t = document.querySelector(`[role="tab"][aria-controls="${tabId}"]`); if (t) t.click(); }, 30);
  }

  window.Neu = { named, iconBtn, loc, askText, goto, slots, copyText, copyBtn, prog, md, tabbed, kv, fdate, ftime, sleep, note, ownerKey, h, ico, $, $$, fmt, dur, api, App, poll, ask, sheet, closeSheet, toast, run, badge, btn, alertBox, card, row, list, sw, field, input, select, seg, range, disclosure, pageHead, toneOf, view, views };
})();
