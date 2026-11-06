/* Netzwerk > Fernzugriff (Tailscale): einrichten, anmelden, im privaten Netz freigeben, öffentlich (Funnel), Statistik öffentlich. */
(() => {
  'use strict';
  const { h, api, App, poll, ask, toast, run, badge, btn, alertBox, card, row, list, sw, field, input, disclosure, copyBtn, note } = Neu;
  const R_STATE = { Running: 'verbunden', NeedsLogin: 'Anmeldung nötig', Stopped: 'getrennt', NoState: 'startet …', Starting: 'startet …' };
  const TS = /^https:\/\/(login|console)\.tailscale\.com\//;
  const FUNNEL_ASK = 'Die Oberfläche öffentlich im Internet freigeben (Funnel)?\n\nDanach kann JEDER im Internet, der die Adresse der Box kennt, die Anmeldeseite öffnen, auch ohne Tailscale. Geschützt ist die Box dann nur durch das BELABOX-Passwort.\n\n• Nur mit einem starken, langen Passwort verwenden.\n• Die Freigabe bleibt an, auch nach einem Neustart der Box, bis du sie mit „Öffentliche Freigabe beenden“ abschaltest.\n• Der Gerätename steht in öffentlichen Zertifikatslisten und wird von Suchprogrammen gefunden, die Anmeldeseite wird dann ausprobiert.\n\nWenn du nur von eigenen Geräten zugreifen willst, reicht die Freigabe im privaten Netz (Tailscale-App auf dem Gerät).';
  const SS_GROUPS = [['send', 'Senden (Bitrate, Verlust, Laufzeit)'], ['net', 'Sendewege und Netze'], ['cameras', 'Kameras'], ['cpu', 'Prozessor und Speicher'], ['temp', 'Temperaturen'], ['alerts', 'Meldungen'], ['cores', 'Prozessor-Details (Kerne, NPU, GPU)'], ['disk', 'Speicherplatz']];
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* privat */ } return null; };
  const openLink = (href, text) => h('a', { class: 'btn', href, target: '_blank', rel: 'noopener noreferrer', 'data-size': 'sm' }, text);

  const T = Neu.slots.netz, box = h('div', { class: 'stack', style: '--gap:var(--space-5)' }), msg = note();
  T.add('n-fern', 'Fernzugriff', box);
  const refresh = poll('remote', '/api/remote', 6000, () => App.view === 'netz'); App.data['remote:refresh'] = refresh;
  const ssRefresh = poll('statsshare', '/api/statsshare', 8000, () => App.view === 'netz');
  let declined = store('rDeclined') === '1', sig = '', ssSig = '';
  const act = async (action, extra) => { try { await api('POST', '/api/remote', Object.assign({ action, confirm: true }, extra || {})); msg.set('Wird ausgeführt …'); } catch (x) { msg.set(x.message, 'bad'); } setTimeout(refresh, 1200); };
  const confirmAct = (action, title, text, ok, extra) => async () => { if (await ask({ title, text, ok })) act(action, extra); };

  function renderRemote() {
    const d = App.data.remote; if (!d) return; const sg = JSON.stringify([d, declined]); if (sg === sig) return; sig = sg;
    const busy = d.state === 'working', off = busy || !d.helper_installed, kids = [], body = [];
    const stateBadge = !d.installed ? badge(declined ? 'nicht eingerichtet' : 'nicht installiert') : busy ? badge('läuft …', 'accent') : badge((R_STATE[d.backend] || d.backend || '–') + (d.connected && d.serve ? ', freigegeben' : ''), d.connected && d.serve ? 'ok' : 'warn');
    body.push(h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, 'Erreiche die Oberfläche von unterwegs, ', h('b', null, 'nur über dein privates Tailscale-Netz'), ', nie öffentlich im Internet. Jedes Gerät, das zugreifen soll, braucht die Tailscale-App und dein Konto.'));
    if (!d.helper_installed) body.push(alertBox({ tone: 'warn', text: 'Der Fernzugriff-Helfer ist nicht installiert. Bitte install.sh einmal erneut ausführen.' }));
    if (!d.installed) {
      if (declined) body.push(btn('Doch einrichten', { onclick: () => { declined = false; store('rDeclined', null); sig = ''; renderRemote(); } }));
      else body.push(alertBox({ tone: 'info', title: 'Möchtest du den Fernzugriff einrichten?', text: 'Das ist freiwillig. Ohne Fernzugriff funktioniert alles wie bisher im lokalen Netz. Es wird nur etwas installiert, wenn du „Ja“ wählst.' }),
        h('div', { class: 'cluster' }, btn('Ja, einrichten', { variant: 'primary', disabled: off, onclick: confirmAct('install', 'Tailscale installieren?', 'Tailscale wird aus der offiziellen Paketquelle installiert. Das lädt ein kleines Programm herunter. Es ändert nichts am BELABOX-System.', 'Installieren') }),
          btn('Nein, nicht einrichten', { onclick: () => { declined = true; store('rDeclined', '1'); sig = ''; renderRemote(); } })));
    } else {
      if (d.connected) body.push(h('div', { class: 'grid-auto', style: '--min:12rem;--gap:var(--space-3)' }, ...[['Gerätename', d.name], ['Adresse im Tailscale-Netz', d.ip], ['Tailscale-Netz', d.tailnet]].filter(x => x[1]).map(([k, v]) => h('div', { class: 'tile' }, h('div', { class: 'stat' }, h('span', { class: 'stat__label', text: k }), h('span', { class: 'num', style: 'word-break:break-all', text: v }))))));
      const login = d.login_url && TS.test(d.login_url);
      if (login) body.push(alertBox({ tone: 'info', title: 'Bei Tailscale anmelden', text: 'Der Link öffnet Tailscale. Melde dich mit deinem Konto an (zum Beispiel GitHub oder Google) und klicke dort auf „Connect“. Danach hier einen Moment warten.', actions: [h('a', { class: 'btn', href: d.login_url, target: '_blank', rel: 'noopener noreferrer', 'data-size': 'sm', 'data-variant': 'primary' }, 'Bei Tailscale anmelden')] }));
      if (d.installed && d.connected && !d.serve) body.push(alertBox({ tone: 'warn', title: 'Für die Freigabe muss HTTPS im Tailscale-Konto einmal freigeschaltet sein.', text: 'Dort „MagicDNS“ und „HTTPS Certificates“ einschalten, danach hier „Oberfläche im privaten Netz freigeben“ drücken.', actions: [openLink(d.hint_url && TS.test(d.hint_url) ? d.hint_url : 'https://login.tailscale.com/admin/dns', 'Tailscale-Einstellungen öffnen')] }));
      if (d.connected && d.serve && d.url) body.push(field({ label: 'Adresse im privaten Netz', control: h('div', { class: 'cluster', style: 'flex-wrap:nowrap' }, h('a', { class: 'grow truncate', href: d.url, target: '_blank', rel: 'noopener noreferrer', text: d.url }), copyBtn(d.url, 'Kopieren')) }));
      if (d.funnel) body.push(alertBox({ tone: 'danger', title: 'Öffentlich erreichbar (Funnel)', text: 'Die Oberfläche ist öffentlich im Internet erreichbar. Das bleibt so, auch nach einem Neustart der Box, bis du die Freigabe mit „Öffentliche Freigabe beenden“ abschaltest.' }));
      const acts = [];
      if (!d.connected) acts.push(btn(login ? 'Neuen Anmeldelink holen' : 'Verbinden', { variant: 'primary', disabled: off, onclick: () => act('login') }));
      else {
        acts.push(d.serve ? btn('Freigabe beenden', { disabled: off, onclick: () => act('serve_off') }) : btn('Oberfläche im privaten Netz freigeben', { variant: 'primary', disabled: off, onclick: () => act('serve_on') }));
        acts.push(d.funnel ? btn('Öffentliche Freigabe beenden', { variant: 'danger', disabled: off, onclick: () => act('funnel_off') }) : btn('Öffentlich im Internet freigeben (Funnel) …', { disabled: off, onclick: confirmAct('funnel_on', 'Öffentlich freigeben?', FUNNEL_ASK, 'Öffentlich freigeben', { public: true }) }));
        acts.push(btn('Verbindung trennen', { variant: 'ghost', disabled: off, onclick: confirmAct('down', 'Verbindung trennen?', 'Verbindung zu Tailscale trennen? Der Fernzugriff geht dann nicht mehr.', 'Trennen') }));
        acts.push(btn('Vom Konto abmelden', { variant: 'ghost', disabled: off, onclick: confirmAct('logout', 'Vom Konto abmelden?', 'Diese Box aus deinem Tailscale-Konto abmelden? Danach musst du dich neu anmelden.', 'Abmelden') }));
      }
      body.push(h('div', { class: 'cluster' }, acts));
      let m = d.message || ''; if (!d.helper_installed) m = 'Der Fernzugriff-Helfer ist nicht installiert. Bitte install.sh einmal erneut ausführen.'; else if (busy) m = (d.step || 'Arbeite') + ' …'; body.push(h('p', { class: 'muted', style: 'font-size:var(--text-sm);' + (d.state === 'failed' ? 'color:var(--danger)' : ''), text: m }));
      if (d.hint_url && TS.test(d.hint_url) && !(d.installed && d.connected && !d.serve)) body.push(openLink(d.hint_url, 'Tailscale-Seite öffnen'));
    }
    kids.push(card({ title: 'Fernzugriff (Tailscale)', actions: [stateBadge], body }));
    kids.push(card({ title: 'Tailscale-App für deine Geräte', sub: 'Offizielle Stores', body: [h('div', { class: 'cluster' }, ...[['Android (Play Store)', 'https://play.google.com/store/apps/details?id=com.tailscale.ipn'], ['iPhone/iPad (App Store)', 'https://apps.apple.com/app/tailscale/id1470499037'], ['Mac', 'https://tailscale.com/download/mac'], ['Windows', 'https://tailscale.com/download/windows'], ['Linux', 'https://tailscale.com/download/linux']].map(([t, u]) => openLink(u, t))),
      disclosure('Anleitung für Handy und Rechner', h('ol', { style: 'padding-left:var(--space-5);display:grid;gap:var(--space-2);font-size:var(--text-sm)' }, ...[['Konto:', 'Auf tailscale.com kostenlos anmelden (zum Beispiel mit GitHub, Google, Microsoft oder Apple).'], ['Box:', 'Hier „Ja, einrichten“, dann „Verbinden“, den Link öffnen, anmelden, „Connect“ klicken.'], ['Freigabe:', '„Oberfläche im privaten Netz freigeben“. Falls Tailscale nach „HTTPS“ fragt: den Link öffnen, „Funnel“ aus lassen, „Enable HTTPS“ klicken, dann erneut freigeben.'], ['Handy und Rechner:', 'Die App „Tailscale“ installieren, mit demselben Konto anmelden, Verbindung einschalten.'], ['Zugriff:', 'Die Adresse oben im Browser öffnen, mit dem BELABOX-Passwort anmelden. Es ist nichts am Router freizugeben.']].map(([a, b]) => h('li', null, h('b', null, a), ' ', b))))] }));
    ssHost = h('div'); kids.push(ssHost); ssSig = ''; renderStats();
    box.replaceChildren(...kids);
  }
  let ssHost = h('div');
  /* Hintergrund der Statistik-Seite: wird als &bg=… an die Adresse gehängt (und in die heruntergeladene Datei geschrieben); die Seite wählt die Schriftfarben passend dazu */
  const BGS = [['transparent', 'Transparent (für OBS)'], ['dark', 'Dunkel'], ['light', 'Hell'], ['custom', 'Eigene Farbe']];
  let bgMode = store('pb_ss_bgmode') || 'transparent', bgColor = store('pb_ss_bgcolor') || '#1a2233';
  if (!/^#[0-9a-f]{6}$/i.test(bgColor)) bgColor = '#1a2233';
  const bgHex = () => bgMode === 'dark' ? '0b1220' : bgMode === 'light' ? 'f2f5fa' : bgMode === 'custom' ? bgColor.slice(1) : '';
  const styled = url => { const x = bgHex(); return !url || !x ? url : url + (url.indexOf('?') >= 0 ? '&' : '?') + 'bg=' + x; };

  /* ---------- Statistik öffentlich ---------- */
  const ssPost = async body => { try { App.set('statsshare', await api('POST', '/api/statsshare', body)); setTimeout(refresh, 1500); } catch (x) { toast(x.message); ssRefresh(); } };
  function renderStats() {
    const d = App.data.statsshare; if (!d) return; const sg = JSON.stringify(d); if (sg === ssSig && ssHost.firstChild) return; if (ssHost.contains(document.activeElement) && document.activeElement.tagName === 'INPUT' && document.activeElement.type !== 'checkbox') return; ssSig = sg;
    const kids = [];
    kids.push(h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, 'Gibt ', h('b', null, 'nur die Statistik'), ' (Sendewege, Bitrate, Temperatur, Kameras …) über eine eigene öffentliche Adresse frei, zum Beispiel für eine Browserquelle in OBS oder eine Homepage. Die Oberfläche der Box bleibt davon unberührt und kann privat bleiben. Der Zugriff geht nur mit einem geheimen Schlüssel in der Adresse, höchstens 3 Betrachter gleichzeitig. Die Adresse bleibt fest, auch wenn die Box ihr Netz wechselt.'));
    if (d.installed && !d.connected) kids.push(alertBox({ tone: 'warn', text: 'Zuerst oben mit Tailscale verbinden.' }));
    kids.push(sw({ checked: !!d.enabled, label: 'Statistik öffentlich freigeben', disabled: !d.installed || !d.connected || d.busy, s: 'Statistik öffentlich', k: 'obs homepage statistik öffentlich freigeben', onchange: async (on, el) => {
      if (on && !await ask({ title: 'Statistik öffentlich freigeben?', text: 'Danach kann JEDER, der die Adresse mit dem Schlüssel kennt, die freigegebenen Werte lesen (höchstens 3 gleichzeitig), auch ohne Tailscale. Die Oberfläche der Box bleibt davon unberührt.\n\nDie Freigabe bleibt auch nach einem Neustart der Box, bis du sie wieder ausschaltest.', ok: 'Freigeben' })) { el.checked = false; return; } ssPost({ enabled: on }); } }));
    if (d.enabled) {
      kids.push(h('p', { style: 'font-weight:var(--weight-medium)', text: 'Was darf nach außen?' }), h('div', { class: 'grid-auto', style: '--min:16rem' }, SS_GROUPS.map(([k, t]) => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: (d.groups || {})[k] !== false, onchange: e => ssPost({ groups: { [k]: e.target.checked } }) }), h('span', { class: 'check__text', text: t })))));
      kids.push(h('p', { style: 'font-weight:var(--weight-medium)', text: 'Aussehen der Seite' }),
        h('div', { class: 'cluster', style: 'align-items:flex-end' }, field({ label: 'Hintergrund', control: Neu.select(BGS, bgMode, v => { bgMode = v; store('pb_ss_bgmode', v); ssSig = ''; renderStats(); }) }),
          bgMode === 'custom' ? field({ label: 'Farbe', control: h('input', { type: 'color', value: bgColor, class: 'input', style: 'padding:3px;width:4.5rem', 'aria-label': 'Hintergrundfarbe der Statistik-Seite', oninput: e => { bgColor = e.target.value; store('pb_ss_bgcolor', bgColor); }, onchange: e => { e.target.blur(); ssSig = ''; renderStats(); } }) }) : null),
        h('p', { class: 'field__hint', text: 'Die Schriftfarben passen sich dem Hintergrund an: hell auf dunklem, dunkel auf hellem Grund. „Transparent“ ist für OBS gedacht, dort scheint dein Bild durch die Glas-Karten.' }));
      kids.push(field({ label: 'Adresse der Seite (zum Beispiel für die Browserquelle in OBS)', control: input({ value: styled(d.page_url) || '', readOnly: true, class: 'input mono', 'aria-label': 'Adresse der Statistik-Seite mit Schlüssel' }) }),
        field({ label: 'WebSocket-Adresse (für die eigene statistik.html oder andere Programme)', control: input({ value: d.ws_url || '', readOnly: true, class: 'input mono', 'aria-label': 'WebSocket-Adresse der Statistik mit Schlüssel' }) }),
        h('div', { class: 'cluster' }, d.page_url ? copyBtn(() => styled(d.page_url), 'Adresse der Seite kopieren') : null, d.ws_url ? copyBtn(d.ws_url, 'WebSocket-Adresse kopieren') : null,
          btn('statistik.html herunterladen', { size: 'sm', disabled: !d.ws_url, run: async () => { const r = await fetch('/api/statsshare/widget'); if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || 'Fehler'); } let txt = await r.text(); const bx = bgHex(); if (bx) txt = txt.replace('background:"transparent"', 'background:"#' + bx + '"'); const a = h('a', { href: URL.createObjectURL(new Blob([txt], { type: 'text/html' })), download: 'statistik.html' }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000); } }),
          btn('Neuen Schlüssel erzeugen', { size: 'sm', onclick: async () => { if (await ask({ title: 'Neuen Schlüssel erzeugen?', text: 'Die bisherige Adresse und schon heruntergeladene Dateien funktionieren danach nicht mehr.', ok: 'Erzeugen' })) ssPost({ new_token: true }); } })),
        d.public ? h('p', { class: 'muted', style: 'font-size:var(--text-sm)', text: `Betrachter jetzt: ${d.clients} von 3` }) : null,
        h('p', { class: 'field__hint', text: 'Wer die Adresse kennt, sieht diese Werte. Gib sie nicht weiter und stelle die Datei nicht öffentlich ins Netz: Auf einer Homepage sehen Besucher die Adresse der Box und den Schlüssel.' }));
    }
    let m = ''; if (d.busy) m = 'Wird ausgeführt …'; else if (d.enabled && !d.public) m = d.message || 'Die Freigabe ist noch nicht aktiv.'; else if (d.state === 'needs_funnel' && d.message) m = d.message;
    if (m) kids.push(h('p', { class: 'muted', style: 'font-size:var(--text-sm)' }, m, d.hint_url && TS.test(d.hint_url) && d.state === 'needs_funnel' ? [' ', h('a', { href: d.hint_url, target: '_blank', rel: 'noopener noreferrer' }, 'Tailscale-Seite öffnen')] : null));
    ssHost.replaceChildren(card({ title: 'Statistik öffentlich (für OBS und Homepage)', body: kids }));
  }
  App.on('remote', renderRemote); App.on('statsshare', renderStats);
})();
