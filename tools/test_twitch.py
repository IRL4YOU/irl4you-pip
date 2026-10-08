"""Tests für die Akku-Warnung der DJI-Kameras im Twitch-Chat: Speicher und Prüfung (TwitchStore), IRC-Client gegen einen lokalen Fake-Server (TwitchChat),
Hintergrunddienst mit Fake-Uhr, Fake-Akku und Fake-Sendestatus (TwitchNotifier), Endpunkte, Oberfläche (Aufbau und Seitenskripte in JavaScriptCore) und Übersetzung.
Es wird nie eine Verbindung zu twitch.tv aufgebaut und kein echter Token benutzt: Alles läuft gegen 127.0.0.1 mit erfundenen Werten."""
import contextlib
import io
import json
import os
import re
import shutil
import socket
import socketserver
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import i18n_extract  # noqa: E402
import server  # noqa: E402

PAGE = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
EN = json.load(open(os.path.join(ROOT, "web", "i18n", "en.json"), encoding="utf-8"))
JSC = next((p for p in (shutil.which("jsc"), "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc") if p and os.path.exists(p)), None)
TOKEN = "fake0token0fuer0tests0abcdef01"                       # erfunden: sieht aus wie ein Token, gehört zu keinem Konto
CFG = {"enabled": True, "channel": "MeinKanal", "login": "BotKonto", "token": TOKEN, "threshold": 10, "only_live": True}
WIRE = ["PASS oauth:" + TOKEN, "NICK botkonto", "CAP REQ :twitch.tv/commands", "JOIN #meinkanal"]       # was der Client vor der Nachricht schickt


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def new_store(**cfg):
    s = server.TwitchStore(os.path.join(tempfile.mkdtemp(), "twitch.json"))
    if cfg:
        s.set(cfg)
    return s


# ---------------------------------------------------------------------------------------------------------------- Speicher
class Store(unittest.TestCase):
    def test_defaults_and_public_view(self):
        s = new_store()
        self.assertEqual(s.public(), {"enabled": False, "channel": "", "login": "", "token_set": False, "threshold": 10, "only_live": True, "account": "", "bot": "",
                                      "message": "Akkustand niedrig, bitte Akku wechseln: {Kamera} ({Prozent} %)"})
        s.set(CFG)
        pub = s.public()
        self.assertTrue(pub["token_set"])
        self.assertNotIn("token", pub)
        self.assertNotIn("warned", pub)
        self.assertNotIn(TOKEN, json.dumps(pub))
        self.assertNotIn(TOKEN, repr(s))

    def test_names_are_stored_in_lower_case_and_the_token_without_prefix(self):
        for prefix in ("", "oauth:", "OAuth:", "  oauth:"):
            s = new_store()
            s.set(dict(CFG, token=prefix + TOKEN + " "))
            self.assertEqual(s.settings()["token"], TOKEN, prefix)
        s = new_store(**CFG)
        self.assertEqual((s.settings()["channel"], s.settings()["login"]), ("meinkanal", "botkonto"))

    def test_the_file_is_private_and_written_atomically(self):
        s = new_store(**CFG)
        mode = stat.S_IMODE(os.stat(s.path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(os.listdir(os.path.dirname(s.path)), ["twitch.json"])                    # keine Reste der temporären Datei
        s.mark_warned("dji-aaaaaa", 123.0)
        self.assertEqual(stat.S_IMODE(os.stat(s.path).st_mode), 0o600)
        with open(s.path + ".tmp", "w") as f:                                                       # eine alte temporäre Datei mit offenen Rechten
            f.write("alt")
        os.chmod(s.path + ".tmp", 0o644)
        s.set({"threshold": 12})
        self.assertEqual(stat.S_IMODE(os.stat(s.path).st_mode), 0o600)
        self.assertFalse(os.path.exists(s.path + ".tmp"))

    def test_the_file_keeps_everything_and_a_new_store_reads_it(self):
        s = new_store(**dict(CFG, message="Akku {Kamera}: {Prozent}", only_live=False))
        s.mark_warned("dji-aaaaaa", 1700000000.5)
        saved = json.load(open(s.path))
        self.assertEqual(saved["token"], TOKEN)
        self.assertEqual(saved["warned"], {"dji-aaaaaa": 1700000000.5})
        t = server.TwitchStore(s.path)
        self.assertEqual(t.settings(), s.settings())
        self.assertEqual(t.warned_keys(), ["dji-aaaaaa"])
        self.assertTrue(t.is_warned("dji-aaaaaa"))

    def test_invalid_names_and_channels_are_refused(self):
        for field in ("channel", "login"):
            for bad in ("ab", "x" * 26, "bad name", "bot\r\nJOIN #x", "bo\nt", "böse", "a-b-c", "#kanal", "@bot", "bot,x", "bot:x", 5, None, ["bot"], True):
                with self.assertRaises(ValueError, msg=repr((field, bad))):
                    new_store().set({field: bad})
            self.assertEqual(new_store().public()[field], "")
        for good in ("abc", "a_b_c", "ABC123", "x" * 25, "  Rand \n"):
            s = new_store()
            s.set({"channel": good})
            self.assertEqual(s.public()["channel"], good.strip().lower())

    def test_invalid_tokens_are_refused_and_the_message_never_shows_them(self):
        bads = ["kurz", "x" * 101, "has space in it 12345678901", "line\r\nPASS-12345678901234567", TOKEN + "\n" + TOKEN, "ümlaut0123456789012345678", "oauth:", "oauth:kurz",
                "oauth:" + "ä" * 25, 12345678901234567890, None, [TOKEN], True]
        for bad in bads:
            with self.assertRaises(ValueError, msg=repr(bad)) as e:
                new_store().set({"token": bad})
            self.assertNotIn(TOKEN, str(e.exception))
            self.assertNotIn(str(bad)[:12] if isinstance(bad, str) and len(str(bad)) > 12 else "\0", str(e.exception))
        s = new_store(**CFG)
        s.set({"token": ""})                                                                         # leer: der Token bleibt
        self.assertEqual(s.settings()["token"], TOKEN)
        s.set({"token": "x" * 100})
        self.assertEqual(s.settings()["token"], "x" * 100)

    def test_threshold_must_be_a_whole_number_from_1_to_50(self):
        for bad in (0, 51, -1, "10", "", None, True, False, 10.5, float("nan"), float("inf"), [10], {"a": 1}, 10 ** 30):
            with self.assertRaises(ValueError, msg=repr(bad)):
                new_store().set({"threshold": bad})
        for good, want in ((1, 1), (50, 50), (10.0, 10), (25, 25)):
            s = new_store()
            s.set({"threshold": good})
            self.assertEqual(s.public()["threshold"], want)

    def test_message_rules(self):
        bads = ["", "   ", "x" * 301, "zwei\nZeilen", "ein\rText", "mit\0Null", "mit\tTab", "/ban jemand", ".ban jemand", "  /me hallo", "Text Ende", "x\x1b[31m", 5, None, ["a"], True]
        for bad in bads:
            with self.assertRaises(ValueError, msg=repr(bad)):
                new_store().set({"message": bad})
        for good in ("Akku {Kamera} {Prozent}", "x", "x" * 300, "  mit Rand  ", "Akku niedrig 🔋 {Kamera}", "{Kamera} ({Prozent} %)", "Hallo/Welt. Ende.", "{unbekannt}"):
            s = new_store()
            s.set({"message": good})
            self.assertEqual(s.public()["message"], good.strip())

    def test_booleans_must_be_real_booleans(self):
        for key in ("enabled", "only_live"):
            for bad in ("true", 1, 0, None, "ja", [True]):
                with self.assertRaises(ValueError, msg=repr((key, bad))):
                    new_store(**CFG).set({key: bad})

    def test_enabling_needs_channel_bot_and_token(self):
        for missing in ("channel", "login", "token"):
            with self.assertRaises(ValueError, msg=missing) as e:
                new_store().set(dict(CFG, **{missing: ""}))
            self.assertEqual(str(e.exception), "Bitte Kanal, Bot-Konto und Token eintragen")
        s = new_store()
        s.set(dict(CFG, enabled=False))                                                              # ausgeschaltet darf unvollständig sein
        s.set({"channel": ""})
        with self.assertRaises(ValueError):
            s.set({"enabled": True})                                                                 # Kanal fehlt
        s.set({"channel": "kanal", "enabled": True})
        self.assertTrue(s.public()["enabled"])
        with self.assertRaises(ValueError):
            s.set({"channel": ""})                                                                   # eingeschaltet: nichts Pflichtiges leeren
        self.assertEqual(s.public()["channel"], "kanal")                                             # und der alte Stand bleibt

    def test_only_given_fields_change_and_unknown_fields_are_ignored(self):
        s = new_store(**CFG)
        s.set({"threshold": 15, "unbekannt": "x", "warned": {"dji-x": 1}, "token_set": False})
        pub = s.public()
        self.assertEqual((pub["threshold"], pub["channel"], pub["login"], pub["token_set"], pub["enabled"]), (15, "meinkanal", "botkonto", True, True))
        self.assertEqual(s.warned_keys(), [])                                                        # "warned" lässt sich nicht von außen setzen
        for junk in (None, [], "text", 5):
            with self.assertRaises(ValueError):
                s.set(junk)

    def test_a_failed_save_does_not_change_the_state(self):
        s = new_store(**CFG)
        before = s.settings()
        with mock.patch.object(server.os, "replace", side_effect=OSError("voll")):
            with self.assertRaises(RuntimeError) as e:
                s.set({"threshold": 20})
        self.assertEqual(s.settings(), before)
        self.assertNotIn(TOKEN, str(e.exception))
        self.assertFalse(os.path.exists(s.path + ".tmp"))

    def test_broken_or_tampered_files_fall_back_field_by_field(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "twitch.json")
        for junk in ("kein json", "[]", "5", "null", ""):
            with open(path, "w") as f:
                f.write(junk)
            self.assertEqual(server.TwitchStore(path).public()["threshold"], 10, junk)
        evil = {"enabled": True, "login": "bot\r\nJOIN #x", "token": "kurz", "channel": "kanal", "threshold": 99, "message": "/ban", "only_live": "ja",
                "warned": {"dji-ok": 5, "bad key": 5, "dji-neg": -1, "dji-bool": True, "dji-str": "x", "dji-nan": float("nan"), "Dji-Gross": 1, "": 1}}
        with open(path, "w") as f:
            json.dump(evil, f)
        s = server.TwitchStore(path)
        cfg = s.settings()
        self.assertEqual((cfg["login"], cfg["token"], cfg["threshold"], cfg["only_live"]), ("", "", 10, True))
        self.assertEqual(cfg["message"], server.TwitchStore.DEFAULT_MESSAGE)
        self.assertEqual(cfg["channel"], "kanal")
        self.assertFalse(cfg["enabled"])                                                             # unvollständig: bleibt aus
        self.assertEqual(s.warned_keys(), ["dji-ok"])

    def test_warned_keys_can_be_set_and_cleared_and_survive(self):
        s = new_store(**CFG)
        s.mark_warned("dji-aaaaaa", 100)
        s.mark_warned("dji-bbbbbb", 200)
        s.unwarn("dji-aaaaaa")
        s.unwarn("dji-gibtsnicht")
        self.assertEqual(server.TwitchStore(s.path).warned_keys(), ["dji-bbbbbb"])

    def test_a_write_error_while_marking_keeps_the_memory(self):
        s = new_store(**CFG)
        with mock.patch.object(server.os, "replace", side_effect=OSError("voll")):
            s.mark_warned("dji-aaaaaa", 100)
        self.assertTrue(s.is_warned("dji-aaaaaa"))                                                   # bis zum Neustart des Dienstes keine zweite Meldung


# ---------------------------------------------------------------------------------------------------------------- Fake-IRC-Server
class FakeIrc:
    """Ein kleiner IRC-Server auf 127.0.0.1 (ohne TLS, außer mit tls=Kontext), der sich nach einem Drehbuch verhält und alles merkt, was der Client schickt."""

    def __init__(self, auth_notice=None, notice_after_privmsg=None, ping_before_welcome=False, ping_after_privmsg=False, silent=False, close_after_login=False,
                 flood=False, drip=False, tls=None):
        self.lines, self.connections = [], 0
        self.auth_notice, self.notice_after_privmsg, self.ping_before_welcome = auth_notice, notice_after_privmsg, ping_before_welcome
        self.ping_after_privmsg, self.silent, self.close_after_login, self.flood, self.drip, self.tls = ping_after_privmsg, silent, close_after_login, flood, drip, tls
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def setup(self):
                if outer.tls:
                    self.request = outer.tls.wrap_socket(self.request, server_side=True)
                super().setup()

            def handle(self):
                outer.connections += 1
                try:
                    outer.serve(self)
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

            def handle_error(self, request, client_address):
                pass

        self.srv = Server(("127.0.0.1", 0), Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def serve(self, h):
        def say(text):
            h.wfile.write(text.encode() + b"\r\n")
            h.wfile.flush()
        if self.flood:
            h.wfile.write(b"x" * 100000)
            h.wfile.flush()
            time.sleep(0.3)
            return
        if self.drip:
            while True:
                say("NOTE :tropf")
                time.sleep(0.02)
        have, state, token = set(), "neu", ""
        while True:
            raw = h.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            self.lines.append(line)
            word = line.split(" ", 1)[0]
            if line.startswith("PASS oauth:"):
                token = line[len("PASS oauth:"):]
            if word in ("PASS", "NICK"):
                have.add(word)
            if have == {"PASS", "NICK"} and state == "neu":
                if self.silent:
                    state = "stumm"
                elif self.auth_notice:
                    say(":tmi.twitch.tv NOTICE * :" + self.auth_notice)
                    return
                elif self.close_after_login:
                    return
                elif self.ping_before_welcome:
                    state = "ping"
                    say("PING :tmi.twitch.tv")
                else:
                    state = "begruesst"
                    say(":tmi.twitch.tv 001 botkonto :Welcome, GLHF!")
            elif word == "PONG" and state == "ping":
                state = "begruesst"
                say(":tmi.twitch.tv 001 botkonto :Welcome, GLHF!")
            if word == "PRIVMSG":
                if self.notice_after_privmsg:
                    say(self.notice_after_privmsg.replace("{token}", token))
                if self.ping_after_privmsg:
                    say("PING :tmi.twitch.tv")
            if word == "QUIT":
                return


class ServerCase(unittest.TestCase):
    def irc(self, **kw):
        srv = FakeIrc(**kw)
        self.addCleanup(srv.close)
        return srv

    @staticmethod
    def chat(srv, **kw):
        base = dict(host="127.0.0.1", port=srv.port, tls=False, connect_timeout=1.0, total=2.0, notice_wait=0.3)
        return server.TwitchChat(**dict(base, **kw))


class Chat(ServerCase):
    def test_a_message_goes_out_in_the_expected_order(self):
        srv = self.irc()
        self.assertEqual(self.chat(srv).send("BotKonto", TOKEN, "MeinKanal", "Akkustand niedrig, bitte Akku wechseln: Action 4 (9 %)"), (True, "Gesendet"))
        self.assertTrue(wait_for(lambda: srv.lines[-1:] == ["QUIT"]))
        self.assertEqual(srv.lines, WIRE + ["PRIVMSG #meinkanal :Akkustand niedrig, bitte Akku wechseln: Action 4 (9 %)", "QUIT"])

    def test_the_wait_for_notices_is_not_cut_short_and_not_longer_than_needed(self):
        srv = self.irc()
        t = time.time()
        self.chat(srv, notice_wait=0.4).send("botkonto", TOKEN, "meinkanal", "hallo")
        self.assertGreaterEqual(time.time() - t, 0.4)
        self.assertLess(time.time() - t, 3.0)

    def test_wrong_login_is_reported_in_german_and_nothing_else_is_sent(self):
        for text in ("Login authentication failed", "Improperly formatted auth", "Login unsuccessful"):
            srv = self.irc(auth_notice=text)
            res = self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo")
            self.assertEqual(res, (False, "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)"), text)
            self.assertNotIn(TOKEN, res[1])
            self.assertTrue(wait_for(lambda: len(srv.lines) >= 2))
            self.assertEqual(srv.lines, WIRE[:2], text)                                              # kein JOIN, keine Nachricht

    def test_a_notice_after_the_message_is_reported_with_help_and_the_text_of_twitch(self):
        for raw in ("@msg-id=msg_followersonly :tmi.twitch.tv NOTICE #meinkanal :This room is in followers-only mode.",
                    ":tmi.twitch.tv NOTICE #meinkanal :This room is in followers-only mode."):
            srv = self.irc(notice_after_privmsg=raw)
            ok, msg = self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo")
            self.assertFalse(ok)
            self.assertEqual(msg, "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower): This room is in followers-only mode.")

    def test_a_notice_without_text_still_gives_the_help(self):
        srv = self.irc(notice_after_privmsg=":tmi.twitch.tv NOTICE #meinkanal")
        self.assertEqual(self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo"), (False, "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower)"))

    def test_text_from_twitch_is_cleaned_shortened_and_free_of_the_token(self):
        srv = self.irc(notice_after_privmsg=":tmi.twitch.tv NOTICE #meinkanal :Dein Token {token} ist\x01\x02 da\r\x07 ok")
        ok, msg = self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo")
        self.assertNotIn(TOKEN, msg)
        self.assertNotRegex(msg, r"[\x00-\x1f\x7f]")
        self.assertIn("Dein Token … ist da ok", msg)
        srv = self.irc(notice_after_privmsg=":tmi.twitch.tv NOTICE #meinkanal :" + "lang " * 200)
        ok, msg = self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo")
        self.assertLess(len(msg), 220)
        self.assertTrue(msg.endswith("…"))

    def test_ping_is_answered_before_and_after_the_welcome(self):
        srv = self.irc(ping_before_welcome=True)
        self.assertEqual(self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo"), (True, "Gesendet"))
        self.assertTrue(wait_for(lambda: "QUIT" in srv.lines))
        self.assertIn("PONG :tmi.twitch.tv", srv.lines)
        self.assertLess(srv.lines.index("PONG :tmi.twitch.tv"), srv.lines.index("JOIN #meinkanal"))      # erst Antwort auf PING, dann geht es weiter
        srv = self.irc(ping_after_privmsg=True)
        self.assertEqual(self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo"), (True, "Gesendet"))
        self.assertTrue(wait_for(lambda: "QUIT" in srv.lines))
        self.assertIn("PONG :tmi.twitch.tv", srv.lines)

    def test_nothing_can_be_smuggled_into_the_lines(self):
        srv = self.irc()
        c = self.chat(srv)
        bad = [("bot\r\nJOIN #evil", TOKEN, "meinkanal", "hallo"), ("botkonto", TOKEN, "kanal\nPRIVMSG #andere :hallo", "hallo"),
               ("botkonto", TOKEN, "meinkanal", "hallo\r\nPRIVMSG #andere :boese"), ("botkonto", TOKEN, "meinkanal", "hallo\nQUIT"), ("botkonto", TOKEN, "meinkanal", "a\0b"),
               ("botkonto", TOKEN + "\r\nQUIT", "meinkanal", "hallo"), ("botkonto", "kurz", "meinkanal", "hallo"), ("", TOKEN, "meinkanal", "hallo"),
               ("botkonto", TOKEN, "", "hallo"), ("botkonto", TOKEN, "meinkanal", ""), ("botkonto", TOKEN, "meinkanal", "   "), ("botkonto", TOKEN, "meinkanal", "/ban jemand"),
               ("botkonto", TOKEN, "meinkanal", ".ban jemand"), ("botkonto", TOKEN, "meinkanal", "tab\there"), (None, TOKEN, "meinkanal", "hallo"), ("botkonto", None, "meinkanal", "hallo"),
               ("botkonto", TOKEN, 5, "hallo"), ("botkonto", TOKEN, "meinkanal", None), ("botkonto", TOKEN, "meinkanal", b"hallo")]
        for args in bad:
            self.assertEqual(c.send(*args), (False, "Ungültige Anfrage"), repr(args))
        self.assertEqual(srv.connections, 0)                                                         # es kam nicht einmal zu einer Verbindung

    def test_a_long_text_is_cut_at_a_character_boundary(self):
        srv = self.irc()
        self.assertEqual(self.chat(srv).send("botkonto", TOKEN, "meinkanal", "Ä" * 400), (True, "Gesendet"))
        self.assertTrue(wait_for(lambda: "QUIT" in srv.lines))
        line = next(x for x in srv.lines if x.startswith("PRIVMSG"))
        body = line.split(" :", 1)[1]
        self.assertEqual(set(body), {"Ä"})
        self.assertLessEqual(len(body.encode("utf-8")), server.TwitchChat.MAX_BYTES)
        self.assertGreater(len(body), 200)

    def test_silence_ends_with_a_timeout_message_within_the_total_time(self):
        srv = self.irc(silent=True)
        t = time.time()
        self.assertEqual(self.chat(srv, total=0.6).send("botkonto", TOKEN, "meinkanal", "hallo"), (False, "Keine Antwort von Twitch (Zeitüberschreitung)"))
        self.assertLess(time.time() - t, 3.0)

    def test_a_server_that_drips_forever_cannot_hold_the_client_longer_than_the_total_time(self):
        srv = self.irc(drip=True)
        t = time.time()
        self.assertEqual(self.chat(srv, total=0.6).send("botkonto", TOKEN, "meinkanal", "hallo"), (False, "Keine Antwort von Twitch (Zeitüberschreitung)"))
        self.assertLess(time.time() - t, 3.0)

    def test_closed_connection_and_endless_lines(self):
        srv = self.irc(close_after_login=True)
        self.assertEqual(self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo"), (False, "Twitch hat die Verbindung beendet"))
        srv = self.irc(flood=True)
        self.assertEqual(self.chat(srv).send("botkonto", TOKEN, "meinkanal", "hallo"), (False, "Twitch hat die Verbindung beendet"))

    def test_nobody_listening_means_no_connection(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(server.TwitchChat(host="127.0.0.1", port=port, tls=False, connect_timeout=1.0).send("botkonto", TOKEN, "meinkanal", "hallo"),
                         (False, "Keine Verbindung zu Twitch"))
        self.assertEqual(server.TwitchChat(host="gibt-es-nicht.invalid", tls=False, connect_timeout=1.0).send("botkonto", TOKEN, "meinkanal", "hallo"),
                         (False, "Keine Verbindung zu Twitch"))

    def test_the_token_never_shows_up_in_output_or_in_any_message(self):
        out, err = io.StringIO(), io.StringIO()
        results = []
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for kw in ({}, {"auth_notice": "Login authentication failed"}, {"close_after_login": True}, {"silent": True}, {"flood": True},
                       {"notice_after_privmsg": ":tmi.twitch.tv NOTICE #meinkanal :{token}"}):
                srv = self.irc(**kw)
                results.append(self.chat(srv, total=0.5).send("botkonto", TOKEN, "meinkanal", "hallo"))
            results.append(server.TwitchChat(host="127.0.0.1", port=1, tls=False, connect_timeout=0.5).send("botkonto", TOKEN, "meinkanal", "hallo"))
        for ok, msg in results:
            self.assertNotIn(TOKEN, msg)
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())

    def test_an_unexpected_error_gives_a_plain_message_without_its_text(self):
        c = server.TwitchChat(tls=False)
        with mock.patch.object(c, "_send", side_effect=RuntimeError("geheim " + TOKEN)):
            ok, msg = c.send("botkonto", TOKEN, "meinkanal", "hallo")
        self.assertEqual((ok, msg), (False, "unbekannter Fehler"))
        self.assertNotIn(TOKEN, msg)

    def test_the_defaults_point_to_twitch_with_tls(self):
        c = server.TwitchChat()
        self.assertEqual((c.host, c.port, c.tls, c.connect_timeout, c.total, c.notice_wait), ("irc.chat.twitch.tv", 6697, True, 10.0, 15.0, 2.0))


def make_cert(san):
    """Ein selbst ausgestelltes Zertifikat (nur für den Test) mit dem Eintrag `san` oder None, wenn es kein openssl gibt."""
    if not shutil.which("openssl"):
        return None
    d = tempfile.mkdtemp()
    cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert, "-days", "2", "-subj", "/CN=test",
                        "-addext", "subjectAltName=" + san], capture_output=True, timeout=60)
    return (cert, key) if r.returncode == 0 else None


class TlsChat(ServerCase):
    @classmethod
    def setUpClass(cls):
        cls.cert, cls.other = make_cert("IP:127.0.0.1,DNS:localhost"), make_cert("DNS:anderer-name.example")

    def setUp(self):
        if not (self.cert and self.other):
            self.skipTest("kein openssl zum Erzeugen eines Zertifikats")

    @staticmethod
    def context(cert):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*cert)
        return ctx

    @staticmethod
    def trusting(cert):
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cert[0])
        return ctx

    def test_an_unknown_certificate_is_refused_before_the_token_is_sent(self):
        srv = self.irc(tls=self.context(self.cert))
        res = self.chat(srv, tls=True).send("botkonto", TOKEN, "meinkanal", "hallo")
        self.assertEqual(res, (False, "Sichere Verbindung zu Twitch nicht möglich (Zertifikat oder Uhrzeit der Box prüfen)"))
        time.sleep(0.2)
        self.assertEqual(srv.lines, [])                                                              # die PASS-Zeile ging nie hinaus

    def test_a_trusted_certificate_works_over_tls(self):
        srv = self.irc(tls=self.context(self.cert))
        self.assertEqual(self.chat(srv, tls=True, context=self.trusting(self.cert)).send("botkonto", TOKEN, "meinkanal", "hallo"), (True, "Gesendet"))
        self.assertTrue(wait_for(lambda: "QUIT" in srv.lines))
        self.assertEqual(srv.lines, WIRE + ["PRIVMSG #meinkanal :hallo", "QUIT"])

    def test_even_a_trusted_certificate_must_match_the_name(self):
        srv = self.irc(tls=self.context(self.other))
        res = self.chat(srv, tls=True, context=self.trusting(self.other)).send("botkonto", TOKEN, "meinkanal", "hallo")
        self.assertEqual(res, (False, "Sichere Verbindung zu Twitch nicht möglich (Zertifikat oder Uhrzeit der Box prüfen)"))
        time.sleep(0.2)
        self.assertEqual(srv.lines, [])

    def test_a_plain_server_cannot_be_talked_to_over_tls(self):
        srv = self.irc()
        res = self.chat(srv, tls=True, connect_timeout=0.4, total=1.0).send("botkonto", TOKEN, "meinkanal", "hallo")
        self.assertFalse(res[0])
        self.assertNotIn(TOKEN, res[1])
        time.sleep(0.2)
        self.assertEqual([x for x in srv.lines if TOKEN in x], [])                                   # in Klartext erreicht den Server kein Token


# ---------------------------------------------------------------------------------------------------------------- Hintergrunddienst
class FakeClock:
    def __init__(self):
        self.m, self.w = 1000.0, 1_760_000_000.0

    def mono(self):
        return self.m

    def wall(self):
        return self.w

    def advance(self, seconds):
        self.m += seconds
        self.w += seconds

    sleep = advance


class FakeDji:
    def __init__(self):
        self.extras = {}

    def camera_extras(self):
        return {k: dict(v) if isinstance(v, dict) else v for k, v in self.extras.items()}


class FakeChat:
    def __init__(self):
        self.calls, self.results = [], []

    def send(self, login, token, channel, text):
        self.calls.append((login, token, channel, text))
        return self.results.pop(0) if self.results else (True, "Gesendet")


class FakeSend:
    live = True

    def _active(self):
        return self.live


class FakeCams:
    def __init__(self, cams):
        self.cams = cams


class Rig:
    """Alles, was der Hintergrunddienst braucht, mit Attrappen: Uhr, Akkustände, Kameraliste, Sendestatus, Chat."""

    def __init__(self, case, state=None, **cfg):
        self.dir = state or tempfile.mkdtemp()
        self.store = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        if state is None:
            self.store.set(dict(CFG, **cfg))
        self.clock, self.dji, self.chat, self.send, self.bela = FakeClock(), FakeDji(), FakeChat(), FakeSend(), False
        self.cams = FakeCams([{"key": "dji-aaaaaa", "name": "Action 4"}, {"key": "dji-bbbbbb", "name": "Action 5"}])
        patch = mock.patch.object(server, "belacoder_running", lambda: self.bela)
        patch.start()
        case.addCleanup(patch.stop)
        self.n = server.TwitchNotifier(self.store, self.dji, self.cams, self.send, chat=self.chat, mono=self.clock.mono, wall=self.clock.wall, sleep=self.clock.sleep)

    def cam(self, key="dji-aaaaaa", battery=50, age=5, charging=False):
        self.dji.extras[key] = {"battery": battery, "battery_age": age, "charging": charging}

    def tick(self, advance=0):
        self.clock.advance(advance)
        self.n.tick()

    @property
    def texts(self):
        return [c[3] for c in self.chat.calls]


MSG = "Akkustand niedrig, bitte Akku wechseln: %s (%d %%)"


class Notifier(unittest.TestCase):
    def rig(self, **kw):
        return Rig(self, **kw)

    def test_a_low_battery_is_reported_once_with_the_right_account_channel_and_text(self):
        r = self.rig()
        r.cam(battery=9)
        r.tick()
        self.assertEqual(r.chat.calls, [("botkonto", TOKEN, "meinkanal", MSG % ("Action 4", 9))])
        for pct in (9, 8, 7, 5):
            r.cam(battery=pct)
            r.tick(10)
        self.assertEqual(len(r.chat.calls), 1)
        self.assertTrue(r.store.is_warned("dji-aaaaaa"))
        st = r.n.status()["status"]
        self.assertEqual((st["ok"], st["text"], st["retry_at"], st["gave_up"]), (True, MSG % ("Action 4", 9), None, False))

    def test_the_threshold_counts_with_less_or_equal(self):
        for thr, pct, want in ((10, 10, 1), (10, 11, 0), (10, 9, 1), (15, 15, 1), (15, 16, 0), (1, 1, 1), (1, 2, 0), (50, 50, 1)):
            r = self.rig(threshold=thr)
            r.cam(battery=pct)
            r.tick()
            self.assertEqual(len(r.chat.calls), want, (thr, pct))

    def test_hysteresis_a_battery_that_wobbles_does_not_warn_again(self):
        r = self.rig()
        r.cam(battery=9)
        r.tick()
        for pct in (12, 9, 15, 9, 19, 9, 10):                                                        # unter Schwelle + 10 = 20: bleibt gemerkt
            r.cam(battery=pct)
            r.tick(10)
        self.assertEqual(len(r.chat.calls), 1)
        r.cam(battery=20)                                                                            # Schwelle + 10: Akku gewechselt, wieder scharf
        r.tick(10)
        self.assertFalse(r.store.is_warned("dji-aaaaaa"))
        r.cam(battery=9)
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 2)

    def test_the_hysteresis_follows_the_threshold(self):
        r = self.rig(threshold=20)
        r.cam(battery=20)
        r.tick()
        r.cam(battery=29)
        r.tick(10)
        self.assertTrue(r.store.is_warned("dji-aaaaaa"))
        r.cam(battery=30)
        r.tick(10)
        self.assertFalse(r.store.is_warned("dji-aaaaaa"))

    def test_charging_prevents_a_warning_and_rearms_a_warned_camera(self):
        r = self.rig()
        r.cam(battery=5, charging=True)
        r.tick()
        self.assertEqual(r.chat.calls, [])                                                           # am Kabel: kein Grund zu warnen
        r.cam(battery=5, charging=False)
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 1)
        r.cam(battery=6, charging=True)                                                              # an das Kabel gesteckt: wieder scharf
        r.tick(10)
        self.assertFalse(r.store.is_warned("dji-aaaaaa"))
        r.cam(battery=7, charging=False)                                                             # Kabel ab, Akku noch niedrig: noch einmal
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 2)

    def test_unknown_charging_state_counts_as_not_charging(self):
        r = self.rig()
        r.cam(battery=5, charging=None)
        r.tick()
        self.assertEqual(len(r.chat.calls), 1)

    def test_old_or_missing_measurements_do_not_warn(self):
        for age, want in ((301, 0), (300, 1), (0, 1), (None, 0), (-1, 0), (True, 0), ("5", 0), (10 ** 6, 0)):
            r = self.rig()
            r.cam(battery=5, age=age)
            r.tick()
            self.assertEqual(len(r.chat.calls), want, age)
        r = self.rig()
        r.dji.extras["dji-aaaaaa"] = {"battery": 5, "charging": False}                                # ohne battery_age
        r.tick()
        self.assertEqual(r.chat.calls, [])
        r.cam(battery=5, age=2)                                                                      # danach ein frischer Wert: jetzt wird gewarnt
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 1)

    def test_values_that_make_no_sense_are_ignored(self):
        for bad in (True, 101, -1, "9", 9.5, None, [9], {"a": 1}):
            r = self.rig()
            r.cam(battery=bad)
            r.tick()
            self.assertEqual(r.chat.calls, [], repr(bad))
        r = self.rig()
        r.dji.extras = {"dji-aaaaaa": "kaputt", "dji-bbbbbb": None}
        r.tick()
        self.assertEqual(r.chat.calls, [])

    def test_only_while_streaming(self):
        r = self.rig()
        r.send.live = False
        r.cam(battery=5)
        for _ in range(3):
            r.tick(10)
        self.assertEqual(r.chat.calls, [])
        self.assertFalse(r.store.is_warned("dji-aaaaaa"))                                            # nichts verbraucht: die Warnung kommt, sobald gesendet wird
        r.send.live = True
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 1)

    def test_a_stream_started_in_the_original_interface_counts_as_streaming(self):
        r = self.rig()
        r.send.live = False
        r.bela = True
        r.cam(battery=5)
        r.tick()
        self.assertEqual(len(r.chat.calls), 1)

    def test_without_the_condition_it_warns_even_when_not_streaming(self):
        r = self.rig(only_live=False)
        r.send.live = False
        r.cam(battery=5)
        r.tick()
        self.assertEqual(len(r.chat.calls), 1)

    def test_nothing_happens_while_it_is_off_or_incomplete(self):
        r = self.rig(enabled=False)
        r.cam(battery=5)
        r.tick()
        self.assertEqual(r.chat.calls, [])
        self.assertEqual(r.n.status()["status"]["ok"], None)
        r.store.set({"enabled": True})
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 1)

    def test_at_most_one_message_per_three_seconds_in_total(self):
        r = self.rig()
        r.cam("dji-aaaaaa", battery=5)
        r.cam("dji-bbbbbb", battery=6)
        r.tick()
        self.assertEqual(r.texts, [MSG % ("Action 4", 5)])                                           # nur die erste
        r.tick(1)
        r.tick(1)
        self.assertEqual(len(r.chat.calls), 1)
        r.tick(1)                                                                                    # drei Sekunden vergangen
        self.assertEqual(r.texts, [MSG % ("Action 4", 5), MSG % ("Action 5", 6)])
        r.tick(60)
        self.assertEqual(len(r.chat.calls), 2)

    def test_the_gap_also_counts_after_a_failed_try_and_after_a_test_message(self):
        r = self.rig()
        r.cam("dji-aaaaaa", battery=5)
        r.chat.results = [(False, "Keine Verbindung zu Twitch")]
        r.tick()
        r.cam("dji-bbbbbb", battery=6)
        r.tick(2)
        self.assertEqual(len(r.chat.calls), 1)
        r.tick(1)
        self.assertEqual(len(r.chat.calls), 2)

    def test_a_failed_try_is_repeated_after_30_60_120_120_seconds_and_then_given_up(self):
        r = self.rig()
        r.cam(battery=5)
        r.chat.results = [(False, "Keine Verbindung zu Twitch")] * 5
        r.tick()
        st = r.n.status()["status"]
        self.assertEqual((st["ok"], st["text"], st["retry_at"], st["gave_up"]), (False, "Keine Verbindung zu Twitch", int(r.clock.w + 30), False))
        for i, wait in enumerate((30, 60, 120, 120)):
            r.tick(wait - 1)
            self.assertEqual(len(r.chat.calls), i + 1, "zu früh nach %d s" % wait)
            r.tick(1)
            self.assertEqual(len(r.chat.calls), i + 2, "nicht nach %d s" % wait)
        self.assertEqual(len(r.chat.calls), 5)
        r.tick(3600)
        r.tick(3600)
        self.assertEqual(len(r.chat.calls), 5)                                                       # höchstens 5 Versuche je Warnung
        st = r.n.status()["status"]
        self.assertEqual((st["ok"], st["text"], st["retry_at"], st["gave_up"]), (False, "Keine Verbindung zu Twitch", None, True))
        self.assertFalse(r.store.is_warned("dji-aaaaaa"))

    def test_a_retry_that_works_ends_the_series_and_remembers_the_warning(self):
        r = self.rig()
        r.cam(battery=5)
        r.chat.results = [(False, "Keine Verbindung zu Twitch"), (True, "Gesendet")]
        r.tick()
        self.assertFalse(r.store.is_warned("dji-aaaaaa"))
        r.tick(30)
        self.assertEqual(len(r.chat.calls), 2)
        self.assertTrue(r.store.is_warned("dji-aaaaaa"))
        r.tick(1000)
        self.assertEqual(len(r.chat.calls), 2)
        self.assertTrue(r.n.status()["status"]["ok"])
        self.assertEqual(r.n.tries, {})

    def test_the_series_starts_over_when_the_battery_recovers_or_the_stream_ends(self):
        r = self.rig()
        r.cam(battery=5)
        r.chat.results = [(False, "x")] * 3
        r.tick()
        r.cam(battery=60)
        r.tick(10)
        self.assertEqual(r.n.tries, {})
        r.cam(battery=5)
        r.tick(10)                                                                                   # sofort ein neuer Versuch, nicht erst nach der alten Wartezeit
        self.assertEqual(len(r.chat.calls), 2)
        r.send.live = False
        r.tick(10)
        self.assertEqual(r.n.tries, {})
        r.send.live = True
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 3)

    def test_new_settings_start_a_new_series(self):
        r = self.rig()
        r.cam(battery=5)
        r.chat.results = [(False, "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)")] * 5
        r.tick()
        for wait in (30, 60, 120, 120):
            r.tick(wait)
        self.assertEqual(len(r.chat.calls), 5)
        r.tick(500)
        self.assertEqual(len(r.chat.calls), 5)
        out = r.n.save({"token": "n" * 30})                                                          # neuer Token: es geht wieder los
        self.assertNotIn("n" * 30, json.dumps(out))
        r.tick(10)
        self.assertEqual(len(r.chat.calls), 6)
        self.assertEqual(r.chat.calls[-1][1], "n" * 30)

    def test_a_restart_keeps_what_was_warned_but_not_the_retries(self):
        r = self.rig()
        r.cam(battery=5)
        r.tick()
        self.assertEqual(len(r.chat.calls), 1)
        again = Rig(self, state=r.dir)
        again.cam(battery=5)
        again.tick()
        again.tick(10)
        self.assertEqual(again.chat.calls, [])                                                       # nach dem Neustart des Dienstes nicht noch einmal
        again.cam(battery=60)                                                                        # aber Akkuwechsel und neue Warnung funktionieren
        again.tick(10)
        again.cam(battery=5)
        again.tick(10)
        self.assertEqual(len(again.chat.calls), 1)
        r2 = self.rig()
        r2.cam(battery=5)
        r2.chat.results = [(False, "x")] * 2
        r2.tick()
        fresh = Rig(self, state=r2.dir)
        fresh.cam(battery=5)
        fresh.chat.results = [(False, "x")]
        fresh.tick()
        self.assertEqual(len(fresh.chat.calls), 1)                                                   # die Wartezeit der alten Reihe gilt nicht mehr

    def test_camera_names_cannot_inject_lines_or_commands(self):
        evil = ["Kamera\r\nPRIVMSG #andere :boese", "Name\x00mit\x1bSteuer", "/ban jemand", "...versteckt", "  /  . x", "x" * 100, "​‍ zwischen", "{Prozent}"]
        for name in evil:
            for template in (server.TwitchStore.DEFAULT_MESSAGE, "{Kamera} Akku {Prozent}"):
                r = self.rig(message=template)
                r.cams.cams[0]["name"] = name
                r.cam(battery=5)
                r.tick()
                text = r.texts[0]
                self.assertNotRegex(text, r"[\x00-\x1f\x7f  ​‍]", repr(name))
                self.assertFalse(text.startswith(("/", ".")), repr(name))
                self.assertIsNotNone(server.TwitchChat.clean_text(text), repr(name))               # und der Chat-Client nimmt den Text an
                self.assertLessEqual(len(text), 300 + 40)

    def test_camera_text_and_render(self):
        ct = server.TwitchNotifier.camera_text
        self.assertEqual(ct("  Osmo   Action  4 "), "Osmo Action 4")
        self.assertEqual(ct("x" * 100), "x" * 40)
        self.assertEqual(ct("/ban x"), "ban x")
        self.assertEqual(ct("a\tb\r\nc"), "a b c")
        self.assertEqual(ct(None), "")
        self.assertEqual(ct(5), "5")
        rd = server.TwitchNotifier.render
        self.assertEqual(rd("A {Kamera} {Prozent} {Kamera} {unbekannt} {} {{Kamera}}", "K", 9), "A K 9 K {unbekannt} {} {K}")
        self.assertEqual(rd("{Kamera}: {Prozent}", "{Prozent}", 9), "{Prozent}: 9")                  # der Name wird nicht noch einmal durchsucht
        self.assertEqual(rd("ohne alles", "K", 9), "ohne alles")

    def test_an_unknown_camera_is_named_by_its_key(self):
        r = self.rig()
        r.cam("dji-cccccc", battery=5)
        r.tick()
        self.assertEqual(r.texts, [MSG % ("dji-cccccc", 5)])

    def test_the_name_comes_from_the_camera_list_at_the_time_of_sending(self):
        r = self.rig()
        r.cam(battery=5)
        r.chat.results = [(False, "x")]
        r.tick()
        r.cams.cams[0]["name"] = "Neuer Name"
        r.tick(30)
        self.assertEqual(r.texts[-1], MSG % ("Neuer Name", 5))

    def test_the_message_template_is_used(self):
        r = self.rig(message="Akku von {Kamera} bei {Prozent}% (Warnung bei {Prozent})")
        r.cam(battery=7)
        r.tick()
        self.assertEqual(r.texts, ["Akku von Action 4 bei 7% (Warnung bei 7)"])

    def test_a_service_error_does_not_stop_the_loop_and_prints_no_details(self):
        r = self.rig()
        r.n.POLL_S = 0.01
        seen = []

        def boom():
            seen.append(1)
            raise RuntimeError("Verbindungsdaten " + TOKEN)
        r.n.tick = boom
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            t = threading.Thread(target=r.n.run, daemon=True)
            t.start()
            self.assertTrue(wait_for(lambda: len(seen) >= 3))
            r.n.stop_ev.set()
            t.join(2)
        self.assertFalse(t.is_alive())
        self.assertIn("RuntimeError", out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn("Verbindungsdaten", out.getvalue())

    def test_the_whole_way_with_the_real_chat_client_against_the_fake_irc_server(self):
        srv = FakeIrc()
        self.addCleanup(srv.close)
        r = self.rig()
        r.n.chat = server.TwitchChat(host="127.0.0.1", port=srv.port, tls=False, connect_timeout=1.0, total=2.0, notice_wait=0.1)
        r.cams.cams[0]["name"] = "Kamera\r\nPRIVMSG #andere :boese /ban"                              # ein Kameraname, der eine zweite Zeile einschleusen will
        r.cam(battery=7)
        r.tick()
        self.assertTrue(wait_for(lambda: "QUIT" in srv.lines))
        self.assertEqual(srv.lines, WIRE + ["PRIVMSG #meinkanal :Akkustand niedrig, bitte Akku wechseln: Kamera PRIVMSG #andere :boese /ban (7 %)", "QUIT"])      # genau eine Nachricht
        self.assertTrue(r.store.is_warned("dji-aaaaaa"))
        self.assertTrue(r.n.status()["status"]["ok"])

    def test_the_whole_way_when_twitch_refuses(self):
        for kw, text in (({"auth_notice": "Login authentication failed"}, "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)"),
                         ({"notice_after_privmsg": ":tmi.twitch.tv NOTICE #meinkanal :Nur Follower."}, "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower): Nur Follower."),
                         ({"close_after_login": True}, "Twitch hat die Verbindung beendet")):
            srv = FakeIrc(**kw)
            self.addCleanup(srv.close)
            r = self.rig()
            r.n.chat = server.TwitchChat(host="127.0.0.1", port=srv.port, tls=False, connect_timeout=1.0, total=2.0, notice_wait=0.3)
            r.cam(battery=7)
            r.tick()
            st = r.n.status()["status"]
            self.assertEqual((st["ok"], st["text"], st["retry_at"], st["gave_up"]), (False, text, int(r.clock.w + 30), False), kw)
            self.assertFalse(r.store.is_warned("dji-aaaaaa"))                                       # nicht gemerkt: nach 30 s der nächste Versuch

    def test_status_has_the_settings_without_the_token(self):
        r = self.rig()
        r.cam(battery=5)
        r.chat.results = [(False, "Keine Verbindung zu Twitch")]
        r.tick()
        st = r.n.status()
        self.assertTrue(st["token_set"])
        self.assertNotIn(TOKEN, json.dumps(st))
        self.assertEqual(set(st), {"enabled", "channel", "login", "token_set", "threshold", "message", "only_live", "account", "bot", "status"})
        self.assertEqual(set(st["status"]), {"ok", "time", "text", "retry_at", "gave_up"})
        self.assertEqual(st["status"]["time"], int(r.clock.w))


class TestMessage(unittest.TestCase):
    def rig(self, **kw):
        return Rig(self, **kw)

    def test_a_test_message_goes_out_with_the_saved_data(self):
        r = self.rig()
        self.assertEqual(r.n.test(), {"ok": True, "message": "Gesendet"})
        self.assertEqual(r.chat.calls, [("botkonto", TOKEN, "meinkanal", "Test: IRL4YOU BOX")])
        st = r.n.status()["status"]
        self.assertEqual((st["ok"], st["text"]), (True, "Test: IRL4YOU BOX"))

    def test_a_failed_test_reports_the_reason(self):
        r = self.rig()
        r.chat.results = [(False, "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower): Nur Follower.")]
        res = r.n.test()
        self.assertEqual(res, {"ok": False, "message": "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower): Nur Follower."})
        st = r.n.status()["status"]
        self.assertEqual((st["ok"], st["text"]), (False, res["message"]))
        self.assertIsNone(st["retry_at"])                                                            # ein Test wird nicht wiederholt

    def test_it_needs_saved_channel_bot_and_token_but_not_the_switch(self):
        r = Rig(self, enabled=False)
        self.assertTrue(r.n.test()["ok"])
        for part in ({"channel": ""}, {"login": ""}):
            r = Rig(self, enabled=False)
            r.store.set(part)
            with self.assertRaisesRegex(ValueError, "Kanal, Bot-Konto und Token"):
                r.n.test()
            self.assertEqual(r.chat.calls, [])
        blank = Rig(self, state=tempfile.mkdtemp())                                                  # nichts gespeichert, auch kein Token
        blank.store.set({"channel": "kanal", "login": "bot"})
        with self.assertRaisesRegex(ValueError, "Kanal, Bot-Konto und Token"):
            blank.n.test()
        self.assertEqual(blank.chat.calls, [])

    def test_not_more_often_than_every_ten_seconds(self):
        r = self.rig()
        r.n.test()
        for step in (0, 3, 6.9):                                                                     # nach 0, 3 und 9,9 Sekunden
            r.clock.advance(step)
            with self.assertRaises(PermissionError):
                r.n.test()
        self.assertEqual(len(r.chat.calls), 1)
        r.clock.advance(10)
        self.assertTrue(r.n.test()["ok"])

    def test_the_gap_to_the_last_message_is_kept(self):
        r = self.rig()
        r.cam(battery=5)
        r.tick()
        r.clock.advance(1)
        before = r.clock.m
        r.n.test()
        self.assertGreaterEqual(r.clock.m - before, 1.99)                                            # bis drei Sekunden nach der Warnung gewartet

    def test_demo_sends_nothing(self):
        r = self.rig()
        n = server.TwitchNotifier(r.store, r.dji, r.cams, r.send, chat=r.chat, demo=True)
        self.assertEqual(n.test(), {"ok": True, "message": "Demo: gesendet."})
        self.assertEqual(r.chat.calls, [])


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        old = server.Handler.twitch
        server.Handler.twitch = self.rig.n
        self.addCleanup(setattr, server.Handler, "twitch", old)

    @staticmethod
    def handler(path, body=None, authed=True, length=None):
        raw = body if isinstance(body, bytes) else json.dumps({} if body is None else body).encode()
        h = server.Handler.__new__(server.Handler)
        h.path, h.sent, h.hdrs = path, [], {}
        h.authed = lambda: authed
        h.send_response = lambda code, *a: h.sent.append(code)
        h.send_header = lambda k, v: h.hdrs.__setitem__(k, v)
        h.end_headers = lambda: None

        class W:
            data = b""

            def write(self, b):
                W.data += b
        h.wfile, h.out = W(), W
        h.headers = {"Content-Length": str(len(raw) if length is None else length)}
        h.rfile = type("R", (), {"read": lambda self, n: raw[:n]})()
        h.client_address = ("127.0.0.1", 1)
        return h

    def get(self, path, **kw):
        h = self.handler(path, **kw)
        h.do_GET()
        return h.sent[0], h.out.data.decode()

    def post(self, path, body=None, **kw):
        h = self.handler(path, body, **kw)
        h.do_POST()
        return h.sent[0], h.out.data.decode()

    def test_everything_needs_a_login(self):
        before = self.rig.store.settings()
        for fn, path in ((self.get, "/api/twitch"), (self.post, "/api/twitch"), (self.post, "/api/twitch/test")):
            code, text = fn(path, authed=False)
            self.assertEqual((code, json.loads(text)), (401, {"error": "nicht angemeldet"}), path)
        code, text = self.post("/api/twitch", {"threshold": 20, "token": "x" * 30}, authed=False)
        self.assertEqual(code, 401)
        self.assertEqual(self.rig.store.settings(), before)
        self.assertEqual(self.rig.chat.calls, [])

    def test_get_gives_the_settings_and_the_status_but_never_the_token(self):
        code, text = self.get("/api/twitch")
        d = json.loads(text)
        self.assertEqual(code, 200)
        self.assertNotIn(TOKEN, text)
        self.assertEqual((d["channel"], d["login"], d["token_set"], d["enabled"], d["threshold"]), ("meinkanal", "botkonto", True, True, 10))
        self.assertEqual(d["status"]["ok"], None)

    def test_post_saves_and_answers_without_the_token(self):
        new = "n" * 30
        code, text = self.post("/api/twitch", {"enabled": True, "channel": "Anderer", "login": "NeuerBot", "token": "oauth:" + new, "threshold": 25,
                                               "message": "Akku {Kamera} {Prozent}", "only_live": False})
        d = json.loads(text)
        self.assertEqual(code, 200)
        self.assertNotIn(new, text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual((d["channel"], d["login"], d["threshold"], d["message"], d["only_live"], d["token_set"]), ("anderer", "neuerbot", 25, "Akku {Kamera} {Prozent}", False, True))
        saved = json.load(open(self.rig.store.path))
        self.assertEqual(saved["token"], new)
        self.assertEqual(stat.S_IMODE(os.stat(self.rig.store.path).st_mode), 0o600)
        code, text = self.post("/api/twitch", {"token": ""})                                          # leeres Feld: Token bleibt
        self.assertEqual(code, 200)
        self.assertEqual(json.load(open(self.rig.store.path))["token"], new)

    def test_bad_input_is_a_400_with_a_short_message_and_changes_nothing(self):
        before = self.rig.store.settings()
        leak = "L" * 40
        for body in ({"channel": "bad name"}, {"login": "x"}, {"token": leak + " !"}, {"token": "oauth:" + leak + "\r\nJOIN #x"}, {"threshold": 0}, {"threshold": "10"}, {"message": "/ban"},
                     {"message": "a\nb"}, {"message": "x" * 301}, {"enabled": "ja"}, {"only_live": None}, {"enabled": True, "channel": ""}, [1, 2], "text", None, 5):
            code, text = self.post("/api/twitch", body if body is not None else b"null")
            d = json.loads(text)
            self.assertEqual(code, 400, repr(body))
            self.assertIn("error", d)
            self.assertLess(len(d["error"]), 120)
            self.assertNotIn(leak, text)
            self.assertNotIn(TOKEN, text)
        self.assertEqual(self.rig.store.settings(), before)

    def test_broken_or_oversized_requests_are_refused(self):
        code, text = self.post("/api/twitch", b"{kein json")
        self.assertEqual(code, 400)
        code, text = self.post("/api/twitch", {"message": "x" * 5000})
        self.assertEqual((code, json.loads(text)["error"]), (400, "Die Anfrage ist zu groß"))
        code, text = self.post("/api/twitch/test", {"pad": "x" * 5000})
        self.assertEqual((code, json.loads(text)["error"]), (400, "Die Anfrage ist zu groß"))
        code, text = self.post("/api/twitch", {}, length=-1)
        self.assertEqual(code, 400)
        self.assertEqual(self.rig.chat.calls, [])

    def test_the_test_endpoint_sends_and_is_rate_limited(self):
        code, text = self.post("/api/twitch/test")
        self.assertEqual((code, json.loads(text)), (200, {"ok": True, "message": "Gesendet"}))
        self.assertEqual(self.rig.chat.calls, [("botkonto", TOKEN, "meinkanal", "Test: IRL4YOU BOX")])
        code, text = self.post("/api/twitch/test")
        self.assertEqual(code, 429)
        self.assertIn("error", json.loads(text))
        self.assertEqual(len(self.rig.chat.calls), 1)
        self.assertNotIn(TOKEN, text)

    def test_a_failed_test_is_a_normal_answer_with_ok_false(self):
        self.rig.chat.results = [(False, "Keine Verbindung zu Twitch")]
        code, text = self.post("/api/twitch/test")
        self.assertEqual((code, json.loads(text)), (200, {"ok": False, "message": "Keine Verbindung zu Twitch"}))

    def test_the_test_needs_saved_data(self):
        rig = Rig(self, enabled=False)
        rig.store.set({"channel": ""})
        server.Handler.twitch = rig.n
        code, text = self.post("/api/twitch/test")
        self.assertEqual((code, json.loads(text)["error"]), (400, "Bitte Kanal, Bot-Konto und Token eintragen"))
        self.assertEqual(rig.chat.calls, [])

    def test_unknown_paths_and_methods(self):
        self.assertEqual(self.post("/api/twitch/unbekannt")[0], 404)
        self.assertEqual(self.get("/api/twitch/test")[0], 404)
        self.assertEqual(self.get("/api/twitch/")[0], 404)

    def test_a_write_error_is_a_503_with_a_plain_message(self):
        with mock.patch.object(server.os, "replace", side_effect=OSError("voll " + TOKEN)):
            code, text = self.post("/api/twitch", {"threshold": 12})
        self.assertEqual((code, json.loads(text)["error"]), (503, "Die Einstellungen konnten nicht gespeichert werden"))
        self.assertNotIn(TOKEN, text)


class NotInTheBackup(unittest.TestCase):
    def test_neither_token_nor_settings_are_part_of_the_settings_backup(self):
        d = tempfile.mkdtemp()
        cams = server.CameraStore(os.path.join(d, "cameras.json"), "publish", "", True)
        cams.add("Kamera vorn", "cam-vorn", "main")
        pipeline = server.PipelineStore(os.path.join(d, "pipeline.json"))
        srtla = server.SrtlaStore(os.path.join(d, "srtla.json"))
        names = server.DeviceNames(d)
        send = server.SendControl(d, srtla, pipeline, cams, demo=True)
        autostart = server.AutoStart(d, send, True)
        wifi = server.Wifi(d, False, None, names, srtla)
        wifi.helper_call = mock.Mock(side_effect=AssertionError("kein Helfer im Test"))
        t = server.SettingsTransfer(d, cams, pipeline, srtla, autostart, names, server.DjiService(d, cams, "publish", 1935, True), wifi, send, True)
        server.TwitchStore(os.path.join(d, "twitch.json")).set(CFG)
        for secrets_on in (True, False):
            text = json.dumps(t.make_document(secrets_on), ensure_ascii=False)
            self.assertNotIn(TOKEN, text)                      # der Token nie; die übrigen Angaben der Akku-Warnung sind seit 0.9.12x ein eigener Abschnitt der Sicherung


class DemoServer(unittest.TestCase):
    """Der ganze Weg im Demo-Modus (--demo): echter Server, Anmeldung, Endpunkte. Es geht nichts ins Netz: der Test liefert "Demo: gesendet." und der Dienst ist aus."""

    def setUp(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.state = tempfile.mkdtemp()
        self.output = None
        self.proc = subprocess.Popen([sys.executable, "-u", "-W", "ignore", os.path.join(ROOT, "server.py"), "--demo", "--host", "127.0.0.1", "--port", str(port), "--state", self.state],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop)
        self.base = "http://127.0.0.1:%d" % port
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        ok = wait_for(lambda: self.alive(), 20)
        if not ok:
            self.fail("Demo-Server startet nicht")

    def stop(self):
        """Den Server beenden und alles zurückgeben, was er ausgegeben hat (so etwas landet im Journal)."""
        if self.output is None:
            self.proc.terminate()
            try:
                self.output = self.proc.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.output = self.proc.communicate()[0]
        return self.output

    def alive(self):
        try:
            return self.call("GET", "/api/auth")[0] == 200
        except OSError:
            return False

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), method=method, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_full_round_trip_in_demo_mode(self):
        self.assertEqual(self.call("GET", "/api/twitch")[0], 401)
        self.assertEqual(self.call("POST", "/api/twitch/test", {})[0], 401)
        self.assertEqual(self.call("POST", "/api/login", {"password": "demo"})[0], 200)
        code, d = self.call("GET", "/api/twitch")
        self.assertEqual((code, d["enabled"], d["token_set"], d["threshold"]), (200, False, False, 10))
        code, d = self.call("POST", "/api/twitch/test", {})
        self.assertEqual((code, d["error"]), (400, "Bitte Kanal, Bot-Konto und Token eintragen"))
        code, d = self.call("POST", "/api/twitch", {"enabled": True, "channel": "MeinKanal", "login": "BotKonto", "token": "oauth:" + TOKEN, "threshold": 12})
        self.assertEqual((code, d["token_set"], d["channel"], d["threshold"]), (200, True, "meinkanal", 12))
        self.assertNotIn(TOKEN, json.dumps(d))
        code, d = self.call("GET", "/api/twitch")
        self.assertNotIn(TOKEN, json.dumps(d))
        self.assertEqual(self.call("POST", "/api/twitch/test", {}), (200, {"ok": True, "message": "Demo: gesendet."}))
        self.assertEqual(self.call("POST", "/api/twitch/test", {})[0], 429)
        code, d = self.call("GET", "/api/twitch")
        self.assertEqual((d["status"]["ok"], d["status"]["text"]), (True, "Test: IRL4YOU BOX"))
        path = os.path.join(self.state, "twitch.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(json.load(open(path))["token"], TOKEN)
        out = self.stop()
        self.assertIn("PIPBOX auf", out)                                                             # es war wirklich der Server
        self.assertNotIn(TOKEN, out)                                                                 # und er hat den Token nicht ausgegeben


# ---------------------------------------------------------------------------------------------------------------- Oberfläche
def func(name):
    """Quelltext einer Funktion der Seite ("function name" oder "async function name") bis zu ihrer schließenden Klammer in der ersten Spalte."""
    m = re.search(r"^(async )?function %s\(.*?^\}" % re.escape(name), PAGE, re.S | re.M)
    assert m, name
    return m.group(0)


def line(pattern):
    m = re.search(pattern, PAGE, re.M)
    assert m, pattern
    return m.group(0)


SECTION = PAGE[PAGE.index('<details class="dsec" id="tw_sec">'):]
SECTION = SECTION[:SECTION.index("</details>") + len("</details>")]
BLOCK = PAGE[PAGE.index("// ---- Akku-Warnung im Twitch-Chat"):PAGE.index("let netBusy=false;")]
IDS = ("tw_sec", "tw_sum", "tw_on", "tw_channel", "tw_login", "tw_token", "tw_thr", "tw_msg", "tw_live", "tw_save", "tw_test", "tw_status", "tw_err")


class Markup(unittest.TestCase):
    def test_all_ids_exist_exactly_once(self):
        for i in IDS:
            self.assertEqual(PAGE.count('id="%s"' % i), 1, i)

    def test_it_is_a_collapsible_subsection_of_the_dji_cameras_after_the_error_line(self):
        self.assertRegex(PAGE, r'<div id="djierr" class="err"></div>\s*<details class="dsec" id="tw_sec"><summary>Akku-Warnung im Twitch-Chat <span class="muted" id="tw_sum"></span></summary>')
        self.assertLess(PAGE.index('id="c_cams"'), PAGE.index('id="djicard"'))
        self.assertLess(PAGE.index('id="djicard"'), PAGE.index('id="tw_sec"'))
        self.assertLess(PAGE.index('id="tw_sec"'), PAGE.index('id="pipecard"'))                      # noch in der Karte "Kameras"
        self.assertRegex(PAGE, r'</details>\s*</div>\s*<div class="sech">HDMI- und USB-Kameras</div>')                # Abschnitt, dann Ende von #djicard, dann der HDMI-Abschnitt
        self.assertRegex(PAGE, r'</div>\s*</details>\s*<details class="card wide" id="pipecard"')                      # danach das Ende der Karte "Kameras"
        self.assertNotIn("card", re.search(r'<details class="[^"]*" id="tw_sec"', PAGE).group(0))   # keine neue Karte, kein neuer Hauptpunkt

    @staticmethod
    def css(selector):
        """Die Angaben einer CSS-Regel der Seite (erste Spalte) als dict."""
        m = re.search(r"^%s\{([^}]*)\}" % re.escape(selector), PAGE, re.M)
        assert m, selector
        return dict(d.split(":", 1) for d in m.group(1).split(";") if d.strip())

    def test_the_title_looks_like_the_headings_of_the_card_and_still_opens_and_closes(self):
        sech, title = self.css(".sech"), self.css("#tw_sec>summary")
        for prop in ("font-size", "text-transform", "letter-spacing", "color"):                      # Schrift und Farbe wie "DJI-Kameras (Bluetooth)"
            self.assertEqual(title[prop], sech[prop], prop)
        self.assertEqual(title["margin"].split()[-1], sech["margin"].split()[-1])                    # gleicher Abstand nach unten
        self.assertEqual((title["font-size"], title["text-transform"], title["color"]), ("12px", "uppercase", "var(--accent)"))
        self.assertNotIn("#", self.css("#tw_sec>summary")["color"])                                  # Farbe nur über die Variablen: dunkel und hell
        self.assertEqual(title["list-style"], "none")                                                # statt des Dreiecks des Browsers das übliche der Karten:
        self.assertEqual(self.css("#tw_sec>summary::-webkit-details-marker"), {"display": "none"})
        card_open, card_shut = self.css('details.card[open]>summary .sumh::before'), self.css('details.card>summary .sumh::before')
        self.assertEqual(self.css("#tw_sec>summary::before")["content"], card_shut["content"])
        self.assertEqual(self.css("#tw_sec[open]>summary::before")["content"], card_open["content"])
        self.assertEqual(self.css("#tw_sec>summary::before")["content"], '"\\25B8  "')
        self.assertEqual(self.css("#tw_sec[open]>summary::before")["content"], '"\\25BE  "')
        self.assertEqual(self.css("#tw_sum"), {"white-space": "nowrap"})                              # "· an" bricht nur als Ganzes um (Handy, 320 px)
        self.assertRegex(SECTION, r'^<details class="dsec" id="tw_sec"><summary>Akku-Warnung im Twitch-Chat <span class="muted" id="tw_sum"></span></summary>')
        self.assertNotRegex(BLOCK, r'\$\("tw_sec"\)\.addEventListener\("click"')                       # die Bedienung ist die des Browsers (details/summary), kein eigener Klick
        self.assertNotRegex(BLOCK, r'\.querySelector\("summary"\)')

    def test_field_order_channel_first_then_bot_then_token(self):
        order = ["tw_on", "tw_channel", "tw_login", "tw_token", "tw_thr", "tw_msg", "tw_live", "tw_save", "tw_test", "tw_status", "tw_err"]
        pos = [SECTION.index('id="%s"' % i) for i in order]
        self.assertEqual(pos, sorted(pos))
        inputs = re.findall(r'<input id="(\w+)"', SECTION)
        self.assertEqual(inputs[:3], ["tw_channel", "tw_login", "tw_token"])                         # Kanal ist das erste Feld

    def test_short_labels_as_asked(self):
        for text in ("Kanal (Konto, auf dem gestreamt wird)", "Bot-Konto (Name)", "Token des Bot-Kontos", "Warnen bei (%)", "Einschalten", "Nur während der Sendung",
                     "Speichern", "Testnachricht senden", "Akku-Warnung im Twitch-Chat"):
            self.assertIn(text, SECTION, text)
        self.assertNotIn("leer = Name des Bots", SECTION)                                            # entfallen: der Kanal ist ein eigenes Pflichtfeld
        self.assertIn("{Kamera}", SECTION)
        self.assertIn("{Prozent}", SECTION)

    def test_channel_and_bot_name_are_hidden_in_stream_mode(self):
        self.assertIn("body.sm .sens{display:none!important}", PAGE)
        self.assertRegex(SECTION, r'<label class="f sens">Kanal \(Konto, auf dem gestreamt wird\)<input id="tw_channel"')
        self.assertRegex(SECTION, r'<label class="f sens">Bot-Konto \(Name\)<input id="tw_login"')
        self.assertNotRegex(SECTION, r'<label class="[^"]*sens[^"]*">Token des Bot-Kontos')

    def test_the_token_field_is_a_password_field_without_a_value_and_without_autofill(self):
        tag = re.search(r'<input id="tw_token"[^>]*>', SECTION).group(0)
        self.assertIn('type="password"', tag)
        self.assertIn('autocomplete="new-password"', tag)
        self.assertNotIn("value=", tag)
        for i in ("tw_channel", "tw_login", "tw_msg"):
            self.assertIn('autocomplete="off"', re.search(r'<input id="%s"[^>]*>' % i, SECTION).group(0))

    def test_no_intro_text_only_one_short_hint(self):
        self.assertEqual(SECTION.count("data-hlp"), 1)
        hint = re.search(r'<div class="ph" data-hlp>(.*?)</div>', SECTION).group(1)
        self.assertEqual(hint, "Token mit dem Recht „chat:edit“, wie bei NOALBS. Er muss zum Bot-Konto gehören.")
        self.assertLess(len(hint), 120)
        self.assertNotIn("<p", SECTION)
        text = " ".join(re.sub(r"<[^>]+>", " ", SECTION).split())
        self.assertLess(len(text), 450)
        self.assertRegex(SECTION, r'<div id="tw_status" class="ph" role="status"></div>')           # eine Meldung (Kennung, role), kein Hilfstext: bleibt auf dem Handy sichtbar

    def test_inputs_have_limits(self):
        self.assertIn('id="tw_channel" maxlength="25"', SECTION)
        self.assertIn('id="tw_login" maxlength="25"', SECTION)
        self.assertIn('id="tw_msg" maxlength="300"', SECTION)
        self.assertRegex(SECTION, r'id="tw_thr" type="number" min="1" max="50" step="1"')
        self.assertRegex(SECTION, r'<input type="checkbox" id="tw_live">')                           # "Nur während der Sendung" wird mit dem Server-Wert gesetzt (Standard an)
        self.assertRegex(SECTION, r'<button id="tw_test" type="button" class="sec" disabled>')       # erst nach dem Speichern


class Script(unittest.TestCase):
    def test_every_call_goes_through_srtlaCall(self):
        for needle in ('srtlaCall("GET","/api/twitch")', 'srtlaCall("POST","/api/twitch",twForm())', 'srtlaCall("POST","/api/twitch/test",{})'):
            self.assertIn(needle, BLOCK)
        self.assertNotIn("fetch(", BLOCK)
        self.assertNotIn("XMLHttpRequest", BLOCK)

    def test_the_token_is_never_written_into_the_page_or_kept_in_the_browser(self):
        fill = func("twFill")
        self.assertEqual(re.findall(r"d\.token\w*", fill), ["d.token_set"])                          # nur ob einer gespeichert ist
        self.assertIn('$("tw_token").value=""', fill)
        self.assertEqual(len(re.findall(r'\$\("tw_token"\)\.value', BLOCK)), 2)                       # leeren (twFill) und lesen (twForm)
        for bad in ("localStorage", "sessionStorage", "document.cookie", "indexedDB", "console.", "alert("):
            self.assertNotIn(bad, BLOCK, bad)
        self.assertIn('placeholder=d.token_set?"gespeichert":""', fill)

    def test_texts_from_the_server_are_escaped_and_fields_are_set_as_values(self):
        self.assertNotIn("innerHTML", BLOCK)
        status = func("twStatusHtml")
        for expr in re.findall(r"\$\{([^}]*)\}", status):
            self.assertRegex(expr, r"^(esc\(.*\)|t|twTime\(.*\))$", expr)                            # nur Uhrzeiten und mit esc() eingefügte Texte
        self.assertIn('<span translate="no">${esc(s.text)}</span>', status)                          # die eingestellte Nachricht wird nicht übersetzt

    def test_wiring(self):
        for needle in ('$("tw_save").addEventListener("click",twSave);', '$("tw_test").addEventListener("click",twTest);', 'due("tw","c_cams",60000)',
                       '["input","change"].forEach(ev=>$("tw_sec").addEventListener(ev,()=>{ twDirty=true; twButtons(); }));',
                       '$("tw_sec").addEventListener("toggle",()=>{ if($("tw_sec").open) twLoad(); });'):
            self.assertIn(needle, BLOCK, needle)

    def test_the_page_script_still_compiles(self):
        if not JSC:
            self.skipTest("keine JavaScript-Maschine (jsc)")
        scripts = "\n".join(re.findall(r"<script>(.*?)</script>", PAGE, re.S))
        self.assertEqual(run_jsc("try { new Function(%s); print('ok'); } catch (e) { print('FEHLER ' + e); }" % json.dumps(scripts)), "ok")


def run_jsc(src):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
    try:
        r = subprocess.run([JSC, f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout.strip()


STUBS = (
    "var els = {};\n"
    "function mk(id) { return {id: id, value: '', textContent: '', innerHTML: '', placeholder: '', title: '', hidden: false, disabled: false, checked: false, dataset: {}, style: {}}; }\n"
    "function $(id) { return els[id] || (els[id] = mk(id)); }\n"
    + line(r"^const esc=.*$") + "\n" + line(r"^function setHtml.*$") + "\n"
    "var LOC = function () { return 'en-GB'; };\n"
    "var calls = [], replies = {}, failWith = null;\n"
    "function srtlaCall(method, url, body) { calls.push([method, url, body === undefined ? null : JSON.parse(JSON.stringify(body))]); if (failWith) return Promise.reject(new Error(failWith));\n"
    "  var r = replies[method + ' ' + url]; return Promise.resolve(r === undefined ? {} : JSON.parse(JSON.stringify(r))); }\n"
    + line(r"^let twData.*$") + "\n" + line(r"^const twTime=.*$") + "\n"
    + "\n".join(func(n) for n in ("twStatusHtml", "twButtons", "twFill", "twLoad", "twForm", "twSave", "twTest")) + "\n"
)
REPLY = {"enabled": True, "channel": "meinkanal", "login": "botkonto", "token_set": True, "threshold": 12, "message": "Akku {Kamera}", "only_live": True, "status": {"ok": None}}


@unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
class PageScripts(unittest.TestCase):
    def run_case(self, body):
        """`body` läuft hinter den Attrappen; es gibt per print() eine JSON-Zeile aus."""
        return json.loads(run_jsc(STUBS + body).splitlines()[-1])

    def test_the_token_never_comes_back_into_the_page_whatever_the_server_says(self):
        o = self.run_case("twFill(Object.assign({}, %s, {token: 'GEHEIM-LECK-123', password: 'GEHEIM-LECK-123'}));"
                          "print(JSON.stringify({token: $('tw_token').value, ph: $('tw_token').placeholder, all: JSON.stringify(els)}));" % json.dumps(REPLY))
        self.assertEqual((o["token"], o["ph"]), ("", "gespeichert"))
        self.assertNotIn("GEHEIM-LECK-123", o["all"])
        o = self.run_case("twFill(Object.assign({}, %s, {token_set: false})); print(JSON.stringify({ph: $('tw_token').placeholder}));" % json.dumps(REPLY))
        self.assertEqual(o["ph"], "")

    def test_fill_sets_all_fields_and_the_summary(self):
        o = self.run_case("twFill(%s); print(JSON.stringify({on: $('tw_on').checked, ch: $('tw_channel').value, lo: $('tw_login').value, thr: $('tw_thr').value, msg: $('tw_msg').value,"
                          " live: $('tw_live').checked, sum: $('tw_sum').textContent, test: $('tw_test').disabled}));" % json.dumps(REPLY))
        self.assertEqual(o, {"on": True, "ch": "meinkanal", "lo": "botkonto", "thr": 12, "msg": "Akku {Kamera}", "live": True, "sum": "· an", "test": False})
        o = self.run_case("twFill(Object.assign({}, %s, {enabled: false, only_live: false})); print(JSON.stringify({on: $('tw_on').checked, live: $('tw_live').checked, sum: $('tw_sum').textContent}));" % json.dumps(REPLY))
        self.assertEqual(o, {"on": False, "live": False, "sum": "· aus"})

    def test_an_error_in_the_status_line_is_red(self):
        o = self.run_case("var d = %s; d.status = {ok: false, time: 1760000000, text: 'Keine Verbindung zu Twitch', retry_at: null}; twFill(d); var red = $('tw_status').style.color;"
                          "d.status = {ok: true, time: 1760000000, text: 'x'}; twFill(d); var green = $('tw_status').style.color; d.status = {ok: null}; twFill(d);"
                          "print(JSON.stringify({red: red, ok: green, none: $('tw_status').style.color}));" % json.dumps(REPLY))
        self.assertEqual(o, {"red": "var(--crit)", "ok": "", "none": ""})

    def test_unsaved_input_is_not_overwritten_by_the_regular_refresh(self):
        o = self.run_case("twDirty = true; $('tw_msg').value = 'mein Entwurf'; $('tw_token').value = 'neu-getippt'; twFill(%s);"
                          "print(JSON.stringify({msg: $('tw_msg').value, token: $('tw_token').value, status: $('tw_status').innerHTML, test: $('tw_test').disabled, title: $('tw_test').title}));" % json.dumps(REPLY))
        self.assertEqual((o["msg"], o["token"], o["test"], o["title"]), ("mein Entwurf", "neu-getippt", True, "Zuerst speichern"))
        self.assertIn("Noch nichts gesendet", o["status"])                                           # der Status wird trotzdem aktualisiert

    def test_save_sends_the_form_clears_the_token_field_and_shows_the_saved_values(self):
        o = self.run_case("replies['POST /api/twitch'] = %s; twDirty = true; $('tw_on').checked = true; $('tw_channel').value = ' MeinKanal '; $('tw_login').value = 'BotKonto';"
                          "$('tw_token').value = 'oauth:abc'; $('tw_thr').value = '12'; $('tw_msg').value = 'Akku {Kamera}'; $('tw_live').checked = true;"
                          "twSave().then(function () { print(JSON.stringify({calls: calls, token: $('tw_token').value, dirty: twDirty, err: $('tw_err').textContent, color: $('tw_err').style.color,"
                          " ch: $('tw_channel').value, save: $('tw_save').disabled, test: $('tw_test').disabled})); });" % json.dumps(REPLY))
        self.assertEqual(o["calls"], [["POST", "/api/twitch", {"enabled": True, "channel": " MeinKanal ", "login": "BotKonto", "token": "oauth:abc", "threshold": 12,
                                                              "message": "Akku {Kamera}", "only_live": True}]])
        self.assertEqual((o["token"], o["dirty"], o["err"], o["color"], o["ch"], o["save"], o["test"]), ("", False, "Gespeichert.", "var(--ok)", "meinkanal", False, False))

    def test_a_refused_save_keeps_the_input_and_shows_the_message(self):
        msg = "Kanal: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _"
        o = self.run_case("failWith = %s; twDirty = true; $('tw_channel').value = 'x'; $('tw_token').value = 'oauth:abc'; $('tw_thr').value = '12';"
                          "twSave().then(function () { print(JSON.stringify({err: $('tw_err').textContent, color: $('tw_err').style.color, dirty: twDirty, ch: $('tw_channel').value, token: $('tw_token').value,"
                          " save: $('tw_save').disabled})); });" % json.dumps(msg))
        self.assertEqual(o, {"err": msg, "color": "", "dirty": True, "ch": "x", "token": "oauth:abc", "save": False})

    def test_an_empty_number_field_is_sent_as_nothing_and_the_server_refuses_it(self):
        o = self.run_case("$('tw_thr').value = ''; var f = twForm(); print(JSON.stringify({nan: Number.isNaN(f.threshold), sent: JSON.stringify(f).indexOf('\"threshold\":null') >= 0}));")
        self.assertEqual(o, {"nan": True, "sent": True})

    def test_the_test_button_sends_nothing_but_asks_for_the_test_and_then_refreshes(self):
        o = self.run_case("replies['POST /api/twitch/test'] = {ok: true, message: 'Gesendet'}; replies['GET /api/twitch'] = %s;"
                          "twTest().then(function () { print(JSON.stringify({calls: calls, err: $('tw_err').textContent, color: $('tw_err').style.color, test: $('tw_test').disabled})); });" % json.dumps(REPLY))
        self.assertEqual(o["calls"], [["POST", "/api/twitch/test", {}], ["GET", "/api/twitch", None]])
        self.assertEqual((o["err"], o["color"]), ("Gesendet", "var(--ok)"))
        o = self.run_case("replies['POST /api/twitch/test'] = {ok: false, message: 'Keine Verbindung zu Twitch'}; twTest().then(function () { print(JSON.stringify({err: $('tw_err').textContent, color: $('tw_err').style.color})); });")
        self.assertEqual(o, {"err": "Keine Verbindung zu Twitch", "color": ""})
        o = self.run_case("failWith = 'Bitte kurz warten'; twTest().then(function () { print(JSON.stringify({err: $('tw_err').textContent, color: $('tw_err').style.color, test: $('tw_test').disabled})); });")
        self.assertEqual(o["err"], "Bitte kurz warten")

    def test_buttons_follow_the_state(self):
        full = "twData = {token_set: true, channel: 'k', login: 'b'};"
        cases = (("", "twData = null;", True), (full, "", False), (full, "twDirty = true;", True), (full, "twBusy = true;", True),
                 ("twData = {token_set: false, channel: 'k', login: 'b'};", "", True), ("twData = {token_set: true, channel: '', login: 'b'};", "", True),
                 ("twData = {token_set: true, channel: 'k', login: ''};", "", True))
        for setup, extra, disabled in cases:
            o = self.run_case("%s %s twButtons(); print(JSON.stringify({test: $('tw_test').disabled, save: $('tw_save').disabled}));" % (setup, extra))
            self.assertEqual(o["test"], disabled, (setup, extra))
        o = self.run_case("twBusy = true; twButtons(); print(JSON.stringify({save: $('tw_save').disabled}));")
        self.assertTrue(o["save"])

    def test_status_line(self):
        def html(status, enabled=True):
            return self.run_case("print(JSON.stringify({h: twStatusHtml({enabled: %s, status: %s})}));" % (json.dumps(enabled), json.dumps(status)))["h"]
        self.assertEqual(html({"ok": None}, enabled=False), "")
        self.assertIn("Noch nichts gesendet", html({"ok": None}))
        ok = html({"ok": True, "time": 1760000000, "text": MSG % ("Action 4", 9)})
        self.assertRegex(ok, r"^<span>Letzte Meldung um \d{1,2}:\d{2} Uhr:</span> <span translate=\"no\">Akkustand niedrig, bitte Akku wechseln: Action 4 \(9 %\)</span>$")
        err = html({"ok": False, "time": 1760000000, "text": "Keine Verbindung zu Twitch", "retry_at": 1760000030})
        self.assertRegex(err, r"^<span>Fehler um \d{1,2}:\d{2} Uhr:</span> <span>Keine Verbindung zu Twitch</span> <span>Neuer Versuch um \d{1,2}:\d{2} Uhr\.</span>$")
        self.assertNotIn("Neuer Versuch", html({"ok": False, "time": 1760000000, "text": "x", "retry_at": None, "gave_up": True}))
        evil = "<img src=x onerror=alert(1)>&\""
        for st in ({"ok": True, "time": 1760000000, "text": evil}, {"ok": False, "time": 1760000000, "text": evil}):
            h = html(st)
            self.assertNotIn("<img", h)
            self.assertIn("&lt;img", h)


# ---------------------------------------------------------------------------------------------------------------- Übersetzung
ENGINE = open(os.path.join(ROOT, "web", "i18n.js"), encoding="utf-8").read()
NEW_TEXTS = ["Akku-Warnung im Twitch-Chat", "Einschalten", "Kanal (Konto, auf dem gestreamt wird)", "Bot-Konto (Name)", "Token des Bot-Kontos", "Warnen bei (%)",
             "Nachricht ({Kamera} und {Prozent} werden ersetzt)", "Nur während der Sendung", "Testnachricht senden", "Zuerst speichern", "Wird gesendet …", "Noch nichts gesendet",
             "Letzte Meldung um {1} Uhr:", "Fehler um {1} Uhr:", "Neuer Versuch um {1} Uhr.", "gespeichert",
             "Token mit dem Recht „chat:edit“, wie bei NOALBS. Er muss zum Bot-Konto gehören.",
             "Akkustand niedrig, bitte Akku wechseln: {1} ({2} %)", "Test: IRL4YOU BOX", "Demo: gesendet.", "Bitte kurz warten", "Gesendet",
             "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)", "Keine Verbindung zu Twitch", "Keine Antwort von Twitch (Zeitüberschreitung)",
             "Twitch hat die Verbindung beendet", "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower)",
             "Sichere Verbindung zu Twitch nicht möglich (Zertifikat oder Uhrzeit der Box prüfen)", "Die Einstellungen konnten nicht gespeichert werden",
             "Kanal: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _", "Bot-Konto: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _",
             "Token: 20 bis 100 Zeichen, nur Buchstaben, Ziffern, _ und -", "Warnen bei: Zahl von 1 bis 50", "Nachricht: 1 bis 300 Zeichen",
             "Nachricht: keine Sonderzeichen oder Zeilenumbrüche", "Nachricht: darf nicht mit / oder . beginnen", "Bitte Kanal, Bot-Konto und Token eintragen"]


def translate(texts):
    """Die Texte durch die echte Übersetzungs-Engine mit dem echten englischen Wörterbuch schicken."""
    code = ("var window = {}; var self = window; var navigator = {};\n" + ENGINE + "\nvar I = window.PB_I18N; I._load(%s, 'en');\nprint(JSON.stringify(%s.map(function (t) { return I.tr(t); })));"
            % (json.dumps(EN), json.dumps(texts)))
    return json.loads(run_jsc(code))


class Translation(unittest.TestCase):
    def test_every_new_text_is_a_key_and_has_an_english_text(self):
        keys = set(i18n_extract.all_keys())
        for text in NEW_TEXTS:
            self.assertIn(text, keys, text)
            self.assertTrue(EN["exact"].get(text), text)
            self.assertEqual(sorted(re.findall(r"\{\d+\}", text)), sorted(re.findall(r"\{\d+\}", EN["exact"][text])), text)

    def test_every_message_the_code_can_show_is_among_the_new_texts_or_known(self):
        keys = set(i18n_extract.all_keys())
        chat, store = server.TwitchChat, server.TwitchStore
        for text in (chat.OK, chat.NO_CONNECT, chat.NO_TLS, chat.LOGIN_FAILED, chat.NO_ANSWER, chat.CLOSED, chat.NOT_ALLOWED, store.ERR_CHANNEL, store.ERR_LOGIN, store.ERR_MISSING,
                     server.TwitchNotifier.TEST_TEXT, "Ungültige Anfrage", "unbekannter Fehler", "ja oder nein", "Bitte kurz warten", "Demo: gesendet."):
            self.assertIn(text, keys, text)
            self.assertTrue(EN["exact"].get(text), text)

    def test_the_english_texts_keep_the_german_placeholders_of_the_message(self):
        label = EN["exact"]["Nachricht ({Kamera} und {Prozent} werden ersetzt)"]
        self.assertIn("{Kamera}", label)                                                             # der Server ersetzt nur diese beiden Namen
        self.assertIn("{Prozent}", label)

    @unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
    def test_the_engine_translates_the_status_lines_and_help_texts(self):
        out = translate(["Letzte Meldung um 14:32 Uhr:", "Fehler um 14:35 Uhr:", "Neuer Versuch um 14:36 Uhr.", "Noch nichts gesendet", "gespeichert", "Zuerst speichern",
                         "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower)",
                         "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower): This room is in followers-only mode. Follow the channel!",
                         "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower): Your message was not sent because it is identical to the previous message you sent.",
                         "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)", "· an", "· aus"])
        self.assertEqual(out[:7], ["Last message at 14:32:", "Error at 14:35:", "Next attempt at 14:36.", "Nothing sent yet", "saved", "Save first",
                                   "The bot is not allowed to write in the channel (e.g. followers only)"])
        self.assertEqual(out[7], "The bot is not allowed to write in the channel (e.g. followers only): This room is in followers-only mode. Follow the channel!")
        self.assertEqual(out[8], "The bot is not allowed to write in the channel (e.g. followers only): Your message was not sent because it is identical to the previous message you sent.")
        self.assertEqual(out[9], "Login failed (token invalid or without permission to write)")
        self.assertEqual(out[10:], ["· on", "· off"])

    @unittest.skipUnless(JSC, "keine JavaScript-Maschine (jsc) auf diesem Rechner")
    def test_the_texts_of_the_status_line_translate_node_by_node(self):
        """Wie die Seite sie baut: jedes <span> ist ein eigener Textknoten und wird für sich übersetzt; die eingestellte Nachricht (translate="no") bleibt, wie sie ist."""
        run = ("var t = twStatusHtml({enabled: true, status: {ok: false, time: 1760000000, text: 'Keine Verbindung zu Twitch', retry_at: 1760000030}});"
               "var parts = []; t.replace(/<span([^>]*)>(.*?)<\\/span>/g, function (all, attr, text) { if (attr.indexOf('translate') < 0) parts.push(text); return all; });"
               "print(JSON.stringify(parts));")
        parts = json.loads(run_jsc(STUBS + run))
        self.assertEqual(len(parts), 3)
        out = translate(parts)
        self.assertRegex(out[0], r"^Error at \d{1,2}:\d{2}:$")
        self.assertEqual(out[1], "No connection to Twitch")
        self.assertRegex(out[2], r"^Next attempt at \d{1,2}:\d{2}\.$")
        for text in out:
            self.assertNotRegex(text, r"[äöüß]|Uhr|Fehler|Versuch")


if __name__ == "__main__":
    unittest.main()
