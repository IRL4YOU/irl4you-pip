/* Ansicht „Bild“: Bildaufbau mit Vorschau zum Anordnen, Aussehen der kleinen Bilder, Verzögerung, Umschalten und Tausch, Facecam. */
(() => {
  'use strict';
  const { h, api, App, poll, ask, toast, run, badge, btn, alertBox, card, sw, field, input, select, seg, range, disclosure, pageHead, view, note, sleep } = Neu;
  const FREE = '5', CW = 1920, CH = 1080, KEEP = 32;
  const KEYS = ['type', 'main', 'pip', 'corner', 'pip2', 'corner2', 'pip3', 'corner3', 'x', 'y', 'x2', 'y2', 'x3', 'y3', 'size_pct', 'size_pct2', 'size_pct3', 'audio', 'auto_failover', 'swap_cams', 'always_ready', 'styles', 'main_delay_ms', 'pip_delay_ms', 'pip2_delay_ms', 'pip3_delay_ms'];
  const STYLE = () => ({ visible: true, opacity: 100, crop: { l: 0, r: 0, t: 0, b: 0 }, radius: 0, border: { enabled: false, width: 6, color: '#ffffff', opacity: 100 } });
  const clampN = (v, lo, hi, d) => { v = Math.round(+v); return isNaN(v) ? d : Math.max(lo, Math.min(hi, v)); };
  const sfx = i => i === 1 ? '' : String(i), slotKey = i => i === 1 ? 'pip' : 'pip' + i;
  const DELAY = ['main_delay_ms', 'pip_delay_ms', 'pip2_delay_ms', 'pip3_delay_ms'];

  view('bild', sec => {
    const T = Neu.slots.bild = Neu.tabbed(sec, 'Bild');
    let d = null, cfg = null, cams = [], corners = [], live = {}, dirty = false, free = { 1: { x: 500, y: 500 }, 2: { x: 500, y: 500 }, 3: { x: 500, y: 500 } }, styles = { 1: STYLE(), 2: STYLE(), 3: STYLE() }, drag = null;
    const msg = note(), stateEl = h('span'), saveBtn = btn('Bildaufbau speichern', { variant: 'primary', run: save });
    sec.prepend(pageHead('Bild', 'So sieht dein Stream aus. Ziehe die kleinen Bilder in der Vorschau oder stelle sie rechts genau ein.', [stateEl, saveBtn]));
    const canvas = h('div', { class: 'canvas', role: 'img', 'aria-label': 'Vorschau des Bildaufbaus', style: 'touch-action:none' }), controls = h('div', { class: 'stack' }), preNote = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }), techPre = h('pre', { style: 'white-space:pre-wrap;font-size:var(--text-xs);max-height:14rem;overflow:auto' }), infoEl = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' });
    const nm = k => (cams.find(c => c.key === k) || {}).name || k;
    const mark = () => { dirty = true; stateEl.replaceChildren(badge('Nicht gespeichert', 'warn')); msg.set('Nicht gespeichert: Mit „Bildaufbau speichern“ übernehmen.'); };

    /* ---------- Vorschau zeichnen ---------- */
    function paint() {
      if (!cfg) return; const W = 100, H = 56.25, m = 3, kids = [h('div', { class: 'layer layer--main', text: cfg.main ? `Hauptbild · ${nm(cfg.main)}` : 'Hauptkamera wählen' })], canFree = corners.length > 5;
      if (cfg.type === 'pip') [1, 2, 3].forEach(i => {
        const cam = cfg[slotKey(i)]; if (!cam) return; const size = Math.max(1, Math.min(100, +cfg['size_pct' + sfx(i)] || 25));
        let pw = W * size / 100; const ph = pw * 9 / 16, info = cams.find(y => y.key === cam) || {}, portrait = info.w > 0 && info.h > info.w; if (portrait) pw = ph * info.w / info.h;
        const st = styles[i], cr = portrait ? { l: 0, r: 0, t: 0, b: 0 } : st.crop, cw = pw * (CW - cr.l - cr.r) / CW, chh = ph * (CH - cr.t - cr.b) / CH;
        const c = +cfg['corner' + sfx(i)], f = free[i]; let x, y;
        if (c === 5) { x = f.x / 1000 * (W - cw); y = f.y / 1000 * (H - chh); } else if (c === 4) { x = (W - cw) / 2; y = H - chh - m; } else { x = (c & 1) ? W - cw - m : m; y = (c & 2) ? H - chh - m : m; }
        x = Math.max(0, Math.min(W - cw, x)); y = Math.max(0, Math.min(H - chh, y));
        const sc = 1 / 19.2, bw = st.border.enabled ? Math.max(0.15, st.border.width * sc) : 0, rad = st.radius ? Math.min(st.radius * sc, Math.min(cw, chh) / 2) : 0;
        const el = h('div', { class: `layer layer--p${i}`, data: { k: i }, style: `left:${x}%;top:${y / H * 100}%;width:${cw}%;height:${chh / H * 100}%;opacity:${st.visible ? 1 : 0.2};border-radius:${rad}cqw;cursor:${canFree ? 'grab' : 'default'}` }, `${i} · ${nm(cam)}${st.visible ? '' : ' (aus)'}`);
        if (bw) el.style.boxShadow = `inset 0 0 0 max(1px, ${bw}cqw) ${hexRgba(st.border.color, st.border.opacity / 100)}`;
        kids.push(el);
      });
      canvas.replaceChildren(...kids);
      preNote.textContent = canFree ? 'Kleine Bilder mit Maus oder Finger ziehen. Danach „Bildaufbau speichern“.' : 'Freies Verschieben braucht den neuen Überlagerungs-Baustein (kommt beim Software-Update).';
    }
    const hexRgba = (hx, a) => { const n = parseInt(String(hx).slice(1), 16) || 0; return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };
    canvas.addEventListener('pointerdown', e => {
      const el = e.target.closest('.layer--p1,.layer--p2,.layer--p3'); if (!el || corners.length <= 5) return; e.preventDefault(); canvas.setPointerCapture(e.pointerId);
      const r = el.getBoundingClientRect(); drag = { el, k: +el.dataset.k, dx: e.clientX - r.left, dy: e.clientY - r.top };
    });
    canvas.addEventListener('pointermove', e => {
      if (!drag) return; const b = canvas.getBoundingClientRect(), w = drag.el.offsetWidth, hh = drag.el.offsetHeight;
      const x = Math.max(0, Math.min(b.width - w, e.clientX - b.left - drag.dx)), y = Math.max(0, Math.min(b.height - hh, e.clientY - b.top - drag.dy));
      drag.el.style.left = (x / b.width * 100) + '%'; drag.el.style.top = (y / b.height * 100) + '%';
      free[drag.k] = { x: b.width > w ? Math.round(x / (b.width - w) * 1000) : 0, y: b.height > hh ? Math.round(y / (b.height - hh) * 1000) : 0 };
    });
    const endDrag = () => { if (!drag) return; cfg['corner' + sfx(drag.k)] = 5; cfg['x' + sfx(drag.k)] = free[drag.k].x; cfg['y' + sfx(drag.k)] = free[drag.k].y; drag = null; mark(); buildControls(); };
    canvas.addEventListener('pointerup', endDrag); canvas.addEventListener('pointercancel', endDrag);

    /* ---------- Einschränkungen (jede Kamera nur einmal, Ecken nicht doppelt) ---------- */
    function constrain() {
      const has = k => cams.some(x => x.key === k), free0 = ex => cams.filter(x => !ex.includes(x.key));
      const main = has(cfg.main) ? cfg.main : ((cams[0] || {}).key || ''); cfg.main = main;
      const pip = (has(cfg.pip) && cfg.pip !== main) ? cfg.pip : ((free0([main])[0] || {}).key || ''); cfg.pip = pip;
      cfg.pip2 = (has(cfg.pip2) && cfg.pip2 !== main && cfg.pip2 !== pip) ? cfg.pip2 : '';
      cfg.pip3 = (cfg.pip2 && has(cfg.pip3) && cfg.pip3 !== main && cfg.pip3 !== pip && cfg.pip3 !== cfg.pip2) ? cfg.pip3 : '';
      const ids = corners.map((n, i) => String(i)), taken = (i, used) => i !== FREE && used.includes(i);
      let c1 = ids.includes(String(cfg.corner)) ? String(cfg.corner) : '3'; let c2 = String(cfg.corner2); if (taken(c2, [c1]) || !ids.includes(c2)) c2 = ids.find(i => !taken(i, [c1])) || '';
      let c3 = String(cfg.corner3); if (taken(c3, [c1, c2]) || !ids.includes(c3)) c3 = ids.find(i => !taken(i, [c1, c2])) || '';
      cfg.corner = +c1; cfg.corner2 = c2 === '' ? 2 : +c2; cfg.corner3 = c3 === '' ? 0 : +c3;
      const ok = [['main', main], ['pip', pip], ['pip2', cfg.pip2], ['pip3', cfg.pip3]].filter(x => x[1]).map(x => x[0]); if (!ok.includes(cfg.audio)) cfg.audio = 'main';
    }
    const camOpt = (k, selected) => [k.key, `${k.name}${cams.filter(x => x.name === k.name).length > 1 ? ' (' + k.key + ')' : ''}${k.state === 'live' ? ' · sendet' : ''}`];

    /* ---------- Bedienfelder ---------- */
    function styleBlock(i) {
      const st = styles[i], num = (label, val, min, max, step, set) => field({ label, control: input({ type: 'number', min, max, step: step || 1, value: val, onchange: e => { set(e.target.value); mark(); buildControls(); } }) });
      const crop = key => { const opp = { l: 'r', r: 'l', t: 'b', b: 't' }[key], total = (key === 'l' || key === 'r') ? CW : CH; return v => { st.crop[key] = Math.floor(clampN(v, 0, total - KEEP, 0) / 2) * 2; st.crop[key] = Math.max(0, Math.min(st.crop[key], total - KEEP - st.crop[opp])); }; };
      const c = st.crop, cropped = c.l || c.r || c.t || c.b;
      return disclosure(`Aussehen von Bild ${i}`, sw({ checked: st.visible, label: 'Bild einblenden', onchange: v => { st.visible = v; mark(); paint(); } }),
        h('h4', { text: 'Ecken' }), num('Eckenrundung (Pixel)', st.radius, 0, 60, 1, v => { st.radius = clampN(v, 0, 60, 0); }),
        h('h4', { text: 'Beschneiden' }), h('p', { class: 'field__hint', text: 'Pixel, die links, rechts, oben und unten abgeschnitten werden, wie bei einem Bild von 1920 × 1080.' }),
        h('div', { class: 'grid-auto', style: '--min:7rem' }, num('Links', c.l, 0, 1888, 2, crop('l')), num('Rechts', c.r, 0, 1888, 2, crop('r')), num('Oben', c.t, 0, 1048, 2, crop('t')), num('Unten', c.b, 0, 1048, 2, crop('b'))),
        h('p', { class: 'field__hint', text: cropped ? `Bild nach dem Beschneiden: ${CW - c.l - c.r} × ${CH - c.t - c.b} Pixel. Der Maßstab bleibt, es wird nur abgeschnitten.` : 'Nicht beschnitten.' }),
        h('h4', { text: 'Rahmen' }), sw({ checked: st.border.enabled, label: 'Rahmen um dieses Bild', onchange: v => { st.border.enabled = v; mark(); paint(); buildControls(); } }),
        h('div', { class: 'grid-auto', style: '--min:8rem;' + (st.border.enabled ? '' : 'opacity:.5') }, num('Dicke (Pixel)', st.border.width, 1, 40, 1, v => { st.border.width = clampN(v, 1, 40, 6); }),
          field({ label: 'Farbe', control: input({ type: 'color', value: st.border.color, disabled: !st.border.enabled, onchange: e => { st.border.color = /^#[0-9a-fA-F]{6}$/.test(e.target.value) ? e.target.value.toLowerCase() : '#ffffff'; mark(); paint(); } }) }), num('Deckkraft (%)', st.border.opacity, 10, 100, 1, v => { st.border.opacity = clampN(v, 10, 100, 100); })));
    }
    async function liveDelay() { if (!dirty && ((live.main) || (live.pips))) await save(); else { mark(); msg.set(live.main ? 'Nicht gespeichert: Mit „Bildaufbau speichern“ übernehmen (Sendekette startet kurz neu).' : 'Nicht gespeichert: Mit „Bildaufbau speichern“ übernehmen.'); } }
    const delayCtl = (key, label) => { const r = range({ label, min: 0, max: 3000, step: 10, value: Math.max(0, Math.min(3000, +cfg[key] || 0)), unit: ' ms', oninput: v => { cfg[key] = v; }, onchange: liveDelay, s: label, k: 'verzögerung delay ms ton bild' }); return r; };

    function buildControls() {
      if (!cfg) return; const pip = cfg.type === 'pip', kids = [];
      const slotCard = i => { const k = slotKey(i), s = sfx(i), usedAbove = i === 1 || cfg[slotKey(i - 1)]; const camSel = select([...(i > 1 ? [['', 'keins']] : []), ...cams.filter(c => ![cfg.main, ...[1, 2, 3].filter(j => j !== i).map(j => cfg[slotKey(j)])].filter(Boolean).includes(c.key) || c.key === cfg[k]).map(c => camOpt(c))], cfg[k] || '', v => { cfg[k] = v; if (i === 1 && !v) cfg.pip2 = ''; constrain(); mark(); paint(); buildControls(); });
        camSel.disabled = !usedAbove; const used = [1, 2, 3].filter(j => j !== i).map(j => String(cfg['corner' + sfx(j)]));
        const cornSel = select(corners.map((n, j) => [j, n]).filter(([j]) => String(j) === FREE || !used.includes(String(j)) || String(j) === String(cfg['corner' + s])), cfg['corner' + s], v => { cfg['corner' + s] = +v; constrain(); mark(); paint(); buildControls(); }); cornSel.disabled = !cfg[k];
        return [field({ s: `Kamera für kleines Bild ${i}`, k: 'bild in bild pip kamera klein', label: `Kamera für kleines Bild ${i}`, control: camSel }), field({ s: `Ecke von Bild ${i}`, k: 'pip ecke position frei', label: 'Ecke', control: cornSel }), range({ s: `Größe von Bild ${i}`, k: 'pip größe prozent', label: 'Größe', min: 10, max: 50, value: cfg['size_pct' + s] || 25, unit: ' %', oninput: v => { cfg['size_pct' + s] = v; paint(); }, onchange: () => mark() }), delayCtl(DELAY[i], `Verzögerung von Bild ${i}`), ...(d.plugin_style === false && d.plugin_present ? [] : [styleBlock(i)])]; };
      const au = [['main', cfg.main ? `Hauptbild (${nm(cfg.main)})` : 'Hauptbild'], ...[1, 2, 3].filter(i => cfg[slotKey(i)] && pip).map(i => [slotKey(i), `Kleines Bild ${i} (${nm(cfg[slotKey(i)])})`])];
      kids.push(card({ title: 'Aufbau', body: [
        field({ s: 'Art des Bildaufbaus', k: 'bild in bild pip einzeln eine kamera', label: 'Art', control: h('div', null, seg('ptype', [['single', 'Eine Kamera'], ['pip', 'Bild in Bild']], cfg.type, v => { cfg.type = v; constrain(); mark(); paint(); buildControls(); }, 'Art des Bildaufbaus')) }),
        field({ s: 'Hauptkamera', k: 'bild haupt quelle kamera', label: 'Hauptkamera', hint: 'Das große Bild im Hintergrund.', control: select(cams.map(c => camOpt(c)), cfg.main, v => { const prev = cfg.main; cfg.main = v; ['pip', 'pip2', 'pip3'].forEach(k => { if (cfg[k] === v && prev && prev !== v) cfg[k] = prev; }); constrain(); mark(); paint(); buildControls(); }) }),
        field({ s: 'Ton von', k: 'audio mikrofon ton quelle', label: 'Ton von', control: select(au, cfg.audio, v => { cfg.audio = v; mark(); }) }),
        delayCtl(DELAY[0], 'Verzögerung des Hauptbilds'), h('p', { class: 'field__hint', text: 'Das Hauptbild wartet samt Ton vor dem Einblenden. 0 = aus.' }),
        pip && cfg.always_ready && Math.max(+cfg.main_delay_ms || 0, ...[1, 2, 3].filter(i => cfg[slotKey(i)]).map(i => +cfg[DELAY[i]] || 0)) > 750 ? alertBox({ tone: 'warn', text: 'Mit „Alle Kameras immer bereit“ sind Verzögerungen über 750 ms nicht möglich. Die Verzögerung wird dann begrenzt.' }) : null] }));
      if (pip) { kids.push(card({ title: 'Kleines Bild 1', body: slotCard(1) })); if (cfg.pip) kids.push(card({ title: 'Kleines Bild 2', body: slotCard(2) })); if (cfg.pip2) kids.push(card({ title: 'Kleines Bild 3', body: slotCard(3) })); }
      controls.replaceChildren(...kids); paint();
      infoEl.textContent = !cfg.main ? 'Noch keine Pipeline gespeichert.' : cfg.type === 'pip' ? `Gespeichert: Bild-in-Bild, Hauptbild ${nm(cfg.main)}, kleines Bild ${[cfg.pip, cfg.pip2, cfg.pip3].filter(Boolean).map(nm).join(', ')}.` : `Gespeichert: eine Kamera, Hauptbild ${nm(cfg.main)}.`;
      if (d.needs_plugin && !d.plugin_present) infoEl.textContent += ' Bild-in-Bild braucht den Überlagerungs-Baustein, der noch nicht auf der Box installiert ist.';
    }

    /* ---------- Laden und Speichern ---------- */
    let lastSig = '';
    async function load(force) {
      let nd; try { nd = await api('GET', '/api/pipeline'); } catch (e) { return; }
      const sg = JSON.stringify([nd.config, nd.cameras, nd.corners, nd.live, nd.preview]);
      if (!force && sg === lastSig) return;                                  // nichts geändert: nichts neu bauen
      if (dirty && !force) { lastSig = sg; d = nd; cams = d.cameras || []; live = d.live || {}; techPre.textContent = d.preview || ''; renderSwitch(); renderFace(); return; }
      lastSig = sg; d = nd;
      cfg = JSON.parse(JSON.stringify(d.config)); cams = d.cameras || []; corners = d.corners || []; live = d.live || {};
      free = { 1: { x: cfg.x ?? 500, y: cfg.y ?? 500 }, 2: { x: cfg.x2 ?? 500, y: cfg.y2 ?? 500 }, 3: { x: cfg.x3 ?? 500, y: cfg.y3 ?? 500 } };
      [1, 2, 3].forEach(k => { const s0 = (cfg.styles || {})[k] || {}; styles[k] = Object.assign(STYLE(), s0); styles[k].crop = Object.assign({ l: 0, r: 0, t: 0, b: 0 }, styles[k].crop); styles[k].border = Object.assign(STYLE().border, styles[k].border); styles[k].opacity = 100; delete styles[k].border.radius; });
      if (!cfg.pip && cams.length > 1 && cfg.type === 'single') { /* Vorschlag für Bild in Bild bleibt aus */ }
      dirty = false; stateEl.replaceChildren(); msg.set(''); const ct = cfg.type; if (ct === 'pip' || cfg.main) constrainSoft(); buildControls(); techPre.textContent = d.preview || '(noch keine Hauptkamera gewählt)'; renderSwitch(); renderFace();
    }
    function constrainSoft() { const keep = JSON.stringify(cfg); constrain(); if (JSON.stringify(cfg) !== keep && cfg.main) { /* gespeicherter Stand wurde angeglichen */ } }
    async function save() {
      const body = {}; KEYS.forEach(k => { if (cfg[k] !== undefined) body[k] = cfg[k]; });
      body.x = free[1].x; body.y = free[1].y; body.x2 = free[2].x; body.y2 = free[2].y; body.x3 = free[3].x; body.y3 = free[3].y; body.styles = styles;
      if (!body.main) { toast('Bitte zuerst eine Hauptkamera wählen'); return; }
      if (App.data.send && App.data.send.active && !await ask({ title: 'Bildaufbau speichern?', text: 'Die Sendung läuft. Bei manchen Änderungen startet die Sendekette kurz neu, das Bild ist dann einige Sekunden weg.', ok: 'Speichern' })) return;
      const r = await api('POST', '/api/pipeline', body); dirty = false; msg.set((r && r.note) || 'Gespeichert.', 'ok'); toast((r && r.note) || 'Gespeichert');
      if (r && r.restarted) watchRestart(r.note || 'Gespeichert.');
      await load(true); App.data['send:refresh'] && App.data['send:refresh']();
    }
    let watchTok = 0;
    async function watchRestart(base) {
      const tok = ++watchTok, t0 = Date.now(); let seenOther = false;
      for (let i = 0; i < 120 && tok === watchTok; i++) {
        await sleep(1000); let s; try { s = await api('GET', '/api/send'); } catch (e) { continue; } const sec2 = Math.round((Date.now() - t0) / 1000);
        if (s.state === 'running') { if (seenOther || sec2 >= 4) { msg.set(`Gespeichert. Die Übertragung läuft wieder (nach ${sec2} Sekunden).`, 'ok'); return; } }
        else if (s.state === 'refused' || s.state === 'stopped') { msg.set('Gespeichert, aber die Übertragung läuft nicht: ' + (s.message || s.state), 'bad'); return; } else { seenOther = true; msg.set(`${base} ${s.state} … (${sec2} s)`); }
      }
    }

    /* ---------- Reiter ---------- */
    T.add('b-aufbau', 'Aufbau', h('div', { class: 'editor' }, card({ body: [canvas, preNote, msg, infoEl, disclosure('Technische Beschreibung ansehen (für die Fehlersuche)', techPre)] }), controls));

    const swBox = h('div', { class: 'stack' });
    function renderSwitch() {
      if (!cfg || !d) return; const inPic = [cfg.main, cfg.pip, cfg.pip2, cfg.pip3].filter(Boolean), order = cams.map(x => x.key).filter(k => inPic.includes(k)), byKey = Object.fromEntries(cams.map(x => [x.key, x]));
      const seamless = (a, b) => { const g = (App.data.send && App.data.send.swap_group) || []; return !!(App.data.send && App.data.send.active && g.includes(a) && g.includes(b)); };
      const tausch = cfg.type === 'pip' && cfg.main && inPic.length > 1 ? card({ title: 'Hauptbild tauschen', sub: 'Macht eine Kamera zum Hauptbild und tauscht sie mit dem bisherigen.', body: [h('div', { class: 'cluster' }, order.map(k => { const cam = byKey[k] || {}, on = k === cfg.main, gone = !on && cam.state === 'offline';
        return btn(cam.name || k, { variant: on ? 'primary' : null, disabled: on || gone, onclick: async () => { try { const r = await api('POST', '/api/pipeline/swap', { with: k }); toast((r && r.note) || 'Getauscht.'); dirty = false; await load(true); App.data['send:refresh'](); } catch (x) { toast(x.message); } } }); })),
        h('p', { class: 'field__hint', text: seamless(cfg.main, order.find(k => k !== cfg.main)) ? 'Der Tausch läuft ohne Unterbrechung der Sendung.' : 'Läuft die Sendung, startet der Encoder dafür neu (etwa 5 Sekunden ohne Bild), außer der unterbrechungsfreie Tausch unten ist eingeschaltet.' })] }) : null;
      swBox.replaceChildren(...[tausch, card({ title: 'Umschalten und Tausch', body: [
        sw({ s: 'Automatisch umschalten bei Ausfall', k: 'failover ausfall ersatz kamera umschalten', checked: cfg.auto_failover !== false, label: 'Automatisch umschalten, wenn eine Kamera ausfällt', hint: 'Nach 5 s ohne Signal; zurück nach 60 s stabilem Signal.', onchange: v => { cfg.auto_failover = v; mark(); } }),
        cfg.type === 'pip' ? field({ s: 'Hauptbild tauschen ohne Unterbrechung', k: 'tausch swap unterbrechung experimentell', label: 'Hauptbild tauschen ohne Unterbrechung (experimentell)', control: select([[0, 'aus (Encoder startet neu, etwa 5 s ohne Bild)'], [2, 'Hauptbild und erstes kleines Bild'], [4, 'alle Kameras (mehr Rechenlast)']], String(cfg.swap_cams || 0), v => { cfg.swap_cams = +v; mark(); }), hint: 'Der Empfänger bekommt dann weiter Daten. Dafür dekodiert die Box jede Kamera der Gruppe zweimal (groß und klein).' }) : null,
        sw({ s: 'Alle Kameras immer bereit', k: 'immer bereit always kameras', checked: !!cfg.always_ready, label: 'Alle Kameras immer bereit', hint: 'Kameras können jederzeit dazukommen, ausfallen und zurückkehren, ohne dass die Sendung neu startet. Fällt die Hauptkamera aus, übernimmt die nächste. Ohne Kamera sendet die Box Schwarz mit Stille. Dafür braucht die Box mehr Rechenlast, mit eingeschaltetem Datenschutz noch mehr: bitte für ausreichend Kühlung sorgen. Das Bild wird gleichmäßiger, weil jede Kamera einen Puffer bekommt.', onchange: v => { cfg.always_ready = v; mark(); buildControls(); } })] })].filter(Boolean));
    }
    T.add('b-umschalten', 'Umschalten', swBox);

    const faceBox = h('div', { class: 'stack' }), faceMsg = note();
    function renderFace() {
      if (!cfg) return; const c = d.config, byKey = Object.fromEntries((d.cameras || []).map(x => [x.key, x])), nm2 = k => ((byKey[k] || {}).name || k), fc = c.facecam || '';
      const names = { main: 'Hauptbild', pip: 'Kleines Bild 1', pip2: 'Kleines Bild 2', pip3: 'Kleines Bild 3' }, at = Object.keys(names).find(s0 => fc && c[s0] === fc);
      const b = (slot, title, sub, on, off) => h('button', { type: 'button', class: 'btn', 'aria-pressed': String(on), disabled: off, data: { variant: on ? 'primary' : '' }, style: 'height:auto;padding:var(--space-3);display:grid;gap:2px;text-align:left;justify-items:start', onclick: async () => { faceMsg.set('Wird gesetzt …'); try { const r = await api('POST', '/api/pipeline/facecam', { slot }); faceMsg.set(r.note || 'Gespeichert. Der Ton wechselt sofort.', 'ok'); } catch (x) { faceMsg.set(x.message, 'bad'); } d = await api('GET', '/api/pipeline'); renderFace(); App.data['send:refresh'](); } }, h('b', { text: title }), h('span', { style: 'font-weight:var(--weight-regular);font-size:var(--text-xs);opacity:.85', text: sub }));
      faceBox.replaceChildren(card({ title: 'Facecam', sub: 'Die Facecam ist die Kamera, deren Ton immer mitgeht, egal ob ihr Bild groß oder klein ist und wohin sie getauscht wird. Wähle das Bild, in dem sie gerade zu sehen ist. Ohne Facecam folgt der Ton dem Hauptbild. Die Wahl gilt sofort, ohne „Bildaufbau speichern“.', body: [
        h('p', { style: 'font-weight:var(--weight-medium)', role: 'status', text: fc ? (at ? `Facecam: ${nm2(fc)}, gerade im ${names[at]}. Der Ton bleibt bei dieser Kamera.` : `Facecam: ${nm2(fc)}. Sie ist gerade nicht im Bild, der Ton folgt dem Hauptbild.`) : 'Keine Facecam: Der Ton folgt dem Hauptbild.' }),
        h('div', { class: 'grid-auto', style: '--min:11rem' }, Object.keys(names).map(s0 => b(s0, names[s0], c[s0] ? nm2(c[s0]) : 'nicht belegt', !!fc && c[s0] === fc, !c[s0])), b('', 'Keine Facecam', 'Der Ton folgt dem Hauptbild', !fc, false)), faceMsg] }));
    }
    T.add('b-face', 'Facecam', faceBox);

    document.addEventListener('neu:view', e => { if (e.detail === 'bild') load(false); });
    setInterval(() => { if (App.view === 'bild' && !document.hidden && !drag) load(false); }, 8000);
    window.addEventListener('resize', paint); load(true);
    Neu.bild = { reload: () => load(true) };
  });
})();
