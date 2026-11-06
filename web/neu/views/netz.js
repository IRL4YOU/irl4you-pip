/* Ansicht „Netzwerk“: Leitungen (wählen, testen), Server und Bitrate, Fernzugriff. WLAN, Bluetooth und feste Adresse bleiben in der bisherigen Oberfläche. */
(() => {
  'use strict';
  const { h, ico, fmt, api, App, poll, ask, sheet, closeSheet, toast, run, badge, btn, alertBox, card, row, list, sw, field, input, select, seg, range, disclosure, pageHead, view } = Neu;

  view('netz', sec => {
    const leitungen = h('div', { class: 'stack' }), server = h('div', { class: 'stack' });
    const allBtn = btn('Alle Leitungen testen', { ico: 'activity', onclick: () => startTest({ all: true }) });
    sec.append(pageHead('Netzwerk', null, [allBtn]));
    const T = Neu.slots.netz = Neu.tabbed(sec, 'Netzwerk');
    T.add('n1', 'Leitungen', leitungen); T.add('n2', 'Server und Bitrate', server);
    const subP = h('p', { id: 'net-sub' }); sec.querySelector('.page-head > div').append(subP);

    /* ================= Leitungen ================= */
    const rateEls = {}, resEls = {};
    let rowsSig = '';
    const ut = () => App.data.uplinktest || { tests: {}, busy: false };
    async function startTest(body) {
      if (App.data.send && App.data.send.active && !await ask({ title: 'Während der Sendung testen?', text: 'Der Test belegt die Leitung für einige Sekunden, das Bild kann kurz schlechter werden.', ok: 'Trotzdem testen' })) return;
      try { App.set('uplinktest', await api('POST', '/api/uplinktest', body)); pollTest(); } catch (e) { toast(e.message); }
    }
    let testTimer = 0;
    function pollTest() { clearTimeout(testTimer); testTimer = setTimeout(async () => { try { App.set('uplinktest', await api('GET', '/api/uplinktest')); } catch (e) { return; } if (ut().busy) pollTest(); }, 1500); }
    function testText(t) {
      if (!t) return null;
      if (t.state === 'wartet') return h('span', { class: 'muted', text: 'Wartet …' });
      if (t.state === 'läuft') return h('span', { class: 'muted', text: 'Test läuft: ' + (t.step || 'Start') });
      if (t.state === 'fehler') return alertBox({ tone: 'danger', text: (t.text || 'Test fehlgeschlagen') + (t.hint ? ' ' + t.hint : '') });
      if (t.state === 'fertig') { const p = []; if (t.down_mbit != null) p.push(`↓ ${fmt(t.down_mbit)} Mbit/s`); if (t.up_mbit != null) p.push(`↑ ${fmt(t.up_mbit)} Mbit/s`); if (t.rtt_ms != null) p.push(`Antwortzeit ${t.rtt_ms} ms`); return h('span', { class: 'num', text: p.join(' · ') + (t.during_send ? ' (während der Sendung gemessen)' : '') }); }
      return null;
    }
    function paintTests() { const t = ut().tests || {}; for (const [k, el] of Object.entries(resEls)) el.replaceChildren(...[testText(t[k])].filter(Boolean)); allBtn.disabled = !!ut().busy; document.querySelectorAll('[data-testbtn]').forEach(b => { b.disabled = !!ut().busy; }); }

    async function setUplinks(iface, on, el) {
      const sr = App.data.srtla, ups = new Set(sr.settings.uplinks); on ? ups.add(iface) : ups.delete(iface);
      await run(el, async () => { await api('POST', '/api/srtla/settings', { ...sr.settings, uplinks: [...ups] }); App.set('srtla', await api('GET', '/api/srtla')); toast('Sendewege gespeichert. Das gilt innerhalb weniger Sekunden.'); });
      if (el && !ups.has(iface) === on) el.checked = !on;
    }
    function renderLeitungen() {
      const sr = App.data.srtla; if (!sr) return;
      const sel = new Set(sr.settings.uplinks), hot = new Set(sr.hotspot_ifaces || []), ifs = sr.interfaces || [];
      const sig = JSON.stringify([ifs.map(o => [o.iface, o.ip, o.label]), [...sel], sr.diag, sr.conn_names, [...hot], !!(App.data.send || {}).active]);
      if (sig === rowsSig) return; rowsSig = sig;
      for (const k of Object.keys(rateEls)) delete rateEls[k]; for (const k of Object.keys(resEls)) delete resEls[k];
      const rows = ifs.map(o => {
        const d = (sr.diag || {})[o.iface], name = (sr.conn_names || {})[o.iface] ? `${o.label} (${sr.conn_names[o.iface]})` : o.label;
        const rate = h('span', { class: 'num', text: '– Mbit/s hoch' }); rateEls[o.iface] = rate;
        const res = h('div', { class: 'utdiag' }); resEls[o.iface] = res;
        const state = d ? badge(d.level === 'bad' ? 'Ausgefallen' : 'Prüfen', d.level === 'bad' ? 'danger' : 'warn') : (o.ip ? badge('Bereit', 'ok') : badge('Getrennt'));
        const end = [state, hot.has(o.iface) ? badge('Hotspot der Box') : h('button', { class: 'btn', type: 'button', 'data-size': 'sm', 'data-testbtn': '', onclick: () => startTest({ iface: o.iface }) }, 'Testen'),
          sw({ checked: sel.has(o.iface), aria: `${name} zum Senden verwenden`, onchange: (on, el) => setUplinks(o.iface, on, el) })];
        const r = row({ icon: o.iface.startsWith('wlan') ? 'wifi' : 'ethernet', title: Neu.named(name, Neu.iconBtn('pencil', 'Leitung umbenennen', () => Neu.rename('net:' + o.iface, (sr.conn_names || {})[o.iface] || ''))), desc: h('span', null, o.ip || 'Keine Adresse', ' · ', rate), end });
        const extra = [d ? alertBox({ tone: d.level === 'bad' ? 'danger' : 'warn', text: d.text + (d.hint ? ' ' + d.hint : '') }) : null, res].filter(Boolean);
        return h('li', { data: { s: `Leitung ${o.iface} zum Senden verwenden`, k: 'netz upload senden lan wlan hotspot ' + o.label } }, h('div', { class: 'row' }, ...r.childNodes),
          h('div', { style: 'padding:0 var(--space-4) var(--space-3);display:grid;gap:var(--space-2)' }, extra));
      });
      for (const u of sr.settings.uplinks) if (!ifs.some(o => o.iface === u)) rows.push(row({ icon: 'ethernet', title: u, desc: 'Gerade nicht verbunden', end: sw({ checked: true, aria: `${u} zum Senden verwenden`, onchange: (on, el) => setUplinks(u, on, el) }) }));
      leitungen.replaceChildren(
        ...(sr.settings.uplinks.length === 0 ? [alertBox({ tone: 'warn', text: 'Keine Leitung zum Senden gewählt.' })] : []),
        card({ title: 'Leitungen zum Senden', sub: 'Wähle, welche Leitungen die Box zum Senden nutzt.', body: rows.length ? list(rows) : h('div', { class: 'empty' }, 'Keine Netzwerkkarte gefunden.'), flush: true }),
        h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, 'Der Test misst Herunterladen, Hochladen und Antwortzeit der Leitung (höchstens etwa 45 MB Datenvolumen je Leitung).'));
      paintTests(); document.getElementById('net-sub').textContent = `${sr.settings.uplinks.length} von ${ifs.length} Leitungen zum Senden gewählt`;
      const bad = Object.values(sr.diag || {}).some(d => d.level === 'bad'), warn = Object.keys(sr.diag || {}).length > 0, nd = document.getElementById('nd-netz'); nd.hidden = !warn; nd.dataset.tone = bad ? 'danger' : 'warn';
    }
    function paintRates() { const net = (App.data.metrics || {}).net || {}; for (const [k, el] of Object.entries(rateEls)) el.textContent = `${fmt((net[k] || {}).tx_mbit)} Mbit/s hoch`; }

    /* ================= Server und Bitrate ================= */
    function serverForm(srv) {
      const name = input({ value: srv ? srv.name : '', placeholder: 'z. B. Frankfurt', maxlength: 40 }), host = input({ value: srv ? srv.host : '', placeholder: 'z. B. srt.irl4you.de', autocomplete: 'off' }),
        port = input({ type: 'number', value: srv ? srv.port : 5000, min: 1, max: 65535 }), sid = input({ type: 'password', placeholder: srv && srv.has_streamid ? 'Gespeichert (leer lassen = unverändert)' : 'Optional', autocomplete: 'off' });
      let loaded = false;
      const body = [field({ label: 'Name', control: name }), field({ label: 'Adresse', control: host }), field({ label: 'Port', control: port }), field({ label: 'Stream-ID', control: sid, hint: 'Wird nur auf der Box gespeichert.' })];
      if (srv && srv.has_streamid) body.push(btn('Stream-ID anzeigen', { size: 'sm', onclick: async ev => { if (loaded) { sid.type = sid.type === 'text' ? 'password' : 'text'; return; } try { const r = await api('POST', `/api/srtla/${srv.id}/streamid`, {}); sid.value = r.streamid || ''; loaded = true; sid.type = 'text'; sid.placeholder = 'Leer = entfernen'; } catch (e) { toast(e.message); } } }));
      const save = btn('Speichern', { variant: 'primary', run: async () => {
        const b = { name: name.value, host: host.value, port: +port.value, streamid: sid.value };
        if (srv && loaded && !b.streamid) b.clear_streamid = true;
        if (srv) await api('POST', '/api/srtla/' + srv.id, b); else await api('POST', '/api/srtla', b);
        closeSheet(); App.set('srtla', await api('GET', '/api/srtla')); App.data['send:refresh']();
      }, ok: 'Server gespeichert' });
      sheet({ title: srv ? 'Server bearbeiten' : 'Server hinzufügen', body, foot: [btn('Abbrechen', { onclick: closeSheet }), save] });
    }
    let draft = null, srvSig = '';
    function renderServer() {
      const sr = App.data.srtla; if (!sr) return;
      const sg = JSON.stringify([sr.servers, sr.selected, sr.settings]); if (sg === srvSig && draft) return; srvSig = sg;
      if (!draft) draft = { ...sr.settings };
      const cur = sr.servers.find(s => s.id === sr.selected);
      const sel = select(sr.servers.map(s => [s.id, `${s.name} · ${s.host}:${s.port}`]), sr.selected, v => run(null, async () => { await api('POST', '/api/srtla/select', { id: v }); App.set('srtla', await api('GET', '/api/srtla')); App.data['send:refresh'](); }, 'Server gewählt'));
      sel.disabled = !sr.servers.length;
      const mx = range({ s: 'Höchste Bitrate', k: 'bitrate kbit qualität maximum', label: 'Höchste Bitrate', min: 1000, max: 20000, step: 500, value: Math.min(20000, Math.max(1000, draft.max_kbps)), unit: ' kbit/s', oninput: v => { draft.max_kbps = v; } });
      const mn = range({ s: 'Niedrigste Bitrate', k: 'bitrate kbit minimum', label: 'Niedrigste Bitrate', min: 100, max: 2000, step: 50, value: Math.min(2000, Math.max(100, draft.min_kbps)), unit: ' kbit/s', oninput: v => { draft.min_kbps = v; } });
      const lat = input({ type: 'number', value: draft.latency_ms, min: 100, max: 10000, step: 100, oninput: e => { draft.latency_ms = +e.target.value; } });
      const spread = seg('spread', [['best', 'Beste bevorzugen'], ['all', 'Alle gleichzeitig']], draft.spread || 'best', v => { draft.spread = v; }, 'Verteilung');
      server.replaceChildren(
        card({ title: 'SRTLA-Server', sub: sr.servers.length ? null : 'Noch kein Server angelegt. Ohne Server kannst du nicht live gehen.', actions: [btn('Neu', { size: 'sm', ico: 'plus', onclick: () => serverForm(null) })], body: [
          field({ s: 'Aktiver SRTLA-Server', k: 'server frankfurt stream-id srt wählen', label: 'Aktiver Server', control: sel, hint: cur ? (cur.has_streamid ? 'Stream-ID gespeichert.' : 'Keine Stream-ID gespeichert.') : null }),
          h('div', { class: 'cluster' }, btn('Bearbeiten', { size: 'sm', disabled: !cur, onclick: () => serverForm(cur) }),
            btn('Löschen', { size: 'sm', variant: 'danger', disabled: !cur, onclick: async () => { if (await ask({ title: `„${cur.name}“ löschen?`, text: 'Der Server wird aus der Box entfernt.', ok: 'Löschen', danger: true })) { await run(null, async () => { await api('DELETE', '/api/srtla/' + cur.id); App.set('srtla', await api('GET', '/api/srtla')); App.data['send:refresh'](); }, 'Server gelöscht'); } } }))] }),
        card({ title: 'Bitrate und Latenz', body: [mx, mn, field({ s: 'Verteilung auf Leitungen', k: 'beste alle spread', label: 'Verteilung auf Leitungen', control: h('div', null, spread), hint: 'Beste bevorzugen ist stabiler. Alle gleichzeitig nutzt auch langsamere Leitungen.' }),
          disclosure('Erweitert', field({ s: 'Latenz', k: 'verzögerung puffer ms', label: 'Latenz (ms)', control: lat, hint: 'Höher ist stabiler, verzögert aber das Bild.' }))],
          foot: [btn('Speichern', { variant: 'primary', run: async () => { await api('POST', '/api/srtla/settings', { ...draft, uplinks: sr.settings.uplinks }); draft = null; srvSig = ''; App.set('srtla', await api('GET', '/api/srtla')); App.data['send:refresh'](); }, ok: 'Gespeichert' })] }));
    }

    App.on('srtla', () => { renderLeitungen(); renderServer(); });
    App.on('metrics', paintRates); App.on('uplinktest', () => { paintTests(); if (ut().busy) pollTest(); });
    App.on('send', renderLeitungen);
    poll('uplinktest', '/api/uplinktest', 10000, () => App.view === 'netz');
  });
})();
