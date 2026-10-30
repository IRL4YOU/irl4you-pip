
import re
from tw.login import TwitchLogin
from tw.mod import HelixChat
try:
    import pipbox_preview
except ImportError:
    pipbox_preview = None
from srv.auth import REMEMBER_SECONDS, SESSION_SECONDS
from srv.datenschutz import neustart_noetig
from srv.httpd import scene_swap
from srv.netinfo import iface_ips
from srv.pipeline import DELAY_KEYS, PipelineStore, always_compatible, view_only_change
from srv.style import clean_styles
from srv.versions import belacoder_running
from srv.util import NOT_HANDLED


class PostRoutes:
    def post_public(self, path, d):
        if path == "/api/setup":
            self.auth.set_password(d.get("code"), d.get("password"), self.ip())
            rem = d.get("remember") is True
            tok = self.auth.login(d.get("password"), self.ip(), rem)
            return self.reply(200, {"ok": True}, self.cookie(tok, REMEMBER_SECONDS if rem else SESSION_SECONDS))
        if path == "/api/login":
            rem = d.get("remember") is True
            tok = self.auth.login(d.get("password"), self.ip(), rem)
            return self.reply(200, {"ok": True}, self.cookie(tok, REMEMBER_SECONDS if rem else SESSION_SECONDS))
        if path == "/api/logout":
            self.auth.logout(self.token())
            return self.reply(200, {"ok": True}, self.cookie("", 0))
        return NOT_HANDLED

    def post_sending(self, path, d):
        if path == "/api/network":
            self.netchoice.select(d.get("iface"))
            return self.reply(200, {"ok": True})
        if path == "/api/send":
            self.send.request(d.get("action"), d.get("confirm") is True)
            if d.get("action") in ("stop", "start"):
                self.autostart.cancel("Sendung wurde von Hand gestartet oder beendet")
            return self.reply(200, {"ok": True})
        if path == "/api/autostart":
            self.autostart.set_enabled(d.get("enabled"))
            return self.reply(200, self.autostart.status())
        if path == "/api/power":
            self.power.request(d.get("action"), d.get("confirm") is True)
            return self.reply(200, {"ok": True})
        if path == "/api/logmode":
            self.logmode.request(d.get("mode"))
            return self.reply(200, {"ok": True})
        if path == "/api/statsshare":
            en = d.get("enabled")
            if en is not None and not isinstance(en, bool):
                raise ValueError("enabled muss wahr oder falsch sein")
            self.statsshare.update(None, d.get("groups"), d.get("new_token") is True)
            if en is True:
                self.remote.request("stats_on", True, True)
                self.statsshare.update(True)
            elif en is False:
                self.statsshare.update(False)
                try:
                    self.remote.request("stats_off", True)
                except ValueError:
                    pass
            return self.reply(200, self.stats_status())
        if path == "/api/deck":
            return self.reply(200, self.deck.put(d))
        if path == "/api/layout":
            if isinstance(d.get("init"), dict):
                return self.reply(200, self.layout.init(d["init"]))
            return self.reply(200, self.layout.put(d.get("profile"), d))
        if path == "/api/datenschutz":
            try:
                if "addon" in d:
                    self.datenschutz.addon_request(d.get("addon"), bool(belacoder_running() or self.send._active()))
                    return self.reply(200, self.datenschutz.get())
                if d.get("restart_send") is True:
                    res = self.datenschutz.get()
                    res["sending"] = self.send._active()
                    res["restarted"], res["note"] = False, ""
                    if res["sending"] and neustart_noetig(self.pipeline.build()):
                        res["restarted"], res["note"] = self.send.restart_if_live()
                    res["neustart_noetig"] = False
                    return self.reply(200, res)
                return self.reply(200, self.datenschutz.put(d))
            except ValueError as e:
                return self.reply(400, {"error": str(e)})
        return NOT_HANDLED

    def post_admin(self, path, d):
        if path == "/api/btdriver":
            self.btdriver.request()
            return self.reply(200, self.btdriver.status())
        if path == "/api/developer":
            self.developer.request(d.get("action"), d.get("confirm") is True)
            return self.reply(200, self.developer.status())
        if path == "/api/logs":
            if d.get("action") != "collect":
                raise ValueError("Unbekannte Aktion")
            self.logbundle.request()
            return self.reply(200, self.logbundle.status())
        if path == "/api/wifi":
            self.wifi.request(d)
            return self.reply(200, {"ok": True})
        if path == "/api/controller-keys":
            self.ckeys.set_map(d.get("addr"), d.get("code"), d.get("fn"))
            return self.reply(200, {"ok": True})
        if path == "/api/devname":
            if self.names is None:
                raise ValueError("Namen sind hier nicht verfügbar")
            self.names.set(d.get("key"), d.get("name", ""))
            return self.reply(200, {"ok": True})
        if path == "/api/remote":
            self.remote.request(d.get("action"), d.get("confirm") is True, d.get("public") is True)
            return self.reply(200, {"ok": True})
        if path.startswith("/api/settings/"):
            pw = d.get("password")
            if pw is not None and not isinstance(pw, str):
                raise ValueError("Passwort ungültig")
            if path == "/api/settings/export":
                out = self.transfer.export(d.get("secrets") is True, pw or None, d.get("wifi") is not False)
                return self.reply(200, dict(out, ok=True))
            if path == "/api/settings/preview":
                return self.reply(200, self.transfer.preview(d.get("document"), pw or None))
            if path == "/api/settings/import":
                secs = d.get("sections")
                if secs is not None and (not isinstance(secs, list) or any(not isinstance(x, str) for x in secs)):
                    raise ValueError("Auswahl ungültig")
                return self.reply(200, self.transfer.apply(d.get("document"), pw or None, secs))
            if path == "/api/settings/restore":
                return self.reply(200, self.transfer.restore())
            return self.reply(404, {"error": "not found"})
        if path == "/api/swupdate":
            self.swupdate.request(d.get("action"), d.get("confirm") is True, d.get("version"), d.get("older") is True)
            return self.reply(200, {"ok": True})
        return NOT_HANDLED

    def post_picture(self, path, d):
        if path == "/api/pipeline/view":
            if "visible" not in d and "audio" not in d and "mute" not in d:
                raise ValueError("Nichts zu ändern")
            return self.reply(200, dict(self.send.change_view(d.get("visible"), d.get("audio"), d.get("mute")), ok=True))
        if path == "/api/pipeline/active":
            key, act = d.get("key"), d.get("active")
            c = self.pipeline.cfg
            if act is False and c.get("type") == "pip" and key and c.get("audio") in ("pip", "pip2", "pip3") and c.get(c["audio"]) == key and key != c.get("main"):
                self.send.change_view(audio="main")
            self.pipeline.set_active(key, act)
            return self.reply(200, {"ok": True})
        if path == "/api/pipeline/swap":
            return self.reply(200, scene_swap(self.pipeline, self.cams, self.send, d.get("with")))
        if path == "/api/pipeline/facecam":
            slot = d.get("slot", "")
            if not isinstance(slot, str):
                raise ValueError("Facecam: eines der Bilder oder keine")
            aud = self.pipeline.set_facecam(slot)
            return self.reply(200, dict(self.send.change_view(audio=aud), ok=True, facecam=self.pipeline.cfg.get("facecam", "")))
        if path == "/api/pipeline":
            before = dict(self.pipeline.cfg)
            before["styles"] = clean_styles(before.get("styles"))
            self.pipeline.set(d, [c["key"] for c in self.cams.cams])
            restarted, note = (False, "")
            if self.pipeline.cfg != before and self.send.always_live() and always_compatible(before, self.pipeline.cfg):
                hide, aud = PipelineStore.view_values(self.pipeline.cfg)
                cur = self.send.view_state()
                if (hide, aud) != PipelineStore.view_values(before):
                    self.send.apply_view(hide, aud, bool(cur and cur["mute"]))
                return self.reply(200, {"ok": True, "restarted": False, "note": "Gespeichert. Die Änderung gilt sofort, ohne Neustart."})
            if self.pipeline.cfg != before and view_only_change(before, self.pipeline.cfg):
                hide, aud = PipelineStore.view_values(self.pipeline.cfg)
                audio_changed = before.get("audio") != self.pipeline.cfg.get("audio")
                cur = self.send.view_state()
                if self.send.view_live() and (not audio_changed or self.send.audio_live()) and \
                        self.send.apply_view(hide, aud, bool(cur and cur["mute"])):
                    return self.reply(200, {"ok": True, "restarted": False, "note": "Gespeichert. Die Änderung wird live übernommen, ohne Neustart."})
                if self.send.view_degraded():
                    print("Ansicht im Notbetrieb gespeichert, kein Neustart", flush=True)
                    return self.reply(200, {"ok": True, "restarted": False, "note": "Gespeichert. Gilt, sobald alle Kameras wieder da sind (Notbetrieb: ohne Neustart)."})
            if self.pipeline.cfg != before:
                only_delay = {k: v for k, v in before.items() if k not in DELAY_KEYS} == \
                             {k: v for k, v in self.pipeline.cfg.items() if k not in DELAY_KEYS}
                pips_changed = any(before.get(k) != self.pipeline.cfg.get(k) for k in DELAY_KEYS[1:])
                live_ok = self.send.delay_live_pips() if pips_changed else self.send.delay_live()
                if only_delay and live_ok:
                    note = "Gespeichert. Die Verzögerung wird live übernommen, ohne Neustart."
                else:
                    restarted, note = self.send.restart_if_live()
            return self.reply(200, {"ok": True, "restarted": restarted, "note": note})
        if path == "/api/srtla":
            return self.reply(200, {"id": self.srtla.add(d)})
        if path == "/api/srtla/select":
            self.srtla.select(d.get("id"))
            return self.reply(200, {"ok": True})
        if path == "/api/srtla/settings":
            self.srtla.set_settings(d, [o["iface"] for o in iface_ips()] or ["eth0", "eth1"])
            return self.reply(200, {"ok": True})
        m2s = re.match(r"^/api/srtla/([0-9a-f]{8})/streamid$", path)
        if m2s:
            if self.send._active():
                return self.reply(409, {"error": "Die Stream-ID lässt sich nur anzeigen, wenn die Sendung beendet ist."})
            try:
                return self.reply(200, {"streamid": self.srtla.streamid(m2s.group(1))})
            except KeyError:
                return self.reply(404, {"error": "nicht gefunden"})
        m2 = re.match(r"^/api/srtla/([0-9a-f]{8})$", path)
        if m2:
            try:
                self.srtla.update(m2.group(1), d)
                return self.reply(200, {"ok": True})
            except KeyError:
                return self.reply(404, {"error": "nicht gefunden"})
        return NOT_HANDLED

    def post_access(self, path, d):
        if path == "/api/dji/cmd":
            return self.reply(200, self.djisvc.command(d))
        if path == "/api/support":
            try:
                if d.get("action") == "create":
                    return self.reply(200, self.support.create(d.get("title"), d.get("kind", "issue"), d.get("id")))
                if d.get("action") == "done":
                    return self.reply(200, self.support.mark(d.get("id"), d.get("done")))
                if d.get("action") == "delete":
                    return self.reply(200, self.support.delete(d.get("id")))
            except ValueError as e:
                return self.reply(400, {"error": str(e)})
            except KeyError:
                return self.reply(404, {"error": "nicht gefunden"})
            return self.reply(400, {"error": "Ungültige Anfrage"})
        if path == "/api/alerts/dismiss":
            if d.get("kind") in ("cam", "hdmi"):
                return self.reply(200, {"ok": bool(self.outages.dismiss(d.get("kind"), d.get("t")))})
            return self.reply(200, {"ok": bool(self.usbwatch.dismiss(d.get("t")))})
        if path == "/api/netaddr":
            try:
                return self.reply(200, self.netaddr.set(d.get("enable"), d.get("mac"), d.get("addr"), d.get("prefix"), d.get("ack")))
            except ValueError as e:
                return self.reply(400, {"error": str(e)})
        if path == "/api/uiaccess":
            return self.reply(200, self.uiaccess.set(d.get("block_client_wifi"), self.local_ip()))
        if path == "/api/previewaccess":
            return self.reply(200, dict(self.previewaccess.set(d.get("external")), origin=self.origin()))
        return NOT_HANDLED

    def post_chat(self, path, d):
        if path == "/api/twitch/login":
            act = d.get("action")
            key = d.get("owner") or self.headers.get("x-pb-owner", "")
            if act == "start":
                if d.get("as") in ("mod", "user"):
                    self.twitchsessions.start(key, mod=d.get("as") == "mod")
                else:
                    name = self.twitchaccounts.streamer_name()
                    if not name:
                        return self.reply(400, {"error": TwitchLogin.ERR_NO_CHANNEL})
                    claim = not (self.twitchlogin.ready() and self.twitchlogin.is_owner(key))
                    self.twitchlogin.start(mod=d.get("mod") is True, events=d.get("events") is True, everything=d.get("all") is True, owner_key=key, claim=claim, expect=name)
                return self.reply(200, self.twitchaccounts.status(key))
            if act == "cancel":
                s = self.twitchsessions.get(key)
                if s is not None and s.state == "wartet":
                    s.cancel()
                h = TwitchLogin.owner_hash(key)
                if self.twitchlogin.is_owner(key) or (h and (self.twitchlogin.pending or {}).get("owner") == h):
                    self.twitchlogin.cancel()
                return self.reply(200, self.twitchaccounts.status(key))
            if act == "mod" and isinstance(d.get("on"), bool):
                role, acct = self.twitchaccounts.pick(key)
                if role == "none":
                    return self.reply(400, {"error": "Zuerst in diesem Browser bei Twitch anmelden"})
                acct.set_mod(d["on"])
                return self.reply(200, self.twitchaccounts.status(key))
            return self.reply(400, {"error": "Ungültige Anfrage"})
        if path == "/api/twitch/logout":
            key = d.get("owner") or self.headers.get("x-pb-owner", "")
            role, acct = self.twitchaccounts.pick(key)
            if role in ("mod", "user"):
                self.twitchsessions.logout(key)
            elif role == "owner":
                self.twitchlogin.logout()
            else:
                return self.reply(400, {"error": "In diesem Browser ist niemand angemeldet"})
            return self.reply(200, self.twitchaccounts.status(key))
        if path == "/api/twitch/bot":
            act = d.get("action")
            if act == "start":
                return self.reply(200, self.twitchbot.start())
            if act == "cancel":
                return self.reply(200, self.twitchbot.cancel())
            if act == "logout":
                r = self.twitchbot.logout()
                with self.twitch.lock:
                    self.twitch.tries.clear()
                return self.reply(200, r)
            return self.reply(400, {"error": "Ungültige Anfrage"})
        if path == "/api/chat/mod":
            key = self.headers.get("x-pb-owner", "")
            role, acct = self.twitchaccounts.pick(key)
            if role == "none":
                return self.reply(400, {"error": self.ERR_NO_ACCOUNT})
            if role == "user":
                return self.reply(400, {"error": self.ERR_NOT_MOD})
            return self.reply(200, (self.chatmod if role == "owner" else self.chatmod.view(acct)).do(d, key))
        if path == "/api/chat/send":
            key = self.headers.get("x-pb-owner", "")
            role, acct = self.twitchaccounts.pick(key)
            if role == "none":
                return self.reply(400, {"error": self.ERR_NO_ACCOUNT})
            if role == "owner":
                return self.reply(200, self.chatsender.say(d.get("text"), key))
            mv = self.chatmod.view(acct)
            return self.reply(200, self.chatsender.say_as(mv, HelixChat(acct, mv), d.get("text"), key, commands=role == "mod"))
        if path == "/api/twitch":
            return self.reply(200, self.twitch.save(d))
        if path == "/api/sysalerts":
            return self.reply(200, self.sysalerts.save(d))
        if path == "/api/twitch/test":
            return self.reply(200, self.twitch.test())
        return NOT_HANDLED

    def post_cameras(self, path, d):
        if path == "/api/hdmi":
            return self.reply(200, self.hdmi.set(d))
        if path == "/api/update":
            self.updates.request(d.get("mode"), d.get("confirm") is True)
            return self.reply(200, {"ok": True})
        m = re.match(r"^/api/cameras/([0-9a-f]{8})$", path)
        if m:
            try:
                if d.get("swap_with") is not None:
                    if not isinstance(d["swap_with"], str):
                        raise ValueError("Kamera ungültig")
                    return self.reply(200, self.cams.swap(m.group(1), d["swap_with"]))
                if "move_before" in d:
                    if d["move_before"] is not None and not isinstance(d["move_before"], str):
                        raise ValueError("Kamera ungültig")
                    return self.reply(200, self.cams.move(m.group(1), d["move_before"]))
                if d.get("iface") is not None:
                    cam = next((c for c in self.cams.cams if c["id"] == m.group(1)), None)
                    if cam and cam["key"].startswith("dji-"):
                        raise ValueError("Die Verbindung einer DJI-Kamera wird in ihrer DJI-Karte gewählt")
                return self.reply(200, self.cams.update(m.group(1), d.get("name"), d.get("role"), d.get("iface")))
            except KeyError:
                return self.reply(404, {"error": "nicht gefunden"})
        if path == "/api/cameras":
            cam = self.cams.add(d.get("name"), d.get("key"), d.get("role", "extra"))
            return self.reply(200, cam)
        return NOT_HANDLED
