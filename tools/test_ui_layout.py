"""Tests für die Menüeinstellung (Optionen): Reihenfolge, Ausblenden und die Überschriften von Chat und Vorschau gelten je Gerät (Browser) und stehen nicht auf der Box."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SRC = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
_A = PAGE.index("// ---- Hauptmenüs: Reihenfolge")
BLOCK = PAGE[_A:PAGE.index("\n})();", _A) + 6]                                           # nur dieser Block


class PerDevice(unittest.TestCase):
    def test_page_keeps_the_setting_only_in_the_browser(self):
        self.assertIn('localStorage.setItem(KEY,JSON.stringify(st))', BLOCK)
        self.assertIn("je Gerät (Browser) gemerkt, nicht auf der Box", BLOCK)
        self.assertNotIn("srtlaCall", BLOCK)                                                  # nichts wird auf die Box geschickt oder von ihr geholt
        self.assertNotIn("/api/layout", PAGE)
        self.assertNotIn("function pull", BLOCK)
        self.assertNotIn("function push", BLOCK)

    def test_save_is_the_only_writer(self):
        self.assertIn("const save=()=>{ try{ localStorage.setItem(KEY,JSON.stringify(st)); }catch(e){} };", BLOCK)

    def test_applied_at_start_without_asking_the_box(self):
        self.assertIn("applyOrder(); applyHidden(); buildList();\n})();", BLOCK)

    def test_server_has_no_shared_layout_any_more(self):
        for needle in ("class UiLayout", "/api/layout", "Handler.layout", "ui-layout.json", "self.layout"):
            self.assertNotIn(needle, SRC, needle)

    def test_backup_does_not_carry_the_menu_setting(self):
        self.assertNotIn('("layout", "Optionen', SRC)
        self.assertNotIn('doc["layout"]', SRC)


class CompactChat(unittest.TestCase):
    def test_idle_line_and_gear_go_away_while_the_chat_runs(self):
        self.assertIn("html.nohead #chat_msg.quiet{display:none}", PAGE)
        self.assertIn("html.nohead #c_chat.chatok>summary{visibility:hidden;", PAGE)
        self.assertIn("html.nohead #c_chat.chatok.showctl>summary", PAGE)
        self.assertIn("#chat_opt[aria-expanded=true])>summary{visibility:visible", PAGE)       # solange die Chat-Einstellungen offen sind
        self.assertIn('msg.classList.toggle("quiet",d.state==="ok"&&!shown)', PAGE)
        self.assertIn('card.classList.toggle("chatok",d.state==="ok")', PAGE)

    def test_errors_stay_visible(self):
        self.assertIn('msg.classList.remove("quiet"); card.classList.remove("chatok")', PAGE)    # keine Verbindung zur Box: Zeile und ⚙ zurück
        self.assertNotIn("#chat_msg{display:none", PAGE)

    def test_gear_comes_back_on_mouse_move_or_tap(self):
        i = PAGE.index("// ---- Chat in der kompakten Ansicht")
        blk = PAGE[i:i + 600]
        self.assertIn('c.addEventListener("pointermove"', blk)
        self.assertIn('c.addEventListener("click",show)', blk)
        self.assertIn("10000", blk)                                                            # zehn Sekunden sichtbar (7 bis 10 s wünschte der Nutzer: 3 s waren zu wenig)


class PhoneChat(unittest.TestCase):
    def test_phone_rules_are_tight(self):
        i = PAGE.index("@media(max-width:620px){\n  .chatbox{")
        blk = PAGE[i:PAGE.index("\n}", i)]
        self.assertIn("font-size:15px;line-height:1.15", blk)
        self.assertIn(".chatbox .cm{padding:0 2px;", blk)                                   # 0 px statt 9 px zwischen den Nachrichten
        self.assertIn("img.em{height:1.3em;", blk)

    def test_desktop_rules_unchanged(self):
        self.assertIn("line-height:1.45;display:flex;flex-direction:column", PAGE)
        self.assertIn(".chatbox .cm{display:grid;grid-template-columns:auto 1fr auto;column-gap:10px;padding:7px 2px;", PAGE)


class FootHides(unittest.TestCase):
    BLK = PAGE[PAGE.index("// Fußleiste am Handy, Einstellung je Gerät"):PAGE.index("function sizeFoot(){")]

    def test_footer_slides_away_and_back(self):
        self.assertIn("html.mfhide #mfoot{transform:translateY(", PAGE)
        self.assertIn("visibility:hidden", PAGE[PAGE.index("html.mfhide #mfoot"):PAGE.index("html.mfhide #mfoot") + 300])
        self.assertIn("if(ny<24||bottom||run<-8) scrollHid=false; else if(run>8) scrollHid=true;", self.BLK)   # oben, unten, deutlich hoch: da; deutlich runter: weg
        self.assertIn('getComputedStyle(f).display==="none"', self.BLK)                              # nur wo die Leiste steht
        self.assertIn('f.addEventListener("focusin"', self.BLK)
        self.assertIn('window.addEventListener("scroll"', self.BLK)                                  # Fensterscrollen, nicht das im Chatfeld

    def test_only_hides_while_chat_or_preview_is_shown(self):
        self.assertIn('["c_chat","c_prev"].some(id=>{ const e=$(id); return !!e&&e.open&&e.offsetParent!==null; })', self.BLK)
        self.assertIn('h=mode==="off"||(mode!=="always"&&wide&&(mode==="hide"||scrollHid))', self.BLK)   # automatisch und "hide" nur mit Chat oder Vorschau

    def test_three_modes_kept_per_device_only(self):
        self.assertIn('KEY="pb_foot"', self.BLK)
        self.assertIn('localStorage.setItem(KEY,mode)', self.BLK)
        self.assertIn('v==="always"||v==="hide"||v==="off"', self.BLK)                               # Standard: automatisch
        self.assertNotIn("srtlaCall", self.BLK)                                                      # nichts geht an die Box
        self.assertIn('[["auto","Automatisch"],["always","Immer anzeigen"],["hide","Ausblenden bei Chat oder Vorschau"],["off","Immer ausblenden"]]', PAGE)
        self.assertIn('window.pbFootMode(sel.value)', PAGE)

    def test_always_hide_works_without_chat_and_preview(self):
        """Issue #56: Wer Chat und Vorschau nicht nutzt, wählte "Ausblenden" und die Leiste blieb."""
        self.assertIn('h=mode==="off"||(mode!=="always"&&wide&&(mode==="hide"||scrollHid))', self.BLK)
        self.assertIn('root.classList.toggle("mfperm",mode==="off"||(mode==="hide"&&wide))', self.BLK)

    def test_no_space_kept_when_footer_is_gone_for_good(self):
        self.assertIn("html.mfperm body{padding-bottom:calc(16px + env(safe-area-inset-bottom))}", PAGE)


class HeaderLogo(unittest.TestCase):
    """Issue #61: Das IRL4YOU-Logo im Kopf, im Wechsel mit dem Text; während der Sendung nur das Logo."""

    def test_logo_and_text_are_both_in_the_header_and_readable_by_screen_readers(self):
        self.assertIn('<h1 class="hl" aria-label="IRL4YOU BOX"><span class="hlogo"><svg viewBox="0 0 1024 1024"', PAGE)
        self.assertIn('aria-hidden="true" focusable="false"', PAGE[PAGE.index('class="hlogo"'):PAGE.index('class="hlogo"') + 200])
        self.assertIn('<span class="htxt">IRL4YOU BOX</span></h1>', PAGE)

    def test_logo_is_a_small_inline_vector_with_the_brand_colours(self):
        a = PAGE.index('<span class="hlogo">')
        svg = PAGE[a:PAGE.index("</svg>", a) + 6]
        self.assertLess(len(svg), 1200)                                                        # klein, nichts wird nachgeladen
        for colour in ("#12213c", "#1cdaf5", "#f8fafc", "#ffa14f"):                             # Marineblau, Cyan, Weiß, Orange
            self.assertIn(colour, svg)
        self.assertNotIn("<image", svg)
        self.assertNotIn("href=", svg.replace('url(#hl4m)', ""))

    def test_alternates_every_30_seconds_with_a_soft_fade(self):
        self.assertIn(".hl .hlogo{animation:hlA 60s linear infinite}.hl .htxt{animation:hlB 60s linear infinite}", PAGE)
        self.assertIn("@keyframes hlA{0%,46%{opacity:1}50%,96%{opacity:0}100%{opacity:1}}", PAGE)
        self.assertIn("@keyframes hlB{0%,46%{opacity:0}50%,96%{opacity:1}100%{opacity:0}}", PAGE)

    def test_only_the_logo_while_sending(self):
        self.assertIn("body.streaming .hl .htxt{display:none}body.streaming .hl .hlogo{animation:none;opacity:1}", PAGE)

    def test_no_animation_when_the_device_reduces_motion(self):
        i = PAGE.index("@media(prefers-reduced-motion:reduce){.hl .hlogo,.hl .htxt{animation:none;opacity:1}")
        self.assertIn("h1.hl{display:inline-flex;gap:8px}", PAGE[i:i + 250])                  # dann Logo und Text nebeneinander


class DefaultOrder(unittest.TestCase):
    def test_options_sit_near_the_bottom_and_power_is_last(self):
        """Wunsch des Nutzers: Standardreihenfolge unten: Optionen (mit Problem melden darin), Box ausschalten und abmelden (jeder kann sie ändern)."""
        import re
        ids = [m.group(1) for m in re.finditer(r'<details class="card[^>]*?\bid="([A-Za-z_0-9]+)"', PAGE[PAGE.index("<main"):])]
        self.assertEqual(ids[-2:], ["c_layout", "c_power"])                                      # "Problem melden" steht jetzt unter Optionen (Wunsch des Nutzers, 0.9.200)
        self.assertNotIn("c_report", ids)
        self.assertLess(ids.index("c_dev"), ids.index("c_layout"))                              # Entwickler steht über den Optionen
        self.assertEqual(ids[:3], ["c_prev", "c_chat", "c_status"])                              # oben unverändert


class ImportProgress(unittest.TestCase):
    def test_import_and_restore_show_a_progress_bar_like_the_update(self):
        """Wunsch des Nutzers: beim Einspielen eine Fortschrittsanzeige wie beim Software-Update (damit niemand denkt, es passiere nichts)."""
        self.assertIn('<div id="bk_prog" class="prog" hidden>', PAGE)
        self.assertIn('async function bkWithProgress(fn){', PAGE)
        self.assertIn('srtlaCall("GET","/api/settings/progress")', PAGE)
        self.assertIn('await bkWithProgress(()=>srtlaCall("POST","/api/settings/import"', PAGE)
        self.assertIn('await bkWithProgress(()=>srtlaCall("POST","/api/settings/restore"', PAGE)
        self.assertIn('/api/settings/progress', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())

    def test_todo_note_after_import_lists_what_the_backup_does_not_contain(self):
        """Nach dem Einspielen steht, was noch zu tun ist (Wunsch des Nutzers): DJI koppeln, Twitch, Tailscale, Netzwerkkarten prüfen; Haken merkt der Browser."""
        self.assertIn('<div id="bk_todo" class="bktodo" hidden>', PAGE)
        for k in ("dji", "twitch", "tailscale", "net"):
            self.assertIn('data-todo="%s"' % k, PAGE)
        self.assertIn("function bkTodoShow(){", PAGE)
        self.assertIn("function bkResults(r){\n  bkTodoShow();", PAGE)                      # erscheint, sobald Ergebnisse da sind
        self.assertIn('bkTodoSave(all?null:st)', PAGE)                                      # alle gesetzt: weg

    def test_backup_card_lists_what_is_and_is_not_saved(self):
        """Vor dem Herunterladen steht, was die Sicherung enthält und was nicht (Wunsch des Nutzers); die Liste deckt alle Teile des Dokuments ab."""
        i = PAGE.index('<details class="subsec" id="bk_what">')
        self.assertLess(i, PAGE.index('id="bk_export"'))                                     # über dem Knopf zum Herunterladen
        block = PAGE[i:PAGE.index("</details>", i)]
        yes, no = block.split('class="bkno"')
        for t in ("Kameras", "SRTLA-Server", "Sendewege, Kameranetz und Hotspots", "Gespeicherte WLAN-Netze", "DJI-Kameras", "HDMI-Eingang",
                  "Automatischer Start", "Eigene Namen", "Twitch: Kanal"):
            self.assertIn(t, yes)
        for t in ("Bluetooth-Kopplung", "Twitch-Anmeldung und Token", "Tailscale", "Feste Zusatzadresse", "Protokolle und Mitschnitte"):
            self.assertIn(t, no)
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        doc = src[src.index("def make_document"):src.index("def export(self, secrets_on")]
        self.assertNotIn("ExtraAddress", doc)                                               # die feste Adresse steckt tatsächlich nicht in der Sicherung
        self.assertNotIn('"token"', doc)

    def test_chat_command_answer_stays_visible_under_the_input(self):
        """Ein Befehl im Chat-Feld zeigt die Antwort (zum Beispiel "VIP vergeben: Name") einige Sekunden an; Fehler von Twitch stehen wie bisher an derselben Stelle."""
        self.assertIn('if(t[0]==="/"&&r&&r.message){', PAGE)
        self.assertIn('err.textContent=r.message; err.classList.add("okmsg");', PAGE)
        self.assertIn('.err.okmsg{color:var(--ok)}', PAGE)
        i = PAGE.index('$("chat_form").addEventListener("submit"')
        self.assertIn("catch(x){ err.textContent=x.message; }", PAGE[i:i + 1200])

    def test_help_button_sits_next_to_the_heading_and_only_while_open(self):
        """Das "i" steht in der Kopfzeile hinter der Überschrift (keine eigene Zeile darunter) und nur bei aufgeklapptem Bereich (Wunsch des Nutzers)."""
        self.assertIn('(head.querySelector(":scope>.sumh")||head).appendChild(b);', PAGE)
        self.assertNotIn('r.className="ibrow"', PAGE)
        self.assertIn("details:not([open])>summary .ibtn{display:none}", PAGE)

    def test_twitch_roles_per_browser_in_the_page(self):
        """Ohne eigene Anmeldung: nur lesen; Moderator meldet sich selbst an; der Streamer weist sich im neuen Browser aus."""
        self.assertIn('id="tw_acc_owner"', PAGE)
        self.assertIn('twAcc&&twAcc.role==="none"?{action:"start",as:"user"}:{action:"start",all:true}', PAGE)      # Zuschauer: nur Chat
        self.assertIn('id="tw_acc_asmod"', PAGE)
        self.assertIn('{action:"start",as:"mod"}', PAGE)                                                             # Moderator: Moderatorenrechte
        self.assertIn('a.role==="user"?`Angemeldet als ${a.login} · Zuschauer`', PAGE)
        self.assertIn('a.role==="none"&&!!a.streamer', PAGE)
        self.assertIn('hdr["x-pb-owner"]=pbOwnerKey()', PAGE)
        self.assertIn('"x-pb-owner":pbOwnerKey()', PAGE)                                    # auch der Chat-Abruf nennt den Schlüssel

    def test_chat_card_shows_the_channel_on_top_and_asks_for_it_when_missing(self):
        """Oben im Chat steht der Kanal (wie bei der Akku-Warnung, "Konto, auf dem gestreamt wird"); fehlt er, trägt man ihn dort ein. Nichts wird vorbelegt."""
        self.assertIn('id="tw_chrow" hidden', PAGE)
        self.assertIn('Kanal (Konto, auf dem gestreamt wird): ${a.streamer}', PAGE)
        self.assertIn('$("tw_ch_form").hidden=!a.need_channel;', PAGE)
        self.assertIn('go.hidden=wait||on||!!a.need_channel;', PAGE)
        self.assertIn('srtlaCall("POST","/api/twitch",{channel:v})', PAGE)
        self.assertNotIn('id="tw_ch_in" value=', PAGE)                                      # kein Name vorbelegt


class LiveAlwaysReachable(unittest.TestCase):
    def test_header_live_button_appears_when_the_footer_is_gone_for_good(self):
        """Am Handy steht Live/Stop in der Fußleiste; war sie per Einstellung ganz weg, fehlte der Knopf zum Live-Gehen (Meldung des Nutzers)."""
        self.assertIn("html:not(.mfperm) #hdr_live{display:none!important}", PAGE)
        self.assertNotIn("  #hdr_live{display:none!important}", PAGE)                            # nicht mehr bedingungslos weg
        self.assertIn('root.classList.toggle("mfperm",mode==="off"||(mode==="hide"&&wide))', PAGE)  # mfperm = Leiste dauerhaft weg (Ausblenden bei Chat/Vorschau, Immer ausblenden)

    def test_live_button_in_the_preview_card_while_not_sending(self):
        """Die Vorschau gibt es nur beim Senden; solange nicht gesendet wird, steht dort ein Knopf zum Live-Gehen."""
        self.assertIn('<button type="button" id="prev_live" class="livebtn" hidden>Live gehen</button>', PAGE)
        self.assertIn("pl.hidden=live||busy; pl.disabled=!d.can_start; pl.title=b.title;", PAGE)
        self.assertIn('$("prev_live").addEventListener("click",liveToggle);', PAGE)

    def test_header_label_is_short_on_phones(self):
        self.assertIn('matchMedia("(max-width:620px)").matches?"Live":"Live gehen"', PAGE)

    def test_phone_header_shows_only_the_logo_and_wide_centred_buttons(self):
        """Wunsch des Nutzers: am Handy dauerhaft das Logo (kein Wechsel mit dem Text), dafür breite, zentrierte Knöpfe."""
        self.assertIn("h1.hl{flex:0 0 auto}h1.hl .htxt{display:none}h1.hl .hlogo{animation:none;opacity:1}", PAGE)
        self.assertIn("display:inline-flex;align-items:center;justify-content:center;min-width:36px;min-height:34px;padding:3px 10px;line-height:1;text-align:center;white-space:nowrap}", PAGE)
        self.assertIn("header .hdr select{min-height:34px;text-align:center;text-align-last:center}", PAGE)
        i = PAGE.index("Knöpfe im Kopf etwas enger, damit auch mit Update-Punkt und Live-Knopf alles passt")   # schmale Handys: enger
        self.assertIn("min-width:30px;padding:3px 6px", PAGE[i:i + 400])


if __name__ == "__main__":
    unittest.main()
