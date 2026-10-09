#!/usr/bin/env python3
"""Tests für "Problem melden / Wunsch äußern" (Issue #13): Support-Nummern, Liste "Meine Fälle", vorbereitetes GitHub-Formular. Die Box sendet selbst nichts."""
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as _f:
    PAGE = _f.read()
with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as _f:
    SRC = _f.read()
BLK = PAGE[PAGE.index("// ---- Problem melden / Wunsch äußern"):PAGE.index("// ---- Controller-Tasten")]


def cases(path=None, now=1791500000):
    t = [now]
    return server.SupportCases(path or tempfile.mktemp(), clock=lambda: t[0]), t


class Cases(unittest.TestCase):
    def test_number_looks_generic_and_is_unique(self):
        c, _ = cases()
        ids = {c.create("Titel %d" % i)["id"] for i in range(60)}
        self.assertEqual(len(ids), 60)
        for i in ids:
            self.assertRegex(i, r"^IRL-[A-HJKMNP-Z2-9]{6}$")                      # keine verwechselbaren Zeichen (I, L, O, 0, 1)

    def test_wishes_get_a_number_too_and_the_kind_is_kept(self):
        """Issue #62: Auch ein Wunsch (FEATURE) bekommt eine Nummer und steht in "Meine Fälle"."""
        c, _ = cases()
        w = c.create("Mehr Emotes", "feature")
        p = c.create("Vorschau ruckelt")
        self.assertEqual((w["kind"], p["kind"]), ("feature", "issue"))
        self.assertRegex(w["id"], r"^IRL-[A-HJKMNP-Z2-9]{6}$")
        self.assertEqual({x["id"]: x["kind"] for x in c.list()}, {w["id"]: "feature", p["id"]: "issue"})
        with self.assertRaises(ValueError):
            c.create("Titel", "bug")
        with self.assertRaises(ValueError):
            c.create("Titel", None)

    def test_old_entries_without_kind_are_problems(self):
        path = tempfile.mktemp()
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"id": "IRL-ABCDEF", "title": "Alt", "t": 1791500000, "done": False}], f)
        c, _ = cases(path)
        self.assertEqual(c.list()[0]["kind"], "issue")

    def test_title_is_cleaned_and_limited(self):
        c, _ = cases()
        case = c.create("  Vorschau \n ruckelt\t am   Handy \x07 ")
        self.assertEqual(case["title"], "Vorschau ruckelt am Handy")
        self.assertEqual(len(c.create("x" * 500)["title"]), server.SupportCases.TITLE_MAX)
        for bad in ("", "ab", "   ", None, 5, ["x"]):
            with self.assertRaises(ValueError):
                c.create(bad)

    def test_list_is_newest_first_with_date_and_open_state(self):
        c, t = cases()
        a = c.create("Erster Fall")
        t[0] += 3600
        b = c.create("Zweiter Fall")
        lst = c.list()
        self.assertEqual([x["id"] for x in lst], [b["id"], a["id"]])
        self.assertEqual((lst[1]["t"], lst[0]["t"]), (1791500000, 1791503600))
        self.assertFalse(lst[0]["done"])

    def test_mark_done_and_reopen_only_locally(self):
        c, _ = cases()
        a = c.create("Fall")
        self.assertTrue(c.mark(a["id"], True)["done"])
        self.assertFalse(c.mark(a["id"], False)["done"])
        with self.assertRaises(KeyError):
            c.mark("IRL-ABCDEF", True)
        for bad in (("irl-abcdef", True), ("IRL-ABCDE", True), (5, True), (a["id"], "ja"), (a["id"], 1)):
            with self.assertRaises(ValueError):
                c.mark(*bad)

    def test_survives_a_restart_and_ignores_broken_entries(self):
        path = tempfile.mktemp()
        c, _ = cases(path)
        a = c.create("Bleibt")
        c.mark(a["id"], True)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data += [{"id": "kaputt", "title": "x", "t": 1}, "text", {"id": "IRL-ABCDEF", "title": 5, "t": 1}]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        c2, _ = cases(path)
        self.assertEqual([x["id"] for x in c2.list()], [a["id"]])
        self.assertTrue(c2.list()[0]["done"])

    def test_list_is_bounded(self):
        c, _ = cases()
        for i in range(server.SupportCases.KEEP + 20):
            c.create("Fall %d" % i)
        self.assertEqual(len(c.list()), server.SupportCases.KEEP)
        self.assertEqual(c.list()[0]["title"], "Fall %d" % (server.SupportCases.KEEP + 19))

    def test_unwritable_folder_does_not_crash(self):
        c, _ = cases("/proc/gibt/es/nicht.json")
        self.assertTrue(c.create("Trotzdem")["id"])


class DeleteAndNumbers(unittest.TestCase):
    """Issue #64: Fälle bleiben erhalten; Entfernen nur kurz nach dem Anlegen; Nummer schon im Link, Eintrag erst beim Öffnen."""

    def test_number_chosen_by_the_page_is_used_if_valid_and_free(self):
        c, _ = cases()
        a = c.create("Titel eins", "issue", "IRL-ABCDEF")
        self.assertEqual(a["id"], "IRL-ABCDEF")
        for bad in ("IRL-ABCDEF", "irl-abcdef", "IRL-ABCDE", "IRL-ABCDE0", "XYZ", 5):
            with self.assertRaises(ValueError):
                c.create("Titel zwei", "issue", bad)                                          # doppelt oder ungültig

    def test_entry_can_be_removed_only_in_the_first_30_minutes(self):
        c, t = cases()
        a = c.create("Falsch angelegt")
        t[0] += 29 * 60
        self.assertEqual(c.delete(a["id"]), {"ok": True})
        self.assertEqual(c.list(), [])
        b = c.create("Bleibt")
        t[0] += 31 * 60
        with self.assertRaises(ValueError) as cm:
            c.delete(b["id"])
        self.assertIn("Danach bleibt der Eintrag erhalten", str(cm.exception))
        self.assertEqual(len(c.list()), 1)                                                    # auch ein erledigter Fall bleibt erhalten
        c.mark(b["id"], True)
        with self.assertRaises(ValueError):
            c.delete(b["id"])

    def test_delete_validates_the_request(self):
        c, _ = cases()
        with self.assertRaises(ValueError):
            c.delete("kaputt")
        with self.assertRaises(KeyError):
            c.delete("IRL-ABCDEF")

    def test_box_never_touches_github_when_removing(self):
        cls = SRC[SRC.index("    def delete(self, cid):"):SRC.index("    def mark(self, cid, done):")]
        for needle in ("urllib", "http", "socket", "token"):
            self.assertNotIn(needle, cls, needle)

    def test_list_answer_carries_the_clock_and_the_window(self):
        self.assertIn('"now": int(time.time()), "delete_window": int(self.support.DELETE_WINDOW)', SRC)
        self.assertEqual(server.SupportCases.DELETE_WINDOW, 1800.0)


class Routes(unittest.TestCase):
    def test_routes_exist_and_need_login(self):
        self.assertIn('if path == "/api/support":', SRC)
        self.assertIn("Handler.support = SupportCases(", SRC)
        self.assertIn('self.support.create(d.get("title"), d.get("kind", "issue"), d.get("id"))', SRC)
        self.assertIn('self.support.delete(d.get("id"))', SRC)
        self.assertIn('self.support.mark(d.get("id"), d.get("done"))', SRC)

    def test_box_never_talks_to_github_itself(self):
        cls = SRC[SRC.index("class SupportCases:"):SRC.index("class UiAccess:")]
        for needle in ("urllib", "http://", "https://", "socket", "token", "Authorization"):
            self.assertNotIn(needle, cls, needle)                                    # kein Netz, kein Schlüssel


class Page(unittest.TestCase):
    def test_card_and_texts(self):
        self.assertIn('id="c_report"', PAGE)
        self.assertIn("Problem melden / Wunsch äußern", PAGE)
        self.assertIn("Die Box sendet selbst nichts.", PAGE)
        self.assertIn('value="issue" checked', PAGE)
        self.assertIn('value="feature"', PAGE)

    def test_titles_follow_the_requested_form(self):
        self.assertIn('ttl=(f?"[FEATURE] ":"[ISSUE] ")+t+" ("+id+")";', BLK)               # "[ISSUE] Titeltext (Nummer)" und "[FEATURE] Titeltext (Nummer)"
        self.assertIn('{action:"create",kind:n.f?"feature":"issue",title:n.t,id:n.id}', BLK)
        self.assertIn('if(f&&!body0){ msg.textContent="Bei einem Wunsch ist die Beschreibung Pflicht."', BLK)
        self.assertIn("t.length<3", BLK)                                              # Titel immer Pflicht

    def test_link_opens_in_a_new_tab_and_url_is_limited(self):
        self.assertIn('a.target="_blank"; a.rel="noopener noreferrer"', BLK)
        self.assertIn('"https://github.com/IRL4YOU/irl4you-pip/issues/new"', BLK)
        self.assertIn("MAXURL=7000", BLK)
        self.assertIn("encodeURIComponent(body", BLK)

    def test_log_is_a_file_to_attach_not_part_of_the_link(self):
        self.assertIn("logsDownloadTo(go,lm)", BLK)
        self.assertIn("async function logsDownloadTo(btn,msg)", PAGE)
        self.assertIn('const logsDownload=()=>logsDownloadTo($("lg_dl"),$("lg_msg"));', PAGE)

    def test_list_shows_problem_or_wish(self):
        self.assertIn('kd.textContent=c.kind==="feature"?"Wunsch":"Problem"', BLK)

    def test_prepare_saves_nothing_and_the_link_is_rebuilt_on_click(self):
        """Issue #64: "Formular vorbereiten" legte jedes Mal einen Eintrag an, und der Link konnte alte Daten enthalten."""
        i = BLK.index('go.addEventListener("click"')
        prepare = BLK[i:BLK.index("card.addEventListener", i)]
        self.assertNotIn('"/api/support"', prepare)                                          # vorbereiten speichert nichts
        self.assertIn("a.href=n.url;", BLK)                                                   # beim Klick aus den aktuellen Feldern neu gebaut
        self.assertIn("if(!draft.saved){", BLK)                                               # der Eintrag entsteht erst beim Öffnen, nur einmal je Nummer
        self.assertIn("(draft.saved&&t!==draft.title)", BLK)                                  # anderer Titel nach dem Öffnen: neuer Fall, neue Nummer

    def test_remove_button_only_inside_the_window_and_reopen_label(self):
        self.assertIn('if(age-c.t<=win){', BLK)
        self.assertIn('x.dataset.del="1"; x.textContent="Entfernen";', BLK)
        self.assertIn('{action:"delete",id:b.dataset.id}', BLK)
        self.assertIn('c.done?"Wieder öffnen":"Erledigt"', BLK)
        self.assertNotIn("Wieder offen", PAGE)

    def test_cases_can_be_searched_on_github_and_marked_done(self):
        self.assertIn('"?q="+encodeURIComponent("is:issue "+c.id)', BLK)
        self.assertIn('{action:"done",id:b.dataset.id,done:b.dataset.done==="1"}', BLK)
        self.assertIn("Erledigt", BLK)

    def test_user_text_is_never_inserted_as_html(self):
        self.assertNotIn("innerHTML=c.", BLK)
        self.assertNotIn("innerHTML=t", BLK)
        self.assertIn("t.textContent=c.title", BLK)


if __name__ == "__main__":
    unittest.main()
