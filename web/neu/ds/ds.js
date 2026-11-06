/* IRL4YOU BOX · Designsystem · Verhalten (ohne Abhängigkeiten, ~150 Zeilen)
   Enthält: Symbolsatz, Hell/Dunkel, Tabs, Ansichtswechsel, Dialoge, Menüs, Toasts, Regler.
   Alle Funktionen hängen an data-Attributen; die Seiten brauchen kein eigenes Skript. */
(() => {
  const ICONS = {
    live: '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/>',
    camera: '<path d="M3 8a2 2 0 0 1 2-2h2l1.5-2h7L17 6h2a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="3.5"/>',
    layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><rect x="12" y="12" width="6" height="5" rx="1"/>',
    wifi: '<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1"/>',
    ethernet: '<rect x="4" y="4" width="16" height="10" rx="2"/><path d="M8 14v4M12 14v4M16 14v4M6 18h12"/>',
    chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    settings: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h10M18 18h2"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
    more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    right: '<path d="m9 6 6 6-6 6"/>', left: '<path d="m15 6-6 6 6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    warning: '<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
    battery: '<rect x="3" y="8" width="16" height="9" rx="2"/><path d="M21 11v3"/>',
    plug: '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.3L4 8.5M4 4v4.5h4.5M4 13a8 8 0 0 0 14.3 4.3L20 15.5M20 20v-4.5h-4.5"/>',
    trash: '<path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>', download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    thermo: '<path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z"/>',
    power: '<path d="M12 3v9M6.3 6.8a8 8 0 1 0 11.4 0"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3A4 4 0 0 0 13 5.3l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"/>',
    shield: '<path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6z"/>',
    update: '<path d="M12 3v11M8 10l4 4 4-4M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2"/>',
    file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10h14V9M10 13h4"/>',
    gamepad: '<rect x="3" y="8" width="18" height="10" rx="5"/><path d="M8 11v4M6 13h4M15 12h.01M17.5 14h.01"/>',
    bluetooth: '<path d="m7 7 10 10-5 4V3l5 4L7 17"/>',
    monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    bell: '<path d="M6 9a6 6 0 0 1 12 0c0 6 2 7 2 7H4s2-1 2-7zM10 20a2 2 0 0 0 4 0"/>',
    activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17v.01"/>',
    home: '<path d="M4 11 12 4l8 7v9h-5v-6H9v6H4z"/>',
    grip: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    tv: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="m8 3 4 3 4-3"/>',
    pencil: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    up: '<path d="m6 15 6-6 6 6"/>',
    micoff: '<path d="M3 3l18 18"/><path d="M9 5.5V6a3 3 0 0 1 5.5-1.7M15 10v1a3 3 0 0 1-4.6 2.5M5 11a7 7 0 0 0 11 5.7M19 11a7 7 0 0 1-.6 2.8M12 18v3"/>',
    speaker: '<path d="M4 9.5v5h4l5 4v-13l-5 4z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/>',
    eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4M6.5 6.6C3.7 8.4 2 12 2 12s3.5 7 10 7c1.6 0 3-.4 4.3-1M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'
  };
  const sprite = '<svg xmlns="http://www.w3.org/2000/svg" style="position:absolute;width:0;height:0" aria-hidden="true">' +
    Object.entries(ICONS).map(([k, v]) => `<symbol id="i-${k}" viewBox="0 0 24 24">${v}</symbol>`).join('') + '</svg>';
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* privat */ } } };

  /* ---- Hell/Dunkel: [data-theme-toggle] wechselt Hell → Dunkel → System ---- */
  const applyTheme = (t) => { if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; };
  applyTheme(store.get('ds_theme'));

  /* ---- Toast ---------------------------------------------------------------- */
  function toast(text) {
    let box = $('.toasts');
    if (!box) { box = document.createElement('div'); box.className = 'toasts'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.append(box); }
    const t = document.createElement('div'); t.className = 'toast'; t.textContent = text; box.append(t);
    setTimeout(() => t.remove(), 3200);
  }

  /* ---- Ansichtswechsel (Seitenleiste/Tableiste): <a data-view-link="live"> + <section data-view="live"> ---- */
  function views() {
    const links = $$('[data-view-link]'), panels = $$('[data-view]');
    if (!links.length) return;
    const show = (name, push) => {
      if (!panels.some(p => p.dataset.view === name)) name = panels[0].dataset.view;
      panels.forEach(p => p.hidden = p.dataset.view !== name);
      links.forEach(l => { const on = l.dataset.viewLink === name; l.toggleAttribute('aria-current', on); if (on) l.setAttribute('aria-current', 'page'); });
      if (push) history.replaceState(null, '', '#' + name);
      window.scrollTo({ top: 0 });
      document.dispatchEvent(new CustomEvent('ds:view', { detail: name }));
    };
    document.addEventListener('click', e => { const l = e.target.closest('[data-view-link]'); if (l) { e.preventDefault(); show(l.dataset.viewLink, true); } });
    window.addEventListener('hashchange', () => show(location.hash.slice(1), false));
    show(location.hash.slice(1), false);
  }

  /* ---- Tabs (innerhalb einer Ansicht) -------------------------------------- */
  function tabs() {
    $$('[role="tablist"]').forEach(list => {
      const tabs = $$('[role="tab"]', list);
      const select = (tab, focus) => {
        tabs.forEach(t => { const on = t === tab; t.setAttribute('aria-selected', on); t.tabIndex = on ? 0 : -1; const p = document.getElementById(t.getAttribute('aria-controls')); if (p) p.hidden = !on; });
        if (focus) tab.focus();
      };
      tabs.forEach(t => t.addEventListener('click', () => select(t)));
      list.addEventListener('keydown', e => {
        const i = tabs.indexOf(document.activeElement); if (i < 0) return;
        const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
        if (to !== undefined) { e.preventDefault(); select(tabs[(to + tabs.length) % tabs.length], true); }
      });
      select(tabs.find(t => t.getAttribute('aria-selected') === 'true') || tabs[0]);
    });
  }

  /* ---- Regler: setzt --fill und die Anzeige [data-range-out="id"] -------------- */
  function ranges() {
    const sync = i => {
      const min = +i.min || 0, max = +i.max || 100;
      i.style.setProperty('--fill', ((i.value - min) / (max - min) * 100) + '%');
      const out = i.id && $(`[data-range-out="${i.id}"]`); if (out) out.textContent = i.value + (i.dataset.unit || '');
    };
    $$('.range input[type="range"]').forEach(i => { sync(i); i.addEventListener('input', () => sync(i)); });
  }

  /* ---- Klicks: Dialoge, Menüs, Toasts, Theme, Demo-Aktionen -------------------- */
  document.addEventListener('click', e => {
    const t = e.target;
    const open = t.closest('[data-open]');
    if (open) { const d = document.getElementById(open.dataset.open); if (d) d.showModal(); return; }
    if (t.closest('[data-close]')) { t.closest('dialog')?.close(); return; }
    if (t.tagName === 'DIALOG') { t.close(); return; }                       /* Klick auf den Hintergrund */
    const ts = t.closest('[data-theme-set]');
    if (ts) { const v = ts.dataset.themeSet; applyTheme(v); store.set('ds_theme', v === 'system' ? '' : v); return; }
    const tl = t.closest('[data-tab-link]');
    if (tl) { document.querySelector(`[role="tab"][aria-controls="${tl.dataset.tabLink}"]`)?.click(); return; }
    const th = t.closest('[data-theme-toggle]');
    if (th) {
      const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = cur === 'dark' ? 'light' : 'dark'; applyTheme(next); store.set('ds_theme', next); return;
    }
    const mb = t.closest('[data-menu]');
    $$('.menu[data-open-menu]').forEach(m => { if (!mb || m.id !== mb.dataset.menu) { m.hidden = true; m.removeAttribute('data-open-menu'); } });
    if (mb) { const m = document.getElementById(mb.dataset.menu); const show = m.hidden; m.hidden = !show; m.toggleAttribute('data-open-menu', show); mb.setAttribute('aria-expanded', show); return; }
    const tm = t.closest('[data-toast]'); if (tm) toast(tm.dataset.toast);
    const busy = t.closest('[data-demo-busy]');
    if (busy) { busy.setAttribute('aria-busy', 'true'); setTimeout(() => { busy.removeAttribute('aria-busy'); toast(busy.dataset.demoBusy); }, 1200); }
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $$('.menu[data-open-menu]').forEach(m => { m.hidden = true; m.removeAttribute('data-open-menu'); }); });

  document.addEventListener('DOMContentLoaded', () => {
    document.body.insertAdjacentHTML('afterbegin', sprite);
    views(); tabs(); ranges();
  });
  window.ds = { toast, applyTheme };
})();
