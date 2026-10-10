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
            return self._send(200, {"client_id": "x", "login": FakeTwitch.state.get("login", "Streamer"), "scopes": FakeTwitch.state.get("scopes", ["chat:read", "chat:edit"]), "user_id": FakeTwitch.state.get("user_id", "42"), "expires_in": 14000})
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
    mods = {"99"}                                                                   # Konten, die im Kanal Moderator sind

    def log_message(self, *a):
        pass

    def _go(self, method):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode() if n else ""
        FakeHelix.log.append((method, self.path, self.headers.get("Authorization"), self.headers.get("Client-Id"), body))
        if method == "GET" and self.path.startswith("/helix/users?login=bob"):
            out = json.dumps({"data": [{"id": "777", "login": "bob"}]}).encode()
            code = 200
        elif method == "GET" and self.path.startswith("/helix/moderation/moderators"):
            uid = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("user_id", [""])[0]
            out, code = json.dumps({"data": [{"user_id": uid}] if uid in FakeHelix.mods else []}).encode(), (FakeHelix.status if FakeHelix.status >= 400 else 200)
        elif method == "GET" and self.path.startswith("/helix/games?name=Just"):
            out, code = json.dumps({"data": [{"id": "509658", "name": "Just Chatting"}]}).encode(), 200
        elif method == "GET" and self.path.startswith("/helix/games"):
            out, code = json.dumps({"data": []}).encode(), 200
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

    def do_PATCH(self):
        self._go("PATCH")


KEY = "A" * 20 + "b" * 20                                                          # Streamer-Schlüssel des Browsers, in dem angemeldet wurde
KEY2 = "Z" * 40                                                                  # ein anderer Browser


class HelixBase(Base):
    """Fake-Twitch (Anmeldung) und Fake-Helix (Schnittstelle) auf 127.0.0.1."""
    def setUp(self):
        super().setUp()
        FakeHelix.log, FakeHelix.status, FakeHelix.mods = [], 204, {"99"}
        self.hx = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHelix)
        threading.Thread(target=self.hx.serve_forever, daemon=True).start()

    def tearDown(self):
        self.hx.shutdown()
        self.hx.server_close()
        super().tearDown()

    def mod(self, scopes=True, channel="", everything=False):
        FakeTwitch.state["mod_scopes"] = scopes
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        if channel:
            st.set({"channel": channel})
        tl = self.make()
        st.account = tl
        tl.start(mod=scopes, everything=everything, owner_key=KEY)
        self.wait(tl, "angemeldet")
        return server.TwitchMod(st, tl, api_base="http://127.0.0.1:%d/helix/" % self.hx.server_address[1], client_id="testclient"), tl


class Moderation(HelixBase):
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
        for bad in ("/me hallo", "/timeout", ".ban bob", "/vip", "/unknown x"):
            with self.assertRaises(ValueError):
                snd.say(bad)
            self.now[0] += 2
        self.assertEqual(chat.sent, [])


    def test_moderation_includes_all_command_rights_and_everything_at_once_adds_events(self):
        m, tl = self.mod()                                                              # "Moderation einschalten": Löschen, Bann UND alle Befehle in einer Bestätigung
        wanted = FakeTwitch.state["seen"][0][1]["scopes"].split()
        for sc in server.TwitchLogin.CMD_SCOPES.split() + "moderator:manage:banned_users moderator:manage:chat_messages".split():
            self.assertIn(sc, wanted)
        self.assertEqual((tl.status()["mod"], tl.status()["events"]), (True, False))
        m2, tl2 = self.mod(everything=True)                                            # erste Anmeldung: alles auf einmal
        self.assertEqual((tl2.status()["mod"], tl2.status()["events"]), (True, True))
        for sc in server.TwitchLogin.EVENT_SCOPES.split():
            self.assertIn(sc, FakeTwitch.state["seen"][-1][1]["scopes"].split())

    def test_an_older_login_without_the_command_rights_needs_moderation_switched_on_again(self):
        m, tl = self.mod()
        tl.tokens["scopes"] = ["chat:read", "chat:edit", "user:write:chat", "moderator:manage:banned_users", "moderator:manage:chat_messages"]   # Stand vor den Befehlen
        st = tl.status()
        self.assertEqual((st["mod"], st["mod_scope"]), (False, False))                 # der Knopf "Moderation einschalten" holt alles in einer Anmeldung
        with self.assertRaises(ValueError):
            tl.set_mod(True)
        with self.assertRaises(ValueError) as c:
            m.do({"action": "vip", "user_id": "777"})
        self.assertEqual(str(c.exception), server.TwitchMod.ERR_SCOPE)

    def test_commands_only_for_the_owner_where_twitch_says_so(self):
        m, tl = self.mod(channel="bob")                                                 # fremder Kanal: nur Moderatorenbefehle
        FakeHelix.log.clear()
        for act in ("vip", "unvip", "mod", "unmod", "raid", "unraid", "marker", "title", "game"):
            with self.assertRaises(ValueError) as c:
                m.do({"action": act, "user_id": "5", "text": "x"}, KEY)
            self.assertEqual(str(c.exception), server.TwitchMod.ERR_OWNER, act)
        self.assertEqual([x[0] for x in FakeHelix.log], ["GET"])                         # nur der Name des Kanals wird aufgelöst, sonst geht nichts zu Twitch
        self.assertEqual(m.do({"action": "clear"}, KEY)["message"], "Chat geleert")

    def test_owner_commands_only_work_in_the_browser_where_the_streamer_signed_in(self):
        m, tl = self.mod()
        FakeHelix.log.clear()
        owner_acts = ({"action": "vip", "user_id": "777"}, {"action": "unvip", "user_id": "777"}, {"action": "mod", "user_id": "777"}, {"action": "unmod", "user_id": "777"},
                      {"action": "raid", "user_id": "777"}, {"action": "unraid"}, {"action": "marker"}, {"action": "title", "text": "x"}, {"action": "game", "text": "Just Chatting"})
        for key in (None, "", "kurz", KEY2, KEY[:-1], 5):                                # kein, ungültiger oder fremder Schlüssel
            for req in owner_acts:
                with self.assertRaises(ValueError) as c:
                    m.do(req, key)
                self.assertEqual(str(c.exception), server.TwitchMod.ERR_BROWSER, (key, req))
        self.assertEqual(FakeHelix.log, [])                                              # nichts ging zu Twitch
        for req in ({"action": "clear"}, {"action": "slow"}, {"action": "announce", "text": "Hallo"}, {"action": "ban", "user_id": "777"}):
            self.assertTrue(m.do(req, KEY2)["ok"], req)                                   # was auch Moderatoren dürfen, geht ohne Streamer-Schlüssel
        self.assertEqual(m.do({"action": "vip", "user_id": "777"}, KEY)["message"], "VIP vergeben")
        chat = FakeChat()
        snd = server.TwitchSender(m.store, chat=chat, clock=lambda: self.now[0], mod=m)
        self.now[0] += 2
        with self.assertRaises(ValueError) as c:
            snd.say("/mod bob", KEY2)
        self.assertEqual(str(c.exception), server.TwitchMod.ERR_BROWSER)
        self.assertEqual(chat.sent, [])

    def test_only_a_hash_is_stored_and_more_browsers_can_be_added_by_signing_in_there(self):
        m, tl = self.mod()
        raw = open(self.path).read()
        self.assertNotIn(KEY, raw)                                                       # der Schlüssel selbst steht nie auf der Box
        import hashlib
        self.assertIn(hashlib.sha256(KEY.encode()).hexdigest(), raw)
        self.assertEqual((tl.is_owner(KEY), tl.is_owner(KEY2)), (True, False))
        tl.start(mod=True, owner_key=KEY2)                                               # zweiter Browser (zum Beispiel das Handy des Streamers): meldet sich auch an
        self.wait(tl, "angemeldet")
        self.assertEqual((tl.is_owner(KEY), tl.is_owner(KEY2)), (True, True))
        tl2 = self.make()                                                                # nach einem Neustart der Box
        self.assertEqual((tl2.is_owner(KEY), tl2.is_owner(KEY2)), (True, True))
        tl2.refresh()
        self.assertEqual((tl2.is_owner(KEY), tl2.is_owner(KEY2)), (True, True))          # Erneuern behält sie
        tl2.logout()
        self.assertEqual((tl2.is_owner(KEY), tl2.is_owner(KEY2)), (False, False))        # Abmelden löscht sie

    def test_a_pending_sign_in_of_another_browser_cannot_hand_out_its_key_to_the_streamer(self):
        m, tl = self.mod()
        tl.start(mod=True, owner_key=KEY2)                                               # jemand mit dem Link beginnt eine Anmeldung ...
        tl.start(mod=True, owner_key=KEY)                                                # ... der Streamer beginnt in seinem Browser eine eigene: sie ersetzt die fremde
        self.assertEqual(tl.pending["owner"], tl.owner_hash(KEY))
        self.wait(tl, "angemeldet")
        self.assertEqual((tl.is_owner(KEY), tl.is_owner(KEY2)), (True, False))

    def test_new_commands_call_the_right_endpoints(self):
        m, tl = self.mod()
        FakeHelix.log.clear()
        calls = [({"action": "vip", "user_id": "777"}, "VIP vergeben"), ({"action": "unvip", "user_id": "777"}, "VIP entfernt"),
                 ({"action": "mod", "user_id": "777"}, "Moderator ernannt"), ({"action": "unmod", "user_id": "777"}, "Moderator entfernt"),
                 ({"action": "clear"}, "Chat geleert"), ({"action": "slow"}, "Langsamer Modus an (30 Sekunden)"), ({"action": "slow", "number": 9999}, "Langsamer Modus an (120 Sekunden)"),
                 ({"action": "slowoff"}, "Langsamer Modus aus"), ({"action": "followers", "number": 10}, "Nur Follower an (seit 10 Minuten)"),
                 ({"action": "followersoff"}, "Nur Follower aus"), ({"action": "subscribers"}, "Nur Abonnenten an"), ({"action": "emoteonlyoff"}, "Nur Emotes aus"),
                 ({"action": "announce", "text": "Gleich geht es los"}, "Ankündigung gesendet"), ({"action": "raid", "user_id": "777"}, "Raid gestartet"),
                 ({"action": "unraid"}, "Raid abgebrochen"), ({"action": "marker", "text": "Tor"}, "Marker gesetzt"), ({"action": "title", "text": "Neuer Titel"}, "Titel geändert"),
                 ({"action": "game", "text": "Just Chatting"}, "Kategorie geändert")]
        for req, msg in calls:
            self.assertEqual(m.do(req, KEY)["message"], msg, req)
        log = [(x[0], x[1].replace("/helix/", ""), json.loads(x[4]) if x[4] else None) for x in FakeHelix.log]
        self.assertEqual(log[0][0:2], ("POST", "channels/vips?broadcaster_id=42&user_id=777"))
        self.assertEqual(log[1][0:2], ("DELETE", "channels/vips?broadcaster_id=42&user_id=777"))
        self.assertEqual(log[2][0:2], ("POST", "moderation/moderators?broadcaster_id=42&user_id=777"))
        self.assertEqual(log[3][0:2], ("DELETE", "moderation/moderators?broadcaster_id=42&user_id=777"))
        self.assertEqual(log[4][0:2], ("DELETE", "moderation/chat?broadcaster_id=42&moderator_id=42"))
        self.assertEqual(log[5], ("PATCH", "chat/settings?broadcaster_id=42&moderator_id=42", {"slow_mode": True, "slow_mode_wait_time": 30}))
        self.assertEqual(log[6][2], {"slow_mode": True, "slow_mode_wait_time": 120})
        self.assertEqual(log[7][2], {"slow_mode": False})
        self.assertEqual(log[8][2], {"follower_mode": True, "follower_mode_duration": 10})
        self.assertEqual(log[9][2], {"follower_mode": False})
        self.assertEqual(log[10][2], {"subscriber_mode": True})
        self.assertEqual(log[11][2], {"emote_mode": False})
        self.assertEqual(log[12], ("POST", "chat/announcements?broadcaster_id=42&moderator_id=42", {"message": "Gleich geht es los", "color": "primary"}))
        self.assertEqual(log[13][0:2], ("POST", "raids?from_broadcaster_id=42&to_broadcaster_id=777"))
        self.assertEqual(log[14][0:2], ("DELETE", "raids?broadcaster_id=42"))
        self.assertEqual(log[15], ("POST", "streams/markers", {"user_id": "42", "description": "Tor"}))
        self.assertEqual(log[16], ("PATCH", "channels?broadcaster_id=42", {"title": "Neuer Titel"}))
        self.assertEqual(log[17][0], "GET")
        self.assertEqual(log[18], ("PATCH", "channels?broadcaster_id=42", {"game_id": "509658"}))
        with self.assertRaises(ValueError) as c:
            m.do({"action": "game", "text": "Gibtsnicht"}, KEY)
        self.assertEqual(str(c.exception), "Diese Kategorie gibt es nicht")
        for bad in ({"action": "announce"}, {"action": "title", "text": "  "}, {"action": "slow", "number": "abc"}):
            with self.assertRaises(ValueError):
                m.do(bad, KEY)

    def test_twitch_errors_for_the_new_commands_become_short_messages(self):
        m, tl = self.mod()
        for status, text in ((404, server.TwitchMod.ERR_NOTFOUND), (403, server.TwitchMod.ERR_FORBIDDEN)):
            FakeHelix.status = status
            with self.assertRaises(ValueError) as c:
                m.do({"action": "vip", "user_id": "777"} if status != 404 else {"action": "marker"}, KEY)
            self.assertEqual(str(c.exception), text)

    def test_slash_commands_for_the_new_functions(self):
        m, tl = self.mod()
        chat = FakeChat()
        snd = server.TwitchSender(m.store, chat=chat, clock=lambda: self.now[0], mod=m)
        def go(t):
            self.now[0] += 2
            return snd.say(t, KEY)["message"]
        self.assertEqual(go("/vip @bob"), "VIP vergeben: bob")
        self.assertEqual(go("/unmod bob"), "Moderator entfernt: bob")
        self.assertEqual(go("/clear"), "Chat geleert")
        self.assertEqual(go("/slow 45"), "Langsamer Modus an (45 Sekunden)")
        self.assertEqual(go("/slow"), "Langsamer Modus an (30 Sekunden)")
        self.assertEqual(go("/followersoff"), "Nur Follower aus")
        self.assertEqual(go("/announce Hallo zusammen, schön dass ihr da seid"), "Ankündigung gesendet")
        self.assertEqual(go("/marker"), "Marker gesetzt")
        self.assertEqual(go("/title Neuer Titel mit Leerzeichen"), "Titel geändert")
        self.assertEqual(go("/game Just Chatting"), "Kategorie geändert")
        ann = [json.loads(x[4]) for x in FakeHelix.log if "announcements" in x[1]][0]
        self.assertEqual(ann["message"], "Hallo zusammen, schön dass ihr da seid")
        for bad in ("/vip", "/slow abc", "/announce", "/title", "/raid"):
            with self.assertRaises(ValueError):
                go(bad)
        self.assertEqual(chat.sent, [])                                                  # nie als Text im Chat


KEY3 = "Y" * 40
KEY4 = "X" * 40


class Moderators(HelixBase):
    """Moderatoren melden sich in ihrem Browser mit dem eigenen Twitch-Konto an: eigene Rechte, nie das Konto des Streamers."""

    def setUp(self):
        super().setUp()
        self.sessions = server.ModSessions(self.dir, id_base=self.base, client_id="testclient", clock=lambda: self.now[0], sleep=lambda s: time.sleep(0.01))

    def box(self, channel="bob"):
        FakeTwitch.state["login"], FakeTwitch.state["user_id"] = "Streamer", "42"
        m, tl = self.mod(channel=channel, everything=True)
        self.accounts = server.TwitchAccounts(tl, self.sessions)
        return m, tl

    def moderator(self, key, login="Modfrau", uid="99", mod=True):
        FakeTwitch.state["login"], FakeTwitch.state["user_id"] = login, uid
        self.sessions.start(key, mod=mod)
        s = self.sessions.get(key)
        self.wait(s, "angemeldet")
        return s

    def until(self, fn, secs=5):
        end = time.time() + secs
        while time.time() < end and not fn():
            time.sleep(0.02)
        self.assertTrue(fn())

    def test_a_moderator_asks_only_for_moderator_rights(self):
        m, tl = self.box()
        self.moderator(KEY2)
        dev = [f for p, f in FakeTwitch.state["seen"] if p == "/oauth2/device"][-1]
        got = dev["scopes"].split()
        for sc in ("chat:read", "chat:edit", "user:write:chat", "moderator:manage:banned_users", "moderator:manage:chat_messages",
                   "moderator:manage:chat_settings", "moderator:manage:announcements"):
            self.assertIn(sc, got)
        for sc in server.TwitchLogin.CMD_SCOPES.split() + server.TwitchLogin.EVENT_SCOPES.split():
            if sc.startswith("channel:") or sc.startswith("moderator:read"):
                self.assertNotIn(sc, got)                                               # nichts vom Kanalinhaber, keine Ereignisse

    def test_each_browser_gets_its_own_account(self):
        m, tl = self.box()
        s = self.moderator(KEY2)
        self.assertEqual(self.accounts.pick(KEY), ("owner", tl))                        # der Browser des Streamers
        self.assertEqual(self.accounts.pick(KEY2), ("mod", s))                          # ein Moderator mit eigener Anmeldung
        self.assertEqual(self.accounts.pick(KEY3), ("none", None))                      # nur den Link: nur lesen
        self.assertEqual(self.accounts.pick(None), ("none", None))
        a, b, c = self.accounts.status(KEY), self.accounts.status(KEY2), self.accounts.status(KEY3)
        self.assertEqual((a["role"], a["login"], a["mod"], a["box_login"]), ("owner", "streamer", True, "streamer"))
        self.assertEqual((b["role"], b["login"], b["mod"], b["box_login"]), ("mod", "modfrau", True, "streamer"))
        self.assertEqual((c["role"], c["state"], c["login"], c["mod"], c["scopes"], c["box_login"]), ("none", "aus", "", False, [], "streamer"))
        self.assertNotIn("AT1", json.dumps([a, b, c]))
        tl2 = self.make()
        self.assertEqual(server.TwitchAccounts(tl2, self.sessions).pick(KEY3)[0], "none")   # ohne Streamer-Schlüssel auch nach einem Neustart nicht

    def test_before_anybody_signed_in_the_first_browser_becomes_the_streamer(self):
        tl = self.make()
        acc = server.TwitchAccounts(tl, self.sessions)
        self.assertEqual(acc.pick(KEY3)[0], "owner")

    def test_a_moderator_moderates_and_writes_with_the_own_account_only(self):
        m, tl = self.box()
        s = self.moderator(KEY2)
        view = m.view(s)
        FakeHelix.log.clear()
        view.do({"action": "ban", "user_id": "5"}, KEY2)
        view.do({"action": "slow"}, KEY2)
        view.do({"action": "announce", "text": "Hallo"}, KEY2)
        calls = [x for x in FakeHelix.log if x[0] != "GET"]                              # (der Name des Kanals wird einmal nachgeschlagen)
        self.assertEqual(calls[0][1], "/helix/moderation/bans?broadcaster_id=777&moderator_id=99")   # Kanal des Streamers, Moderator ist er selbst
        self.assertIn("broadcaster_id=777&moderator_id=99", calls[1][1])
        for act in ("vip", "mod", "raid", "marker", "title", "game"):
            with self.assertRaises(ValueError) as c:
                view.do({"action": act, "user_id": "5", "text": "x"}, KEY2)
            self.assertEqual(str(c.exception), server.TwitchMod.ERR_OWNER, act)       # nichts vom Kanalinhaber
        chat = FakeChat()
        snd = server.TwitchSender(m.store, chat=chat, clock=lambda: self.now[0], mod=m)
        self.now[0] += 2
        FakeHelix.log.clear()
        self.assertEqual(snd.say_as(view, server.HelixChat(s, view), "Hallo Chat", KEY2)["ok"], True)
        post = [x for x in FakeHelix.log if x[1] == "/helix/chat/messages"][0]
        self.assertEqual(json.loads(post[4]), {"broadcaster_id": "777", "sender_id": "99", "message": "Hallo Chat"})   # als er selbst, nicht als Streamer
        self.assertEqual(chat.sent, [])
        self.now[0] += 2
        self.assertEqual(snd.say_as(view, server.HelixChat(s, view), "/clear", KEY2)["message"], "Chat geleert")
        self.now[0] += 2
        with self.assertRaises(ValueError) as c:
            snd.say_as(view, server.HelixChat(s, view), "/vip bob", KEY2)
        self.assertEqual(str(c.exception), server.TwitchMod.ERR_OWNER)

    def test_signing_in_as_streamer_in_a_new_browser_needs_the_same_twitch_account(self):
        m, tl = self.box()
        FakeTwitch.state["login"], FakeTwitch.state["user_id"] = "Streamer", "42"
        tl.start(mod=True, owner_key=KEY2, claim=True)                                   # das Handy des Streamers
        self.until(lambda: tl.is_owner(KEY2))
        self.assertTrue(tl.is_owner(KEY))                                                # der erste Browser bleibt
        FakeTwitch.state["login"], FakeTwitch.state["user_id"] = "Modfrau", "99"        # jemand mit dem Link bestätigt bei Twitch mit dem eigenen Konto
        tl.start(mod=True, owner_key=KEY3, claim=True)
        self.until(lambda: tl.pending is None)
        self.assertEqual((tl.login(), tl.is_owner(KEY3), tl.is_owner(KEY)), ("streamer", False, True))      # das Konto der Box bleibt
        self.assertEqual(json.load(open(self.path))["login"], "streamer")                # auch auf der Platte
        self.assertTrue(FakeTwitch.state.get("revoked"))                                 # der fremde Zugang wurde gleich widerrufen
        st = self.accounts.status(KEY3)
        self.assertEqual((st["state"], st["error"]), ("fehler", server.TwitchLogin.ERR_NOT_OWNER))
        self.assertNotEqual(self.accounts.status(KEY4)["state"], "fehler")             # nur der betroffene Browser sieht den Fehler

    def test_logout_of_a_moderator_keeps_the_streamers_account_and_the_files_are_private(self):
        m, tl = self.box()
        s = self.moderator(KEY2)
        files = [n for n in os.listdir(self.dir) if n.startswith("twitch-mod-")]
        self.assertEqual(len(files), 1)
        self.assertNotIn(KEY2, files[0])                                                 # nur der Fingerabdruck steht im Dateinamen
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, files[0])).st_mode), 0o600)
        again = server.ModSessions(self.dir, id_base=self.base, client_id="testclient")  # nach einem Neustart der Box
        self.assertEqual(again.get(KEY2).login(), "modfrau")
        self.assertIsNone(again.get(KEY3))
        self.sessions.logout(KEY2)
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith("twitch-mod-")], [])
        self.assertEqual(self.accounts.pick(KEY2), ("none", None))
        self.assertTrue(tl.ready())

    def test_not_too_many_moderators(self):
        m, tl = self.box()
        self.sessions.MAX = 2
        self.moderator(KEY2)
        self.moderator(KEY3)
        with self.assertRaises(ValueError):
            self.sessions.start(KEY4)
        self.sessions.logout(KEY3)
        self.sessions.start(KEY4)                                                        # frei geworden
        with self.assertRaises(ValueError):
            self.sessions.start("zu kurz")

    def test_a_viewer_signs_in_for_chat_only_and_can_write_but_not_moderate(self):
        m, tl = self.box()
        s = self.moderator(KEY3, login="Zuschauer", uid="55", mod=False)
        dev = [f for p, f in FakeTwitch.state["seen"] if p == "/oauth2/device"][-1]
        self.assertEqual(sorted(dev["scopes"].split()), ["chat:edit", "chat:read", "user:write:chat"])      # nichts zum Moderieren
        self.assertEqual(self.accounts.pick(KEY3), ("user", s))
        st = self.accounts.status(KEY3)
        self.assertEqual((st["role"], st["login"], st["mod"], st["box_login"]), ("user", "zuschauer", False, "streamer"))
        view = m.view(s)
        chat = FakeChat()
        snd = server.TwitchSender(m.store, chat=chat, clock=lambda: self.now[0], mod=m)
        self.now[0] += 2
        FakeHelix.log.clear()
        snd.say_as(view, server.HelixChat(s, view), "Hallo aus dem Chat", KEY3, commands=False)
        post = [x for x in FakeHelix.log if x[1] == "/helix/chat/messages"][0]
        self.assertEqual(json.loads(post[4])["sender_id"], "55")                         # als er selbst
        self.now[0] += 2
        for text in ("/ban bob", "/clear", "/vip bob"):
            with self.assertRaises(ValueError) as c:
                snd.say_as(view, server.HelixChat(s, view), text, KEY3, commands=False)
            self.assertEqual(str(c.exception), "Befehle gibt es nur für den Streamer und Moderatoren")
            self.now[0] += 2
        self.assertEqual([x for x in FakeHelix.log if x[0] != "GET" and "chat/messages" not in x[1]], [])      # nichts ging zu Twitch
        self.moderator(KEY3, login="Zuschauer", uid="55", mod=True)                      # später als Moderator anmelden: die Rechte kommen dazu
        self.assertEqual(self.accounts.pick(KEY3)[0], "mod")                             # (ohne Nachfrage bei Twitch gelten die angemeldeten Rechte)

    def test_the_box_asks_twitch_whether_a_moderator_really_is_one_in_the_channel(self):
        m, tl = self.box(channel="")                                                     # eigener Kanal des Streamers: Nachfragen möglich
        self.accounts = server.TwitchAccounts(tl, self.sessions, check=m.is_moderator, clock=lambda: self.now[0])
        s = self.moderator(KEY2)
        FakeHelix.mods = set()                                                           # sie ist (noch) keine Moderatorin
        FakeHelix.log.clear()
        self.assertEqual(self.accounts.pick(KEY2)[0], "user")
        st = self.accounts.status(KEY2)
        self.assertEqual((st["role"], st["mod"]), ("user", False))
        asks = [x for x in FakeHelix.log if "moderation/moderators" in x[1]]
        self.assertEqual(len(asks), 1)                                                   # die Antwort wird gemerkt
        self.assertIn("broadcaster_id=42&user_id=99", asks[0][1])
        FakeHelix.mods = {"99"}
        self.now[0] += 400                                                               # nach fünf Minuten fragt die Box erneut
        self.assertEqual(self.accounts.pick(KEY2)[0], "mod")
        FakeHelix.mods = set()
        self.now[0] += 400
        FakeHelix.status = 500                                                           # Twitch antwortet nicht: es gelten die angemeldeten Rechte
        self.assertEqual(self.accounts.pick(KEY2)[0], "mod")
        FakeHelix.status = 204

    def test_without_a_possible_check_the_signed_in_rights_count(self):
        m, tl = self.box(channel="bob")                                                  # fremder Kanal: das Konto des Streamers kann nicht nachfragen
        self.assertIsNone(m.is_moderator("99"))
        self.accounts = server.TwitchAccounts(tl, self.sessions, check=m.is_moderator)
        self.moderator(KEY2)
        self.assertEqual(self.accounts.pick(KEY2)[0], "mod")
        self.assertIsNone(m.is_moderator("abc"))


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
