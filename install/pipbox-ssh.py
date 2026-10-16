#!/usr/bin/env python3
"""Root-Helfer: SSH-Dienst ein- oder ausschalten und das SSH-Passwort erzeugen (läuft nur über pipbox-ssh.path).

Liest aus der Auslösedatei ein Stichwort aus fester Liste, ohne Verweisen zu folgen, löscht die Datei und führt es aus:
  start   `systemctl start ssh`. Gibt es für den SSH-Benutzer der BELABOX (setup.json der Original-Oberfläche) noch kein erzeugtes Passwort,
          wird vorher eines erzeugt (wie in der Original-Oberfläche), damit SSH nie mit dem Auslieferungspasswort aufgeht.
  stop    `systemctl stop ssh`.
  reset   neues zufälliges Passwort (20 Zeichen) für den SSH-Benutzer: gesetzt mit chpasswd (über stdin, nie als Befehlsargument), gemerkt in
          /var/lib/pipbox/ssh-pass.json (Benutzer pipbox, 0600) samt der Passwort-Zeile aus /etc/shadow, damit sich später feststellen lässt, ob jemand
          es von Hand geändert hat.
  check   ändert nichts; stellt nur fest, ob das SSH-Passwort noch das erzeugte ist (nur das Ergebnis "generated", "own" oder "unknown" wird weitergegeben).
Sonst ändert der Helfer nichts: keine Schlüssel, keine Konfiguration von SSH, und auch das Starten beim Hochfahren bleibt, wie es ist (nach einem
Neustart gilt wieder die Einstellung des Systems)."""
import json
import os
import re
import secrets
import stat
import string
import subprocess
import sys
import time
import pwd

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/ssh-request"
RUN = "/run/pipbox-ssh"
STATUS = f"{RUN}/status.json"
SERVICE = "ssh"
ACTIONS = ("start", "stop", "check", "reset")
PASS_FILE = f"{STATE}/ssh-pass.json"
PASS_LEN = 20
ALPHABET = string.ascii_letters + string.digits
BELA_DIR = "/opt/belaUI"
SHADOW = "/etc/shadow"
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


def read_req(path, limit=32):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def write_status(**kw):
    os.makedirs(RUN, exist_ok=True)
    kw["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def read_json(path, limit=65536):
    try:
        with open(path) as f:
            d = json.loads(f.read(limit))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def ssh_user():
    """Name des SSH-Benutzers laut Original-Oberfläche (setup.json), sonst None."""
    user = (read_json(f"{BELA_DIR}/setup.json") or {}).get("ssh_user")
    return user if isinstance(user, str) and USER_RE.fullmatch(user) else None


def shadow_line(user):
    try:
        with open(SHADOW) as f:
            return next((l.rstrip("\n") for l in f if l.startswith(user + ":")), None)
    except OSError:
        return None


def load_ours(user):
    """Das von IRL4YOU BOX erzeugte Passwort dieses Benutzers (ssh-pass.json) oder None."""
    d = read_json(PASS_FILE)
    if d and d.get("user") == user and isinstance(d.get("password"), str) and d["password"] and isinstance(d.get("hash"), str):
        return d
    return None


def generated_password_exists(user):
    """Gibt es ein erzeugtes Passwort: von IRL4YOU BOX (ssh-pass.json) oder von der Original-Oberfläche (ssh_pass in deren config.json)?"""
    return bool(load_ours(user) or (read_json(f"{BELA_DIR}/config.json") or {}).get("ssh_pass"))


def password_state():
    """"generated": Das Passwort ist (noch) das erzeugte, "own": jemand hat es danach selbst geändert, "unknown": nicht feststellbar. Verglichen wird
    die Zeile des Benutzers in /etc/shadow mit der gemerkten: bei einem Passwort von IRL4YOU BOX mit der in ssh-pass.json, sonst wie in der
    Original-Oberfläche mit der in deren config.json."""
    user = ssh_user()
    if not user:
        return "unknown"
    line = shadow_line(user)
    if line is None:
        return "unknown"
    ours = load_ours(user)
    if ours:
        return "generated" if line == ours["hash"] else "own"
    cfg = read_json(f"{BELA_DIR}/config.json") or {}
    if not cfg.get("ssh_pass") or not cfg.get("ssh_pass_hash"):
        return "unknown"
    return "generated" if line == str(cfg["ssh_pass_hash"]).rstrip("\n") else "own"


def store_ours(user, password, line):
    """ssh-pass.json atomar schreiben, Besitzer pipbox, 0600. Der Ordner gehört dem Benutzer pipbox: nie einem untergeschobenen Verweis folgen
    (Temporärdatei vorher entfernen, mit O_EXCL | O_NOFOLLOW neu anlegen, Besitzer über den Dateideskriptor setzen)."""
    tmp = PASS_FILE + ".tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            u = pwd.getpwnam("pipbox")
            os.fchown(fd, u.pw_uid, u.pw_gid)
        except (KeyError, PermissionError):
            pass
        with os.fdopen(fd, "w") as f:
            json.dump({"user": user, "password": password, "hash": line, "time": int(time.time())}, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, PASS_FILE)


def reset_password():
    """Neues zufälliges Passwort für den SSH-Benutzer setzen und merken. Das Passwort steht nirgends im Protokoll oder in der Statusmeldung."""
    user = ssh_user()
    if not user:
        raise RuntimeError("Für diese Box ist kein SSH-Benutzer eingerichtet")
    password = "".join(secrets.choice(ALPHABET) for _ in range(PASS_LEN))
    try:
        r = subprocess.run(["chpasswd"], input=f"{user}:{password}\n", capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        raise RuntimeError("Das Programm chpasswd fehlt, das Passwort konnte nicht erzeugt werden")
    if r.returncode != 0:
        raise RuntimeError("Das Passwort konnte nicht gesetzt werden")
    line = shadow_line(user)
    if line is None:
        raise RuntimeError("Das Passwort wurde gesetzt, ist aber nicht prüfbar")
    store_ours(user, password, line)


def systemctl(*args, timeout=30):
    return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=timeout)


def apply(action):
    if action not in ACTIONS:
        raise ValueError("Unbekannte Aktion")
    if action == "check":
        return "Passwort geprüft"
    if action == "reset":
        reset_password()
        return "SSH-Passwort neu erzeugt"
    if systemctl("cat", SERVICE + ".service").returncode != 0:
        raise RuntimeError("Der SSH-Dienst ist auf dieser Box nicht installiert")
    if action == "start":
        user = ssh_user()
        if user and not generated_password_exists(user):          # wie in der Original-Oberfläche: nie mit dem Auslieferungspasswort einschalten
            try:
                reset_password()
            except RuntimeError as e:
                raise RuntimeError("SSH nicht eingeschaltet: " + str(e))
    r = systemctl(action, SERVICE)
    if r.returncode != 0:
        raise RuntimeError("systemctl %s %s fehlgeschlagen: %s" % (action, SERVICE, (r.stderr or r.stdout).strip()[:160] or "unbekannter Fehler"))
    active = systemctl("is-active", SERVICE).stdout.strip() == "active"
    if (action == "start") != active:
        raise RuntimeError("Der SSH-Dienst ist danach nicht %s" % ("gestartet" if action == "start" else "beendet"))
    return "SSH eingeschaltet" if active else "SSH ausgeschaltet"


def main():
    try:
        action = read_req(REQ).strip()
    except (OSError, ValueError):
        action = ""
    try:
        os.remove(REQ)
    except OSError:
        pass
    if action not in ACTIONS:
        write_status(state="error", message="Ungültige Anfrage")
        return 1
    write_status(state="working", message="Wird ausgeführt …", action=action)
    try:
        write_status(state="done", message=apply(action), action=action, password_state=password_state())
    except (ValueError, RuntimeError) as e:
        write_status(state="error", message=str(e), action=action, password_state=password_state())
        return 1
    except (OSError, subprocess.TimeoutExpired) as e:
        write_status(state="error", message="Fehler: " + type(e).__name__, action=action)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
