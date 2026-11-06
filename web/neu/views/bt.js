/* Netzwerk > Bluetooth und Geräte: Adapter und Treiber, Handys (Akkustand), Controller koppeln. */
(() => {
  'use strict';
  const { h, api, App, poll, ask, toast, run, badge, btn, alertBox, card, row, list, sw, prog, note } = Neu;
  const T = Neu.slots.netz, box = h('div', { class: 'stack', style: '--gap:var(--space-5)' }), info = note();
  T.add('n-bt', 'Bluetooth und Geräte', box);
  poll('btdriver', '/api/btdriver', 4000, () => App.view === 'netz');
  let ctAll = false, sig = '';
  const cmd = async body => { const ok = await Neu.djiCmd(body); if (!ok) return false; return true; };
  const phoneAge = s => s == null ? '' : s < 90 ? 'gerade eben' : s < 5400 ? `vor ${Math.round(s / 60)} Min.` : `vor ${Math.round(s / 3600)} Std.`;

  function adapterCard(d) {
    const ad = d.available ? (d.adapters || []) : [], pr = d.available ? (d.adapter_problems || []) : [], dr = d.driver || {};
    const drTxt = d.available && dr.message && ['working', 'waiting', 'failed', 'unsupported'].includes(dr.state) ? dr.message : '';
    const rows = [];
    if (!d.available) rows.push(h('p', { style: 'color:var(--danger)', text: d.reason || 'Der Bluetooth-Dienst ist nicht erreichbar' }));
    else {
      ad.forEach(a => rows.push(row({ icon: 'bluetooth', title: Neu.named(a.label || a.name || (a.usb_id ? 'Bluetooth-Stick' : 'Eingebauter Bluetooth-Adapter'), a.key ? Neu.iconBtn('pencil', 'Umbenennen', () => Neu.rename(a.key, a.custom ? a.label : '')) : null), desc: [a.usb_id, a.address].filter(Boolean).join(' · '), end: [badge(a.powered ? 'bereit' : 'ausgeschaltet', a.powered ? 'ok' : 'warn')],
        s: `Bluetooth-Adapter ${a.label || a.name || ''}`, k: 'bluetooth adapter stick' })));
      pr.forEach(x => rows.push(row({ icon: 'bluetooth', title: `${x.label || x.name} (${x.id})`, desc: x.hint, end: badge('nicht nutzbar', 'danger') })));
      if (!ad.length && !pr.length) rows.push(alertBox({ tone: 'warn', text: 'Kein Bluetooth-Adapter gefunden. Für DJI-Kameras einen USB-Bluetooth-Stick einstecken.' }));
    }
    const bd = App.data.btdriver || {}, run0 = bd.state === 'working', ch = bd.chip || null;
    const done = !!bd.helper_installed && !run0 && (bd.state === 'ok' || (!!ch && ch.state === 'ok' && (bd.state === 'none' || !bd.state)));
    let bad = bd.state === 'failed' || bd.state === 'unsupported', show;
    if (!bd.helper_installed) show = 'Der Treiber-Helfer ist auf dieser Box noch nicht installiert (install.sh erneut ausführen).';
    else if (ch && (bd.state === 'none' || !bd.state)) { bad = ch.state === 'failed' || ch.state === 'unsupported'; show = (bd.manual || ch.manual || ch.state !== 'ok') && ch.message; } else show = (bd.manual || bad || run0 || bd.state === 'waiting') && bd.message;
    return card({ title: 'Bluetooth-Adapter', sub: 'Die Bluetooth-Sticks dieser Box. Steht ein Stick als nicht nutzbar da, fehlt ihm der Treiber oder er wird nicht unterstützt.', body: [
      rows.length ? (rows[0].tagName === 'LI' ? list(rows) : h('div', { class: 'stack' }, rows)) : null, drTxt ? h('p', { style: 'font-size:var(--text-sm);' + (dr.state === 'failed' || dr.state === 'unsupported' ? 'color:var(--danger)' : ''), text: drTxt }) : null,
      run0 ? prog(bd.steps ? Math.round(100 * (bd.step || 0) / bd.steps) : 3, bd.step_text || 'Wird eingerichtet …') : null, show ? h('p', { style: 'font-size:var(--text-sm);' + (bad || !bd.helper_installed ? 'color:var(--danger)' : ''), text: show }) : null,
      h('div', { class: 'cluster' }, btn('Treiber jetzt einrichten', { variant: done ? null : 'primary', disabled: !bd.helper_installed || run0 || !!(ch && ch.state === 'working'), run: async () => { await api('POST', '/api/btdriver', {}); App.set('btdriver', await api('GET', '/api/btdriver')); }, ok: 'Wird gestartet …' })),
      h('p', { class: 'field__hint', text: 'Baut und lädt den Bluetooth-Treiber für Sticks, die der Kernel nicht kennt (zum Beispiel TP-Link UB500 oder UGREEN BT6.0). Das dauert einige Minuten, Bluetooth fehlt dabei kurz. Während einer Übertragung wartet die Box damit bis zum Ende. Auf der ROCK 5B Plus richtet der Knopf außerdem den Bluetooth-Chip des Boards ein, wenn er noch nicht läuft.' })] });
  }

  function phoneCard(d) {
    const ps = d.available ? (d.phones || []) : [], pr = d.phone_pairing || {};
    return card({ title: 'Handyverbindungen', sub: 'Zeigt den Akkustand gekoppelter Handys. Die Box fragt alle 10 Minuten kurz nach und trennt danach wieder, es gibt keine Dauerverbindung. Android meldet den Akku in Schritten von 20 %, ein iPhone in Schritten von etwa 10 %.', body: [
      ps.length ? list(ps.map(p => row({ icon: 'plug', title: p.name, desc: (p.percent == null ? (p.reading ? 'wird gelesen …' : 'noch kein Wert') : `${p.percent} %`) + (p.age != null ? ' · ' + phoneAge(p.age) : '') + (p.error ? ' · ' + p.error : ''),
        end: [btn('Jetzt lesen', { size: 'sm', disabled: p.reading, run: () => cmd({ cmd: 'phone_read', addr: p.addr }) }), btn('Entfernen', { size: 'sm', variant: 'ghost', onclick: async () => { if (await ask({ title: 'Dieses Handy entfernen?', text: 'Die Kopplung wird aufgehoben, zum erneuten Abfragen muss es neu gekoppelt werden.', ok: 'Entfernen', danger: true })) cmd({ cmd: 'phone_remove', addr: p.addr }); } })] }))) : h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: 'Noch kein Handy gekoppelt.' }),
      h('div', { class: 'cluster' }, btn(pr.active ? 'Abbrechen' : 'Handy koppeln', { disabled: !d.available, variant: pr.active ? null : 'primary', run: () => cmd({ cmd: pr.active ? 'phone_pair_stop' : 'phone_pair' }) })),
      pr.active || pr.message ? h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: pr.active ? `Die Box ist jetzt noch ${pr.left} Sekunden sichtbar. Am Handy in den Bluetooth-Einstellungen nach neuen Geräten suchen und „belabox“ auswählen.` : pr.message }) : null] });
  }

  function ctrlCard(d) {
    const ok = d.available, cs = ok ? (d.controllers || []) : [], sc = d.ctrl_scan || {}, found = (d.ctrl_found || []).filter(r => ctAll || r.input);
    let m = d.ctrl_message || sc.message || (!sc.active && (d.ctrl_found || []).length && !found.length ? 'Kein Controller erkannt. Mit „Alle Geräte zeigen“ siehst du alle Geräte in der Nähe.' : '');
    return card({ title: 'Controller', sub: 'Bluetooth-Controller mit Tasten, zum Beispiel ein Mini-Tastenfeld oder ein Gamepad. Controller einschalten, in den Kopplungsmodus bringen, nah an die Box legen und suchen. Die Tasten belegst du unter System > Eingabe.', body: [
      cs.length ? list(cs.map(c => row({ icon: 'gamepad', title: c.name, desc: (c.pairing ? 'Koppeln …' : c.connected ? 'verbunden' : 'getrennt') + (c.battery != null ? ` · Akku ${c.battery} %` : ''), end: btn('Entfernen', { size: 'sm', variant: 'ghost', onclick: async () => { if (await ask({ title: 'Diesen Controller entfernen?', text: 'Die Kopplung wird aufgehoben.', ok: 'Entfernen', danger: true })) cmd({ cmd: 'ctrl_remove', addr: c.addr }); } }) }))) : h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: 'Noch kein Controller gekoppelt.' }),
      h('div', { class: 'cluster' }, btn(sc.active ? 'Suche läuft …' : 'Nach Controllern suchen', { variant: 'primary', disabled: !ok || !!sc.active, run: () => cmd({ cmd: 'ctrl_scan' }) }),
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ctAll, onchange: e => { ctAll = e.target.checked; sig = ''; render(); } }), h('span', { class: 'check__text', text: 'Alle Geräte zeigen' }))),
      found.length ? list(found.map(r => row({ icon: 'bluetooth', title: r.name || '(ohne Namen)', desc: `${r.addr} · ${r.rssi} dBm`, end: btn('Koppeln', { size: 'sm', variant: 'primary', disabled: cs.some(c => c.pairing), run: () => cmd({ cmd: 'ctrl_pair', addr: r.addr }) }) }))) : null,
      m ? h('p', { class: 'muted', style: 'font-size:var(--text-sm);' + (d.ctrl_message ? 'color:var(--danger)' : ''), text: m }) : null] });
  }

  function render() {
    const d = App.data.dji; if (!d) return; const sg = JSON.stringify([d, App.data.btdriver, ctAll]); if (sg === sig) return; sig = sg;
    if (box.contains(document.activeElement) && document.activeElement.tagName === 'INPUT' && document.activeElement.type !== 'checkbox') return;
    box.replaceChildren(adapterCard(d), phoneCard(d), ctrlCard(d));
  }
  App.on('dji', render); App.on('btdriver', render);
})();
