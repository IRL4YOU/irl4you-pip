/* Kopfzeile: Verbindungsqualität (Upload der letzten 30 Sekunden) und Kamera-Punkte, wie in der bisherigen Oberfläche. */
(() => {
  'use strict';
  const { h, $, App, fmt } = Neu;
  const box = $('#hb-meters'), sig = $('#hb-sig'), dots = $('#hb-dots'), line = $('#hb-line'), val = $('#hb-sigv'), smax = $('#hb-smax'), smid = $('#hb-smid'), smin = $('#hb-smin');
  let okAt = 0, lastM = null;
  const upTotal = m => Neu.up.wire(m);   // Upload-Wert und Kurve kommen aus Neu.up (gemeinsam mit der Live-Seite)

  /* 0 (nichts geht durch) bis 4 (gut); null = es wird nicht gesendet */
  function level(m) {
    const s = m.details && m.details.send; if (!s) return null;
    const ups = Object.values(m.uplinks || {}).filter(Boolean), on = ups.filter(u => u.state === 'an').length;
    if (ups.length && !on) return 0;
    let q = 4; if (ups.length && on < ups.length) q -= 1;
    const want = (s.bitrate_kbps || 0) / 1000;
    if (want > 0) { const r = upTotal(m) / want, backlog = s.snd_buf_ms != null && s.snd_buf_ms > 300; if (r < .1) q -= 2; else if (backlog && r < .5) q -= 2; else if (backlog && r < .8) q -= 1; }
    if (s.rtt_ms != null) { if (s.rtt_ms > 1000) q -= 2; else if (s.rtt_ms > 400) q -= 1; }
    if (s.snd_buf_ms != null) { if (s.snd_buf_ms > 3000) q -= 2; else if (s.snd_buf_ms > 1500) q -= 1; }
    return Math.max(0, Math.min(4, q));
  }
  function draw() {
    const vw = Neu.up.view(); if (!vw) return;
    const { plot, cut, top, WIN } = vw;
    val.textContent = fmt(vw.last, 1);
    smax.textContent = fmt(top, top % 1 ? 1 : 0); smid.textContent = fmt(top / 2, 1); smin.textContent = '0';   // Skala wie in der bisherigen Oberfläche (Mbit/s)
    line.setAttribute('points', plot.map(x => (Math.min(60, (x.t - cut) / WIN * 60)).toFixed(1) + ',' + (27 - Math.max(0, Math.min(top, x.v)) / top * 26).toFixed(1)).join(' '));
  }

  const camTone = c => c.state === 'live' ? (c.pic === 'wartet' || c.pic === 'aus' ? 'warn' : 'ok') : (c.state === 'unknown' ? '' : 'danger');
  const T = s => (window.PB_I18N && window.PB_I18N.tr ? window.PB_I18N.tr(s) : s);
  const camTitle = c => (c.name || c.key || 'Kamera') + ': ' + T(c.state !== 'live' ? (c.state === 'unknown' ? 'Status unbekannt' : 'Kein Signal')
    : c.pic === 'an' ? 'Sendet und ist im Bild' : c.pic === 'wartet' ? `Sendet, kommt in ${c.pic_wait} s ins Bild` : c.pic === 'aus' ? 'Sendet, ist noch nicht im Bild' : 'Sendet');
  const quiet = c => c.parked ? 2 : (c.state === 'live' || c.state === 'unknown' ? 0 : 1);
  function drawDots(m) {
    const l = (m.cameras || []).filter(c => !c.parked).sort((a, b) => quiet(a) - quiet(b)).slice(0, 4);
    dots.replaceChildren(...l.map(c => h('span', { class: 'hdot', data: camTone(c) ? { tone: camTone(c) } : {}, title: camTitle(c) })));
    dots.setAttribute('aria-label', l.map(camTitle).join('; '));
  }
  function paint() {
    const m = lastM; if (!m) return; const s = App.data.send;
    const q = level(m), active = !!(s && s.active);
    sig.hidden = !active;
    const tone = q == null ? '' : q >= 3 ? 'ok' : q === 2 ? 'warn' : 'danger';
    if (tone) sig.dataset.tone = tone; else delete sig.dataset.tone;
    const tip = q == null ? 'Verbindung: keine Messung (es wird nicht gesendet)' : q >= 3 ? 'Verbindung: gut' : q === 2 ? 'Verbindung: mäßig' : 'Verbindung: schlecht';
    sig.title = tip; sig.setAttribute('aria-label', tip);
    box.hidden = sig.hidden && !dots.children.length;
  }
  App.on('metrics', m => {
    okAt = Date.now(); lastM = m;
    drawDots(m); draw(); paint();
  });
  App.on('send', paint);
  setInterval(() => {
    if (document.hidden || !lastM) return;
    const stale = okAt > 0 && Date.now() - okAt > 8000; sig.classList.toggle('stale', stale);
    if (stale) { val.textContent = '–'; sig.title = 'Verbindung: keine Messwerte von der Box'; } else draw();
  }, 500);
})();
