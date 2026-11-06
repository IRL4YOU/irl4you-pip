/* Schnellsteuerung: Hauptkamera, kleine Bilder und Ton ändern, ohne die Sendung neu zu starten.
   Zwei Orte, eine Bedienung: die Karte „Bild und Ton jetzt“ (Live) und die Leiste am unteren Rand des Handys. */
(() => {
  'use strict';
  const { h, ico, api, App, toast, run, badge, btn, row, list, sw, select, field } = Neu;
  const SLOT = ['main', 'pip', 'pip2', 'pip3'];
  const post = (p, b) => api('POST', p, b);
  async function act(fn) { try { const r = await fn(); if (r && r.note) toast(r.note); } catch (e) { toast(e.message); } finally { App.data['send:refresh'] && App.data['send:refresh'](); } }
  const A = {
    main: c => act(() => post('/api/pipeline/swap', { with: c.key })),
    view: c => act(() => post('/api/pipeline/view', { visible: { [c.slot]: c.hidden } })),
    active: c => act(() => post('/api/pipeline/active', { key: c.key, active: c.inactive })),
    audio: v => act(() => post('/api/pipeline/view', { audio: v })),
    mute: v => act(() => post('/api/pipeline/view', { mute: v })),
  };
  /* Live gehen und beenden: gleiche Regeln wie der große Knopf auf der Live-Seite */
  function liveState(s) {
    const running = s.state === 'running';
    if (s.active) return running ? { label: 'Stop', variant: 'danger', icon: 'stop' } : { label: '…', variant: '', icon: 'live', busy: true };
    return s.can_start ? { label: 'Live', variant: 'live', icon: 'play' } : { label: 'Live', variant: '', icon: 'play', dim: true };
  }
  async function goLive() {
    const s = App.data.send; if (!s) return;
    try {
      if (s.active) {
        if (s.state !== 'running') return toast('Die Sendung startet oder endet gerade, bitte kurz warten.');
        await post('/api/send', { action: 'stop' });
      } else if (s.can_start) await post('/api/send', { action: 'start', confirm: true });
      else { toast(s.state === 'refused' && s.message ? s.message : ((s.reasons || []).join(' · ') || 'Noch nicht bereit')); Neu.goto('live'); return; }
    } catch (e) { toast(e.message); }
    setTimeout(() => App.data['send:refresh'] && App.data['send:refresh'](), 700);
  }
  const footer = () => { const s = App.data.send; return s && s.footer && s.footer.cams && s.footer.cams.length ? s.footer : null; };
  const name = c => c.name || c.key;

  /* ---------- Vollständige Steuerung (Liste mit Knöpfen) ---------- */
  function body(f) {
    const rows = f.cams.map(c => {
      const end = [];
      if (c.main) end.push(badge('Hauptbild', 'accent'));
      else {
        end.push(btn('Zum Hauptbild', { size: 'sm', disabled: c.inactive || c.state === 'offline', onclick: () => A.main(c) }));
        end.push(btn(c.hidden ? 'Einblenden' : 'Ausblenden', { size: 'sm', disabled: c.inactive, onclick: () => A.view(c) }));
        end.push(btn(c.inactive ? 'Aktivieren' : 'Deaktivieren', { size: 'sm', variant: 'ghost', onclick: () => A.active(c) }));
      }
      const desc = [c.main ? 'Großes Bild' : c.inactive ? 'Deaktiviert (nicht im Stream, kein Ton)' : c.hidden ? 'Bild ausgeblendet' : 'Kleines Bild', c.face ? 'Facecam' : '',
        f.audio && f.audio.key === c.key ? (f.audio.mute ? 'Ton stumm' : 'Ton von hier') : '', c.state === 'offline' ? 'kein Signal' : ''].filter(Boolean).join(' · ');
      return row({ icon: 'camera', title: name(c), desc, end });
    });
    const a = f.audio || {}, opts = f.cams.filter(c => !c.inactive).map(c => [SLOT[c.slot], name(c)]);
    const audioSel = select(opts, a.src || 'main', v => A.audio(v));
    return [list(rows), h('div', { class: 'cluster', style: 'padding:var(--space-3) var(--space-4) var(--space-4)' }, field({ label: 'Tonquelle', control: audioSel }),
      sw({ checked: !!a.mute, label: 'Ton stumm', onchange: (v, el) => run(el, () => post('/api/pipeline/view', { mute: v })) }))];
  }
  const fill = (box, f) => box.replaceChildren(...body(f));

  /* ---------- Leiste am unteren Rand (nur Handy) ---------- */
  const bar = h('div', { class: 'qbar', id: 'qbar', hidden: true, role: 'toolbar', 'aria-label': 'Schnellsteuerung' });
  document.body.append(bar);
  let sig = '', sheetBox = null;
  const setH = () => { document.documentElement.style.setProperty('--qbar-h', bar.hidden ? '0px' : bar.offsetHeight + 'px'); document.body.toggleAttribute('data-qbar', !bar.hidden); };
  const side = document.querySelector('.side');
  const setNav = () => { if (side) document.documentElement.style.setProperty('--nav-h', side.offsetHeight + 'px'); setH(); };
  if (window.ResizeObserver) { new ResizeObserver(setNav).observe(side || document.body); new ResizeObserver(setH).observe(bar); } window.addEventListener('resize', setNav);

  /* Eine Taste mit drei Bedienungen: kurz, doppelt, lang. Der kurze Druck wartet einen Moment, ob ein zweiter folgt (nur wenn es eine Doppel-Bedienung gibt). */
  function gesture(el, g) {
    let pressT = 0, clickT = 0, fired = false, sx = 0, sy = 0;
    const cancel = () => clearTimeout(pressT);
    el.addEventListener('pointerdown', e => { if (e.button > 0) return; fired = false; sx = e.clientX; sy = e.clientY; cancel(); if (g.long) pressT = setTimeout(() => { fired = true; try { if (navigator.vibrate) navigator.vibrate(30); } catch (x) { /* nichts */ } g.long(); }, 520); });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(t => el.addEventListener(t, cancel));
    el.addEventListener('pointermove', e => { if (Math.abs(e.clientX - sx) > 12 || Math.abs(e.clientY - sy) > 12) cancel(); });
    el.addEventListener('contextmenu', e => e.preventDefault());
    el.addEventListener('click', e => {
      if (fired) { fired = false; e.preventDefault(); return; }               // nach langem Drücken kein zusätzlicher Klick
      if (e.detail === 0 || !g.dbl) { g.tap(); return; }                     // Tastatur oder keine Doppel-Bedienung: sofort
      if (clickT) { clearTimeout(clickT); clickT = 0; g.dbl(); return; }
      clickT = setTimeout(() => { clickT = 0; g.tap(); }, 270);
    });
  }
  /* Deaktivierte Kamera: einschalten, das kleine Bild bleibt aus (Ton nutzbar) */
  const wake = c => act(async () => { await post('/api/pipeline/active', { key: c.key, active: true }); await post('/api/pipeline/view', { visible: { [c.slot]: false } }); toast('Aktiviert, Bild ausgeblendet: Ton nutzbar.'); });
  function chip(c, a) {
    const b = h('button', { class: 'qchip', type: 'button', 'aria-pressed': String(!!c.main), title: name(c) + (c.main ? ' ist das Hauptbild' : ' zum Hauptbild machen'),
      data: Object.assign({}, c.main && { main: '' }, c.inactive && { off: '' }, c.hidden && { hid: '' }) }, h('span', { text: name(c) }), a.key === c.key ? ico(a.mute ? 'micoff' : 'speaker', 'sm') : null);
    gesture(b, {
      tap: () => {
        if (c.main) return toast(`${name(c)} ist schon das Hauptbild.`);
        if (c.inactive) return wake(c);
        if (c.state === 'offline') return toast(`${name(c)} ist nicht verbunden und kann nicht Hauptbild werden.`);
        A.main(c);
      },
      dbl: () => {
        if (c.main) return toast('Das Hauptbild lässt sich nicht ausblenden. Zum Tauschen kurz auf eine andere Kamera drücken.');
        if (c.inactive) return wake(c);
        A.view(c);                                                           // kleines Bild aus- oder einblenden (der Ton bleibt)
      },
      long: () => {
        if (c.main) return toast('Das Hauptbild lässt sich nicht deaktivieren.');
        A.active(c); toast(c.inactive ? 'Aktiviert.' : 'Deaktiviert: nicht mehr im Stream, Ton nicht nutzbar.');
      } });
    return b;
  }
  const big = b => { const i = b.querySelector('.icon'); if (i) i.removeAttribute('data-size'); return b; };
  function draw() {
    const s = App.data.send, f = footer();
    if (!s) { bar.hidden = true; sig = ''; setH(); return; }
    const sg = JSON.stringify([f, s.active, s.state, s.can_start]); if (sg === sig) { bar.hidden = false; return; } sig = sg;
    const st = liveState(s), a = (f && f.audio) || {}, multi = !!f && f.cams.length >= 2;
    const go = h('button', { class: 'qlive', type: 'button', disabled: !!st.busy, 'aria-label': st.dim ? 'Live gehen (noch nicht bereit)' : st.label, data: Object.assign({}, st.variant && { tone: st.variant }, st.dim && { dim: '' }), onclick: goLive }, ico(st.icon), h('span', { text: st.label }));
    const rows = [], tools = [go];
    if (multi) rows.push(h('div', { class: 'qbar__row qbar__cams', role: 'group', 'aria-label': 'Hauptbild wählen' }, ...f.cams.map(c => chip(c, a))));
    if (f) {
      const an = a.name || a.key || '', one = !multi || (a.next || '') === (a.src || '');
      const aud = h('button', { class: 'qtool qaud', type: 'button', title: 'Tonquelle wechseln: ' + an, 'aria-label': 'Tonquelle wechseln: ' + an }, ico(a.mute ? 'micoff' : 'speaker'), h('span', { text: an }));
      gesture(aud, { tap: () => one ? toast('Es gibt nur eine Tonquelle. Lang drücken schaltet stumm oder wieder laut.') : A.audio(a.next || 'main'),
        long: () => { if (!s || !s.active) return toast('Stumm schalten geht nur während der Sendung.'); A.mute(!a.mute); } });
      const mute = big(Neu.iconBtn(a.mute ? 'micoff' : 'mic', a.mute ? 'Ton einschalten' : 'Ton stumm schalten', () => { if (!s || !s.active) return toast('Stumm schalten geht nur während der Sendung.'); A.mute(!a.mute); }, { variant: a.mute ? 'danger' : '', size: '' }));
      mute.setAttribute('aria-pressed', String(!!a.mute));
      const more = big(Neu.iconBtn('sliders', 'Alle Kamera- und Toneinstellungen', () => { sheetBox = h('div', { class: 'stack' }); fill(sheetBox, footer() || f); Neu.sheet({ title: 'Bild und Ton jetzt', body: [sheetBox, h('p', { class: 'field__hint', text: 'Tipp für die Leiste: Eine Kamera kurz antippen macht sie zum Hauptbild, doppelt antippen blendet ihr kleines Bild aus oder ein, lang drücken deaktiviert oder aktiviert die Kamera. Beim Ton-Knopf wechselt kurz die Tonquelle, lang schaltet stumm.' })], foot: [btn('Schließen', { onclick: Neu.closeSheet })] }); }, { variant: '', size: '' }));
      tools.push(aud, mute, more);
    }
    rows.push(h('div', { class: 'qbar__row' }, ...tools));
    bar.replaceChildren(...rows); bar.hidden = false; setH();
  }
  App.on('send', () => { draw(); if (sheetBox && sheetBox.isConnected) { const f = footer(); if (f) fill(sheetBox, f); } });
  Neu.quick = { body, fill };
  Neu.live = { state: liveState, go: goLive };
})();
