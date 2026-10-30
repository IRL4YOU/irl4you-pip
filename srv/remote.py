
import json
import os
import shutil
import subprocess
import time

from srv.util import _TTL


class Remote:
    STATUS = "/run/pipbox-remote/status.json"
    ACTIONS = ("install", "login", "down", "serve_on", "serve_off", "funnel_on", "funnel_off", "stats_on", "stats_off", "logout")

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "remote-request")
        self.demo = demo
        self.fake = {}

    def _ts(self, *args):
        try:
            sig = os.stat(self.STATUS).st_mtime_ns
        except OSError:
            sig = None
        key = ("ts", args)
        hit = _TTL.get(key)
        if hit is not None and hit[2] == sig and time.monotonic() - hit[0] < 20:
            return hit[1]
        try:
            r = subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=6)
            val = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            val = {}
        _TTL[key] = (time.monotonic(), val, sig)
        return val

    def status(self):
        if self.demo:
            d = {"installed": True, "backend": "Running", "connected": True, "name": "irl4you-box.demo.ts.net",
                 "ip": "100.64.0.1", "tailnet": "demo",
                 "serve": bool(self.fake.get("serve")), "url": "https://irl4you-box.demo.ts.net/" if self.fake.get("serve") else "",
                 "funnel": bool(self.fake.get("funnel")), "stats": bool(self.fake.get("stats")), "stats_base": "https://irl4you-box.demo.ts.net:8443" if self.fake.get("stats") else "",
                 "state": "idle", "step": "", "message": self.fake.get("message", ""), "login_url": "",
                 "hint_url": "", "helper_installed": True}
            return d
        installed = bool(shutil.which("tailscale"))
        out = {"installed": installed, "backend": "", "connected": False, "name": "", "ip": "", "tailnet": "",
               "serve": False, "url": "", "funnel": False, "stats": False, "stats_base": "", "state": "idle", "step": "", "message": "", "login_url": "",
               "hint_url": "", "helper_installed": os.path.exists("/etc/systemd/system/pipbox-remote.path")}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        out.update(state=h.get("state", "idle"), step=h.get("step", ""), message=h.get("message", ""),
                   login_url=h.get("login_url", ""), hint_url=h.get("hint_url", ""))
        if h.get("time") and out["state"] not in ("working",) and time.time() - h["time"] > 6 * 3600:
            out["message"] = ""
        if not installed:
            return out
        d = self._ts("status", "--json")
        out["backend"] = d.get("BackendState", "")
        out["connected"] = out["backend"] == "Running"
        me = d.get("Self") or {}
        out["name"] = str(me.get("DNSName", "")).rstrip(".")
        ips = d.get("TailscaleIPs") or []
        out["ip"] = next((i for i in ips if ":" not in i), "")
        out["tailnet"] = (d.get("CurrentTailnet") or {}).get("Name", "")
        if d.get("AuthURL"):
            out["login_url"] = d["AuthURL"]
        if out["connected"]:
            out["login_url"] = ""
            sv = self._ts("serve", "status", "--json")
            web = sv.get("Web") or {}
            allow = sv.get("AllowFunnel") or {}
            for host, cfg in web.items():
                for h2 in (cfg.get("Handlers") or {}).values():
                    px = str(h2.get("Proxy", ""))
                    if "127.0.0.1:%d" % 8780 in px:
                        out["serve"], out["url"] = True, "https://" + host.replace(":443", "") + "/"
                        if allow.get(host):
                            out["funnel"] = True
                    if "127.0.0.1:%d" % 8781 in px and allow.get(host):
                        out["stats"], out["stats_base"] = True, "https://" + host
        return out

    def request(self, action, confirm, public=False):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        if action == "funnel_on" and public is not True:
            raise ValueError("Die öffentliche Freigabe braucht eine ausdrückliche Bestätigung")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Fernzugriff-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working":
            raise ValueError("Es läuft schon eine Aktion")
        if action == "install" and st["installed"]:
            raise ValueError("Tailscale ist schon installiert")
        if action != "install" and not st["installed"]:
            raise ValueError("Tailscale ist noch nicht installiert")
        if action == "funnel_on" and not st["connected"]:
            raise ValueError("Zuerst mit Tailscale verbinden")
        if action == "funnel_off" and not st["funnel"]:
            raise ValueError("Die öffentliche Freigabe ist nicht an")
        if action == "stats_on" and (public is not True or not st["connected"]):
            raise ValueError("Zuerst mit Tailscale verbinden" if public is True else "Die öffentliche Freigabe braucht eine ausdrückliche Bestätigung")
        if action == "stats_off" and not st["stats"]:
            raise ValueError("Die öffentliche Statistik ist nicht an")
        if self.demo:
            if action == "serve_on":
                self.fake = {"serve": True, "message": "Demo: freigegeben."}
            elif action == "serve_off":
                self.fake = {"serve": False, "message": "Demo: beendet."}
            elif action == "funnel_on":
                self.fake = {"serve": True, "funnel": True, "message": "Demo: öffentlich freigegeben."}
            elif action == "funnel_off":
                self.fake = {"serve": True, "funnel": False, "message": "Demo: öffentliche Freigabe beendet."}
            elif action in ("stats_on", "stats_off"):
                self.fake = dict(self.fake, stats=action == "stats_on", message="Demo: Statistik " + ("öffentlich." if action == "stats_on" else "nicht mehr öffentlich."))
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")
