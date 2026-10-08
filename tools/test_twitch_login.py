"""Tests für die Anmeldung beim Twitch-Konto per Geräte-Code (server.TwitchLogin), die Einbindung in die Twitch-Einstellungen (TwitchStore) und das Senden aus der
Oberfläche (TwitchSender). Alles läuft gegen einen Fake-Twitch auf 127.0.0.1 mit erfundenen Werten; es wird nie eine Verbindung zu twitch.tv aufgebaut."""
import http.server
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402


class FakeTwitch(http.server.BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        st = FakeTwitch.state
        n = int(self.headers.get("Content-Length", 0))
        f = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
        st.setdefault("seen", []).append((self.path, f))
        if self.path == "/oauth2/device":
            if st.get("device_status"):
                return self._send(st["device_status"], {"status": st["device_status"], "message": "kaputt"})
            if f.get("client_id") != st["client_id"]:
                return self._send(400, {"status": 400, "message": "invalid client"})
            return self._send(200, {"device_code": "DC1", "expires_in": 1800, "interval": 2, "user_code": "ABCDEFGH",
                                    "verification_uri": "https://www.twitch.tv/activate?device-code=ABCDEFGH"})
        if self.path == "/oauth2/token":
            if f.get("grant_type") == server.TwitchLogin.GRANT:
                st["polls"] = st.get("polls", 0) + 1
                if st.get("deny"):
                    return self._send(400, {"status": 400, "message": "access_denied"})
                if st["polls"] < 3:
                    return self._send(400, {"status": 400, "message": "authorization_pending"})
                st["n"] = 1
                sc = f.get("scopes", "").split()
                st["scopes"] = sc
                return self._send(200, {"access_token": "AT1", "expires_in": 14400, "refresh_token": "RT1", "scope": sc, "token_type": "bearer"})
            if f.get("grant_type") == "refresh_token":
                if st.get("reject_refresh") or f.get("refresh_token") != "RT%d" % st["n"]:
                    return self._send(400, {"status": 400, "message": "Invalid refresh token"})
                st["n"] += 1
                return self._send(200, {"access_token": "AT%d" % st["n"], "expires_in": 14400, "refresh_token": "RT%d" % st["n"], "scope": ["chat:read", "chat:edit"]})
        if self.path == "/oauth2/revoke":
            st["revoked"] = f.get("token")
            return self._send(200, {})
        self._send(404, {})

    def do_GET(self):
        if self.path == "/oauth2/validate" and self.headers.get("Authorization", "").startswith("OAuth AT"):
            return self._send(200, {"client_id": "x", "login": FakeTwitch.state.get("login", "Streamer"), "scopes": FakeTwitch.state.get("scopes", ["chat:read", "chat:edit"]), "user_id": "42", "expires_in": 14000})
        self._send(401, {"status": 401, "message": "invalid access token"})


class Base(unittest.TestCase):
    def setUp(self):
        FakeTwitch.state = {"client_id": "testclient"}
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeTwitch)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d/oauth2/" % self.srv.server_address[1]
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "twitch-login.json")
        self.now = [1000.0]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, **kw):
        return server.TwitchLogin(self.path, client_id="testclient", id_base=self.base, clock=lambda: self.now[0], sleep=lambda s: time.sleep(0.01), **kw)

    def wait(self, tl, state, secs=5):
        end = time.time() + secs
        while time.time() < end and tl.state != state:
            time.sleep(0.02)
        self.assertEqual(tl.state, state)


class Login(Base):
    def test_device_flow_end_to_end(self):
        tl = self.make()
        st = tl.start()
        self.assertEqual(st["state"], "wartet")
        self.assertEqual(st["code"], "ABCDEFGH")
        self.assertIn("activate", st["uri"])
        self.wait(tl, "angemeldet")
        self.assertEqual(tl.login(), "streamer")
        self.assertTrue(tl.ready())
        self.assertEqual(tl.token(), "AT1")
        sent = [f for path, f in FakeTwitch.state["seen"] if path == "/oauth2/device"][0]
        self.assertEqual(sent["scopes"], "chat:read chat:edit user:write:chat")   # nur Lesen und Schreiben im Chat (Senden über die Twitch-Schnittstelle)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        pub = json.dumps(tl.status())
        self.assertNotIn("AT1", pub)
        self.assertNotIn("RT1", pub)
        again = self.make()                                                            # nach einem Neustart der Box noch angemeldet
        self.assertEqual((again.state, again.login()), ("angemeldet", "streamer"))

    def test_refresh_rotates_the_single_use_refresh_token_and_saves_it(self):
        tl = self.make()
        tl.start()
        self.wait(tl, "angemeldet")
        self.now[0] += 14400 - 30                                                      # kurz vor dem Ablauf
        self.assertEqual(tl.token(), "AT2")                                            # token() erneuert sofort
        self.assertEqual(json.load(open(self.path))["refresh"], "RT2")                  # der neue Erneuerungsschlüssel steht schon auf der Platte
        self.now[0] += 14000
        self.assertTrue(tl.refresh())
        self.assertEqual(tl.token(), "AT3")

    def test_new_refresh_token_is_saved_before_the_account_check_and_login_appears_only_complete(self):
        tl = self.make()
        tl.start()
        self.wait(tl, "angemeldet")
        self.now[0] += 14400 - 30
        seen = {}
        orig = tl._call
        def spy(url, fields=None, headers=None, once=False):
            if url.endswith("/validate"):
                seen["on_disk"] = json.load(open(self.path))["refresh"]              # beim Prüfen des Kontos steht der neue Schlüssel schon auf der Platte
                seen["login"], seen["uid"] = tl.login(), tl.user_id()                  # und Name und Kennung sind nie leer
            return orig(url, fields, headers, once=once)
        tl._call = spy
        self.assertEqual(tl.token(), "AT2")
        self.assertEqual(seen["on_disk"], "RT2")
        self.assertEqual(seen["login"], "streamer")
        self.assertTrue(seen["uid"])

    def test_a_failed_new_login_does_not_lock_out_the_logged_in_account(self):
        tl = self.make()
        tl.start()
        self.wait(tl, "angemeldet")
        FakeTwitch.state["device_status"] = 500
        try:
            with self.assertRaises(ValueError):
                tl.start(mod=True)
        finally:
            FakeTwitch.state.pop("device_status", None)
        self.assertEqual(tl.state, "angemeldet")
        self.assertTrue(tl.ready())
        self.assertEqual(tl.login(), "streamer")
        self.assertEqual(tl.token(), "AT1")

    def test_moderation_can_be_switched_off_and_on_without_a_new_twitch_login(self):
        tl = self.make()
        tl.start(mod=True)
        self.wait(tl, "angemeldet")
        st = tl.status()
        self.assertTrue(st["mod"] and st["mod_scope"])
        off = tl.set_mod(False)
        self.assertFalse(off["mod"])
        self.assertTrue(off["mod_scope"])                                          # die Rechte hat der Zugang weiterhin
        self.assertTrue(json.load(open(self.path))["mod_off"])                      # und der Schalter überlebt einen Neustart
        again = self.make()
        self.assertFalse(again.status()["mod"])
        self.now[0] += 14400 - 30
        tl.token()                                                                  # eine Erneuerung ändert den Schalter nicht
        self.assertFalse(tl.status()["mod"])
        self.assertTrue(tl.set_mod(True)["mod"])
        self.assertFalse(json.load(open(self.path))["mod_off"])

    def test_moderation_cannot_be_switched_on_without_the_rights(self):
        tl = self.make()
        tl.start()                                                                  # nur Lesen und Schreiben
        self.wait(tl, "angemeldet")
        self.assertFalse(tl.status()["mod_scope"])
        with self.assertRaises(ValueError):
            tl.set_mod(True)
        self.assertFalse(tl.status()["mod"])

    def test_keep_refreshes_before_expiry_and_leaves_fresh_tokens_alone(self):
        tl = self.make()
        tl.start()
        self.wait(tl, "angemeldet")
        calls = []
        orig = tl.refresh
        tl.refresh = lambda: calls.append(1) or orig()
        stop = threading.Event()
        tl.sleep = lambda s: (stop.wait(0.02), None)[1]
        t = threading.Thread(target=tl.keep, daemon=True)
        t.start()
        time.sleep(0.2)
        self.assertEqual(calls, [])                                                    # frisch: nichts zu tun
        self.now[0] += 14400 - 100                                                     # unter REFRESH_BEFORE
        time.sleep(0.3)
        self.assertGreaterEqual(len(calls), 1)

    def test_rejected_refresh_means_sign_in_again(self):
        tl = self.make()
        tl.start()
        self.wait(tl, "angemeldet")
        FakeTwitch.state["reject_refresh"] = True
        self.now[0] += 14400
        self.assertEqual(tl.token(), "")
        self.assertEqual(tl.state, "abgelaufen")
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(tl.status()["state"], "abgelaufen")

    def test_logout_revokes_and_forgets(self):
        tl = self.make()
        tl.start()
        self.wait(tl, "angemeldet")
        out = tl.logout()
        self.assertEqual(out["state"], "aus")
        self.assertEqual(FakeTwitch.state["revoked"], "AT1")
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(tl.token(), "")

    def test_denied_and_cancelled_attempts(self):
        FakeTwitch.state["deny"] = True
        tl = self.make()
        tl.start()
        self.wait(tl, "fehler")
        self.assertIn("abgelehnt", tl.status()["error"])
        FakeTwitch.state["deny"] = False
        FakeTwitch.state["polls"] = 0
        tl.start()
        self.assertEqual(tl.cancel()["state"], "aus")
        time.sleep(0.2)
        self.assertEqual(tl.state, "aus")

    def test_unreachable_twitch_gives_a_short_message(self):
        tl = server.TwitchLogin(self.path, client_id="testclient", id_base="http://127.0.0.1:1/oauth2/", clock=lambda: 0.0)
        with self.assertRaises(ValueError) as cm:
            tl.start()
        self.assertEqual(str(cm.exception), server.TwitchLogin.ERR_NET)

    def test_demo_login_needs_no_network(self):
        now = [0.0]
        tl = server.TwitchLogin(self.path, demo=True, clock=lambda: now[0], id_base="http://127.0.0.1:1/oauth2/")
        self.assertEqual(tl.start()["state"], "wartet")
        now[0] += 7
        st = tl.status()
        self.assertEqual((st["state"], st["login"]), ("angemeldet", "demo_streamer"))
        self.assertEqual(tl.logout()["state"], "aus")


class StoreIntegration(Base):
    def store(self):
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        tl = self.make()
        st.account = tl
        return st, tl

    def test_account_replaces_manual_bot_account_and_token(self):
        st, tl = self.store()
        with self.assertRaises(ValueError):
            st.set({"enabled": True})                                                   # ohne Konto und ohne Token geht es nicht
        tl.start()
        self.wait(tl, "angemeldet")
        st.set({"enabled": True})                                                       # mit Konto reicht "einschalten"
        cfg = st.settings()
        self.assertEqual((cfg["login"], cfg["token"], cfg["channel"]), ("streamer", "AT1", "streamer"))
        self.assertEqual(st.public()["account"], "streamer")
        self.assertNotIn("AT1", json.dumps(st.public()))

    def test_manual_token_still_works_without_account(self):
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        st.set({"channel": "kanal", "login": "botkonto", "token": "a" * 30, "enabled": True})
        cfg = st.settings()
        self.assertEqual((cfg["login"], cfg["channel"]), ("botkonto", "kanal"))


class FakeHelix(http.server.BaseHTTPRequestHandler):
    log = []
    status = 204
    sub_status = 202
    sub_script = []
    chat_reply = {"data": [{"message_id": "m1", "is_sent": True}]}

    def log_message(self, *a):
        pass

    def _go(self, method):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode() if n else ""
        FakeHelix.log.append((method, self.path, self.headers.get("Authorization"), self.headers.get("Client-Id"), body))
        if method == "GET" and self.path.startswith("/helix/users?login=bob"):
            out = json.dumps({"data": [{"id": "777", "login": "bob"}]}).encode()
            code = 200
        elif method == "GET" and self.path.startswith("/helix/users"):
            out, code = json.dumps({"data": []}).encode(), 200
        elif method == "POST" and self.path == "/helix/eventsub/subscriptions":
            code = FakeHelix.sub_script.pop(0) if FakeHelix.sub_script else FakeHelix.sub_status
            out = json.dumps({"data": [{"id": "s1"}]} if code < 300 else {"message": "kaputt"}).encode()
        elif method == "POST" and self.path == "/helix/chat/messages":
            out, code = json.dumps(FakeHelix.chat_reply).encode(), 200
        else:
            out, code = (json.dumps({"message": "kaputt"}).encode() if FakeHelix.status == 400 else b""), FakeHelix.status
        self.send_response(code)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")

    def do_DELETE(self):
        self._go("DELETE")


class Moderation(Base):
    def setUp(self):
        super().setUp()
        FakeHelix.log, FakeHelix.status = [], 204
        self.hx = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHelix)
        threading.Thread(target=self.hx.serve_forever, daemon=True).start()

    def tearDown(self):
        self.hx.shutdown()
        self.hx.server_close()
        super().tearDown()

    def mod(self, scopes=True, channel=""):
        FakeTwitch.state["mod_scopes"] = scopes
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        if channel:
            st.set({"channel": channel})
        tl = self.make()
        st.account = tl
        tl.start(mod=scopes)
        self.wait(tl, "angemeldet")
        return server.TwitchMod(st, tl, api_base="http://127.0.0.1:%d/helix/" % self.hx.server_address[1], client_id="testclient"), tl

    def test_needs_sign_in_and_the_moderation_rights(self):
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        tl = self.make()
        st.account = tl
        m = server.TwitchMod(st, tl, api_base="http://127.0.0.1:1/helix/")
        with self.assertRaises(ValueError) as c:
            m.do({"action": "ban", "user_id": "5"})
        self.assertEqual(str(c.exception), server.TwitchMod.ERR_LOGIN)
        m, tl = self.mod(scopes=False)
        with self.assertRaises(ValueError) as c:
            m.do({"action": "ban", "user_id": "5"})
        self.assertEqual(str(c.exception), server.TwitchMod.ERR_SCOPE)
        self.assertEqual(FakeHelix.log, [])

    def test_delete_timeout_ban_unban_call_the_right_endpoints(self):
        m, tl = self.mod()
        self.assertEqual(tl.status()["mod"], True)
        m.do({"action": "delete", "message_id": "abcdef12-3456-7890-abcd-ef1234567890"})
        out = m.do({"action": "timeout", "user_id": "777", "seconds": 99999999, "reason": "Spam"})
        self.assertEqual(out["message"], "Timeout")
        m.do({"action": "ban", "user_id": "777"})
        m.do({"action": "unban", "user_id": "777"})
        (d, t, b, u) = FakeHelix.log
        self.assertEqual(d[0:2], ("DELETE", "/helix/moderation/chat?broadcaster_id=42&moderator_id=42&message_id=abcdef12-3456-7890-abcd-ef1234567890"))
        self.assertEqual(t[0:2], ("POST", "/helix/moderation/bans?broadcaster_id=42&moderator_id=42"))
        self.assertEqual(json.loads(t[4])["data"], {"user_id": "777", "reason": "Spam", "duration": server.TwitchMod.MAX_SECONDS})
        self.assertEqual(t[2:4], ("Bearer AT1", "testclient"))
        self.assertEqual("duration" in json.loads(b[4])["data"], False)                 # Bann ohne Dauer = dauerhaft
        self.assertEqual(u[0:2], ("DELETE", "/helix/moderation/bans?broadcaster_id=42&moderator_id=42&user_id=777"))

    def test_names_are_resolved_once_and_other_channels_are_looked_up(self):
        m, tl = self.mod(channel="bob")
        m.do({"action": "ban", "user": "@Bob"})
        m.do({"action": "unban", "user": "bob"})
        gets = [x for x in FakeHelix.log if x[0] == "GET"]
        self.assertEqual(len(gets), 1)                                                  # Name nur einmal aufgelöst (Kanal "bob" = 777)
        self.assertIn("broadcaster_id=777&moderator_id=42", FakeHelix.log[1][1])
        with self.assertRaises(ValueError) as c:
            m.do({"action": "ban", "user": "niemand"})
        self.assertEqual(str(c.exception), "Dieses Konto gibt es nicht")

    def test_errors_become_short_messages_and_bad_requests_never_reach_twitch(self):
        m, tl = self.mod()
        for status, text in ((401, server.TwitchMod.ERR_AUTH), (403, server.TwitchMod.ERR_FORBIDDEN), (500, server.TwitchMod.ERR_OTHER), (400, "kaputt")):
            FakeHelix.status = status
            with self.assertRaises(ValueError) as c:
                m.do({"action": "ban", "user_id": "777"})
            self.assertEqual(str(c.exception), text)
        n = len(FakeHelix.log)
        for bad in ({"action": "mod"}, {"action": "delete", "message_id": "../x"}, {"action": "ban"}, {"action": "ban", "user_id": "1; DROP"}, None, "x"):
            with self.assertRaises(ValueError):
                m.do(bad)
        self.assertEqual(len(FakeHelix.log), n)

    def test_slash_commands_go_through_the_moderation(self):
        m, tl = self.mod()
        chat = FakeChat()
        snd = server.TwitchSender(m.store, chat=chat, clock=lambda: self.now[0], mod=m)
        self.assertEqual(snd.say("/timeout bob 120 zu laut")["message"], "Timeout: bob")
        self.now[0] += 2
        snd.say("/ban @bob")
        self.now[0] += 2
        snd.say("/unban bob")
        posts = [json.loads(x[4])["data"] for x in FakeHelix.log if x[0] == "POST"]
        self.assertEqual(posts[0], {"user_id": "777", "reason": "zu laut", "duration": 120})
        self.assertEqual(chat.sent, [])                                                  # Befehle landen nie als Text im Chat
        self.now[0] += 2
        for bad in ("/me hallo", "/timeout", ".ban bob", "/clear"):
            with self.assertRaises(ValueError):
                snd.say(bad)
            self.now[0] += 2
        self.assertEqual(chat.sent, [])


class FakeChat:
    def __init__(self):
        self.sent = []

    def send(self, login, token, channel, text):
        self.sent.append((login, token, channel, text))
        return True, "Gesendet"


class Sending(Base):
    def make_sender(self):
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        tl = self.make()
        st.account = tl
        chat = FakeChat()
        snd = server.TwitchSender(st, chat=chat, clock=lambda: self.now[0])
        return snd, chat, tl

    def test_needs_sign_in_and_refuses_commands(self):
        snd, chat, tl = self.make_sender()
        with self.assertRaises(ValueError):
            snd.say("hallo")
        tl.start()
        self.wait(tl, "angemeldet")
        for bad in ("", "   ", "/ban jemand", ".timeout x", None, 5):
            with self.assertRaises(ValueError):
                snd.say(bad)
        self.assertEqual(chat.sent, [])

    def test_sends_as_the_account_and_limits_the_rate(self):
        snd, chat, tl = self.make_sender()
        tl.start()
        self.wait(tl, "angemeldet")
        self.assertEqual(snd.say("  Hallo Chat  ")["message"], "Gesendet")
        self.assertEqual(chat.sent, [("streamer", "AT1", "streamer", "Hallo Chat")])
        with self.assertRaises(ValueError):
            snd.say("zu schnell")
        self.now[0] += 2
        snd.say("jetzt wieder")
        self.assertEqual(len(chat.sent), 2)


class BotAccount(Base):
    """Zweites Konto (Bot), das nur die Akku-Meldung schreibt."""
    def rig(self):
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        main = self.make()
        bot = server.TwitchBotLogin(os.path.join(self.dir, "twitch-bot-login.json"), client_id="testclient", id_base=self.base, clock=lambda: self.now[0],
                                    sleep=lambda s: time.sleep(0.01))
        st.account, st.bot = main, bot
        return st, main, bot

    def sign_in(self, tl):
        tl.start()
        self.wait(tl, "angemeldet")

    def test_bot_asks_for_the_smallest_rights_and_has_its_own_file(self):
        st, main, bot = self.rig()
        FakeTwitch.state["polls"] = 0
        bot.start()
        self.wait(bot, "angemeldet")
        sent = [f for path, f in FakeTwitch.state["seen"] if path == "/oauth2/device"][0]
        self.assertEqual(sent["scopes"], "chat:read chat:edit")                         # kein Senden über Helix, keine Moderation, keine Ereignisse
        self.assertTrue(os.path.exists(os.path.join(self.dir, "twitch-bot-login.json")))
        self.assertFalse(os.path.exists(self.path))                                     # die Datei des Hauptkontos bleibt unberührt
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "twitch-bot-login.json")).st_mode), 0o600)
        self.assertNotIn("AT1", json.dumps(bot.status()))
        self.assertEqual(bot.scopes_for(mod=True, events=True), "chat:read chat:edit")

    def test_only_the_battery_message_uses_the_bot_the_chat_stays_with_the_main_account(self):
        st, main, bot = self.rig()
        self.sign_in(main)
        FakeTwitch.state["polls"] = 0
        FakeTwitch.state["login"] = "botkonto"
        FakeTwitch.state["n"] = 1
        bot.start()
        self.wait(bot, "angemeldet")
        self.assertEqual(bot.login(), "botkonto")
        chat = st.settings()
        self.assertEqual((chat["login"], chat["channel"]), ("streamer", "streamer"))        # Chat: Hauptkonto
        msg = st.notify_settings()
        self.assertEqual((msg["login"], msg["channel"]), ("botkonto", "streamer"))          # Meldung: Bot, aber im Kanal des Hauptkontos
        self.assertTrue(msg["token"])
        pub = st.public()
        self.assertEqual((pub["account"], pub["bot"]), ("streamer", "botkonto"))
        self.assertNotIn("AT", json.dumps(pub))

    def test_without_main_account_the_bot_needs_a_channel(self):
        st, main, bot = self.rig()
        self.sign_in(bot)
        with self.assertRaises(ValueError):
            st.set({"enabled": True})                                                   # Bot allein: der Kanal fehlt
        st.set({"enabled": True, "channel": "Kanal_X"})
        msg = st.notify_settings()
        self.assertEqual((msg["login"], msg["channel"]), ("streamer", "kanal_x"))

    def test_test_message_is_written_by_the_bot(self):
        st, main, bot = self.rig()
        self.sign_in(bot)
        st.set({"enabled": True, "channel": "kanal"})
        chat = FakeChat()
        n = server.TwitchNotifier(st, None, None, None, chat=chat, mono=lambda: self.now[0], wall=lambda: self.now[0], sleep=lambda s: None)
        self.assertTrue(n.test()["ok"])
        self.assertEqual(chat.sent, [("streamer", "AT1", "kanal", "Test: IRL4YOU BOX")])

    def test_expired_bot_login_stays_silent_instead_of_writing_as_the_main_account(self):
        st, main, bot = self.rig()
        self.sign_in(main)
        FakeTwitch.state["polls"] = 0
        bot.start()
        self.wait(bot, "angemeldet")
        FakeTwitch.state["reject_refresh"] = True
        bot.tokens["expires_at"] = self.now[0] - 5                                      # nur der Zugang des Bots ist abgelaufen
        self.assertEqual(bot.token(), "")                                               # Erneuerung abgelehnt: Anmeldung abgelaufen
        self.assertEqual(bot.state, "abgelaufen")
        msg = st.notify_settings()
        self.assertEqual(msg["token"], "")                                              # nie still mit dem Hauptkonto schreiben
        self.assertEqual(st.settings()["login"], "streamer")                            # der Chat läuft weiter
        bot.logout()                                                                    # abgemeldet: wieder das Hauptkonto
        self.assertEqual(st.notify_settings()["login"], "streamer")
        self.assertTrue(st.notify_settings()["token"])


if __name__ == "__main__":
    unittest.main()
