/* Ansicht „Kameras“: Quellen (RTMP, DJI, HDMI/USB) verwalten, neue Kameras anlegen, QR-Codes, DJI-Suche und -Einstellungen. */
(() => {
  'use strict';
  const { h, ico, fmt, api, App, poll, ask, sheet, closeSheet, toast, run, badge, btn, alertBox, card, row, list, sw, field, input, select, seg, disclosure, pageHead, view, note, copyBtn, copyText, sleep } = Neu;
  const ROLE = [['main', 'Hauptbild'], ['pip', 'Bild in Bild'], ['extra', 'Weitere']], ROLE_NAME = Object.fromEntries(ROLE);
  const DJI_STATE = { idle: ['Getrennt', 0], searching: ['Sucht', 1], connecting: ['Verbindet', 2], pairing: ['Koppelt', 3], preparing: ['Bereitet vor', 4], wifi: ['WLAN wird übergeben', 5], configuring: ['Stellt ein', 6], starting: ['Stream startet', 6], streaming: ['Streamt', 0], status: ['Nur Akku', 0], stopping: ['Beendet', 0], error: ['Fehler', 0] };
  const DJI_BUSY = ['searching', 'connecting', 'pairing', 'preparing', 'wifi', 'configuring', 'starting', 'stopping'];
  const DJI_STAB = [['off', 'Aus'], ['rocksteady', 'RockSteady'], ['rocksteadyplus', 'RockSteady+'], ['horizonbalancing', 'HorizonBalancing'], ['horizonsteady', 'HorizonSteady']];

  view('kameras', sec => {
    const T = Neu.slots.kameras = Neu.tabbed(sec, 'Kameras');
    const subP = h('p', { id: 'cam-sub' });
    const grid = h('div', { class: 'cam-grid' }), netBox = h('div'), qrOut = {}, qrApp = {};
    let sig = '';
    const addBtn = btn('Kamera hinzufügen', { variant: 'primary', ico: 'plus', onclick: () => Neu.goto('kameras', 'k-neu') });
    sec.prepend(h('div', { class: 'page-head' }, h('div', null, h('h1', { text: 'Kameras' }), subP), addBtn));

    /* ---------- Daten ---------- */
    const djiRefresh = poll('dji', '/api/dji', 2500, () => App.view === 'kameras' || App.view === 'netz' || App.view === 'live'); App.data['dji:refresh'] = djiRefresh;
    poll('hdmi', '/api/hdmi', 4000, () => App.view === 'kameras');
    poll('network', '/api/network', 15000, () => App.view === 'kameras');
    poll('wifi', '/api/wifi', 10000, () => App.view === 'netz' || App.view === 'kameras');
    const djiOf = c => ((App.data.dji && App.data.dji.cameras) || []).find(d => d.rtmp_key === c.key);
    Neu.djiCmd = async body => { try { await api('POST', '/api/dji/cmd', body); djiRefresh(); return true; } catch (e) { toast(e.message); return false; } };
    const djiCmd = Neu.djiCmd, refreshAll = () => { App.data['metrics:refresh'](); djiRefresh(); };

    /* ---------- Hauptverbindung (Adresse der Kameras) ---------- */
    let netSig = '';
    function renderNet() {
      const d = App.data.network; if (!d) return; if (document.activeElement && netBox.contains(document.activeElement)) return; const nsg = JSON.stringify(d); if (nsg === netSig && netBox.firstChild) return; netSig = nsg;
      const sel = select((d.options || []).map(o => [o.iface, `${o.label} · ${o.cam_ip || o.ip}${o.cam_ip && o.cam_ip !== o.ip ? ' (feste Adresse)' : ''}`]), d.selected || (d.options.find(o => o.ip === d.ip) || {}).iface, v => run(null, async () => { await api('POST', '/api/network', { iface: v }); App.set('network', await api('GET', '/api/network')); refreshAll(); }, 'Hauptverbindung gewählt'));
      if (!(d.options || []).length) sel.replaceChildren(h('option', null, 'keine Netzwerke'));
      netBox.replaceChildren(card({ title: 'Hauptverbindung', sub: 'Über dieses Netz erreichen Kameras die Box, wenn sie keine eigene Verbindung haben (Handy, Drohne, Action-Kamera).', body: [field({ label: 'Verbindung', control: sel, hint: `Adresse für die Kamera: rtmp://${d.ip}:1935/publish/<Schlüssel>${d.selected ? '' : ' (Standardweg, noch nicht gewählt)'}`, s: 'Hauptverbindung der Kameras', k: 'kamera verbindung netz adresse rtmp' })] }));
    }

    /* ---------- Kamera-Karten ---------- */
    const netOpts = () => ((App.data.network && App.data.network.options) || []);
    async function qrFor(c, app) {
      let hs = null, secret = null;
      if (app === 'gopro' || app === 'gopro13') {
        const w = App.data.wifi, card0 = w && w.cards.find(x => x.hotspot && x.hotspot.running);
        if (card0) { try { secret = await api('GET', '/api/wifi/hotspot?iface=' + encodeURIComponent(card0.iface)); hs = { iface: card0.iface, ip: card0.ip }; } catch (e) { /* ohne WLAN-Code */ } }
      }
      const r = NeuQR.codes(c, app, hs, secret);
      return h('div', { class: 'stack', style: '--gap:var(--space-2)' }, h('div', { class: 'cluster' }, r.items.map(([svg, label]) => h('figure', { style: 'margin:0;display:grid;gap:4px;justify-items:center' }, h('div', { html: svg, style: `width:${r.big ? '11rem' : '12rem'};background:#fff;border-radius:var(--radius-m);padding:4px` }), label ? h('figcaption', { class: 'muted', style: 'font-size:var(--text-xs)', text: label }) : null))), r.note ? h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: r.note }) : null);
    }
    function qrBlock(c) {
      const out = h('div'), sel = select(NeuQR.APPS, qrApp[c.id] || 'irlpro', v => { qrApp[c.id] = v; out.replaceChildren(); qrOut[c.id] = false; });
      const go = btn('QR-Code zeigen', { size: 'sm', onclick: async () => { if (out.firstChild) { out.replaceChildren(); go.textContent = 'QR-Code zeigen'; return; } qrApp[c.id] = sel.value; out.replaceChildren(await qrFor(c, sel.value)); go.textContent = 'QR-Code ausblenden'; } });
      return h('div', { class: 'stack', style: '--gap:var(--space-2)' }, h('div', { class: 'cluster', style: 'flex-wrap:nowrap' }, h('div', { class: 'grow' }, sel), go),
        h('p', { class: 'field__hint', text: 'IRL Pro und Moblin: Code mit der Kamera-App des Handys scannen. GoPro: zuerst den Hotspot der Box einrichten und die GoPro damit verbinden, dann bei Labs-Firmware die Codes 1, 2, 3 nacheinander vor die Kamera halten. Der Code enthält den Kamera-Schlüssel.' }), out);
    }
    async function setOff(c, wasOff) {
      try {
        if (c.in_pips) await api('POST', '/api/pipeline/active', { key: c.key, active: wasOff });
        const dj = djiOf(c); if (c.key && c.key.startsWith('dji-') && dj) await djiCmd({ cmd: wasOff ? 'connect' : 'disconnect', addr: dj.addr });
      } catch (e) { toast(e.message); } refreshAll();
    }
    function camNetSelect(c, dj) {
      const opts = [['', 'Hauptverbindung (Standard)'], ...netOpts().map(o => [o.iface, `${o.label} · ${o.cam_ip || o.ip}`])];
      if (dj) {
        const wo = (App.data.dji.wifi_options || []);
        const o2 = [['', '– wählen –'], ...wo.map(o => [o.ifname, o.type === 'other' ? `${o.ifname} · ${o.ip}` : (o.type === 'hotspot' ? `${o.ssid} (Hotspot der Box an ${o.ifname}, ${o.ip})` : `${o.ssid} (WLAN an ${o.ifname}, ${o.ip})`)]), ['manual', 'Manuell (WLAN-Name, Passwort, Adresse)']];
        if (dj.wifi_ifname && !o2.some(x => x[0] === dj.wifi_ifname)) o2.push([dj.wifi_ifname, dj.wifi_ifname + ' (nicht verbunden)']);
        const s = select(o2, dj.wifi_ifname || '', v => { if (v) djiCmd({ cmd: 'update', addr: dj.addr, wifi_ifname: v }).then(() => toast('Netz gewählt. Gilt nach „Neu verbinden“ oder beim nächsten Verbinden.')); });
        s.disabled = dj.switch_wait > 0; if (s.disabled) s.title = `Wechsel für zwei Minuten gesperrt: noch ${dj.switch_wait} s.`; return s;
      }
      if (c.via_src === 'hdmi') return null;
      if (c.iface && !opts.some(x => x[0] === c.iface)) opts.push([c.iface, c.iface + ' (nicht verbunden)']);
      return select(opts, c.iface || '', v => run(null, async () => { await api('POST', '/api/cameras/' + c.id, { iface: v }); App.data['metrics:refresh'](); }, 'Verbindung gesetzt'));
    }
    /* Werte, die sich bei jeder Abfrage ändern (Bildrate, Datenrate, Akku, Wartezeiten): kommen aus einer Stelle, damit sie später ohne Neuaufbau der Karte nachgetragen werden */
    function dynOf(c, dj) {
      const live = c.state === 'live', bat = dj ? dj.battery : c.battery;
      return { bat,
        meta: [ROLE_NAME[c.role] || '', dj && dj.model, live && c.h ? `${c.h}p` : '', live && c.fps ? `${fmt(c.fps, 0)} fps` : '', live && c.mbit ? `${fmt(c.mbit)} Mbit/s` : ''].filter(Boolean).join(' · ') || 'RTMP',
        batTxt: bat != null ? bat + ' %' + (dj && dj.battery_age > 600 ? ' (alt)' : '') : '', batLbl: 'Akku' + (dj && dj.charging ? ' (lädt)' : ''),
        alert: dj && dj.detail && (dj.state === 'error' || dj.state === 'idle') ? dj.detail + (dj.state === 'error' && dj.retry_in && dj.autoconnect ? ` – neuer Versuch in ${dj.retry_in} s` : '') : '' };
    }
    function patchCard(card, c, dj) {
      const D = dynOf(c, dj), set = (sel, t) => { const e = card.querySelector(sel); if (e && t != null && e.textContent !== t) e.textContent = t; };
      set('.dyn-meta', D.meta); set('.dyn-batlbl', D.batLbl); set('.dyn-battxt', D.batTxt);
      const bar = card.querySelector('.dyn-batbar'); if (bar && D.bat != null) { bar.dataset.tone = D.bat < 25 ? 'danger' : 'ok'; const v = bar.firstChild; if (v) v.style.setProperty('--value', D.bat + '%'); }
      if (D.alert) set('.dyn-alert .alert__body > span:last-child', D.alert);
    }
    function camCard(c, i, all) {
      const dj = c.key && c.key.startsWith('dji-') ? djiOf(c) : null, live = c.state === 'live';
      let st; if (dj) { const [txt, step] = DJI_STATE[dj.state] || [dj.state, 0]; st = [txt + (step ? ` (${step}/6)` : ''), dj.state === 'streaming' || dj.state === 'status' ? 'ok' : dj.state === 'error' ? 'danger' : DJI_BUSY.includes(dj.state) ? 'accent' : undefined]; if ((dj.state === 'idle' || dj.state === 'error') && dj.publishing) st = ['Stream läuft (ohne Bluetooth)', 'warn']; }
      else st = live ? ['Sendet', 'ok'] : c.state === 'unknown' ? ['Unbekannt'] : c.parked ? ['Aus (gewollt)'] : ['Wartet auf Signal'];
      const D = dynOf(c, dj), bat = D.bat, nsel = camNetSelect(c, dj);
      const canOff = c.in_pips || c.is_main || (c.key || '').startsWith('dji-');
      const mv = (dir) => { const o = all[i + dir]; return btn('', { size: 'sm', variant: 'ghost', icon: true, ico: dir < 0 ? 'down' : 'down', label: dir < 0 ? 'Kamera nach oben schieben' : 'Kamera nach unten schieben', disabled: !o, run: async () => { await api('POST', '/api/cameras/' + c.id, { swap_with: o.id }); App.data['metrics:refresh'](); } }); };
      const up = mv(-1), dn = mv(1); up.firstChild.style.transform = 'rotate(180deg)';
      const body = [
        h('div', { class: 'split' }, h('h2', { class: 'card__title', text: c.name || c.key }), badge(st[0], st[1])),
        h('div', { class: 'cam__meta' }, h('span', { class: 'dyn-meta', text: D.meta }), c.auto ? badge('automatisch erkannt') : null),
        bat != null ? h('div', { class: 'stack', style: '--gap:var(--space-1)' }, h('div', { class: 'split' }, h('span', { class: 'muted dyn-batlbl', style: 'font-size:var(--text-xs)', text: D.batLbl }), h('span', { class: 'num dyn-battxt', style: 'font-size:var(--text-xs)', text: D.batTxt })), h('div', { class: 'meter dyn-batbar', data: { tone: bat < 25 ? 'danger' : 'ok' } }, h('span', { style: `--value:${bat}%` }))) : null,
        D.alert ? (() => { const a = alertBox({ tone: dj.state === 'error' ? 'danger' : 'info', text: D.alert }); a.classList.add('dyn-alert'); return a; })() : null,
        nsel ? Neu.field({ label: 'Verbindung der Kamera', control: nsel }) : null,
        c.url ? h('div', { class: 'cluster', style: 'flex-wrap:nowrap' }, h('code', { class: 'url-box grow truncate', title: c.url, text: c.url }), copyBtn(c.url, 'Kopieren')) : null,
        (c.url && c.via_src !== 'hdmi' && !dj) ? qrBlock(c) : null,
        h('div', { class: 'cluster' }, btn('Einstellungen', { size: 'sm', onclick: () => openCam(c, dj) }),
          dj && dj.state !== 'streaming' && dj.state !== 'status' ? btn('Neu verbinden', { size: 'sm', variant: 'primary', run: () => djiCmd({ cmd: 'reconnect', addr: dj.addr }) }) : null,
          canOff ? btn(c.parked ? 'Einschalten' : 'Ausschalten', { size: 'sm', disabled: c.is_main, onclick: () => setOff(c, !!c.parked) }) : null,
          h('span', { class: 'grow' }), up, dn)];
      return h('article', { class: 'card cam', data: { s: c.name || c.key, k: 'kamera quelle ' + (c.key || '') } }, h('div', { class: 'card__body' }, body));
    }

    /* ---------- Einstellungen: normale Kamera ---------- */
    function openCam(c, dj) {
      if (dj) return openDji(dj, c);
      const name = input({ value: c.name || '', maxlength: 40 }), role = select(ROLE, c.role || 'extra');
      const save = btn('Speichern', { variant: 'primary', run: async () => {
        const patch = {}; if (name.value.trim() && name.value.trim() !== c.name) patch.name = name.value.trim(); if (role.value !== (c.role || 'extra')) patch.role = role.value;
        if (Object.keys(patch).length) await api('POST', '/api/cameras/' + c.id, patch); closeSheet(); App.data['metrics:refresh']();
      }, ok: 'Gespeichert' });
      const del = btn('Entfernen', { variant: 'danger', onclick: async () => { closeSheet(); if (!await ask({ title: `„${c.name}“ entfernen?`, text: 'Die Kamera wird aus der Box entfernt.', ok: 'Entfernen', danger: true })) return; await run(null, () => api('DELETE', '/api/cameras/' + c.id), 'Kamera entfernt'); App.data['metrics:refresh'](); } });
      sheet({ title: c.name || 'Kamera', body: [field({ label: 'Name', control: name, hint: 'Zum Beispiel Rucksack, Brust, Helm.' }), field({ label: 'Rolle im Bild', control: role, hint: 'Die Rolle bestimmt, wo die Kamera im Bild erscheint (Genaueres unter „Bild“).' })], foot: [del, h('span', { class: 'grow' }), btn('Abbrechen', { onclick: closeSheet }), save] });
    }

    /* ---------- Einstellungen: DJI-Kamera ---------- */
    function nearNets() { const seen = new Set(), out = []; for (const n of ((App.data.wifi && App.data.wifi.scan && App.data.wifi.scan.nets) || []).slice().sort((a, b) => (b.signal || 0) - (a.signal || 0))) if (n.ssid && !seen.has(n.ssid)) { seen.add(n.ssid); out.push(n); } return out; }
    function openDji(d, c) {
      const wo = (App.data.dji.wifi_options || []), other = o => o && (o.type === 'other' || o.secret_missing);
      const name = input({ value: (c && c.name) || d.name, maxlength: 40 });
      const wopts = [['', '– wählen –'], ...wo.map(o => [o.ifname, o.type === 'other' ? `${o.ifname} · ${o.ip}` : (o.type === 'hotspot' ? `${o.ssid} (Hotspot der Box an ${o.ifname}, ${o.ip})` : `${o.ssid} (WLAN an ${o.ifname}, ${o.ip})`)]), ['manual', 'Manuell (WLAN-Name, Passwort, Adresse)']];
      if (d.wifi_ifname && !wopts.some(x => x[0] === d.wifi_ifname)) wopts.push([d.wifi_ifname, d.wifi_ifname + ' (nicht verbunden)']);
      const conn = select(wopts, d.wifi_ifname || '', () => paintMan());
      const ssid = input({ value: d.ssid || '', maxlength: 32, autocomplete: 'off' }), pw = input({ type: 'password', maxlength: 63, autocomplete: 'new-password', placeholder: (d.saved || []).includes(d.ssid) ? 'gespeichert, leer lassen = behalten' : 'Passwort' }), ip = input({ value: d.ip || '', maxlength: 45 });
      const saved = select([['', '– gespeicherte Netze –'], ...(d.saved || []).map(n => [n, n])], (d.saved || []).includes(d.ssid) ? d.ssid : '', v => { if (v) { ssid.value = v; pw.value = ''; djiCmd({ cmd: 'use_saved', addr: d.addr, ssid: v }); } });
      const near = select([['', '– Netz wählen –']], '', v => { if (!v) return; ssid.value = v; pw.value = ''; if ((d.saved || []).includes(v)) djiCmd({ cmd: 'use_saved', addr: d.addr, ssid: v }); near.value = ''; pw.focus(); });
      const nearMsg = h('p', { class: 'field__hint' });
      const fillNear = () => { const ns = nearNets(); near.replaceChildren(h('option', { value: '' }, ns.length ? '– Netz wählen –' : '– keine Netze gefunden –'), ...ns.map(n => h('option', { value: n.ssid }, `${n.ssid} · ${n.security && n.security !== 'offen' ? '🔒 ' : ''}${n.signal} %`))); };
      const scan = btn('Netze suchen', { size: 'sm', run: async () => { const w = App.data.wifi; const card0 = w && w.cards && w.cards[0]; if (!card0) { nearMsg.textContent = 'Keine WLAN-Karte gefunden. Name und Passwort lassen sich auch von Hand eintragen.'; return; } nearMsg.textContent = 'Suche läuft …'; await api('POST', '/api/wifi', { action: 'scan', iface: card0.iface }); await sleep(9000); App.set('wifi', await api('GET', '/api/wifi')); fillNear(); nearMsg.textContent = nearNets().length ? '' : 'Keine Netze gefunden. Steckt ein WLAN-Stick, und läuft keine Sendung?'; } });
      const delSaved = btn('Gespeichertes Netz löschen', { size: 'sm', variant: 'danger', onclick: async () => { if (saved.value && await ask({ title: 'Gespeichertes Netz löschen?', text: saved.value, ok: 'Löschen', danger: true })) { await djiCmd({ cmd: 'delete_saved', addr: d.addr, ssid: saved.value }); closeSheet(); } } });
      const man = h('div', { class: 'stack' }, (d.saved || []).length ? h('div', { class: 'stack' }, Neu.field({ label: 'Gespeicherte Netze', control: saved }), delSaved) : null, Neu.field({ label: 'Netze in der Nähe', control: near }), scan, nearMsg, Neu.field({ label: 'WLAN-Name der Kamera', control: ssid }), Neu.field({ label: 'WLAN-Passwort', control: pw }));
      const ipRow = Neu.field({ label: 'Adresse der Box in diesem Netz', control: ip });
      function paintMan() { const v = conn.value, ot = other(wo.find(o => o.ifname === v)); man.hidden = !(v === 'manual' || ot); ipRow.hidden = ot || man.hidden; }
      fillNear(); paintMan();
      const res = select([['480p', '480p'], ['720p', '720p'], ['1080p', '1080p']], d.resolution || '1080p'), fps = select([[25, '25 fps'], [30, '30 fps']], d.fps === 25 ? 25 : 30), br = input({ type: 'number', min: 0.5, max: 16, step: 0.5, value: (d.bitrate || 6000) / 1000 }), stab = select(DJI_STAB, d.stabilization || 'off');
      const auto = sw({ checked: !!d.autoconnect, label: 'Automatisch verbinden und streamen' }), so = sw({ checked: !!d.status_only, label: 'Nur Akkustand lesen', hint: 'Für DJI-Kameras, die per HDMI senden.', disabled: !!(d.mode_locked !== undefined ? d.mode_locked : d.locked) });
      const lock = d.locked ? alertBox({ tone: 'info', text: 'Die Kamera ist verbunden. Änderungen an der Verbindung gelten ab der nächsten Verbindung, die laufende bleibt bestehen.', actions: [btn('Jetzt neu verbinden', { size: 'sm', onclick: () => djiCmd({ cmd: 'reconnect', addr: d.addr }) })] }) : null;
      const save = btn('Speichern', { variant: 'primary', run: async () => {
        const upd = { cmd: 'update', addr: d.addr, resolution: res.value, fps: parseInt(fps.value, 10), bitrate: Math.round(parseFloat(br.value) * 1000), stabilization: stab.value, autoconnect: auto.firstChild.checked, status_only: so.firstChild.checked };
        if (name.value.trim()) upd.name = name.value.trim(); if (conn.value) upd.wifi_ifname = conn.value; if (!d.locked || true) { if (!man.hidden) { upd.ssid = ssid.value; if (pw.value) upd.password = pw.value; } if (!ipRow.hidden) upd.ip = ip.value; }
        await api('POST', '/api/dji/cmd', upd); closeSheet(); djiRefresh(); App.data['metrics:refresh']();
      }, ok: 'Gespeichert' });
      const remove = btn('Entfernen', { variant: 'danger', onclick: async () => { closeSheet(); if (await ask({ title: 'Diese Kamera entfernen?', text: 'Sie verschwindet auch aus der Kameraliste.', ok: 'Entfernen', danger: true })) { await djiCmd({ cmd: 'remove', addr: d.addr }); App.data['metrics:refresh'](); } } });
      const acts = h('div', { class: 'cluster' }, btn('Verbinden', { size: 'sm', run: () => djiCmd({ cmd: 'connect', addr: d.addr }), disabled: d.state === 'streaming' || d.state === 'status' }), btn('Neu verbinden', { size: 'sm', run: () => djiCmd({ cmd: 'reconnect', addr: d.addr }) }), btn('Trennen', { size: 'sm', run: () => djiCmd({ cmd: 'disconnect', addr: d.addr }), disabled: d.state === 'idle' && !d.publishing }));
      sheet({ title: (c && c.name) || d.name, body: [lock, Neu.field({ label: 'Name', control: name }), so,
        ...(d.status_only ? [] : [h('h3', { text: 'Verbindung' }), Neu.field({ label: 'Verbindung der Kamera', control: conn, hint: 'Ein WLAN-Hotspot oder -Netz der Box: Die Kamera bekommt Name und Passwort selbst. Jede andere Verbindung (Ethernet, USB-Router, Modem): Name und Passwort des WLANs eintragen, dem die Kamera beitreten soll.' }), man, ipRow,
          h('h3', { text: 'Bild und Stream' }), h('div', { class: 'grid-auto', style: '--min:9rem' }, Neu.field({ label: 'Auflösung', control: res }), Neu.field({ label: 'Bildrate', control: fps }), Neu.field({ label: 'Bitrate (Mbit/s)', control: br }), Neu.field({ label: 'Stabilisierung', control: stab })),
          h('p', { class: 'field__hint' }, 'Die Kamera sendet auf den Schlüssel ', h('code', null, d.rtmp_key || ''), '. Bildrate 25 oder 30 (60 gibt es beim DJI-Livestream nicht).'), auto]), acts],
        foot: [remove, h('span', { class: 'grow' }), btn('Abbrechen', { onclick: closeSheet }), save] });
    }

    /* ---------- Anzeige der Quellen ---------- */
    function render() {
      const m = App.data.metrics || {}, djs = (App.data.dji && App.data.dji.cameras) || [];
      const cams = (m.cameras || []).filter(c => c.key !== 'hdmi');
      const sg = JSON.stringify([cams.map(c => [c.id, c.name, c.role, c.state, c.parked, c.iface, c.url, c.via, c.in_pips, c.is_main, c.auto, (c.key || '').startsWith('dji-') ? null : c.battery != null]), djs.map(d => [d.addr, d.state, d.battery != null, d.charging, d.detail, d.wifi_ifname, d.switch_wait > 0, d.publishing, d.saved, d.ssid, d.autoconnect, d.battery_age > 600, !!d.retry_in]), netOpts().map(o => o.iface), App.data.dji && App.data.dji.wifi_options]);
      const n = cams.length, live = cams.filter(c => c.state === 'live').length;
      subP.textContent = `${n} ${n === 1 ? 'Quelle' : 'Quellen'} · ${live} senden gerade`;
      const bad = djs.some(d => d.state === 'error'), nd = document.getElementById('nd-kameras'); nd.hidden = !bad; nd.dataset.tone = 'danger';
      if (sg === sig) { if (grid.children.length === cams.length) cams.forEach((c, k) => patchCard(grid.children[k], c, c.key && c.key.startsWith('dji-') ? djiOf(c) : null)); return; }
      if (grid.contains(document.activeElement) && document.activeElement.tagName !== 'BUTTON') return; sig = sg;
      grid.replaceChildren(...(cams.length ? cams.map((c, i) => camCard(c, i, cams)) : [h('div', { class: 'empty card', style: 'grid-column:1/-1' }, h('b', null, 'Noch keine Kamera'), h('span', null, 'Lege ein Handy oder eine Kamera an, um zu starten.'), btn('Kamera hinzufügen', { variant: 'primary', onclick: () => Neu.goto('kameras', 'k-neu') }))]));
    }
    App.on('metrics', render); App.on('dji', render); App.on('network', () => { renderNet(); render(); });

    /* ---------- Reiter: Quellen ---------- */
    T.add('k-quellen', 'Quellen', h('div', { class: 'stack', style: '--gap:var(--space-5)' }, grid, netBox));

    /* ---------- Reiter: Hinzufügen ---------- */
    const nm = input({ placeholder: 'Name, z. B. Hauptkamera', maxlength: 40 }), key = input({ placeholder: 'Schlüssel (leer = automatisch)', maxlength: 32 }), formErr = note('muted'), qrnApp = select(NeuQR.APPS, 'irlpro'), qrnOut = h('div'); let qrnId = {};
    const createCam = async (name, k) => { const r = await api('POST', '/api/cameras', { name, key: k || '' }); App.data['metrics:refresh'](); return r; };
    const addRtmp = btn('Kamera anlegen', { variant: 'primary', run: async () => { if (!nm.value.trim()) { formErr.set('Bitte einen Namen eingeben.', 'bad'); return; } try { await createCam(nm.value.trim(), key.value.trim()); nm.value = ''; key.value = ''; formErr.set(''); toast('Kamera angelegt'); Neu.goto('kameras', 'k-quellen'); } catch (e) { formErr.set(e.message, 'bad'); } } });
    const qrnGo = btn('QR-Code erzeugen', { size: 'sm', onclick: async () => {
      if (qrnOut.firstChild) { qrnOut.replaceChildren(); return; }
      const app = qrnApp.value, base = (NeuQR.APPS.find(a => a[0] === app) || [0, 'Kamera'])[1].replace(/ \(.*\)/, '');
      let list = (await api('GET', '/api/metrics')).cameras || [], c = list.find(x => x.id === qrnId[app]);
      if (!c) { const n = list.filter(x => (x.name || '').startsWith(base)).length + 1; try { const r = await createCam(base + ' ' + n, ''); qrnId[app] = r.id; list = (await api('GET', '/api/metrics')).cameras || []; c = list.find(x => x.id === r.id); } catch (e) { formErr.set(e.message, 'bad'); return; } }
      if (c && c.url) qrnOut.replaceChildren(await qrFor(c, app));
    } });
    qrnApp.addEventListener('change', () => qrnOut.replaceChildren());
    const scanBtn = btn('Nach DJI-Kameras suchen', { ico: 'bluetooth', onclick: async () => { await djiCmd({ cmd: 'scan' }); } }), scanList = h('div'), djiNote = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }), adaptNote = h('p', { class: 'muted', style: 'font-size:var(--text-sm)' });
    function renderScan() {
      const d = App.data.dji; if (!d) return;
      if (!d.available) { djiNote.textContent = d.reason || 'Bluetooth nicht verfügbar'; djiNote.style.color = 'var(--danger)'; scanBtn.disabled = true; scanList.replaceChildren(); return; }
      djiNote.textContent = d.bleak === false ? 'Die Bluetooth-Bibliothek (bleak) ist nicht installiert. install.sh erneut ausführen (braucht Internet).' : (d.scan_error || ''); djiNote.style.color = djiNote.textContent ? 'var(--danger)' : '';
      const pr = (d.adapter_problems || []).map(x => x.hint), ad = d.adapters || [], dr = d.driver || {}, drTxt = dr.message && ['working', 'waiting', 'failed', 'unsupported'].includes(dr.state) ? dr.message : '';
      adaptNote.textContent = pr.length && !drTxt ? pr.join(' ') : pr.length ? drTxt : (ad.length ? '' : 'Kein Bluetooth-Adapter gefunden. ') + drTxt; adaptNote.style.color = (pr.length || !ad.length || dr.state === 'failed' || dr.state === 'unsupported') ? 'var(--danger)' : '';
      scanBtn.disabled = !!d.scanning; scanBtn.replaceChildren(Neu.ico('bluetooth', 'sm'), d.scanning ? 'Suche läuft …' : 'Nach DJI-Kameras suchen');
      scanList.replaceChildren(...(d.scan || []).map(r => row({ icon: 'camera', title: r.name, desc: `${r.model} · ${r.addr} · ${r.rssi} dBm`, end: r.paired ? badge('hinzugefügt') : btn('Hinzufügen', { size: 'sm', variant: 'primary', run: async () => { if (await djiCmd({ cmd: 'add', addr: r.addr, name: r.name, model: r.model, kind: r.kind })) { App.data['metrics:refresh'](); toast('Kamera hinzugefügt'); } } }) })).map(r => list(r)));
    }
    App.on('dji', renderScan);
    T.add('k-neu', 'Hinzufügen', h('div', { class: 'cols-2' },
      h('div', { class: 'col' }, card({ title: 'Handy oder Kamera (RTMP)', sub: 'Die Kamera sendet an eine Adresse der Box. Du bekommst sie nach dem Anlegen.', body: [field({ label: 'Name', control: nm }), field({ label: 'Schlüssel', control: key, hint: 'Leer lassen für automatisch.' }), formErr], foot: [addRtmp] }),
        card({ title: 'Mit QR-Code einrichten', sub: 'Legt eine Kamera an und zeigt den Code für die App.', body: [field({ label: 'App', control: qrnApp, hint: 'IRL Pro und Moblin: Code mit der Kamera-App scannen. GoPro: zuerst den Hotspot der Box einrichten, dann die Codes 1, 2, 3 nacheinander vor die Kamera halten.' }), qrnOut], foot: [qrnGo] })),
      h('div', { class: 'col' }, card({ title: 'DJI-Kamera (Bluetooth)', sub: 'Kamera einschalten und wach halten, Kopplung an der Kamera bestätigen. Unterstützt: Osmo Action 3, 4, 5 Pro, 6 und Osmo Pocket 3, 4 und 4 Pro.', body: [djiNote, adaptNote, scanBtn, scanList] }),
        card({ title: 'HDMI, USB und Testbild', body: [h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, 'Ein HDMI-Eingang, eine USB-Webcam oder ein Testbild wird zu einer normalen Kamera.'), btn('Einstellungen öffnen', { onclick: () => Neu.goto('kameras', 'k-hdmi') })] }))));

    /* ---------- Reiter: HDMI und USB ---------- */
    const hdmiBox = h('div'); let hdmiDirty = false, hdmiBusy = false, hdmiSig = '';
    const usb = d => !!(d && d.settings && d.settings.source === 'usb'), test = d => !!(d && d.settings && d.settings.source === 'test');
    async function hdmiPost(body, done) { hdmiBusy = true; try { App.set('hdmi', await api('POST', '/api/hdmi', body)); hdmiDirty = false; if (done) toast(done); App.data['metrics:refresh'](); } catch (e) { toast(e.message); } hdmiBusy = false; hdmiSig = ''; renderHdmi(); }
    function renderHdmi() {
      const d = App.data.hdmi; if (!d) return; const sg = JSON.stringify(d); if (sg === hdmiSig || hdmiDirty || (hdmiBox.contains(document.activeElement) && ['INPUT', 'SELECT'].includes(document.activeElement.tagName))) return; hdmiSig = sg;
      const s = d.settings || {}, u = usb(d), t = test(d), usable = !!(d.service && d.available !== false) && !hdmiBusy;
      const stateTxt = !d.service ? 'Dienst fehlt' : d.available === false ? 'kein HDMI-Eingang' : ({ off: 'aus', waiting: u ? 'wartet auf Kamera' : 'wartet auf Signal', starting: 'startet …', streaming: 'sendet', error: 'Fehler' })[d.state] || '';
      let sigTxt = ''; if (!d.service) sigTxt = d.message || ''; else if (t) sigTxt = 'Bewegtes Testbild mit Zeitanzeige und Stereo-Testton (links tief, rechts hoch)';
      else if (u) { const x = d.usb || {}; sigTxt = !x.present ? (x.power && d.message ? d.message : 'Keine USB-Kamera angeschlossen') : [x.name, x.format].filter(Boolean).join(' · ') + (x.format && !x.audio && (s.audio !== 'none') ? ' · kein Ton der Kamera gefunden' : ''); }
      else if (d.available === false) sigTxt = 'Diese Box hat keinen HDMI-Eingang'; else if (d.signal_known !== false) { const g = d.signal || {}; sigTxt = g.locked ? `Signal ${g.width}×${g.height} · ${Math.round(g.fps)} Hz` : 'Kein HDMI-Signal'; }
      const src = select([['hdmi', 'HDMI-Eingang'], ['usb', 'USB-Webcam'], ['test', 'Testbild']], s.source || 'hdmi', v => hdmiPost({ source: v })); src.disabled = !usable;
      const on = sw({ checked: !!s.enabled, label: 'Als Kamera senden', disabled: !usable, onchange: v => hdmiPost({ enabled: v }), s: 'HDMI als Kamera senden', k: 'hdmi usb testbild eingang' });
      const fps = select([[30, '30 fps'], [25, '25 fps']], s.fps || 30, () => { hdmiDirty = true; }); fps.disabled = !usable || u;
      const br = input({ type: 'number', min: 1, max: 20, step: 0.5, value: (s.bitrate || 8000) / 1000, oninput: () => { hdmiDirty = true; } }); br.disabled = !usable;
      const tonName = t ? 'Testton' : u ? 'Ton der Kamera' : 'HDMI-Ton', au = select([['hdmi', tonName], ['none', 'ohne Ton']], s.audio || 'hdmi', () => { hdmiDirty = true; }); au.disabled = !usable;
      const fm = select([['auto', 'Automatisch (MJPEG zuerst)'], ['mjpeg', 'MJPEG (die Box kodiert mit der Bitrate neu)'], ['h264', 'H.264 (Strom der Kamera)']], s.usb_format || 'auto', () => { hdmiDirty = true; }); fm.disabled = !usable;
      const save = btn('Speichern', { variant: 'primary', disabled: !usable, onclick: () => hdmiPost({ bitrate: Math.round(parseFloat(br.value) * 1000), fps: parseInt(fps.value, 10), audio: au.value, usb_format: fm.value }, 'Gespeichert') });
      hdmiBox.replaceChildren(card({ title: t ? 'Testbild' : u ? 'USB-Webcam' : 'HDMI-Eingang', actions: [badge(stateTxt, d.state === 'streaming' ? 'ok' : d.state === 'error' || !d.service ? 'danger' : undefined)], sub: sigTxt, body: [
        d.state === 'error' && d.message ? alertBox({ tone: 'danger', text: d.message }) : null, field({ label: 'Quelle', control: src, s: 'HDMI Quelle', k: 'hdmi usb testbild quelle' }), on,
        h('div', { class: 'grid-auto', style: '--min:10rem' }, field({ label: 'Bildrate', control: fps }), field({ label: 'Bitrate (Mbit/s)', control: br }), field({ label: 'Ton', control: au }), u ? field({ label: 'Bildformat', control: fm }) : null)], foot: [save] }),
        h('p', { class: 'muted', style: 'font-size:var(--text-sm);margin-top:var(--space-3)' }, 'Das Bild wird in der Box zu einer normalen Kamera („HDMI“ oder „USB-Kamera“) und steht dann unter „Bild“ wie alle anderen zur Auswahl.'));
    }
    App.on('hdmi', renderHdmi); document.addEventListener('neu:tab', () => { hdmiDirty = false; hdmiSig = ''; });
    T.add('k-hdmi', 'HDMI und USB', hdmiBox);
  });
})();
