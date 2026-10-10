"""Tests für die Übersetzung der Oberfläche (Issue #24): Engine (web/i18n.js in JavaScriptCore), Übersetzungsdateien, Sprachliste, Server-Routen, Einbindung in die Seiten.
Die Übersetzungen selbst sind maschinell erstellt und nicht von Muttersprachlern geprüft; die Tests prüfen Form und Vollständigkeit, nicht den Wortlaut."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import i18n_extract  # noqa: E402
import server  # noqa: E402

WEB = os.path.join(ROOT, "web")
ENGINE = open(os.path.join(WEB, "i18n.js"), encoding="utf-8").read()
PAGE = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
LOGIN = open(os.path.join(WEB, "login.html"), encoding="utf-8").read()
JSC = next((p for p in (shutil.which("jsc"), "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc") if p and os.path.exists(p)), None)
PH = re.compile(r"\{\d+\}")


def load(code):
    return json.load(open(os.path.join(WEB, "i18n", code + ".json"), encoding="utf-8"))


REGISTRY = json.load(open(os.path.join(WEB, "i18n", "languages.json"), encoding="utf-8"))
CODES = [x["code"] for x in REGISTRY]
FILES = [c for c in CODES if c != "de"]
KEYS = None


def keys():
    global KEYS
    if KEYS is None:
        KEYS = i18n_extract.all_keys()
    return KEYS


def run_js(code):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code)
    try:
        r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout.strip()


class Registry(unittest.TestCase):
    def test_list_is_well_formed(self):
        self.assertEqual(len(CODES), len(set(CODES)))
        for x in REGISTRY:
            self.assertRegex(x["code"], r"^[a-z]{2,3}$")
            self.assertTrue(x["name"] and x["label"])
        self.assertIn("en", CODES)
        self.assertIn("de", CODES)

    def test_every_language_in_the_list_has_a_file_except_the_source_language(self):
        for c in FILES:
            self.assertTrue(os.path.isfile(os.path.join(WEB, "i18n", c + ".json")), c)

    def test_every_file_is_in_the_list(self):
        for f in os.listdir(os.path.join(WEB, "i18n")):
            if f.endswith(".json") and f != "languages.json":
                self.assertIn(f[:-5], CODES, f)

    def test_the_wanted_languages_are_there(self):
        for c in ("en", "de", "fr", "es", "pt", "it", "nl", "pl", "tr", "ru", "zh", "ja", "ko", "th"):
            self.assertIn(c, CODES)
        self.assertEqual(CODES[0], "en")                                                     # Englisch zuerst (Standard), Deutsch gleich danach
        self.assertEqual(CODES[1], "de")


class Dictionaries(unittest.TestCase):
    def test_form_of_every_file(self):
        for c in FILES:
            d = load(c)
            self.assertEqual(d["lang"], c)
            self.assertIsInstance(d["exact"], dict)
            self.assertIsInstance(d.get("patterns", []), list)
            self.assertIn(d.get("fallback", ""), ("", "en"))
            if c != "en":
                self.assertEqual(d.get("fallback"), "en")                                    # fehlende Texte: erst Englisch, dann Deutsch
            for k, v in d["exact"].items():
                self.assertIsInstance(k, str)
                self.assertIsInstance(v, str)
                self.assertEqual(k, " ".join(k.split()), k)                                  # Schlüssel sind schon bereinigt (Leerraum)

    def test_placeholders_match_the_german_text(self):
        for c in FILES:
            for k, v in load(c)["exact"].items():
                if v:
                    self.assertEqual(sorted(PH.findall(k)), sorted(PH.findall(v)), "%s: %r -> %r" % (c, k, v))

    def test_patterns_compile_in_javascript_style(self):
        for c in FILES:
            for pat, out in load(c).get("patterns", []):
                re.compile(pat)
                self.assertNotIn("(?<", pat)                                                 # kein Rückblick: ältere Safari-Versionen kennen ihn nicht
                self.assertNotIn("(?P<", pat)
                self.assertIsInstance(out, str)

    def test_english_is_complete(self):
        ex = load("en")["exact"]
        missing = [k for k in keys() if not ex.get(k)]
        self.assertEqual(missing, [], "Fehlende englische Texte (python3 tools/i18n_extract.py --keys): %d, z. B. %r" % (len(missing), missing[:5]))

    def test_the_other_languages_are_nearly_complete(self):
        # Neue Texte bekommen je Version nur Deutsch und Englisch; die anderen Sprachen werden am Schluss einmal komplett nachgezogen (fehlt ein Text, gilt Englisch).
        # (Nutzerwunsch 10. Okt 2026: nie auf Übersetzungen warten; die Schwelle bleibt locker, der Rückstand wird ein- bis zweimal am Tag gemeldet und in einem Rutsch nachgetragen.)
        for c in FILES:
            if c == "en":
                continue
            ex = load(c)["exact"]
            filled = sum(1 for k in keys() if ex.get(k))
            self.assertGreaterEqual(filled / len(keys()), 0.90, "%s: %d von %d" % (c, filled, len(keys())))

    def test_no_german_text_left_in_the_latin_script_languages(self):
        for c in FILES:
            if c in ("th", "ru", "zh", "ja", "ko"):
                continue
            pat = r"[äßÄ]" if c == "tr" else r"[äöüÄÖÜß]"                                    # Türkisch schreibt ö und ü selbst
            if c in ("pt", "it", "nl", "pl", "fr", "es", "en", "tr"):
                bad = [(k, v) for k, v in load(c)["exact"].items() if v and re.search(pat, v)]
                self.assertEqual(bad[:3], [], c)

    def test_other_scripts_are_really_used_where_the_german_has_words(self):
        scripts = {"th": r"[฀-๿]", "ru": r"[Ѐ-ӿ]", "zh": r"[一-鿿]", "ja": r"[぀-ヿ一-鿿]", "ko": r"[가-힣]"}
        for c, rx in scripts.items():
            ex = load(c)["exact"]
            words = [k for k, v in ex.items() if v and re.search(r"[a-zäöü]{4,} [a-zäöü]{3,}", k) and not re.search(rx, v)]
            self.assertLess(len(words), 0.05 * len(ex), "%s: %r" % (c, words[:5]))

    def test_the_source_texts_are_not_changed_by_a_translation_file(self):
        for c in FILES:
            self.assertNotIn("", [k for k in load(c)["exact"]])


@unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
class Engine(unittest.TestCase):
    def tr(self, lines, dictionary=None, lang="en"):
        d = dictionary if dictionary is not None else load("en")
        code = "var window = {}; var self = window; var navigator = {};\n" + ENGINE + "\nvar I = window.PB_I18N; I._load(%s, %s);\nvar out = [];\n" % (json.dumps(d), json.dumps(lang))
        for s in lines:
            code += "out.push(%s(%s));\n" % ("I.trMulti" if "\n" in s else "I.tr", json.dumps(s))
        code += "print(JSON.stringify(out));"
        return json.loads(run_js(code))

    def test_exact_text_whitespace_is_kept_and_unknown_text_is_left_alone(self):
        ex = load("en")["exact"]
        out = self.tr(["Kopieren", "  Kopieren  ", "Dieser Text steht in keinem Wörterbuch.", "45 %", "5.3 Mbit/s", "", "  "])
        self.assertEqual(out, [ex["Kopieren"], "  " + ex["Kopieren"] + "  ", "Dieser Text steht in keinem Wörterbuch.", "45 %", "5.3 Mbit/s", "", "  "])

    def test_german_as_source_language_is_the_identity(self):
        self.assertEqual(self.tr(["Kopieren"], lang="de"), ["Kopieren"])

    def test_values_in_the_text_are_carried_over_and_may_move(self):
        d = {"exact": {"Temperatur {1} °C": "Temperature {1} °C", "{1} von {2} senden": "{2} cameras, {1} are sending"}}
        self.assertEqual(self.tr(["Temperatur 55 °C", "2 von 3 senden", "Temperatur  1 °C"], d), ["Temperature 55 °C", "3 cameras, 2 are sending", "Temperature 1 °C"])

    def test_inserted_texts_are_translated_too_and_names_stay(self):
        ex = load("en")["exact"]
        d = {"exact": {"Fehler: {1}": "Error: {1}", "Kopieren": ex["Kopieren"], "Kamera {1} sendet nicht": "Camera {1} is not sending"}}
        self.assertEqual(self.tr(["Fehler: Kopieren", "Kamera Osmo Action sendet nicht"], d), ["Error: " + ex["Kopieren"], "Camera Osmo Action is not sending"])

    def test_full_stop_and_colon_variants_fall_back(self):
        d = {"exact": {"Gespeichert": "Saved", "Name": "Name", "Fertig …": "Done …"}}
        self.assertEqual(self.tr(["Gespeichert.", "Gespeichert:", "Gespeichert"], d), ["Saved.", "Saved:", "Saved"])

    def test_bullet_lists_and_sentences_are_split(self):
        d = {"exact": {"Gespeichert.": "Saved.", "Die Übertragung wird neu gestartet.": "The stream is restarted.", "Hallo": "Hello", "Welt": "World"}}
        self.assertEqual(self.tr(["Gespeichert. Die Übertragung wird neu gestartet.", "Hallo · Welt · Unbekannt"], d),
                         ["Saved. The stream is restarted.", "Hello · World · Unbekannt"])
        self.assertEqual(self.tr(["Hallo\n\n• Welt\n- Unbekannt"], d), ["Hello\n\n• World\n- Unbekannt"])

    def test_neighbouring_values_may_be_empty(self):
        d = {"exact": {"Akku {1} %{2}{3}": "Battery {1} %{2}{3}", ", am Ladekabel (lädt)": ", plugged in (charging)"}}
        self.assertEqual(self.tr(["Akku 82 %, am Ladekabel (lädt)", "Akku 18 %"], d), ["Battery 82 %, plugged in (charging)", "Battery 18 %"])

    def test_label_and_value_are_translated_separately(self):
        d = {"exact": {"Verbindung": "Connection", "Hauptverbindung": "Main connection", "Fehler:": "Error:", "Hauptbild": "Main picture"}}
        self.assertEqual(self.tr(["Verbindung: Hauptverbindung", "Fehler: Datei a.txt fehlt", "Hauptbild (Webcam)", "Verbindung: Unbekanntes Ding"], d),
                         ["Connection: Main connection", "Error: Datei a.txt fehlt", "Main picture (Webcam)", "Connection: Unbekanntes Ding"])

    def test_punctuation_at_the_edge_does_not_hide_a_known_text(self):
        d = {"exact": {"nicht verbunden": "not connected", "Kamera": "Camera"}}
        self.assertEqual(self.tr(["· nicht verbunden", "(Kamera)", ", Kamera –", "Völlig unbekannt ·"], d), ["· not connected", "(Camera)", ", Camera –", "Völlig unbekannt ·"])

    def test_explicit_patterns_and_broken_patterns(self):
        d = {"exact": {}, "patterns": [["^Server: (.+)$", "Server: $1"], ["^(\\d+) Kameras$", "$1 cameras"], ["([unclosed", "x"]]}
        self.assertEqual(self.tr(["Server: Redefined", "3 Kameras"], d), ["Server: Redefined", "3 cameras"])

    def test_more_specific_pattern_wins(self):
        d = {"exact": {"Fehler: {1}": "Error: {1}", "Fehler: Datei {1} fehlt": "Error: file {1} is missing"}}
        self.assertEqual(self.tr(["Fehler: Datei a.txt fehlt"], d), ["Error: file a.txt is missing"])

    def test_special_characters_in_texts_are_not_taken_for_patterns(self):
        d = {"exact": {"Preis (netto) [EUR] $5.00 + {1}": "Price (net) [EUR] $5.00 + {1}"}}
        self.assertEqual(self.tr(["Preis (netto) [EUR] $5.00 + 7"], d), ["Price (net) [EUR] $5.00 + 7"])

    def test_the_real_english_file_translates_every_known_text(self):
        ex = load("en")["exact"]
        sample = [k for k in ex if not PH.search(k)][:400]
        out = self.tr(sample)
        self.assertEqual(out, [ex[k] for k in sample])

    def test_the_real_file_translates_texts_with_values(self):
        ex = load("en")["exact"]
        k = next(x for x in ex if PH.search(x) and len(PH.findall(x)) == 1 and x.count("{1}") == 1 and "." not in x)
        filled = k.replace("{1}", "42")
        got = self.tr([filled])[0]
        self.assertEqual(got, ex[k].replace("{1}", "42"))

    def test_engine_source_avoids_features_old_browsers_lack(self):
        self.assertNotIn("(?<", ENGINE)
        self.assertNotIn("matchAll", ENGINE)
        self.assertNotIn("replaceAll", ENGINE)
        self.assertNotIn("=>", ENGINE.replace("// ", ""))                                    # klassische Funktionen, läuft auch auf älteren Handys

    def test_engine_does_not_crash_without_a_page(self):
        out = run_js("var window = {}; " + ENGINE + "\nprint(typeof window.PB_I18N.tr + ' ' + window.PB_I18N.locale() + ' ' + window.PB_I18N.lang());")
        self.assertEqual(out, "function de-DE de")

    def test_locales(self):
        langs = ("en", "de", "fr", "es", "th", "pt", "ja", "it", "pl", "nl", "ko", "zh", "tr", "ru", "xx")
        code = "var window = {}; " + ENGINE + "\nvar I = window.PB_I18N, r = [];\n" + "".join("I._load({exact: {}}, '%s'); r.push(I.locale());\n" % c for c in langs) + "print(JSON.stringify(r));"
        self.assertEqual(json.loads(run_js(code)), ["en-GB", "de-DE", "fr-FR", "es-ES", "th-TH-u-ca-gregory-nu-latn", "pt-BR", "ja-JP", "it-IT", "pl-PL", "nl-NL", "ko-KR", "zh-CN", "tr-TR", "ru-RU", "en-GB"])

    def test_a_value_does_not_reach_over_a_sentence_end(self):
        d = {"exact": {"Gespeichert: {1}.": "Saved: {1}.", "Das gilt erst nach einem Neustart.": "Applies after a restart."}}
        self.assertEqual(self.tr(["Gespeichert: a, b. Das gilt erst nach einem Neustart.", "Gespeichert: 10.1.1.20."], d),
                         ["Saved: a, b. Applies after a restart.", "Saved: 10.1.1.20."])

    def test_dates_with_dots_and_spaces_do_not_split_a_sentence(self):
        d = {"exact": {"Stand {1}.": "As of {1}.", "Danach ist ein Neustart nötig.": "A restart is needed."}}
        self.assertEqual(self.tr(["Stand 2026. 10. 4. 11:15. Danach ist ein Neustart nötig."], d), ["As of 2026. 10. 4. 11:15. A restart is needed."])

    def test_leading_punctuation_and_parentheses_inside_a_text(self):
        d = {"exact": {"Kamera nicht gefunden. Ist sie an?": "Camera not found. Is it on?", "älter": "older", "neuer": "newer", "Verfügbar:": "Available:"}}
        self.assertEqual(self.tr(["· Kamera nicht gefunden. Ist sie an?", "Verfügbar: 0.9.1 (älter), 0.9.2 (neuer)."], d),
                         ["· Camera not found. Is it on?", "Available: 0.9.1 (older), 0.9.2 (newer)."])

    def test_short_lists_are_translated_item_by_item(self):
        d = {"exact": {"Bitrate": "Bitrate", "Latenz": "Latency", "Verteilung": "Distribution", "Noch nicht übernommen: {1}.": "Not applied yet: {1}."}}
        self.assertEqual(self.tr(["Noch nicht übernommen: Bitrate, Latenz, Verteilung."], d), ["Not applied yet: Bitrate, Latency, Distribution."])


class Pages(unittest.TestCase):
    def test_both_pages_load_the_engine_first(self):
        for page in (PAGE, LOGIN):
            self.assertIn('<script src="/i18n.js"></script>', page)
            self.assertLess(page.index('<script src="/i18n.js"></script>'), page.index("<style>"))

    def test_language_selector_is_not_translated_itself(self):
        self.assertRegex(PAGE, r'<select id="lang_sel" data-lang translate="no"')
        self.assertRegex(LOGIN, r'<select id="lang_sel" data-lang translate="no"')
        self.assertLess(PAGE.index('id="lang_sel"'), PAGE.index('id="theme_btn"'))

    def test_dates_follow_the_language(self):
        self.assertEqual(PAGE.count('"de-DE"'), 1)                                           # nur der Ersatzwert, falls die Übersetzung nicht lädt
        self.assertIn("const LOC=()=>window.PB_I18N?PB_I18N.locale():\"de-DE\";", PAGE)

    def test_default_language_is_english(self):
        self.assertIn('DEFAULT = "en"', ENGINE)
        self.assertIn("stored() || DEFAULT", ENGINE)


class Routes(unittest.TestCase):
    def get(self, path):
        h = server.Handler.__new__(server.Handler)
        h.path, h.sent, h.hdrs = path, [], {}
        h.authed = lambda: False
        h.send_response = lambda code, *a: h.sent.append(code)
        h.send_header = lambda k, v: h.hdrs.__setitem__(k, v)
        h.end_headers = lambda: None

        class W:
            data = b""

            def write(self, b):
                W.data += b
        h.wfile, h.out = W(), W
        h.headers = {}
        h.client_address = ("127.0.0.1", 1)
        h.auth = type("A", (), {"valid": lambda self, t: False, "status": lambda self, *a, **k: {}})()
        try:
            h.do_GET()
        except Exception:
            pass
        return h

    def test_engine_and_dictionaries_are_public(self):
        h = self.get("/i18n.js")
        self.assertEqual(h.sent, [200])
        self.assertIn("javascript", h.hdrs["Content-Type"])
        self.assertIn(b"PB_I18N", h.out.data)
        for code in ("en", "fr", "es", "th", "languages"):
            h = self.get("/i18n/%s.json" % code)
            self.assertEqual(h.sent, [200], code)
            self.assertIn("application/json", h.hdrs["Content-Type"])
            json.loads(h.out.data)

    def test_unknown_or_dangerous_paths_serve_nothing(self):
        for path in ("/i18n/xx.json", "/i18n/../server.py", "/i18n/..%2Fserver.py", "/i18n/en.json/../../server.py", "/i18n/EN.json", "/i18n/e.json", "/i18n/en.js", "/i18n/",
                     "/i18n/dd/en.json", "/i18n/en.json.bak", "/i18n/.json", "/i18n/languages.json/x"):
            h = self.get(path)
            self.assertNotIn(b"class Handler", h.out.data, path)
            self.assertNotIn(b"PB_I18N", h.out.data, path)
            self.assertNotEqual(h.sent[:1], [200], path)

    def test_installer_and_updater_know_the_new_files(self):
        sh = open(os.path.join(ROOT, "install", "install.sh"), encoding="utf-8").read()
        self.assertIn('install -m 644 "$HERE/web/i18n.js" /opt/pipbox/web/i18n.js', sh)
        self.assertIn("/opt/pipbox/web/i18n", sh)
        up = open(os.path.join(ROOT, "install", "pipbox-swupdate.py"), encoding="utf-8").read()
        self.assertIn('"web/i18n.js"', up)
        self.assertIn('"web/i18n/languages.json"', up)


class Extraction(unittest.TestCase):
    def test_extraction_finds_texts_and_normalises_values(self):
        ks = keys()
        self.assertGreater(len(ks), 1000)
        self.assertIn("Kopieren", ks)
        self.assertTrue(any("{1}" in k for k in ks))
        self.assertEqual(i18n_extract.py_format_to_ph("Kamera %s sendet %d Mbit"), "Kamera {1} sendet {2} Mbit")
        self.assertEqual(i18n_extract.py_format_to_ph("{name} hat {} Punkte"), "{1} hat {2} Punkte")
        self.assertEqual(i18n_extract.js_strings("var a=`Hallo ${x?'ja':\"nein\"} Welt`; // Kommentar \"nicht\"\nvar r=/\"x\"/g;")[-1], "Hallo \x00 Welt")
        self.assertFalse(i18n_extract.ui_like("var(--muted)"))
        self.assertFalse(i18n_extract.ui_like("nmcli"))
        self.assertTrue(i18n_extract.ui_like("Abbrechen"))
        self.assertTrue(i18n_extract.ui_like("Kamera {1} sendet nicht"))


if __name__ == "__main__":
    unittest.main()
