/* Hilfetexte ein- und ausblenden.
   Standard: am Computer an, am Handy aus. Alle zusammen über den Knopf in der Kopfzeile, das Menü „⋯“ oder System > Box,
   einzeln über das (i) in der Überschrift einer Karte. Erkannt werden die Beschreibung unter dem Kartentitel, die Hinweise unter Feldern
   und längere erklärende Absätze; Statusmeldungen bleiben immer sichtbar. */
(() => {
  'use strict';
  const { $, $$ } = Neu;
  const views = $('#views'), mq = window.matchMedia('(max-width: 52rem)');
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* privat */ } return null; };
  let mode = store('pb_neu_help'); if (mode !== 'on' && mode !== 'off') mode = null;          // null = automatisch (Computer an, Handy aus)
  let cards = {}; try { cards = JSON.parse(store('pb_neu_help_cards') || '{}') || {}; } catch (e) { cards = {}; }
  const globalOn = () => mode === 'on' ? true : mode === 'off' ? false : !mq.matches;
  const SEL = '.card__sub, .field__hint, .help';
  const isHelp = el => el.matches(SEL) || (el.tagName === 'P' && el.classList.contains('muted') && !el.hasAttribute('role') && el.textContent.trim().length >= 80 && !el.closest('.alert, .empty, .notes-md'));
  const keyOf = card => { const v = card.closest('[data-view]'); return (v ? v.dataset.view : '') + '|' + (((card.querySelector('.card__title') || {}).textContent) || '').trim(); };
  const resolve = el => { const c = el.closest('.card'); if (c) { const o = cards[keyOf(c)]; if (o) return o === 'on'; } return globalOn(); };
  const paint = el => el.classList.toggle('help-off', !resolve(el));

  function decorate(card) {
    const head = card.querySelector(':scope > .card__head'); if (!head) return;
    const first = card.querySelector('[data-help]'); let b = head.querySelector(':scope > .help-btn');
    if (!first) { if (b) b.remove(); return; }
    if (!b) { b = Neu.iconBtn('info', 'Hilfetext ein- oder ausblenden', () => toggleCard(card)); b.classList.add('help-btn'); head.append(b); }
    b.setAttribute('aria-pressed', String(resolve(first)));
  }
  function toggleCard(card) {
    const first = card.querySelector('[data-help]'); if (!first) return;
    cards[keyOf(card)] = resolve(first) ? 'off' : 'on'; store('pb_neu_help_cards', JSON.stringify(cards)); scan();
  }
  function sync() {
    const on = globalOn();
    $$('input[data-helpsw]').forEach(i => { i.checked = on; });
    $$('[data-helpbtn]').forEach(b => b.setAttribute('aria-pressed', String(on)));
  }
  function scan() {
    $$(SEL + ', p.muted', views).forEach(el => { if (el.hasAttribute('data-help')) { paint(el); return; } if (isHelp(el)) { el.setAttribute('data-help', ''); paint(el); } });
    $$('.card', views).forEach(decorate); sync();
  }
  let queued = false;
  new MutationObserver(() => { if (!queued) { queued = true; setTimeout(() => { queued = false; scan(); }, 40); } }).observe(views, { childList: true, subtree: true });
  mq.addEventListener('change', scan);
  document.addEventListener('neu:view', () => setTimeout(scan, 40));

  Neu.help = {
    on: globalOn,
    set(v) { mode = v ? 'on' : 'off'; cards = {}; store('pb_neu_help', mode); store('pb_neu_help_cards', null); scan(); },
    toggle() { this.set(!globalOn()); },
  };
  const hb = $('#help-btn'); if (hb) { hb.dataset.helpbtn = ''; hb.addEventListener('click', () => Neu.help.toggle()); }
  scan();
})();
