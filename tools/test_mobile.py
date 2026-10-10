"""Tests für die mobile Ansicht (Issue #19, #21, #22): Hilfstexte hinter "i", Reihenfolge im Status; Fußleiste mit "Live"/"Stop" sowie Kamera- und Ton-Knöpfen, einzeilige Kopfleiste, keine Rückfrage
beim Start der Sendung. Die Seitenskripte laufen, wenn die JavaScript-Maschine von macOS (jsc) da ist, in einer Attrappe der Seite."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()


class MobileFooter(unittest.TestCase):
    def test_footer_exists_and_is_only_shown_on_phones(self):
        self.assertIn('<nav id="mfoot"', PAGE)
        self.assertIn('id="mf_live"', PAGE)
        self.assertRegex(PAGE, r"#mfoot\{display:none\}")                               # Standard: unsichtbar (Desktop)
        phone = re.search(r"@media\(max-width:620px\)\{\s*header\{flex-wrap:nowrap.*?\n\}", PAGE, re.S)
        self.assertIsNotNone(phone)
        css = phone.group(0)
        self.assertIn("#mfoot{display:flex;position:fixed", css)                          # auf dem Handy fest am unteren Rand
        self.assertIn("#hdr_live{display:none!important}", css)                           # "Live" wandert aus der Kopfleiste in die Fußleiste
        self.assertIn("flex-wrap:nowrap", css)                                            # Kopfleiste in einer Zeile
        self.assertIn("safe-area-inset-bottom", css)                                      # nicht unter der Navigationsleiste des Handys

    def test_footer_follows_the_state_of_the_sending(self):
        i = PAGE.index("function updHdrLive(d){")
        body = PAGE[i:PAGE.index("\n}\n", i)]
        self.assertIn('f.textContent=busy?"…":(live?"Stop":"Live")', body)                # Beschriftung "Live" und "Stop"
        self.assertIn("f.disabled=b.disabled", body)                                      # gleiche Bedingungen wie die Kopfleiste
        self.assertIn('f.className="mf-live"+(live&&!busy?" on":"")+(busy?" busy":"")', body)

    def test_both_buttons_use_the_same_toggle(self):
        self.assertIn('$("hdr_live").addEventListener("click",liveToggle)', PAGE)
        self.assertIn('$("mf_live").addEventListener("click",liveToggle)', PAGE)

    def test_no_question_when_going_live_or_when_stopping(self):
        start = PAGE[PAGE.index("async function doLiveStart(){"):PAGE.index("function seamlessTo(main,key){")]
        self.assertNotIn("confirm(", start)
        self.assertNotIn("Jetzt LIVE senden?", PAGE)
        toggle = PAGE[PAGE.index("async function liveToggle(){"):PAGE.index('$("hdr_live").addEventListener')]
        self.assertNotIn("confirm(", toggle)                                              # auch beim Beenden keine Rückfrage (Antwort des Melders: Ja)
        self.assertNotIn("Die Sendung jetzt beenden?", PAGE)
        self.assertIn("doLiveStop()", toggle)
        self.assertNotIn("(mit Rückfrage)", PAGE)                                         # Tooltips stimmen wieder

    def test_page_leaves_room_for_the_footer_and_the_toast(self):
        self.assertIn("body{padding-bottom:calc(var(--mfh,72px) + 20px + env(safe-area-inset-bottom))}", PAGE)   # --mfh: gemessene Höhe der Fußleiste
        self.assertIn(".toast{bottom:calc(var(--mfh,72px) + 28px + env(safe-area-inset-bottom))}", PAGE)
        self.assertIn('setProperty("--mfh"', PAGE)
        self.assertIn('new ResizeObserver(sizeFoot).observe($("mfoot"))', PAGE)           # die Höhe wird nachgemessen, wenn die Übersetzung den Text umbricht

    def test_header_and_footer_are_as_wide_as_the_cards(self):
        self.assertRegex(PAGE, r"main\{max-width:980px;margin:0 auto;padding:16px;")        # Karten: 16 px Rand links und rechts
        head = re.search(r"\nheader\{display:flex[^\n]*", PAGE).group(0)
        self.assertIn("max-width:calc(980px - 32px);width:calc(100% - 32px)", head)         # auch am Rechner nicht breiter als die Karten
        self.assertNotRegex(head, r"[0-9]+px [0-9]+px [0-9]+px [1-9][0-9]*px var\(--shade\)")   # kein Schatten mit Ausdehnung zur Seite (Spreizung > 0)
        self.assertIn("-4px var(--shade)", head)                                          # Schatten nur nach unten, hell dezenter als dunkel
        self.assertRegex(PAGE, r':root\[data-theme="light"\]\{[^}]*--shade:rgba\(15,23,42,\.16\)')
        foot = PAGE[PAGE.index("#mfoot{display:flex;position:fixed"):]
        foot = foot[:foot.index("}")]
        self.assertNotRegex(foot, r"-?[0-9]+px -?[0-9]+px [0-9]+px [1-9][0-9]*px var\(--shade\)")
        phone = PAGE[PAGE.index("@media(max-width:620px){\n  header{flex-wrap:nowrap"):]
        self.assertIn("main{padding:6px 8px 16px", phone)                                 # schmaler Rand am Handy (8 px)
        self.assertIn("header{flex-wrap:nowrap;padding:6px 10px;gap:6px;width:calc(100% - 16px)}", phone)

    def test_footer_is_rounded_and_inset_like_the_header(self):
        i = PAGE.index("#mfoot{display:flex;position:fixed")
        rule = PAGE[i:PAGE.index("}", i)]
        self.assertIn("left:8px;right:8px", rule)                                         # gleiche Breite wie die Karten (main hat am Handy 8 px Rand)
        self.assertIn("border-radius:14px", rule)                                         # gleiche Rundung wie Karten und Kopfleiste
        self.assertIn("bottom:calc(8px + env(safe-area-inset-bottom))", rule)             # kleiner Abstand nach unten, nicht unter der Navigationsleiste
        self.assertNotIn("border-top", rule)                                              # ringsum ein Rand statt nur oben
        self.assertIn("border:1px solid var(--line)", rule)


JSC = next((p for p in (shutil.which("jsc"), "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc") if p and os.path.exists(p)), None)


def run_js(code):
    """Code in jsc ausführen, Ausgabe zurückgeben."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code)
    try:
        r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(f.name)
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout.strip()


def foot_source():
    return PAGE[PAGE.index("const FIC=(()=>{"):PAGE.index("async function footAct(b,long,dbl){")]


STUBS = """
var els = {};
function $(id) { return els[id] || (els[id] = {id: id, hidden: false, dataset: {}, offsetHeight: 120, addEventListener: function () {}}); }
function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function setHtml(el, h) { el.html = h; }
var document = {documentElement: {style: {setProperty: function () {}, removeProperty: function () {}}, classList: {toggle: function () {}, add: function () {}, remove: function () {}, contains: function () { return false; }}}, addEventListener: function () {}, dispatchEvent: function () {}, querySelector: function () { return {style: {}, classList: {toggle: function () {}, add: function () {}, remove: function () {}, contains: function () { return false; }}, addEventListener: function () {}, offsetHeight: 0, getBoundingClientRect: function () { return {height: 0, top: 0}; }}; }, querySelectorAll: function () { return []; }};
var window = {addEventListener: function () {}};
function MutationObserver() { this.observe = function () {}; this.disconnect = function () {}; }
"""


@unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
class FooterScripts(unittest.TestCase):
    def test_whole_page_script_compiles(self):
        scripts = "\n".join(re.findall(r"<script>(.*?)</script>", PAGE, re.S))
        self.assertIn("function renderFoot", scripts)
        out = run_js("try { new Function(%s); print('ok'); } catch (e) { print('FEHLER ' + e); }" % json.dumps(scripts))
        self.assertEqual(out, "ok")

    def labels(self, names):
        cams = [{"key": "k%d" % i, "name": n} for i, n in enumerate(names)]
        src = foot_source()
        fn = src[src.index("function camLabels(cams){"):src.index("let footBusy")]
        out = run_js(fn + "\nprint(JSON.stringify(camLabels(%s)));" % json.dumps(cams))
        return [json.loads(out)["k%d" % i] for i in range(len(names))]

    def test_label_is_the_first_word_of_the_name(self):
        self.assertEqual(self.labels(["Osmo Action 4", "iPhone hinten", "Webcam"]), ["Osmo", "iPhone", "Webcam"])
        self.assertEqual(self.labels(["  Osmo   Action 4 ", ""]), ["Osmo", "k1"])

    def test_two_cameras_with_the_same_first_word_get_more_words_until_they_differ(self):
        self.assertEqual(self.labels(["Osmo Action 4", "Osmo Action 5", "iPhone"]), ["Osmo Action 4", "Osmo Action 5", "iPhone"])
        self.assertEqual(self.labels(["Osmo Action 4", "Osmo Pocket 3"]), ["Osmo Action", "Osmo Pocket"])
        self.assertEqual(self.labels(["Webcam", "Webcam"]), ["Webcam", "Webcam"])                   # gleiche Namen bleiben gleich, nichts hängt

    def render(self, footer):
        out = run_js(STUBS + foot_source() + "\nrenderFoot(%s); print(JSON.stringify({hidden: $('mf_tools').hidden, html: $('mf_tools').html || ''}));"
                     % json.dumps({"footer": footer}))
        return json.loads(out)

    CAMS = [{"key": "a", "name": "Osmo Action 4", "state": "live", "slot": 0, "main": True, "hidden": False},
            {"key": "b", "name": "iPhone hinten", "state": "live", "slot": 1, "main": False, "hidden": True},
            {"key": "c", "name": "Action 5 Pro", "state": "offline", "slot": 2, "main": False, "hidden": False}]

    def test_buttons_only_for_picture_in_picture(self):
        self.assertTrue(self.render(None)["hidden"])
        self.assertTrue(self.render({"cams": [], "audio": {}})["hidden"])                           # keine Kamera: nichts zu schalten
        one = self.render({"cams": self.CAMS[:1], "audio": {"key": "a", "name": "Osmo Action 4", "mute": False, "src": "main", "next": "main"}})
        self.assertFalse(one["hidden"])                                                             # eine Kamera: nur der Ton-Knopf (stumm), keine Kamera-Knöpfe
        self.assertEqual(one["html"].count('data-kind="cam"'), 0)
        self.assertEqual(one["html"].count('data-kind="aud"'), 1)
        self.assertIn('data-single="1"', one["html"])
        r = self.render({"cams": self.CAMS, "audio": {"key": "a", "name": "Osmo Action 4", "mute": False, "next": "pip"}})
        self.assertFalse(r["hidden"])

    def test_one_button_per_camera_named_by_the_first_word_plus_the_audio_button(self):
        r = self.render({"cams": self.CAMS, "audio": {"key": "a", "name": "Osmo Action 4", "mute": False, "next": "pip"}})["html"]
        labels = re.findall(r'data-kind="cam".*?<span>(.*?)</span>', r)
        self.assertEqual(labels, ["Osmo", "iPhone", "Action"])
        self.assertIn("<span>Ton: Osmo</span>", r)
        self.assertEqual(r.count('data-kind="aud"'), 1)

    def test_deactivated_camera_is_marked_and_never_green(self):
        cams = [dict(c) for c in self.CAMS]
        cams[2]["inactive"] = True
        cams[2]["state"] = "live"
        r = self.render({"cams": cams, "audio": {"key": "a", "name": "Osmo Action 4", "mute": False, "next": "pip"}})["html"]
        mine = re.search(r'<button type="button" class="([^"]*)" data-kind="cam" data-key="c".*?data-inactive="(\d)"', r)
        self.assertIn("off", mine.group(1).split())
        self.assertEqual(mine.group(2), "1")
        self.assertIn(".mf-cam.off.live{color:var(--text)}", PAGE)                                    # auch wenn sie sendet: nicht grün
        self.assertIn(".mf-cam.off{opacity:.55;border-style:dashed}", PAGE)
        others = re.findall(r'class="([^"]*)" data-kind="cam" data-key="(a|b)".*?data-inactive="(\d)"', r)
        self.assertTrue(all("off" not in c.split() and i == "0" for c, k, i in others))

    def test_states_main_hidden_sending_and_muted_are_marked(self):
        r = self.render({"cams": self.CAMS, "audio": {"key": "b", "name": "iPhone hinten", "mute": True, "next": "pip2"}})["html"]
        btn = re.findall(r'<button type="button" class="([^"]*)" data-kind="cam" data-key="(\w)".*?data-slot="(\d)" data-main="(\d)" data-hidden="(\d)"', r)
        by = {k: (cls, slot, main, hid) for cls, k, slot, main, hid in btn}
        self.assertIn("ismain", by["a"][0])                                                         # blauer Rahmen: das Hauptbild
        self.assertNotIn("hid", by["a"][0].split())
        self.assertIn("hid", by["b"][0].split())                                                    # blaues Symbol: ausgeblendet
        self.assertEqual(by["b"][1:], ("1", "0", "1"))
        self.assertIn("live", by["b"][0].split())                                                   # grün: sendet
        self.assertNotIn("live", by["c"][0].split())                                                # weiß: sendet nicht
        self.assertIn("mf-aud muted", r)                                                            # rot durchgestrichenes Mikro
        self.assertIn('data-mute="1"', r)
        self.assertIn('data-next="pip2"', r)
        self.assertEqual(r.count("M4 4l16 16"), 2)                                                  # Strich: ausgeblendetes Bild und stummes Mikro
        self.assertEqual(r.count('x="12" y="11"'), 2)                                               # kleine Bilder mit Bild-im-Bild-Symbol (die Hauptkamera hat das Kamera-Symbol)
        self.assertEqual(r.count("<circle"), 1)

    def test_names_cannot_inject_markup(self):
        cams = [dict(self.CAMS[0], name='<img src=x onerror=alert(1)> "x"'), self.CAMS[1]]
        r = self.render({"cams": cams, "audio": {"key": "a", "name": cams[0]["name"], "mute": False, "next": "pip"}})["html"]
        self.assertNotIn("<img", r)


class FooterBehaviour(unittest.TestCase):
    def test_long_press_and_short_press_are_told_apart(self):
        i = PAGE.index("(function(){                                                              // kurzer und langer Druck")
        block = PAGE[i:PAGE.index("})();", i)]
        self.assertIn("setTimeout(()=>{ timer=null; fired=true;", block)
        self.assertIn(",550)", block)                                                               # etwas mehr als eine halbe Sekunde
        self.assertIn("footAct(b,true)", block)
        self.assertIn("footAct(b,false)", block)
        self.assertIn("if(fired){ fired=false; e.preventDefault(); return; }", block)                # dem langen Druck folgt kein kurzer
        self.assertIn('"contextmenu"', block)                                                       # kein Kontextmenü beim langen Druck
        self.assertIn("touch-action:manipulation", PAGE)                                            # kein Zoomen durch Doppeltippen

    def test_requests_of_the_buttons(self):
        i = PAGE.index("async function footAct(b,long,dbl){")
        body = PAGE[i:PAGE.index("(function(){", i)]
        self.assertIn('"/api/pipeline/swap",{with:b.dataset.key}', body)                            # kurz auf eine Kamera: zum Hauptbild machen
        self.assertIn('"/api/pipeline/view",{visible:{[b.dataset.slot]:b.dataset.hidden==="1"}}', body)   # doppelt: aus-/einblenden
        self.assertIn('"/api/pipeline/view",{mute:b.dataset.mute!=="1"}', body)                    # lang auf Ton: stumm und wieder laut
        self.assertIn('"/api/pipeline/view",{audio:b.dataset.next}', body)                          # kurz auf Ton: nächste Tonspur
        self.assertIn("Das Hauptbild lässt sich nicht ausblenden", body)
        self.assertIn("Stumm schalten geht nur während der Sendung.", body)

    def test_double_tap_hides_and_a_single_tap_waits_for_it(self):
        i = PAGE.index("let tapT=null, tapKey=\"\";")
        block = PAGE[i:PAGE.index("});", PAGE.index("tapT=setTimeout", i)) + 3]
        self.assertIn("footAct(b,false,true)", block)                                           # zweiter Tipp: kleines Bild aus-/einblenden
        self.assertIn("setTimeout(()=>{ tapT=null; footAct(b,false); },300)", block)            # sonst nach 300 ms der kurze Tipp
        self.assertIn('b.dataset.kind!=="cam"', block)                                          # der Ton-Knopf wartet nicht
        j = PAGE.index("async function footAct(b,long,dbl){")
        body = PAGE[j:PAGE.index("(function(){", j)]
        self.assertIn('"/api/pipeline/active",{key:b.dataset.key,active:on}', body)
        self.assertIn("Das Hauptbild lässt sich nicht deaktivieren.", body)
        self.assertIn("on=b.dataset.inactive===\"1\"", body)

    def test_gestures_short_main_double_hide_long_deactivate(self):
        j = PAGE.index("async function footAct(b,long,dbl){")
        body = PAGE[j:PAGE.index("}else if(long){", j)]                                         # nur der Teil für die Kamera-Knöpfe
        i_long, i_dbl, i_swap = body.index("if(long){"), body.index("if(dbl) r="), body.index("else r=await srtlaCall(\"POST\",\"/api/pipeline/swap\"")
        self.assertLess(i_long, i_dbl)
        self.assertIn('"/api/pipeline/active"', body[i_long:i_dbl])                               # lang: deaktivieren (und wieder aktivieren)
        self.assertIn('"/api/pipeline/view",{visible:', body[i_dbl:i_swap])                       # doppelt: ausblenden
        self.assertIn('"/api/pipeline/swap"', body[i_swap:])                                      # kurz: Hauptbild wechseln
        self.assertIn("Das ist schon das Hauptbild.", body)                                       # kurz auf das Hauptbild: nichts zu tun
        self.assertIn("Zum Tauschen kurz auf eine andere Kamera drücken.", body)                  # der Hinweis nennt die neue Bedienung

    def test_descriptions_match_the_gestures(self):
        i = PAGE.index('$("mf_info").addEventListener("click"')
        info = PAGE[i:PAGE.index("function fitMsg", i)]
        for text in ("Kurzer Klick = zum Hauptbild machen", "Doppelklick = aus-/einblenden, Ton bleibt", "Langer Klick = deaktivieren / aktivieren",
                     "Ton: kurzer Klick = nächste Spur", "Ton: langer Klick = stumm / wieder laut"):
            self.assertIn(text, info)
        tip = PAGE[PAGE.index("const tip=(c.main?"):PAGE.index('.join(" ");', PAGE.index("const tip=(c.main?"))]
        for text in ("Kurz drücken macht es zum Hauptbild.", "Doppelt tippen: das kleine Bild ausblenden", "Doppelt tippen: das kleine Bild einblenden.",
                     "Lang drücken: deaktivieren", "Lang drücken: aktivieren."):
            self.assertIn(text, tip)
        for old in ("Lang drücken macht es zum Hauptbild", "Kurz drücken blendet es aus"):
            self.assertNotIn(old, PAGE)

    def test_info_lines_never_wrap(self):
        i = PAGE.index('$("mf_info").addEventListener("click"')
        info = PAGE[i:PAGE.index("function fitMsg", i)]
        for line in re.findall(r'"([^"]+)"', info[info.index("const lines="):info.index("footMsg(lines")]):
            self.assertLessEqual(len(line), 42, line)                                               # kurze Zeilen
        self.assertIn('.mf-msg[data-info="1"]{white-space:pre;overflow:hidden}', PAGE)               # kein Umbruch in jedem Design
        fit = PAGE[PAGE.index("function fitMsg(m)"):PAGE.index("function sizeFoot")]
        self.assertIn("m.scrollWidth>m.clientWidth", fit)                                           # die Schrift schrumpft, bis die breiteste Zeile passt
        self.assertIn("fitMsg(m)", info)

    def test_one_camera_keeps_the_audio_button_and_live_is_not_stretched(self):
        i = PAGE.index("function renderFoot(d){")
        body = PAGE[i:PAGE.index("async function footAct", i)]
        self.assertIn("!f.cams.length", body)                                                       # erst ohne Kamera verschwindet die Leiste
        self.assertIn("multi=f.cams.length>=2", body)
        self.assertIn('data-single="${one?1:0}"', body)
        self.assertIn("#mfoot:has(#mf_tools[hidden]) .mf-live{flex:0 0 auto", PAGE)                  # "Live"/"Stop" allein nicht über die ganze Breite

    def test_a_camera_that_is_not_connected_is_never_made_main(self):
        j = PAGE.index("async function footAct(b,long,dbl){")
        body = PAGE[j:PAGE.index("}else if(long){", j)]
        self.assertIn('data-gone="${c.state==="offline"?1:0}"', PAGE)
        self.assertIn('b.dataset.gone==="1") return footMsg("Diese Kamera ist nicht verbunden und lässt sich nicht zum Hauptbild machen.")', body)
        self.assertLess(body.index('b.dataset.gone==="1"'), body.index('"/api/pipeline/swap"'))       # die Prüfung kommt vor dem Wechsel
        self.assertIn("Es gibt nur eine Tonquelle", PAGE)                                              # kurz drücken auf den einzigen Ton: Hinweis statt Wechsel

    def test_footer_and_header_never_open_a_popup_on_the_phone(self):                        # Issue #23
        i = PAGE.index("async function footAct(b,long,dbl){")
        body = PAGE[i:PAGE.index("(function(){", i)]
        self.assertNotIn("confirm(", body)
        self.assertNotIn("alert(", body)
        start = PAGE[PAGE.index("async function doLiveStart(){"):PAGE.index("function seamlessTo(main,key){")]
        self.assertIn("if(isPhone()) footMsg(x.message); else alert(x.message);", start)       # Fehler beim Start: am Handy in der Fußleiste, nicht im Fenster
        self.assertIn('window.matchMedia("(max-width:620px)")', PAGE)
        hdr = PAGE[PAGE.index("function updHdrLive(d){"):PAGE.index("\n}\n", PAGE.index("function updHdrLive(d){"))]
        self.assertNotIn("confirm(", hdr)
        self.assertNotIn("alert(", hdr)

    def test_footer_follows_the_poll_and_does_not_replace_buttons_while_pressed(self):
        self.assertIn("updHdrLive(d); renderFoot(d);", PAGE)
        i = PAGE.index("function renderFoot(d){")
        self.assertIn("if(footBusy) return;", PAGE[i:i + 200])

    def test_colours(self):
        for rule in (".mf-cam.live{color:var(--ok)}", ".mf-cam.hid{color:var(--blue)}", ".mf-cam.ismain{border-color:var(--blue)}",
                     ".mf-aud.muted{color:var(--crit);border-color:var(--crit)}"):
            self.assertIn(rule, PAGE)
        self.assertIn("--blue:#3b82f6", PAGE)

    def test_markup(self):
        self.assertRegex(PAGE, r'<nav id="mfoot".*?id="mf_msg".*?id="mf_live".*?id="mf_tools".*?</nav>')
        i = PAGE.index("#mfoot{display:flex;position:fixed")
        self.assertNotIn("flex-direction:column", PAGE[i:PAGE.index("}", i)])                         # eine Reihe wie in der BELABOX-Oberfläche: "Live" links, Knöpfe rechts
        self.assertLess(PAGE.index('id="mf_live"'), PAGE.index('id="mf_tools"'))


class MovedFunctions(unittest.TestCase):
    """Issue #23: Automatischer Start in die SRTLA-Karte, der Block "Nicht live" entfällt am Handy, Knöpfe im Kopf statt im Block."""
    def test_autostart_sits_first_in_the_srtla_card(self):
        i = PAGE.index('id="srtlacard"')
        card = PAGE[i:PAGE.index("</details>", i)]
        for needle in ('id="auto_on"', 'id="auto_help_btn"', 'id="auto_help"', 'id="autostatus"'):
            self.assertIn(needle, card)
            self.assertEqual(PAGE.count(needle), 1, needle)
        self.assertLess(card.index('id="auto_on"'), card.index(">SRTLA-Server</summary>"))
        live = PAGE[PAGE.index('id="livecard"'):PAGE.index("</section>", PAGE.index('id="livecard"'))]
        self.assertNotIn("auto_on", live)

    def test_live_and_streaming_mode_buttons_are_gone_from_the_live_block(self):
        live = PAGE[PAGE.index('id="livecard"'):PAGE.index("</section>", PAGE.index('id="livecard"'))]
        for gone in ("live_go", "live_stop", "sm_btn2", "Live gehen", "Streammodus"):
            self.assertNotIn(gone, live)
            self.assertNotIn(gone if gone.startswith(("live_", "sm_")) else "xx-nie-da", PAGE)
        self.assertIn('id="hdr_live"', PAGE)
        self.assertIn('id="sm_btn"', PAGE)

    def test_header_button_has_the_colour_of_the_former_live_button(self):
        self.assertIn(".livebtn:not(.on):not(.busy){background:var(--warn);border-color:var(--warn);color:#0f172a}", PAGE)
        self.assertIn("button.warnbtn{background:var(--warn)}", PAGE)                         # dieselbe Farbe wie "Live gehen" in der Karte

    def test_the_live_block_disappears_on_the_phone_but_notes_stay(self):
        phone = re.search(r"@media\(max-width:620px\)\{\s*header\{flex-wrap:nowrap.*?\n\}", PAGE, re.S).group(0)
        self.assertIn("#livecard{padding:0;border:0;background:transparent;box-shadow:none;margin:0}", phone)
        self.assertIn("#livecard #livebox>.row,#livecard #swapbar,#livecard #liveserver,#livecard #livehealth{display:none}", phone)
        self.assertIn("#livecard :is(.ph,.err,.pending):empty{display:none}", phone)           # Hinweise, Fehler und "Noch nicht übernommen" bleiben, wenn es welche gibt
        for kept in ('id="livewhy"', 'id="livepending"', 'id="liveerr"'):
            self.assertIn(kept, PAGE)

    def test_scripts_do_not_touch_removed_elements(self):
        for gone in ('$("live_go")', '$("live_stop")', '$("sm_btn2")'):
            self.assertNotIn(gone, PAGE)


LOGIN = open(os.path.join(ROOT, "web", "login.html"), encoding="utf-8").read()


class Theme(unittest.TestCase):
    """Hell und dunkel (Issue #24)."""
    def test_both_pages_know_the_light_colours_and_the_stored_choice(self):
        for page in (PAGE, LOGIN):
            self.assertIn('<meta name="color-scheme" content="dark light">', page)
            self.assertIn(':root[data-theme="light"]{color-scheme:light;--bg:#f1f5f9;', page)
            self.assertIn(":root{color-scheme:dark}", page)
            self.assertLess(page.index('localStorage.getItem("pb_theme")'), page.index("<style>"))   # vor dem Aufbau der Seite, sonst blitzt es kurz dunkel

    def test_dark_stays_the_default_and_every_variable_has_a_light_value(self):
        import re as _re
        dark = dict(_re.findall(r"--([a-z]+):(#[0-9a-f]{6})", PAGE[PAGE.index(":root{--bg:"):PAGE.index("\n", PAGE.index(":root{--bg:"))]))
        light = dict(_re.findall(r"--([a-z]+):(#[0-9a-f]{6})", PAGE[PAGE.index(':root[data-theme="light"]'):PAGE.index("\n", PAGE.index(':root[data-theme="light"]'))]))
        self.assertEqual(set(dark), set(light))
        self.assertEqual(dark["bg"], "#0f172a")                                                 # der bisherige Look ist unverändert der Standard
        self.assertNotEqual(dark["bg"], light["bg"])

    def test_no_dark_only_colours_for_text_are_left_in_the_footer(self):
        self.assertNotIn("#e2e8f0", PAGE)
        body = PAGE[PAGE.index("</style>"):]
        body = re.sub(r"<svg.*?</svg>", "", body, flags=re.S)                                   # das Logo im Kopf hat feste Markenfarben
        self.assertNotIn("#f8fafc", body)

    def test_button_in_the_header(self):
        self.assertRegex(PAGE, r'<button type="button" class="sec small" id="theme_btn" aria-label="Hell oder dunkel"')
        self.assertLess(PAGE.index('id="theme_btn"'), PAGE.index('id="hdr_live"'))


@unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
class ThemeScripts(unittest.TestCase):
    def run_early(self, stored):
        script = re.search(r"<script>(try\{var t=localStorage.*?)</script>", PAGE, re.S).group(1)
        code = ("var attrs = {}; var document = {documentElement: {setAttribute: function (k, v) { attrs[k] = v; }}};\n"
                "var localStorage = {getItem: function () { %s }};\n%s\nprint(JSON.stringify(attrs));") % (stored, script)
        return json.loads(run_js(code))

    def test_stored_choice_is_applied_and_junk_is_ignored(self):
        self.assertEqual(self.run_early("return 'light';"), {"data-theme": "light"})
        self.assertEqual(self.run_early("return 'dark';"), {"data-theme": "dark"})
        for junk in ("return null;", "return 'blau';", "return '';", "return 5;"):
            self.assertEqual(self.run_early(junk), {}, junk)
        self.assertEqual(self.run_early("throw new Error('gesperrt');"), {})                 # Speicher gesperrt (privates Fenster): kein Fehler

    def test_set_theme_switches_the_icon_saves_only_on_request_and_survives_blocked_storage(self):
        src = PAGE[PAGE.index("const THEME_ICONS="):PAGE.index("$(\"theme_btn\").addEventListener")]
        code = """
var els = {}; function $(id) { return els[id] || (els[id] = {id: id, innerHTML: "", title: "", attrs: {}, setAttribute: function (k, v) { this.attrs[k] = v; }}); }
var root = {attrs: {"data-theme": "dark"}, getAttribute: function (k) { return this.attrs[k]; }, setAttribute: function (k, v) { this.attrs[k] = v; }};
var document = {documentElement: root, querySelector: function () { return null; }};
var saved = []; var blocked = false;
var localStorage = {setItem: function (k, v) { if (blocked) throw new Error("gesperrt"); saved.push([k, v]); }};
function getComputedStyle() { return {getPropertyValue: function () { return "#fff"; }}; }
""" + src + """
setTheme("light", false); var a = [curTheme(), $("theme_btn").title, $("theme_btn").innerHTML.indexOf("<circle") >= 0, saved.length];
setTheme("dark", true);  var b = [curTheme(), $("theme_btn").title, $("theme_btn").innerHTML.indexOf("<circle") >= 0, saved.slice()];
blocked = true; setTheme("light", true); var c = curTheme();
print(JSON.stringify({a: a, b: b, c: c}));"""
        o = json.loads(run_js(code))
        self.assertEqual(o["a"], ["light", "Auf dunkel wechseln", False, 0])                  # hell: Mond, nichts gespeichert
        self.assertEqual(o["b"], ["dark", "Auf hell wechseln", True, [["pb_theme", "dark"]]])  # dunkel: Sonne, gespeichert
        self.assertEqual(o["c"], "light")                                                      # gesperrter Speicher darf nicht stören


class StatusOrder(unittest.TestCase):
    def test_upload_then_cameras_then_system(self):                                         # Issue #22
        i = PAGE.index('<div class="statgrid">')
        grid = PAGE[i:PAGE.index('<details class="dsec" id="statdet">', i)]                  # ohne den Bereich "Details" (Issue #26)
        pos = [grid.index(m) for m in ('<div class="sech">Up- und Download</div>', 'id="camlights"', '<div class="sech">System</div>')]
        self.assertEqual(pos, sorted(pos))
        self.assertEqual(grid.count('<div class="statbox">'), 3)
        for needle in ('id="net"', 'id="cpu"', 'id="mem"', 'id="cpubar"', 'id="membar"'):                 # nichts ging beim Umstellen verloren
            self.assertEqual(grid.count(needle), 1, needle)


@unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
class HelpTexts(unittest.TestCase):
    """Hilfstexte hinter einem "i" (Issue #21): nur auf dem Handy, nur feste Beschreibungen unter einer Überschrift."""
    def owner(self, build):
        src = PAGE[PAGE.index("function hlpOwner(ph){"):PAGE.index("\n}\n", PAGE.index("function hlpOwner(ph){")) + 3]
        code = """
function Node(tag, cls, attrs) { this.tagName = tag; this.cls = (cls || "").split(" "); this.attrs = attrs || {}; this.parentElement = null; this.kids = [];
  var self = this; this.classList = {contains: function (c) { return self.cls.indexOf(c) >= 0; }}; }
Node.prototype.hasAttribute = function (a) { return a in this.attrs; };
Object.defineProperty(Node.prototype, "previousElementSibling", {get: function () { if (!this.parentElement) return this.prev || null; var k = this.parentElement.kids, i = k.indexOf(this); return i > 0 ? k[i - 1] : null; }});
function add(parent, child) { child.parentElement = parent; parent.kids.push(child); return child; }
var card = new Node("DETAILS", "card");
""" + src + build + "\nprint(r === null ? 'null' : r.tag);\n"
        return run_js(code)

    def test_text_right_after_a_heading_or_a_card_header_belongs_to_it(self):
        self.assertEqual(self.owner("var h = add(card, new Node('DIV', 'sech')); h.tag = 'ueberschrift'; var p = add(card, new Node('DIV', 'ph')); var r = hlpOwner(p);"), "ueberschrift")
        self.assertEqual(self.owner("var h = add(card, new Node('SUMMARY', '')); h.tag = 'kopf'; var p = add(card, new Node('DIV', 'ph')); var r = hlpOwner(p);"), "kopf")

    def test_text_as_first_child_of_the_block_after_a_heading(self):
        self.assertEqual(self.owner("var h = add(card, new Node('DIV', 'sech')); h.tag = 'ueberschrift'; var box = add(card, new Node('DIV', '')); var p = add(box, new Node('DIV', 'ph')); var r = hlpOwner(p);"),
                         "ueberschrift")

    def test_text_further_down_stays_visible(self):
        self.assertEqual(self.owner("add(card, new Node('DIV', 'sech')); add(card, new Node('DIV', 'zeilen')); var p = add(card, new Node('DIV', 'ph')); var r = hlpOwner(p);"), "null")
        self.assertEqual(self.owner("var p = add(card, new Node('DIV', 'ph')); var r = hlpOwner(p);"), "null")

    def test_marked_text_finds_the_heading_before_it_even_further_down(self):
        self.assertEqual(self.owner("var h = add(card, new Node('DIV', 'sech')); h.tag = 'ueberschrift'; add(card, new Node('DIV', 'zeilen')); add(card, new Node('FORM', '')); "
                                    "var p = add(card, new Node('DIV', 'ph', {'data-hlp': ''})); var r = hlpOwner(p);"), "ueberschrift")
        self.assertEqual(self.owner("var h = add(card, new Node('DIV', 'sech')); h.tag = 'ueberschrift'; var box = add(card, new Node('DIV', '')); add(box, new Node('DIV', 'zeilen')); "
                                    "var p = add(box, new Node('DIV', 'ph', {'data-hlp': ''})); var r = hlpOwner(p);"), "ueberschrift")
        self.assertEqual(self.owner("add(card, new Node('DIV', 'zeilen')); var p = add(card, new Node('DIV', 'ph', {'data-hlp': ''})); var r = hlpOwner(p);"), "null")


class HelpTextMarkup(unittest.TestCase):
    def test_messages_and_labelled_texts_are_never_hidden(self):
        self.assertIn('const HLP_SEL=".card .ph:not([id]):not([role])";', PAGE)             # Meldungen haben eine Kennung oder role="status"

    def test_help_buttons_on_every_screen_and_can_be_switched_off(self):
        """Stand seit 0.9.212: Das "i" gibt es auch am Rechner (Hilfetexte erst nach Antippen) und lässt sich in den Optionen ausblenden."""
        self.assertIn(".hlp:not(.open){display:none}", PAGE)
        self.assertRegex(PAGE, r"\.ibtn\{display:inline-block")
        self.assertIn("html.nohelp :is(.ibtn,.ibrow,.helpbtn,.hlp){display:none!important}", PAGE)

    def test_the_i_sits_next_to_the_heading_and_only_while_the_area_is_open(self):
        i = PAGE.index('let b=head.querySelector(":scope>.ibtn,:scope>.sumh>.ibtn");')
        block = PAGE[i:PAGE.index("b._hlp.push(ph)", i)]
        self.assertIn('(head.querySelector(":scope>.sumh")||head).appendChild(b);', block)       # in der Kopfzeile hinter der Überschrift
        self.assertNotIn('"ibrow"', block)                                                      # keine eigene Zeile darunter
        self.assertIn("details:not([open])>summary .ibtn{display:none}", PAGE)                  # nur bei aufgeklapptem Bereich

    def test_the_i_button_does_not_fold_the_card_and_is_labelled(self):
        i = PAGE.index('document.addEventListener("click",e=>{\n    const b=e.target.closest&&e.target.closest(".ibtn")')
        block = PAGE[i:PAGE.index("},true);", i)]
        self.assertIn("e.preventDefault(); e.stopPropagation();", block)
        self.assertIn('aria-expanded', block)
        self.assertIn('Hilfe ausblenden', block)
        self.assertIn('b.setAttribute("aria-label","Hilfe anzeigen")', PAGE)

    def test_the_two_long_texts_without_a_heading_right_above_are_marked(self):
        self.assertIn('<div class="ph" data-hlp>Die Kamera sendet an die angezeigte RTMP-Adresse.', PAGE)
        self.assertIn('<div class="ph" data-hlp>Kamera einschalten und wach halten', PAGE)


if __name__ == "__main__":
    unittest.main()
