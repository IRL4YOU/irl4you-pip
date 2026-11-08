/* Ansicht „Live“: Sendung starten und beenden, Zustand, Leitungen, Kameras, Meldungen, Vorschau, Chat. */
(() => {
  'use strict';
  const { h, ico, fmt, dur, api, App, poll, ask, toast, run, badge, btn, alertBox, card, row, list, sw, pageHead, view, $, kv, prog, disclosure, note } = Neu;
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* privat */ } return null; };
  /* Nur neu zeichnen, wenn sich der Inhalt wirklich geändert hat (sonst flackern Karten bei jeder Abfrage, und Knöpfe werden mitten im Klick ausgetauscht) */
  const setIf = (box, nodes) => { const flat = [].concat(nodes).flat(3).filter(Boolean); const sg = flat.map(n => n.outerHTML !== undefined ? n.outerHTML : n.textContent).join(''); if (box.__sg === sg) return; box.__sg = sg; box.replaceChildren(...flat); };
  const LABEL = { starting: 'Startet …', restarting: 'Verbindung wird neu aufgebaut …', stopping: 'Wird beendet …', refused: 'Nicht gestartet' };

  view('live', sec => {
    const hero = h('div', { class: 'card__body' });
    const heroCard = h('section', { class: 'card' }, hero);
    const pending = h('div'), alerts = h('div', { class: 'stack', style: '--gap:var(--space-3)' });
    const kpi = h('div', { class: 'kpis' });
    const linesBody = h('div', { class: 'card__body', data: { flush: '' }, style: 'padding-top:var(--space-3)' });
    const camsBody = h('div', { class: 'card__body', data: { flush: '' }, style: 'padding-top:var(--space-3)' });

    /* ---------- Vorschau ---------- */
    const WHY = { off: 'Die Vorschau gibt es nur, während die Box sendet.', busy: 'Es laufen schon so viele Vorschauen, wie die Box zugleich zeigen kann.', 'kein-bild': 'Es kommt kein Bild. Läuft die Sendung?',
      extern: 'Die Vorschau ist von außen nicht freigegeben. Das lässt sich in der bisherigen Oberfläche erlauben.', missing: 'Die Vorschau ist auf dieser Box nicht verfügbar.' };
    let prevOn = false, prevTimer = 0;
    const prevMsg = h('div', { class: 'preview__msg' }, ico('eye', 'lg'), h('span', null, 'Die Vorschau startet auf Knopfdruck'));
    const prevImg = h('img', { alt: 'Vorschau der Sendung', hidden: true });
    prevImg.addEventListener('error', () => { if (prevOn) prevStop('Die Vorschau wurde unterbrochen. Läuft die Sendung noch?'); });
    const prevBtn = btn('Start', { size: 'sm', ico: 'play' });
    const prevBox = h('div', { class: 'preview' }, prevImg, prevMsg);
    function prevStop(text) {
      prevOn = false; clearTimeout(prevTimer); prevImg.removeAttribute('src'); prevImg.hidden = true; prevMsg.hidden = false;
      prevMsg.replaceChildren(ico('eye', 'lg'), h('span', null, text || 'Die Vorschau startet auf Knopfdruck')); prevBtn.replaceChildren(ico('play', 'sm'), 'Start');
    }
    async function prevStart() {
      prevBtn.setAttribute('aria-busy', 'true');
      let st; try { st = await api('GET', '/api/preview'); } catch (e) { st = { available: false, why: 'missing' }; }
      prevBtn.removeAttribute('aria-busy');
      if (!st.available) return prevStop(WHY[st.why] || WHY.missing);
      prevOn = true; prevMsg.hidden = true; prevImg.hidden = false; prevImg.src = '/api/preview/stream?fmt=mjpeg&t=' + Date.now();
      prevBtn.replaceChildren(ico('stop', 'sm'), 'Stop');
      prevTimer = setTimeout(() => prevStop('Die Vorschau ist nach 10 Minuten beendet.'), Math.max(5, (st.max_seconds || 600) - 3) * 1000);
    }
    prevBtn.addEventListener('click', () => prevOn ? prevStop() : prevStart());
    document.addEventListener('neu:view', e => { if (e.detail !== 'live' && prevOn) prevStop(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden && prevOn) prevStop(); });

    /* ---------- Zusammenbau ---------- */
    /* ---------- Vorschau-Optionen ---------- */
    const prevOpt = h('div', { class: 'tile stack', hidden: true, style: '--gap:var(--space-1)' });
    const keepSw = sw({ checked: store('pb_prev_always') === '1', label: 'Vorschau immer anzeigen', hint: 'Startet von selbst, sobald die Box sendet. Das verbraucht Daten und Rechenleistung.', s: 'Vorschau immer anzeigen', k: 'vorschau dauerhaft automatisch', onchange: v => { store('pb_prev_always', v ? '1' : null); } });
    const extSw = sw({ label: 'Vorschau von außen erlauben', hint: 'Auch über Tailscale. Die Vorschau ist dort klein, damit der Upload für die Sendung bleibt.', s: 'Vorschau von außen erlauben', k: 'vorschau extern tailscale', onchange: (v, el) => run(el, async () => { const r = await api('POST', '/api/previewaccess', { external: v }); extSw.firstChild.checked = !!r.external; }) });
    prevOpt.append(keepSw, extSw);
    api('GET', '/api/previewaccess').then(r => { extSw.firstChild.checked = !!r.external; }).catch(() => { });
    const prevGear = btn('', { size: 'sm', variant: 'ghost', icon: true, label: 'Vorschau-Optionen', ico: 'settings', onclick: () => { prevOpt.hidden = !prevOpt.hidden; } });
    setInterval(() => { if (document.hidden || prevOn || App.view !== 'live' || store('pb_prev_always') !== '1') return; const s = App.data.send; if (s && s.active && s.state === 'running') prevStart(); }, 10000);

    /* ---------- Bild und Ton während der Sendung ---------- */
    const liveCtl = h('div', { class: 'stack' }), liveCtlCard = card({ title: 'Bild und Ton jetzt', sub: 'Gilt sofort, ohne die Sendung neu zu starten.', body: liveCtl }); liveCtlCard.hidden = true;
    function renderLiveCtl() {
      const s = App.data.send, f = s && s.footer;
      if (!f || !f.cams || !f.cams.length) { liveCtlCard.hidden = true; return; } liveCtlCard.hidden = false;
      Neu.quick.fill(liveCtl, f);
    }

    /* ---------- Systemdetails ---------- */
    const detBox = h('div', { class: 'stack' });
    const det = disclosure('Systemdetails (Sendung, Prozessor, Temperaturen, Speicher)', detBox); det.addEventListener('toggle', () => { if (det.open) renderDet(); });
    const f1 = x => x == null || x < 0 ? '–' : (+x).toFixed(1), n0 = x => x == null || x < 0 ? '–' : String(Math.round(x));
    function renderDet() {
      if (!det.open) return; const m = App.data.metrics; if (!m) return; const d = m.details || {}, s = d.send, kids = [];
      kids.push(h('h3', { text: 'Sendung' }));
      if (!s) kids.push(h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: 'Keine Daten (die Sendung läuft nicht).' }));
      else kids.push(kv('Bitrate', `${f1(s.send_mbps)} Mbit/s (eingestellt ${f1(s.bitrate_kbps / 1000)})`), kv('Laufzeit (Ping, RTT)', `${f1(s.rtt_ms)} ms`),
        ...Object.entries(m.uplinks || {}).filter(([n, u]) => u && u.srtt != null).map(([n, u]) => kv('· ' + n, `${n0(u.srtt)} ms`)), kv('Sendepuffer', `${n0(s.snd_buf_ms)} ms (${n0(s.snd_buf_pkts)} Pakete)`), kv('Paketverlust', `${n0(s.loss_total)} (${s.loss_pct == null ? '–' : s.loss_pct.toFixed(2)} %)`), kv('Encoder-Bilder pro Sekunde', f1(s.fps)));
      const cores = (m.cpu && m.cpu.cores) || [], fr = (m.cpu && m.cpu.freq_mhz) || [], ac = d.accel || {};
      kids.push(h('h3', { text: 'Prozessor' }), ...cores.map((c, i) => h('div', null, kv(`Kern ${i}`, `${fmt(c, 0)} %${fr[i] ? ' · ' + (fr[i] >= 1000 ? (fr[i] / 1000).toFixed(1) + ' GHz' : fr[i] + ' MHz') : ''}`), prog(c))),
        ...(ac.npu ? [kv('NPU', `${ac.npu.load_pct} % · ${ac.npu.mhz} MHz`), prog(ac.npu.load_pct)] : []), ...(ac.gpu ? [kv('GPU', `${ac.gpu.load_pct} % · ${ac.gpu.mhz} MHz`), prog(ac.gpu.load_pct)] : []));
      kids.push(h('h3', { text: 'Temperaturen' }), ...((d.temps || []).map(t => kv(t.name, `${f1(t.c)} °C`))), ...((d.temps || []).length ? [] : [h('p', { class: 'muted', text: 'keine Daten' })]));
      const k = d.disk; kids.push(h('h3', { text: 'Speicher' }), ...(k ? [kv('Belegt', `${f1(k.used_gb)} GB (${f1(k.used_pct)} %)`), prog(k.used_pct), kv('Frei', `${f1(k.free_gb)} GB`), kv('Gesamt', `${f1(k.total_gb)} GB`)] : [h('p', { class: 'muted', text: 'keine Daten' })]));
      if (m.fan_pct != null) kids.push(kv('Lüfter', fmt(m.fan_pct, 0) + ' %'));
      detBox.replaceChildren(...kids);
    }

    /* ---------- Zusammenbau und Anordnung ---------- */
    /* Jede Karte hat eine feste Kennung; Reihenfolge und Ausblenden merkt sich der Browser getrennt für Computer und Handy. */
    const linkCard = (title, view, body) => h('section', { class: 'card' }, h('div', { class: 'card__head' }, h('h2', { class: 'card__title', text: title }), h('a', { class: 'btn', href: '#' + view, data: { variant: 'ghost', size: 'sm', viewLink: view }, text: 'Alle' })), body);
    const CARDS = [ /* Kennung, Titel, Inhalt, Spalte am Computer, darf ausgeblendet werden */
      ['sendung', 'Sendung starten und beenden', [heroCard, pending], 'a', false],
      ['kennzahlen', 'Kennzahlen', [kpi], 'a', true],
      ['vorschau', 'Vorschau', [card({ title: 'Vorschau', actions: [prevGear, prevBtn], body: [prevOpt, prevBox] })], 'a', true],
      ['steuerung', 'Bild und Ton jetzt (erscheint während der Sendung)', [liveCtlCard], 'a', true],
      ['deck', 'Deck', [Neu.chat.deckFrameCard()], 'a', true],
      ['details', 'Systemdetails', [h('section', { class: 'card' }, h('div', { class: 'card__body' }, det))], 'a', true],
      ['meldungen', 'Meldungen', [alerts], 'b', true],
      ['leitungen', 'Leitungen', [linkCard('Leitungen', 'netz', linesBody)], 'b', true],
      ['kameras', 'Kameras', [linkCard('Kameras', 'kameras', camsBody)], 'b', true],
      ['chat', 'Chat', [Neu.chat.feedCard()], 'b', true],
    ];
    const BY = Object.fromEntries(CARDS.map(c => [c[0], c]));
    const mq = window.matchMedia('(max-width: 64rem)'), prof = () => mq.matches ? 'mobile' : 'desktop';
    const lay = () => { try { const d = JSON.parse(store('pb_neu_live_' + prof()) || '{}'); return { order: (d.order || []).filter(x => BY[x]), hidden: (d.hidden || []).filter(x => BY[x] && BY[x][4]) }; } catch (e) { return { order: [], hidden: [] }; } };
    const saveLay = l => store('pb_neu_live_' + prof(), JSON.stringify(l));
    const seq = l => [...l.order, ...CARDS.map(c => c[0]).filter(x => !l.order.includes(x))];
    const colA = h('div', { class: 'col' }), colB = h('div', { class: 'col' });
    let editing = false;
    const wrap = {};
    CARDS.forEach(([id, title, nodes, , canHide]) => {
      const up = Neu.iconBtn('up', 'Nach oben', () => move(id, -1)), down = Neu.iconBtn('down', 'Nach unten', () => move(id, 1));
      const hide = canHide ? btn('', { size: 'sm', onclick: () => toggleHide(id) }) : null;
      wrap[id] = h('div', { class: 'lc', data: { id } }, h('div', { class: 'lc__bar' }, h('span', { class: 'lc__title', text: title }), up, down, hide), nodes);
    });
    function place() {
      const l = lay(), hid = new Set(l.hidden), order = seq(l), one = mq.matches;
      /* Nur verschieben, wenn sich etwas geändert hat (sonst lädt zum Beispiel das Deck neu) */
      [[colA, order.filter(id => one || BY[id][3] === 'a')], [colB, order.filter(id => !one && BY[id][3] === 'b')]].forEach(([col, ids]) => {
        const want = ids.map(id => wrap[id]); if (col.children.length === want.length && want.every((w, i) => col.children[i] === w)) return; col.replaceChildren(...want);
      });
      order.forEach(id => {
        const w = wrap[id], off = hid.has(id); w.hidden = off && !editing; w.toggleAttribute('data-off', off);
        const b = w.querySelector('.lc__bar .btn:last-child'); if (BY[id][4] && b) { b.textContent = off ? 'Einblenden' : 'Ausblenden'; }
      });
      sec.toggleAttribute('data-arrange', editing);
    }
    function move(id, d) {
      const l = lay(), o = seq(l), one = mq.matches, same = o.filter(x => one || BY[x][3] === BY[id][3]), i = same.indexOf(id), k = i + d;
      if (k < 0 || k >= same.length) return; const a = o.indexOf(same[i]), b = o.indexOf(same[k]); [o[a], o[b]] = [o[b], o[a]];
      saveLay({ order: o, hidden: l.hidden }); place(); wrap[id].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    function toggleHide(id) { const l = lay(), s = new Set(l.hidden); s.has(id) ? s.delete(id) : s.add(id); saveLay({ order: seq(l), hidden: [...s] }); place(); }
    const arrBar = h('div', { class: 'lc__edit', hidden: true }, h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: 'Mit den Pfeilen verschiebst du die Karten, mit „Ausblenden“ nimmst du sie von der Seite. Das gilt nur für dieses Gerät (Handy und Computer sind getrennt).' }),
      h('div', { class: 'cluster' }, btn('Fertig', { variant: 'primary', onclick: () => setEdit(false) }), btn('Zurücksetzen', { onclick: () => { saveLay({ order: [], hidden: [] }); place(); toast('Reihenfolge zurückgesetzt'); } })));
    const arrBtn = btn('Anordnen', { size: 'sm', ico: 'sliders', onclick: () => setEdit(!editing) });
    function setEdit(v) { editing = v; arrBar.hidden = !v; arrBtn.hidden = v; place(); }
    mq.addEventListener('change', place);
    sec.append(pageHead('Live', 'Alles, was du während der Sendung brauchst.', [arrBtn]), arrBar, h('div', { class: 'cols-main' }, colA, colB));
    place();

    /* ---------- Zustand der Sendung ---------- */
    const item = (icon, text, ok, action) => h('div', { class: 'check-item', data: ok ? { ok: '' } : {} }, ico(icon), h('span', { class: 'grow' }, text), action);
    function renderHero() {
      const s = App.data.send, m = App.data.metrics || {}, sr = App.data.srtla;
      if (!s) return;
      const active = !!s.active, running = s.state === 'running', busyState = active && !running;
      const mode = running ? 'live' : busyState ? 'live' : (s.can_start ? 'ready' : 'idle');
      document.body.dataset.state = mode;
      const kids = [];
      const ups = sr && sr.settings ? sr.settings.uplinks.length : 0;
      const send = (m.details || {}).send || {};
      let title, sub, items = [], action;
      if (active) {
        title = running ? 'Du bist live' : (LABEL[s.state] || s.state);
        const since = s.since ? dur(Date.now() / 1000 - s.since) : null;
        sub = [since && `Seit ${since}`, s.server && s.server.name, `${fmt(Neu.up.shown(m))} Mbit/s`].filter(Boolean).join(' · ');
        action = btn(running ? 'Beenden' : 'Bitte warten', { variant: running ? 'danger' : null, size: 'lg', ico: 'stop', disabled: !running, run: async () => {
          await api('POST', '/api/send', { action: 'stop' }); setTimeout(() => App.data['send:refresh'] && App.data['send:refresh'](), 600);
        } });
      } else if (s.can_start) {
        title = 'Bereit zum Livegang';
        sub = [s.server && `Server ${s.server.name}`, ups && `${ups} ${ups === 1 ? 'Leitung' : 'Leitungen'} gewählt`].filter(Boolean).join(' · ');
        items = [item('check', s.server ? 'Server: ' + s.server.name : 'Server gewählt', true), item('check', 'Bildaufbau gespeichert', true)];
        action = btn('Live gehen', { variant: 'live', size: 'lg', ico: 'play', run: async () => { await api('POST', '/api/send', { action: 'start', confirm: true }); setTimeout(() => App.data['send:refresh'] && App.data['send:refresh'](), 800); } });
      } else {
        title = s.state === 'refused' ? 'Nicht gestartet' : 'Noch nicht bereit';
        sub = s.state === 'refused' && s.message ? s.message : (s.reasons && s.reasons.length > 1 ? `${s.reasons.length} Dinge fehlen noch.` : 'Ein Schritt fehlt noch.');
        const goto = (view, text) => h('a', { class: 'btn', href: '#' + view, data: { size: 'sm', viewLink: view } }, text);
        if (s.no_layout) items.push(item('warning', 'Hauptkamera wählen und Bildaufbau speichern', false, goto('bild', 'Zum Bild')));
        if (s.no_server) items.push(item('warning', 'SRTLA-Server auswählen', false, goto('netz', 'Zum Server')));
        for (const r of s.reasons || []) if (!(s.no_layout && r === 'Kein Bildaufbau gespeichert') && !(s.no_server && r === 'Kein SRTLA-Server ausgewählt')) items.push(item('warning', r, false));
        action = btn('Live gehen', { variant: 'live', size: 'lg', ico: 'play', disabled: true });
      }
      kids.push(h('div', { class: 'hero' },
        h('div', null, h('p', { class: 'hero__title', text: title }), h('p', { class: 'muted', style: 'font-size:var(--text-sm)', data: { sub: '' } }), items.length ? h('div', { class: 'check-list' }, items) : null),
        h('div', { class: 'stack', style: '--gap:var(--space-2)' }, action)));
      const notes = [];
      if (s.net_conflict && Array.isArray(s.net_conflict.cards) && s.net_conflict.cards.length > 1) { const nets = (s.net_conflict.nets || []).join(', '); notes.push(nets ? `Die Karten ${s.net_conflict.cards.join(', ')} haben beide eine Adresse im Netz ${nets}. Antworten können über die falsche Karte gehen.` : `Die Karten ${s.net_conflict.cards.join(', ')} liegen im selben Netz. Antworten können über die falsche Karte gehen.`); }
      if (active && s.failover && s.failover.waiting) notes.push('Keine Kamera sendet, warte auf ein Signal');
      else if (active && s.failover && s.failover.degraded) notes.push('Notbetrieb: nicht alle Kameras senden (fehlende kommen nach 60 s stabilem Signal zurück)');
      if (active && s.always_note) notes.push(s.always_note);
      if (active && s.facecam_main) notes.push('Facecam ist das Hauptbild: nichts wird verpixelt');
      if (active && s.last && s.last_age != null && s.last_age < 180) notes.push(s.last);
      if (active && s.restarts) { const e = s.restarts.belacoder || 0, n = s.restarts.srtla_send || 0; if (e || n) notes.push('Seit dem Start automatisch neu gestartet: ' + [e && `Encoder ${e}×`, n && `Netzverbindung ${n}×`].filter(Boolean).join(', ')); }
      if (notes.length) kids.push(h('p', { class: 'muted', style: 'font-size:var(--text-sm);margin-top:var(--space-3)', text: notes.join(' · ') }));
      if (!active) {
        const a = s.autostart || {};
        kids.push(h('div', { style: 'margin-top:var(--space-3);border-top:1px solid var(--border);padding-top:var(--space-2)' },
          sw({ s: 'Automatisch starten', k: 'autostart kamera sendet live', checked: a.enabled, label: 'Automatisch starten, sobald eine Kamera sendet', hint: a.phase === 'wartet' ? 'Wartet auf eine sendende Kamera' : (a.message || null),
            onchange: (on, el) => run(el, () => api('POST', '/api/autostart', { enabled: on })) })));
      }
      const hsg = kids.map(n => n.outerHTML !== undefined ? n.outerHTML : n.textContent).join('') + '|' + JSON.stringify((s.autostart || {}).enabled);
      if (hero.__sg === hsg) { const cur = hero.querySelector('[data-sub]'); if (cur && cur.textContent !== (sub || '')) cur.textContent = sub || ''; }       // nur die Zeitangabe nachtragen
      else { hero.__sg = hsg; const se = kids[0].querySelector('[data-sub]'); if (se) se.textContent = sub || ''; hero.replaceChildren(...kids); }
      /* Neustart nötig? */
      const pend = active && s.pending && s.pending.length ? s.pending : [];
      setIf(pending, (pend.length ? [alertBox({ tone: 'warn', title: 'Änderungen noch nicht übernommen', text: `Geändert: ${pend.join(', ')}. Das gilt erst nach einem Neustart der Sendung.`,
        actions: [btn('Sendung neu starten', { size: 'sm', variant: 'primary', run: async () => { if (await ask({ title: 'Sendung neu starten?', text: 'Das Bild ist dabei einige Sekunden weg.', ok: 'Neu starten' })) await api('POST', '/api/send', { action: 'restart', confirm: true }); } })] })] : []));
    }

    function renderKpi() {
      const s = App.data.send || {}, m = App.data.metrics || {}, sr = App.data.srtla;
      const sel = sr && sr.settings ? sr.settings.uplinks : null, net = m.net || {};
      const up = Neu.up.shown(m);
      const send = (m.details || {}).send || {};
      const vw = Neu.up.view() || { plot: [], cut: 0, top: 5, WIN: 30000 };
      const spark = () => { const pts = vw.plot.map(x => `${(Math.min(100, (x.t - vw.cut) / vw.WIN * 100)).toFixed(1)},${(22 - Math.max(0, Math.min(vw.top, x.v)) / vw.top * 20).toFixed(1)}`).join(' '); const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('class', 'spark'); svg.setAttribute('viewBox', '0 0 100 24'); svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('aria-hidden', 'true'); const pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline'); pl.setAttribute('points', pts); svg.append(pl); return svg; };
      const stat = (label, value, unit, bar, tone, extra) => h('div', { class: 'card' }, h('div', { class: 'card__body' }, h('div', { class: 'stat' }, h('span', { class: 'stat__label', text: label }), h('span', { class: 'stat__value' }, value, h('span', { class: 'stat__unit', text: unit }))),
        bar != null && h('div', { class: 'meter', data: tone ? { tone } : {} }, h('span', { style: `--value:${Math.max(0, Math.min(100, bar))}%` })), extra));
      const cpu = (m.cpu || {}).avg, temp = m.temp_c, mem = m.mem || {};
      const cards = [
        stat('Upload', fmt(up), 'Mbit/s', null, null, h('div', { class: 'spark-box' }, h('span', { class: 'spark__scale', 'aria-hidden': 'true' }, h('i', null, fmt(vw.top, vw.top % 1 ? 1 : 0)), h('i', null, fmt(vw.top / 2, 1)), h('i', null, '0')), spark())),
        stat('Prozessor', fmt(cpu, 0), '%', cpu, cpu > 90 ? 'danger' : cpu > 75 ? 'warn' : undefined),
        stat('Temperatur', fmt(temp, 0), '°C', temp != null ? temp / 100 * 100 : null, temp > 80 ? 'danger' : temp > 70 ? 'warn' : 'ok'),
        s.active && send.rtt_ms != null ? stat('Antwortzeit', fmt(send.rtt_ms, 0), 'ms', null) : stat('Speicher frei', fmt((mem.avail_mb || 0) / 1024), 'GiB')];
      /* Aufbau nur ändern, wenn sich Beschriftung, Einheit oder Farbe ändern; sonst die Zahlen, Balken und die Kurve an Ort und Stelle nachtragen (kein Flackern) */
      const shape = cards.map(c => [c.querySelector('.stat__label').textContent, c.querySelector('.stat__unit').textContent, !!c.querySelector('.meter'), (c.querySelector('.meter') || { dataset: {} }).dataset.tone || '']);
      const sg = JSON.stringify(shape);
      if (kpi.__sg !== sg || kpi.children.length !== cards.length) { kpi.__sg = sg; kpi.replaceChildren(...cards); return; }
      cards.forEach((n, i) => {
        const o = kpi.children[i], ov = o.querySelector('.stat__value'), nv = n.querySelector('.stat__value');
        if (ov.firstChild && nv.firstChild && ov.firstChild.nodeValue !== nv.firstChild.nodeValue) ov.firstChild.nodeValue = nv.firstChild.nodeValue;
        const om = o.querySelector('.meter > span'), nm = n.querySelector('.meter > span'); if (om && nm) om.setAttribute('style', nm.getAttribute('style'));
        const op = o.querySelector('.spark polyline'), np = n.querySelector('.spark polyline'); if (op && np) op.setAttribute('points', np.getAttribute('points'));
        const os = o.querySelectorAll('.spark__scale i'), ns = n.querySelectorAll('.spark__scale i'); ns.forEach((e, k) => { if (os[k] && os[k].textContent !== e.textContent) os[k].textContent = e.textContent; });
      });
    }

    function renderLines() {
      const sr = App.data.srtla, m = App.data.metrics || {}, s = App.data.send || {};
      if (!sr) return;
      const sel = new Set(sr.settings.uplinks), net = m.net || {}, st = m.uplinks || {};
      const rows = sr.interfaces.filter(o => sel.has(o.iface)).map(o => {
        const u = st[o.iface] || {}, d = (sr.diag || {})[o.iface];
        const tone = d && d.level === 'bad' ? 'danger' : d ? 'warn' : (s.active ? (u.state === 'an' ? 'ok' : u.state === 'reserve' ? 'warn' : undefined) : (o.ip ? 'ok' : undefined));
        const label = s.active ? (u.state === 'an' ? 'Sendet' : u.state === 'reserve' ? 'Reserve' : 'Aus') : (o.ip ? 'Bereit' : 'Getrennt');
        return row({ icon: o.iface.startsWith('wlan') ? 'wifi' : 'ethernet', title: (sr.conn_names || {})[o.iface] ? `${o.label} (${sr.conn_names[o.iface]})` : o.label,
          desc: d ? d.text : `${fmt((net[o.iface] || {}).tx_mbit)} Mbit/s hoch`, end: badge(d && d.level !== 'bad' ? 'Prüfen' : label, tone) });
      });
      setIf(linesBody, rows.length ? list(rows) : h('div', { class: 'empty' }, h('span', { text: 'Keine Leitung zum Senden gewählt.' }), h('a', { class: 'btn', href: '#netz', data: { size: 'sm', viewLink: 'netz' }, text: 'Leitungen wählen' })));
    }

    const ROLE = { main: 'Hauptbild', pip: 'Bild in Bild', extra: 'Weitere' };
    function renderCams() {
      const m = App.data.metrics || {};
      const cams = (m.cameras || []).slice().sort((a, b) => (a.is_main ? 0 : 1) - (b.is_main ? 0 : 1));
      const rows = cams.map(c => {
        const live = c.state === 'live', tone = live ? 'ok' : (c.state === 'unknown' || c.parked) ? undefined : 'warn';
        const label = live ? 'Sendet' : c.state === 'unknown' ? 'Unbekannt' : c.parked ? 'Aus' : 'Kein Signal';
        const bits = [ROLE[c.role] || '', live && c.fps ? `${fmt(c.fps, 0)} fps` : '', c.battery != null ? `Akku ${c.battery} %` : ''].filter(Boolean).join(' · ');
        return row({ icon: 'camera', title: c.name || c.key, desc: bits, end: badge(label, tone) });
      });
      setIf(camsBody, rows.length ? list(rows) : h('div', { class: 'empty' }, h('span', { text: 'Noch keine Kamera.' }), h('a', { class: 'btn', href: '#kameras', data: { size: 'sm', viewLink: 'kameras' }, text: 'Kamera hinzufügen' })));
    }

    const USB = { wlan: 'USB-WLAN-Adapter getrennt', bt: 'USB-Bluetooth-Adapter getrennt', net: 'USB-Router getrennt', cam: 'USB-Kamera getrennt', other: 'USB-Gerät getrennt' };
    const when = t => { const d = new Date((t || 0) * 1000); return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }); };
    function alertInfo(a) {
      if (a.kind === 'usb') return { title: USB[a.cat] || USB.other, text: [a.name, a.ids, `Um ${when(a.t)}`, a.why === 'emi' || a.why === 'over' ? 'Stromversorgung prüfen' : ''].filter(Boolean).join(' · '), dismiss: true };
      if (a.kind === 'cam') return { title: (a.cam === 'dji' ? 'DJI-Kamera ausgefallen: ' : 'Kamera ausgefallen: ') + (a.name || ''), text: a.cam === 'dji' ? 'Bluetooth getrennt, Akku leer oder Kamera aus. Kamera aufwecken und prüfen.' : 'Die Kamera sendet nicht mehr. Akku, WLAN und Kabel prüfen.', dismiss: true };
      if (a.kind === 'hdmi') return { title: a.src === 'usb' ? 'USB-Kamera nicht gefunden' : 'HDMI: kein Signal', text: a.src === 'usb' ? 'Kamera anstecken und einschalten (Webcam-Modus).' : 'Kabel prüfen und die Kamera einschalten.', dismiss: true };
      return { title: a.text || 'Meldung', text: '', dismiss: false };
    }
    function renderAlerts() {
      const m = App.data.metrics || {}, al = (m.alerts || []).slice(0, 4);
      setIf(alerts, al.map(a => {
        const i = alertInfo(a);
        return alertBox({ tone: a.level === 'crit' ? 'danger' : 'warn', title: i.title, text: i.text, actions: i.dismiss ? [btn('Verstanden', { size: 'sm', run: async () => { await api('POST', '/api/alerts/dismiss', { kind: a.kind, t: a.t }); App.data['metrics:refresh'] && App.data['metrics:refresh'](); } })] : [] });
      }));
    }

    App.on('send', () => { renderHero(); renderLiveCtl(); }); App.on('srtla', () => { renderHero(); renderKpi(); renderLines(); });
    App.on('metrics', () => { renderKpi(); renderLines(); renderCams(); renderAlerts(); renderHero(); renderDet(); });
    App.data['send:refresh'] = poll('send', '/api/send', 2000);
    App.data['metrics:refresh'] = poll('metrics', '/api/metrics', 2000);
    poll('srtla', '/api/srtla', 5000);
  });
})();
