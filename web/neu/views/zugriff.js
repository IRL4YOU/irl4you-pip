/* Netzwerk > Zugriff und Adresse: Oberfläche über fremde WLANs sperren, feste Zusatzadresse (LAN). */
(() => {
  'use strict';
  const { h, api, App, poll, ask, toast, run, badge, btn, alertBox, card, sw, field, input, select, note } = Neu;
  const T = Neu.slots.netz, box = h('div', { class: 'stack', style: '--gap:var(--space-5)' });
  T.add('n-zugriff', 'Zugriff und Adresse', box);
  poll('uiaccess', '/api/uiaccess', 20000, () => App.view === 'netz');
  poll('netaddr', '/api/netaddr', 15000, () => App.view === 'netz');
  const uaCard = h('div'), naCard = h('div'); box.append(uaCard, naCard);

  function renderUa() {
    const d = App.data.uiaccess; if (!d) return; const l = d.client_wifi || [];
    uaCard.replaceChildren(card({ title: 'Zugriff auf diese Oberfläche', body: [
      sw({ checked: !!d.block_client_wifi, label: 'Über fremde WLANs sperren', s: 'Über fremde WLANs sperren', k: 'zugriff sperren gast wlan http oberfläche', onchange: (v, el) => run(el, async () => { try { App.set('uiaccess', await api('POST', '/api/uiaccess', { block_client_wifi: v })); } catch (x) { el.checked = !v; throw x; } }) }),
      h('p', { class: 'field__hint', text: 'Die Oberfläche ist unverschlüsselt (HTTP). In einem WLAN, in dem die Box nur Gast ist (Hotel, Handy-Hotspot, fremdes Netz), kann dort jeder mitlesen. Ist der Schalter an, antwortet die Box in solchen WLANs nicht. Erreichbar bleiben Ethernet, der Hotspot der Box, USB und Tailscale (HTTPS). Wer sich aussperrt, schaltet den Schalter über Ethernet oder Tailscale wieder aus.' }),
      h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: l.length ? `Gast-WLAN der Box: ${l.map(x => x.iface + ' (' + x.ip + ')').join(', ')}` : 'Die Box ist gerade in keinem Gast-WLAN.' })] }));
  }

  let naSig = '', ack = false;
  function renderNa() {
    const d = App.data.netaddr; if (!d) return; const sg = JSON.stringify(d); if (sg === naSig) return; if (naCard.contains(document.activeElement) && document.activeElement.tagName === 'INPUT' && document.activeElement.type !== 'checkbox') return; naSig = sg;
    const cards = d.cards || [], ready = !!d.installed && cards.length > 0, err = note();
    const on = sw({ checked: !!d.enabled, label: 'Feste Zusatzadresse verwenden', disabled: !ready, s: 'Feste Zusatzadresse LAN', k: 'ip adresse fest statisch zusatz lan', onchange: () => gate() });
    const mac = select(cards.map(c => [c.mac, `${c.name} · ${c.mac}${c.addrs && c.addrs.length ? ' · ' + c.addrs.join(', ') : ''}`]), d.mac || (cards[0] || {}).mac);
    if (d.mac && ![...mac.options].some(o => o.value === d.mac)) mac.append(h('option', { value: d.mac, selected: true }, d.mac));
    const addr = input({ value: d.addr || '', maxlength: 15, inputmode: 'decimal', placeholder: 'z. B. 192.168.1.50', autocomplete: 'off' }), prefix = input({ type: 'number', min: 8, max: 30, value: d.prefix || 24 });
    const ackBox = h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ack, onchange: e => { ack = e.target.checked; gate(); } }), h('span', { class: 'check__text', text: 'Ich bin einverstanden, dass die Änderung am Netzwerk der Box auf meine eigene Verantwortung erfolgt.' }));
    const save = btn('Speichern', { variant: 'primary', run: async () => {
      try { const body = on.firstChild.checked ? { enable: true, ack, mac: mac.value, addr: addr.value.trim(), prefix: parseInt(prefix.value, 10) } : { enable: false }; App.set('netaddr', await api('POST', '/api/netaddr', body)); ack = false; toast('Gespeichert'); naSig = ''; } catch (x) { err.set(x.message, 'bad'); }
    } });
    function gate() { ackBox.hidden = !on.firstChild.checked; save.disabled = !ready || (on.firstChild.checked && !ack); } gate();
    const status = !d.installed ? 'Kommt mit dem nächsten Software-Update.' : !cards.length ? 'Keine LAN-Netzkarte gefunden.' : d.enabled ? (d.present ? `Aktiv: ${d.addr}/${d.prefix} auf ${(cards.find(c => c.mac === d.mac) || {}).name || 'der gewählten Karte'}` : 'Eingestellt, aber gerade nicht auf der Karte. Sie wird gesetzt, sobald die Karte hochfährt.') : 'Aus. Die Box nutzt nur die Adresse vom Router.';
    naCard.replaceChildren(card({ title: 'Feste Zusatzadresse (LAN)', sub: 'Neben der Adresse vom Router (DHCP) bekommt die Box eine feste, zum Beispiel wenn sie am selben Router hängt wie andere Geräte.', body: [
      h('p', { class: 'field__hint', text: 'Die feste Adresse gehört zur MAC-Adresse der gewählten Netzkarte. Voraussetzung ist ein getrennter Adressraum: Sie darf sich nicht mit dem Netz einer anderen Netzkarte überschneiden, kein anderes Gerät darf sie benutzen, und sie sollte außerhalb des DHCP-Bereichs deines Routers liegen. Das gilt für die ganze Box, nicht nur für dieses Gerät.' }),
      on, field({ label: 'Netzkarte', control: mac }), h('div', { class: 'grid-auto', style: '--min:10rem' }, field({ label: 'IP-Adresse', control: addr }), field({ label: 'Netzlänge', control: prefix })), ackBox,
      h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: status }), err], foot: [save] }));
  }
  App.on('uiaccess', renderUa); App.on('netaddr', renderNa);
})();
