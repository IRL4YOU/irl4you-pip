/* Bild > Datenschutz (DSGVO): Köpfe und Kennzeichen verpixeln, Lizenz, Einrichtung der Erkennung, Zonen ohne Verpixelung. */
(() => {
  'use strict';
  const { h, api, App, ask, toast, run, badge, btn, alertBox, card, sw, field, input, range, disclosure, note, sleep, prog } = Neu;
  const T = Neu.slots.bild, box = h('div', { class: 'stack', style: '--gap:var(--space-5)' });
  T.add('b-ds', 'Datenschutz', box);
  const CLAMP = v => Math.max(0, Math.min(1, v)), MIN = 0.02, HANDLE = 0.035, pct = v => Math.round(v * 100), round = z => z.map(v => Math.round(v * 1000) / 1000);
  let last = null, zones = [], licText = '', licLoaded = false, dirty = false, saveT = 0, flow = 0, timer = 0, sig = '';
  const info = note(), err = note(), cancel = btn('Neustart abbrechen', { size: 'sm', onclick: () => { stopped = true; flow++; cancel.hidden = true; info.set('Gespeichert. Die Änderung gilt, sobald die Sendung neu gestartet wird.'); } }); cancel.hidden = true; let stopped = false;
  let form = { an: false, kopf: true, kennz: true, zweit: true, delay_ms: 2000 };

  /* ---------- Zonen-Zeichner ---------- */
  const cv = h('canvas', { width: 640, height: 360, 'aria-label': 'Zonen zeichnen', style: 'width:100%;height:auto;display:block;touch-action:none;cursor:crosshair;border-radius:var(--radius-m);position:relative' }), img = h('img', { alt: '', hidden: true, style: 'position:absolute;inset:0;width:100%;height:100%;object-fit:fill;border-radius:var(--radius-m);pointer-events:none' });
  const zoneBox = h('div', { style: 'position:relative' }, img, cv), zoneList = h('div', { class: 'stack', style: '--gap:var(--space-1)' });
  let drag = null;
  function draw() {
    const g = cv.getContext('2d'), W = cv.width, H = cv.height; g.clearRect(0, 0, W, H);
    if (img.hidden) { g.fillStyle = '#1b1f27'; g.fillRect(0, 0, W, H); g.strokeStyle = 'rgba(255,255,255,.12)'; g.lineWidth = 1; for (let i = 1; i < 10; i++) { g.beginPath(); g.moveTo(W * i / 10, 0); g.lineTo(W * i / 10, H); g.stroke(); } for (let i = 1; i < 6; i++) { g.beginPath(); g.moveTo(0, H * i / 6); g.lineTo(W, H * i / 6); g.stroke(); } }
    zones.forEach((z, i) => { const x = z[0] * W, y = z[1] * H, w = (z[2] - z[0]) * W, hh = (z[3] - z[1]) * H; g.fillStyle = 'rgba(255,200,0,.22)'; g.fillRect(x, y, w, hh); g.strokeStyle = '#ffc800'; g.lineWidth = 2; g.strokeRect(x, y, w, hh); g.fillStyle = '#ffc800'; g.font = 'bold 16px sans-serif'; g.fillText(String(i + 1), x + 6, y + 18); for (const [cx, cy] of [[x, y], [x + w, y], [x, y + hh], [x + w, y + hh]]) g.fillRect(cx - 5, cy - 5, 10, 10); });
    if (drag && drag.mode === 'new') { const x = Math.min(drag.x0, drag.x1) * W, y = Math.min(drag.y0, drag.y1) * H, w = Math.abs(drag.x1 - drag.x0) * W, hh = Math.abs(drag.y1 - drag.y0) * H; g.strokeStyle = '#fff'; g.setLineDash([6, 4]); g.lineWidth = 2; g.strokeRect(x, y, w, hh); g.setLineDash([]); }
  }
  const pos = ev => { const r = cv.getBoundingClientRect(); return [CLAMP((ev.clientX - r.left) / r.width), CLAMP((ev.clientY - r.top) / r.height)]; };
  function hit(x, y) { for (let i = zones.length - 1; i >= 0; i--) { const z = zones[i]; for (const [ci, cx, cy] of [[0, z[0], z[1]], [1, z[2], z[1]], [2, z[0], z[3]], [3, z[2], z[3]]]) if (Math.abs(x - cx) < HANDLE && Math.abs(y - cy) < HANDLE * 1.6) return { i, mode: 'size', corner: ci }; if (x >= z[0] && x <= z[2] && y >= z[1] && y <= z[3]) return { i, mode: 'move' }; } return null; }
  cv.addEventListener('pointerdown', ev => { const [x, y] = pos(ev), hh = hit(x, y); if (hh) drag = Object.assign({ x0: x, y0: y, z0: zones[hh.i].slice() }, hh); else { if (zones.length >= 16) return; drag = { mode: 'new', x0: x, y0: y, x1: x, y1: y }; } cv.setPointerCapture(ev.pointerId); ev.preventDefault(); });
  cv.addEventListener('pointermove', ev => {
    const [x, y] = pos(ev); if (!drag) { const hh = hit(x, y); cv.style.cursor = !hh ? 'crosshair' : (hh.mode === 'move' ? 'move' : 'nwse-resize'); return; }
    if (drag.mode === 'new') { drag.x1 = x; drag.y1 = y; }
    else if (drag.mode === 'move') { const w = drag.z0[2] - drag.z0[0], hh = drag.z0[3] - drag.z0[1]; let nx = drag.z0[0] + x - drag.x0, ny = drag.z0[1] + y - drag.y0; nx = Math.max(0, Math.min(1 - w, nx)); ny = Math.max(0, Math.min(1 - hh, ny)); zones[drag.i] = [nx, ny, nx + w, ny + hh]; }
    else { const z = drag.z0.slice(); if (drag.corner % 2 === 0) z[0] = Math.min(x, z[2] - MIN); else z[2] = Math.max(x, z[0] + MIN); if (drag.corner < 2) z[1] = Math.min(y, z[3] - MIN); else z[3] = Math.max(y, z[1] + MIN); zones[drag.i] = z; }
    draw();
  });
  cv.addEventListener('pointerup', ev => {
    if (!drag) return; const [x, y] = pos(ev);
    if (drag.mode === 'new') { drag.x1 = x; drag.y1 = y; const z = round([Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)]); if (z[2] - z[0] >= MIN && z[3] - z[1] >= MIN) { zones.push(z); change(); } }
    else { zones[drag.i] = round(zones[drag.i]); change(); } drag = null; renderZoneList(); draw();
  });
  cv.addEventListener('pointercancel', () => { if (drag && drag.mode !== 'new') zones[drag.i] = drag.z0; drag = null; draw(); });
  img.addEventListener('error', () => { img.hidden = true; img.removeAttribute('src'); draw(); });
  function renderZoneList() {
    zoneList.replaceChildren(...(zones.length ? zones.map((z, i) => h('div', { class: 'split', style: 'font-size:var(--text-sm)' }, h('span', { text: `Zone ${i + 1}: ${pct(z[0])} bis ${pct(z[2])} % von links, ${pct(z[1])} bis ${pct(z[3])} % von oben` }), btn('Entfernen', { size: 'sm', variant: 'ghost', onclick: () => { zones.splice(i, 1); change(); renderZoneList(); draw(); } }))) : [h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: 'Keine Zonen. Zum Anlegen ein Rechteck im Bild aufziehen oder eine Vorlage nehmen.' })]));
  }
  const tpl = (label, v) => btn(label, { size: 'sm', onclick: () => { if (zones.length >= 16) return; zones.push(v); change(); renderZoneList(); draw(); } });
  const zoneCard = card({ title: 'Zonen ohne Verpixelung', sub: 'In diesen Bereichen wird nie verpixelt, zum Beispiel am eigenen Tacho oder an der Halterung des Handys auf dem Motorrad. Rechteck im Bild aufziehen (Maus oder Finger), zum Verschieben in das Rechteck ziehen, zum Ändern der Größe an einer Ecke ziehen. Läuft die Sendung, zeigt das Bild die Vorschau, sonst ein Raster. Kleine Bilder, etwa eine Facecam, werden ohnehin nicht verpixelt. Ist die Facecam aber Teil des Hauptbilds (fest eingeblendet oder selbst als Hauptbild gewählt), trage ihren Bereich hier ein, sonst wird sie mit verpixelt.', body: [
    zoneBox, h('div', { class: 'cluster' }, tpl('Vorlage: Unten Mitte (Tacho, Halterung)', [0.30, 0.55, 0.70, 1.00]), tpl('Vorlage: Bildmitte', [0.35, 0.30, 0.65, 0.70]), tpl('Vorlage: Linker Rand', [0, 0, 0.12, 1]), tpl('Vorlage: Rechter Rand', [0.88, 0, 1, 1])),
    zoneList, h('div', { class: 'cluster' }, btn('Alle Zonen entfernen', { size: 'sm', variant: 'ghost', onclick: () => { zones = []; change(); renderZoneList(); draw(); } }))] });

  /* ---------- Speichern (automatisch, mit Neustart der Sendung) ---------- */
  function change() { dirty = true; clearTimeout(saveT); info.set('…'); saveT = setTimeout(doSave, 1200); }
  async function doSave() {
    err.set(''); const my = ++flow; cancel.hidden = true; stopped = false;
    try { render(await api('POST', '/api/datenschutz', { an: form.an, kopf: form.kopf, kennz: form.kennz, zweit: form.zweit, delay_ms: +form.delay_ms, zonen: zones })); dirty = false; sig = ''; render(last); } catch (e) { err.set(e.message, 'bad'); return; }
    if (my !== flow) return;
    if (!(last && last.neustart_noetig)) { info.set(last && last.sending ? 'Gespeichert. Module und Zonen gelten sofort.' : 'Gespeichert.'); return; }
    if (form.an) { for (let i = 0; i < 40 && my === flow && !(last.status && last.status.up); i++) { info.set('Gespeichert. Warte auf den Erkennungsdienst …'); await sleep(1000); try { render(await api('GET', '/api/datenschutz')); } catch (e) { /* weiter */ } } if (my !== flow) return; if (!(last.status && last.status.up)) { info.set('Gespeichert, aber der Erkennungsdienst läuft nicht. Die Sendung wurde nicht neu gestartet.'); return; } }
    cancel.hidden = false; info.set('Gespeichert. Die Sendung startet in wenigen Sekunden neu, damit die Änderung gilt.'); for (let n = 3; n > 0 && !stopped && my === flow; n--) await sleep(1000);
    if (stopped || my !== flow) return; cancel.hidden = true;
    try { render(await api('POST', '/api/datenschutz', { restart_send: true })); info.set((last && last.note) || 'Die Sendung wurde neu gestartet.'); } catch (e) { err.set(e.message, 'bad'); }
  }

  /* ---------- Anzeige ---------- */
  function render(d) {
    last = d; App.set('dshead', d); const c = d.config || {}, a = d.addon || {}, installed = !!d.installiert, working = a.state === 'installing';
    if (typeof d.lizenz_text === 'string') { licText = d.lizenz_text; licLoaded = true; }
    if (!dirty) { form = { an: !!c.an, kopf: c.kopf !== false, kennz: c.kennz !== false, zweit: c.zweit !== false, delay_ms: c.delay_ms || 2000 }; zones = (c.zonen || []).map(z => z.slice()); renderZoneList(); }
    const sg = JSON.stringify([d, dirty ? form : 0]); if (sg === sig) { bild(d); return; } if (box.contains(document.activeElement) && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName) && document.activeElement.type !== 'checkbox') { bild(d); return; } sig = sg;
    let t = '', tone = '';
    if (working) t = a.action === 'remove' ? 'Die Erkennung wird entfernt.' : 'Die Erkennung wird eingerichtet. Das kann einige Minuten dauern.';
    else if (a.state === 'failed' || a.state === 'refused') { t = (a.state === 'failed' ? 'Fehlgeschlagen: ' : '') + a.message; tone = 'warn'; }
    else if (!installed) t = !a.helper ? 'Der Add-on-Helfer fehlt. Ein Software-Update richtet ihn ein.' : (!c.lizenz_ok ? 'Nicht eingerichtet. Zum Einrichten unten die Lizenz der Erkennung lesen und annehmen.' : (d.sending ? 'Nicht eingerichtet. Zum Einrichten die Sendung beenden.' : 'Nicht eingerichtet. „Erkennung einrichten“ lädt die Erkennung auf die Box.'));
    else if (!c.lizenz_ok) t = 'Eingerichtet, aber die Lizenz ist noch nicht angenommen (oder der Text hat sich geändert). Bitte unten annehmen.';
    else if (!c.an) t = 'Eingerichtet. Verpixeln ist aus.';
    else if (d.sending && d.neustart_noetig) { t = 'ACHTUNG: Verpixeln ist eingeschaltet, die laufende Sendung ist aber noch nicht verpixelt. Die Sendung muss neu gestartet werden.'; tone = 'warn'; }
    else if (d.status && d.status.up) { const s = d.status; t = 'Läuft. Erkennung alle ' + (+s.pass_s || 0).toFixed(1) + ' s' + (s.slow > 0.3 ? ' (gedrosselt wegen Wärme)' : '') + (s.warn ? ' · ' + s.warn : '') + (d.sending ? '' : ' · Die Sendung läuft nicht; verpixelt wird ab dem nächsten Start.'); tone = s.warn ? 'warn' : ''; }
    else { t = 'Eingeschaltet. Der Dienst startet gerade (das dauert etwa eine halbe Minute). Solange er nicht läuft, bleibt das Hauptbild einer laufenden Sendung schwarz, und eine neue Sendung bitte erst starten, wenn hier „Läuft“ steht.'; tone = 'warn'; }
    const stateBadge = working ? badge('läuft …', 'accent') : installed && c.an && c.lizenz_ok ? badge(d.status && d.status.up ? 'an' : 'an · wartet', d.status && d.status.up ? 'ok' : 'warn') : null;
    const kids = [];
    kids.push(card({ title: 'Datenschutz (DSGVO): Köpfe und Kennzeichen verpixeln', actions: stateBadge ? [stateBadge] : [], body: [
      h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, 'Die Box erkennt mit KI-Unterstützung (künstliche Intelligenz, die Erkennung basiert auf neuronalen Netzen) im Bild der Hauptkamera Köpfe und Kennzeichen und verpixelt sie, bevor das Bild gesendet wird. Die Erkennung läuft nur auf der Box, es werden keine Bilder übertragen. Kleine Bilder (zum Beispiel die eigene Kamera) bleiben unverändert. Die Sendung wird um die eingestellte Verzögerung später ausgegeben. Die Erkennung ist ein Add-on: Wer sie nutzen will, richtet sie hier ein. Sie ist nicht zu 100 % zuverlässig: Sie findet nicht jeden Kopf und nicht jedes Kennzeichen.'),
      h('p', { role: 'status', style: tone === 'warn' ? 'color:var(--warn)' : 'color:var(--text-2);font-size:var(--text-sm)', text: t }),
      working ? prog(a.progress || 0, (a.action === 'remove' ? 'Entferne … ' : 'Richte ein … ') + (a.step || '')) : null,
      d.sending && d.neustart_noetig && installed && c.an && c.lizenz_ok ? btn('Sendung jetzt neu starten', { variant: 'primary', run: async () => { render(await api('POST', '/api/datenschutz', { restart_send: true })); info.set((last && last.note) || 'Die Sendung wurde neu gestartet.'); } }) : null] }));
    /* Lizenz */
    const licSw = sw({ checked: !!c.lizenz_ok, label: 'Ich habe die Lizenz der Erkennung gelesen und nehme sie an', s: 'Lizenz der Erkennung annehmen', k: 'datenschutz lizenz erkennung verpixeln', onchange: async (v, el) => {
      if (v && !await ask({ title: 'Lizenz der Erkennung annehmen?', text: 'Du bestätigst, dass du den Text gelesen hast und die Erkennung als Privatperson oder Streamer nach dieser Lizenz nutzt.', ok: 'Annehmen' })) { el.checked = false; return; }
      try { render(await api('POST', '/api/datenschutz', { lizenz_ok: v, lizenz_sha: d.lizenz_sha })); info.set(v ? 'Lizenz angenommen.' : 'Annahme zurückgenommen, Verpixeln ist aus.'); } catch (x) { err.set(x.message, 'bad'); el.checked = !v; } } });
    const licPre = h('pre', { style: 'white-space:pre-wrap;font-size:var(--text-xs);max-height:16rem;overflow:auto' }, licText || (licLoaded ? '' : 'Wird geladen …'));
    const lic = disclosure('Lizenz der Erkennung', licPre, licSw, h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: c.lizenz_ok ? 'Lizenz angenommen.' : 'Erst nach der Annahme lässt sich die Erkennung einrichten und das Verpixeln einschalten.' })); if (!c.lizenz_ok) lic.open = true;
    if (!installed && !working) kids.push(card({ title: 'Erkennung einrichten', body: [lic, btn('Erkennung einrichten', { variant: 'primary', disabled: !c.lizenz_ok || !!d.sending || !a.helper || !a.dateien, run: async () => { render(await api('POST', '/api/datenschutz', { addon: 'install' })); info.set('Wird installiert …'); schedule(); } })] }));
    else if (installed && !working) {
      const ctl = [lic,
        sw({ checked: form.an, label: 'Verpixeln einschalten', disabled: !c.lizenz_ok, s: 'Verpixeln einschalten', k: 'datenschutz dsgvo pixel blur', onchange: v => { form.an = v; change(); } }),
        h('p', { style: 'font-weight:var(--weight-medium)', text: 'Was wird verpixelt?' }),
        sw({ checked: form.kopf, label: 'Köpfe', onchange: v => { form.kopf = v; change(); } }), sw({ checked: form.kennz, label: 'Kennzeichen', onchange: v => { form.kennz = v; change(); } }), sw({ checked: form.zweit, label: 'Zweite Erkennung (findet mehr, braucht mehr Rechenleistung)', onchange: v => { form.zweit = v; change(); } }),
        range({ label: 'Verzögerung der Sendung', min: 2000, max: 12000, step: 100, value: Math.max(2000, Math.min(12000, +form.delay_ms || 2000)), unit: ' ms', oninput: v => { form.delay_ms = v; }, onchange: () => change(), s: 'Datenschutz Verzögerung', k: 'verpixeln verzögerung delay' }),
        h('p', { class: 'field__hint', text: 'Änderungen werden automatisch gespeichert.' }), cancel,
        h('div', { class: 'cluster' }, btn('Erkennung entfernen', { variant: 'ghost', disabled: working || !!d.sending, onclick: async () => { if (await ask({ title: 'Die Erkennung entfernen?', text: 'Programme, Modelle und Python-Umgebung werden gelöscht, Verpixeln ist danach aus. Die Einstellungen und Zonen bleiben gespeichert. Neu einrichten geht jederzeit.', ok: 'Entfernen', danger: true })) { try { render(await api('POST', '/api/datenschutz', { addon: 'remove' })); info.set('Das Entfernen wurde angefordert.'); schedule(); } catch (e) { err.set(e.message, 'bad'); } } } }))];
      kids.push(card({ title: 'Einstellungen', body: ctl }), zoneCard);
    }
    kids.push(info, err); box.replaceChildren(...kids); bild(d);
  }
  function bild(d) { const want = d && d.sending && App.view === 'bild' && T.panels.querySelector('#b-ds') && !T.panels.querySelector('#b-ds').hidden; if (want && img.hidden && zoneBox.isConnected) { img.hidden = false; img.src = '/api/preview/stream?fmt=mjpeg&fps=5&w=640&t=' + Date.now(); } if (!want && !img.hidden) { img.hidden = true; img.removeAttribute('src'); } draw(); }
  const wait = h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: 'Wird geladen …' });
  const retry = btn('Erneut versuchen', { onclick: () => load(true) }); retry.hidden = true;
  box.replaceChildren(wait, err, retry);
  async function load(withText) {
    try { retry.hidden = true; err.set(''); render(await api('GET', '/api/datenschutz' + (withText || !licLoaded ? '?lizenz=1' : ''))); }
    catch (e) { if (wait.isConnected) { wait.textContent = 'Die Datenschutz-Einstellungen konnten nicht geladen werden.'; retry.hidden = false; } else if (!box.contains(err)) box.append(err); err.set(e.message, 'bad'); }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(async () => { if (!document.hidden && !dirty && App.view === 'bild') await load(false); schedule(); }, last && last.addon && last.addon.state === 'installing' ? 3000 : 10000); }
  document.addEventListener('neu:view', e => { if (e.detail === 'bild') { load(true).then(schedule); } else bild(null); });
  document.addEventListener('neu:tab', e => { setTimeout(() => bild(last), 50); if (e.detail === 'b-ds' && !last) load(true); });
  new MutationObserver(() => { cv.width = 640; draw(); }).observe(box, { childList: true });
  renderZoneList(); draw();
})();
