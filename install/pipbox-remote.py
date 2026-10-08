#!/usr/bin/env python3
"""Root-Helfer für den Fernzugriff über Tailscale (läuft nur über pipbox-remote.path).

Liest aus der Auslösedatei ausschließlich ein Stichwort aus fester Liste:
  install    Tailscale aus der offiziellen Paketquelle installieren
  login      verbinden (zeigt bei Bedarf einen Anmeldelink)
  down       Verbindung trennen (Konto bleibt verbunden)
  serve_on   Oberfläche im PRIVATEN Tailscale-Netz freigeben (HTTPS, nur Tailnet)
  serve_off  Freigabe beenden
  funnel_on  Oberfläche ÖFFENTLICH im Internet freigeben (Funnel), nur auf ausdrückliche Anforderung; bleibt an (auch nach einem
             Neustart der Box), bis "funnel_off" kommt
  funnel_off öffentliche Freigabe beenden (die Freigabe im privaten Netz bleibt)
  logout     Gerät aus dem Tailscale-Konto abmelden
Nimmt keine Adressen, Pfade oder Befehle von der Weboberfläche an. Funnel wird nie von selbst eingeschaltet.
"""
import fcntl
import json
import os
import stat
import re
import subprocess
import sys
import time
import urllib.request

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/remote-request"
RUN = "/run/pipbox-remote"
STATUS = f"{RUN}/status.json"
LOG = "/var/log/pipbox-remote.log"
LOCK = "/run/pipbox-remote.lock"
PORT = 8780
HOSTNAME = "irl4you-box"
MODES = ("install", "login", "down", "serve_on", "serve_off", "funnel_on", "funnel_off", "logout")
CODENAMES = ("jammy", "noble", "focal")
KEY_URL = "https://pkgs.tailscale.com/stable/ubuntu/{}.noarmor.gpg"
LIST_URL = "https://pkgs.tailscale.com/stable/ubuntu/{}.tailscale-keyring.list"
KEYRING = "/usr/share/keyrings/tailscale-archive-keyring.gpg"
SOURCE = "/etc/apt/sources.list.d/tailscale.list"
SERVE_TIMEOUT = 40          # Sekunden, die "tailscale serve" und "tailscale funnel" höchstens laufen dürfen
URL_RE = re.compile(r"https://(?:login\.tailscale\.com|console\.tailscale\.com)/[A-Za-z0-9_./?=&%-]{4,200}")


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 200_000:        # Protokoll klein halten (letzte ~100 KB bleiben)
            with open(LOG, "rb") as f:
                f.seek(-100_000, 2)
                f.readline()
                keep = f.read()
            with open(LOG, "wb") as f:
                f.write(keep)
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass
    print(msg, flush=True)


def status(**kw):
    os.makedirs(RUN, exist_ok=True)
    try:
        with open(STATUS) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.update(kw)
    s["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def ts(*args, timeout=60):
    return subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=timeout)


def run_capture(cmd, timeout):
    """Befehl ausführen und seine Ausgabe auch dann behalten, wenn er nach der Frist abgebrochen wird. Bei einem Tailscale-Konto, in dem "Serve" (HTTPS) noch nicht
    erlaubt ist, druckt "tailscale serve" den Freischaltlink und WARTET dann, bis man ihn bestätigt hat. Die bisherige Ausgabe steckt bei Ablauf der Frist in der
    Ausnahme (subprocess.run bricht den Befehl ab); sie wurde nur nie gelesen, es blieb "timed out after 40 seconds".
    Ergebnis: (Rückgabewert oder None, Ausgabe, Frist abgelaufen)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or ""), False
    except subprocess.TimeoutExpired as e:
        parts = [x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr)]
        return None, "".join(parts), True


def installed():
    return subprocess.run(["which", "tailscale"], capture_output=True).returncode == 0


def codename():
    for line in open("/etc/os-release"):
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def get(url, limit=100000):
    if not url.startswith("https://pkgs.tailscale.com/"):
        raise RuntimeError("Unerlaubte Quelle")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "irl4you-box"}), timeout=30) as r:
        return r.read(limit + 1)[:limit]


def do_install():
    if installed():
        status(state="idle", message="Tailscale ist schon installiert.")
        return
    cn = codename()
    if cn not in CODENAMES:
        raise RuntimeError(f"Dieses System ({cn or '?'}) wird nicht unterstützt (Ubuntu 22.04/24.04).")
    status(state="working", step="Lade die Paketquelle von tailscale.com", message="")
    key = get(KEY_URL.format(cn), 20000)
    lst = get(LIST_URL.format(cn), 2000).decode()
    if "pkgs.tailscale.com/stable/ubuntu" not in lst or "deb " not in lst:
        raise RuntimeError("Die Quellenliste von tailscale.com sieht unerwartet aus")
    with open(KEYRING, "wb") as f:
        f.write(key)
    os.chmod(KEYRING, 0o644)
    with open(SOURCE, "w") as f:
        f.write(lst)
    os.chmod(SOURCE, 0o644)
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C")
    status(step="Aktualisiere die Paketliste (nur Tailscale)")
    subprocess.run(["apt-get", "update", "-o", "Dir::Etc::sourcelist=sources.list.d/tailscale.list",
                    "-o", "Dir::Etc::sourceparts=-", "-o", "APT::Get::List-Cleanup=0"], env=env, capture_output=True, timeout=300)
    status(step="Installiere Tailscale")
    r = subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "-o", "DPkg::Lock::Timeout=120", "tailscale"],
                       env=env, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not installed():
        raise RuntimeError("Die Installation von Tailscale ist fehlgeschlagen: " + (r.stdout + r.stderr)[-150:].replace("\n", " "))
    subprocess.run(["systemctl", "enable", "--now", "tailscaled"], capture_output=True)
    log("Tailscale installiert")
    status(state="idle", step="", message="Tailscale ist installiert. Als Nächstes verbinden.")


def auth_url():
    for _ in range(10):
        try:
            d = json.loads(ts("status", "--json", timeout=15).stdout or "{}")
        except (ValueError, subprocess.TimeoutExpired):
            d = {}
        if d.get("AuthURL"):
            return d["AuthURL"]
        if d.get("BackendState") == "Running":
            return ""
        time.sleep(1)
    return ""


def do_login():
    status(state="working", step="Verbinde mit Tailscale", message="", login_url="")
    r = subprocess.run(["tailscale", "up", f"--hostname={HOSTNAME}", "--accept-dns=false", "--accept-routes=false",
                        "--reset", "--timeout=12s"], capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    m = URL_RE.search(out)
    url = m.group(0) if m else auth_url()
    if url:
        log("Anmeldung nötig")
        status(state="needs_login", step="", login_url=url,
               message="Öffne den Link, melde dich mit deinem Tailscale-Konto an und klicke „Connect“.")
    else:
        status(state="idle", step="", login_url="", message="Verbunden.")


def do_serve_on():
    status(state="working", step="Gebe die Oberfläche im privaten Netz frei", message="", hint_url="")
    rc, out, timed_out = run_capture(["tailscale", "serve", "--bg", "--https=443", f"http://127.0.0.1:{PORT}"], SERVE_TIMEOUT)
    low = out.lower()
    if "not enabled on your tailnet" in low or ("enable" in low and "visit" in low):
        m = URL_RE.search(out)
        status(state="needs_serve", step="", hint_url=m.group(0) if m else "",
               message="Tailscale muss die Funktion „Serve“ (HTTPS) für dein Netz einmal freischalten. Öffne den Link, lasse "
                       "„Funnel“ AUS und klicke „Enable HTTPS“. Danach erneut freigeben.")
        return
    if timed_out:
        raise RuntimeError(f"Tailscale hat nicht innerhalb von {SERVE_TIMEOUT} Sekunden geantwortet. Ist die Box mit Tailscale verbunden (Status „Verbunden“)? "
                           "Sonst zuerst „Verbinden“ drücken und dann erneut freigeben." + (" Ausgabe: " + out[-100:].replace("\n", " ") if out.strip() else ""))
    if rc != 0:
        raise RuntimeError("Freigabe fehlgeschlagen: " + out[-150:].replace("\n", " "))
    status(state="idle", step="", hint_url="", message="Die Oberfläche ist im privaten Tailscale-Netz erreichbar.")


def do_serve_off():
    ts("serve", "reset", timeout=30)
    status(state="idle", step="", hint_url="", message="Freigabe beendet.")


def serve_config():
    try:
        r = ts("serve", "status", "--json", timeout=15)
        return json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return {}


def funnel_active(cfg=None):
    """Ist Funnel für die Oberfläche (Ziel 127.0.0.1:PORT) eingeschaltet? Fremde Funnel-Freigaben für andere Ziele zählen nicht."""
    cfg = serve_config() if cfg is None else cfg
    web = cfg.get("Web") or {}
    for hostport, on in (cfg.get("AllowFunnel") or {}).items():
        if not on:
            continue
        for h in ((web.get(hostport) or {}).get("Handlers") or {}).values():
            if f"127.0.0.1:{PORT}" in str(h.get("Proxy", "")) or f"localhost:{PORT}" in str(h.get("Proxy", "")):
                return True
    return False


def do_funnel_on():
    status(state="working", step="Gebe die Oberfläche öffentlich im Internet frei (Funnel)", message="", hint_url="")
    rc, out, timed_out = run_capture(["tailscale", "funnel", "--bg", "--yes", str(PORT)], SERVE_TIMEOUT)     # gleiche Falle wie bei "serve": Link drucken, dann warten
    if rc != 0 or timed_out or not funnel_active():
        low = out.lower()
        if "funnel" in low and ("not enabled" in low or "not available" in low or "enable" in low or "policy" in low or "visit" in low):
            m = URL_RE.search(out)
            status(state="needs_funnel", step="", hint_url=m.group(0) if m else "",
                   message="Tailscale muss „Funnel“ (und HTTPS) für dein Netz einmal erlauben. Öffne den Link, erlaube es und versuche es dann erneut.")
            return
        raise RuntimeError("Funnel fehlgeschlagen: " + out[-150:].replace("\n", " "))
    log("Funnel an (ohne Zeitgrenze)")
    status(state="idle", step="", hint_url="",
           message="Die Oberfläche ist öffentlich im Internet erreichbar. Die Freigabe bleibt an, auch nach einem Neustart der Box, bis sie beendet wird.")


def do_funnel_off(message="Die öffentliche Freigabe ist beendet. Die Oberfläche bleibt im privaten Tailscale-Netz erreichbar."):
    status(state="working", step="Beende die öffentliche Freigabe", message="")
    ts("funnel", "reset", timeout=30)                        # setzt die Freigaben zurück; danach nur die der Oberfläche wieder im PRIVATEN Netz
    subprocess.run(["tailscale", "serve", "--bg", "--https=443", f"http://127.0.0.1:{PORT}"], capture_output=True, text=True, timeout=40)
    if funnel_active():
        raise RuntimeError("Die öffentliche Freigabe ließ sich nicht beenden. Bitte in einer Konsole auf der Box: sudo tailscale funnel reset")
    log("Funnel aus")
    status(state="idle", step="", hint_url="", message=message)


def read_req(path, limit=4096):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "guard":
        return 0              # den Wächter-Zeitgeber früherer Versionen gibt es nicht mehr (die Freigabe hat keine Zeitgrenze)
    os.makedirs(RUN, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    try:
        mode = read_req(REQ).strip().splitlines()[0]
    except (OSError, IndexError):
        mode = ""
    try:
        os.remove(REQ)
    except OSError:
        pass
    if mode not in MODES:
        log("Unbekannte Anforderung verworfen.")
        return 0
    try:
        if mode == "install":
            do_install()
        elif not installed():
            raise RuntimeError("Tailscale ist noch nicht installiert.")
        elif mode == "login":
            do_login()
        elif mode == "down":
            ts("down", timeout=30)
            status(state="idle", step="", message="Verbindung getrennt.")
        elif mode == "serve_on":
            do_serve_on()
        elif mode == "serve_off":
            do_serve_off()
        elif mode == "funnel_on":
            do_funnel_on()
        elif mode == "funnel_off":
            do_funnel_off()
        elif mode == "logout":
            ts("serve", "reset", timeout=30)
            ts("logout", timeout=40)
            status(state="idle", step="", login_url="", message="Vom Tailscale-Konto abgemeldet.")
        log(f"{mode}: fertig")
    except Exception as e:                                  # nie ohne Meldung enden
        log(f"{mode}: Fehler {e!r}")
        status(state="failed", step="", message=str(e)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
