"""Tests für den Bereich "Chat" (server.TwitchReader): Zerlegen der IRC-Zeilen von Twitch, Bereinigung, Abfrage mit Nummer, Vorschau-Modus und eine
Verbindung gegen einen lokalen Fake-Server. Es wird nie eine Verbindung zu twitch.tv aufgebaut."""
import json
import os
import socket
import socketserver
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402


class FakeStore:
    def __init__(self, channel="testkanal"):
        self.data = {"channel": channel}


def reader(channel="testkanal", **kw):
    return server.TwitchReader(FakeStore(channel), **kw)


class Parsing(unittest.TestCase):
    def test_normal_message_with_color_and_emotes(self):
        line = ("@badges=moderator/1;color=#1E90FF;display-name=Anna_Streams;emotes=25:0-4,12-16/1902:6-10;bits=100 "
                ":anna_streams!anna_streams@anna_streams.tmi.twitch.tv PRIVMSG #kanal :Kappa Keepo Kappa hi")
        item = reader().parse(line)
        self.assertEqual(item["name"], "Anna_Streams")
        self.assertEqual(item["color"], "#1E90FF")
        self.assertEqual(item["text"], "Kappa Keepo Kappa hi")
        self.assertEqual(item["emotes"], [["25", 0, 4], ["1902", 6, 10], ["25", 12, 16]])
        self.assertEqual(item["bits"], 100)
        self.assertEqual(item["type"], "msg")

    def test_new_twitch_emote_ids_with_41_characters_are_kept(self):
        """Kanal-, Abo-, Follower- und bewegte Emotes haben Kennungen wie "emotesv2_" + 32 Zeichen (41): Bis 0.9.179 wurden sie wegen der Grenze von 40 verworfen."""
        eid = "emotesv2_dcd06b30a5c24f6eb871e8f5edbd44f7"
        self.assertEqual(len(eid), 41)
        line = ("@display-name=Anna;emotes=%s:0-4,10-14/25:6-8 :anna!anna@anna.tmi.twitch.tv PRIVMSG #kanal :Hallo Kpa Hallo" % eid)
        item = reader().parse(line)
        self.assertEqual(item["emotes"], [[eid, 0, 4], ["25", 6, 8], [eid, 10, 14]])

    def test_emote_id_limits(self):
        r = reader()
        ok = "a" * 64
        self.assertEqual(r.parse("@emotes=%s:0-1 :a!a@a.tmi.twitch.tv PRIVMSG #kanal :hi" % ok)["emotes"], [[ok, 0, 1]])
        self.assertEqual(r.parse("@emotes=%s:0-1 :a!a@a.tmi.twitch.tv PRIVMSG #kanal :hi" % ("a" * 65))["emotes"], [])         # zu lang
        for bad in ("a.b", "a-b", "a%2Fb", "emotesv2_äö", ""):
            self.assertEqual(r.parse("@emotes=%s:0-1 :a!a@a.tmi.twitch.tv PRIVMSG #kanal :hi" % bad)["emotes"], [], bad)

    def test_page_builds_the_image_address_from_the_id(self):
        page = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()
        self.assertIn('static-cdn.jtvnw.net/emoticons/v2/%s/default/dark/1.0', page)                  # "default" liefert bewegte Emotes als GIF
        i = page.index("EMOTE.replace(")
        self.assertIn("encodeURIComponent", page[i:i + 120])                                          # die Kennung wird für die Adresse kodiert

    def test_twitch_timestamp_is_used_for_the_time_of_the_message(self):
        r = reader()
        item = r.parse("@tmi-sent-ts=1791403200123;display-name=Zeit :z!z@z.tmi.twitch.tv PRIVMSG #kanal :Uhr")
        self.assertEqual(item["ts"], 1791403200)
        r._add(item)
        self.assertEqual(r.items[-1]["t"], 1791403200)
        self.assertNotIn("ts", r.parse(":z!z@z.tmi.twitch.tv PRIVMSG #kanal :ohne Tag"))
        self.assertNotIn("ts", r.parse("@tmi-sent-ts=abc;x=y :z!z@z.tmi.twitch.tv PRIVMSG #kanal :kaputt"))

    def test_message_ids_and_deletion_events_are_carried(self):
        r = reader()
        msg = r.parse("@id=abcdef12-3456-7890-abcd-ef1234567890;user-id=777;display-name=Bob :bob!bob@bob.tmi.twitch.tv PRIVMSG #kanal :hallo")
        self.assertEqual((msg["mid"], msg["uid"], msg["login"]), ("abcdef12-3456-7890-abcd-ef1234567890", "777", "bob"))
        d = r.parse("@login=bob;target-msg-id=abcdef12-3456-7890-abcd-ef1234567890 :tmi.twitch.tv CLEARMSG #kanal :hallo")
        self.assertEqual((d["type"], d["mid"], d["meta"]), ("del", "abcdef12-3456-7890-abcd-ef1234567890", True))
        c = r.parse("@ban-duration=600;target-user-id=777 :tmi.twitch.tv CLEARCHAT #kanal :bob")
        self.assertEqual((c["type"], c["uid"], c["seconds"]), ("clear", "777", 600))
        perm = r.parse("@target-user-id=777 :tmi.twitch.tv CLEARCHAT #kanal :bob")
        self.assertEqual(perm["seconds"], 0)                                      # kein ban-duration = dauerhaft
        self.assertIsNone(r.parse("@target-msg-id=../../etc :tmi.twitch.tv CLEARMSG #kanal :x"))         # ungültige Kennung
        junk = r.parse("@id=x;user-id=12ab :b!b@b.tmi.twitch.tv PRIVMSG #kanal :t")
        self.assertNotIn("mid", junk)
        self.assertNotIn("uid", junk)

    def test_badges_tell_streamer_moderator_vip_and_subscriber_apart(self):
        r = reader()
        line = "@badges=moderator/1,subscriber/12,bits/100,evil<script>/1;display-name=Mo :mo!mo@mo.tmi.twitch.tv PRIVMSG #kanal :hi"
        self.assertEqual(r.parse(line)["badges"], ["moderator", "subscriber"])                 # nur bekannte Abzeichen, in fester Reihenfolge
        self.assertEqual(r.parse("@badges=broadcaster/1 :b!b@b.tmi.twitch.tv PRIVMSG #kanal :hi")["badges"], ["broadcaster"])
        self.assertEqual(r.parse(":x!x@x.tmi.twitch.tv PRIVMSG #kanal :hi")["badges"], [])

    def test_action_message(self):
        item = reader().parse(":bob!bob@bob.tmi.twitch.tv PRIVMSG #kanal :\x01ACTION winkt\x01")
        self.assertEqual(item["type"], "me")
        self.assertEqual(item["text"], "winkt")
        self.assertEqual(item["name"], "bob")

    def test_subscription_and_raid_notices(self):
        sub = reader().parse("@msg-id=resub;system-msg=Lena\\ssubscribed\\sfor\\s3\\smonths. :tmi.twitch.tv USERNOTICE #kanal :Tolle Show")
        self.assertEqual(sub["type"], "sub")
        self.assertEqual(sub["text"], "Lena subscribed for 3 months. – Tolle Show")
        raid = reader().parse("@msg-id=raid;system-msg=12\\sraiders\\sfrom\\sMax :tmi.twitch.tv USERNOTICE #kanal")
        self.assertEqual(raid["type"], "raid")

    def test_special_events_carry_kind_and_amount(self):
        r = reader()
        sub = r.parse("@msg-id=resub;msg-param-cumulative-months=7;system-msg=Lena\\ssubscribed. :tmi.twitch.tv USERNOTICE #kanal :Tolle Show")
        self.assertEqual(sub["ev"], {"k": "sub", "n": 7})
        self.assertEqual(r.parse("@msg-id=sub;system-msg=Neu :tmi.twitch.tv USERNOTICE #kanal")["ev"], {"k": "sub", "n": 0})
        raid = r.parse("@msg-id=raid;msg-param-viewerCount=42;system-msg=42\\sraiders :tmi.twitch.tv USERNOTICE #kanal")
        self.assertEqual(raid["ev"], {"k": "raid", "n": 42})
        mass = r.parse("@msg-id=submysterygift;msg-param-mass-gift-count=5;system-msg=Max\\sgifted\\s5. :tmi.twitch.tv USERNOTICE #kanal")
        self.assertEqual(mass["ev"], {"k": "gift", "n": 5})
        self.assertEqual(r.parse("@msg-id=subgift;system-msg=Max\\sgifted\\sone. :tmi.twitch.tv USERNOTICE #kanal")["ev"], {"k": "gift", "n": 1})
        part = r.parse("@msg-id=subgift;msg-param-community-gift-id=123;system-msg=Max\\sgifted\\sone. :tmi.twitch.tv USERNOTICE #kanal")
        self.assertIsNone(part)                                                                  # Teil einer Sammelaktion: nur die Sammelmeldung zählt
        cheer = r.parse("@bits=500;display-name=Lena :lena!lena@lena.tmi.twitch.tv PRIVMSG #kanal :cheer500 Weiter so")
        self.assertEqual(cheer["ev"], {"k": "cheer", "n": 500})
        self.assertEqual(cheer["type"], "msg")
        self.assertNotIn("ev", r.parse(":bob!bob@bob.tmi.twitch.tv PRIVMSG #kanal :hallo"))
        junk = r.parse("@msg-id=raid;msg-param-viewerCount=99999999999999;system-msg=x :tmi.twitch.tv USERNOTICE #kanal")
        self.assertEqual(junk["ev"], {"k": "raid", "n": 0})                                        # unsinnige Zahl: 0, nie ein Fehler

    def test_hostile_values_are_neutralised(self):
        line = ("@color=red;display-name=Mallory\x07;emotes=a%b:0-3,9-99/ok_1:0-1 "
                ":m!m@m.tmi.twitch.tv PRIVMSG #kanal :<img src=x onerror=alert(1)>\x1b[31m rot")
        item = reader().parse(line)
        self.assertEqual(item["color"], "")                                    # kein gültiger Farbwert
        self.assertNotIn("\x07", item["name"])
        self.assertNotIn("\x1b", item["text"])
        self.assertEqual(item["emotes"], [["ok_1", 0, 1]])                    # ungültige Kennung und Bereich außerhalb weg
        self.assertTrue(item["text"].startswith("<img"))                      # bleibt Text; die Oberfläche setzt textContent

    def test_long_text_and_other_commands(self):
        item = reader().parse(":x!x@x.tmi.twitch.tv PRIVMSG #kanal :" + "a" * 2000)
        self.assertEqual(len(item["text"]), 500)
        for other in ("PING :tmi.twitch.tv", ":tmi.twitch.tv 001 justinfan1 :Welcome", ":x!x@x.tmi.twitch.tv JOIN #kanal", ""):
            self.assertIsNone(reader().parse(other))


class Polling(unittest.TestCase):
    def test_no_channel_means_no_connection(self):
        r = reader(channel="")
        out = r.poll(0)
        self.assertEqual(out["state"], "kein-kanal")
        self.assertIsNone(r.thread)

    def test_the_channel_of_the_signed_in_account_is_used_when_none_is_entered(self):
        import tempfile
        import types
        store = server.TwitchStore(os.path.join(tempfile.mkdtemp(), "twitch.json"))
        store.account = types.SimpleNamespace(login=lambda: "meinkonto")       # per Geräte-Code angemeldet, im Feld steht nichts
        r = server.TwitchReader(store)
        r._ensure = lambda ch: setattr(r, "channel", ch)                       # keine Verbindung aufbauen
        out = r.poll(0)
        self.assertEqual((out["state"], out["channel"]), (r.state, "meinkonto"))
        self.assertNotEqual(out["state"], "kein-kanal")
        store.account = None
        self.assertEqual(r.poll(0)["state"], "kein-kanal")                     # ohne Konto und ohne Eintrag weiter: kein Kanal
        store.set({"channel": "Anderer_Kanal"})
        self.assertEqual(r.poll(0)["channel"], "anderer_kanal")                 # der eingetragene Kanal geht vor

    def test_since_returns_only_new_items_and_numbers_restart_cleanly(self):
        r = reader()
        for i in range(5):
            r._add({"type": "msg", "name": "n", "color": "", "text": "t%d" % i, "emotes": []})
        with unittest.mock.patch.object(r, "_ensure"):
            out = r.poll(2)
            self.assertEqual([i["id"] for i in out["items"]], [3, 4, 5])
            self.assertEqual(out["last"], 5)
            out = r.poll(99)                                                   # Box neu gestartet: Nummer aus der Zukunft -> alles von vorn
            self.assertEqual(len(out["items"]), 5)

    def test_buffer_keeps_only_the_newest(self):
        r = reader()
        for i in range(r.KEEP + 20):
            r._add({"type": "msg", "name": "n", "color": "", "text": str(i), "emotes": []})
        self.assertEqual(len(r.items), r.KEEP)
        self.assertEqual(r.items[0]["text"], "20")

    def test_demo_mode_makes_messages_without_network(self):
        now = [100.0]
        r = reader(channel="", demo=True, clock=lambda: now[0])
        with unittest.mock.patch("socket.create_connection", side_effect=AssertionError("kein Netz im Vorschau-Modus")):
            self.assertEqual(r.poll(0)["channel"], "demo")
            now[0] += 3
            out = r.poll(0)
        self.assertGreaterEqual(len(out["items"]), 2)
        self.assertEqual(out["state"], "ok")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.server.seen = []
        for _ in range(2):                                                   # PASS und NICK
            self.server.seen.append(self.rfile.readline().decode().strip())
        self.wfile.write(b":tmi.twitch.tv 001 justinfan1 :Welcome\r\n")
        for _ in range(2):                                                   # CAP REQ und JOIN
            self.server.seen.append(self.rfile.readline().decode().strip())
        self.wfile.write(b"PING :tmi.twitch.tv\r\n")
        self.wfile.write(b"@display-name=Anna;color=#FF4500 :anna!anna@anna.tmi.twitch.tv PRIVMSG #testkanal :hallo welt\r\n")
        self.wfile.flush()
        time.sleep(0.6)


class Live(unittest.TestCase):
    def test_reads_anonymously_from_a_local_irc_server(self):
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = reader(host="127.0.0.1", port=srv.server_address[1], tls=False)
            r.IDLE = 5.0
            out = r.poll(0)
            self.assertEqual(out["state"], "verbinde")
            deadline = time.time() + 5
            while time.time() < deadline and not r.items:
                time.sleep(0.05)
            out = r.poll(0)
            self.assertEqual([i["text"] for i in out["items"]], ["hallo welt"])
            self.assertTrue(srv.seen[0].startswith("PASS SCHMOOPIIE"))        # anonym: nie ein Token
            self.assertRegex(srv.seen[1], r"^NICK justinfan\d{5}$")
            self.assertEqual(srv.seen[3], "JOIN #testkanal")
        finally:
            r.IDLE = 0.0                                                      # Hintergrundteil beenden
            srv.shutdown()
            srv.server_close()

    def test_connection_is_dropped_when_nobody_asks(self):
        r = reader(host="127.0.0.1", port=1, tls=False, sleep=lambda s: time.sleep(0.01))
        r.IDLE = 0.3
        r.poll(0)
        t = r.thread
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(r.state, "aus")


class ThirdParty(unittest.TestCase):
    BTTV, STV, FFZ = "54fa8f1401e468494b85b537", "01FGH8NE3800064MEQW00DNBNG", "9"

    def fake(self, url):
        if "betterttv.net/3/cached/emotes/global" in url:
            return [{"id": self.BTTV, "code": "OMEGALUL"}, {"id": "../../x", "code": "Boese"}, {"id": self.BTTV, "code": "kaputt name"}]
        if "betterttv.net/3/cached/users/twitch/55" in url:
            return {"channelEmotes": [{"id": self.BTTV, "code": "KanalEmote"}], "sharedEmotes": []}
        if "frankerfacez.com/v1/set/global" in url:
            return {"sets": {"3": {"emoticons": [{"id": 9, "name": "ZreknarF"}]}}}
        if "7tv.io/v3/emote-sets/global" in url:
            return {"emotes": [{"id": self.STV, "name": "Sevenhead"}]}
        if "7tv.io/v3/users/twitch/55" in url:
            return {"emote_set": {"emotes": [{"id": self.STV, "name": "Kanal7"}]}}
        return None

    def loaded(self, room="55"):
        tp = server.ThirdPartyEmotes(fetch=self.fake)
        tp.ensure(room)
        tp.thread.join(5)
        return tp

    def test_names_become_urls_built_only_from_checked_ids(self):
        m = self.loaded().snapshot()
        self.assertEqual(m["OMEGALUL"], "https://cdn.betterttv.net/emote/%s/2x.webp" % self.BTTV)
        self.assertEqual(m["Kanal7"], "https://cdn.7tv.app/emote/%s/2x.webp" % self.STV)
        self.assertEqual(m["ZreknarF"], "https://cdn.frankerfacez.com/emote/9/2")
        self.assertNotIn("Boese", m)                                                          # ungültige Kennung
        self.assertNotIn("kaputt name", m)                                                    # ungültiger Name
        for u in m.values():
            self.assertRegex(u, server.ThirdPartyEmotes.HOSTS)

    def test_words_in_a_message_get_marked_and_native_emotes_win(self):
        tp = self.loaded()
        item = {"text": "hi OMEGALUL und Kanal7 Kappa", "emotes": [["25", 25, 29]]}
        tp.mark(item)
        words = [(e[1], e[2]) for e in item["emotes"]]
        self.assertEqual(words, [(3, 10), (16, 21), (25, 29)])
        item2 = {"text": "OMEGALUL", "emotes": [["999", 0, 7]]}
        tp.mark(item2)
        self.assertEqual(item2["emotes"], [["999", 0, 7]])                                    # Twitch-eigenes Emote an derselben Stelle bleibt
        plain = {"text": "nichts dabei", "emotes": []}
        tp.mark(plain)
        self.assertEqual(plain["emotes"], [])

    def test_reader_uses_room_id_from_the_message_tags(self):
        r = reader()
        r.third = server.ThirdPartyEmotes(fetch=self.fake)
        first = r.parse("@room-id=55;display-name=A :a!a@a.tmi.twitch.tv PRIVMSG #kanal :Kanal7 hallo")
        self.assertEqual(first["emotes"], [])                                                 # Liste wird erst geladen (Hintergrund)
        r.third.thread.join(5)
        second = r.parse("@room-id=55;display-name=A :a!a@a.tmi.twitch.tv PRIVMSG #kanal :Kanal7 hallo")
        self.assertEqual(len(second["emotes"]), 1)
        self.assertTrue(second["emotes"][0][0].startswith("https://cdn.7tv.app/"))

    def test_failed_download_keeps_words_as_text(self):
        tp = server.ThirdPartyEmotes(fetch=lambda u: None)
        tp.ensure("55")
        tp.thread.join(5)
        item = {"text": "OMEGALUL", "emotes": []}
        tp.mark(item)
        self.assertEqual(item["emotes"], [])


class Review(unittest.TestCase):
    """Befunde der Gegenlese-Prüfung vom 8. Okt 2026."""

    def test_emote_positions_follow_the_text_cleaning(self):
        r = reader()
        item = r.parse("@emotes=25:6-10 :a!a@a.tmi.twitch.tv PRIVMSG #kanal :a  b  Kappa")
        self.assertEqual(item["text"], "a b Kappa")
        self.assertEqual(item["emotes"], [["25", 4, 8]])                       # Stelle im bereinigten Text, nicht im ursprünglichen
        again = r.parse("@emotes=25:0-4,7-11 :a!a@a.tmi.twitch.tv PRIVMSG #kanal :Kappa  Kappa")
        self.assertEqual([e[1:] for e in again["emotes"]], [[0, 4], [6, 10]])

    def test_a_viewer_named_reconnect_does_not_drop_the_connection(self):
        line = "@display-name=RECONNECT;color=#FF0000 :x!x@x.tmi.twitch.tv PRIVMSG #kanal :hallo"
        self.assertNotEqual(server.TwitchChat._parse(line)[0], "RECONNECT")
        self.assertEqual(server.TwitchChat._parse(":tmi.twitch.tv RECONNECT")[0], "RECONNECT")

    def test_odd_digit_characters_never_raise(self):
        r = reader()
        item = r.parse("@bits=²;user-id=²;emotes=25:²-4;tmi-sent-ts=²;display-name=A :a!a@a.tmi.twitch.tv PRIVMSG #kanal :hallo")
        self.assertEqual(item["text"], "hallo")
        self.assertNotIn("bits", item)
        self.assertEqual(item["emotes"], [])

    def test_new_channel_gets_a_new_generation_and_the_old_thread_stops(self):
        r = reader()
        starts = []
        r._run = lambda ch, gen: starts.append((ch, gen))
        r._ensure("a"); r.thread.join(2)
        r._ensure("b"); r.thread.join(2)
        r._ensure("a"); r.thread.join(2)
        self.assertEqual([s[1] for s in starts], [1, 2, 3])
        self.assertTrue(r._mine("a", 3))
        self.assertFalse(r._mine("a", 1))                                      # der erste Faden für "a" ist veraltet, auch wenn der Kanal wieder "a" heißt
        r.items.clear()
        r._add({"type": "msg", "text": "alt", "emotes": []}, 1)
        self.assertEqual(len(r.items), 0)                                      # Nachzügler eines alten Fadens werden verworfen

    def test_partial_emote_download_keeps_the_good_list(self):
        full = {"https://api.betterttv.net/3/cached/emotes/global": [{"id": "54fa8f1401e468494b85b537", "code": "Gut"}]}
        tp = server.ThirdPartyEmotes(fetch=lambda u: full.get(u, False))      # False = nicht gefunden: in Ordnung
        tp.ensure("55"); tp.thread.join(5)
        self.assertIn("Gut", tp.snapshot())
        tp.until = 0.0
        tp.fetch = lambda u: None                                              # jetzt scheitert alles
        tp.ensure("55"); tp.thread.join(5)
        self.assertIn("Gut", tp.snapshot())                                    # die gute Liste bleibt
        self.assertLess(tp.until - tp.clock(), tp.RETRY + 1)                   # und es wird bald neu versucht

    def test_message_text_allows_emoji_sequences_and_nbsp_but_not_controls(self):
        c = server.TwitchChat.clean_text
        self.assertEqual(c("hallo\u00a0welt"), "hallo welt")
        self.assertIsNotNone(c("👨\u200d👩\u200d👧 Familie"))
        self.assertIsNone(c("zeile\nzwei"))
        self.assertIsNone(c("rück\u202ewärts"))
        self.assertIsNone(c("/ban jemand"))


class SilentHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.server.seen = []
        for _ in range(2):
            self.server.seen.append(self.rfile.readline().decode().strip())
        self.wfile.write(b":tmi.twitch.tv 001 justinfan1 :Welcome\r\n")
        self.wfile.flush()
        while True:                                                          # danach still: liest nur noch mit
            line = self.rfile.readline()
            if not line:
                return
            self.server.seen.append(line.decode().strip())


class Silent(unittest.TestCase):
    def test_silent_connection_is_probed_and_then_given_up_on(self):
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SilentHandler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        failed = []
        try:
            r = reader(host="127.0.0.1", port=srv.server_address[1], tls=False)
            r.PROBE, r.STALE, r.IDLE = 0.4, 1.6, 30.0
            r.paths.fail = lambda src, secs: failed.append(src)
            r.poll(0)
            deadline = time.time() + 8
            while time.time() < deadline and not failed:
                time.sleep(0.05)
            self.assertTrue(failed, "der stille Weg wurde nicht aufgegeben")
            self.assertIn("PING :pipbox", srv.seen)                           # die Box hat selbst nachgefragt
        finally:
            r.IDLE = 0.0
            srv.shutdown()
            srv.server_close()


class Paths(unittest.TestCase):
    def test_best_link_first_failed_link_last_default_route_always_at_the_end(self):
        import tempfile
        now = [0.0]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("08:00:00 links: 10.0.0.2 srtt=60ms var=0 peak=60 guete=60 genutzt in_flight=1 pkts_5s=5\n"
                    "08:00:00 links: 192.168.1.2 srtt=2ms var=1 peak=5 guete=7 genutzt in_flight=1 pkts_5s=900\n"
                    "08:00:00 links: 172.16.0.2 srtt=300ms var=9 peak=400 guete=300 reserve in_flight=0 pkts_5s=0\n")
        p = server.ChatPaths(lambda: ["10.0.0.2", "172.16.0.2", "192.168.1.2"], clock=lambda: now[0], links_file=f.name)
        self.assertEqual(p.order(), ["192.168.1.2", "10.0.0.2", "172.16.0.2", None])        # beste Güte zuerst, Reserve nach den genutzten
        p.fail("192.168.1.2", 100)
        self.assertEqual(p.order(), ["10.0.0.2", "172.16.0.2", None, "192.168.1.2"])        # versagt: hinter die normale Route
        now[0] += 101
        self.assertEqual(p.order()[0], "192.168.1.2")                                       # nach der Sperrzeit wieder vorn
        os.unlink(f.name)
        self.assertEqual(server.ChatPaths().order(), [None])                                # ohne Wege nur die normale Route

    def test_connect_skips_a_link_that_cannot_be_used_and_remembers_it(self):
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = server.ChatPaths(lambda: ["203.0.113.77"])                                   # keine Adresse dieser Maschine: Binden schlägt fehl
            sock, src = p.connect("127.0.0.1", srv.server_address[1], timeout=2)
            sock.close()
            self.assertIsNone(src)                                                           # ging über die normale Route
            self.assertEqual(p.order()[-2:], [None, "203.0.113.77"])                         # der kaputte Weg steht jetzt hinter der normalen Route
        finally:
            srv.shutdown()
            srv.server_close()

    def test_http_request_falls_back_too(self):
        import http.server

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = server.ChatPaths(lambda: ["203.0.113.77"])
            self.assertEqual(p.request("GET", "http://127.0.0.1:%d/x" % srv.server_address[1]), (200, b"ok"))
            self.assertEqual(server.ChatPaths().request("GET", "http://127.0.0.1:1/x", timeout=1), (0, b""))   # nirgends erreichbar
        finally:
            srv.shutdown()
            srv.server_close()


import unittest.mock  # noqa: E402

class ChatterEmotes(unittest.TestCase):
    """Emotes aus der öffentlichen Liste des Schreibers (BTTV/7TV, ohne Anmeldung): catKISS stand in der Liste von Knochi, nicht in der des Kanals."""
    BTTV, STV = "54fa8f1401e468494b85b537", "01FGH8NE3800064MEQW00DNBNG"

    def fake(self, url):
        if "betterttv.net/3/cached/users/twitch/501025597" in url:
            return {"channelEmotes": [], "sharedEmotes": [{"id": self.BTTV, "code": "catKISS"}, {"id": "../x", "code": "Boese"}]}
        if "7tv.io/v3/users/twitch/501025597" in url:
            return {"emote_set": {"emotes": [{"id": self.STV, "name": "Mein7"}]}}
        return None

    def wait(self, tp):
        for _ in range(100):
            if tp.uthread is None:
                return
            time.sleep(0.05)

    def test_list_of_the_chatter_is_loaded_once_and_marks_later_messages(self):
        tp = server.ThirdPartyEmotes(fetch=self.fake)
        first = {"text": "catKISS Mein7", "emotes": [], "uid": "501025597"}
        tp.mark(first)
        self.assertEqual(first["emotes"], [])                                                 # Liste wird erst geladen
        self.wait(tp)
        second = {"text": "catKISS Mein7 hallo", "emotes": [], "uid": "501025597"}
        tp.mark(second)
        self.assertEqual([(e[1], e[2]) for e in second["emotes"]], [(0, 6), (8, 12)])
        self.assertTrue(second["emotes"][0][0].startswith("https://cdn.betterttv.net/emote/"))
        self.assertTrue(second["emotes"][1][0].startswith("https://cdn.7tv.app/emote/"))
        self.assertNotIn("Boese", tp.user_map("501025597"))                                   # ungültige Kennung

    def test_other_chatters_do_not_get_it(self):
        tp = server.ThirdPartyEmotes(fetch=self.fake)
        tp.want_user("501025597"); self.wait(tp)
        item = {"text": "catKISS", "emotes": [], "uid": "42"}
        tp.mark(item)
        self.assertEqual(item["emotes"], [])

    def test_bad_uid_is_never_requested(self):
        seen = []
        tp = server.ThirdPartyEmotes(fetch=lambda u: seen.append(u))
        for bad in ("", "abc", "1" * 13, "12/../x", None, 5):
            tp.want_user(bad)
        self.assertEqual(seen, [])
        self.assertEqual(len(tp.uq), 0)

    def test_failed_download_is_retried_later_not_every_message(self):
        calls = []
        tp = server.ThirdPartyEmotes(fetch=lambda u: calls.append(u))
        tp.want_user("7"); self.wait(tp)
        n = len(calls)
        tp.want_user("7"); self.wait(tp)
        self.assertEqual(len(calls), n)                                                       # nicht sofort wieder (RETRY)

    def test_cache_and_queue_are_bounded(self):
        tp = server.ThirdPartyEmotes(fetch=lambda u: {})
        tp.USER_MAX = 5
        for i in range(12):
            tp.want_user(str(i + 1)); self.wait(tp)
        self.assertLessEqual(len(tp.users), 5)

    def test_reader_patches_the_message_already_shown(self):
        tp = server.ThirdPartyEmotes(fetch=self.fake)
        r = server.TwitchReader(None, third=tp)
        mid = "11111111-2222-4333-8444-555555555555"
        it = r.parse("@room-id=55;user-id=501025597;id=%s;display-name=Knochi :k!k@k.tmi.twitch.tv PRIVMSG #kanal :catKISS" % mid)
        self.assertEqual(it["emotes"], [])
        r._add(it)
        self.wait(tp)
        for _ in range(100):
            if any(i.get("type") == "emotes" for i in r.items):
                break
            time.sleep(0.05)
        meta = [i for i in r.items if i.get("type") == "emotes"]
        self.assertEqual(len(meta), 1)
        self.assertTrue(meta[0]["meta"])
        self.assertEqual(meta[0]["mid"], mid)
        self.assertEqual((meta[0]["emotes"][0][1], meta[0]["emotes"][0][2]), (0, 6))
        self.assertEqual(len(it["emotes"]), 1)                                                # auch der Speicher ist nachgeführt (wer neu lädt, sieht das Emote)

    def test_page_redraws_the_row_from_the_meta_item(self):
        page = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
        self.assertIn('if(m.type==="emotes")', page)
        self.assertIn("fillText(x._mt,{text:x._txt,emotes:m.emotes})", page)


class Links(unittest.TestCase):
    def page(self):
        with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_links_open_in_a_new_tab_safely(self):
        p = self.page()
        self.assertIn('a.target="_blank"; for(const v of ["noopener","noreferrer","nofollow"]) a.relList.add(v);', p)
        self.assertIn('if(u.protocol!=="https:"&&u.protocol!=="http:") return null; if(u.username||u.password) return null;', p)
        self.assertIn("a.textContent=t", p)                                                   # Text, nie HTML
        self.assertIn("u.hostname.replace(", p)                                               # echter Rechnername (Punycode) wird angezeigt

    def test_text_between_emotes_goes_through_the_link_filter(self):
        p = self.page()
        self.assertIn("if(e[1]>pos) addText(mt,sl(pos,e[1]));", p)
        self.assertIn("if(pos<cp.length) addText(mt,sl(pos));", p)

    def test_no_regex_lookbehind_so_old_browsers_still_load_the_page(self):
        self.assertNotIn("(?<", self.page())


class FakeBttvSocket:
    """Kleiner WebSocket-Server (ohne TLS) wie sockets.betterttv.net: nimmt join_channel an und schickt Ereignisse."""
    def __init__(self, events, close_after=None):
        self.events, self.received, self.close_after = events, [], close_after
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    @staticmethod
    def frame(text, op=1):
        d = text.encode() if isinstance(text, str) else text
        n = len(d)
        return bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + n.to_bytes(2, "big")) + d

    def _serve(self):
        try:
            c, _ = self.srv.accept()
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += c.recv(4096)
            c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n")
            c.settimeout(5)
            d = c.recv(4096)                                                   # maskierter Rahmen vom Client
            n = d[1] & 0x7F
            m, body = d[2:6], d[6:6 + n]
            self.received.append(json.loads(bytes(b ^ m[i % 4] for i, b in enumerate(body))))
            for e in self.events:
                c.sendall(self.frame(e if isinstance(e, str) else json.dumps(e)))
            if self.close_after is not None:
                time.sleep(self.close_after)
                c.sendall(self.frame(b"", 8))
            time.sleep(3)
            c.close()
        except OSError:
            pass


class PersonalEmotes(unittest.TestCase):
    """BTTV Pro: persönliche Emotes kommen über BTTVs Live-Verbindung (lookup_user). Beispiel aus der Praxis: "DanceDanceDance" von Knochi3006 stand in keiner öffentlichen Liste."""
    BTTV = "607f3f3239b5010444d03135"

    def event(self, uid="501025597", pro=True, code="DanceDanceDance", id_=None):
        return {"name": "lookup_user", "data": {"providerId": uid, "pro": pro, "emotes": [{"id": id_ or self.BTTV, "channel": "kittycatmatsu", "code": code, "animated": True}]}}

    def make(self, events, **kw):
        fake = FakeBttvSocket(events, **kw)
        tp = server.ThirdPartyEmotes(fetch=lambda u: None)
        tp.sock_on, tp.sock_host, tp.sock_port, tp.sock_tls = True, "127.0.0.1", fake.port, False
        return tp, fake

    def wait_for(self, cond, sec=5.0):
        end = time.time() + sec
        while time.time() < end and not cond():
            time.sleep(0.05)
        return cond()

    def test_personal_emote_from_the_live_connection_marks_the_word(self):
        tp, fake = self.make([self.event()])
        got = []
        tp.on_user = got.append
        tp.ensure("279326559")
        self.assertTrue(self.wait_for(lambda: "501025597" in tp.personal))
        self.assertEqual(fake.received, [{"name": "join_channel", "data": {"name": "twitch:279326559"}}])      # nur zuhören, nie broadcast_me
        self.assertEqual(got, ["501025597"])
        item = {"text": "DanceDanceDance", "emotes": [], "uid": "501025597"}
        tp.mark(item)
        self.assertEqual([(e[1], e[2]) for e in item["emotes"]], [(0, 14)])
        self.assertEqual(item["emotes"][0][0], "https://cdn.betterttv.net/emote/%s/2x.webp" % self.BTTV)
        other = {"text": "DanceDanceDance", "emotes": [], "uid": "42"}
        tp.mark(other)
        self.assertEqual(other["emotes"], [])                                                    # nur für diesen Schreiber

    def test_ignored_events(self):
        evs = [self.event(pro=False), self.event(uid="abc"), self.event(id_="../x"), {"name": "emote_create", "data": {}}, "kein json", self.event(uid="7", code="Gut")]
        tp, _ = self.make(evs)
        tp.ensure("1")
        self.assertTrue(self.wait_for(lambda: "7" in tp.personal))
        self.assertEqual(sorted(tp.personal), ["7"])                                              # nur das Gültige bleibt

    def test_personal_list_is_merged_with_the_public_one(self):
        tp = server.ThirdPartyEmotes(fetch=lambda u: None)
        tp.users["5"] = (tp.clock() + 100, {"catKISS": "https://cdn.betterttv.net/emote/54fa8f1401e468494b85b537/2x.webp"})
        tp._on_event(json.dumps(self.event(uid="5")))
        m = tp.user_map("5")
        self.assertIn("catKISS", m)
        self.assertIn("DanceDanceDance", m)

    def test_reader_patches_the_row_when_the_personal_emote_arrives(self):
        tp = server.ThirdPartyEmotes(fetch=lambda u: None)
        r = server.TwitchReader(None, third=tp)
        mid = "11111111-2222-4333-8444-555555555555"
        it = r.parse("@room-id=55;user-id=501025597;id=%s;display-name=Knochi :k!k@k.tmi.twitch.tv PRIVMSG #kanal :DanceDanceDance" % mid)
        r._add(it)
        self.assertEqual(it["emotes"], [])
        tp._on_event(json.dumps(self.event()))
        meta = [i for i in r.items if i.get("type") == "emotes"]
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["mid"], mid)
        self.assertEqual(len(it["emotes"]), 1)

    def test_broken_connection_is_retried_later_with_longer_waits(self):
        tp = server.ThirdPartyEmotes(fetch=lambda u: None)
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()        # niemand hört zu
        tp.sock_on, tp.sock_host, tp.sock_port, tp.sock_tls = True, "127.0.0.1", port, False
        tp.ensure("1")
        self.assertTrue(self.wait_for(lambda: tp.sthread is None and tp.sfails == 1))
        w1 = tp.sretry - tp.clock()
        self.assertGreater(w1, 20)
        threads = threading.active_count()
        tp.ensure("1")                                                                             # innerhalb der Wartezeit: kein neuer Versuch
        time.sleep(0.2)
        self.assertEqual(tp.sfails, 1)
        self.assertLessEqual(threading.active_count(), threads + 1)

    def test_close_frame_ends_the_connection(self):
        tp, _ = self.make([self.event()], close_after=0.2)
        tp.ensure("1")
        self.assertTrue(self.wait_for(lambda: tp.sfails == 1 and tp.sthread is None, 6))

    def test_no_network_without_real_paths(self):
        tp = server.ThirdPartyEmotes(fetch=lambda u: None)
        self.assertFalse(tp.sock_on)
        tp.ensure("1")
        self.assertIsNone(tp.sthread)


if __name__ == "__main__":
    unittest.main()
