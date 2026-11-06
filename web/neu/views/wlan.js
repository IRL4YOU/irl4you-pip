/* Netzwerk > WLAN und Hotspot: mit WLAN verbinden, Netze suchen und vergessen, Hotspot der Box einrichten und ein-/ausschalten. */
(() => {
  'use strict';
  const { h, ico, api, App, ask, toast, run, badge, btn, alertBox, card, row, list, sw, field, input, select, sleep, note } = Neu;
  const HS_CH = { bg: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], a: [0, 36, 40, 44, 48] };
  const T = Neu.slots.netz, box = h('div', { class: 'stack', style: '--gap:var(--space-5)' }), msg = note();
  T.add('n-wlan', 'WLAN und Hotspot', box);
  let drafts = {}, open = {}, pwShown = {}, sig = '', connDraft = { iface: '', ssid: '', pw: '', hidden: false };
  const hsOn = c => !!(c.hotspot && c.hotspot.running);
  const refresh = async () => { try { App.set('wifi', await api('GET', '/api/wifi')); } catch (e) { /* nichts */ } };
  const later = () => { [1500, 6000, 14000].forEach(ms => setTimeout(refresh, ms)); };
  async function post(body, wait) { msg.set(wait || ''); try { await api('POST', '/api/wifi', body); later(); return true; } catch (x) { msg.set(x.message, 'bad'); return false; } }
  const defaults = c => { const hs = c.hotspot; return { ssid: hs ? hs.ssid : 'IRL4YOU-BOX' + (c.mac4 ? '-' + c.mac4 : ''), pw: '', band: hs && hs.band === 'a' ? 'a' : 'bg', ch: hs ? (hs.channel || 0) : 0 }; };
  const genPw = () => { const al = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789', a = crypto.getRandomValues(new Uint32Array(12)); return [...a].map(n => al[n % al.length]).join(''); };

  async function hsToggle(c) {
    if (hsOn(c)) { delete pwShown[c.iface]; await post({ action: 'hotspot_stop', iface: c.iface }, 'Hotspot wird beendet …'); return; }
    if (!c.hotspot) { open[c.iface] = true; sig = ''; render(); msg.set('Bitte zuerst „Einstellen“: Name und Passwort festlegen, dann einschalten.'); return; }
    const body = { action: 'hotspot_start', iface: c.iface };
    if (c.ip) { if (!await ask({ title: 'Hotspot einschalten?', text: (c.ssid ? `Die WLAN-Karte ${c.iface} ist gerade mit „${c.ssid}“ verbunden.` : `Die WLAN-Karte ${c.iface} ist gerade mit einem WLAN verbunden.`) + ' Der Hotspot beendet diese Verbindung.\n\nHast du die Oberfläche über dieses WLAN geöffnet, bricht sie ab. Du erreichst sie danach über den Hotspot (Adresse meist 10.42.0.1) oder über ein anderes Netz.', ok: 'Hotspot einschalten' })) return; body.confirm = true; }
    await post(body, 'Hotspot wird gestartet …');
  }
  function hsPanel(c, busy) {
    const dr = drafts[c.iface] || (drafts[c.iface] = defaults(c));
    const ssid = input({ value: dr.ssid, maxlength: 32, autocomplete: 'off', oninput: e => { dr.ssid = e.target.value; tip.hidden = dr.ssid !== 'IRL4YOU-BOX'; } });
    const tip = h('p', { class: 'field__hint', hidden: dr.ssid !== 'IRL4YOU-BOX', text: 'Tipp: Mit mehreren Boxen bitte jedem Hotspot einen eigenen Namen geben. Die Kamera verwechselt sonst Netze mit gleichem Namen und tritt keinem mehr bei.' });
    const pw = input({ value: dr.pw, maxlength: 63, autocomplete: 'off', placeholder: c.hotspot ? 'leer = gespeichertes Passwort' : '', oninput: e => { dr.pw = e.target.value; } });
    const chSel = select(HS_CH[dr.band].map(n => [n, n ? `Kanal ${n}` : 'automatisch']), dr.ch, v => { dr.ch = parseInt(v, 10) || 0; });
    const band = select([['bg', '2,4 GHz (reicht weiter)'], ['a', '5 GHz (schneller, weniger Störungen)']], dr.band, v => { dr.band = v; const chs = HS_CH[v]; if (!chs.includes(+dr.ch)) dr.ch = 0; chSel.replaceChildren(...chs.map(n => h('option', { value: n, selected: +dr.ch === n }, n ? `Kanal ${n}` : 'automatisch'))); });
    if (c.band5 === false) band.options[1].disabled = true;
    return h('div', { class: 'tile stack' }, field({ label: 'Name des Hotspots', control: ssid }), tip, field({ label: 'Passwort (8 bis 63 Zeichen)', control: pw }), h('div', { class: 'grid-auto', style: '--min:12rem' }, field({ label: 'Band', control: band }), field({ label: 'Kanal', control: chSel })),
      h('div', { class: 'cluster' }, btn('Speichern', { variant: 'primary', size: 'sm', disabled: busy, onclick: async () => { if (await post({ action: 'hotspot_save', iface: c.iface, ssid: (dr.ssid || '').trim(), password: dr.pw || '', band: dr.band, channel: parseInt(dr.ch, 10) || 0 }, 'Speichere die Einstellungen …')) { open[c.iface] = false; delete drafts[c.iface]; sig = ''; } } }),
        btn('Passwort erzeugen', { size: 'sm', onclick: () => { dr.pw = genPw(); pw.value = dr.pw; msg.set('Neues Passwort eingetragen. Mit „Speichern“ wird es übernommen; bitte notieren.'); } }),
        btn('Schließen', { size: 'sm', variant: 'ghost', onclick: () => { open[c.iface] = false; delete drafts[c.iface]; sig = ''; render(); } })));
  }
  async function showPw(c) { if (pwShown[c.iface]) { delete pwShown[c.iface]; sig = ''; render(); return; } try { const r = await api('GET', '/api/wifi/hotspot?iface=' + encodeURIComponent(c.iface)); pwShown[c.iface] = r.password; msg.set(''); } catch (x) { msg.set(x.message, 'bad'); } sig = ''; render(); }
  async function rename(key, old) { const n = await Neu.askText({ title: 'Umbenennen', label: 'Neuer Name', value: old || '', hint: 'Zum Beispiel „Stick vorn“. Leer lassen setzt den Namen zurück.' }); if (n == null || n.trim() === (old || '')) return; try { await api('POST', '/api/devname', { key, name: n }); await refresh(); App.set('srtla', await api('GET', '/api/srtla')); App.data['dji:refresh'] && App.data['dji:refresh'](); } catch (x) { toast(x.message); } }
  Neu.rename = rename;

  function cardFor(c, busy, ups) {
    const on = hsOn(c); const kids = [];
    const status = c.camera_net ? `Kameranetz${on ? ' · Hotspot ' + c.hotspot.ssid + ' läuft · ' + c.ip : ''} (hier nicht änderbar)` : on ? `Hotspot „${c.hotspot.ssid}“ läuft · ${c.ip}` : c.ip ? `verbunden${c.ssid ? ' mit ' + c.ssid : ''} · ${c.ip}${c.signal != null ? ' · Signal ' + c.signal + ' %' : ''}` : 'nicht verbunden';
    const head = h('div', { class: 'split' }, h('div', null, h('h3', { class: 'card__title' }, Neu.named(c.label || c.iface, Neu.iconBtn('pencil', 'Umbenennen', () => rename(c.key, c.custom ? c.label : '')))), h('p', { class: 'card__sub', text: `${c.iface}${c.usb_id ? ' · ' + c.usb_id : ''}${c.driver ? ' · ' + c.driver : ''}` })), badge(on ? 'Hotspot an' : c.ip ? 'verbunden' : 'getrennt', on || c.ip ? 'ok' : undefined));
    kids.push(head, h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: status }));
    if (c.ip && !c.camera_net && !on) kids.push(sw({ checked: ups.includes(c.iface), label: 'Zum Senden verwenden', hint: 'Sendet die Box auch über dieses Netz. Danach ggf. die Sendekette neu starten.', s: `WLAN ${c.iface} zum Senden verwenden`, k: 'wlan senden upload ' + (c.label || ''), onchange: async (v, el) => { const sr = App.data.srtla, u = new Set(sr.settings.uplinks); v ? u.add(c.iface) : u.delete(c.iface); try { await api('POST', '/api/srtla/settings', { ...sr.settings, uplinks: [...u] }); App.set('srtla', await api('GET', '/api/srtla')); msg.set((v ? 'Zum Senden gespeichert' : 'Nicht mehr zum Senden') + '. Die Änderung wirkt innerhalb weniger Sekunden, ohne Neustart.'); } catch (x) { msg.set(x.message, 'bad'); el.checked = !v; } } }));
    if (c.ap !== false) {
      if (c.camera_net && !on) { /* nichts */ }
      else {
        const acts = [];
        if (!c.camera_net) acts.push(sw({ checked: on, label: 'Hotspot-Modus', hint: 'Macht aus dem Stick ein eigenes WLAN der Box, zum Beispiel für DJI-Kameras oder ein Handy. Der Stick ist dann nicht mehr mit einem anderen WLAN verbunden.', disabled: busy, s: `Hotspot ${c.iface}`, k: 'hotspot wlan ap kamera ' + (c.label || ''), onchange: (v, el) => { hsToggle(c).then(() => { sig = ''; setTimeout(() => { el.checked = hsOn((App.data.wifi.cards || []).find(x => x.iface === c.iface) || {}); }, 200); }); } }));
        const bar = [];
        if (on) bar.push(btn(pwShown[c.iface] ? 'Passwort ausblenden' : 'Passwort anzeigen', { size: 'sm', onclick: () => showPw(c) }));
        else if (!c.camera_net) { bar.push(btn('Einstellen', { size: 'sm', disabled: busy || open[c.iface], onclick: () => { open[c.iface] = true; drafts[c.iface] = defaults(c); msg.set(''); sig = ''; render(); } })); if (c.hotspot) bar.push(btn('Hotspot löschen', { size: 'sm', variant: 'danger', disabled: busy, onclick: async () => { if (await ask({ title: `Hotspot auf ${c.iface} löschen?`, text: 'Name, Passwort und Einstellungen dieser Karte werden entfernt. Kameras, die damit verbunden waren, finden ihn dann nicht mehr.', ok: 'Löschen', danger: true })) { delete pwShown[c.iface]; post({ action: 'hotspot_delete', iface: c.iface }, 'Hotspot wird gelöscht …'); } } })); }
        kids.push(...acts, bar.length ? h('div', { class: 'cluster' }, bar) : null);
        if (on && pwShown[c.iface]) kids.push(h('button', { class: 'tile mono', type: 'button', title: 'Klicken kopiert das Passwort', onclick: async () => { toast(await Neu.copyText(pwShown[c.iface]) ? 'Passwort kopiert' : 'Kopieren nicht möglich'); }, text: pwShown[c.iface] }));
        if (!on && open[c.iface]) kids.push(hsPanel(c, busy));
      }
    }
    return h('article', { class: 'card', data: { s: `WLAN-Karte ${c.label || c.iface}`, k: 'wlan hotspot karte ' + c.iface } }, h('div', { class: 'card__body' }, kids));
  }

  function connectCard(d, busy) {
    const cards = (d.cards || []).filter(c => !hsOn(c) && !c.camera_net);
    if (!connDraft.iface || !cards.some(c => c.iface === connDraft.iface)) connDraft.iface = (cards[0] || {}).iface || '';
    const saved = d.saved || [];
    const ifSel = select(cards.map(c => [c.iface, `${c.label || c.iface} (${c.iface})`]), connDraft.iface, v => { connDraft.iface = v; });
    const ssid = input({ value: connDraft.ssid, maxlength: 32, autocomplete: 'off', placeholder: 'WLAN-Name', oninput: e => { connDraft.ssid = e.target.value; hint(); } });
    const pw = input({ type: 'password', value: connDraft.pw, maxlength: 63, autocomplete: 'new-password', oninput: e => { connDraft.pw = e.target.value; } });
    const hid = h('input', { type: 'checkbox', checked: connDraft.hidden, onchange: e => { connDraft.hidden = e.target.checked; } });
    const hint = () => { pw.placeholder = saved.includes(ssid.value) ? 'leer = gespeichertes Passwort verwenden' : 'leer bei offenem Netz'; }; hint();
    const nets = ((d.scan && d.scan.nets) || []);
    const dbg = (d.scan && d.scan.debug) || [];
    return card({ title: 'Mit WLAN verbinden', sub: 'Zum Beispiel ein Handy-Hotspot, auch zum Senden nutzbar.', body: [
      cards.length ? null : alertBox({ tone: 'warn', text: 'Keine freie WLAN-Karte gefunden.' }),
      field({ label: 'WLAN-Karte', control: ifSel }),
      h('div', { class: 'cluster' }, btn('Netze suchen', { size: 'sm', ico: 'refresh', disabled: busy || !cards.length, onclick: () => post({ action: 'scan', iface: ifSel.value }, 'Suche läuft …') }),
        d.scan && d.scan.iface ? h('span', { class: 'muted', style: 'font-size:var(--text-xs)' }, `Letzte Suche: ${d.scan.iface}`) : null),
      nets.length ? list(nets.slice().sort((a, b) => (b.signal || 0) - (a.signal || 0)).map(n => row({ icon: 'wifi', title: n.ssid || '(ohne Namen)', desc: `${n.security && n.security !== 'offen' ? '🔒 ' + n.security : 'offen'} · Signal ${n.signal} %`, end: n.in_use ? badge('verbunden', 'ok') : btn('Wählen', { size: 'sm', onclick: () => { connDraft.ssid = n.ssid; ssid.value = n.ssid; hint(); pw.focus(); } }) }))) : null,
      !nets.length && dbg.length && !busy ? Neu.disclosure('Technische Angaben (die Suche hat nichts gefunden)', h('pre', { style: 'white-space:pre-wrap;font-size:var(--text-xs)', text: dbg.join('\n') })) : null,
      field({ label: 'WLAN-Name', control: ssid, s: 'Mit WLAN verbinden', k: 'wlan verbinden netz passwort ssid' }), field({ label: 'Passwort', control: pw }),
      h('label', { class: 'check' }, hid, h('span', { class: 'check__text', text: 'Verstecktes Netz' })),
      h('div', { class: 'cluster' }, btn('Verbinden', { variant: 'primary', disabled: busy || !cards.length, onclick: async () => { await post({ action: 'connect', iface: ifSel.value, ssid: ssid.value, password: pw.value, hidden: hid.checked }, 'Verbinde …'); connDraft.pw = ''; pw.value = ''; } }),
        btn('Trennen', { disabled: busy || !cards.length, onclick: () => post({ action: 'disconnect', iface: ifSel.value }, 'Trenne …') })),
      saved.length ? h('div', { class: 'stack' }, h('p', { style: 'font-weight:var(--weight-medium)', text: 'Gespeicherte Netze' }), list(saved.map(n => row({ icon: 'wifi', title: n, end: [btn('Wählen', { size: 'sm', onclick: () => { connDraft.ssid = n; ssid.value = n; hint(); } }), btn('Vergessen', { size: 'sm', variant: 'ghost', onclick: () => post({ action: 'forget', ssid: n }, 'Wird vergessen …') })] })))) : null] });
  }

  function render() {
    const d = App.data.wifi; if (!d) return;
    const busy = !d.helper_installed || d.state === 'working';
    const sg = JSON.stringify([d, [...Object.keys(open)].map(k => open[k]), App.data.srtla && App.data.srtla.settings.uplinks]); if (sg === sig) return;
    if (box.contains(document.activeElement) && /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return; sig = sg;
    const ups = (App.data.srtla && App.data.srtla.settings.uplinks) || [];
    const kids = [];
    if (!d.helper_installed) kids.push(alertBox({ tone: 'warn', text: 'Der WLAN-Helfer ist auf dieser Box noch nicht installiert (install.sh erneut ausführen).' }));
    if (d.message && d.state !== 'working') msg.set(d.message, d.state === 'failed' ? 'bad' : '');
    if (d.state === 'working') msg.set((d.action || '').startsWith('hotspot') ? 'Läuft …' : 'Läuft …');
    kids.push(msg);
    if (!(d.cards || []).length) kids.push(h('div', { class: 'empty card' }, 'Keine WLAN-Karte gefunden. Steck einen WLAN-Stick an.'));
    else { kids.push(...d.cards.map(c => cardFor(c, busy, ups))); kids.push(connectCard(d, busy)); }
    box.replaceChildren(...kids);
  }
  App.on('wifi', render); App.on('srtla', () => { sig = ''; render(); });
  document.addEventListener('neu:tab', () => { sig = ''; });
})();
