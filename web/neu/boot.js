/* Start der neuen Oberfläche: Kopfzeile, Suche über alle Einstellungen (Strg+K). */
(() => {
  'use strict';
  const { h, $, $$, App, badge } = Neu;

  /* ---------- Kopfzeile: Sendestatus und Warnlampe ---------- */
  function head() {
    const s = App.data.send, m = App.data.metrics;
    const el = $('#hb-live');
    if (s) {
      const running = s.active && s.state === 'running';
      const txt = running ? 'Live' : s.active ? 'Startet …' : s.can_start ? 'Bereit' : 'Nicht live';
      el.replaceChildren(h('span', { class: 'dot', data: running ? { pulse: '' } : {} }), h('span', { text: txt }));
      el.dataset.tone = running ? 'live' : s.can_start || s.active ? 'accent' : '';
    }
    const l = m && m.light, hl = $('#hb-light'), lv = l && ({ ok: 'ok', good: 'ok', warn: 'warn', crit: 'crit', bad: 'crit' })[l.level];
    if (lv) {                                                    // Ampel: immer sichtbar, wie in der bisherigen Oberfläche
      const txt = { ok: 'Alles okay', warn: 'Warnung', crit: 'Problem' }[lv], tone = { ok: 'ok', warn: 'warn', crit: 'danger' }[lv];
      if (hl.dataset.lv !== lv) { hl.replaceChildren(h('span', { class: 'dot' }), h('span', { text: txt })); hl.dataset.lv = lv; }
      hl.hidden = false; hl.dataset.tone = tone; const rs = (l.reasons || []).map(r => r.text);
      hl.title = rs.length ? rs.join(' · ') : 'Alles in Ordnung' + (l.live ? '' : ' (keine Sendung)');
    } else hl.hidden = true;
    const go = $('#hb-go');                                      // Live-Knopf auch in der Kopfzeile (Computer)
    if (s && Neu.live) { const st = Neu.live.state(s); go.hidden = false; go.replaceChildren(Neu.ico(st.icon, 'sm'), h('span', { text: st.label })); go.dataset.variant = st.variant; go.disabled = !!st.busy; if (st.dim) go.dataset.dim = ''; else delete go.dataset.dim; }
  }
  $('#hb-go').addEventListener('click', () => Neu.live && Neu.live.go());
  App.on('send', head); App.on('metrics', head);

  /* ---------- Suche ---------- */
  const pal = $('#palette'), q = $('#q'), pl = $('#pl'), pc = $('#pc');
  const catName = {}; $$('.nav a').forEach(a => { catName[a.dataset.viewLink] = a.textContent.trim(); });
  const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ß/g, 'ss');
  let index = [], shown = [], active = 0;
  const tr = s => (window.PB_I18N && window.PB_I18N.tr ? window.PB_I18N.tr(s) : s);
  const navName = v => { const a = $(`.nav a[data-view-link="${v}"]`); return (a ? a.textContent.trim() : '') || catName[v] || v; };
  const txt = el => (el.textContent || '').replace(/\s+/g, ' ').trim();
  /* Zusatzwörter: Wer „Backup“, „Speedtest“ oder „wifi“ eintippt, findet trotzdem die deutsche Stelle */
  const ALIAS = [['sicherung', 'backup sichern wiederherstellen einspielen restore export import'], ['leitung', 'speedtest speed test geschwindigkeit uplink upload messen internet'], ['tailscale', 'vpn fernzugriff remote funnel'], ['hotspot', 'ap access point'], ['verpixel', 'dsgvo privacy blur unkenntlich datenschutz'], ['wlan', 'wifi wi-fi'], ['kamera', 'camera cam'], ['bitrate', 'qualität bandbreite'], ['neu starten', 'reboot restart'], ['herunterfahren', 'shutdown poweroff'], ['abmelden', 'logout'], ['protokoll', 'log logs fehlersuche'], ['hell', 'dunkel dark light theme modus'], ['akku', 'batterie battery'], ['ton', 'audio mikrofon mic stumm mute'], ['sprache', 'language'], ['update', 'aktualisieren version'], ['passwort', 'password kennwort'], ['vorschau', 'preview'], ['chat', 'twitch nachrichten'], ['statistik', 'obs homepage widget'], ['bildschirm', 'display wach standby']];
  const aliasOf = s => { const n = norm(s); return ALIAS.filter(([k]) => n.includes(k)).map(([, v]) => v).join(' '); };
  /* Index: (1) von Hand beschriftete Einstellungen mit Suchwörtern, (2) automatisch alles Übrige: Ansichten, Reiter, Karten (samt ihrem ganzen Text)
     und die Beschriftungen von Feldern, Schaltern und Zeilen. So ist auch ohne Handarbeit jedes Wort auffindbar, zum Beispiel „Tailscale“. */
  function build() {
    const out = [], seen = new Set();
    const push = (el, view, title, group, kw, kind) => {
      title = (title || '').replace(/\s+/g, ' ').trim(); if (!title || title.length > 90 || !el || !view) return;
      const key = norm(title) + '|' + view + '|' + norm(group); if (seen.has(key)) return; seen.add(key);
      out.push({ el, view, title, path: [navName(view), group].filter(Boolean).join(' › '), hay: norm([title, kw || '', group, navName(view), aliasOf(title + ' ' + group)].join(' ')), kind, n: out.length });
    };
    const viewOf = el => { const v = el.closest('[data-view]'); return v ? v.dataset.view : ''; };
    const cardTitle = el => { const c = el.closest('.card'); const t = c && c.querySelector('.card__title'); return t ? txt(t) : ''; };
    $$('#views [data-s]').forEach(el => { const de = el.dataset.s; push(el, viewOf(el), tr(de), cardTitle(el), de + ' ' + (el.dataset.k || ''), 0); });
    $$('.nav a').forEach(a => { const v = a.dataset.viewLink, sec = $(`#views [data-view="${v}"]`); if (sec) push(sec, v, txt(a), '', '', 1); });
    $$('#views [role="tab"]').forEach(t => { const p = document.getElementById(t.getAttribute('aria-controls')); if (p) push(p.firstElementChild || p, viewOf(t), txt(t), '', txt(p).slice(0, 1200), 1); });
    $$('#views .page-head .btn').forEach(b => { const t = txt(b); if (t && !b.closest('[hidden]')) push(b, viewOf(b), t, '', '', 1); });
    $$('#views .card').forEach(c => { const t = c.querySelector('.card__title'); if (t) push(c, viewOf(c), txt(t), '', txt(c).slice(0, 1500), 1); });
    $$('#views .field__label, #views .switch__text, #views .row__title, #views .check__text, #views .range__head label, #views .stat__label').forEach(l => {
      const first = l.firstChild && l.firstChild.nodeType === 3 ? l.firstChild.textContent : txt(l);
      push(l.closest('.field, .switch, .row, .check, .range, .tile') || l, viewOf(l), first, cardTitle(l), '', 2);
    });
    index = out;
  }
  const hl = (t, terms) => { const nt = norm(t); if (nt.length !== t.length) return [t]; for (const w of terms) { const m = nt.indexOf(w); if (m >= 0) return [t.slice(0, m), h('mark', null, t.slice(m, m + w.length)), t.slice(m + w.length)]; } return [t]; };
  const MAXR = 40;
  function render() {
    const terms = norm(q.value).split(/\s+/).filter(Boolean);
    const all = !terms.length ? index.filter(i => i.kind === 0).slice(0, 8) : index.map(i => { if (!terms.every(t => i.hay.includes(t))) return null; const nt = norm(i.title); return { i, score: (nt.startsWith(terms[0]) ? 0 : nt.includes(terms[0]) ? 1 : 2) * 10 + i.kind }; })
      .filter(Boolean).sort((a, b) => a.score - b.score || a.i.n - b.i.n).map(x => x.i);
    shown = all.slice(0, MAXR); active = 0;
    pl.replaceChildren(...(shown.length ? shown.map((i, n) => h('button', { class: 'pal__item', role: 'option', type: 'button', 'aria-selected': String(n === 0), data: { n } }, Neu.ico('settings'), h('span', null, h('span', null, hl(i.title, terms)), h('small', { text: i.path }))))
      : [h('div', { class: 'empty' }, Neu.ico('search', 'lg'), h('b', { text: 'Nichts gefunden' }), h('span', { text: 'Versuche ein anderes Wort, zum Beispiel „Akku“ oder „Server“.' }))]));
    pc.textContent = terms.length ? (all.length > MAXR ? `${MAXR} von ${all.length} Treffern` : all.length + ' Treffer') : 'Vorschläge';
  }
  const mark = n => { [...pl.children].forEach((c, k) => c.setAttribute('aria-selected', String(k === n))); pl.children[n]?.scrollIntoView({ block: 'nearest' }); };
  function go(i) {
    pal.close();
    $(`[data-view-link="${i.view}"]`).click();
    let p = i.el.parentElement;                                   // Reiter öffnen, falls die Zeile in einem Reiter liegt
    while (p && p.id !== 'views') { if (p.id) { const t = $(`[role="tab"][aria-controls="${p.id}"]`); if (t) t.click(); } p = p.parentElement; }
    setTimeout(() => { i.el.scrollIntoView({ block: 'center', behavior: 'smooth' }); i.el.classList.remove('flash'); void i.el.offsetWidth; i.el.classList.add('flash'); }, 80);
  }
  function openSearch() { build(); if (!pal.open) pal.showModal(); render(); q.focus(); }
  $('#open-search').addEventListener('click', openSearch);
  pal.addEventListener('close', () => { q.value = ''; });
  pl.addEventListener('click', e => { const it = e.target.closest('.pal__item'); if (it) go(shown[+it.dataset.n]); });
  q.addEventListener('input', render);
  q.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); mark(active); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); mark(active); }
    else if (e.key === 'Enter' && shown[active]) { e.preventDefault(); go(shown[active]); }
  });
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch(); } });

  /* ---------- Warnlampe: Gründe zeigen ---------- */
  $('#hb-light').addEventListener('click', () => { const l = (App.data.metrics || {}).light; if (!l) return; Neu.sheet({ title: 'Hinweise der Box', body: (l.reasons || []).length ? (l.reasons || []).map(r => Neu.alertBox({ tone: r.level === 'crit' ? 'danger' : 'warn', text: r.text })) : [h('p', { class: 'muted', text: 'Alles in Ordnung' + (l.live ? '' : ' (keine Sendung)') })], foot: [Neu.btn('Schließen', { onclick: Neu.closeSheet })] }); });
  
  /* ---------- Bildschirm wach halten (Wake Lock, sonst unsichtbares Video) ---------- */
  (() => {
    let lock = null, vid = null, tick = null, on = false, acquiring = false; try { on = localStorage.getItem('pb_awake') === '1'; } catch (e) { /* privat */ }
    function viaVideo(start) {
      if (!start) { if (vid) { vid.pause(); vid.srcObject = null; vid.remove(); vid = null; } clearInterval(tick); return true; } if (vid) return true;
      try { const c = document.createElement('canvas'); c.width = c.height = 2; const x = c.getContext('2d'); x.fillStyle = '#000'; tick = setInterval(() => { x.fillRect(0, 0, 2, 2); }, 1000); const v = document.createElement('video'); v.muted = true; v.setAttribute('playsinline', ''); v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:0;top:0'; v.srcObject = c.captureStream(1); document.body.appendChild(v); vid = v; v.play().catch(() => { }); return true; } catch (e) { return false; }
    }
    async function acquire() {
      if (!on || document.hidden || lock || vid || acquiring) return; acquiring = true;
      try { if (navigator.wakeLock) { const s = await navigator.wakeLock.request('screen'); if (!on || lock) { try { s.release(); } catch (e) { /* nichts */ } return; } lock = s; s.addEventListener('release', () => { if (lock === s) lock = null; }); return; } } catch (e) { lock = null; } finally { acquiring = false; }
      if (on) viaVideo(true);
    }
    const release = () => { try { if (lock) lock.release(); } catch (e) { /* nichts */ } lock = null; viaVideo(false); };
    window.neuAwake = v => { on = v; if (on) acquire(); else release(); };
    document.addEventListener('visibilitychange', () => { if (document.hidden) { lock = null; viaVideo(false); } else acquire(); }); document.addEventListener('click', () => { if (on && !lock && !vid) acquire(); }); acquire();
  })();

  /* ---------- Hinweis nach einem Update ---------- */
  try { const v = sessionStorage.getItem('pb_updated'); if (v !== null) { sessionStorage.removeItem('pb_updated'); setTimeout(() => Neu.toast('Update installiert: Die Box läuft jetzt mit Version ' + (v || 'neu') + '.'), 800); } } catch (e) { /* privat */ }

  /* ---------- Streammodus: sperrt Server/Bitrate und Fernzugriff gegen versehentliches Ändern ---------- */
  (() => {
    const b = $('#sm-btn'); let on = false; try { on = localStorage.getItem('pb_sendemodus') === '1'; } catch (e) { /* privat */ }
    const apply = () => { document.body.classList.toggle('sm', on); b.setAttribute('aria-pressed', String(on)); b.querySelector('span').textContent = on ? 'Streammodus: an' : 'Streammodus: aus'; b.dataset.variant = on ? 'primary' : ''; try { localStorage.setItem('pb_sendemodus', on ? '1' : '0'); } catch (e) { /* privat */ } };
    b.addEventListener('click', () => { on = !on; apply(); }); apply();
  })();

  /* ---------- Sprache (gleiche Einstellung wie in der bisherigen Oberfläche) ---------- */
  $('#lang-btn').addEventListener('click', () => {
    const i = window.PB_I18N; if (!i) return Neu.toast('Die Sprachumschaltung ist nicht verfügbar.');
    const cur = i.lang();
    const grid = h('div', { class: 'langgrid', translate: 'no' }, i.languages().map(l => h('button', { class: 'btn', type: 'button', 'aria-pressed': String(l.code === cur), data: l.code === cur ? { variant: 'primary' } : {}, onclick: async () => { await i.set(l.code); Neu.closeSheet(); } }, l.name)));
    Neu.sheet({ title: 'Sprache', body: [grid], foot: [Neu.btn('Schließen', { onclick: Neu.closeSheet })] });
  });

  /* ---------- Zurück zur bisherigen Oberfläche: Wahl merken, damit sie beim nächsten Öffnen bleibt ---------- */
  const toOld = () => { try { localStorage.setItem('pb_ui', 'alt'); } catch (e) { /* privat */ } };
  $('#oldui').addEventListener('click', toOld);
  Neu.toOld = () => { toOld(); location.href = '/'; };

  /* ---------- Handy: Sprache, Hell/Dunkel und Bildschirm in einem Menü ---------- */
  $('#more-btn').addEventListener('click', () => {
    const i = window.PB_I18N, cur = i && i.languages().find(l => l.code === i.lang());
    let awake = false; try { awake = localStorage.getItem('pb_awake') === '1'; } catch (e) { /* privat */ }
    Neu.sheet({ title: 'Weitere Einstellungen', body: [Neu.list(
      Neu.row({ icon: 'globe', title: 'Sprache', desc: cur ? cur.name : '', end: Neu.ico('right'), onclick: () => { Neu.closeSheet(); setTimeout(() => $('#lang-btn').click(), 60); } }),
      Neu.row({ icon: 'sun', title: 'Hell oder Dunkel', desc: 'Wechselt die Darstellung', end: Neu.ico('right'), onclick: () => $('[data-theme-toggle]').click() }),
      Neu.row({ icon: 'info', title: 'Hilfetexte anzeigen', desc: 'Erklärungen unter den Überschriften', end: (() => { const w = Neu.sw({ checked: Neu.help ? Neu.help.on() : false, aria: 'Hilfetexte anzeigen', onchange: v => Neu.help && Neu.help.set(v) }); w.querySelector('input').dataset.helpsw = ''; return w; })() }),
      Neu.row({ icon: 'home', title: 'Zur bisherigen Oberfläche', desc: 'Die frühere Ansicht öffnen', end: Neu.ico('right'), onclick: () => Neu.toOld() }),
      Neu.row({ icon: 'monitor', title: 'Bildschirm wach halten', desc: 'Solange die Seite offen ist', end: (() => { const w = Neu.sw({ checked: awake, aria: 'Bildschirm wach halten', onchange: v => Neu.awake && Neu.awake.set(v) }); w.querySelector('input').dataset.awakesw = ''; return w; })() }))],
      foot: [Neu.btn('Schließen', { onclick: Neu.closeSheet })] });
  });

  /* ---------- Bildschirm wach halten: Knopf in der Kopfzeile (Handy), Schalter im Menü und unter System bleiben im Gleichklang ---------- */
  (() => {
    const b = $('#awake-btn'), get = () => { try { return localStorage.getItem('pb_awake') === '1'; } catch (e) { return false; } };
    const paint = () => { const on = get(); b.setAttribute('aria-pressed', String(on)); b.title = on ? 'Bildschirm bleibt an (Antippen: wieder aus)' : 'Bildschirm wach halten'; $$('input[data-awakesw]').forEach(i => { i.checked = on; }); };
    Neu.awake = { get, set(v) { try { localStorage.setItem('pb_awake', v ? '1' : '0'); } catch (e) { /* privat */ } window.neuAwake && window.neuAwake(v); paint(); } };
    b.addEventListener('click', () => { Neu.awake.set(!get()); Neu.toast(get() ? 'Der Bildschirm bleibt an, solange diese Seite offen ist.' : 'Der Bildschirm darf sich wieder ausschalten.'); });
    paint();
  })();

  /* ---------- Verpixeln (Datenschutz) ein- und ausschalten: Knopf in der Kopfzeile, Text am Computer, nur Symbol am Handy ---------- */
  (() => {
    const b = $('#px-btn'), lab = b.querySelector('span'); let busy = false;
    Neu.poll('dshead', '/api/datenschutz', 10000, () => true);
    const st = () => { const d = App.data.dshead || {}, c = d.config || {}, ready = !!d.installiert && !!c.lizenz_ok; return { d, c, ready, on: ready && !!c.an, up: !!(d.status && d.status.up), sending: !!(App.data.send && App.data.send.active) }; };
    function paint() {
      const s = st(); b.setAttribute('aria-pressed', String(s.on)); b.toggleAttribute('data-dim', !s.ready); b.toggleAttribute('data-warn', s.on && !s.up);
      lab.textContent = s.on ? 'Verpixeln: an' : 'Verpixeln: aus';
      b.title = !s.ready ? 'Verpixeln (Datenschutz): noch nicht eingerichtet, Antippen öffnet die Einrichtung'
        : s.on ? (s.up ? 'Köpfe und Kennzeichen werden verpixelt. Antippen schaltet aus.' : 'Verpixeln ist an, der Erkennungsdienst startet noch. Antippen schaltet aus.') : 'Verpixeln ist aus. Antippen schaltet ein.';
      b.setAttribute('aria-label', 'Verpixeln ' + (s.on ? 'an' : 'aus') + ': ' + (s.ready ? 'umschalten' : 'einrichten'));
    }
    App.on('dshead', paint); App.on('send', paint);
    async function set(v, sending) {
      busy = true; b.setAttribute('aria-busy', 'true');
      try {
        await Neu.api('POST', '/api/datenschutz', { an: v });
        if (!sending) { Neu.toast(v ? 'Verpixeln ist an. Es gilt ab dem nächsten Start der Sendung.' : 'Verpixeln ist aus.'); }
        else {
          if (v) {                                                       // Erst warten, bis die Erkennung läuft, sonst bleibt das Hauptbild schwarz
            let up = false; Neu.toast('Der Erkennungsdienst startet, das dauert bis zu 40 Sekunden …');
            for (let i = 0; i < 40 && !up; i++) { const d = await Neu.api('GET', '/api/datenschutz'); App.set('dshead', d); up = !!(d.status && d.status.up); if (!up) await Neu.sleep(1000); }
            if (!up) { Neu.toast('Gespeichert, aber der Erkennungsdienst läuft nicht. Die Sendung wurde nicht neu gestartet.'); return; }
          }
          const r = await Neu.api('POST', '/api/datenschutz', { restart_send: true }); Neu.toast(r.note || 'Die Sendung wurde neu gestartet.');
        }
      } catch (e) { Neu.toast(e.message || 'Fehlgeschlagen'); }
      finally { busy = false; b.removeAttribute('aria-busy'); try { App.set('dshead', await Neu.api('GET', '/api/datenschutz')); } catch (e) { /* weiter */ } }
    }
    b.addEventListener('click', async () => {
      if (busy) return; const s = st();
      if (!s.ready) { Neu.toast('Die Erkennung ist noch nicht eingerichtet. Hier kannst du sie einrichten.'); Neu.goto('bild', 'b-ds'); return; }
      const v = !s.on;
      if (s.sending) {
        const ok = await Neu.ask({ title: v ? 'Verpixeln einschalten?' : 'Verpixeln ausschalten?', text: (v ? '' : 'Danach werden Köpfe und Kennzeichen nicht mehr verpixelt. ') + 'Die laufende Sendung startet dafür kurz neu (etwa 5 Sekunden ohne Bild).', ok: v ? 'Einschalten' : 'Ausschalten', danger: !v });
        if (!ok) return;
      }
      set(v, s.sending);
    });
    paint();
  })();
})();
