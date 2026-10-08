#!/usr/bin/env python3
"""Tests für Senden über die Twitch-Schnittstelle (HelixChat), den kleinen WebSocket-Client und die Live-Ereignisse (EventSub): alles gegen
Gegenstellen auf 127.0.0.1, nie gegen Twitch."""
import base64
import hashlib
import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import server
import test_twitch_login as T


def srv_frame(op, payload=b"", fin=True, mask=False):
    """Ein Rahmen, wie ihn ein Server schickt (ohne Maske); mask=True nur für den Verstoß-Test."""
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | op])
    mb = 0x80 if mask else 0
    head += bytes([mb | n]) if n < 126 else (bytes([mb | 126]) + n.to_bytes(2, "big") if n < 65536 else bytes([mb | 127]) + n.to_bytes(8, "big"))
    return head + (b"\0\0\0\0" if mask else b"") + payload


class Codec(unittest.TestCase):
    def pair(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        return a, server.WebSocketLite(b)

    def test_text_message_in_one_frame_and_in_fragments(self):
        a, ws = self.pair()
        a.sendall(srv_frame(1, b'{"a":1}'))
        self.assertEqual(ws.recv_message(1), '{"a":1}')
        a.sendall(srv_frame(1, b"Hal", fin=False) + srv_frame(0, b"lo ", fin=False) + srv_frame(0, "Wörld".encode(), fin=True))
        self.assertEqual(ws.recv_message(1), "Hallo Wörld")
        threading.Thread(target=lambda: a.sendall(srv_frame(1, b"x" * 70000)), daemon=True).start()     # 64-Bit-Längenfeld (in eigenem Faden: größer als der Puffer)
        self.assertEqual(len(ws.recv_message(3)), 70000)
        a.sendall(srv_frame(1, b"y" * 300))                                             # 16-Bit-Längenfeld
        self.assertEqual(len(ws.recv_message(1)), 300)

    def test_ping_is_answered_with_pong_and_timeout_keeps_partial_data(self):
        a, ws = self.pair()
        a.sendall(srv_frame(9, b"hi") + srv_frame(1, b"ok"))
        self.assertEqual(ws.recv_message(1), "ok")
        a.settimeout(1)
        reply = a.recv(100)
        self.assertEqual(reply[0], 0x8A)                                                # Pong, mit Maske vom Client
        self.assertTrue(reply[1] & 0x80)
        f = srv_frame(1, b"zusammen")
        a.sendall(f[:5])                                                                # nur ein Teil kommt an
        self.assertIsNone(ws.recv_message(0.2))                                         # Zeitüberschreitung, nichts geht verloren
        a.sendall(f[5:])
        self.assertEqual(ws.recv_message(1), "zusammen")
        a.sendall(srv_frame(1, b"Teil1", fin=False))
        self.assertIsNone(ws.recv_message(0.2))
        a.sendall(srv_frame(0, b"Teil2"))
        self.assertEqual(ws.recv_message(1), "Teil1Teil2")

    def test_close_and_violations_raise(self):
        a, ws = self.pair()
        a.sendall(srv_frame(8, b"\x03\xe8"))
        with self.assertRaises(EOFError):
            ws.recv_message(1)
        a, ws = self.pair()
        a.sendall(srv_frame(1, b"x", mask=True))                                        # ein Server maskiert nie
        with self.assertRaises(OSError):
            ws.recv_message(1)
        a, ws = self.pair()
        a.sendall(bytes([0x81 | 0x40, 1]) + b"x")                                       # reservierte Bits
        with self.assertRaises(OSError):
            ws.recv_message(1)
        a, ws = self.pair()
        a.sendall(srv_frame(2, b"binaer"))                                              # Twitch sendet Text
        with self.assertRaises(OSError):
            ws.recv_message(1)
        a, ws = self.pair()
        a.sendall(bytes([0x81, 127]) + (server.WebSocketLite.MAX + 1).to_bytes(8, "big"))     # zu groß, ohne es zu lesen
        with self.assertRaises(OSError):
            ws.recv_message(1)
        a, ws = self.pair()
        a.sendall(srv_frame(0, b"streu"))                                               # Fortsetzung ohne Anfang
        with self.assertRaises(OSError):
            ws.recv_message(1)
        a, ws = self.pair()
        a.sendall(srv_frame(9, b"x" * 200))                                             # Steuerrahmen höchstens 125 Byte
        with self.assertRaises(OSError):
            ws.recv_message(1)
        a, ws = self.pair()
        a.close()
        with self.assertRaises(EOFError):
            ws.recv_message(1)

    def test_a_flood_of_pings_does_not_hold_the_loop(self):
        a, ws = self.pair()
        stop = threading.Event()

        def flood():
            while not stop.is_set():
                try:
                    a.sendall(srv_frame(9, b"p"))
                except OSError:
                    return
        threading.Thread(target=flood, daemon=True).start()
        self.addCleanup(stop.set)

        def drain():                                                                      # die Pong-Antworten des Clients abnehmen
            while not stop.is_set():
                try:
                    a.settimeout(0.2)
                    a.recv(65536)
                except OSError:
                    pass
        threading.Thread(target=drain, daemon=True).start()
        t0 = time.monotonic()
        self.assertIsNone(ws.recv_message(0.3))
        self.assertLess(time.monotonic() - t0, 1.5)                                      # die Zeitgrenze gilt auch bei lauter Steuerrahmen
        self.assertGreater(ws.last_rx, t0)                                               # und die Gegenstelle gilt als lebendig

    def test_client_frames_are_masked(self):
        f = server.WebSocketLite.frame(1, b"abc")
        self.assertEqual(f[0], 0x81)
        self.assertTrue(f[1] & 0x80)
        mask = f[2:6]
        self.assertEqual(bytes(b ^ mask[i % 4] for i, b in enumerate(f[6:])), b"abc")


class WsServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, script):
        self.script, self.connections, self.paths = script, 0, []
        super().__init__(("127.0.0.1", 0), WsHandler)


class WsHandler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server
        srv.connections += 1
        n = srv.connections
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            buf += chunk
        lines = buf.decode("latin-1").split("\r\n")
        srv.paths.append(lines[0].split()[1])
        key = next(l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("sec-websocket-key"))
        acc = base64.b64encode(hashlib.sha1(key.encode("ascii") + server.WebSocketLite.GUID).digest()).decode()
        self.request.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
        try:
            srv.script(self.request, n, srv)
        except OSError:
            pass


def msg(kind, payload=None, mid=None):
    return json.dumps({"metadata": {"message_id": mid or "id-%s-%f" % (kind, time.time()), "message_type": kind}, "payload": payload or {}}).encode()


def notif(typ, event, mid):
    return msg("notification", {"subscription": {"type": typ}, "event": event}, mid)


class Handshake(unittest.TestCase):
    def test_wrong_accept_key_is_refused(self):
        def script(sock, n, srv):
            time.sleep(0.5)
        srv = WsServer(script)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        s = socket.create_connection(srv.server_address, timeout=3)
        ws = server.WebSocketLite.handshake(s, "127.0.0.1", "/ws")                      # richtig: klappt
        self.assertIsInstance(ws, server.WebSocketLite)
        s.close()

        class Bad(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(4096)
                self.request.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: falsch\r\n\r\n")
        bad = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Bad)
        threading.Thread(target=bad.serve_forever, daemon=True).start()
        self.addCleanup(bad.server_close)
        self.addCleanup(bad.shutdown)
        s2 = socket.create_connection(bad.server_address, timeout=3)
        with self.assertRaises(OSError):
            server.WebSocketLite.handshake(s2, "127.0.0.1", "/ws")
        s2.close()


class FakeReader:
    def __init__(self):
        self.items, self.idle = [], False

    def _idle(self):
        return self.idle

    def _add(self, item):
        self.items.append(item)


class Events(T.Base):
    def setUp(self):
        super().setUp()
        T.FakeHelix.log, T.FakeHelix.status, T.FakeHelix.sub_status, T.FakeHelix.sub_script = [], 204, 202, []
        self.hx = http.server.ThreadingHTTPServer(("127.0.0.1", 0), T.FakeHelix)
        threading.Thread(target=self.hx.serve_forever, daemon=True).start()

    def tearDown(self):
        self.hx.shutdown()
        self.hx.server_close()
        super().tearDown()

    def build(self, script, events=True, channel=""):
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        if channel:
            st.set({"channel": channel})
        tl = self.make()
        st.account = tl
        tl.start(events=events)
        self.wait(tl, "angemeldet")
        mod = server.TwitchMod(st, tl, api_base="http://127.0.0.1:%d/helix/" % self.hx.server_address[1], client_id="testclient", paths=tl.paths)
        rd = FakeReader()
        ws = WsServer(script)
        threading.Thread(target=ws.serve_forever, daemon=True).start()
        self.addCleanup(ws.server_close)
        self.addCleanup(ws.shutdown)
        ev = server.TwitchEvents(st, tl, mod, rd, paths=tl.paths, host="127.0.0.1", port=ws.server_address[1], tls=False, sleep=lambda s: time.sleep(0.02))
        ev.KEEP_MIN = 0.1
        ev.SUB_DELAYS = (0.0, 0.3, 0.6)
        self.addCleanup(lambda: setattr(rd, "idle", True))
        return ev, rd, ws

    def until(self, cond, secs=6):
        end = time.time() + secs
        while time.time() < end and not cond():
            time.sleep(0.02)
        return cond()

    def subs(self):
        return [json.loads(b) for m, p, a, c, b in T.FakeHelix.log if p == "/helix/eventsub/subscriptions"]

    def test_follow_and_points_arrive_once_and_are_subscribed_with_the_session(self):
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "SESS_1", "keepalive_timeout_seconds": 30}})))
            end = time.time() + 5
            while len(T.FakeHelix.log) < 2 and time.time() < end:                       # erst abwarten, bis beide Abonnements angelegt sind
                time.sleep(0.02)
            sock.sendall(srv_frame(1, notif("channel.follow", {"user_name": "Anna", "user_login": "anna"}, "m-1")))
            sock.sendall(srv_frame(1, notif("channel.follow", {"user_name": "Anna", "user_login": "anna"}, "m-1")))          # doppelt: wird verworfen
            sock.sendall(srv_frame(1, notif("channel.channel_points_custom_reward_redemption.add",
                                            {"user_name": "Lena", "reward": {"title": "Wasser trinken", "cost": 500}, "user_input": "jetzt"}, "m-2")))
            sock.sendall(srv_frame(1, msg("session_keepalive")))
            time.sleep(1)
        ev, rd, ws = self.build(script)
        ev.ensure()
        self.assertTrue(self.until(lambda: len(rd.items) >= 2))
        time.sleep(0.2)
        self.assertEqual(len(rd.items), 2)
        self.assertEqual(rd.items[0]["ev"], {"k": "follow", "n": 0})
        self.assertEqual(rd.items[0]["text"], "Anna")
        self.assertEqual(rd.items[1]["ev"], {"k": "points", "n": 500})
        self.assertEqual(rd.items[1]["text"], "Lena: Wasser trinken – jetzt")
        subs = self.subs()
        self.assertEqual([s["type"] for s in subs], ["channel.follow", "channel.channel_points_custom_reward_redemption.add"])
        self.assertEqual(subs[0]["condition"], {"broadcaster_user_id": "42", "moderator_user_id": "42"})
        self.assertEqual(subs[1]["condition"], {"broadcaster_user_id": "42"})
        self.assertEqual(subs[0]["transport"], {"method": "websocket", "session_id": "SESS_1"})
        self.assertEqual(subs[0]["version"], "2")
        auth = [a for m, p, a, c, b in T.FakeHelix.log if p == "/helix/eventsub/subscriptions"]
        self.assertTrue(all(a == "Bearer AT1" for a in auth))
        self.assertTrue(self.until(lambda: ev.status()["state"] == "ok"))
        self.assertEqual(ev.status()["types"], sorted(t[0] for t in server.TwitchEvents.SUBS))

    def test_reconnect_goes_to_the_given_address_without_new_subscriptions(self):
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "SESS_A", "keepalive_timeout_seconds": 30}})))
            if n == 1:
                end = time.time() + 5
                while len(T.FakeHelix.log) < 2 and time.time() < end:
                    time.sleep(0.02)
                sock.sendall(srv_frame(1, msg("session_reconnect", {"session": {"id": "SESS_A", "reconnect_url": "wss://127.0.0.1/ws?neu=1"}})))
                time.sleep(0.5)
            else:
                sock.sendall(srv_frame(1, notif("channel.follow", {"user_name": "Neu"}, "m-9")))
                time.sleep(1)
        ev, rd, ws = self.build(script)
        ev.ensure()
        self.assertTrue(self.until(lambda: any(i["text"] == "Neu" for i in rd.items)))
        self.assertEqual(ws.connections, 2)
        self.assertIn("neu=1", ws.paths[1])
        self.assertEqual(len(self.subs()), 2)                                            # nicht noch einmal abonniert

    def test_foreign_reconnect_address_is_refused(self):
        self.assertEqual(server.TwitchEvents._split_url("wss://eventsub.wss.twitch.tv/ws?x=1"), ("eventsub.wss.twitch.tv", "/ws?x=1"))
        for bad in ("ws://eventsub.wss.twitch.tv/ws", "wss://evil.example.com/ws", "https://eventsub.wss.twitch.tv", "wss://x.twitch.tv.evil.com/ws", "", None):
            with self.assertRaises(OSError, msg=repr(bad)):
                server.TwitchEvents._split_url(bad)

    def test_silent_connection_is_reopened(self):
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "S%d" % n, "keepalive_timeout_seconds": 0.2}})))
            time.sleep(6)                                                                # danach still, die Verbindung bleibt offen
        ev, rd, ws = self.build(script)
        ev.KEEP_MIN = 0.1
        ev.RETRY_FIRST = 0.05
        ev.ensure()
        self.assertTrue(self.until(lambda: ws.connections >= 2, 5))                      # nach 1,5 x Keepalive still: neu verbunden

    def test_no_rights_or_foreign_channel_means_no_connection(self):
        ev, rd, ws = self.build(lambda s, n, srv: None, events=False)                    # Zugang ohne die Rechte für Follows und Kanalpunkte
        ev.ensure()
        time.sleep(0.3)
        self.assertEqual(ws.connections, 0)
        self.assertEqual(ev.status()["state"], "aus")
        ev2, rd2, ws2 = self.build(lambda s, n, srv: None, channel="jemandanders")      # fremder Kanal
        ev2.ensure()
        time.sleep(0.3)
        self.assertEqual(ws2.connections, 0)

    def test_rejected_subscriptions_show_an_error_and_do_not_crash(self):
        T.FakeHelix.sub_status = 400
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "SESS_X", "keepalive_timeout_seconds": 30}})))
            time.sleep(1)
        ev, rd, ws = self.build(script)
        ev.ensure()
        self.assertTrue(self.until(lambda: ev.status()["state"] == "fehler"))
        self.assertEqual(ev.status()["types"], [])

    def test_revocation_removes_the_type(self):
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "SESS_R", "keepalive_timeout_seconds": 30}})))
            end = time.time() + 5
            while len(T.FakeHelix.log) < 2 and time.time() < end:
                time.sleep(0.02)
            time.sleep(0.3)
            sock.sendall(srv_frame(1, msg("revocation", {"subscription": {"type": "channel.follow"}})))
            time.sleep(1)
        ev, rd, ws = self.build(script)
        ev.ensure()
        self.assertTrue(self.until(lambda: ev.status()["state"] == "ok"))
        self.assertTrue(self.until(lambda: "channel.follow" not in ev.status()["types"]))
        self.assertEqual(ev.status()["state"], "ok")                                     # ein Abonnement bleibt

    def test_idle_reader_stops_the_connection(self):
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "SESS_I", "keepalive_timeout_seconds": 30}})))
            time.sleep(3)
        ev, rd, ws = self.build(script)
        ev.ensure()
        self.assertTrue(self.until(lambda: ws.connections == 1))
        rd.idle = True
        self.assertTrue(self.until(lambda: ev.status()["state"] == "aus", 5))

    def test_status_never_waits_behind_a_token_refresh(self):
        ev, rd, ws = self.build(lambda s, n, srv: None)
        tl = ev.account
        tl.tokens["expires_at"] = self.now[0] + 30                                      # läuft gleich ab: token() müsste erneuern
        release = threading.Event()

        def hold():
            with tl.lock:
                release.wait(5)
        th = threading.Thread(target=hold, daemon=True)
        th.start()
        time.sleep(0.1)
        t0 = time.time()
        ev.status()
        ev.granted()
        ev.ensure()
        took = time.time() - t0
        release.set()
        self.assertLess(took, 0.5)                                                       # die Oberfläche wartet nie hinter einer Anfrage an Twitch

    def test_failed_subscription_is_tried_again_and_409_counts_as_done(self):
        T.FakeHelix.sub_script = [500, 500, 202, 202]                                    # erste Runde scheitert, zweite klappt
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "SESS_RETRY", "keepalive_timeout_seconds": 30}})))
            time.sleep(4)
        ev, rd, ws = self.build(script)
        ev.ensure()
        self.assertTrue(self.until(lambda: ev.status()["state"] == "ok", 5))
        self.assertEqual(len(self.subs()), 4)
        self.assertEqual(ev.status()["types"], sorted(t[0] for t in server.TwitchEvents.SUBS))
        # 409 = gibt es schon: gilt als angelegt
        T.FakeHelix.log.clear()
        T.FakeHelix.sub_script = [409, 409]
        ev2, rd2, ws2 = self.build(script)
        ev2.ensure()
        self.assertTrue(self.until(lambda: ev2.status()["state"] == "ok", 5))
        self.assertEqual(len(self.subs()), 2)

    def test_stale_subscribe_thread_cannot_overwrite_a_newer_session(self):
        ev, rd, ws = self.build(lambda s, n, srv: None)
        ev.sid = "NEU"
        ev.state, ev.types = "ok", {"channel.follow"}
        ev._subscribe(ev.gen, "ALT", "AT1", None)                                        # eine späte Antwort der alten Sitzung
        self.assertEqual((ev.state, ev.types), ("ok", {"channel.follow"}))
        self.assertEqual(self.subs(), [])                                                # und sie legt auch nichts mehr an

    def test_all_revoked_restarts_the_session_and_scope_change_resubscribes(self):
        def script(sock, n, srv):
            sock.sendall(srv_frame(1, msg("session_welcome", {"session": {"id": "SESS_%d" % n, "keepalive_timeout_seconds": 30}})))
            if n == 1:
                end = time.time() + 5
                while len(T.FakeHelix.log) < 2 and time.time() < end:
                    time.sleep(0.02)
                time.sleep(0.3)
                for typ in [t[0] for t in server.TwitchEvents.SUBS]:
                    sock.sendall(srv_frame(1, msg("revocation", {"subscription": {"type": typ}})))
            time.sleep(3)
        ev, rd, ws = self.build(script)
        ev.RETRY_FIRST = 0.05
        ev.ensure()
        self.assertTrue(self.until(lambda: ws.connections >= 2, 6))                      # alles entzogen: neue Sitzung
        self.assertTrue(self.until(lambda: len(self.subs()) >= 4, 6))                    # und neu abonniert
        # Rechte ändern sich während der Verbindung: neue Sitzung mit weniger Abonnements
        n0 = ws.connections
        T.FakeHelix.log.clear()
        ev.account.tokens["scopes"] = [x for x in ev.account.tokens["scopes"] if x != "channel:read:redemptions"]
        self.assertTrue(self.until(lambda: ws.connections > n0, 6))
        self.assertTrue(self.until(lambda: len(self.subs()) >= 1, 6))
        self.assertEqual([s["type"] for s in self.subs()], ["channel.follow"])

    def test_reconnect_url_without_a_path(self):
        self.assertEqual(server.TwitchEvents._split_url("wss://eventsub.wss.twitch.tv?challenge=abc-123"), ("eventsub.wss.twitch.tv", "/?challenge=abc-123"))
        self.assertEqual(server.TwitchEvents._split_url("wss://eventsub.wss.twitch.tv/ws?challenge=abc"), ("eventsub.wss.twitch.tv", "/ws?challenge=abc"))
        for bad in ("wss://eventsub.wss.twitch.tv:8080/ws", "wss://user@eventsub.wss.twitch.tv/ws", "wss://eventsub.wss.twitch.tv/ws\r\nX: y"):
            with self.assertRaises(OSError):
                server.TwitchEvents._split_url(bad)

    def test_map_event_cleans_and_limits_values(self):
        m = server.TwitchEvents.map_event
        self.assertIsNone(m("channel.follow", {}))
        self.assertIsNone(m("channel.follow", "kaputt"))
        self.assertIsNone(m("unbekannt", {"user_name": "x"}))
        it = m("channel.follow", {"user_name": "A\x07\x1bB" + "x" * 200})
        self.assertNotIn("\x07", it["text"])
        self.assertLessEqual(len(it["text"]), 40)
        p = m("channel.channel_points_custom_reward_redemption.add", {"user_name": "U", "reward": {"title": "T", "cost": "viel"}})
        self.assertEqual(p["ev"]["n"], 0)                                                # keine Zahl: 0, nie ein Fehler
        p = m("channel.channel_points_custom_reward_redemption.add", {"user_name": "U", "reward": {"title": "T", "cost": 10 ** 12}})
        self.assertEqual(p["ev"]["n"], 100_000_000)
        self.assertIsNone(m("channel.channel_points_custom_reward_redemption.add", {"reward": {}}))


class Scopes(T.Base):
    def test_default_events_and_union_of_scopes(self):
        tl = self.make()
        self.assertEqual(tl.scopes_for(), "chat:read chat:edit user:write:chat")
        self.assertEqual(tl.scopes_for(events=True), "chat:read chat:edit user:write:chat moderator:read:followers channel:read:redemptions")
        tl.start(mod=True)
        self.wait(tl, "angemeldet")
        st = tl.status()
        self.assertTrue(st["mod"] and st["helix_chat"])
        self.assertFalse(st["events"])
        # Ereignisse später dazu: die Moderation geht nicht verloren
        self.assertEqual(tl.scopes_for(events=True).split(), ["chat:read", "chat:edit", "user:write:chat", "moderator:manage:banned_users",
                                                              "moderator:manage:chat_messages", "moderator:read:followers", "channel:read:redemptions"])
        T.FakeTwitch.state["polls"] = 0
        tl.start(events=True)
        self.wait(tl, "angemeldet")
        st = tl.status()
        self.assertTrue(st["events"] and st["mod_scope"] and st["helix_chat"])

    def test_old_login_without_the_new_right_still_works_via_irc(self):
        T.FakeTwitch.state["scopes_override"] = ["chat:read", "chat:edit"]
        tl = self.make()
        tl.start()
        self.wait(tl, "angemeldet")
        # die Gegenstelle gibt die angeforderten Rechte zurück; ein alter Zugang hat nur Lesen und Schreiben
        tl.tokens["scopes"] = ["chat:read", "chat:edit"]
        self.assertFalse(tl.status()["helix_chat"])
        self.assertFalse(server.HelixChat(tl, None).usable())


class Sending(T.Base):
    def setUp(self):
        super().setUp()
        T.FakeHelix.log, T.FakeHelix.status = [], 204
        T.FakeHelix.chat_reply = {"data": [{"message_id": "m1", "is_sent": True}]}
        self.hx = http.server.ThreadingHTTPServer(("127.0.0.1", 0), T.FakeHelix)
        threading.Thread(target=self.hx.serve_forever, daemon=True).start()

    def tearDown(self):
        self.hx.shutdown()
        self.hx.server_close()
        T.FakeHelix.chat_reply = {"data": [{"message_id": "m1", "is_sent": True}]}
        super().tearDown()

    def build(self, channel=""):
        st = server.TwitchStore(os.path.join(self.dir, "twitch.json"))
        if channel:
            st.set({"channel": channel})
        tl = self.make()
        st.account = tl
        tl.start()
        self.wait(tl, "angemeldet")
        mod = server.TwitchMod(st, tl, api_base="http://127.0.0.1:%d/helix/" % self.hx.server_address[1], client_id="testclient", paths=tl.paths)
        return st, tl, mod

    def test_helix_send_and_reply_checks(self):
        st, tl, mod = self.build()
        hc = server.HelixChat(tl, mod)
        self.assertTrue(hc.usable())
        self.assertEqual(hc.send("", "Hallo"), server.TwitchChat.OK)
        m, path, auth, cid, body = [x for x in T.FakeHelix.log if x[1] == "/helix/chat/messages"][0]
        self.assertEqual(json.loads(body), {"broadcaster_id": "42", "sender_id": "42", "message": "Hallo"})   # eigener Kanal: eigene Kennung
        self.assertEqual((auth, cid), ("Bearer AT1", "testclient"))
        T.FakeHelix.chat_reply = {"data": [{"message_id": "", "is_sent": False, "drop_reason": {"code": "msg_duplicate", "message": "Your message is a duplicate."}}]}
        with self.assertRaises(ValueError) as cm:
            hc.send("", "Hallo")
        self.assertIn("nicht gesendet", str(cm.exception))
        self.assertIn("duplicate", str(cm.exception))
        T.FakeHelix.chat_reply = {"data": []}
        with self.assertRaises(ValueError):
            hc.send("", "Hallo")                                                         # keine Bestätigung: nie "gesendet" melden

    def test_other_channel_is_looked_up_by_name(self):
        st, tl, mod = self.build(channel="bob")
        hc = server.HelixChat(tl, mod)
        hc.send("bob", "Hi")
        body = json.loads([x for x in T.FakeHelix.log if x[1] == "/helix/chat/messages"][0][4])
        self.assertEqual(body["broadcaster_id"], "777")

    def test_sender_prefers_helix_and_falls_back_to_irc(self):
        class Irc:
            def __init__(self):
                self.calls = []

            def send(self, *a):
                self.calls.append(a)
                return True, "ok"
        st, tl, mod = self.build()
        irc = Irc()
        s = server.TwitchSender(st, chat=irc, mod=mod, helix=server.HelixChat(tl, mod), clock=lambda: time.monotonic() + 1000 * len(irc.calls) + 1e6)
        out = s.say("Hallo Welt")
        self.assertTrue(out["ok"])
        self.assertEqual(irc.calls, [])                                                  # über Helix, nicht über IRC
        tl.tokens["scopes"] = ["chat:read", "chat:edit"]                                 # alter Zugang ohne das neue Recht
        s.last = -1e9
        s.say("Noch einmal")
        self.assertEqual(len(irc.calls), 1)
        self.assertEqual(irc.calls[0][3], "Noch einmal")

    def test_helix_errors_are_short_and_german(self):
        st, tl, mod = self.build()
        hc = server.HelixChat(tl, mod)
        orig = mod._call

        def forbidden(*a, **k):
            raise ValueError(server.TwitchMod.ERR_FORBIDDEN)                              # 403 von Twitch, zum Beispiel Nur-Follower-Chat
        mod._call = forbidden
        with self.assertRaises(ValueError) as cm:
            hc.send("", "x")
        self.assertEqual(str(cm.exception), server.HelixChat.ERR_FORBIDDEN)             # ein verständlicher Satz statt "Moderator im Kanal?"
        mod._call = orig


class OnceRequests(unittest.TestCase):
    """ChatPaths.request(once=True): eine gesendete Anfrage wird nie ein zweites Mal über einen anderen Weg gesendet."""
    def raw_server(self, reply):
        hits = []

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                hits.append(1)
                self.request.recv(65536)
                if reply:
                    self.request.sendall(reply)
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, hits

    def test_no_answer_after_sending_gives_minus_one_and_one_request_only(self):
        srv, hits = self.raw_server(b"")
        url = "http://127.0.0.1:%d/x" % srv.server_address[1]
        paths = server.ChatPaths(lambda: ["127.0.0.1"])                                  # zwei Wege: 127.0.0.1 und die normale Route
        st, body = paths.request("POST", url, b"{}", {}, timeout=2, once=True)
        self.assertEqual((st, body), (-1, b""))
        self.assertEqual(len(hits), 1)                                                   # nicht über den nächsten Weg wiederholt
        hits.clear()
        paths2 = server.ChatPaths(lambda: ["127.0.0.1"])
        st, body = paths2.request("GET", url, None, {}, timeout=2)                      # ohne once: darf es noch einmal versuchen
        self.assertEqual(st, 0)
        self.assertGreaterEqual(len(hits), 2)

    def test_status_line_received_but_body_lost_keeps_the_status(self):
        srv, hits = self.raw_server(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nabc")
        url = "http://127.0.0.1:%d/x" % srv.server_address[1]
        st, body = server.ChatPaths(lambda: ["127.0.0.1"]).request("POST", url, b"{}", {}, timeout=2, once=True)
        self.assertEqual(st, 200)                                                        # der Status gilt, auch wenn der Rumpf unvollständig ist
        self.assertLessEqual(len(body), 3)
        self.assertEqual(len(hits), 1)

    def test_mod_call_turns_minus_one_into_a_clear_message(self):
        class P:
            def request(self, *a, **k):
                return -1, b""
        class A:
            def token(self):
                return "AT"
        mod = server.TwitchMod(None, A(), paths=P(), client_id="x")
        with self.assertRaises(ValueError) as cm:
            mod._call("POST", "chat/messages", None, {}, once=True)
        self.assertEqual(str(cm.exception), server.TwitchMod.ERR_UNSURE)


class ReaderHook(unittest.TestCase):
    def test_poll_starts_the_events_when_not_demo(self):
        class Stub:
            def __init__(self):
                self.n = 0

            def ensure(self):
                self.n += 1
        import types
        store = types.SimpleNamespace(data={"channel": ""})
        r = server.TwitchReader(store)
        stub = Stub()
        r.events = stub
        r.poll(0)
        self.assertEqual(stub.n, 0)                                                      # ohne Kanal gibt es nichts zu tun
        store.data["channel"] = "kanal"
        r.IDLE = 0.0
        r.poll(0)
        self.assertEqual(stub.n, 1)


if __name__ == "__main__":
    unittest.main()
