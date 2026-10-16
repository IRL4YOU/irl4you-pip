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

Aufruf "heal" (nur vom Zeitgeber pipbox-remote-heal.timer, ohne Eingabe): Selbstheilung nach Stromausfall. Nach einem Stromausfall (die Box hat keine Echtzeituhr) bleibt die
öffentliche Freigabe manchmal tot, obwohl Tailscale sie als eingeschaltet anzeigt, bis man sie aus- und wieder einschaltet (Meldung eines Nutzers, 16. Oktober 2026). Der Helfer
wendet die Freigabe dann selbst neu an, aber nur, wenn der Nutzer sie gewollt hat (Merkzettel /var/lib/pipbox-remote/funnel-wanted, geschrieben von funnel_on, gelöscht von
funnel_off, serve_off und logout) und nur, wenn Tailscale "Running" meldet, die Box online ist und die Uhr stimmt:
  * lief tailscaled schon vor der Zeitsynchronisation (die Uhr ist danach gesprungen), wird er einmal neu gestartet;
  * einmal je Start (die Freigabe wird vorher von außen geprüft, wenn das geht, siehe probe) und nach einer Rückkehr des Netzes nach mehr als 5 Minuten wird neu angewendet;
  * alle 10 Minuten prüft er, ob die Freigabe von außen erreichbar ist, und wendet sie sonst neu an (höchstens alle 10 Minuten und 6 Mal je Stunde).
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
INTENT_DIR = "/var/lib/pipbox-remote"                   # nur für root: die Freigabe war vom Nutzer gewollt (der Benutzer pipbox kann sie damit nicht von selbst einschalten)
INTENT = f"{INTENT_DIR}/funnel-wanted"
HEAL_STATE = f"{RUN}/heal.json"                         # Zustand der Selbstheilung (nur dieser Start)
HEAL_PROBE_EVERY_S = 600
HEAL_COOLDOWN_S = 600
HEAL_MAX_PER_HOUR = 6
HEAL_AFTER_OFFLINE_S = 300
CLOCK_JUMP_S = 120
PROBE_PATH = "/api/auth"                                # öffentlich, ohne Anmeldung (antwortet mit 200)
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
    set_intent(False)
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


def set_intent(on):
    """Merkzettel: der Nutzer will die öffentliche Freigabe (nur root lesbar und schreibbar)."""
    try:
        if on:
            os.makedirs(INTENT_DIR, mode=0o700, exist_ok=True)
            with open(INTENT, "w") as f:
                f.write("1\n")
            os.chmod(INTENT, 0o600)
        else:
            os.remove(INTENT)
    except FileNotFoundError:
        pass
    except OSError as e:
        log(f"Merkzettel der Freigabe: {e!r}")


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
    set_intent(True)
    status(state="idle", step="", hint_url="",
           message="Die Oberfläche ist öffentlich im Internet erreichbar. Die Freigabe bleibt an, auch nach einem Neustart der Box, bis sie beendet wird.")


def do_funnel_off(message="Die öffentliche Freigabe ist beendet. Die Oberfläche bleibt im privaten Tailscale-Netz erreichbar."):
    status(state="working", step="Beende die öffentliche Freigabe", message="")
    ts("funnel", "reset", timeout=30)                        # setzt die Freigaben zurück; danach nur die der Oberfläche wieder im PRIVATEN Netz
    subprocess.run(["tailscale", "serve", "--bg", "--https=443", f"http://127.0.0.1:{PORT}"], capture_output=True, text=True, timeout=40)
    if funnel_active():
        raise RuntimeError("Die öffentliche Freigabe ließ sich nicht beenden. Bitte in einer Konsole auf der Box: sudo tailscale funnel reset")
    log("Funnel aus")
    set_intent(False)
    status(state="idle", step="", hint_url="", message=message)


# ---------------------------------------------------------------- Selbstheilung (Aufruf "heal")

def heal_load():
    try:
        with open(HEAL_STATE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def heal_save(st):
    os.makedirs(RUN, exist_ok=True)
    st["time"] = int(time.time())
    tmp = HEAL_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, HEAL_STATE)


def clock_synced():
    """Hat die Box ihre Uhrzeit aus dem Netz? (Ohne Echtzeituhr steht sie nach dem Start auf dem Stand der letzten Sicherung.)"""
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def tailscale_state():
    """(BackendState, online, DNS-Name) aus 'tailscale status --json'; bei Fehlern ('', False, '')."""
    try:
        d = json.loads(ts("status", "--json", timeout=15).stdout or "{}")
    except (ValueError, subprocess.SubprocessError, OSError):
        return "", False, ""
    me = d.get("Self") or {}
    return str(d.get("BackendState") or ""), bool(me.get("Online")), str(me.get("DNSName") or "").rstrip(".")


def systemd_time(text):
    """Zeitangabe von systemctl ('Fri 2026-10-16 10:56:43 UTC', mit --timestamp=utc) in Unix-Sekunden; None, wenn nicht lesbar. ('--timestamp=unix' kennt das systemd der Box nicht.)"""
    import calendar
    try:
        return calendar.timegm(time.strptime(text.strip().replace(" UTC", ""), "%a %Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def tailscaled_clock_jumped():
    """Ist die Uhr nach dem Start von tailscaled gesprungen (er lief schon, bevor die Zeit stimmte)? systemd merkt sich den Zeitpunkt des Starts als Uhrzeit und als Zeit seit dem
    Systemstart; bei einem Sprung stimmen beide nicht mehr überein."""
    try:
        r = subprocess.run(["systemctl", "show", "tailscaled", "--timestamp=utc", "-p", "ActiveEnterTimestamp", "-p", "ActiveEnterTimestampMonotonic"],
                           capture_output=True, text=True, timeout=10, env=dict(os.environ, LC_ALL="C"))
        vals = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
        wall = systemd_time(vals.get("ActiveEnterTimestamp", ""))
        mono = int(vals.get("ActiveEnterTimestampMonotonic", "0"))
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    if wall is None or mono <= 0:
        return False
    return (time.time() - up + mono / 1e6) - wall > CLOCK_JUMP_S


def probe(dns):
    """Ist die öffentliche Freigabe von außen erreichbar? True/False, None = nicht zu prüfen. Die Box ruft ihre eigene öffentliche Adresse auf (öffentliche Anmeldeseite, ohne
    Anmeldung). Jede Antwort des Servers zählt (auch ein Fehlercode: dann hat die Freigabe den Weg gefunden). Löst der Name zu einer Adresse im Tailscale-Netz auf (100.64.0.0/10),
    ginge der Aufruf nur lokal und sagte nichts: nicht zu prüfen."""
    import ipaddress
    import socket
    import urllib.error
    if not dns:
        return None
    try:
        infos = socket.getaddrinfo(dns, 443, type=socket.SOCK_STREAM)
    except OSError:
        return None
    nets = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
    addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not addrs or all(any(a in n for n in nets if a.version == n.version) for a in addrs):
        return None
    req = urllib.request.Request(f"https://{dns}{PROBE_PATH}", headers={"User-Agent": "irl4you-box"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True
        except urllib.error.HTTPError:
            return True
        except (OSError, ValueError):
            time.sleep(3)
    return False


def reapply_funnel():
    """Dasselbe wie 'Öffentliche Freigabe beenden' und wieder einschalten, aber ohne die Meldung der Oberfläche zu ändern. Gelingt das Einschalten nicht, bleibt der Merkzettel,
    und der nächste Durchlauf versucht es wieder."""
    ts("funnel", "reset", timeout=30)
    run_capture(["tailscale", "serve", "--bg", "--https=443", f"http://127.0.0.1:{PORT}"], SERVE_TIMEOUT)
    rc, out, timed_out = run_capture(["tailscale", "funnel", "--bg", "--yes", str(PORT)], SERVE_TIMEOUT)
    ok = rc == 0 and not timed_out and funnel_active()
    if not ok:
        log("Selbstheilung: Funnel ließ sich nicht neu einschalten: " + out[-150:].replace("\n", " "))
    return ok


def heal(now=None):
    """Ein Durchlauf der Selbstheilung (der Zeitgeber ruft ihn jede Minute). Gibt kurz zurück, was er getan hat (für Tests und das Protokoll)."""
    now = time.time() if now is None else now
    st = heal_load()
    if not installed():
        return "nicht installiert"
    wanted = os.path.exists(INTENT)
    if not wanted and funnel_active():          # Freigabe aus einer Version ohne Merkzettel: gilt als gewollt
        set_intent(True)
        wanted = True
    if not wanted:
        st["wanted"] = False
        heal_save(st)
        return "Freigabe nicht gewollt"
    st["wanted"] = True
    backend, online, dns = tailscale_state()
    if backend != "Running" or not online:
        st.setdefault("offline_since", now)
        heal_save(st)
        return "Tailscale nicht verbunden"
    offline_for = now - st.pop("offline_since", now)
    if not clock_synced():
        st["note"] = "Uhr noch nicht synchron"
        heal_save(st)
        return "Uhr noch nicht synchron"
    st.pop("note", None)
    if tailscaled_clock_jumped() and not st.get("restarted"):
        st["restarted"] = int(now)
        log("Selbstheilung: Die Uhr ist nach dem Start von tailscaled gesprungen, starte ihn einmal neu")
        subprocess.run(["systemctl", "restart", "tailscaled"], capture_output=True, timeout=60)
        heal_save(st)
        return "tailscaled neu gestartet"
    reason = ""
    if not funnel_active():
        reason = "die Freigabe fehlt in der Konfiguration"
    elif not st.get("boot_done"):
        res = probe(dns)
        st["probe_t"] = now
        if res is True:
            st["boot_done"] = int(now)
            log("Selbstheilung: Freigabe von außen erreichbar, nichts zu tun")
        else:
            reason = "nach dem Start" + (" (Prüfung von außen fehlgeschlagen)" if res is False else " (von außen nicht zu prüfen)")
    elif offline_for >= HEAL_AFTER_OFFLINE_S:
        reason = f"Netz war {int(offline_for // 60)} Minuten weg"
    elif now - st.get("probe_t", 0) >= HEAL_PROBE_EVERY_S:
        st["probe_t"] = now
        if probe(dns) is False:
            reason = "Prüfung von außen fehlgeschlagen"
    if not reason:
        heal_save(st)
        return "ok"
    fixes = [t for t in st.get("fixes", []) if now - t < 3600]
    if fixes and now - fixes[-1] < HEAL_COOLDOWN_S:
        heal_save(dict(st, fixes=fixes))
        return "zu früh nach der letzten Neuanwendung"
    if len(fixes) >= HEAL_MAX_PER_HOUR:
        heal_save(dict(st, fixes=fixes))
        return "zu oft in der letzten Stunde"
    log(f"Selbstheilung: wende die öffentliche Freigabe neu an ({reason})")
    ok = reapply_funnel()
    fixes.append(int(now))
    st.update(fixes=fixes, last_fix=int(now), last_fix_ok=ok, last_reason=reason)
    if ok:
        st["boot_done"] = int(now)
    heal_save(st)
    return "neu angewendet" if ok else "Neuanwendung fehlgeschlagen"


def heal_main():
    os.makedirs(RUN, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                                # eine Anforderung der Oberfläche läuft gerade
    try:
        heal()
    except Exception as e:                      # der Zeitgeber ruft wieder
        log(f"Selbstheilung: Fehler {e!r}")
    return 0


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
    if len(sys.argv) > 1 and sys.argv[1] == "heal":
        return heal_main()
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
            set_intent(False)
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
