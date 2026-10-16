/* Übersetzung der Oberfläche (Issue #24).
   Die Texte im Quelltext sind deutsch und gleichzeitig die Schlüssel der Übersetzungsdateien (web/i18n/<Sprache>.json). Die Seite wird zur Laufzeit übersetzt:
   Textknoten, title, placeholder, aria-label und alt sowie alert/confirm. Texte ohne Eintrag bleiben deutsch. Neue Sprache: web/i18n/README.md. */
(function (root) {
  "use strict";
  var LS_KEY = "pb_lang", SOURCE = "de", DEFAULT = "en";
  var ATTRS = ["title", "placeholder", "aria-label", "alt"];
  var LOCALES = {en: "en-GB", de: "de-DE", fr: "fr-FR", es: "es-ES", th: "th-TH-u-ca-gregory-nu-latn", pt: "pt-BR", ja: "ja-JP", it: "it-IT", pl: "pl-PL",
                 nl: "nl-NL", ko: "ko-KR", zh: "zh-CN", tr: "tr-TR", ru: "ru-RU"};
  var S = {lang: SOURCE, dicts: [], langs: [{code: "en", name: "English", label: "EN"}, {code: "de", name: "Deutsch", label: "DE"}],
           cache: new Map(), text: new WeakMap(), attr: new WeakMap(), obs: null, listeners: []};

  function norm(s) { return String(s).replace(/\s+/g, " ").trim(); }
  function hasLetters(s) { return /[A-Za-zÄÖÜäöüß฀-๿]{2}/.test(s); }
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  /* ---- Wörterbuch: feste Texte und Texte mit {1}, {2} ... (eingefügte Werte) */
  var SEG = "(?:(?!\\.\\s+[A-ZÄÖÜ„]).)";                             // ein Wert darf nicht über eine Satzgrenze reichen (". Neuer Satz": dann gilt jeder Satz für sich)
  function compile(d) {
    var exact = Object.create(null), rules = [];
    var ex = d && d.exact || {};
    Object.keys(ex).forEach(function (k) {
      var v = ex[k];
      if (typeof v !== "string" || !v) return;
      var order = [], re;
      if (/\{\d+\}/.test(k)) {
        var parts = k.split(/(\{\d+\})/), src = "";
        for (var j = 0; j < parts.length; j++) {
          var ph = /^\{(\d+)\}$/.exec(parts[j]);
          if (ph) {
            order.push(+ph[1]);
            var adj = (parts[j - 1] === "" && j > 1) || (parts[j + 1] === "" && j + 1 < parts.length - 1);       // neben einem anderen Platzhalter: darf leer sein
            src += adj ? "(" + SEG + "*?)" : "(" + SEG + "+?)";
          } else {
            src += esc(parts[j]);
          }
        }
        try { re = new RegExp("^" + src + "$"); } catch (e) { return; }
        rules.push({re: re, order: order, out: v, len: k.replace(/\{\d+\}/g, "").length});
      } else {
        exact[k] = v;
      }
    });
    (d && d.patterns || []).forEach(function (p) {
      try { rules.push({re: new RegExp(p[0]), explicit: true, out: p[1], len: p[0].length}); } catch (e) { /* kaputtes Muster ignorieren */ }
    });
    rules.sort(function (a, b) { return b.len - a.len; });
    return {exact: exact, rules: rules};
  }

  function build(rule, m, depth) {
    if (rule.explicit) {
      return rule.out.replace(/\$(t?)(\d)/g, function (_, t, n) { var g = m[+n]; return g === undefined ? "" : (t ? (lookup(g, depth + 1) || g) : g); });
    }
    return rule.out.replace(/\{(\d+)\}/g, function (_, n) {
      var idx = rule.order.indexOf(+n);
      var g = idx >= 0 ? m[idx + 1] : "";
      return g === undefined ? "" : (hasLetters(g) ? (lookup(g, depth + 1) || g) : g);
    });
  }

  function lookupIn(dict, key, depth) {
    var v = dict.exact[key];
    if (v !== undefined) return v;
    for (var i = 0; i < dict.rules.length; i++) {
      var m = dict.rules[i].re.exec(key);
      if (m) return build(dict.rules[i], m, depth);
    }
    return null;
  }


  function direct(key, depth) {
    for (var i = 0; i < S.dicts.length; i++) { var r = lookupIn(S.dicts[i], key, depth); if (r !== null) return r; }
    return null;
  }

  function structural(key, depth) {                                    // "Name: Wert", "Name (Wert)" und Satzzeichen am Rand: die Teile einzeln übersetzen
    var m = /^([^:]{1,80}?)\s*:\s+(.+)$/.exec(key), l, r;
    if (m) {
      l = direct(m[1], depth);
      var lc = l === null ? direct(m[1] + ":", depth) : null;
      if (l !== null || lc !== null) {
        r = lookup(m[2], depth + 1);
        return (l !== null ? l + ": " : lc + " ") + (r === null ? m[2] : r);
      }
    }
    m = /^(.{1,100}?) \((.+)\)$/.exec(key);
    if (m) {
      l = lookup(m[1], depth + 1);
      r = lookup(m[2], depth + 1);
      if (l !== null || r !== null) return (l === null ? m[1] : l) + " (" + (r === null ? m[2] : r) + ")";
    }
    if (key.indexOf("(") >= 0) {                                       // Klammern mitten im Text einzeln: "0.9.1 (älter), 0.9.0 (älter)"
      var anyP = false;
      var outP = key.replace(/\(([^()]{1,80})\)/g, function (all, inner) { var t = lookup(inner, depth + 1); if (t === null) return all; anyP = true; return "(" + t + ")"; });
      if (anyP) return outP;
    }
    m = /^([\s·,.;:()\u2013\u2014\-]*)(.*?)([\s·,.;:()\u2013\u2014\-]*)$/.exec(key);
    if (m && m[2] && (m[1] || m[3])) {
      r = lookup(m[2], depth + 1);
      if (r !== null) return m[1] + r + m[3];
    }
    if (key.indexOf(", ") > 0) {                                       // Aufzählung kurzer Begriffe: "Bitrate, Latenz"
      var items = key.split(", "), anyL = false;
      if (items.every(function (x) { return x.split(" ").length <= 4; })) {
        var outL = items.map(function (x) { var t = lookup(x, depth + 1); if (t !== null) anyL = true; return t === null ? x : t; });
        if (anyL) return outL.join(", ");
      }
    }
    return null;
  }

  function sentences(key) {                                            // Satzende: . ! ? und danach ein Großbuchstabe (Daten wie "2026. 10. 4." trennen nicht)
    var parts = [], last = 0, re = /[.!?]+\s+(?=[A-ZÄÖÜ„])/g, m;
    while ((m = re.exec(key)) !== null) { parts.push(key.slice(last, m.index + m[0].length)); last = m.index + m[0].length; }
    parts.push(key.slice(last));
    return parts;
  }

  function lookup(key, depth) {
    depth = depth || 0;
    if (depth > 3 || !key) return null;
    var i, r;
    for (i = 0; i < S.dicts.length; i++) { r = lookupIn(S.dicts[i], key, depth); if (r !== null) return r; }
    var lead = /^[\s·,.;:\u2013\u2014\-]+/.exec(key);                  // ". Bildrate: …" / "· Kamera nicht gefunden. …": der Rest als Ganzes
    if (lead && lead[0].length < key.length) {
      var rest = key.slice(lead[0].length);
      for (i = 0; i < S.dicts.length; i++) { r = lookupIn(S.dicts[i], rest, depth); if (r !== null) return lead[0] + r; }
    }
    var tail = /[.:…]$/.exec(key);                                    // "Gespeichert." / "Gespeichert" und "Name:" / "Name" (nur Treffer im Wörterbuch, keine Teilübersetzung)
    if (tail && key.length > 3) {
      var bare = key.slice(0, -1).replace(/\s+$/, "");
      for (i = 0; i < S.dicts.length; i++) { r = lookupIn(S.dicts[i], bare, depth); if (r !== null) return r + tail[0]; }
    }
    if (key.indexOf(" · ") > 0) {                                      // Aufzählungen mit Mittelpunkt
      var parts = key.split(" · "), any = false;
      var out = parts.map(function (p) { var t = lookup(p, depth + 1); if (t !== null) any = true; return t === null ? p : t; });
      if (any) return out.join(" · ");
    }
    var sent = sentences(key);                                         // mehrere Sätze: jeden einzeln
    if (sent && sent.length > 1) {
      var any2 = false;
      var out2 = sent.map(function (p) { var q = p.trim(), t = lookup(q, depth + 1); if (t !== null) any2 = true; return t === null ? q : t; });
      if (any2) return out2.join(" ");
    }
    return structural(key, depth);
  }

  function tr(s) {
    if (S.lang === SOURCE || typeof s !== "string" || !S.dicts.length) return s;
    var key = norm(s);
    if (!key || !hasLetters(key)) return s;
    var hit = S.cache.get(key);
    if (hit === undefined) {
      hit = lookup(key, 0);
      if (S.cache.size > 4000) S.cache.clear();
      S.cache.set(key, hit);
    }
    if (hit === null) return s;
    var lead = /^\s*/.exec(s)[0], trail = /\s*$/.exec(s)[0];
    return lead + hit + trail;
  }

  function trMulti(s) {                                                // Meldungsfenster: Zeile für Zeile, Aufzählungszeichen bleiben
    return String(s).split("\n").map(function (line) {
      var whole = tr(line);                                          // Zeile samt Aufzählungszeichen (so stehen manche Texte im Wörterbuch)
      if (whole !== line) return whole;
      var m = /^(\s*(?:[•\-*]\s+)?)(.*)$/.exec(line);
      return m[2] ? m[1] + tr(m[2]) : line;
    }).join("\n");
  }

  /* ---- Seite */
  var SKIP = {SCRIPT: 1, STYLE: 1, PRE: 1, CODE: 1, TEXTAREA: 1, NOSCRIPT: 1, SVG: 1};
  function skipped(el) {
    for (var e = el; e && e.nodeType === 1; e = e.parentNode) {
      if (SKIP[e.tagName] || (e.getAttribute && e.getAttribute("translate") === "no")) return true;
    }
    return false;
  }

  function doText(n) {
    if (n.nodeType !== 3 || !n.parentNode || skipped(n.parentNode)) return;
    var rec = S.text.get(n), cur = n.data;
    if (rec && cur === rec.out) return;                               // unverändert seit unserer Übersetzung
    if (S.lang === SOURCE) { if (rec) S.text.delete(n); return; }
    var out = tr(cur);
    if (out !== cur) { S.text.set(n, {src: cur, out: out}); n.data = out; }
    else if (rec) S.text.delete(n);
  }

  function doAttr(el, name) {
    if (!el.getAttribute || skipped(el)) return;
    var v = el.getAttribute(name);
    if (v === null) return;
    var m = S.attr.get(el) || {}, rec = m[name];
    if (rec && v === rec.out) return;
    if (S.lang === SOURCE) { if (rec) { delete m[name]; } return; }
    var out = tr(v);
    if (out !== v) { m[name] = {src: v, out: out}; S.attr.set(el, m); el.setAttribute(name, out); }
    else if (rec) { delete m[name]; }
  }

  function walk(rootNode) {
    if (!rootNode) return;
    if (rootNode.nodeType === 3) { doText(rootNode); return; }
    if (rootNode.nodeType !== 1 && rootNode.nodeType !== 9) return;
    var base = rootNode.nodeType === 9 ? rootNode.documentElement : rootNode;
    if (base.nodeType === 1) ATTRS.forEach(function (a) { doAttr(base, a); });
    var w = document.createTreeWalker(base, 1 | 4, null, false), n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) doText(n);
      else ATTRS.forEach(function (a) { doAttr(n, a); });
    }
  }

  function restoreAll() {                                              // zurück auf Deutsch: gemerkte Originale wieder einsetzen
    var w = document.createTreeWalker(document.documentElement, 1 | 4, null, false), n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) { var r = S.text.get(n); if (r && n.data === r.out) n.data = r.src; S.text.delete(n); }
      else { var m = S.attr.get(n); if (m) { Object.keys(m).forEach(function (a) { if (n.getAttribute(a) === m[a].out) n.setAttribute(a, m[a].src); }); S.attr.delete(n); } }
    }
  }

  function onMut(list) {
    list.forEach(function (m) {
      if (m.type === "childList") { for (var i = 0; i < m.addedNodes.length; i++) walk(m.addedNodes[i]); }
      else if (m.type === "characterData") doText(m.target);
      else if (m.type === "attributes") doAttr(m.target, m.attributeName);
    });
  }

  function startObserver() {
    if (typeof MutationObserver === "undefined" || S.obs) return;
    S.obs = new MutationObserver(onMut);
    S.obs.observe(document.documentElement, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS});
  }

  function fillSelects() {
    var sels = document.querySelectorAll("select[data-lang]");
    for (var i = 0; i < sels.length; i++) {
      var s = sels[i];
      s.innerHTML = "";
      S.langs.forEach(function (l) { var o = document.createElement("option"); o.value = l.code; o.textContent = l.label || l.code.toUpperCase(); o.title = l.name; s.appendChild(o); });
      s.value = S.lang;
    }
  }

  function fillButtons() {                                             // Karte "Language": ein Knopf je Sprache, in ihrer eigenen Schrift
    var boxes = document.querySelectorAll("[data-lang-buttons]");
    for (var i = 0; i < boxes.length; i++) {
      var box = boxes[i];
      box.innerHTML = "";
      S.langs.forEach(function (l) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "langbtn" + (l.code === S.lang ? " on" : "");
        b.setAttribute("data-code", l.code);
        b.setAttribute("aria-pressed", l.code === S.lang ? "true" : "false");
        b.textContent = l.name;
        box.appendChild(b);
      });
    }
  }

  var PICK_CSS = ".pbpick{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(2,6,23,.78);font-family:-apple-system,system-ui,sans-serif}" +
    ".pbpick>div{width:100%;max-width:520px;max-height:90vh;overflow:auto;background:var(--card,#1e293b);color:var(--text,#f8fafc);border:1px solid var(--line,#50647f);border-radius:14px;padding:20px}" +
    ".pbpick h2{margin:0 0 14px;font-size:17px;line-height:1.3;color:var(--accent,#06b6d4)}" +
    ".pbpick .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}" +
    ".pbpick button,.langbtn{font:inherit;font-size:15px;padding:10px 12px;border-radius:10px;border:1px solid var(--line,#50647f);background:var(--bg,#0f172a);color:var(--text,#f8fafc);cursor:pointer;text-align:center}" +
    ".pbpick button:hover,.langbtn:hover{border-color:var(--accent,#06b6d4)}.pbpick button.on,.langbtn.on{background:var(--bar,#06b6d4);border-color:var(--bar,#06b6d4);color:#0f172a;font-weight:600}" +
    ".langbtns{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}";

  function pickerStyle() {
    if (document.getElementById("pbpickcss")) return;
    var st = document.createElement("style");
    st.id = "pbpickcss";
    st.textContent = PICK_CSS;
    document.head.appendChild(st);
  }

  function pick(code) {
    var el = document.getElementById("pbpick");
    if (el) el.parentNode.removeChild(el);
    setLang(code || S.lang || DEFAULT, true);
  }

  function showPicker() {                                              // beim ersten Öffnen: die Sprache wählen (danach über den Kopf oder die Karte unten)
    if (document.getElementById("pbpick")) return;
    pickerStyle();
    var o = document.createElement("div");
    o.id = "pbpick";
    o.className = "pbpick";
    o.setAttribute("translate", "no");
    o.setAttribute("role", "dialog");
    o.setAttribute("aria-modal", "true");
    o.setAttribute("aria-label", "Language");
    var box = document.createElement("div");
    var h = document.createElement("h2");
    h.textContent = "Language · Sprache · Langue · Idioma · ภาษา · 言語";
    box.appendChild(h);
    var grid = document.createElement("div");
    grid.className = "grid";
    S.langs.forEach(function (l) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("data-code", l.code);
      b.textContent = l.name;
      if (l.code === S.lang) b.className = "on";
      grid.appendChild(b);
    });
    box.appendChild(grid);
    o.appendChild(box);
    o.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-code]") : null;
      if (b) pick(b.getAttribute("data-code"));
      else if (e.target === o) pick(S.lang);
    });
    o.addEventListener("keydown", function (e) { if (e.key === "Escape") pick(S.lang); });
    document.body.appendChild(o);
    var first = grid.querySelector("button.on") || grid.firstChild;
    if (first && first.focus) first.focus();
  }

  /* ---- Laden und Umschalten */
  function getJSON(url) {
    return fetch(url, {cache: "no-store"}).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  function loadChain(code, seen) {
    seen = seen || [];
    if (code === SOURCE || seen.indexOf(code) >= 0) return Promise.resolve([]);
    seen.push(code);
    return getJSON("/i18n/" + code + ".json").then(function (d) {
      var mine = compile(d);
      var fb = typeof d.fallback === "string" && /^[a-z]{2,3}$/.test(d.fallback) ? d.fallback : (code === DEFAULT ? "" : DEFAULT);
      return (fb ? loadChain(fb, seen) : Promise.resolve([])).then(function (rest) { return [mine].concat(rest); });
    }).catch(function () { return []; });
  }

  function setLang(code, save) {
    if (!/^[a-z]{2,3}$/.test(code)) code = DEFAULT;
    return loadChain(code).then(function (chain) {
      if (typeof document !== "undefined" && S.lang !== SOURCE) restoreAll();
      S.lang = code;
      S.dicts = chain;
      S.cache.clear();
      if (save) { try { localStorage.setItem(LS_KEY, code); } catch (e) { /* gesperrt */ } }
      if (typeof document !== "undefined") {
        document.documentElement.setAttribute("lang", code);
        if (code !== SOURCE) walk(document);
        fillSelects();
        fillButtons();
      }
      S.listeners.forEach(function (f) { try { f(code); } catch (e) { /* Zuhörer dürfen nichts kaputt machen */ } });
      return code;
    });
  }

  function stored() {
    try { var v = localStorage.getItem(LS_KEY); return /^[a-z]{2,3}$/.test(v || "") ? v : null; } catch (e) { return null; }
  }

  var api = {
    tr: tr, trMulti: trMulti, norm: norm, set: function (c) { return setLang(c, true); },
    lang: function () { return S.lang; }, languages: function () { return S.langs; },
    locale: function () { return LOCALES[S.lang] || LOCALES[DEFAULT]; }, onChange: function (f) { S.listeners.push(f); },
    walk: function (n) { walk(n || document); }, showPicker: function () { showPicker(); },
    _load: function (d, code) { S.dicts = [compile(d)]; S.lang = code || "en"; S.cache.clear(); },       // für Tests
    _compile: compile, _lookup: function (k) { return lookup(k, 0); }
  };
  root.PB_I18N = api;
  root.T = tr;

  if (typeof document === "undefined") return;

  /* Meldungsfenster übersetzen */
  ["alert", "confirm"].forEach(function (name) {
    var orig = root[name];
    if (typeof orig === "function") root[name] = function (m) { return orig.call(root, trMulti(m)); };
  });

  var want = stored() || DEFAULT;
  S.lang = want;                                                       // schon vor dem Laden gilt die gewünschte Sprache (Zahlen- und Datumsformat)
  if (want !== SOURCE) document.documentElement.style.visibility = "hidden";       // kein Aufblitzen von Deutsch, bis die Übersetzung da ist
  var shown = false;
  function show() { if (!shown) { shown = true; document.documentElement.style.visibility = ""; } }
  setTimeout(show, 2500);
  getJSON("/i18n/languages.json").then(function (l) { if (Array.isArray(l) && l.length) S.langs = l.filter(function (x) { return x && /^[a-z]{2,3}$/.test(x.code); }); }).catch(function () {})
    .then(function () { if (!S.langs.some(function (l) { return l.code === want; })) want = DEFAULT; return setLang(want, false); })
    .then(function () {
      var go = function () {
        if (S.lang !== SOURCE) walk(document);
        fillSelects(); fillButtons(); startObserver(); show();
        if (stored() === null) showPicker();                           // noch nie gewählt: Auswahl zeigen
      };
      if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go); else go();
    }, show);
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.matches && t.matches("select[data-lang]")) setLang(t.value, true);
  });
  document.addEventListener("click", function (e) {
    var b = e.target && e.target.closest ? e.target.closest(".langbtn[data-code]") : null;
    if (b) setLang(b.getAttribute("data-code"), true);
  });
})(typeof window !== "undefined" ? window : this);
