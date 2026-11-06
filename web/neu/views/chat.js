/* Chat, Twitch und Streamerbot Deck: Chat-Anzeige (mit Senden und Moderation), Anmeldung per Code, Bot, Warnungen, Deck.
   Stellt Bausteine bereit (Neu.chat), die „Live“ und „System“ einbauen. */
(() => {
  'use strict';
  const { h, ico, api, App, ask, toast, run, badge, btn, alertBox, card, row, list, sw, field, input, select, range, seg, ftime, note, copyBtn, view } = Neu;
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* privat */ } } };

  /* ================= Chat-Anzeige ================= */
  const OPT_KEY = 'pb_chatopt';
  const BADGE_TXT = { broadcaster: '●', moderator: '⚔︎', vip: '◆', subscriber: '★', founder: '★', staff: '✱', partner: '✔︎' };
  const BADGE_TIP = { broadcaster: 'Streamer', moderator: 'Moderator', vip: 'VIP', subscriber: 'Abonnent', founder: 'Abonnent (Gründer)', staff: 'Twitch-Mitarbeiter', partner: 'Partner' };
  const EMOTE = 'https://static-cdn.jtvnw.net/emoticons/v2/%s/default/dark/1.0';
  const LINK_RE = /(?:https?:\/\/|www\.)[^\s<>"']+|(?:[a-z0-9-]+\.)+(?:com|net|org|de|tv|gg|io|be|me|co|app|ly|to|fm|ch|at|eu|info|live|dev|xyz|uk|us|ru|fr|nl|es|it|pl)(?![\w-])(?:\/[^\s<>"']*)?/gi;
  const EVHEAD = { sub: n => n > 0 ? `Sub · ${n} Monate` : 'Sub', gift: n => n > 1 ? `Geschenk-Abos · ${n}` : 'Geschenk-Abo', raid: n => n > 0 ? `Raid · ${n} Zuschauer` : 'Raid', cheer: n => `Cheer · ${n} Bits`, announce: () => 'Ankündigung', follow: () => 'Follow', points: n => n > 0 ? `Kanalpunkte · ${n}` : 'Kanalpunkte' };
  const EVICON = { sub: '⭐', gift: '🎁', raid: '🚀', cheer: '💎', announce: '📢', follow: '💜', points: '🎯' };

  function linkTo(raw) {
    let u; try { u = new URL(/^https?:\/\//i.test(raw) ? raw : 'https://' + raw); } catch (e) { return null; }
    if ((u.protocol !== 'https:' && u.protocol !== 'http:') || u.username || u.password) return null;
    const a = h('a', { class: 'lk', href: u.href, target: '_blank', title: u.href, rel: 'noopener noreferrer nofollow' });
    let t = u.hostname.replace(/^www\./, '') + (u.pathname === '/' ? '' : u.pathname) + u.search; if (t.length > 40) t = t.slice(0, 39) + '…'; a.textContent = t; return a;
  }
  function addText(mt, str) {
    let last = 0, x; LINK_RE.lastIndex = 0;
    while ((x = LINK_RE.exec(str))) {
      let raw = x[0]; const trail = raw.match(/[.,;:!?)\]}'"]+$/); if (trail) raw = raw.slice(0, raw.length - trail[0].length);
      if (x.index > 0 && !/^(?:https?:\/\/|www\.)/i.test(raw) && /[\w@.\/-]/.test(str[x.index - 1])) continue;
      const a = raw ? linkTo(raw) : null; if (!a) continue;
      if (x.index > last) mt.append(document.createTextNode(str.slice(last, x.index))); mt.append(a); last = x.index + raw.length; LINK_RE.lastIndex = last;
    }
    if (last < str.length) mt.append(document.createTextNode(str.slice(last)));
  }
  function fillText(mt, m) {
    const cp = Array.from(m.text || ''), sl = (a, b) => cp.slice(a, b).join(''); let pos = 0;
    for (const e of (m.emotes || [])) {
      if (e[1] < pos || e[2] >= cp.length) continue; if (e[1] > pos) addText(mt, sl(pos, e[1]));
      const im = h('img', { class: 'em', alt: sl(e[1], e[2] + 1), loading: 'lazy' });
      im.src = /^https:\/\/(cdn\.betterttv\.net|cdn\.7tv\.app|cdn\.frankerfacez\.com)\//.test(e[0]) ? e[0] : EMOTE.replace('%s', encodeURIComponent(e[0]));
      const et = h('span', { class: 'emt', text: sl(e[1], e[2] + 1) }); mt.append(im, et); im.onerror = () => { im.style.display = 'none'; et.style.display = 'inline'; }; pos = e[2] + 1;
    }
    if (pos < cp.length) addText(mt, sl(pos));
  }

  function feedCard() {
    let opt = { ts: true, badge: true, emote: true, line: true, sig: false, size: 'm' };
    try { Object.assign(opt, JSON.parse(store.get(OPT_KEY) || '{}') || {}); } catch (e) { /* Standard */ }
    const box = h('div', { class: 'chatbox', role: 'log', 'aria-live': 'polite' }), msg = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }), err = h('p', { class: 'muted', style: 'font-size:var(--text-sm);color:var(--danger)' });
    const statusEl = h('span'), evchip = btn('Neues Ereignis ↓', { size: 'sm', variant: 'primary', onclick: () => { box.scrollTop = box.scrollHeight; evchip.hidden = true; } }); evchip.hidden = true;
    const toastEl = h('div', { class: 'alert', data: { tone: 'info' }, hidden: true }), toastT = h('span'), undo = btn('Rückgängig', { size: 'sm' }); toastEl.append(h('div', { class: 'alert__body' }, toastT), undo);
    let since = 0, busy = false, shown = 0, altN = 0, modOn = false, me = '', stick = true, chan = '', lastOk = 0, undoTimer = 0, actx = null;

    function paintOpt() {
      box.classList.toggle('o-nots', !opt.ts); box.classList.toggle('o-nobadge', !opt.badge); box.classList.toggle('o-noemote', !opt.emote); box.classList.toggle('o-noline', !opt.line);
      box.classList.remove('o-s', 'o-l'); if (opt.size === 's' || opt.size === 'l') box.classList.add('o-' + opt.size);
    }
    const optBox = h('div', { class: 'tile', hidden: true, style: 'display:grid;gap:var(--space-1)' },
      [['ts', 'Uhrzeit zeigen'], ['badge', 'Abzeichen zeigen'], ['emote', 'Emotes als Bilder'], ['line', 'Trennlinien'], ['sig', 'Signalton und Vibration bei Ereignissen (Sub, Raid …)']].map(([k, t]) =>
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!opt[k], onchange: e => { opt[k] = e.target.checked; store.set(OPT_KEY, JSON.stringify(opt)); paintOpt(); if (k === 'sig' && e.target.checked) { try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); actx.resume(); } catch (x) { /* nichts */ } } } }), h('span', { class: 'check__text', text: t }))),
      field({ label: 'Schriftgröße', control: select([['s', 'Klein'], ['m', 'Mittel'], ['l', 'Groß']], opt.size, v => { opt.size = v; store.set(OPT_KEY, JSON.stringify(opt)); paintOpt(); }) }));
    paintOpt();

    function evSignal() {
      if (!opt.sig) return; try { if (navigator.vibrate) navigator.vibrate([120, 70, 120]); } catch (e) { /* nichts */ }
      try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); if (actx.state === 'suspended') actx.resume(); [0, .18].forEach((dt, i) => { const o = actx.createOscillator(), g = actx.createGain(); o.type = 'sine'; o.frequency.value = i ? 1175 : 880; o.connect(g); g.connect(actx.destination); const t = actx.currentTime + dt; g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.18, t + .02); g.gain.exponentialRampToValueAtTime(.0001, t + .15); o.start(t); o.stop(t + .16); }); } catch (e) { /* nichts */ }
    }
    function line(m) {
      const d = h('div', { class: 'cm ' + (m.type || 'msg') }), body = h('div', { class: 'cb' });
      const dt = new Date((m.t || 0) * 1000); d.append(h('span', { class: 'ct', title: dt.toLocaleString(), text: String(dt.getHours()).padStart(2, '0') + ':' + String(dt.getMinutes()).padStart(2, '0') }), body);
      if (m.mid) d.dataset.mid = m.mid; if (m.uid) d.dataset.uid = m.uid; if (m.login) d.dataset.login = m.login; if ((m.badges || []).includes('broadcaster')) d.dataset.owner = '1';
      for (const bn of (m.badges || [])) body.append(h('span', { class: 'bdg ' + bn, title: BADGE_TIP[bn] || bn, text: BADGE_TXT[bn] || bn }));
      if (m.name) { const n = h('span', { class: 'cn', translate: 'no', text: m.name }); if (/^#[0-9a-fA-F]{6}$/.test(m.color || '')) n.style.setProperty('--nc', m.color); body.append(n); }
      const mt = h('span', { translate: 'no' }); body.append(' ', mt); fillText(mt, m); d._mt = mt; d._txt = m.text || '';
      if (m.bits && !m.ev) body.append(' ', h('span', { class: 'bt', text: m.bits + ' Bits' }));
      if (m.ev) { const head = EVHEAD[m.ev.k]; if (head) { d.classList.add('ev', 'ev-' + m.ev.k); body.insertBefore(h('span', { class: 'evh' }, (EVICON[m.ev.k] || '') + ' ' + head(m.ev.n || 0)), body.firstChild); } }
      if (modOn) addMm(d); return d;
    }
    function addMm(d) {
      if (d.querySelector('.mm') || !d.dataset.mid || !d.dataset.uid || d.dataset.login === me || d.dataset.owner) return;
      d.append(h('button', { class: 'mm', type: 'button', title: 'Moderation', 'aria-label': 'Moderation', text: '⋯' }));
    }
    function notice(text) { const n = new Date(); box.append(h('div', { class: 'cm notice' }, h('span', { class: 'ct', text: String(n.getHours()).padStart(2, '0') + ':' + String(n.getMinutes()).padStart(2, '0') }), h('div', { class: 'cb', text }))); shown++; }
    function metaEvent(m) {
      if (m.type === 'emotes') { for (const x of box.querySelectorAll('.cm[data-mid]')) if (x.dataset.mid === m.mid && x._mt) { x._mt.textContent = ''; fillText(x._mt, { text: x._txt, emotes: m.emotes }); } return; }
      if (m.type === 'del') { for (const x of box.querySelectorAll('.cm[data-mid]')) if (x.dataset.mid === m.mid) x.classList.add('gone'); return; }
      if (m.type === 'clear') {
        if (!m.uid) { notice('Der Chat wurde geleert.'); return; }
        let who = m.uid; for (const x of box.querySelectorAll('.cm[data-uid]')) if (x.dataset.uid === m.uid) { x.classList.add('gone'); who = x.dataset.login || who; }
        notice(who + (m.seconds ? ': Timeout ' + (m.seconds >= 3600 ? Math.round(m.seconds / 3600) + ' Std' : (m.seconds >= 60 ? Math.round(m.seconds / 60) + ' Min' : m.seconds + ' s')) : ': gebannt'));
      }
    }
    function showToast(text, u) { toastEl.hidden = false; toastT.textContent = text; undo.hidden = !u; undo._u = u || null; clearTimeout(undoTimer); undoTimer = setTimeout(() => { toastEl.hidden = true; }, 8000); }
    async function modDo(body, label) {
      try { const r = await api('POST', '/api/chat/mod', body); showToast(label || r.message, (body.action === 'timeout' || body.action === 'ban') && r.user_id && body.seconds !== 1 ? { action: 'unban', user_id: r.user_id } : null); }
      catch (x) { showToast(x.message, null); }
    }
    undo.addEventListener('click', async () => { const u = undo._u; if (!u) return; undo.hidden = true; await modDo(u, 'Bann aufgehoben'); });
    box.addEventListener('click', e => {
      const mm = e.target.closest('.mm'); if (!mm) return; const rowEl = mm.parentNode, old = rowEl.querySelector('.mrow'); if (old) { old.remove(); return; }
      const mr = h('div', { class: 'mrow' }), uid = rowEl.dataset.uid, mid = rowEl.dataset.mid, who = rowEl.dataset.login || '';
      for (const [txt, body, lab] of [['Löschen', { action: 'delete', message_id: mid }, 'Gelöscht'], ['Alle löschen', { action: 'timeout', user_id: uid, seconds: 1 }, `Alle Nachrichten von ${who} gelöscht`], ['Timeout 10 Min', { action: 'timeout', user_id: uid, seconds: 600 }, 'Timeout 10 Min: ' + who], ['Timeout 1 Std', { action: 'timeout', user_id: uid, seconds: 3600 }, 'Timeout 1 Std: ' + who], ['Bannen', { action: 'ban', user_id: uid }, 'Gebannt: ' + who]])
        mr.append(h('button', { class: 'btn', type: 'button', 'data-size': 'sm', onclick: () => { mr.remove(); modDo(body, lab); } }, txt));
      rowEl.append(mr);
    });
    box.addEventListener('scroll', () => { if (box.clientHeight > 0) stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60; if (stick) evchip.hidden = true; });

    async function poll() {
      if (busy || document.hidden || !box.isConnected || App.view !== 'live') return; busy = true;
      try {
        const since0 = since, d = await api('GET', '/api/chat?since=' + since);
        if (!d || !Array.isArray(d.items) || !Number.isFinite(d.last)) throw new Error('bad'); lastOk = Date.now();
        App.set('twacc', d.account || null);
        const changed = !!(d.channel && chan && d.channel !== chan);
        if (d.last < since0 || changed) { box.textContent = ''; shown = 0; stick = true; evchip.hidden = true; }
        chan = d.channel || chan;
        const near = box.clientHeight > 0 ? box.scrollHeight - box.scrollTop - box.clientHeight < 60 : stick;
        const wasMod = modOn; modOn = !!(d.account && d.account.mod); me = (d.account && d.account.login) || '';
        if (modOn && !wasMod) box.querySelectorAll('.cm').forEach(addMm); if (!modOn && wasMod) box.querySelectorAll('.mm,.mrow').forEach(e => e.remove());
        const first = since0 === 0; let evNew = false;
        for (const m of d.items) { try { if (m.meta) { metaEvent(m); continue; } const r = line(m); if (altN++ & 1) r.classList.add('alt'); box.append(r); shown++; if (m.ev && !first && Date.now() / 1000 - (m.t || 0) < 120) { r.classList.add('fresh'); evNew = true; } } catch (e) { /* Zeile überspringen */ } }
        since = d.last;
        while (shown > 200) { box.removeChild(box.firstChild); shown--; }
        if (near) { if (!box.querySelector('.mrow')) box.scrollTop = box.scrollHeight; }
        if (evNew) { if (!near) evchip.hidden = false; evSignal(); }
        const text = { 'kein-kanal': 'Kein Kanal eingetragen. Unter System > Chat und Deck eintragen.', verbinde: 'Verbinde …', fehler: 'Keine Verbindung zu Twitch.' }[d.state] || '';
        msg.textContent = (d.state === 'ok' && !shown) ? 'Noch keine Nachrichten.' : text;
        statusEl.replaceChildren(d.state === 'ok' ? badge('Verbunden', 'ok') : badge(d.state === 'kein-kanal' ? 'Nicht eingerichtet' : d.state === 'verbinde' ? 'Verbindet …' : 'Getrennt', d.state === 'fehler' ? 'danger' : undefined));
        form.hidden = !(d.account && d.account.state === 'angemeldet');
      } catch (e) { if (Date.now() - lastOk > 10000) { msg.textContent = 'Keine Verbindung zur Box.'; statusEl.replaceChildren(badge('Getrennt', 'danger')); } }
      finally { busy = false; }
    }
    const txt = h('input', { class: 'input', placeholder: 'Nachricht schreiben', maxlength: 500, autocomplete: 'off', 'aria-label': 'Nachricht' }), sendB = btn('Senden', { variant: 'primary' }); sendB.type = 'submit';
    const form = h('form', { class: 'cluster', style: 'flex-wrap:nowrap;--gap:var(--space-2)', hidden: true, onsubmit: async e => {
      e.preventDefault(); const t = txt.value.trim(); if (!t) return; sendB.disabled = true; err.textContent = '';
      try { const r = await api('POST', '/api/chat/send', { text: t }); txt.value = ''; if (t[0] === '/' && r && r.message) { err.textContent = r.message; err.style.color = 'var(--ok)'; setTimeout(() => { err.textContent = ''; }, 8000); } else err.style.color = 'var(--danger)'; }
      catch (x) { err.style.color = 'var(--danger)'; err.textContent = x.message; }
      sendB.disabled = false; txt.focus();
    } }, h('div', { class: 'grow' }, txt), sendB);
    setInterval(poll, 2000); document.addEventListener('neu:view', e => { if (e.detail === 'live') poll(); }); poll();
    const gear = btn('', { size: 'sm', variant: 'ghost', icon: true, label: 'Chat-Anzeige einstellen', ico: 'settings', onclick: () => { optBox.hidden = !optBox.hidden; } });
    const setup = btn('Chat einrichten', { size: 'sm', variant: 'ghost', onclick: () => Neu.goto('system', 'sy-chat') });
    return card({ title: 'Chat', actions: [statusEl, gear], body: [optBox, evchip, box, toastEl, msg, err, form, setup] });
  }

  /* ================= Twitch: Konto, Bot, Warnungen ================= */
  function accountCard() {
    const t = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }), out = h('div', { class: 'stack' }); let a = null, poller = 0, wasWait = false;
    const chIn = input({ placeholder: 'Twitch-Kanal des Streamers', maxlength: 40, autocomplete: 'off' });
    async function chanSave() { try { await api('POST', '/api/twitch', { channel: chIn.value.trim() }); await load(); } catch (x) { toast(x.message); } }
    const post = async (body, p = '/api/twitch/login') => { try { render(await api('POST', p, body)); } catch (x) { toast(x.message); } };
    function render(d) {
      if (!d) return; a = d; const on = d.state === 'angemeldet', wait = d.state === 'wartet', other = d.role === 'none' && !!d.streamer;
      const kids = [];
      if (d.need_channel || !on) kids.push(h('div', { class: 'cluster', style: 'flex-wrap:nowrap' }, h('div', { class: 'grow' }, chIn), btn('Speichern', { size: 'sm', onclick: chanSave })));
      if (document.activeElement !== chIn) chIn.value = d.streamer || '';
      let title;
      if (on) title = 'Angemeldet als ' + d.login + (d.role === 'mod' ? ' · Moderator' : d.role === 'user' ? ' · Zuschauer' : d.mod ? ' · Moderation an' : '');
      else if (wait) title = 'Warte auf die Bestätigung bei Twitch …'; else if (d.state === 'abgelaufen') title = 'Anmeldung abgelaufen'; else if (d.state === 'fehler') title = d.error || 'Anmeldung fehlgeschlagen';
      else if (other) title = d.box_login ? `Streamer: ${d.box_login}. Anmelden als:` : `Streamer (${d.streamer}) noch nicht angemeldet. Anmelden als:`; else title = 'Nicht angemeldet';
      kids.push(h('p', { style: 'font-weight:var(--weight-medium)', text: title }));
      if (wait) {
        const uri = d.uri || '', qr = uri && window.NeuQR ? window.NeuQR.svg(uri) : '';
        kids.push(
          h('div', { class: 'cluster' }, h('div', { class: 'stat' }, h('span', { class: 'stat__label', text: 'Code' }), h('span', { class: 'stat__value mono', text: d.code || '' })),
            qr ? h('div', { html: qr, style: 'width:9rem;background:#fff;border-radius:var(--radius-m);padding:4px' }) : null),
          h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, 'Öffne ', h('a', { href: uri || '#', target: '_blank', rel: 'noopener noreferrer', text: (uri || '').replace(/^https?:\/\//, '').replace(/\?.*$/, '') || 'twitch.tv/activate' }), ' und gib den Code ein.'),
          btn('Abbrechen', { size: 'sm', onclick: () => post({ action: 'cancel' }) }));
      }
      const acts = [];
      if (!on && !wait && !d.need_channel) {
        if (other) { acts.push(btn('Streamer', { variant: 'primary', size: 'sm', onclick: () => post({ action: 'start', all: true }) }), btn('Moderator', { size: 'sm', onclick: () => post({ action: 'start', as: 'mod' }) }), btn('Zuschauer', { size: 'sm', onclick: () => post({ action: 'start', as: 'user' }) })); }
        else acts.push(btn('Mit Twitch anmelden', { variant: 'primary', size: 'sm', onclick: () => post({ action: 'start', all: true }) }));
      }
      if (on) {
        if (!d.mod) acts.push(btn('Moderation einschalten', { size: 'sm', onclick: () => post(d.mod_scope ? { action: 'mod', on: true } : (d.role === 'mod' || d.role === 'user') ? { action: 'start', as: 'mod' } : { action: 'start', all: true }) }));
        else acts.push(btn('Moderation ausschalten', { size: 'sm', onclick: () => post({ action: 'mod', on: false }) }));
        if (!d.events) acts.push(btn('Ereignisse einblenden (Sub, Raid …)', { size: 'sm', onclick: () => post({ action: 'start', events: true }) })); else acts.push(badge('Ereignisse an', 'ok'));
        acts.push(btn('Abmelden', { size: 'sm', variant: 'ghost', onclick: async () => { try { render(await api('POST', '/api/twitch/logout', {})); } catch (x) { toast(x.message); } } }));
      }
      if (acts.length) kids.push(h('div', { class: 'cluster' }, acts));
      out.replaceChildren(...kids);
      if (wait && !poller) poller = setInterval(() => { if (!document.hidden) load(); }, 2000);
      if (!wait && poller) { clearInterval(poller); poller = 0; }
      wasWait = wait;
    }
    async function load() { try { render(await api('GET', '/api/twitch/login')); } catch (e) { /* nichts */ } }
    App.on('twacc', d => { if (d && (!a || JSON.stringify(d) !== JSON.stringify(a))) render(d); });
    document.addEventListener('neu:view', e => { if (e.detail === 'system') load(); }); load();
    return card({ title: 'Twitch-Konto', sub: 'Zum Mitlesen, Schreiben und Moderieren im Chat und für die Warnungen.', body: [t, out] });
  }

  function botCard() {
    const out = h('div', { class: 'stack' }); let poller = 0;
    const post = async action => { try { render(await api('POST', '/api/twitch/bot', { action })); } catch (x) { toast(x.message); } };
    function render(d) {
      const on = d.state === 'angemeldet', wait = d.state === 'wartet', kids = [];
      kids.push(h('p', { style: 'font-weight:var(--weight-medium)', text: on ? 'Bot: ' + d.login : wait ? 'Warte auf die Bestätigung bei Twitch …' : d.state === 'abgelaufen' ? 'Anmeldung des Bots abgelaufen' : d.state === 'fehler' ? (d.error || 'Anmeldung fehlgeschlagen') : 'Kein Bot (das Hauptkonto schreibt)' }));
      if (wait) kids.push(h('div', { class: 'stat' }, h('span', { class: 'stat__label', text: 'Code' }), h('span', { class: 'stat__value mono', text: d.code || '' })), h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, 'Öffne ', h('a', { href: d.uri || '#', target: '_blank', rel: 'noopener noreferrer', text: (d.uri || '').replace(/^https?:\/\//, '').replace(/\?.*$/, '') || 'twitch.tv/activate' }), ' mit dem Konto des Bots.'));
      kids.push(h('div', { class: 'cluster' }, on ? btn('Bot abmelden', { size: 'sm', onclick: () => post('logout') }) : wait ? btn('Abbrechen', { size: 'sm', onclick: () => post('cancel') }) : btn('Bot anmelden', { size: 'sm', variant: 'primary', onclick: () => post('start') })));
      out.replaceChildren(...kids);
      if (wait && !poller) poller = setInterval(() => { if (!document.hidden) load(); }, 2000); if (!wait && poller) { clearInterval(poller); poller = 0; }
    }
    async function load() { try { render(await api('GET', '/api/twitch/bot')); } catch (e) { /* nichts */ } }
    document.addEventListener('neu:view', e => { if (e.detail === 'system') load(); }); load();
    return card({ title: 'Bot-Konto (freiwillig)', sub: 'Ein zweites Konto schreibt die Warnungen, damit sie nicht von deinem Hauptkonto kommen.', body: out });
  }

  function batteryCard() {
    const status = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }), info = note();
    let d = null, dirty = false;
    const on = sw({ label: 'Akku-Warnung im Twitch-Chat', hint: 'Nur bei DJI-Kameras.', onchange: () => mark(), s: 'Akku-Warnung im Chat', k: 'dji akku batterie twitch warnung chat' }), ch = input({ placeholder: 'Kanal', maxlength: 40 }), login = input({ placeholder: 'Benutzername (nur manuell)', autocomplete: 'off' }),
      token = input({ type: 'password', placeholder: 'Token (nur manuell)', autocomplete: 'off' }), thr = input({ type: 'number', min: 1, max: 99 }), msgIn = input({ maxlength: 200 }), live = sw({ label: 'Nur während der Sendung', onchange: () => mark() });
    const mark = () => { dirty = true; save.disabled = false; test.disabled = true; };
    [ch, login, token, thr, msgIn].forEach(i => i.addEventListener('input', mark));
    const save = btn('Speichern', { variant: 'primary', run: async () => { fill(await api('POST', '/api/twitch', { enabled: on.firstChild.checked, channel: ch.value, login: login.value, token: token.value, threshold: parseInt(thr.value, 10), message: msgIn.value, only_live: live.firstChild.checked })); dirty = false; }, ok: 'Gespeichert' });
    const test = btn('Testnachricht senden', { run: async () => { const r = await api('POST', '/api/twitch/test', {}); info.set(r.message, r.ok ? 'ok' : 'bad'); load(); } });
    function fill(x) {
      d = x; const s = x.status || {};
      status.textContent = s.ok == null ? (x.enabled ? 'Noch nichts gesendet' : '') : s.ok ? `Letzte Meldung um ${ftime(s.time)} Uhr: ${s.text}` : `Fehler um ${ftime(s.time)} Uhr: ${s.text}` + (s.retry_at ? ` Neuer Versuch um ${ftime(s.retry_at)} Uhr.` : '');
      status.style.color = s.ok === false ? 'var(--danger)' : '';
      if (!dirty) { on.firstChild.checked = !!x.enabled; ch.value = x.channel || ''; login.value = x.login || ''; token.value = ''; token.placeholder = x.token_set ? 'gespeichert' : 'Token (nur manuell)'; thr.value = x.threshold; msgIn.value = x.message || ''; live.firstChild.checked = x.only_live !== false; }
      const ok = !!((x.token_set && x.channel && x.login) || (x.account && x.channel) || (x.bot && x.channel)); test.disabled = dirty || !ok; save.disabled = !dirty;
    }
    async function load() { try { fill(await api('GET', '/api/twitch')); } catch (e) { /* nichts */ } }
    setInterval(() => { if (App.view === 'system' && !document.hidden) load(); }, 10000); document.addEventListener('neu:view', e => { if (e.detail === 'system') load(); }); load(); save.disabled = true;
    return card({ title: 'Akku-Warnung im Twitch-Chat', sub: 'Die Box schreibt in den Chat, wenn der Akku einer DJI-Kamera niedrig ist.', body: [on, field({ label: 'Warnen unter (%)', control: thr, s: 'Akku-Warnschwelle', k: 'akku prozent warnung grenze' }), field({ label: 'Nachricht', control: msgIn, hint: 'Platzhalter: {Kamera}, {Prozent}', s: 'Text der Akku-Warnung', k: 'akku nachricht text' }), live,
      disclosure('Manuelle Anmeldung (nur wenn keine Anmeldung per Code möglich ist)', field({ label: 'Kanal', control: ch }), field({ label: 'Benutzername', control: login }), field({ label: 'Token', control: token })), status, info], foot: [test, save] });
  }
  const disclosure = Neu.disclosure;

  function sysAlertsCard() {
    const status = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }), info = note(); let d = null, dirty = false;
    const mark = () => { dirty = true; save.disabled = false; test.disabled = true; };
    const chk = (label, hint, s, k) => { const x = sw({ label, hint, onchange: mark, s, k }); return x; };
    const val = x => x.firstChild.checked, setv = (x, v) => { x.firstChild.checked = !!v; };
    const on = chk('Systemwarnungen im Chat', 'Meldet Temperatur, Prozessor, Arbeitsspeicher und Kamera-Bildrate.', 'Systemwarnungen im Chat', 'system warnung temperatur cpu ram chat');
    const tOn = chk('Temperatur'), cOn = chk('Prozessorlast'), rOn = chk('Arbeitsspeicher'), fOn = chk('Kamera unter 20 fps');
    const num = (min, max) => { const i = input({ type: 'number', min, max }); i.addEventListener('input', mark); return i; };
    const tL = num(30, 100), cL = num(10, 100), rL = num(10, 100), cd = num(1, 120);
    const rec = chk('Melden, wenn wieder alles normal ist'), live = chk('Nur während der Sendung');
    const cmdT = chk('Befehl für die Temperatur', 'Zuschauer können die Temperatur im Chat abfragen.'), cmdS = chk('Befehl für den Status'), cmdV = chk('Befehle auch für VIPs'), cmdP = chk('Befehl zum Verpixeln', 'Zum Beispiel „!pixel“.'), cmdPh = chk('Verpixeln nur für Moderatoren');
    const txt = () => { const i = input({ maxlength: 200 }); i.addEventListener('input', mark); return i; };
    const mA = txt(), mO = txt(), mF = txt(), mT = txt(), mSo = txt(), mSw = txt(), mSc = txt();
    [on, tOn, cOn, rOn, fOn, rec, live, cmdT, cmdS, cmdV, cmdP, cmdPh].forEach(x => x.firstChild.addEventListener('change', mark));
    const form = () => ({ enabled: val(on), cooldown: parseInt(cd.value, 10), recover: val(rec), only_live: val(live), cmd_temp: val(cmdT), cmd_status: val(cmdS), cmd_vip: val(cmdV), cmd_pixel: val(cmdP), pixel_head_mod: val(cmdPh), fps_on: val(fOn),
      temp: { on: val(tOn), limit: parseInt(tL.value, 10) }, cpu: { on: val(cOn), limit: parseInt(cL.value, 10) }, ram: { on: val(rOn), limit: parseInt(rL.value, 10) },
      msg_alert: mA.value, msg_ok: mO.value, msg_fps: mF.value, msg_temp: mT.value, msg_st_ok: mSo.value, msg_st_warn: mSw.value, msg_st_crit: mSc.value });
    const save = btn('Speichern', { variant: 'primary', run: async () => { fill(await api('POST', '/api/sysalerts', form())); dirty = false; }, ok: 'Gespeichert' });
    const test = btn('Testnachricht senden', { run: async () => { const r = await api('POST', '/api/twitch/test', {}); info.set(r.message, r.ok ? 'ok' : 'bad'); load(); } });
    function fill(x) {
      d = x; const parts = [];
      if (x.enabled && !x.ready) parts.push('Twitch ist noch nicht eingerichtet: Anmeldung im Reiter „Chat“ oder Bot-Konto bei der Akku-Warnung.');
      if (x.enabled && x.ready && (x.alerting || []).length) parts.push('Gerade über der Grenze: ' + x.alerting.map(k => ({ temp: 'Temperatur', cpu: 'CPU-Last', ram: 'RAM', fps: 'Kamera unter 20 fps' })[k]).join(', '));
      if (x.last) parts.push(x.last.ok ? `Letzte Meldung um ${ftime(x.last.time)} Uhr: ${x.last.text}` : `Fehler um ${ftime(x.last.time)} Uhr: ${x.last.text}`);
      status.textContent = parts.join(' ');
      if (!dirty) { setv(on, x.enabled); [[tOn, tL, 'temp'], [cOn, cL, 'cpu'], [rOn, rL, 'ram']].forEach(([o, l, k]) => { setv(o, x[k].on); l.value = x[k].limit; }); cd.value = x.cooldown; setv(rec, x.recover); setv(live, x.only_live !== false); setv(cmdT, x.cmd_temp); setv(cmdS, x.cmd_status); setv(cmdV, x.cmd_vip); setv(cmdP, x.cmd_pixel); setv(cmdPh, x.pixel_head_mod); setv(fOn, x.fps_on !== false);
        mA.value = x.msg_alert || ''; mO.value = x.msg_ok || ''; mF.value = x.msg_fps || ''; mT.value = x.msg_temp || ''; mSo.value = x.msg_st_ok || ''; mSw.value = x.msg_st_warn || ''; mSc.value = x.msg_st_crit || ''; }
      test.disabled = dirty || !x.ready; save.disabled = !dirty;
    }
    async function load() { try { fill(await api('GET', '/api/sysalerts')); } catch (e) { /* nichts */ } }
    setInterval(() => { if (App.view === 'system' && !document.hidden) load(); }, 10000); document.addEventListener('neu:view', e => { if (e.detail === 'system') load(); }); load(); save.disabled = true;
    const pair = (a, b) => h('div', { class: 'grid-auto', style: '--min:12rem' }, a, b);
    return card({ title: 'Systemwarnungen im Twitch-Chat', sub: 'Die Box meldet im Chat, wenn etwas zu heiß, zu voll oder zu langsam wird.', body: [on,
      h('div', { class: 'tile stack', style: '--gap:var(--space-1)' }, pair(tOn, field({ label: 'Grenze Temperatur (°C)', control: tL })), pair(cOn, field({ label: 'Grenze Prozessor (%)', control: cL })), pair(rOn, field({ label: 'Grenze Arbeitsspeicher (%)', control: rL })), fOn),
      field({ label: 'Mindestabstand zwischen Meldungen (Minuten)', control: cd }), rec, live,
      disclosure('Befehle für Zuschauer', cmdT, cmdS, cmdV, cmdP, cmdPh),
      disclosure('Texte der Meldungen', h('p', { class: 'field__hint' }, 'Platzhalter: {Name}, {Wert}, {Grenze}'), field({ label: 'Warnung', control: mA }), field({ label: 'Wieder normal', control: mO }), field({ label: 'Kamera zu langsam', control: mF }), field({ label: 'Temperatur', control: mT }), field({ label: 'Status: okay', control: mSo }), field({ label: 'Status: Warnung', control: mSw }), field({ label: 'Status: Problem', control: mSc })),
      status, info], foot: [test, save] });
  }

  /* ================= Streamerbot Deck ================= */
  const cleanUrl = v => { v = String(v || '').trim(); if (!v) return ''; if (v.length > 500) return null; let u; try { u = new URL(v); } catch (e) { return null; } return u.protocol === 'https:' && u.hostname && !u.username && !u.password ? u.href : null; };
  let deckSaved = { url: '', width: 600, height: 600, mobile_width: 390, mobile_height: 600 }, deckSub = [];
  async function deckLoad() { try { const d = await api('GET', '/api/deck'); deckSaved = { url: d.url || '', width: +d.width || 600, height: +d.height || 600, mobile_width: +d.mobile_width || 390, mobile_height: +d.mobile_height || 600 }; deckSub.forEach(f => f()); } catch (e) { /* nichts */ } }
  function deckFrameCard() {
    const wrap = h('div'), msg = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }); let shown = null;
    const mobile = () => matchMedia('(max-width:52rem)').matches;
    function layout() { const f = wrap.firstChild; if (!f) return; const d = mobile() ? { w: deckSaved.mobile_width, h: deckSaved.mobile_height } : { w: deckSaved.width, h: deckSaved.height }, avail = wrap.clientWidth || d.w, r = avail / d.w, s = r >= .85 ? 1 : r; f.style.height = Math.round(d.h * s) + 'px'; const fr = f.firstChild; fr.style.width = (s < 1 ? d.w : avail) + 'px'; fr.style.height = d.h + 'px'; fr.style.transform = s < 1 ? 'scale(' + s + ')' : ''; }
    function render() {
      msg.textContent = deckSaved.url ? '' : 'Noch keine Deck-Adresse. Trage sie unter System > Chat und Deck ein.';
      if (shown !== deckSaved.url) { shown = deckSaved.url; wrap.textContent = ''; if (deckSaved.url) { const fr = h('iframe', { src: deckSaved.url, title: 'Streamerbot Deck', loading: 'lazy', referrerpolicy: 'no-referrer', sandbox: 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals', allow: 'fullscreen', style: 'border:0;transform-origin:0 0;background:#000' }); wrap.append(h('div', { style: 'overflow:hidden;border-radius:var(--radius-m)' }, fr)); } }
      layout();
    }
    deckSub.push(render); if (window.ResizeObserver) new ResizeObserver(layout).observe(wrap); window.addEventListener('resize', layout);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) deckLoad(); }); setInterval(() => { if (!document.hidden) deckLoad(); }, 20000); deckLoad();
    return card({ title: 'Streamerbot Deck', actions: [btn('', { size: 'sm', variant: 'ghost', icon: true, label: 'Deck einstellen', ico: 'settings', onclick: () => Neu.goto('system', 'sy-chat') })], body: [wrap, msg] });
  }
  function deckSettingsCard() {
    const u = input({ placeholder: 'https://streamer.bot/decks/…', autocomplete: 'off' }), w = input({ type: 'number', min: 320, max: 4000 }), hh = input({ type: 'number', min: 320, max: 4000 }), mw = input({ type: 'number', min: 200, max: 1200 }), mh = input({ type: 'number', min: 320, max: 4000 }), info = note();
    function fill() { if (document.activeElement && [u, w, hh, mw, mh].includes(document.activeElement)) return; u.value = deckSaved.url; w.value = deckSaved.width; hh.value = deckSaved.height; mw.value = deckSaved.mobile_width; mh.value = deckSaved.mobile_height; }
    deckSub.push(fill);
    const save = btn('Speichern', { variant: 'primary', run: async () => {
      const c = cleanUrl(u.value); if (c === null) { info.set('Die Adresse muss mit https:// beginnen.', 'bad'); return; }
      try { const d = await api('POST', '/api/deck', { url: c, width: +w.value || 600, height: +hh.value || 600, mobile_width: +mw.value || 390, mobile_height: +mh.value || 600 }); deckSaved = { url: d.url || '', width: +d.width || 600, height: +d.height || 600, mobile_width: +d.mobile_width || 390, mobile_height: +d.mobile_height || 600 }; deckSub.forEach(f => f()); info.set('Gespeichert. Gilt auf allen Geräten.', 'ok'); } catch (e) { info.set('Nicht auf der Box gespeichert: ' + e.message, 'bad'); }
    } });
    return card({ title: 'Streamerbot Deck', sub: 'Zeigt dein Deck auf der Live-Seite. Adresse und Größen gelten für alle Geräte.', body: [field({ label: 'Adresse des Decks', control: u, hint: 'Leer lassen entfernt das Deck. Die Höhe muss für das Auswahlfeld der Seiten reichen (mindestens 320).', s: 'Streamerbot Deck Adresse', k: 'deck streamer.bot iframe' }),
      h('div', { class: 'grid-auto', style: '--min:9rem' }, field({ label: 'Breite Desktop (px)', control: w }), field({ label: 'Höhe Desktop (px)', control: hh }), field({ label: 'Breite Handy (px)', control: mw }), field({ label: 'Höhe Handy (px)', control: mh })), info], foot: [save] });
  }

  Neu.chat = { feedCard, accountCard, botCard, batteryCard, sysAlertsCard, deckFrameCard, deckSettingsCard };
})();
