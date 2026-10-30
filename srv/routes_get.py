
import os
import time
import urllib.parse
try:
    import pipbox_preview
except ImportError:
    pipbox_preview = None
from srv.auth import Auth
from srv.datenschutz import neustart_noetig
from srv.httpd import PHONE_FRAME
from srv.netinfo import iface_ips
from srv.static import ASSET_RE
from srv.wifi import label_bluetooth
from srv.util import NOT_HANDLED


class GetRoutes:
    def get_public(self, path):
        if (path == "/i18n.js" or path.startswith("/i18n/")) and self.i18n_file(path):
            return
        if path in ("/", "/index.html"):
            if self.authed() and "app=1" not in self.path and self.phone_view_wanted():
                return self.send_bytes(200, PHONE_FRAME.encode("utf-8"), "text/html; charset=utf-8")
            return self.page("index.html" if self.authed() else "login.html")
        if path == "/api/auth":
            out = {"configured": self.auth.configured, "mode": self.auth.mode, "authed": self.authed()}
            if self.auth.mode == "demo":
                out["demo_password"] = Auth.DEMO_PASSWORD
            return self.reply(200, out)
        if path == "/api/swprogress":
            return self.reply(200, self.swupdate.progress_public())
        return NOT_HANDLED

    def get_status(self, path):
        if ASSET_RE.match(path):
            e = self.webfiles.entry(path[1:])
            if e is None:
                return self.reply(404, {"error": "not found"})
            fixed = self.webfiles.version and urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("v") == [self.webfiles.version]
            return self.send_file(e, "private, max-age=31536000, immutable" if fixed else "no-cache")
        if path == "/api/statsshare":
            return self.reply(200, self.stats_status())
        if path == "/api/statsshare/widget":
            return self.stats_widget()
        if path == "/api/metrics":
            return self.reply(200, self.metrics_data())
        if path == "/api/previewaccess":
            return self.reply(200, dict(self.previewaccess.status(), origin=self.origin()))
        if path == "/api/preview":
            org = self.origin()
            if not self.preview:
                return self.reply(200, {"available": False, "why": "missing", "origin": org})
            if not self.previewaccess.allowed(org):
                return self.reply(200, {"available": False, "why": "extern", "origin": org})
            if not self.send._active():
                return self.reply(200, {"available": False, "why": "off", "origin": org})
            st = self.preview.status()
            st["origin"] = org
            if org == "extern":
                st.update(ext_width=self.previewaccess.EXT_WIDTH, ext_fps=self.previewaccess.EXT_FPS)
                if self.previewaccess.status()["ext_viewers"] >= self.previewaccess.EXT_MAX:
                    st.update(available=False, why="busy")
            return self.reply(200, st)
        if path == "/api/preview/stream":
            return self.preview_stream()
        return NOT_HANDLED

    def get_admin(self, path):
        if path == "/api/controller-keys":
            return self.reply(200, self.ckeys.snapshot())
        if path == "/api/logmode":
            return self.reply(200, self.logmode.status())
        if path == "/api/layout":
            return self.reply(200, self.layout.get())
        if path == "/api/datenschutz":
            d = self.datenschutz.get()
            if "lizenz" in urllib.parse.parse_qs((self.path.split("?", 1) + [""])[1]):
                d["lizenz_text"] = self.datenschutz.lizenz_text()
            d["sending"] = self.send._active()
            d["neustart_noetig"] = bool(d["sending"] and neustart_noetig(self.pipeline.build()))
            return self.reply(200, d)
        if path == "/api/deck":
            return self.reply(200, self.deck.get())
        if path == "/api/btdriver":
            return self.reply(200, self.btdriver.status())
        if path == "/api/logs":
            return self.reply(200, self.logbundle.status())
        if path == "/api/settings/progress":
            return self.reply(200, dict(self.transfer.progress))
        if path == "/api/settings":
            return self.reply(200, {"has_backup": os.path.isfile(self.transfer.backup_path), "sending": bool(self.send._active())})
        if path == "/api/developer":
            return self.reply(200, self.developer.status())
        if path == "/api/developer/password":
            try:
                return self.reply(200, self.developer.password())
            except ValueError as e:
                return self.reply(404, {"error": str(e)})
        if path == "/api/logs/file":
            body = self.logbundle.content()
            if body is None:
                return self.reply(404, {"error": "Es liegt noch keine fertige Protokolldatei vor"})
            return self.send_bytes(200, body, "text/plain; charset=utf-8",
                                   headers={"Content-Disposition": 'attachment; filename="irl4you-protokolle.txt"'})
        if path == "/api/power":
            return self.reply(200, self.power.status())
        if path == "/api/wifi":
            return self.reply(200, self.wifi.status())
        return NOT_HANDLED

    def get_system(self, path):
        if path == "/api/wifi/hotspot":
            q = urllib.parse.parse_qs((self.path.split("?", 1) + [""])[1])
            try:
                return self.reply(200, self.wifi.hotspot_secret((q.get("iface") or [""])[0]))
            except ValueError as e:
                return self.reply(404, {"error": str(e)})
        if path == "/api/remote":
            return self.reply(200, self.remote.status())
        if path == "/api/swupdate":
            q = (self.path.split("?", 1) + [""])[1]
            return self.reply(200, self.swupdate.status(force="check=1" in q, fresh="fresh=1" in q))
        if path == "/api/swupdate/history":
            return self.reply(200, self.swupdate.history())
        if path == "/api/support":
            return self.reply(200, {"cases": self.support.list(), "version": self.swupdate.version, "issues": "https://github.com/IRL4YOU/irl4you-pip/issues",
                                    "now": int(time.time()), "delete_window": int(self.support.DELETE_WINDOW)})
        if path == "/api/update":
            try:
                self.updates.auto_check(present=True)
            except Exception as e:
                print("auto_check (Anmeldung):", e)
            return self.reply(200, self.updates.status())
        if path == "/api/dji":
            st = label_bluetooth(self.djisvc.status(), self.names)
            return self.reply(200, st)
        if path == "/api/uiaccess":
            return self.reply(200, self.uiaccess.status(self.local_ip()))
        if path == "/api/netaddr":
            return self.reply(200, self.netaddr.status())
        return NOT_HANDLED

    def get_chat(self, path):
        if path == "/api/twitch":
            return self.reply(200, self.twitch.status())
        if path == "/api/sysalerts":
            return self.reply(200, self.sysalerts.status())
        if path == "/api/twitch/login":
            return self.reply(200, self.twitchaccounts.status(self.headers.get("x-pb-owner", "")))
        if path == "/api/twitch/bot":
            return self.reply(200, self.twitchbot.status())
        if path == "/api/chat":
            qs = urllib.parse.parse_qs((self.path.split("?", 1) + [""])[1])
            try:
                since = max(0, int((qs.get("since") or ["0"])[0]))
            except ValueError:
                since = 0
            out = self.chatreader.poll(since)
            out["account"] = self.twitchaccounts.status(self.headers.get("x-pb-owner", ""))
            out["events"] = self.chatevents.status() if getattr(self, "chatevents", None) else {"state": "aus", "types": [], "error": ""}
            return self.reply(200, out)
        return NOT_HANDLED

    def get_sending(self, path):
        if path == "/api/hdmi":
            return self.reply(200, self.hdmi.status())
        if path == "/api/network":
            return self.reply(200, self.netchoice.status())
        if path == "/api/srtla":
            return self.reply(200, {**self.srtla.public(), "interfaces": iface_ips(), "conn_names": self.conn_labels()})
        if path == "/api/pipeline":
            st = self.pipeline.status(self.cams.listing(""))
            st["live"] = {"main": self.send.delay_live(), "pips": self.send.delay_live_pips()}
            return self.reply(200, st)
        if path == "/api/send":
            out = self.send.status()
            out["autostart"] = self.autostart.status()
            return self.reply(200, out)
        return NOT_HANDLED
