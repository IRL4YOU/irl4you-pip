"""Der eingebaute Twitch-Chat der Box (Lesen, Schreiben, Moderation, Anmeldung mit Code, Ereignisse, Akku- und Systemwarnungen im Chat).

Eigene Datei, damit server.py kleiner bleibt; server.py importiert die Klassen von hier und verdrahtet sie (siehe `from twitch import ...`). Die Oberfläche dazu steht in web/chat.js.
Nichts hier greift auf server.py zurück (kein Rundum-Import): die wenigen Hilfen stehen unten."""
import base64
import collections
import hashlib
import json
import math
import os
import random
import re
import socket
import ssl
import struct
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")                 # Stream-Schlüssel einer Kamera (wie in server.py)


def read(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def belacoder_running():
    try:
        for d in os.listdir("/proc"):
            if d.isdigit() and (read(f"/proc/{d}/comm", "") or "").strip() == "belacoder":
                return True
    except OSError:
        pass
    return False


class TwitchStore:
    """Einstellungen der Akku-Warnung im Twitch-Chat: <state>/twitch.json (Rechte 0600, atomar geschrieben).

    Zwei Angaben: der Kanal (das Twitch-Konto, auf dem gestreamt wird; der Bot tritt ihm bei) und das Bot-Konto (Name und OAuth-Token des Kontos,
    das die Nachricht schreibt: ein zweites Konto oder dasselbe wie der Kanal). Name und Kanal werden klein geschrieben gespeichert (so kennt sie IRC),
    der Token ohne das Vorsatzwort "oauth:". Der Token bleibt auf der Box: Er steht nie in einer Antwort der Oberfläche (public() kennt nur token_set),
    in Meldungen oder Protokollen und nicht in der Einstellungssicherung (SettingsTransfer). Alles wird streng geprüft, denn die Werte landen in
    IRC-Zeilen: keine Steuerzeichen, kein CR/LF. Intern merkt sich die Datei auch, für welche Kamera schon gewarnt wurde ("warned": Schlüssel -> Zeitstempel)."""
    NAME_RE = re.compile(r"[A-Za-z0-9_]{3,25}")
    TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,100}")
    DEFAULT_MESSAGE = "Akkustand niedrig, bitte Akku wechseln: {Kamera} ({Prozent} %)"
    DEFAULTS = {"enabled": False, "login": "", "token": "", "channel": "", "threshold": 10, "message": DEFAULT_MESSAGE, "only_live": True, "via_account": False}

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = dict(self.DEFAULTS)
        self.warned = {}
        self.account = None                      # TwitchLogin (Anmeldung per Geräte-Code), wenn vorhanden
        self.bot = None                          # TwitchBotLogin: zweites Konto, das nur die Akku-Meldung schreibt (Anmeldung per Geräte-Code), wenn vorhanden
        try:
            with open(path) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            self._adopt(saved)

    # ---- Prüfung der einzelnen Angaben (leer ist bei Name, Kanal und Token erlaubt: dann fehlt die Angabe)
    @classmethod
    def _name(cls, v, error):
        if not isinstance(v, str):
            raise ValueError(error)
        v = v.strip()
        if v and not cls.NAME_RE.fullmatch(v):
            raise ValueError(error)
        return v.lower()

    @classmethod
    def _token(cls, v):
        if not isinstance(v, str):
            raise ValueError("Token: 20 bis 100 Zeichen, nur Buchstaben, Ziffern, _ und -")
        v = v.strip()
        if not v:
            return ""
        if v[:6].lower() == "oauth:":
            v = v[6:]
        if not cls.TOKEN_RE.fullmatch(v):
            raise ValueError("Token: 20 bis 100 Zeichen, nur Buchstaben, Ziffern, _ und -")
        return v

    @staticmethod
    def _threshold(v):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 50:
            raise ValueError("Warnen bei: Zahl von 1 bis 50")
        return v

    @staticmethod
    def _message(v):
        if not isinstance(v, str) or not 1 <= len(v.strip()) <= 300:
            raise ValueError("Nachricht: 1 bis 300 Zeichen")
        v = v.strip()
        if any(not ch.isprintable() for ch in v):
            raise ValueError("Nachricht: keine Sonderzeichen oder Zeilenumbrüche")
        if v[0] in "/.":
            raise ValueError("Nachricht: darf nicht mit / oder . beginnen")      # "/…" und ".…" wären Chat-Befehle
        return v

    ERR_CHANNEL = "Kanal: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _"
    ERR_LOGIN = "Bot-Konto: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _"
    ERR_MISSING = "Bitte Kanal, Bot-Konto und Token eintragen"

    def _adopt(self, saved):
        """Gespeicherte Werte übernehmen; was nicht (mehr) zur Prüfung passt, bleibt beim Standard (die Datei kann von Hand verändert sein)."""
        for key, check in (("login", lambda v: self._name(v, self.ERR_LOGIN)), ("channel", lambda v: self._name(v, self.ERR_CHANNEL)), ("token", self._token),
                           ("threshold", self._threshold), ("message", self._message)):
            if key in saved:
                try:
                    self.data[key] = check(saved[key])
                except ValueError:
                    pass
        if isinstance(saved.get("only_live"), bool):
            self.data["only_live"] = saved["only_live"]
        d = self.data
        d["via_account"] = saved.get("via_account") is True        # eingeschaltet mit dem angemeldeten Twitch-Konto statt Bot-Konto und Token von Hand
        d["enabled"] = saved.get("enabled") is True and (bool(d["channel"] and d["login"] and d["token"]) or d["via_account"])
        w = saved.get("warned")
        if isinstance(w, dict):
            self.warned = {k: float(t) for k, t in w.items() if isinstance(k, str) and KEY_RE.match(k) and isinstance(t, (int, float)) and not isinstance(t, bool)
                           and math.isfinite(t) and t > 0}

    def _write(self, data, warned):
        """Atomar schreiben; die Rechte 0600 gelten schon, bevor der Token in die Datei kommt."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(dict(data, warned=warned), f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def set(self, req):
        """Einstellungen übernehmen. Fehlende Felder behalten ihren Wert, ein leeres Token-Feld behält den gespeicherten Token. Wirft ValueError (kurze Meldung)."""
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            new = dict(self.data)
            for key in ("enabled", "only_live"):
                if key in req:
                    if not isinstance(req[key], bool):
                        raise ValueError("ja oder nein")
                    new[key] = req[key]
            if "channel" in req:
                new["channel"] = self._name(req["channel"], self.ERR_CHANNEL)
            if "login" in req:
                new["login"] = self._name(req["login"], self.ERR_LOGIN)
            if "token" in req:
                new["token"] = self._token(req["token"]) or new["token"]
            if "threshold" in req:
                new["threshold"] = self._threshold(req["threshold"])
            if "message" in req:
                new["message"] = self._message(req["message"])
            acc = self.account.login() if self.account else ""
            bot = self.bot.login() if self.bot else ""
            manual = bool(new["channel"] and new["login"] and new["token"])
            if new["enabled"] and not (manual or acc or (bot and new["channel"])):
                raise ValueError(self.ERR_MISSING)
            new["via_account"] = bool(new["enabled"] and not manual)
            try:
                self._write(new, self.warned)
            except OSError:
                raise RuntimeError("Die Einstellungen konnten nicht gespeichert werden")
            self.data = new

    def public(self):
        """Was die Oberfläche sieht: alles außer dem Token (nur, ob einer gespeichert ist)."""
        with self.lock:
            d = self.data
            acc = self.account.login() if self.account else ""
            bot = self.bot.login() if self.bot else ""
            return {"enabled": d["enabled"], "channel": d["channel"] or acc, "login": d["login"], "token_set": bool(d["token"]), "threshold": d["threshold"],
                    "message": d["message"], "only_live": d["only_live"], "account": acc, "bot": bot}

    def settings(self):
        """Alle Einstellungen samt Token, nur für den Chat-Client und den Hintergrunddienst. Nie nach außen geben."""
        with self.lock:
            d = dict(self.data)
        acc = self.account.login() if self.account else ""
        if acc:                                                    # angemeldetes Twitch-Konto hat Vorrang vor dem von Hand eingetragenen Token
            tok = self.account.token()
            d["login"], d["token"] = acc, tok                      # ist der Zugang gerade nicht verfügbar (wird erneuert), bleibt er leer: nie still als Bot-Konto schreiben
            d["channel"] = d["channel"] or acc
        return d

    def channel_name(self):
        """Der Kanal des Chats: der eingetragene, sonst der des angemeldeten Hauptkontos (wer sich per Geräte-Code anmeldet, trägt nichts ein). Ohne Netzzugriff."""
        with self.lock:
            ch = self.data["channel"]
        return ch or (self.account.login() if self.account else "")

    def notify_settings(self):
        """Die Angaben für die Akku-Meldung: wie settings(), aber ein angemeldetes Bot-Konto schreibt statt des Hauptkontos. Nur der Hintergrunddienst nimmt diese;
        der Chat (Lesen, Senden, Moderation) bleibt beim Hauptkonto. Ist das Bot-Konto angemeldet, sein Zugang aber gerade nicht verfügbar (wird erneuert),
        bleibt der Token leer: nie still mit dem Hauptkonto schreiben."""
        d = self.settings()
        bot = self.bot.login() if self.bot else ""
        if bot:
            d["login"], d["token"] = bot, self.bot.token()
        elif self.bot and self.bot.state == "abgelaufen":
            d["token"] = ""                                         # Anmeldung des Bots abgelaufen: schweigen, bis neu angemeldet oder abgemeldet wird
        return d

    # ---- für welche Kamera schon gewarnt wurde
    def warned_keys(self):
        with self.lock:
            return list(self.warned)

    def is_warned(self, key):
        with self.lock:
            return key in self.warned

    def _flush(self):
        try:
            self._write(self.data, self.warned)
        except OSError:
            pass                       # der Speicher gilt bis zum Neustart des Dienstes; beim nächsten erfolgreichen Schreiben ist es nachgeholt

    def mark_warned(self, key, when):
        with self.lock:
            self.warned[key] = float(when)
            self._flush()

    def unwarn(self, key):
        with self.lock:
            if self.warned.pop(key, None) is not None:
                self._flush()


class _IrcLines:
    """Zeilen aus einem Socket lesen, mit Frist."""
    MAX = 16384

    def __init__(self, sock, clock):
        self.sock, self.clock, self.buf = sock, clock, b""

    def get(self, until):
        """Nächste Zeile ohne Zeilenende; None, wenn die Frist abläuft; EOFError, wenn die Gegenseite schließt (oder keine Zeile zu sehen ist: kein IRC)."""
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line, self.buf = self.buf[:i], self.buf[i + 1:]
                return line.rstrip(b"\r").decode("utf-8", "replace")
            if len(self.buf) > self.MAX:
                raise EOFError
            left = until - self.clock()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise EOFError
            self.buf += chunk


class TwitchChat:
    """Schreibt eine Nachricht in den Twitch-Chat (IRC über TLS, nur Standardbibliothek). Nur Schreiben: Befehle aus dem Chat werden nicht gelesen.

    Ablauf je Nachricht (eine eigene Verbindung): verbinden (höchstens 10 s) -> PASS und NICK -> auf die Begrüßung (001) oder eine Absage warten -> die Fähigkeit
    "twitch.tv/commands" anfordern (nur dann meldet Twitch eine Ablehnung als NOTICE) -> JOIN -> PRIVMSG -> bis zu 2 s auf NOTICE-Antworten warten -> QUIT.
    PING wird mit PONG beantwortet. Alles zusammen dauert höchstens 15 s (die Namensauflösung zählt nicht mit). Host, Port und TLS sind für Tests einstellbar.
    Die PASS-Zeile mit dem Token wird nirgends ausgegeben. Text, den Twitch schickt, wird vor dem Anzeigen gekürzt und von Steuerzeichen und dem Token befreit.
    Twitch bestätigt eine angenommene Nachricht nicht: Kommt innerhalb der Wartezeit kein NOTICE, gilt sie als gesendet."""
    HOST, PORT = "irc.chat.twitch.tv", 6697
    MAX_BYTES = 450                  # Twitch erlaubt 500 Zeichen, eine IRC-Zeile höchstens 512 Byte
    OK = "Gesendet"
    NO_CONNECT = "Keine Verbindung zu Twitch"
    NO_TLS = "Sichere Verbindung zu Twitch nicht möglich (Zertifikat oder Uhrzeit der Box prüfen)"
    LOGIN_FAILED = "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)"
    NO_ANSWER = "Keine Antwort von Twitch (Zeitüberschreitung)"
    CLOSED = "Twitch hat die Verbindung beendet"
    NOT_ALLOWED = "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower)"
    LOGIN_NOTICE_RE = re.compile(r"authentication failed|login unsuccessful|improperly formatted", re.I)      # so meldet Twitch eine Absage vor der Begrüßung

    def __init__(self, host=None, port=None, tls=True, context=None, connect_timeout=10.0, total=15.0, notice_wait=2.0, clock=time.monotonic, paths=None):
        self.paths = paths or ChatPaths()
        self.host, self.port, self.tls, self.context = host or self.HOST, port or self.PORT, tls, context
        self.connect_timeout, self.total, self.notice_wait, self.clock = connect_timeout, total, notice_wait, clock

    @staticmethod
    def clean_text(text):
        """Der Text für PRIVMSG oder None: nur druckbare Zeichen (kein CR, LF, NUL), nicht mit / oder . am Anfang, höchstens MAX_BYTES Byte."""
        t = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text).strip()                  # geschütztes Leerzeichen und Ähnliches -> Leerzeichen
        bad = ("Cc", "Zl", "Zp", "Cs", "Co", "Cn")
        if not t or any(unicodedata.category(ch) in bad or ch in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for ch in t) or t[0] in "/.":
            return None
        raw = t.encode("utf-8")
        return t if len(raw) <= TwitchChat.MAX_BYTES else raw[:TwitchChat.MAX_BYTES].decode("utf-8", "ignore").rstrip()

    @staticmethod
    def _parse(line):
        """(Befehl, Rest) einer Zeile vom Server; Tags (@…) und Absender (:…) davor werden übersprungen."""
        if line.startswith("@"):
            line = line.partition(" ")[2]
        if line.startswith(":"):
            line = line.partition(" ")[2]
        cmd, _, rest = line.partition(" ")
        return cmd.upper(), rest

    @staticmethod
    def _notice_text(rest):
        return rest[1:] if rest.startswith(":") else rest.partition(" :")[2]

    @staticmethod
    def _scrub(text, token):
        """Text von Twitch zum Anzeigen: ohne den Token, ohne Steuerzeichen, höchstens 120 Zeichen."""
        text = text.replace(token, "…")
        text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
        return text[:120] + ("…" if len(text) > 120 else "")

    @staticmethod
    def _pong(sock, rest):
        arg = "".join(ch for ch in rest if ch.isprintable())[:100]
        sock.sendall(b"PONG " + arg.encode("utf-8") + b"\r\n")

    def send(self, login, token, channel, text):
        """Als `login` (mit dem Token des Kontos) `text` in den Chat von `channel` schreiben. Gibt (ok, kurze Meldung auf Deutsch) zurück und wirft nichts;
        der Text einer Ausnahme wird nie übernommen (er könnte Angaben enthalten)."""
        try:
            return self._send(login, token, channel, text)
        except Exception:
            return False, "unbekannter Fehler"

    def _send(self, login, token, channel, text):
        if not all(isinstance(v, str) for v in (login, token, channel, text)):
            return False, "Ungültige Anfrage"
        login, channel, body = login.lower(), channel.lower(), self.clean_text(text)
        if body is None or not (TwitchStore.NAME_RE.fullmatch(login) and TwitchStore.NAME_RE.fullmatch(channel) and TwitchStore.TOKEN_RE.fullmatch(token)):
            return False, "Ungültige Anfrage"                       # nichts, was eine Zeile umbrechen oder einen Befehl einschleusen könnte, geht auf die Leitung
        until = self.clock() + self.total
        try:
            sock, _ = self.paths.connect(self.host, self.port, timeout=max(0.1, min(self.connect_timeout, until - self.clock())))
        except OSError:
            return False, self.NO_CONNECT
        try:
            if self.tls:
                try:
                    sock = (self.context or ssl.create_default_context()).wrap_socket(sock, server_hostname=self.host)
                except ssl.SSLError:
                    return False, self.NO_TLS
                except OSError:
                    return False, self.NO_CONNECT
            return self._talk(sock, login, token, channel, body, until)
        except (EOFError, OSError):
            return False, self.CLOSED
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _talk(self, sock, login, token, channel, body, until):
        rd = _IrcLines(sock, self.clock)
        sock.settimeout(max(0.1, until - self.clock()))
        sock.sendall(b"PASS oauth:" + token.encode("ascii") + b"\r\nNICK " + login.encode("ascii") + b"\r\n")
        while True:                                                 # auf die Begrüßung warten
            line = rd.get(until)
            if line is None:
                return False, self.NO_ANSWER
            cmd, rest = self._parse(line)
            if cmd == "001":
                break
            if cmd == "PING":
                self._pong(sock, rest)
            elif cmd in ("464", "465") or (cmd == "NOTICE" and self.LOGIN_NOTICE_RE.search(self._notice_text(rest))):
                return False, self.LOGIN_FAILED
        chan = channel.encode("ascii")
        sock.settimeout(max(0.1, until - self.clock()))
        sock.sendall(b"CAP REQ :twitch.tv/commands\r\nJOIN #" + chan + b"\r\nPRIVMSG #" + chan + b" :" + body.encode("utf-8") + b"\r\n")
        notice, end = None, min(self.clock() + self.notice_wait, until)
        try:
            while notice is None:                                   # eine Ablehnung meldet Twitch als NOTICE
                line = rd.get(end)
                if line is None:
                    break
                cmd, rest = self._parse(line)
                if cmd == "PING":
                    self._pong(sock, rest)
                elif cmd == "NOTICE":
                    notice = self._scrub(self._notice_text(rest), token)
        except EOFError:
            pass                                                    # Twitch schließt nach einer Absage manchmal gleich
        try:
            sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        if notice is None:
            return True, self.OK
        return False, self.NOT_ALLOWED + (": " + notice if notice else "")


def _ascii_digits(v):
    """True nur für nicht leere Zeichenketten aus den Ziffern 0-9 (str.isdigit() lässt auch "²" und andere Ziffernzeichen zu, int() scheitert dann)."""
    return isinstance(v, str) and v.isascii() and v.isdigit()


class ChatPaths:
    """Wege ins Internet für den Chat (Anmeldung, Lesen, Schreiben, Moderation). Die Box hat je Sendeweg eine eigene Quelladresse mit eigener Route (Quell-Routing),
    wie sie auch srtla_send benutzt: Ein Socket, der an die Adresse eines Sendewegs gebunden wird, geht genau über diesen Weg hinaus. Der Chat nimmt die gewählten
    Sendewege der Reihe nach, den besten zuerst (nach der Güte in srtla-links.txt, sonst nach der Reihenfolge der Einstellung), und weicht bei Fehler oder
    Stille auf den nächsten aus; zuletzt kommt die normale Route. Ein Weg, der versagt hat, rückt für `hold` Sekunden ans Ende. Das ist Ausweichen, kein
    Bündeln: Eine TCP-Verbindung lässt sich nicht auf mehrere Wege verteilen, und der Chat braucht nur wenige Bytes. Ohne Quelle (Tests) gilt nur die normale Route."""

    def __init__(self, sources=None, clock=time.monotonic, links_file="/run/pipbox-send/srtla-links.txt"):
        self.sources, self.clock, self.links_file = sources, clock, links_file
        self.bad = {}
        self.lock = threading.Lock()

    def _quality(self):
        """{Adresse: Güte} aus den letzten Zeilen von srtla-links.txt (kleiner ist besser; Reserve-Wege gelten als schlechter)."""
        out = {}
        try:
            with open(self.links_file, errors="replace") as f:
                lines = f.read()[-6000:].splitlines()
        except OSError:
            return out
        for ln in lines:
            m = re.search(r"links: (\d+\.\d+\.\d+\.\d+) .*?guete=(-?\d+) (genutzt|reserve)", ln)
            if m:
                g = int(m.group(2))
                out[m.group(1)] = (9999 if g < 0 else g) + (0 if m.group(3) == "genutzt" else 100000)
        return out

    def order(self):
        try:
            srcs = [x for x in (self.sources() if self.sources else []) if isinstance(x, str) and x]
        except Exception:
            srcs = []
        q = self._quality()
        now = self.clock()
        with self.lock:
            good = [x for x in srcs if self.bad.get(x, 0) <= now]
            bad = [x for x in srcs if x not in good]
        good.sort(key=lambda x: q.get(x, 50000))                                  # stabil: gleiche Güte behält die Reihenfolge der Einstellung
        return good + [None] + bad

    def fail(self, src, hold=300.0):
        if src:
            with self.lock:
                self.bad[src] = self.clock() + hold

    def connect(self, host, port, timeout=6.0):
        """(Socket, Quelladresse) über den ersten Weg, der klappt; OSError, wenn keiner geht."""
        last = OSError()
        for src in self.order():
            try:
                return socket.create_connection((host, port), timeout=timeout, source_address=(src, 0) if src else None), src
            except socket.gaierror as e:                       # Namensauflösung: hängt nicht am Weg, also keinen Weg sperren und nicht jeden einzeln versuchen
                raise e
            except OSError as e:
                last = e
                self.fail(src, 120.0)
        raise last

    def request(self, method, url, data=None, headers=None, timeout=15.0, limit=200_000, once=False, prefer=False):
        """(Status, Antworttext als Bytes) einer HTTP(S)-Anfrage über den ersten Weg, der antwortet; (0, b"") ohne Verbindung.
        once=True für Anfragen, die sich nicht wiederholen lassen (Erneuern des Zugangs mit einmaligem Schlüssel): Nur ein Fehler beim Verbinden führt zum
        nächsten Weg; ist die Anfrage erst gesendet und die Antwort geht verloren, wird nicht ein zweites Mal gesendet (Ergebnis dann (-1, b"")).
        prefer: ein Weg (auch None = normale Route), der zuerst versucht wird, zum Beispiel der, über den gerade eine Verbindung steht."""
        import http.client
        import urllib.parse
        u = urllib.parse.urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        target = u.path + ("?" + u.query if u.query else "")
        order = self.order()
        if prefer is not False and prefer in order:
            order.remove(prefer)
            order.insert(0, prefer)
        for src in order:
            conn = None
            try:
                conn = cls(u.hostname, u.port, timeout=timeout, source_address=(src, 0) if src else None)
                try:
                    conn.connect()
                except socket.gaierror:
                    return 0, b""                                  # Namensauflösung hängt nicht am Weg: keinen Weg sperren, nicht alle durchprobieren
                except (OSError, http.client.HTTPException, ValueError):
                    self.fail(src, 120.0)
                    continue
                try:
                    conn.request(method, target, body=data, headers=headers or {})
                    r = conn.getresponse()
                    status = r.status
                    try:
                        return status, r.read(limit)
                    except (OSError, http.client.HTTPException, ValueError):
                        self.fail(src, 120.0)
                        if once:
                            return status, b""                         # Antwortzeile da, Rumpf verloren: der Status gilt
                        raise
                except (OSError, http.client.HTTPException, ValueError):
                    self.fail(src, 120.0)
                    if once:
                        return -1, b""                                 # gesendet, aber keine Antwort: nicht noch einmal senden
            except (OSError, http.client.HTTPException, ValueError):
                self.fail(src, 120.0)
            finally:
                try:
                    if conn:
                        conn.close()
                except OSError:
                    pass
        return 0, b""


class TwitchLogin:
    """Anmeldung beim Twitch-Konto des Nutzers per Geräte-Code ("Device Code Grant Flow" von Twitch): Die Box zeigt einen Code und einen Link, der Nutzer meldet
    sich auf twitch.tv selbst an und bestätigt die Rechte; die Box holt sich danach die Zugangsdaten ab. Die Zugangsdaten des Nutzers sehen wir nie.

    Die Anwendung ("IRL4YOU BOX", Typ "Public") hat nur eine Client-ID, die nicht geheim ist; ein Client-Secret gibt es nicht und wird nicht gebraucht.
    Der Zugangsschlüssel gilt 4 Stunden und wird im Hintergrund erneuert (keep). Der Erneuerungsschlüssel ist nur einmal verwendbar: Jede Erneuerung liefert einen
    neuen, der sofort atomar gespeichert wird. Bei einer öffentlichen Anwendung verfällt er nach 30 Tagen ohne Nutzung (dann: neu anmelden). Gespeichert wird in
    <state>/twitch-login.json (Rechte 0600). Tokens stehen nie in einer Antwort der Oberfläche, in Meldungen oder Protokollen. Im Vorschau-Modus (demo) läuft die
    Anmeldung ohne Netz ab. Adresse, Uhr und Wartezeit sind für Tests einstellbar."""
    CLIENT_ID = "a9w1hzb4d6nu70re8wxt6rpyxths35"
    ID_BASE = "https://id.twitch.tv/oauth2/"
    SCOPES = "chat:read chat:edit user:write:chat"                       # Lesen und Schreiben im Chat (user:write:chat: Senden über die Twitch-Schnittstelle mit Rückmeldung)
    CMD_SCOPES = ("channel:manage:vips channel:manage:moderators moderator:manage:chat_settings moderator:manage:announcements "
                  "channel:manage:raids channel:manage:broadcast")           # Chat-Befehle: VIP, Moderatoren, Chat-Einstellungen, Ankündigung, Raid, Marker, Titel und Kategorie
    MOD_BASE = "moderator:manage:banned_users moderator:manage:chat_messages"
    MOD_SCOPES = MOD_BASE + " " + CMD_SCOPES                                 # Moderation = Löschen, Timeout, Bann UND alle Befehle: wer sie einschaltet, bestätigt alles auf einmal
    EVENT_SCOPES = "moderator:read:followers channel:read:redemptions"          # Follows und Kanalpunkte-Einlösungen (EventSub); nur, wenn der Nutzer die Ereignisse einschaltet
    SCOPES_MOD = "chat:read chat:edit user:write:chat " + MOD_SCOPES      # nur, wenn der Nutzer die Moderation einschaltet
    GRANT = "urn:ietf:params:oauth:grant-type:device_code"
    # Streamer-Schlüssel: Jeder Browser, in dem sich der Streamer bei Twitch anmeldet, erzeugt einen Zufallsschlüssel und behält ihn nur bei sich (localStorage). Die Box
    # speichert davon nur den SHA-256-Wert (höchstens MAX_OWNERS Browser). Befehle, die der Kanalinhaber ausführt (VIP, Moderatoren, Raid, Marker, Titel, Kategorie),
    # gehen nur mit einem Schlüssel, dessen Wert hier steht: Wer nur den Link zur Box bekommt (zum Beispiel ein Moderator), kann sie damit nicht benutzen.
    OWNER_RE = re.compile(r"[0-9a-f]{64}")
    KEY_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
    MAX_OWNERS = 5
    DEMO_LOGIN = "demo_streamer"
    ERR_NOT_OWNER = "Das ist nicht das Konto des Streamers dieser Box"
    ERR_NO_CHANNEL = "Bitte zuerst eintragen, auf welchem Konto gestreamt wird (Kanal)"
    REFRESH_BEFORE = 600                       # Sekunden vor dem Ablauf erneuern
    KEEP_EVERY = 30.0
    ERR_NET = "Keine Verbindung zu Twitch"
    ERR_DENIED = "Die Anmeldung wurde abgelehnt oder ist abgelaufen"

    def __init__(self, path, client_id=None, id_base=None, clock=time.time, sleep=time.sleep, demo=False, paths=None):
        self.paths = paths or ChatPaths()
        self.path, self.client_id = path, client_id or self.CLIENT_ID
        self.base = id_base or self.ID_BASE
        self.clock, self.sleep, self.demo = clock, sleep, demo
        self.lock = threading.RLock()
        self.tokens = None                     # {"access", "refresh", "expires_at", "login", "user_id", "scopes"}
        self.pending = None                    # {"device_code", "user_code", "uri", "expires_at", "interval"}
        self.state, self.error = "aus", ""
        self.gen = 0                           # zählt Anmeldeversuche: ein abgebrochener Versuch beendet seinen Abfrage-Faden
        self.claim_fail = None                 # {"owner": Fingerabdruck, "error": Text}: ein Browser wollte sich als Streamer ausweisen, das Konto war ein anderes
        try:
            with open(path) as f:
                saved = json.load(f)
            if isinstance(saved, dict) and isinstance(saved.get("access"), str) and isinstance(saved.get("refresh"), str):
                self.tokens = {"access": saved["access"], "refresh": saved["refresh"], "expires_at": float(saved.get("expires_at", 0)),
                               "login": str(saved.get("login", ""))[:25], "user_id": str(saved.get("user_id", ""))[:20],
                               "scopes": [str(x)[:60] for x in saved.get("scopes", [])][:30], "mod_off": saved.get("mod_off") is True,
                               "owners": [x for x in saved.get("owners", []) if isinstance(x, str) and self.OWNER_RE.fullmatch(x)][:self.MAX_OWNERS]}
                self.state = "angemeldet"
        except (OSError, ValueError, TypeError):
            pass

    # ---- Anfragen an Twitch (nur Standardbibliothek)
    def _call(self, url, fields=None, headers=None, once=False):
        """(Status, JSON-dict) einer Anfrage über die Wege des Chats; bei Netzfehlern (0, {})."""
        import urllib.parse
        data = urllib.parse.urlencode(fields).encode() if fields is not None else None
        hdr = dict(headers or {}, **{"User-Agent": "irl4you-box"})
        if data is not None:
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        status, body = self.paths.request("POST" if data is not None else "GET", url, data, hdr, once=once)
        try:
            out = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError:
            out = {}
        return status, out if isinstance(out, dict) else {}

    def _write(self, tokens=None):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(tokens if tokens is not None else self.tokens, f)
                f.flush()
                os.fsync(f.fileno())                          # auch bei Stromausfall nie eine halbe oder leere Datei
            os.replace(tmp, self.path)
            try:
                dfd = os.open(os.path.dirname(os.path.abspath(self.path)), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ---- Zustand für die Oberfläche (ohne Tokens)
    def status(self):
        if self.demo:
            with self.lock:
                self._demo_step()
        t, p, state = self.tokens, self.pending, self.state                # nur lesen: nie hinter einer laufenden Anfrage an Twitch warten
        scopes = list((t or {}).get("scopes", []))
        has = bool(self.MOD_SCOPES) and all(x in scopes for x in self.MOD_SCOPES.split())             # Moderation samt Befehlen: ältere Anmeldungen ohne die Befehlsrechte brauchen einmal "Moderation einschalten"
        ev = all(x in scopes for x in self.EVENT_SCOPES.split())
        out = {"state": state, "login": (t or {}).get("login", ""), "scopes": scopes, "error": self.error,
               "mod": has and not (t or {}).get("mod_off"),           # Moderation ist an: Rechte vorhanden und nicht von Hand ausgeschaltet
               "mod_scope": has,                                      # die Rechte hat der Zugang (Einschalten geht dann ohne neue Anmeldung bei Twitch)
               "events": ev,                                          # Rechte für Follows und Kanalpunkte sind da
               "helix_chat": "user:write:chat" in scopes}             # Senden über die Twitch-Schnittstelle ist möglich
        if p and state == "wartet":
            out.update(code=p["user_code"], uri=p["uri"], expires_in=max(0, int(p["expires_at"] - self.clock())))
        return out

    # ready/login/user_id nehmen die Sperre nicht: Sie lesen nur, und die Oberfläche (alle paar Sekunden) darf nie hinter einer Anfrage an Twitch warten.
    # Maßgeblich ist, ob Zugangsdaten da sind, nicht der Zustand einer gerade laufenden neuen Anmeldung (wartet/fehler).
    def ready(self):
        return bool(self.tokens)

    def login(self):
        t = self.tokens
        return t.get("login", "") if t else ""

    def user_id(self):
        t = self.tokens
        return t.get("user_id", "") if t else ""

    def token(self):
        """Gültiger Zugangsschlüssel oder "" (erneuert bei Bedarf sofort)."""
        t = self.tokens
        if not t:
            return ""
        if t["expires_at"] - self.clock() < 60:
            with self.lock:
                if self.tokens and self.tokens["expires_at"] - self.clock() < 60:
                    self.refresh()
            t = self.tokens
        if t and t["expires_at"] - self.clock() > 0:
            return t["access"]
        return ""

    # ---- Anmelden
    def scopes_for(self, mod=False, events=False, everything=False):
        """Die Rechte für eine neue Anmeldung: immer die Grundrechte, Moderation (samt Befehlen) und Ereignisse nur auf Wunsch (alles auf einmal mit everything), und
        was der Zugang schon hat, bleibt erhalten (wer die Ereignisse einschaltet, verliert die Moderation nicht, und umgekehrt)."""
        want = self.SCOPES.split()
        have = set(((self.tokens or {}).get("scopes")) or [])
        if everything:
            mod = events = True
        for flag, group in ((mod, self.MOD_SCOPES), (events, self.EVENT_SCOPES)):
            for sc in group.split():
                if (flag or sc in have) and sc not in want:
                    want.append(sc)
        return " ".join(want)

    @classmethod
    def owner_hash(cls, key):
        """SHA-256 des Schlüssels oder None, wenn er nicht der Form entspricht."""
        if not isinstance(key, str) or not cls.KEY_RE.fullmatch(key):
            return None
        return hashlib.sha256(key.encode("ascii")).hexdigest()

    def is_owner(self, key):
        """Gehört dieser Schlüssel zu einem Browser, in dem sich der Streamer angemeldet hat?"""
        h = self.owner_hash(key)
        t = self.tokens
        return bool(h and t and h in (t.get("owners") or []))

    def start(self, mod=False, events=False, everything=False, owner_key=None, claim=False, expect=None):
        """claim: Dieser Browser gilt noch nicht als der des Streamers: Die Anmeldung gilt nur, wenn Twitch das Konto des Streamers bestätigt (sonst würde jeder mit
        dem Link das Konto der Box ersetzen). Das Konto des Streamers ist expect (der eingetragene Kanal, "Konto, auf dem gestreamt wird"), sonst das schon
        angemeldete Konto der Box."""
        scopes = self.scopes_for(mod, events, everything)
        owner = self.owner_hash(owner_key)
        with self.lock:
            if self.claim_fail and self.claim_fail.get("owner") == owner:
                self.claim_fail = None
            if (self.state == "wartet" and self.pending and self.pending["expires_at"] > self.clock() and self.pending.get("scopes") == scopes
                    and self.pending.get("owner") == owner):                          # ein anderer Browser (anderer Schlüssel) beginnt eine neue Anmeldung: der Schlüssel gehört dem, der sie bestätigt
                return self.status()
            self.gen += 1
            gen = self.gen
            self.error = ""
            if self.demo:
                self.pending = {"device_code": "demo", "user_code": "ABCD-EFGH", "uri": "https://www.twitch.tv/activate?device-code=ABCDEFGH",
                                "expires_at": self.clock() + 1800, "interval": 5, "since": self.clock(), "scopes": scopes, "owner": owner, "claim": bool(claim),
                                "expect": str(expect or "").lower()[:25]}
                self.state = "wartet"
                return self.status()
            st, d = self._call(self.base + "device", {"client_id": self.client_id, "scopes": scopes})
            if st != 200 or not all(isinstance(d.get(k), str) for k in ("device_code", "user_code", "verification_uri")):
                self.state, self.error = ("angemeldet" if self.tokens else "fehler", self.ERR_NET if st == 0 else "Twitch hat die Anmeldung nicht angenommen")
                raise ValueError(self.error)
            try:
                interval = max(2, min(30, int(d.get("interval", 5))))
                expires = max(60, min(3600, int(d.get("expires_in", 1800))))
            except (TypeError, ValueError):
                interval, expires = 5, 1800
            uri = d["verification_uri"][:300]
            if not re.match(r"https://(www\.|id\.)?twitch\.tv/", uri):
                uri = "https://www.twitch.tv/activate"                              # nur Twitch-Adressen als Link und QR-Code
            self.pending = {"device_code": d["device_code"], "user_code": d["user_code"][:20], "uri": uri,
                            "expires_at": self.clock() + expires, "interval": interval, "scopes": scopes, "owner": owner, "claim": bool(claim),
                            "expect": str(expect or "").lower()[:25]}
            self.state = "wartet"
        threading.Thread(target=self._poll, args=(gen,), daemon=True).start()
        return self.status()

    def cancel(self):
        with self.lock:
            self.gen += 1
            self.pending = None
            if self.state == "wartet":
                self.state = "angemeldet" if self.tokens else "aus"
            return self.status()

    def _poll(self, gen):
        while True:
            with self.lock:
                p = self.pending
                if gen != self.gen or not p or self.state != "wartet":
                    return
                if p["expires_at"] <= self.clock():
                    self.state, self.error, self.pending = ("angemeldet" if self.tokens else "fehler"), self.ERR_DENIED, None
                    return
                wait, device, scopes = p["interval"], p["device_code"], p.get("scopes", self.SCOPES)
            self.sleep(wait)
            st, d = self._call(self.base + "token", {"client_id": self.client_id, "scopes": scopes, "device_code": device, "grant_type": self.GRANT}, once=True)
            with self.lock:
                if gen != self.gen or self.state != "wartet":
                    if st == 200 and isinstance(d.get("access_token"), str):          # der Nutzer hat abgebrochen, Twitch hat trotzdem ausgestellt: Zugang gleich widerrufen
                        self._call(self.base + "revoke", {"client_id": self.client_id, "token": d["access_token"]})
                    return
                if st == 200 and isinstance(d.get("access_token"), str) and isinstance(d.get("refresh_token"), str):
                    self._adopt(d)
                    return
                msg = str(d.get("message", "")).lower()
                if st == 400 and "authorization_pending" in msg:
                    continue
                if st == 400 and "slow_down" in msg:
                    self.pending["interval"] = min(30, self.pending["interval"] + 5)
                    continue
                if st <= 0 or st >= 500:
                    continue                                    # Netz kurz weg oder Twitch hakt: weiter versuchen, bis der Code abläuft
                if st == 429:
                    self.pending["interval"] = min(30, self.pending["interval"] + 5)
                    continue
                self.state, self.error, self.pending = ("angemeldet" if self.tokens else "fehler"), self.ERR_DENIED, None
                return

    def _adopt(self, d):
        """Neue Zugangsdaten von Twitch übernehmen (mit Sperre aufgerufen): Konto prüfen, atomar speichern."""
        scopes = d.get("scope")
        scopes = [str(x)[:60] for x in scopes][:30] if isinstance(scopes, list) else self.SCOPES.split()
        try:
            expires = max(60.0, float(d.get("expires_in", 14400)))
        except (TypeError, ValueError):
            expires = 14400.0
        old = self.tokens or {}
        pend = (self.pending or {}).get("owner")                     # Schlüssel des Browsers, der diese Anmeldung gestartet hat (beim Erneuern: keiner)
        want = (self.pending or {}).get("expect") or old.get("login")
        if (self.pending or {}).get("claim") and want:
            st0, v0 = self._call(self.base + "validate", None, {"Authorization": "OAuth " + d["access_token"]})
            who = v0.get("login", "").lower() if st0 == 200 and isinstance(v0.get("login"), str) else ""
            if who != want:                                  # ein anderes Konto: nichts übernehmen, den neuen Zugang gleich widerrufen, den alten behalten
                self._call(self.base + "revoke", {"client_id": self.client_id, "token": d["access_token"]})
                self.claim_fail = {"owner": pend, "error": self.ERR_NOT_OWNER}
                self.pending, self.state, self.error = None, ("angemeldet" if self.tokens else "aus"), ""
                return
        t = {"access": d["access_token"], "refresh": d["refresh_token"], "expires_at": self.clock() + expires, "scopes": scopes,
             "login": old.get("login", ""), "user_id": old.get("user_id", ""), "mod_off": old.get("mod_off") is True,
             "owners": list(old.get("owners") or [])}
        werr = False
        try:
            self._write(t)                                           # sofort: der alte Erneuerungsschlüssel ist ab jetzt ungültig, der neue darf nie verloren gehen
        except OSError:
            werr = True
        st, v = self._call(self.base + "validate", None, {"Authorization": "OAuth " + t["access"]})
        changed = False
        if st == 200 and isinstance(v.get("login"), str):
            new = (v["login"][:25].lower(), str(v.get("user_id", ""))[:20], [str(x)[:60] for x in v["scopes"]][:30] if isinstance(v.get("scopes"), list) else scopes)
            changed = new != (t["login"], t["user_id"], t["scopes"])
            if t["login"] and new[0] != t["login"]:
                t["owners"] = []                                     # anderes Konto: die Schlüssel des früheren gelten nicht mehr
            t["login"], t["user_id"], t["scopes"] = new
        if pend and pend not in t["owners"]:
            t["owners"] = (t["owners"] + [pend])[-self.MAX_OWNERS:]
            changed = True
        self.tokens = t                                              # erst jetzt sichtbar: nie mit leerem Namen oder leerer Konto-Kennung
        self.pending, self.state, self.error = None, "angemeldet", ""
        if changed:
            try:
                self._write()
                werr = False
            except OSError:
                werr = True
        if werr:
            self.error = "Die Anmeldung konnte nicht gespeichert werden"

    # ---- Erneuern und Abmelden
    def refresh(self):
        """Zugangsschlüssel erneuern (mit Sperre). Netzfehler lassen alles, wie es ist; ein abgelehnter Erneuerungsschlüssel bedeutet: neu anmelden."""
        with self.lock:
            if not self.tokens:
                return False
            if self.clock() < getattr(self, "next_try", 0.0):
                return False                                          # nach Fehlschlägen nicht gleich wieder (Twitch oder Netz hakt)
            st, d = self._call(self.base + "token", {"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": self.tokens["refresh"]}, once=True)
            if st == 200 and isinstance(d.get("access_token"), str) and isinstance(d.get("refresh_token"), str):
                self.fails, self.next_try = 0, 0.0
                self._adopt(d)
                return True
            if st <= 0 or st == 429 or st >= 500:
                self.fails = getattr(self, "fails", 0) + 1
                self.next_try = self.clock() + min(300.0, 15.0 * 2 ** min(self.fails, 5))
                return False
            if st in (400, 401):
                keep = self.tokens.get("login", "")
                self.tokens, self.pending, self.state = None, None, "abgelaufen"
                self.error = keep
                try:
                    os.remove(self.path)
                except OSError:
                    pass
            return False

    def keep(self):
        """Hintergrunddienst: erneuert rechtzeitig vor dem Ablauf (läuft, solange die Box läuft, auch ohne Sendung)."""
        while True:
            try:
                if self.tokens and self.tokens["expires_at"] - self.clock() < self.REFRESH_BEFORE:
                    with self.lock:
                        if self.tokens and self.tokens["expires_at"] - self.clock() < self.REFRESH_BEFORE:
                            self.refresh()
            except Exception:
                pass                                                  # nie den Hintergrunddienst verlieren
            self.sleep(self.KEEP_EVERY)

    def set_mod(self, on):
        """Moderation von Hand aus- oder einschalten. Aus: Die Box nutzt die Rechte nicht mehr (Twitch erlaubt nicht, die Rechte eines Zugangs zu verkleinern;
        sie fallen erst bei Abmelden oder neuer Anmeldung ohne Moderation weg). Ein: nur, wenn der Zugang die Rechte hat, sonst ist eine neue Anmeldung nötig."""
        with self.lock:
            t = self.tokens
            if not t:
                raise ValueError("Zuerst mit Twitch anmelden")
            has = bool(self.MOD_SCOPES) and all(x in t.get("scopes", []) for x in self.MOD_SCOPES.split())
            if on and not has:
                raise ValueError("Moderation braucht eine neue Anmeldung bei Twitch")
            t["mod_off"] = not on
            try:
                self._write()
            except OSError:
                pass
            return self.status()

    def refresh_now(self):
        """Sofort erneuern (auch gegen den Wartebetrieb nach Fehlschlägen), zum Beispiel nach einem 401 von Twitch. True, wenn es geklappt hat."""
        with self.lock:
            self.next_try = 0.0
            return bool(self.tokens) and self.refresh()

    def logout(self):
        with self.lock:
            self.gen += 1
            t = self.tokens
            self.tokens, self.pending, self.state, self.error = None, None, "aus", ""
            try:
                os.remove(self.path)
            except OSError:
                pass
        if t and not self.demo:
            self._call(self.base + "revoke", {"client_id": self.client_id, "token": t["access"]})      # Fehler egal: lokal ist die Anmeldung weg
        return self.status()

    # ---- Vorschau-Modus
    def _demo_step(self):
        if self.state == "wartet" and self.pending and self.clock() - self.pending.get("since", 0) > 6:
            self.tokens = {"access": "demo", "refresh": "demo", "expires_at": self.clock() + 14400, "login": self.DEMO_LOGIN, "user_id": "1",
                           "scopes": (self.pending.get("scopes") or self.SCOPES).split(), "owners": [self.pending["owner"]] if self.pending.get("owner") else []}
            self.pending, self.state = None, "angemeldet"


class TwitchBotLogin(TwitchLogin):
    """Zweites Twitch-Konto ("Bot"), das nur die Akku-Meldung in den Chat schreibt: dieselbe Anmeldung per Geräte-Code wie beim Hauptkonto, aber mit den kleinsten
    Rechten (Lesen und Schreiben im Chat über IRC, keine Moderation, keine Ereignisse) und eigener Datei <state>/twitch-bot-login.json. Der Chat der Oberfläche
    nutzt dieses Konto nie."""
    SCOPES = "chat:read chat:edit"
    MOD_SCOPES = EVENT_SCOPES = ""


class TwitchModLogin(TwitchLogin):
    """Eigenes Twitch-Konto eines Zuschauers oder Moderators, der sich in seinem Browser anmeldet: Zuschauer nur Lesen und Schreiben im Chat, Moderatoren
    zusätzlich die Moderatorenrechte (Löschen, Timeout, Bann, Chat-Einstellungen, Ankündigung); nichts vom Kanalinhaber (kein VIP, Raid, Titel) und keine
    Ereignisse. Eine Datei je Browser."""
    MOD_SCOPES = "moderator:manage:banned_users moderator:manage:chat_messages moderator:manage:chat_settings moderator:manage:announcements"
    EVENT_SCOPES = CMD_SCOPES = ""
    DEMO_LOGIN = "demo_moderator"


class ModSessions:
    """Die Twitch-Anmeldungen der Moderatoren: je Browser (Schlüssel, siehe TwitchLogin.owner_hash) ein eigenes Konto in <state>/twitch-mod-<Fingerabdruck>.json
    (Rechte 0600). Wer nur den Link zur Box hat, schreibt und moderiert damit nie mit dem Konto des Streamers, sondern meldet sich selbst an und darf, was er bei
    Twitch als Moderator darf. Höchstens MAX Browser."""
    MAX = 12

    def __init__(self, state_dir, demo=False, paths=None, id_base=None, client_id=None, clock=time.time, sleep=time.sleep):
        self.dir, self.demo, self.paths = state_dir, demo, paths
        self.kw = {"clock": clock, "sleep": sleep}
        if id_base:
            self.kw["id_base"] = id_base
        if client_id:
            self.kw["client_id"] = client_id
        self.items = {}
        self.lock = threading.Lock()

    def _path(self, h):
        return os.path.join(self.dir, "twitch-mod-%s.json" % h[:32])

    def _make(self, h):
        return TwitchModLogin(self._path(h), demo=self.demo, paths=self.paths, **self.kw)

    def _count(self):
        try:
            return len([n for n in os.listdir(self.dir) if n.startswith("twitch-mod-") and n.endswith(".json")])
        except OSError:
            return 0

    def get(self, key):
        """Die Anmeldung dieses Browsers oder None (auch von der Platte geladen, zum Beispiel nach einem Neustart der Box)."""
        h = TwitchLogin.owner_hash(key)
        if not h:
            return None
        with self.lock:
            s = self.items.get(h)
            if s is None and os.path.exists(self._path(h)):
                s = self.items[h] = self._make(h)
            return s

    def start(self, key, mod=True):
        """mod=False: normale Zuschauer-Anmeldung (nur Lesen und Schreiben im Chat); mod=True: zusätzlich die Moderatorenrechte. Wer sich später als Moderator
        anmeldet, behält, was er schon hat."""
        h = TwitchLogin.owner_hash(key)
        if not h:
            raise ValueError("Dieser Browser kann sich nicht anmelden")
        with self.lock:
            s = self.items.get(h)
            if s is None:
                if self._count() >= self.MAX and not os.path.exists(self._path(h)):
                    raise ValueError("Es sind schon zu viele Moderatoren angemeldet")
                s = self.items[h] = self._make(h)
        return s.start(mod=bool(mod), owner_key=key)

    def logout(self, key):
        s = self.get(key)
        if s is not None:
            s.logout()
        h = TwitchLogin.owner_hash(key)
        if h:
            with self.lock:
                self.items.pop(h, None)
            try:
                os.remove(self._path(h))
            except OSError:
                pass


class TwitchAccounts:
    """Welches Twitch-Konto gilt in welchem Browser, und was darf es? "owner": der Browser des Streamers (sein Schlüssel steht bei der Anmeldung der Box; solange
    noch niemand angemeldet ist, kann sich jeder als Erster anmelden und wird es), "mod": ein Browser mit eigener Anmeldung eines Moderators, "user": ein Browser
    mit eigener Zuschauer-Anmeldung (schreiben, nicht moderieren), "none": sonst, nur lesen. Ob ein angemeldeter "Moderator" im Kanal wirklich einer ist, fragt die
    Box (mit dem Konto des Streamers) höchstens alle fünf Minuten bei Twitch nach (check); weiß sie es nicht, gelten die angemeldeten Rechte. Die Oberfläche bekommt
    immer die Lage des eigenen Browsers (status), nie die Zugangsdaten."""
    NONE_STATUS = {"state": "aus", "login": "", "scopes": [], "error": "", "mod": False, "mod_scope": False, "events": False, "helix_chat": False}
    CHECK_TTL = 300.0

    def __init__(self, box, sessions, check=None, clock=time.time, store=None):
        self.box, self.sessions, self.check, self.clock, self.store = box, sessions, check, clock, store
        self.known = {}                                                # Kennung -> (Zeitpunkt, True/False/None)
        self.lock = threading.Lock()

    def _is_channel_mod(self, acct):
        """True/False, ob dieses Konto im Kanal Moderator ist; None, wenn unbekannt."""
        if self.check is None:
            return None
        uid = acct.user_id()
        now = self.clock()
        with self.lock:
            hit = self.known.get(uid)
            if hit and now - hit[0] < self.CHECK_TTL:
                return hit[1]
        try:
            ans = self.check(uid)
        except Exception:
            ans = None
        with self.lock:
            if len(self.known) > 100:
                self.known.clear()
            self.known[uid] = (now, ans)
        return ans

    def streamer_name(self):
        """Das Konto des Streamers: das angemeldete Konto der Box, sonst der eingetragene Kanal ("Konto, auf dem gestreamt wird", dasselbe Feld wie bei der
        Akku-Warnung); nichts, wenn beides fehlt. Es wird nie etwas vorbelegt."""
        name = self.box.login()
        if name:
            return name
        if self.store is not None:
            with self.store.lock:
                return str(self.store.data.get("channel") or "").lower()
        return ""

    def pick(self, key):
        if self.box.ready():
            if self.box.is_owner(key):
                return "owner", self.box
        elif not self.streamer_name():
            return "owner", self.box                                   # noch nichts eingetragen: es lässt sich nur der Kanal eintragen, dann ist der Streamer bekannt
        s = self.sessions.get(key)
        if s is not None and self.sessions.demo:
            s.status()                                                  # Vorschau-Modus: die Anmeldung läuft nur beim Abfragen weiter
        if s is not None and s.ready():
            if s.status()["mod"] and self._is_channel_mod(s) is not False:
                return "mod", s
            return "user", s
        return "none", None

    def status(self, key):
        role, acct = self.pick(key)
        boxlogin = self.box.login()
        if role == "owner":
            st = self.box.status()
        elif role in ("mod", "user"):
            st = acct.status()
            if role == "user":
                st["mod"] = False                                       # (auch wenn die Rechte da sind: im Kanal kein Moderator)
        else:
            h = TwitchLogin.owner_hash(key)
            s = self.sessions.get(key)
            st = dict(self.NONE_STATUS, scopes=[])
            if s is not None and s.state != "aus":
                own = s.status()                                       # eigene Anmeldung läuft (Code und Link) oder ist fehlgeschlagen
                st.update({k: own[k] for k in ("state", "error", "code", "uri", "expires_in") if k in own})
            elif h and self.box.state == "wartet" and (self.box.pending or {}).get("owner") == h:
                own = self.box.status()                                # dieser Browser weist sich gerade als Streamer aus
                st.update({k: own[k] for k in ("state", "code", "uri", "expires_in") if k in own})
            cf = self.box.claim_fail
            if cf and h and cf.get("owner") == h and st["state"] != "wartet":
                st["state"], st["error"] = "fehler", cf["error"]
        name = self.streamer_name()
        st["role"], st["box_login"], st["streamer"] = role, boxlogin, name
        st["need_channel"] = bool(role == "owner" and not self.box.ready() and not name)
        return st


class ThirdPartyEmotes:
    """Emotes von 7TV, BetterTTV und FrankerFaceZ (global und je Kanal) für den Chat. Die Box lädt die Listen selbst (über dieselben Wege wie der Chat),
    baut die Bildadressen **nur aus geprüften Kennungen** (nie aus Adressen der Dienste) und hält sie 30 Minuten. Der Reader ersetzt damit Wörter im Text
    durch [Adresse, von, bis]. Ohne Verbindung bleiben die Wörter Text; Fehler bleiben still."""
    TTL = 1800.0
    RETRY = 300.0
    MAX = 8000
    NAME_RE = re.compile(r"[A-Za-z0-9_:()!.\-]{1,40}")
    BTTV_ID, STV_ID, FFZ_ID = re.compile(r"[0-9a-f]{24}"), re.compile(r"[0-9A-Za-z]{26}"), re.compile(r"[0-9]{1,9}")
    URLS = {"bttv": "https://cdn.betterttv.net/emote/%s/2x.webp", "7tv": "https://cdn.7tv.app/emote/%s/2x.webp", "ffz": "https://cdn.frankerfacez.com/emote/%s/2"}
    HOSTS = re.compile(r"https://(cdn\.betterttv\.net|cdn\.7tv\.app|cdn\.frankerfacez\.com)/")

    def __init__(self, paths=None, fetch=None, clock=time.monotonic):
        self.paths, self.fetch, self.clock = paths, fetch or self._fetch, clock
        self.lock = threading.Lock()
        self.map, self.room, self.until, self.thread = {}, None, 0.0, None
        self.users, self.uq, self.uthread, self.on_user = collections.OrderedDict(), collections.deque(), None, None      # Listen der Schreiber (Uid -> (bis, Wörter))
        self.personal = collections.OrderedDict()                       # persönliche BTTV-Emotes (Uid -> (bis, Wörter)), kommen über die Live-Verbindung von BTTV
        self.sthread, self.stouch, self.sroom, self.sretry, self.sfails = None, 0.0, "", 0.0, 0
        self.sock_on = paths is not None and fetch is None             # nur mit echten Wegen (Tests mit eigener Abfrage bleiben ohne Netz)
        self.sock_host, self.sock_port, self.sock_tls, self.sock_path = "sockets.betterttv.net", 443, True, "/ws"

    def _fetch(self, url):
        try:
            if self.paths is not None:
                status, body = self.paths.request("GET", url, headers={"User-Agent": "pipbox"}, timeout=10.0, limit=3_000_000)
            else:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pipbox"}), timeout=10) as r:
                    status, body = r.status, r.read(3_000_000)
            return json.loads(body.decode("utf-8", "replace")) if status == 200 else (False if status in (404, 410) else None)
        except Exception:
            return None

    def snapshot(self):
        return self.map

    def ensure(self, room):
        room = room if isinstance(room, str) and re.fullmatch(r"[0-9]{1,12}", room) else ""
        self._watch(room)
        with self.lock:
            if (self.thread is not None and self.thread.is_alive()) or (self.room == room and self.clock() < self.until):
                return
            self.room = room
            self.until = self.clock() + self.RETRY            # bis zur Fertigstellung nicht noch einmal starten
            self.thread = threading.Thread(target=self._load, args=(room,), daemon=True)
            self.thread.start()

    @classmethod
    def _add(cls, out, kind, items, idkey, namekey, idre):
        for it in (items or [])[:3000]:
            if len(out) >= cls.MAX or not isinstance(it, dict):
                continue
            i, n = it.get(idkey), it.get(namekey)
            if isinstance(i, (str, int)) and isinstance(n, str) and cls.NAME_RE.fullmatch(n) and idre.fullmatch(str(i)):
                out[n] = cls.URLS[kind] % i

    def _load(self, room):
        out, fails = {}, 0
        def get(url):
            nonlocal fails
            try:
                d = self.fetch(url)
            except Exception:
                d = None
            if d is None:
                fails += 1                                   # Zeitüberschreitung, 5xx, kaputte Antwort (ein 404 für den Kanal ist dagegen in Ordnung)
            return d or None
        ffz = lambda d: [e for st in ((d or {}).get("sets") or {}).values() if isinstance(st, dict) for e in (st.get("emoticons") or [])]
        def lst(x):
            return x if isinstance(x, list) else []
        def dct(x):
            return x if isinstance(x, dict) else {}
        steps = [
            lambda: self._add(out, "bttv", lst(get("https://api.betterttv.net/3/cached/emotes/global")), "id", "code", self.BTTV_ID),
            lambda: self._add(out, "ffz", ffz(get("https://api.frankerfacez.com/v1/set/global")), "id", "name", self.FFZ_ID),
            lambda: self._add(out, "7tv", lst(dct(get("https://7tv.io/v3/emote-sets/global")).get("emotes")), "id", "name", self.STV_ID)]
        if room:
            steps += [
                lambda: (lambda d: self._add(out, "bttv", lst(d.get("channelEmotes")) + lst(d.get("sharedEmotes")), "id", "code", self.BTTV_ID))(
                    dct(get("https://api.betterttv.net/3/cached/users/twitch/" + room))),
                lambda: self._add(out, "ffz", ffz(get("https://api.frankerfacez.com/v1/room/id/" + room)), "id", "name", self.FFZ_ID),
                lambda: self._add(out, "7tv", lst(dct(dct(get("https://7tv.io/v3/users/twitch/" + room)).get("emote_set")).get("emotes")), "id", "name", self.STV_ID)]
        for step in steps:
            try:
                step()
            except Exception:
                fails += 1
        with self.lock:
            if fails == 0 or not self.map:
                self.map = out                                # vollständig geladen (oder noch nichts da: Teilstand ist besser als nichts)
            else:
                merged = dict(self.map)                       # teilweise gescheitert: die gute alte Liste behalten und nur ergänzen
                merged.update(out)
                self.map = merged
            self.until = self.clock() + (self.TTL if fails == 0 else self.RETRY)

    USER_TTL, USER_MAX, USER_QUEUE = 1800.0, 300, 50

    # Persönliche BTTV-Emotes (BTTV Pro): Die Erweiterung meldet sie über BTTVs Live-Verbindung (wss://sockets.betterttv.net/ws) bei jeder Nachricht, die der
    # Zuschauer schickt, an alle, die den Kanal ("twitch:<Kanal-Nr>") abhören. Die Box hört nur zu (join_channel, nie broadcast_me), ohne Anmeldung. Die
    # Verbindung steht, solange Nachrichten kommen (SOCK_IDLE Sekunden nach der letzten), und baut sich mit wachsender Wartezeit neu auf.
    SOCK_IDLE, SOCK_MAX_FRAME = 900.0, 1_000_000

    def _watch(self, room):
        if not room or not self.sock_on:
            return
        with self.lock:
            self.stouch, self.sroom = self.clock(), room
            if (self.sthread is not None and self.sthread.is_alive()) or self.clock() < self.sretry:
                return
            self.sthread = threading.Thread(target=self._sock_loop, args=(room,), daemon=True)
            self.sthread.start()

    def _sock_open(self):
        if self.paths is not None:
            sk, _ = self.paths.connect(self.sock_host, self.sock_port, timeout=8)
        else:
            sk = socket.create_connection((self.sock_host, self.sock_port), timeout=8)
        try:
            if self.sock_tls:
                sk = ssl.create_default_context().wrap_socket(sk, server_hostname=self.sock_host)
            key = base64.b64encode(os.urandom(16)).decode()
            sk.sendall((b"GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                        b"Sec-WebSocket-Version: 13\r\nUser-Agent: pipbox\r\n\r\n") % (self.sock_path.encode(), self.sock_host.encode(), key.encode()))
            sk.settimeout(8)
            buf = b""
            while b"\r\n\r\n" not in buf:
                d = sk.recv(4096)
                if not d or len(buf) > 8192:
                    raise OSError("ws-no-answer")
                buf += d
            head, _, rest = buf.partition(b"\r\n\r\n")
            if b" 101 " not in head.split(b"\r\n")[0] + b" ":
                raise OSError("ws-no-upgrade")
            return sk, rest
        except Exception:
            try:
                sk.close()
            except Exception:
                pass
            raise

    @staticmethod
    def _ws_frame(opcode, payload=b""):
        m = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode, 0x80 | (n if n < 126 else 126)]) + (struct.pack(">H", n) if n >= 126 else b"")
        return head + m + bytes(b ^ m[i % 4] for i, b in enumerate(payload))

    def _ws_frames(self, buf):
        """(Liste (Opcode, Nutzdaten), Rest) aus den empfangenen Bytes; ValueError bei zu großem Rahmen."""
        out = []
        while len(buf) >= 2:
            op, ln, off = buf[0] & 0x0F, buf[1] & 0x7F, 2
            if ln == 126:
                if len(buf) < 4:
                    break
                ln, off = struct.unpack(">H", buf[2:4])[0], 4
            elif ln == 127:
                if len(buf) < 10:
                    break
                ln, off = struct.unpack(">Q", buf[2:10])[0], 10
            if ln > self.SOCK_MAX_FRAME:
                raise ValueError("ws-frame-too-large")
            if buf[1] & 0x80:
                off += 4                                                 # Server maskiert nicht; falls doch, nur überspringen
            if len(buf) < off + ln:
                break
            out.append((op, buf[off:off + ln]))
            buf = buf[off + ln:]
        return out, buf

    def _sock_loop(self, room):
        sk = None
        try:
            sk, buf = self._sock_open()
            sk.sendall(self._ws_frame(1, json.dumps({"name": "join_channel", "data": {"name": "twitch:" + room}}).encode()))
            sk.settimeout(5)
            last_ping = self.clock()
            while self.clock() - self.stouch < self.SOCK_IDLE and self.sroom == room:
                frames, buf = self._ws_frames(buf)
                for op, data in frames:
                    if op == 1:
                        self._on_event(data.decode("utf-8", "replace"))
                    elif op == 9:
                        sk.sendall(self._ws_frame(10, data[:125]))
                    elif op == 8:
                        raise OSError("ws-closed-by-peer")
                if self.clock() - last_ping > 25:
                    sk.sendall(self._ws_frame(9, b"pb"))
                    last_ping = self.clock()
                try:
                    d = sk.recv(65536)
                except socket.timeout:
                    continue
                if not d:
                    raise OSError("ws-closed")
                buf += d
                if len(buf) > 2 * self.SOCK_MAX_FRAME:
                    raise ValueError("ws-too-much-data")
            self.sfails = 0
        except Exception:
            self.sfails = min(self.sfails + 1, 6)
            with self.lock:
                self.sretry = self.clock() + min(30.0 * 2 ** (self.sfails - 1), 900.0)         # nach Fehlern mit wachsender Wartezeit (30 s bis 15 min)
        finally:
            if sk is not None:
                try:
                    sk.close()
                except Exception:
                    pass
            with self.lock:
                self.sthread = None

    def _on_event(self, text):
        try:
            d = json.loads(text)
        except ValueError:
            return
        if not isinstance(d, dict) or d.get("name") != "lookup_user" or not isinstance(d.get("data"), dict):
            return
        data = d["data"]
        uid = str(data.get("providerId") or "")
        if not re.fullmatch(r"[0-9]{1,12}", uid) or not data.get("pro") or not isinstance(data.get("emotes"), list):
            return
        out = {}
        self._add(out, "bttv", data["emotes"], "id", "code", self.BTTV_ID)
        if not out:
            return
        with self.lock:
            self.personal[uid] = (self.clock() + self.USER_TTL, out)
            self.personal.move_to_end(uid)
            while len(self.personal) > self.USER_MAX:
                self.personal.popitem(last=False)
        cb = self.on_user
        if cb:
            try:
                cb(uid)
            except Exception:
                pass

    # Emotes der Schreiber: BTTV und 7TV zeigen in der Erweiterung auch die Emotes, die ein Zuschauer selbst eingerichtet hat (persönliche Liste). Die Listen sind
    # öffentlich (keine Anmeldung); die Box fragt sie je Schreiber einmal ab (nur über die Nummer aus Twitchs Nachricht), hält sie 30 Minuten und lädt nacheinander.
    def user_map(self, uid):
        with self.lock:
            now = self.clock()
            e, p = self.users.get(uid), self.personal.get(uid)
            base = e[1] if e and now < e[0] else None
            per = p[1] if p and now < p[0] else None
        if per is None:
            return base
        return {**(base or {}), **per}

    def want_user(self, uid):
        if not (isinstance(uid, str) and re.fullmatch(r"[0-9]{1,12}", uid)):
            return
        with self.lock:
            e = self.users.get(uid)
            if (e and self.clock() < e[0]) or uid in self.uq or len(self.uq) >= self.USER_QUEUE:
                return
            self.uq.append(uid)
            if self.uthread is None or not self.uthread.is_alive():
                self.uthread = threading.Thread(target=self._user_loop, daemon=True)
                self.uthread.start()

    def _user_loop(self):
        while True:
            with self.lock:
                if not self.uq:
                    self.uthread = None
                    return
                uid = self.uq.popleft()
            out, fails = {}, 0
            for kind, url in (("bttv", "https://api.betterttv.net/3/cached/users/twitch/" + uid), ("7tv", "https://7tv.io/v3/users/twitch/" + uid)):
                try:
                    d = self.fetch(url)
                except Exception:
                    d = None
                if d is None:
                    fails += 1
                    continue
                d = d if isinstance(d, dict) else {}
                if kind == "bttv":
                    lst = [x for k in ("channelEmotes", "sharedEmotes") for x in (d.get(k) if isinstance(d.get(k), list) else [])]
                    self._add(out, "bttv", lst, "id", "code", self.BTTV_ID)
                else:
                    st = d.get("emote_set") if isinstance(d.get("emote_set"), dict) else {}
                    self._add(out, "7tv", st.get("emotes") if isinstance(st.get("emotes"), list) else [], "id", "name", self.STV_ID)
            with self.lock:
                self.users[uid] = (self.clock() + (self.USER_TTL if fails == 0 else self.RETRY), out)
                self.users.move_to_end(uid)
                while len(self.users) > self.USER_MAX:
                    self.users.popitem(last=False)
            cb = self.on_user
            if cb:
                try:
                    cb(uid)
                except Exception:
                    pass

    @staticmethod
    def _apply(item, m):
        """Wörter des Textes, die in m stehen, in item["emotes"] eintragen (mit den vorhandenen zusammengeführt, nichts überlappt). Gibt zurück, ob sich etwas änderte."""
        text = item.get("text") or ""
        if not m or not text:
            return False
        found = [[m[w.group()], w.start(), w.end() - 1] for w in re.finditer(r"\S+", text) if w.group() in m]
        if not found:
            return False
        merged = [list(e) for e in item.get("emotes") or []]
        n0 = len(merged)
        for e in found:
            if all(e[2] < o[1] or e[1] > o[2] for o in merged):
                merged.append(e)
        item["emotes"] = sorted(merged, key=lambda e: e[1])[:60]
        return len(merged) != n0

    def mark(self, item):
        """Wörter des Textes, die ein Emote sind, in item["emotes"] eintragen: erst Kanal und global, dann die Liste des Schreibers (wird sie noch geladen, kommt sie später über mark_user)."""
        self._apply(item, self.map)
        uid = item.get("uid")
        if uid:
            self.want_user(uid)                              # holt die öffentliche Liste, falls sie fehlt oder abgelaufen ist
            um = self.user_map(uid)
            if um:
                self._apply(item, um)

    def mark_user(self, item):
        um = self.user_map(item.get("uid") or "")
        return bool(um) and self._apply(item, um)


class TwitchReader:
    """Liest den Chat des eigenen Kanals für den Bereich "Chat" der Oberfläche (IRC über TLS, nur Standardbibliothek, nur Lesen).

    Gelesen wird **anonym** wie von einem Zuschauer (Name "justinfanNNNNN"): Dazu ist kein Token nötig, und der Token des Bot-Kontos kommt so nie in die Nähe
    dieser Verbindung. Der Kanal ist der aus der Twitch-Karte (TwitchStore). Die Verbindung steht nur, solange die Oberfläche den Chat abfragt (poll):
    Kommt IDLE Sekunden lang keine Abfrage, schließt der Hintergrundteil sie wieder (das schont Strom und Mobilfunk). Abbrüche werden mit wachsender
    Wartezeit wiederholt. Die letzten KEEP Nachrichten bleiben im Speicher; poll(since) liefert, was nach der Nummer `since` dazukam.
    Alles, was Twitch schickt, wird vor dem Speichern bereinigt (nur druckbare Zeichen, Längen begrenzt, Farben und Emote-Angaben streng geprüft);
    die Oberfläche setzt es als Text ein, nie als HTML. Im Vorschau-Modus (demo) entstehen Beispielnachrichten, ohne Netz."""
    HOST, PORT = "irc.chat.twitch.tv", 6697
    KEEP = 300
    IDLE = 120.0
    COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
    MID_RE = re.compile(r"[0-9a-fA-F-]{20,40}")
    BADGES = ("broadcaster", "moderator", "vip", "subscriber", "founder", "staff", "partner")        # nur diese Abzeichen zeigt die Oberfläche
    EMOTE_ID_RE = re.compile(r"[A-Za-z0-9_]{1,64}")                 # alte Kennungen sind Zahlen ("25"), neue (Kanal-, Abo-, Follower- und bewegte Emotes) "emotesv2_" plus 32 Zeichen = 41; bis 40 war zu knapp
    NOTICE_KINDS = {"sub": "sub", "resub": "sub", "subgift": "sub", "submysterygift": "sub", "giftpaidupgrade": "sub", "anongiftpaidupgrade": "sub",
                    "raid": "raid", "announcement": "notice", "ritual": "notice"}
    UNESC = {"s": " ", ":": ";", "\\": "\\", "r": "", "n": ""}

    STALE = 100.0                        # Sekunden ohne jede Zeile (auch ohne Antwort auf unser PING): Verbindung gilt als tot, nächster Weg
    PROBE = 40.0                         # nach so vielen stillen Sekunden schickt die Box selbst ein PING (Twitch antwortet mit PONG), sonst fiele ein toter Weg erst nach Minuten auf

    def __init__(self, store, demo=False, host=None, port=None, tls=True, context=None, clock=time.monotonic, wall=time.time, sleep=time.sleep, paths=None, third=None):
        self.paths = paths or ChatPaths()
        self.third = third or ThirdPartyEmotes(self.paths)
        if hasattr(self.third, "on_user"):
            self.third.on_user = self._user_ready             # Liste eines Schreibers ist da: seine Nachrichten im Speicher nachträglich mit Emotes versehen
        self.events = None                  # TwitchEvents (EventSub), wird nach dem Start gesetzt
        self.store, self.demo = store, demo
        self.host, self.port, self.tls, self.context = host or self.HOST, port or self.PORT, tls, context
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.lock = threading.Lock()
        self.items = collections.deque(maxlen=self.KEEP)
        self.next_id = 1
        self.gen = 0
        self.state = "aus"                  # aus | verbinde | ok | fehler
        self.channel = ""
        self.thread = None
        self.last_poll = 0.0
        self.demo_at = 0.0

    # ---- Zeilen von Twitch zerlegen
    @classmethod
    def _tags(cls, raw):
        out = {}
        for part in raw.split(";"):
            k, _, v = part.partition("=")
            if k and len(out) < 60:
                out[k] = re.sub(r"\\(.)", lambda m: cls.UNESC.get(m.group(1), m.group(1)), v)[:600]
        return out

    @staticmethod
    def _clean_map(text, limit):
        """Wie _clean (nicht druckbare Zeichen und Leerraum werden zu einem einzelnen Leerzeichen, Anfang und Ende ohne), aber mit Zuordnung:
        (bereinigter Text, Liste mit der neuen Stelle jedes ursprünglichen Zeichens oder -1)."""
        out, mp, pend = [], [-1] * len(text), False
        for i, ch in enumerate(text):
            if not ch.isprintable() or ch.isspace():
                pend = bool(out)
                continue
            if pend:
                out.append(" ")
                pend = False
            if len(out) < limit:
                mp[i] = len(out)
                out.append(ch)
        return "".join(out)[:limit], mp

    @staticmethod
    def _remap(emotes, mp):
        """Bereiche [id, von, bis] (Stellen im ursprünglichen Text) auf den bereinigten Text umrechnen; was wegfällt, entfällt."""
        out = []
        for eid, a, b in emotes:
            kept = [mp[i] for i in range(a, min(b, len(mp) - 1) + 1) if mp[i] >= 0]
            if kept:
                out.append([eid, kept[0], kept[-1]])
        return out

    @staticmethod
    def _clean(text, limit):
        t = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
        return t[:limit]

    @classmethod
    def _emotes(cls, raw, length):
        """"25:0-4,12-16/1902:6-10" -> [[id, von, bis], ...]; nur gültige Kennungen und Bereiche im Text."""
        out = []
        for chunk in raw.split("/")[:30]:
            eid, _, ranges = chunk.partition(":")
            if not cls.EMOTE_ID_RE.fullmatch(eid):
                continue
            for r in ranges.split(",")[:40]:
                a, _, b = r.partition("-")
                if _ascii_digits(a) and _ascii_digits(b) and int(a) <= int(b) < length:
                    out.append([eid, int(a), int(b)])
        return sorted(out, key=lambda e: e[1])

    def parse(self, line):
        """Eine IRC-Zeile -> Eintrag (dict ohne id/t) oder None. PING und anderes beachtet der Aufrufer."""
        tags = {}
        if line.startswith("@"):
            raw, _, line = line.partition(" ")
            tags = self._tags(raw[1:])
        prefix = ""
        if line.startswith(":"):
            prefix, _, line = line.partition(" ")
        cmd, _, rest = line.partition(" ")
        cmd = cmd.upper()
        if cmd == "CLEARMSG":                                         # eine Nachricht wurde gelöscht
            mid = tags.get("target-msg-id", "")
            return {"type": "del", "meta": True, "mid": mid, "text": "", "emotes": []} if self.MID_RE.fullmatch(mid) else None
        if cmd == "CLEARCHAT":                                        # Timeout, Bann oder der ganze Chat geleert
            uid = tags.get("target-user-id", "")
            dur = tags.get("ban-duration", "")
            return {"type": "clear", "meta": True, "uid": uid if _ascii_digits(uid) and len(uid) <= 20 else "",
                    "seconds": int(dur) if _ascii_digits(dur) and len(dur) <= 8 else 0, "text": "", "emotes": []}
        if cmd not in ("PRIVMSG", "USERNOTICE"):
            return None
        _, _, msg = rest.partition(" :")
        nick = prefix[1:].partition("!")[0]
        name = self._clean(tags.get("display-name") or nick, 40)
        color = tags.get("color", "")
        color = color if self.COLOR_RE.fullmatch(color) else ""
        kind, text = "msg", msg
        if cmd == "PRIVMSG" and text.startswith("\x01ACTION ") and text.endswith("\x01"):
            kind, text = "me", text[8:-1]
        native = self._emotes(tags.get("emotes", ""), len(text))            # Stellen beziehen sich auf den Text, wie Twitch ihn schickt
        text, mp = self._clean_map(text, 500)
        item = {"type": kind, "name": name, "color": color, "text": text, "emotes": self._remap(native, mp)}
        if cmd == "USERNOTICE":
            item["type"] = self.NOTICE_KINDS.get(tags.get("msg-id", ""), "notice")
            item["name"], item["emotes"] = "", []
            system = self._clean(tags.get("system-msg", ""), 300)
            item["text"] = (system + (" – " + text if text else "")) or text
            item["color"] = ""
        bits = tags.get("bits", "")
        if _ascii_digits(bits):
            item["bits"] = min(int(bits), 10_000_000)
        ev = self._event(cmd, tags, item)
        if ev is False:                                              # Einzelgeschenk einer Sammelaktion: die Sammelmeldung zeigt es schon
            return None
        if ev:
            item["ev"] = ev
        if cmd == "PRIVMSG":                                          # Kennungen der Nachricht und des Absenders (für Löschen, Timeout und Bann)
            if self.MID_RE.fullmatch(tags.get("id", "")):
                item["mid"] = tags["id"]
            uid = tags.get("user-id", "")
            if _ascii_digits(uid) and len(uid) <= 20:
                item["uid"] = uid
            item["login"] = self._clean(nick, 25).lower()
            names = [b.partition("/")[0] for b in tags.get("badges", "").split(",")[:20]]
            item["badges"] = [b for b in self.BADGES if b in names]
        if item["text"] and cmd == "PRIVMSG":                         # Emotes von 7TV, BTTV und FFZ (Liste des Kanals wird im Hintergrund geladen)
            self.third.ensure(tags.get("room-id", ""))
            self.third.mark(item)
        sent = tags.get("tmi-sent-ts", "")                           # Zeitpunkt, an dem Twitch die Nachricht angenommen hat (Millisekunden)
        if _ascii_digits(sent) and len(sent) <= 13:
            item["ts"] = int(sent) // 1000
        return item if item["text"] else None

    @staticmethod
    def _num(v, limit=10_000_000):
        return min(int(v), limit) if isinstance(v, str) and _ascii_digits(v) and len(v) <= 9 else 0

    @classmethod
    def _event(cls, cmd, tags, item):
        """Besondere Ereignisse für die auffällige Darstellung: {"k": Art, "n": Zahl} mit k = sub | gift | raid | cheer | announce.
        False = Einzelgeschenk einer Sammelaktion (wird nicht extra gezeigt)."""
        if cmd == "PRIVMSG":
            return {"k": "cheer", "n": item["bits"]} if item.get("bits") else None
        mid = tags.get("msg-id", "")
        if mid in ("sub", "resub"):
            return {"k": "sub", "n": cls._num(tags.get("msg-param-cumulative-months", ""), 1200)}
        if mid == "subgift":
            return False if tags.get("msg-param-community-gift-id") else {"k": "gift", "n": 1}
        if mid == "submysterygift":
            return {"k": "gift", "n": cls._num(tags.get("msg-param-mass-gift-count", ""), 1000) or 1}
        if mid in ("giftpaidupgrade", "anongiftpaidupgrade"):
            return {"k": "sub", "n": 0}
        if mid == "raid":
            return {"k": "raid", "n": cls._num(tags.get("msg-param-viewerCount", ""), 10_000_000)}
        if mid == "announcement":
            return {"k": "announce", "n": 0}
        return None

    def _add(self, item, gen=None):
        with self.lock:
            if gen is not None and gen != self.gen:
                return                                      # Nachzügler eines alten Fadens
            item["id"], item["t"] = self.next_id, int(item.get("ts") or self.wall())
            self.next_id += 1
            self.items.append(item)

    def _user_ready(self, uid):
        patched = []
        with self.lock:
            for it in self.items:
                if it.get("uid") == uid and it.get("mid") and it.get("type") in ("msg", "me") and self.third.mark_user(it):
                    patched.append((it["mid"], [list(e) for e in it["emotes"]]))
        for mid, em in patched:
            self._add({"type": "emotes", "meta": True, "mid": mid, "text": "", "emotes": em})       # laufende Oberflächen ersetzen die Emotes der schon gezeigten Zeile

    # ---- Abfrage durch die Oberfläche
    def poll(self, since=0):
        ch = ""
        if self.store:
            ch = self.store.channel_name() if hasattr(self.store, "channel_name") else self.store.data.get("channel", "")
        now = self.clock()
        with self.lock:
            self.last_poll = now
        if self.demo:
            self._demo(now)
            ch = ch or "demo"
            state = "ok"
        elif not ch:
            return {"state": "kein-kanal", "channel": "", "items": [], "last": 0}
        else:
            self._ensure(ch)
            state = self.state
            if self.events is not None:
                self.events.ensure()                        # Follows und Kanalpunkte (nur mit Rechten, nur eigener Kanal, nur solange abgefragt wird)
        with self.lock:
            last = self.next_id - 1
            if since > last:
                since = 0                           # die Box wurde neu gestartet: Verlauf von vorn
            items = [i for i in self.items if i["id"] > since]
        return {"state": state, "channel": ch, "items": items, "last": last}

    def _ensure(self, ch):
        with self.lock:
            alive = self.thread is not None and self.thread.is_alive() and self.channel == ch
            if alive:
                return
            if self.channel != ch:
                self.items.clear()
            self.channel = ch
            self.state = "verbinde"
            self.gen += 1                                   # jeder Start zählt: ein alter Faden (anderer Kanal, später wieder derselbe) beendet sich selbst
            gen = self.gen
            t = threading.Thread(target=self._run, args=(ch, gen), daemon=True)
            self.thread = t
        t.start()

    def _idle(self):
        return self.clock() - self.last_poll > self.IDLE

    def _mine(self, ch, gen):
        return self.gen == gen and self.channel == ch

    def _run(self, ch, gen):
        wait = 3.0
        while not self._idle() and self._mine(ch, gen):
            began = self.clock()
            try:
                self._session(ch, gen)
            except Exception:
                with self.lock:
                    if self._mine(ch, gen):
                        self.state = "fehler"
            if self.clock() - began > 30:
                wait = 3.0                                  # die Sitzung hat lange gehalten: schnell neu verbinden, nicht mit dem Wert vom letzten Ausfall
            end = self.clock() + wait
            while self.clock() < end and not self._idle() and self._mine(ch, gen):
                self.sleep(0.5)
            wait = min(wait * 2, 60.0)
        with self.lock:
            if self._mine(ch, gen):
                self.state = "aus"

    def _session(self, ch, gen):
        sock, src = self.paths.connect(self.host, self.port, timeout=8)
        ok = False
        try:
            if self.tls:
                sock = (self.context or ssl.create_default_context()).wrap_socket(sock, server_hostname=self.host)
            nick = "justinfan%05d" % random.randint(0, 99999)
            sock.sendall(("PASS SCHMOOPIIE\r\nNICK %s\r\n" % nick).encode("ascii"))
            rd = _IrcLines(sock, self.clock)
            until = self.clock() + 15
            while True:                                     # auf die Begrüßung warten
                line = rd.get(until)
                if line is None:
                    raise OSError
                cmd = line.partition(" ")[2].partition(" ")[0] if line.startswith(":") else line.partition(" ")[0]
                if cmd == "001":
                    break
                if cmd == "PING":
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
            sock.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nJOIN #" + ch.encode("ascii") + b"\r\n")
            ok = True
            with self.lock:
                if self._mine(ch, gen):
                    self.state = "ok"
            heard = probed = self.clock()
            while not self._idle() and self._mine(ch, gen):
                line = rd.get(self.clock() + 5)
                if line is None:
                    if self.clock() - heard > self.PROBE and self.clock() - probed > self.PROBE:
                        sock.sendall(b"PING :pipbox\r\n")
                        probed = self.clock()
                    if self.clock() - heard > self.STALE:
                        self.paths.fail(src, 300.0)                          # still geworden: diesen Weg meiden, über den nächsten neu verbinden
                        raise OSError("still")
                    continue
                heard = self.clock()
                if line.startswith("PING"):
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                    continue
                if TwitchChat._parse(line)[0] == "RECONNECT":            # nur der Befehl, nie ein Name in den Tags
                    return
                try:
                    item = self.parse(line)
                    if item:
                        self._add(item, gen)
                except Exception:
                    continue                                                   # eine unlesbare Zeile beendet nie die Sitzung
        except OSError:
            if not ok:
                self.paths.fail(src, 120.0)                                    # verbunden, aber TLS/Begrüßung hängt: diesen Weg eine Weile meiden
            raise
        finally:
            try:
                sock.close()
            except OSError:
                pass

    # ---- Vorschau-Modus: Beispielnachrichten
    DEMO = [("Anna_Streams", "#FF4500", "Hallo aus dem Chat! Das Bild ist heute richtig scharf"), ("kamerafreak", "#1E90FF", "Welche Kamera ist das gerade?"),
            ("Moderator_Max", "#2E8B57", "Bitte keine Werbung im Chat"), ("nightowl", "", "gg 🎉"), ("Lena", "#DAA520", "Der Ton ist super")]

    def _demo(self, now):
        if now - self.demo_at < 2.0 and self.items:
            return
        self.demo_at = now
        n = self.next_id
        if n % 11 == 0:
            self._add({"type": "sub", "name": "", "color": "", "text": "Anna_Streams hat den Kanal abonniert (Stufe 1)", "emotes": [], "ev": {"k": "sub", "n": 0}})
        elif n % 13 == 0:
            self._add({"type": "raid", "name": "", "color": "", "text": "Max_Raider schickt 42 Zuschauer zu dir", "emotes": [], "ev": {"k": "raid", "n": 42}})
        elif n % 17 == 0:
            self._add({"type": "msg", "name": "Lena", "color": "#DAA520", "text": "cheer500 Weiter so!", "emotes": [], "bits": 500, "ev": {"k": "cheer", "n": 500},
                       "mid": "%08x-0000-4000-8000-000000000000" % n, "uid": "102", "login": "lena", "badges": ["vip", "subscriber"]})
        elif n % 19 == 0:
            self._add({"type": "sub", "name": "", "color": "", "text": "nightowl verschenkt 5 Abos an die Community", "emotes": [], "ev": {"k": "gift", "n": 5}})
        elif n % 23 == 0:
            self._add({"type": "notice", "name": "", "color": "", "text": "Anna_Streams", "emotes": [], "ev": {"k": "follow", "n": 0}})
        elif n % 29 == 0:
            self._add({"type": "notice", "name": "", "color": "", "text": "Lena: Trink etwas Wasser", "emotes": [], "ev": {"k": "points", "n": 500}})
        elif n % 7 == 0 and (self.third.ensure("") or True) and self.third.snapshot():             # Vorschau: echte globale Emotes von 7TV/BTTV/FFZ
            names = sorted(self.third.snapshot())
            a, b = names[n % len(names)], names[(n * 7) % len(names)]
            item = {"type": "msg", "name": "emote_fan", "color": "#8A2BE2", "text": "Der Chat zeigt jetzt %s und %s" % (a, b), "emotes": [],
                    "mid": "%08x-0000-4000-8000-000000000000" % n, "uid": "103", "login": "emote_fan", "badges": []}
            self.third.mark(item)
            self._add(item)
        else:
            i = n % len(self.DEMO)
            name, color, text = self.DEMO[i]
            badges = {"Moderator_Max": ["moderator"], "Anna_Streams": ["subscriber"], "Lena": ["vip", "subscriber"]}.get(name, [])
            self._add({"type": "msg", "name": name, "color": color, "text": text, "emotes": [], "mid": "%08x-0000-4000-8000-000000000000" % n,
                       "uid": str(100 + i), "login": name.lower(), "badges": badges})


class TwitchMod:
    """Moderation im Chat des eigenen Kanals über die Twitch-Schnittstelle (Helix): Nachricht löschen, Timeout, Bann und Bann aufheben. Die Befehle "/ban",
    "/timeout" und "/unban" im IRC gibt es bei Twitch nicht mehr, darum läuft alles über die Schnittstelle mit dem Zugangsschlüssel des angemeldeten Kontos.

    Nötig sind die Rechte moderator:manage:banned_users und moderator:manage:chat_messages; der Nutzer bestätigt sie nur, wenn er die Moderation einschaltet
    (TwitchLogin.start(mod=True)). Das angemeldete Konto muss Streamer oder Moderator des Kanals sein. Namen werden über "Get Users" in Kennungen aufgelöst
    (zwischengespeichert). Fehler kommen als kurze deutsche Meldung (ValueError); Tokens stehen nie darin. Im Vorschau-Modus (demo) geschieht nichts im Netz."""
    API = "https://api.twitch.tv/helix/"
    MAX_SECONDS = 1209600                       # 14 Tage, das Höchste, was Twitch für einen Timeout erlaubt
    ERR_NET = "Keine Verbindung zu Twitch"
    ERR_LOGIN = "Zuerst mit Twitch anmelden"
    ERR_SCOPE = "Moderation ist noch nicht eingeschaltet"
    ERR_AUTH = "Anmeldung abgelaufen, bitte neu anmelden"
    ERR_FORBIDDEN = "Dazu fehlt die Berechtigung (Moderator im Kanal?)"
    ERR_OTHER = "Twitch hat die Anfrage nicht angenommen"
    ERR_UNSURE = "Verbindung gestört, unklar ob es angekommen ist"
    ERR_RIGHTS = "Dafür fehlt ein Recht: bitte „Moderation einschalten“ und bei Twitch bestätigen"
    ERR_OWNER = "Das darf nur der Kanalinhaber (im eigenen Kanal)"
    ERR_BROWSER = "Das geht nur in dem Browser, in dem sich der Streamer bei Twitch angemeldet hat (dort einmal neu anmelden)"
    ERR_NOTFOUND = "Twitch findet das nicht (läuft der Stream?)"
    # Befehl -> nötiges Recht; die mit OWNER_ONLY gelten nur im eigenen Kanal (Kanalinhaber), die anderen auch für Moderatoren
    CMD_SCOPE = {"vip": "channel:manage:vips", "unvip": "channel:manage:vips", "mod": "channel:manage:moderators", "unmod": "channel:manage:moderators",
                 "clear": "moderator:manage:chat_messages", "slow": "moderator:manage:chat_settings", "slowoff": "moderator:manage:chat_settings",
                 "followers": "moderator:manage:chat_settings", "followersoff": "moderator:manage:chat_settings",
                 "subscribers": "moderator:manage:chat_settings", "subscribersoff": "moderator:manage:chat_settings",
                 "emoteonly": "moderator:manage:chat_settings", "emoteonlyoff": "moderator:manage:chat_settings",
                 "announce": "moderator:manage:announcements", "raid": "channel:manage:raids", "unraid": "channel:manage:raids",
                 "marker": "channel:manage:broadcast", "title": "channel:manage:broadcast", "game": "channel:manage:broadcast"}
    OWNER_ONLY = ("vip", "unvip", "mod", "unmod", "raid", "unraid", "marker", "title", "game")
    SETTINGS = {"slowoff": ("slow_mode", "Langsamer Modus aus"), "followersoff": ("follower_mode", "Nur Follower aus"),
                "subscribersoff": ("subscriber_mode", "Nur Abonnenten aus"), "emoteonlyoff": ("emote_mode", "Nur Emotes aus"),
                "subscribers": ("subscriber_mode", "Nur Abonnenten an"), "emoteonly": ("emote_mode", "Nur Emotes an")}

    def __init__(self, store, account, api_base=None, client_id=None, demo_reader=None, demo=False, paths=None):
        self.store, self.account = store, account
        self.paths = paths or getattr(account, "paths", None) or ChatPaths()
        self.api = api_base or self.API
        self.client_id = client_id or TwitchLogin.CLIENT_ID
        self.demo, self.demo_reader = demo, demo_reader
        self.ids = {}
        self.lock = threading.Lock()

    def _call(self, method, path, params=None, body=None, once=False, timeout=15.0, token=None, prefer=False, accept=()):
        import urllib.error
        import urllib.parse
        import urllib.request
        tok = token or self.account.token()
        if not tok:
            raise ValueError(self.ERR_LOGIN)
        url = self.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        hdr = {"Authorization": "Bearer " + tok, "Client-Id": self.client_id, "User-Agent": "irl4you-box"}
        if data is not None:
            hdr["Content-Type"] = "application/json"
        status, raw = self.paths.request(method, url, data, hdr, timeout=timeout, once=once, prefer=prefer)
        if status == 401 and token is None and self.account.refresh_now():                  # Zugang war abgelaufen (zum Beispiel falsche Uhr): einmal erneuern, einmal wiederholen
            hdr["Authorization"] = "Bearer " + self.account.token()
            status, raw = self.paths.request(method, url, data, hdr, timeout=timeout, once=once, prefer=prefer)
        if status == 0:
            raise ValueError(self.ERR_NET)
        if status == -1:
            raise ValueError(self.ERR_UNSURE)
        try:
            out = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            out = {}
        out = out if isinstance(out, dict) else {}
        if status in (200, 201, 202, 204) or status in accept:
            return out
        if status == 401:
            raise ValueError(self.ERR_AUTH)
        if status == 403:
            raise ValueError(self.ERR_FORBIDDEN)
        if status == 404:
            raise ValueError(self.ERR_NOTFOUND)
        msg = TwitchReader._clean(str(out.get("message", "")), 120)
        raise ValueError(msg if status in (400, 409, 422, 425) and msg else self.ERR_OTHER)

    def is_moderator(self, uid):
        """Ist dieses Konto Moderator im Kanal? Fragt mit dem Konto des Streamers (nur im eigenen Kanal möglich). None, wenn das nicht geht oder Twitch nicht antwortet."""
        if self.demo:
            return True
        try:
            bc, me = self._context()
            if bc != me or not _ascii_digits(str(uid)):
                return None
            if "channel:manage:moderators" not in (self.account.status().get("scopes") or []):
                return None
            rows = self._call("GET", "moderation/moderators", {"broadcaster_id": bc, "user_id": str(uid)}).get("data")
        except ValueError:
            return None
        return bool(isinstance(rows, list) and rows)

    def view(self, account):
        """Dieselbe Moderation mit einem anderen Konto (zum Beispiel dem eines Moderators in seinem Browser): gleiche Wege, gleicher Kanal, eigene Rechte."""
        import copy
        v = copy.copy(self)
        v.account = account
        return v

    def _context(self):
        """(Kanal-Kennung, Moderator-Kennung): der Kanal aus den Einstellungen, der Moderator ist das angemeldete Konto."""
        if not self.account.ready():
            raise ValueError(self.ERR_LOGIN)
        if not self.account.status().get("mod"):
            raise ValueError(self.ERR_SCOPE)
        me, mylogin = self.account.user_id(), self.account.login()
        ch = (self.store.channel_name() or mylogin).lower()
        return (me if ch == mylogin else self.lookup(ch)), me

    def lookup(self, login):
        """Kennung eines Kontos aus dem Namen (Get Users)."""
        login = str(login).strip().lstrip("@").lower()
        if not TwitchStore.NAME_RE.fullmatch(login):
            raise ValueError("Ungültiger Name")
        with self.lock:
            if login in self.ids:
                return self.ids[login]
        d = self._call("GET", "users", {"login": login})
        rows = d.get("data") if isinstance(d.get("data"), list) else []
        if not rows or not isinstance(rows[0], dict) or not str(rows[0].get("id", "")).isdigit():
            raise ValueError("Dieses Konto gibt es nicht")
        with self.lock:
            if len(self.ids) > 200:
                self.ids.clear()
            self.ids[login] = str(rows[0]["id"])
            return self.ids[login]

    def do(self, req, owner_key=None):
        """req: {"action": "delete"|"timeout"|"ban"|"unban", "message_id" | "user_id" | "user", "seconds", "reason"} oder ein Befehl aus CMD_SCOPE.
        owner_key: Streamer-Schlüssel des Browsers; ohne ihn gehen die Befehle des Kanalinhabers (OWNER_ONLY) nicht."""
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        act = req.get("action")
        if act in self.CMD_SCOPE:
            return self._extra(act, req, owner_key)
        if act not in ("delete", "timeout", "ban", "unban"):
            raise ValueError("Ungültige Anfrage")
        if self.demo:
            return self._demo(act, req)
        bc, me = self._context()
        base = {"broadcaster_id": bc, "moderator_id": me}
        if act == "delete":
            mid = req.get("message_id")
            if not isinstance(mid, str) or not TwitchReader.MID_RE.fullmatch(mid):
                raise ValueError("Ungültige Anfrage")
            self._call("DELETE", "moderation/chat", dict(base, message_id=mid))
            return {"ok": True, "action": act, "message": "Gelöscht"}
        uid = self._target(req)
        if act == "unban":
            self._call("DELETE", "moderation/bans", dict(base, user_id=uid))
            return {"ok": True, "action": act, "user_id": uid, "message": "Bann aufgehoben"}
        data = {"user_id": uid}
        reason = TwitchReader._clean(str(req.get("reason") or ""), 200)
        if reason:
            data["reason"] = reason
        if act == "timeout":
            try:
                sec = int(req.get("seconds", 600))
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Dauer: Zahl in Sekunden")
            data["duration"] = max(1, min(self.MAX_SECONDS, sec))
        self._call("POST", "moderation/bans", base, {"data": data})
        return {"ok": True, "action": act, "user_id": uid, "message": "Timeout" if act == "timeout" else "Gebannt"}

    def _extra(self, act, req, owner_key=None):
        """Befehle über die Twitch-Schnittstelle: VIP, Moderatoren, Chat leeren und -Einstellungen, Ankündigung, Raid, Marker, Titel und Kategorie."""
        if self.demo:
            return self._demo(act, req)
        bc, me = self._context()
        if act in self.OWNER_ONLY and bc != me:
            raise ValueError(self.ERR_OWNER)                          # Moderatoren (oder fremder Kanal): nichts vom Kanalinhaber
        if self.CMD_SCOPE[act] not in (self.account.status().get("scopes") or []):
            raise ValueError(self.ERR_RIGHTS)
        if act in self.OWNER_ONLY and not self.account.is_owner(owner_key):
            raise ValueError(self.ERR_BROWSER)
        base = {"broadcaster_id": bc, "moderator_id": me}
        text = TwitchReader._clean(str(req.get("text") or ""), 500).strip()
        if act in ("vip", "unvip", "mod", "unmod"):
            path = "channels/vips" if act in ("vip", "unvip") else "moderation/moderators"
            self._call("POST" if act in ("vip", "mod") else "DELETE", path, {"broadcaster_id": bc, "user_id": self._target(req)})
            msg = {"vip": "VIP vergeben", "unvip": "VIP entfernt", "mod": "Moderator ernannt", "unmod": "Moderator entfernt"}[act]
        elif act == "clear":
            self._call("DELETE", "moderation/chat", base)
            msg = "Chat geleert"
        elif act in ("slow", "followers"):
            try:
                n = int(req.get("number")) if req.get("number") is not None else None
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Die Zahl ist ungültig")
            if act == "slow":
                n = 30 if n is None else max(3, min(120, n))
                body, msg = {"slow_mode": True, "slow_mode_wait_time": n}, "Langsamer Modus an (%d Sekunden)" % n
            else:
                n = 0 if n is None else max(0, min(129600, n))
                body, msg = {"follower_mode": True, "follower_mode_duration": n}, ("Nur Follower an" if n == 0 else "Nur Follower an (seit %d Minuten)" % n)
            self._call("PATCH", "chat/settings", base, body)
        elif act in self.SETTINGS:
            key, msg = self.SETTINGS[act]
            self._call("PATCH", "chat/settings", base, {key: not act.endswith("off")})
        elif act == "announce":
            if not text:
                raise ValueError("Der Text fehlt")
            self._call("POST", "chat/announcements", base, {"message": text, "color": "primary"})
            msg = "Ankündigung gesendet"
        elif act == "raid":
            self._call("POST", "raids", {"from_broadcaster_id": bc, "to_broadcaster_id": self._target(req)})
            msg = "Raid gestartet"
        elif act == "unraid":
            self._call("DELETE", "raids", {"broadcaster_id": bc})
            msg = "Raid abgebrochen"
        elif act == "marker":
            self._call("POST", "streams/markers", None, {"user_id": bc, "description": text[:140]})
            msg = "Marker gesetzt"
        elif act == "title":
            if not text:
                raise ValueError("Der Text fehlt")
            self._call("PATCH", "channels", {"broadcaster_id": bc}, {"title": text[:140]})
            msg = "Titel geändert"
        else:                                                                               # game
            if not text:
                raise ValueError("Der Name der Kategorie fehlt")
            rows = self._call("GET", "games", {"name": text[:140]}).get("data")
            row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
            if not str(row.get("id", "")).isdigit():
                raise ValueError("Diese Kategorie gibt es nicht")
            self._call("PATCH", "channels", {"broadcaster_id": bc}, {"game_id": str(row["id"])})
            msg = "Kategorie geändert"
        return {"ok": True, "action": act, "message": msg}

    def _target(self, req):
        uid = req.get("user_id")
        if isinstance(uid, str) and _ascii_digits(uid) and len(uid) <= 20:
            return uid
        if isinstance(req.get("user"), str):
            return self.lookup(req["user"])
        raise ValueError("Ungültige Anfrage")

    def _demo(self, act, req):
        if act in self.CMD_SCOPE:
            return {"ok": True, "action": act, "message": "Demo: Befehl ausgeführt"}
        if act == "delete" and self.demo_reader is not None and isinstance(req.get("message_id"), str):
            self.demo_reader._add({"type": "del", "meta": True, "mid": req["message_id"], "text": "", "emotes": []})
        if act in ("timeout", "ban") and self.demo_reader is not None and _ascii_digits(str(req.get("user_id") or "")):
            self.demo_reader._add({"type": "clear", "meta": True, "uid": str(req["user_id"]), "seconds": int(req.get("seconds") or 0) if act == "timeout" else 0,
                                   "text": "", "emotes": []})
        return {"ok": True, "action": act, "user_id": str(req.get("user_id") or "1"),
                "message": {"delete": "Gelöscht", "timeout": "Timeout", "ban": "Gebannt", "unban": "Bann aufgehoben"}[act]}


class TwitchSender:
    """Nachrichten des Streamers aus der Oberfläche in den Chat (über das angemeldete Twitch-Konto, sonst Bot-Konto und Token von Hand). Höchstens eine
    Nachricht je Sekunde. Befehle mit "/" oder "." am Anfang werden nie als Text gesendet: "/ban Name [Grund]", "/timeout Name [Sekunden] [Grund]",
    "/unban Name" und die weiteren Befehle (VIP, Moderatoren, Chat leeren und -Einstellungen, Ankündigung, Raid, Marker, Titel, Kategorie) gehen als Moderation
    über die Twitch-Schnittstelle (TwitchMod), alles andere wird abgelehnt."""
    COMMANDS = ("ban", "timeout", "unban")
    NAMED = ("vip", "unvip", "mod", "unmod", "raid")                     # Befehl Name
    PLAIN = ("clear", "slowoff", "followersoff", "subscribers", "subscribersoff", "emoteonly", "emoteonlyoff", "unraid")   # Befehl ohne Angabe
    NUMBER = ("slow", "followers")                                       # Befehl [Zahl]: Sekunden beziehungsweise Minuten
    TEXT = ("announce", "title", "game")                                 # Befehl Text (Pflicht)
    HELP = ("Befehle: /ban, /timeout, /unban, /vip, /unvip, /mod, /unmod, /clear, /slow, /slowoff, /followers, /followersoff, /subscribers, /subscribersoff, "
            "/emoteonly, /emoteonlyoff, /announce, /raid, /unraid, /marker, /title, /game")
    MIN_GAP = 1.0

    def __init__(self, store, chat=None, clock=time.monotonic, demo_reader=None, mod=None, helix=None):
        self.store, self.chat, self.clock, self.mod, self.helix = store, chat or TwitchChat(), clock, mod, helix
        self.demo_reader = demo_reader              # nur im Vorschau-Modus: die Nachricht erscheint im Beispiel-Chat, nichts geht ins Netz
        self.last = -1e9
        self.lock = threading.Lock()

    def _command(self, t, owner_key=None, mod=None):
        mod = mod or self.mod
        parts = t[1:].split()
        cmd = parts[0].lower() if t[0] == "/" and parts else ""
        known = self.COMMANDS + self.NAMED + self.PLAIN + self.NUMBER + self.TEXT + ("marker",)
        if cmd not in known or mod is None:
            raise ValueError(self.HELP)
        if cmd in self.COMMANDS + self.NAMED + self.TEXT and len(parts) < 2:
            raise ValueError(self.HELP)
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        if cmd in self.COMMANDS:
            req = {"action": cmd, "user": parts[1]}
            rest = parts[2:]
            if cmd == "timeout" and rest and _ascii_digits(rest[0]):
                req["seconds"] = int(rest[0])
                rest = rest[1:]
            if cmd != "unban" and rest:
                req["reason"] = " ".join(rest)
            out = mod.do(req, owner_key)
            return {"ok": True, "message": "%s: %s" % (out.get("message", ""), parts[1].lstrip("@"))}
        req = {"action": cmd}
        if cmd in self.NAMED:
            req["user"] = parts[1]
        elif cmd in self.NUMBER and len(parts) > 1:
            if not _ascii_digits(parts[1]):
                raise ValueError("Die Zahl ist ungültig")
            req["number"] = int(parts[1])
        elif cmd in self.TEXT or cmd == "marker":
            req["text"] = " ".join(parts[1:])
        out = mod.do(req, owner_key)
        who = ": " + parts[1].lstrip("@") if cmd in self.NAMED else ""
        return {"ok": True, "message": out.get("message", "") + who}

    def say_as(self, mod, helix, text, owner_key=None, commands=True):
        """Nachricht oder Befehl mit einem anderen Konto als dem der Box (Moderator in seinem Browser): Befehle über dessen Moderation, Text über dessen Zugang zur
        Twitch-Schnittstelle (nie über das Konto des Streamers, nie über IRC)."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Nachricht fehlt")
        t = text.strip()
        if t[0] in "/.":
            if not commands:
                raise ValueError("Befehle gibt es nur für den Streamer und Moderatoren")
            return self._command(t, owner_key, mod)
        if not helix.account.ready():
            raise ValueError("Zuerst mit Twitch anmelden")
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        if self.demo_reader is not None:
            self.demo_reader._add({"type": "msg", "name": helix.account.login(), "color": "#9146FF", "text": TwitchChat.clean_text(t) or "", "emotes": []})
            return {"ok": True, "message": TwitchChat.OK}
        t = TwitchChat.clean_text(t)
        if not t:
            raise ValueError("Nachricht ungültig")
        if not helix.usable():
            raise ValueError("Für das Schreiben fehlt ein Recht: bitte neu anmelden")
        return {"ok": True, "message": helix.send(self.store.channel_name(), t)}

    def say(self, text, owner_key=None):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Nachricht fehlt")
        t = text.strip()
        if t[0] in "/.":
            return self._command(t, owner_key)
        cfg = self.store.settings()
        if not (cfg["login"] and cfg["token"] and cfg["channel"]):
            raise ValueError("Zuerst mit Twitch anmelden")
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        if self.demo_reader is not None:
            self.demo_reader._add({"type": "msg", "name": cfg["login"], "color": "#9146FF", "text": TwitchChat.clean_text(t) or "", "emotes": []})
            return {"ok": True, "message": TwitchChat.OK}
        t = TwitchChat.clean_text(t)
        if not t:
            raise ValueError("Nachricht ungültig")
        if self.helix is not None and self.helix.usable():             # über die Twitch-Schnittstelle: mit Rückmeldung, ob die Nachricht wirklich gesendet wurde
            return {"ok": True, "message": self.helix.send(cfg["channel"], t)}
        ok, msg = self.chat.send(cfg["login"], cfg["token"], cfg["channel"], t)
        if not ok:
            raise ValueError(msg)
        return {"ok": True, "message": msg}


class HelixChat:
    """Chat senden über die Twitch-Schnittstelle (POST chat/messages, Recht user:write:chat). Twitch antwortet auch dann mit 200, wenn die Nachricht verworfen wurde
    (Spam- oder Tempofilter, nur Abonnenten, langsamer Modus ...): darum zählt nur "is_sent", sonst steht der Grund von Twitch in der Meldung. Ohne das Recht
    (ältere Anmeldung) bleibt das Senden über IRC (TwitchChat), bis man sich neu anmeldet. Alle Aufrufe laufen über TwitchMod._call (Wege, Erneuern bei 401)."""
    ERR_DROP = "Twitch hat die Nachricht nicht gesendet"
    ERR_FORBIDDEN = "Twitch erlaubt das Senden hier gerade nicht (zum Beispiel nur für Abonnenten oder Follower)"

    def __init__(self, account, mod):
        self.account, self.mod = account, mod

    def usable(self):
        try:
            return bool(self.account.ready() and "user:write:chat" in (self.account.status().get("scopes") or []))
        except Exception:
            return False

    def send(self, channel, text):
        me = self.account.user_id()
        if not me:
            raise ValueError("Zuerst mit Twitch anmelden")
        ch = str(channel or "").strip().lstrip("#").lower()
        bid = me if (not ch or ch == self.account.login()) else self.mod.lookup(ch)
        try:
            d = self.mod._call("POST", "chat/messages", None, {"broadcaster_id": bid, "sender_id": me, "message": text}, once=True)
        except ValueError as e:
            if str(e) == self.mod.ERR_FORBIDDEN:
                raise ValueError(self.ERR_FORBIDDEN)
            raise                                                          # auch ERR_UNSURE (gesendet, Antwort verloren): "unklar, ob es angekommen ist"
        rows = d.get("data") if isinstance(d.get("data"), list) else []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        if row.get("is_sent") is True:
            return TwitchChat.OK
        why = row.get("drop_reason") if isinstance(row.get("drop_reason"), dict) else {}
        msg = TwitchReader._clean(str(why.get("message") or ""), 120)
        raise ValueError(self.ERR_DROP + (": " + msg if msg else ""))


class WebSocketLite:
    """Kleiner WebSocket-Client nach RFC 6455 (nur Standardbibliothek) für EventSub: Aufbau mit Prüfung der Antwort, Text-Nachrichten (auch in Teilen), Ping wird mit
    Pong beantwortet, Schließen beendet. Keine Erweiterungen, der Server darf nicht maskieren, jede Nachricht höchstens MAX Byte. Zeitüberschreitungen verlieren
    keine halb gelesene Nachricht."""
    GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    MAX = 1 << 20

    def __init__(self, sock, clock=time.monotonic):
        self.sock, self.clock = sock, clock
        self.buf = bytearray()
        self.parts, self.first = bytearray(), None
        self.last_rx = clock()                                              # wann zuletzt Daten von der Gegenstelle kamen (auch Ping zählt)

    @classmethod
    def handshake(cls, sock, host, path, clock=time.monotonic, timeout=10.0):
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(b"GET " + path.encode("ascii") + b" HTTP/1.1\r\nHost: " + host.encode("ascii") + b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: "
                     + key.encode("ascii") + b"\r\nSec-WebSocket-Version: 13\r\nUser-Agent: irl4you-box\r\n\r\n")
        buf, deadline = b"", clock() + timeout
        while b"\r\n\r\n" not in buf:
            left = deadline - clock()
            if left <= 0 or len(buf) > 16384:
                raise OSError("ws-handshake-timeout")
            sock.settimeout(left)
            chunk = sock.recv(4096)
            if not chunk:
                raise EOFError("ws-eof")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if not re.match(r"HTTP/1\.[01] 101\b", lines[0]):
            raise OSError("ws-no-upgrade")
        hdr = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
        want = base64.b64encode(hashlib.sha1(key.encode("ascii") + cls.GUID).digest()).decode()
        if hdr.get("sec-websocket-accept") != want:
            raise OSError("ws-bad-accept")
        ws = cls(sock, clock)
        ws.buf += rest
        return ws

    @staticmethod
    def frame(opcode, payload=b""):
        mask, n = os.urandom(4), len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + n.to_bytes(2, "big") if n < 65536 else bytes([0x80 | 127]) + n.to_bytes(8, "big"))
        return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def close(self):
        try:
            self.sock.sendall(self.frame(8, b"\x03\xe8"))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _need(self, n, deadline):
        while len(self.buf) < n:
            left = deadline - self.clock()
            if left <= 0:
                return False
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return False
            if not chunk:
                raise EOFError("ws-eof")
            self.last_rx = self.clock()
            self.buf += chunk
        return True

    def recv_message(self, timeout):
        """Die nächste Text-Nachricht oder None, wenn in `timeout` Sekunden keine vollständige da ist. EOFError: Gegenstelle hat geschlossen; OSError: Verstoß."""
        deadline = self.clock() + timeout
        while True:
            if self.clock() > deadline:
                return None                                              # auch ein Strom von Steuerrahmen hält die Schleife nicht fest
            if not self._need(2, deadline):
                return None
            b0, b1 = self.buf[0], self.buf[1]
            fin, op = b0 & 0x80, b0 & 0x0f
            if (b0 & 0x70) or (b1 & 0x80):
                raise OSError("ws-protocol")            # Erweiterungen und maskierte Server-Rahmen gibt es nicht
            ln, off = b1 & 0x7f, 2
            if ln == 126:
                if not self._need(4, deadline):
                    return None
                ln, off = int.from_bytes(self.buf[2:4], "big"), 4
            elif ln == 127:
                if not self._need(10, deadline):
                    return None
                ln, off = int.from_bytes(self.buf[2:10], "big"), 10
            if ln > self.MAX or (op >= 8 and (not fin or ln > 125)):
                raise OSError("ws-protocol")
            if not self._need(off + ln, deadline):
                return None
            payload = bytes(self.buf[off:off + ln])
            del self.buf[:off + ln]
            if op == 9:
                self.sock.sendall(self.frame(10, payload))
                continue
            if op == 10:
                continue
            if op == 8:
                raise EOFError("ws-closed")
            if op in (1, 2):
                if self.first is not None:
                    raise OSError("ws-protocol")
                self.first = op
            elif op == 0:
                if self.first is None:
                    raise OSError("ws-protocol")
            else:
                raise OSError("ws-protocol")
            self.parts += payload
            if len(self.parts) > self.MAX:
                raise OSError("ws-too-big")
            if fin:
                data, kind = bytes(self.parts), self.first
                self.parts, self.first = bytearray(), None
                if kind != 1:
                    raise OSError("ws-not-text")
                return data.decode("utf-8")


class TwitchEvents:
    """Live-Ereignisse des eigenen Kanals über EventSub (WebSocket, Twitch-Anleitung): Follows und Kanalpunkte-Einlösungen. Subs, Geschenk-Abos, Raids und Cheers liest der
    Chat schon vollständiger (EventSub meldet zum Beispiel Resubs ohne Text nicht), darum kommen sie weiter aus dem Chat und werden nicht doppelt gezeigt.

    Ablauf: verbinden (über die Sendewege wie der Chat), auf session_welcome warten, dann in einem eigenen Faden (damit Ping und Keepalive weiterlaufen) die Abonnements
    anlegen, je eines für sich: fehlt ein Recht, fällt nur dieses weg. Benachrichtigungen werden über message_id entdoppelt (Twitch liefert "mindestens einmal"). Bleibt
    die Gegenstelle länger als das 1,5-fache des Keepalive-Werts still, wird neu verbunden; bei session_reconnect geht es zur genannten Adresse, ohne neu zu abonnieren.
    Die Verbindung steht nur, solange die Oberfläche den Chat abfragt, nur für den eigenen Kanal und nur mit den nötigen Rechten. Der Zugangsschlüssel kommt jedes Mal
    frisch aus TwitchLogin (wird erneuert) und steht nie in einem Protokoll."""
    HOST, PORT, PATH = "eventsub.wss.twitch.tv", 443, "/ws?keepalive_timeout_seconds=30"
    SUBS = (("channel.follow", "2", "moderator:read:followers"),
            ("channel.channel_points_custom_reward_redemption.add", "1", "channel:read:redemptions"))
    ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,100}")
    KEEP_MIN = 10.0                       # kleinster erlaubter Keepalive-Wert in Sekunden (Tests setzen ihn kleiner)
    RETRY_FIRST = 5.0                     # Wartezeit vor dem ersten neuen Versuch (danach verdoppelt, höchstens 60 s)
    SUB_DELAYS = (0.0, 5.0, 15.0)         # Abonnieren: sofort, dann was fehlt nach 5 und nach weiteren 15 Sekunden noch einmal

    def __init__(self, store, account, mod, reader, paths=None, host=None, port=None, tls=True, clock=time.monotonic, sleep=time.sleep):
        self.store, self.account, self.mod, self.reader = store, account, mod, reader
        self.paths = paths or getattr(account, "paths", None) or ChatPaths()
        self.host, self.port, self.tls = host or self.HOST, port or self.PORT, tls
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.thread, self.gen, self.sid = None, 0, None
        self.state, self.error, self.types = "aus", "", set()
        self.seen, self.seen_set = collections.deque(maxlen=300), set()

    # ---- Voraussetzungen (ohne Sperre und ohne Anfrage an Twitch: wird von der Oberfläche alle paar Sekunden aufgerufen)
    def granted(self):
        """Die Abonnements, die der Zugang erlaubt, und die Kennung des Kontos (oder ([], ""))."""
        try:
            if not self.account.ready():
                return [], ""
            have = set(self.account.status().get("scopes") or [])
            mine = self.account.login()
            if str((self.store.data or {}).get("channel") or mine).lower() != mine:
                return [], ""                                           # fremder Kanal: die Rechte gelten nur für den eigenen
            return [t for t in self.SUBS if t[2] in have], self.account.user_id()
        except Exception:
            return [], ""

    def status(self):
        subs, uid = self.granted()
        return {"state": self.state if subs and uid else "aus", "types": sorted(self.types), "error": self.error if self.state == "fehler" else ""}

    # ---- Faden
    def ensure(self):
        subs, uid = self.granted()
        if not subs or not uid:
            return
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.gen += 1
            gen = self.gen
            self.state, self.error, self.types, self.sid = "verbinde", "", set(), None
            t = threading.Thread(target=self._run, args=(gen,), daemon=True)
            self.thread = t
        t.start()

    def _alive(self, gen):
        return self.gen == gen and not self.reader._idle() and bool(self.granted()[0])

    def _run(self, gen):
        wait, url = self.RETRY_FIRST, None
        while self._alive(gen):
            began = self.clock()
            try:
                self._session(gen)
            except Exception as e:
                with self.lock:
                    if self.gen == gen:
                        self.state, self.error = "fehler", ("Keine Verbindung zu Twitch" if isinstance(e, (OSError, EOFError)) else "Ereignisse gestört")
            if self.clock() - began > 60:
                wait = self.RETRY_FIRST
            end = self.clock() + wait
            while self.clock() < end and self._alive(gen):
                self.sleep(0.5)
            wait = min(wait * 2, 60.0)
        with self.lock:
            if self.gen == gen:
                self.state, self.types, self.sid = "aus", set(), None

    @classmethod
    def _split_url(cls, url, allow=None):
        """(Host, Pfad samt Abfrage) einer wss-Adresse von Twitch; alles andere (auch fremde Hosts mit Twitch im Namen) ist ein Fehler. Twitch schickt die
        Adresse bei session_reconnect mit und ohne Pfad ("wss://host/ws?x=1" oder "wss://host?x=1")."""
        m = re.fullmatch(r"wss://([A-Za-z0-9.\-]{1,100})(/[A-Za-z0-9_\-./%]{0,200})?(\?[A-Za-z0-9_\-./=&%]{0,300})?", url or "")
        if not m or not (m.group(1) == (allow or cls.HOST) or m.group(1).endswith(".twitch.tv")):
            raise OSError("ws-bad-url")
        path = (m.group(2) or "") + (m.group(3) or "")
        return m.group(1), path if path.startswith("/") else "/" + path

    def _open(self, host, path):
        """Verbindung über die Sendewege, TLS, Aufbau; liefert (WebSocket, Weg)."""
        sock, src = self.paths.connect(host, self.port, timeout=8)
        try:
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            return WebSocketLite.handshake(sock, host, path, self.clock), src
        except BaseException:
            try:
                sock.close()
            except OSError:
                pass
            raise

    @staticmethod
    def _welcome(payload):
        sess = payload.get("session") or {}
        sid = sess.get("id")
        if not (isinstance(sid, str) and TwitchEvents.ID_RE.fullmatch(sid)):
            raise OSError("ws-bad-session")
        try:
            keep = float(sess.get("keepalive_timeout_seconds") or 30)
        except (TypeError, ValueError):
            keep = 30.0
        return sid, keep

    def _session(self, gen):
        ws, src = self._open(self.host, self.PATH)
        try:
            tok = self.account.token()                                   # einmal vor dem Abonnieren: kein Warten hinter einer Erneuerung, wenn Twitch die 10 Sekunden zählt
            heard, keep, sig = self.clock(), 30.0, None
            while self._alive(gen):
                msg = ws.recv_message(1.0)
                heard = max(heard, ws.last_rx)                           # auch ein Ping der Gegenstelle zeigt, dass sie lebt
                if msg is None:
                    if self.clock() - heard > keep * 1.5:
                        self.paths.fail(src, 120.0)
                        raise OSError("still")
                    if sig is not None and tuple(t[0] for t in self.granted()[0]) != sig:
                        return                                           # Rechte haben sich geändert: neue Sitzung, neu abonnieren
                    continue
                try:
                    d = json.loads(msg)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                meta, payload = d.get("metadata") or {}, d.get("payload") or {}
                typ = meta.get("message_type")
                if typ == "session_welcome":
                    sid, k = self._welcome(payload)
                    keep, heard = min(600.0, max(self.KEEP_MIN, k)), self.clock()
                    sig = tuple(t[0] for t in self.granted()[0])
                    with self.lock:
                        self.sid, self.types = sid, set()
                    threading.Thread(target=self._subscribe, args=(gen, sid, tok, src), daemon=True).start()          # eigener Faden: die Empfangsschleife bleibt frei
                elif typ == "session_reconnect":
                    # Vorgehen laut Twitch: erst die neue Verbindung aufbauen und ihr session_welcome abwarten, dann die alte schließen (die Abonnements ziehen mit um)
                    host, path = self._split_url((payload.get("session") or {}).get("reconnect_url"), self.host)
                    ws2, src2 = self._open(host, path)
                    try:
                        end = self.clock() + 10.0
                        while True:
                            m2 = ws2.recv_message(1.0)
                            if m2 is not None:
                                d2 = json.loads(m2)
                                if isinstance(d2, dict) and (d2.get("metadata") or {}).get("message_type") == "session_welcome":
                                    sid2, k2 = self._welcome(d2.get("payload") or {})
                                    keep = min(600.0, max(self.KEEP_MIN, k2))
                                    break
                            if self.clock() > end:
                                raise OSError("still")
                    except BaseException:
                        ws2.close()
                        raise
                    ws.close()
                    ws, src, heard = ws2, src2, self.clock()
                    with self.lock:
                        self.sid = sid2
                elif typ == "notification":
                    self._notify(meta, payload)
                elif typ == "revocation":
                    sub = (payload.get("subscription") or {}).get("type")
                    with self.lock:
                        self.types.discard(sub)
                        gone = not self.types
                    if gone:
                        return                                           # nichts mehr abonniert: neue Sitzung, neu abonnieren
        finally:
            ws.close()

    def _subscribe(self, gen, sid, tok, src):
        """Abonnements anlegen: beide gleichzeitig, mit kurzer Zeitgrenze und zuerst über den Weg, auf dem die Verbindung steht (Twitch schließt nach 10 s ohne Abonnement);
        was scheitert, wird nach 5 und nach 20 Sekunden noch einmal versucht. Schreibt nur, solange diese Sitzung die aktuelle ist."""
        subs, uid = self.granted()
        todo, last = {t[0]: t for t in subs}, ""
        for delay in self.SUB_DELAYS:
            end = self.clock() + delay
            while self.clock() < end and todo:
                self.sleep(0.2)
                if not self._alive(gen) or self.sid != sid:
                    return
            if not todo or not self._alive(gen) or self.sid != sid:
                break
            results = {}

            def one(typ, ver):
                cond = {"broadcaster_user_id": uid}
                if typ == "channel.follow":
                    cond["moderator_user_id"] = uid
                try:
                    self.mod._call("POST", "eventsub/subscriptions", None,
                                   {"type": typ, "version": ver, "condition": cond, "transport": {"method": "websocket", "session_id": sid}},
                                   timeout=4.0, token=tok, prefer=src, accept=(409,))        # 409: gibt es schon (eine verlorene Antwort wurde wiederholt)
                    results[typ] = ""
                except ValueError as e:
                    results[typ] = str(e) if str(e) == self.mod.ERR_NET else "Twitch hat die Ereignisse nicht angenommen"
            ths = [threading.Thread(target=one, args=(t[0], t[1]), daemon=True) for t in todo.values()]
            for th in ths:
                th.start()
            for th in ths:
                th.join(8.0)
            for typ, err in results.items():
                if err == "":
                    todo.pop(typ, None)
                    with self.lock:
                        if self.sid == sid:
                            self.types.add(typ)
                else:
                    last = err
            with self.lock:
                if self.gen != gen or self.sid != sid:
                    return
                if self.types:
                    self.state, self.error = "ok", ""
                elif not todo or delay == self.SUB_DELAYS[-1]:
                    self.state, self.error = "fehler", last or "Twitch hat die Ereignisse nicht angenommen"
            if not todo:
                break

    # ---- Benachrichtigungen
    def _notify(self, meta, payload):
        mid = meta.get("message_id")
        if isinstance(mid, str) and mid:
            with self.lock:
                if mid in self.seen_set:
                    return                                             # Twitch liefert "mindestens einmal": Wiederholung nach einem Neuaufbau
                if len(self.seen) == self.seen.maxlen:
                    self.seen_set.discard(self.seen[0])
                self.seen.append(mid)
                self.seen_set.add(mid)
        item = self.map_event((payload.get("subscription") or {}).get("type"), payload.get("event"))
        if item:
            self.reader._add(item)

    @staticmethod
    def map_event(typ, ev):
        """Ein Ereignis von Twitch -> Eintrag für den Chat (mit "ev" für die farbige Karte) oder None. Alle Texte werden bereinigt und gekürzt."""
        if not isinstance(ev, dict):
            return None
        clean = lambda v, n: TwitchReader._clean(str(v or ""), n)
        who = clean(ev.get("user_name") or ev.get("user_login"), 40)
        if typ == "channel.follow":
            if not who:
                return None
            return {"type": "notice", "name": "", "color": "", "text": who, "emotes": [], "ev": {"k": "follow", "n": 0}}
        if typ == "channel.channel_points_custom_reward_redemption.add":
            rw = ev.get("reward") if isinstance(ev.get("reward"), dict) else {}
            title = clean(rw.get("title"), 80)
            inp = clean(ev.get("user_input"), 200)
            cost = rw.get("cost")
            n = min(int(cost), 100_000_000) if isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0 else 0
            text = ": ".join(x for x in (who, title) if x) + (" – " + inp if inp else "")
            if not text:
                return None
            return {"type": "notice", "name": "", "color": "", "text": text, "emotes": [], "ev": {"k": "points", "n": n}}
        return None


class TwitchNotifier:
    """Hintergrunddienst: schreibt eine Warnung in den Twitch-Chat, wenn der Akku einer DJI-Kamera die Schwelle erreicht (Auftrag des Nutzers).

    Alle 10 s wird je Kamera geprüft: Warnung, wenn der Akkustand bekannt ist, höchstens die Schwelle beträgt, die Kamera nicht lädt, der Messwert höchstens
    5 Minuten alt ist, für sie noch nicht gewarnt wurde und (bei "Nur während der Sendung") gesendet wird. Danach merkt sich der Speicher die Warnung, auch über einen
    Neustart des Dienstes hinweg. Wieder scharf wird eine Kamera, wenn sie lädt oder ihr Akku mindestens 10 Prozentpunkte über der Schwelle liegt (Akku gewechselt).
    Höchstens eine Nachricht je 3 s insgesamt. Schlägt das Senden fehl, folgen Wiederholungen nach 30, 60 und 120 s (dann weiter alle 120 s), höchstens 5 Versuche je
    Warnung; der letzte Fehler steht im Status. Neue Einstellungen starten die Versuche neu. Im Demo-Modus läuft der Dienst nicht und der Test sendet nichts."""
    POLL_S = 10
    GAP_S = 3
    FRESH_S = 300
    BACKOFF_S = (30, 60, 120, 120)         # Wartezeit nach dem 1., 2., 3. und 4. Fehlversuch; nach dem 5. wird aufgegeben
    TEST_EVERY_S = 10
    TEST_TEXT = "Test: IRL4YOU BOX"

    def __init__(self, store, djisvc, cams, send, chat=None, demo=False, mono=time.monotonic, wall=time.time, sleep=time.sleep):
        self.store, self.djisvc, self.cams, self.send = store, djisvc, cams, send
        self.chat, self.demo = chat or TwitchChat(), demo
        self.mono, self.wall, self.sleep = mono, wall, sleep
        self.lock = threading.Lock()           # Zustand (Versuche, letzte Meldung)
        self.net_lock = threading.Lock()       # immer nur eine Verbindung zu Twitch
        self.tries = {}                        # Schlüssel -> {"n": Versuche, "at": nächster Versuch (monotone Zeit) oder None = aufgegeben}
        self.last = None                       # {"ok", "time", "text"}: Ergebnis des letzten Sendens
        self.last_send = None                  # monotone Zeit der letzten Nachricht
        self.test_at = None
        self.stop_ev = threading.Event()

    # ---- Bausteine
    @staticmethod
    def camera_text(name):
        """Kameraname für die Nachricht: ohne Steuerzeichen und Zeilenumbrüche, höchstens 40 Zeichen, nicht mit / oder . am Anfang (ein Chat-Befehl)."""
        t = " ".join(str(name or "").split())
        t = "".join(ch for ch in t if ch.isprintable()).lstrip("/. ")
        return t[:40].strip()

    @staticmethod
    def render(template, name, percent):
        """{Kamera} und {Prozent} in der Nachricht ersetzen (in einem Durchgang: ein Kameraname wird nicht noch einmal durchsucht)."""
        return re.sub(r"\{(Kamera|Prozent)\}", lambda m: name if m.group(1) == "Kamera" else str(percent), template)

    @staticmethod
    def _percent(e):
        v = e.get("battery") if isinstance(e, dict) else None
        return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100 else None

    def _fresh(self, e):
        age = e.get("battery_age")
        return isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age <= self.FRESH_S

    def _live(self):
        """Wird gerade gesendet (eigene Sendekette oder ein belacoder der BELABOX-Oberfläche)?"""
        return bool(self.send._active() or belacoder_running())

    def _camera_name(self, key):
        cam = next((c for c in list(self.cams.cams) if c.get("key") == key), None)
        return self.camera_text(cam.get("name") if cam else "") or key

    def _deliver(self, cfg, text, wait=False):
        """Eine Nachricht senden (nie zwei Verbindungen zugleich). wait: den Abstand zur letzten Nachricht abwarten."""
        with self.net_lock:
            if wait and self.last_send is not None:
                left = self.GAP_S - (self.mono() - self.last_send)
                if left > 0:
                    self.sleep(left)
            ok, msg = self.chat.send(cfg["login"], cfg["token"], cfg["channel"], text)
            self.last_send = self.mono()
        return ok, msg

    # ---- Dienst
    def tick(self):
        """Ein Durchlauf: Kameras wieder scharf stellen, deren Akku gewechselt wurde oder die laden, und fällige Warnungen senden."""
        cfg = self.store.notify_settings()
        if not (cfg["enabled"] and cfg["login"] and cfg["channel"] and cfg["token"]):
            with self.lock:
                self.tries.clear()
            return
        extras = self.djisvc.camera_extras() or {}
        thr = cfg["threshold"]
        for key in self.store.warned_keys():
            e = extras.get(key)
            pct = self._percent(e)
            if e and (e.get("charging") is True or (pct is not None and pct >= thr + 10)):
                self.store.unwarn(key)
        live, due = None, []
        for key in sorted(extras):
            e = extras[key]
            pct = self._percent(e)
            if pct is None or pct > thr or e.get("charging") is True or not self._fresh(e) or self.store.is_warned(key):
                continue
            if cfg["only_live"]:
                live = self._live() if live is None else live
                if not live:
                    continue
            due.append((key, pct))
        keys = {d[0] for d in due}
        with self.lock:
            self.tries = {k: v for k, v in self.tries.items() if k in keys}      # was nicht mehr fällig ist, wird nicht wiederholt
        for key, pct in due:
            now = self.mono()
            t = self.tries.get(key)
            if t is not None and (t["at"] is None or now < t["at"]):
                continue                                                  # wartet auf den nächsten Versuch oder ist aufgegeben
            if self.last_send is not None and now - self.last_send < self.GAP_S:
                break                                                     # höchstens eine Nachricht je 3 s: der Rest folgt beim nächsten Durchlauf
            self._warn(cfg, key, pct)

    def _warn(self, cfg, key, pct):
        text = self.render(cfg["message"], self._camera_name(key), pct)
        ok, msg = self._deliver(cfg, text)
        with self.lock:
            if ok:
                self.tries.pop(key, None)
                self.last = {"ok": True, "time": int(self.wall()), "text": text}
            else:
                n = self.tries.get(key, {}).get("n", 0) + 1
                self.tries[key] = {"n": n, "at": self.mono() + self.BACKOFF_S[n - 1] if n <= len(self.BACKOFF_S) else None}
                self.last = {"ok": False, "time": int(self.wall()), "text": msg}
        if ok:
            self.store.mark_warned(key, self.wall())

    def run(self):
        """Läuft im Hintergrund, solange die Oberfläche läuft."""
        while not self.stop_ev.is_set():
            try:
                self.tick()
            except Exception as e:                  # nie den Dienst beenden; vom Text der Ausnahme nur den Typ ausgeben (er könnte Angaben enthalten)
                print("twitch:", type(e).__name__)
            self.stop_ev.wait(self.POLL_S)

    # ---- für die Oberfläche
    def status(self):
        out = self.store.public()
        with self.lock:
            last = dict(self.last) if self.last else None
            waits = [t["at"] for t in self.tries.values() if t["at"] is not None]
            gave_up = any(t["at"] is None for t in self.tries.values())
        st = {"ok": None, "time": None, "text": "", "retry_at": None, "gave_up": gave_up}
        if last:
            st.update(ok=last["ok"], time=last["time"], text=last["text"])
            if not last["ok"] and waits:
                st["retry_at"] = int(self.wall() + max(0.0, min(waits) - self.mono()))
        out["status"] = st
        return out

    def save(self, req):
        self.store.set(req)
        with self.lock:
            self.tries.clear()                       # neue Angaben: neue Versuche
        return self.status()

    def test(self):
        """Eine Testnachricht mit den gespeicherten Angaben senden: {"ok": bool, "message": Meldung auf Deutsch}."""
        cfg = self.store.notify_settings()
        if not (cfg["channel"] and cfg["login"] and cfg["token"]):
            raise ValueError(TwitchStore.ERR_MISSING)
        with self.lock:
            now = self.mono()
            if self.test_at is not None and now - self.test_at < self.TEST_EVERY_S:
                raise PermissionError("Bitte kurz warten")
            self.test_at = now
        ok, msg = (True, "Demo: gesendet.") if self.demo else self._deliver(cfg, self.TEST_TEXT, wait=True)
        with self.lock:
            self.last = {"ok": ok, "time": int(self.wall()), "text": self.TEST_TEXT if ok else msg}
        return {"ok": ok, "message": msg}


class SysAlerts:
    """Systemwarnungen im Twitch-Chat (Wunsch aus Issue #67, 12. Oktober 2026): meldet über denselben Bot wie die Akku-Warnung, wenn die Temperatur, die CPU-Last oder
    der Arbeitsspeicher der Box eine Grenze erreicht, und optional, wenn der Wert wieder normal ist. Einstellungen: <state>/sysalerts.json (atomar geschrieben, ohne
    Geheimnisse; Konto, Kanal und Token gehören zur Twitch-Karte und werden von dort genommen).

    Standard: aus. Alle 10 s werden die Messwerte der Box gelesen (dieselben wie in der Karte "Status", kein zusätzlicher Aufwand). Eine Warnung folgt, wenn der Wert in
    3 Messungen hintereinander (etwa 30 s) die Grenze erreicht oder überschreitet und die Pause (Cooldown) seit der letzten Meldung dieses Werts vorbei ist; bleibt er darüber,
    kommt nach jeder Pause eine Erinnerung. "Wieder normal" kommt, wenn er die Grenze um 3 Grad (Temperatur) oder 5 Punkte (CPU, RAM) unterschreitet, nur einmal je Warnung.
    Höchstens eine Nachricht je 3 s insgesamt (wie bei der Akku-Warnung, über denselben Sendeweg); schlägt das Senden fehl, folgt ein neuer Versuch nach 60 s, dann nach
    der Pause. Es wird nur gesendet, wenn die Twitch-Angaben vollständig sind (und bei "Nur während der Sendung" gesendet wird). Ein Fehler hier stört nie die Sendung."""
    METRICS = (("temp", "CPU-Temperatur", "°C", 3, (40, 100), 75), ("cpu", "CPU-Auslastung", "%", 5, (20, 100), 90), ("ram", "RAM-Auslastung", "%", 5, (20, 100), 90))
    DEFAULT_ALERT = "Warnung: {Name} bei {Wert} (Grenze {Grenze})"
    DEFAULT_OK = "{Name} wieder normal ({Wert})"
    DEFAULTS = {"enabled": False, "cooldown": 10, "recover": True, "only_live": True, "msg_alert": DEFAULT_ALERT, "msg_ok": DEFAULT_OK,
                **{k: {"on": True, "limit": dflt} for k, _n, _u, _h, _r, dflt in METRICS}}
    POLL_S = 10
    SUSTAIN = 3                                   # Messungen hintereinander über der Grenze (etwa 30 s)
    RETRY_S = 60
    COOLDOWN = (1, 120)                           # Minuten

    def __init__(self, path, notifier, sampler, demo=False, mono=time.monotonic, wall=time.time):
        self.path, self.notifier, self.sampler, self.demo = path, notifier, sampler, demo
        self.mono, self.wall = mono, wall
        self.lock = threading.Lock()
        self.data = json.loads(json.dumps(self.DEFAULTS))
        self.state = {k: {"over": 0, "alerting": False, "last": None, "retry": None, "pending_ok": False} for k, *_ in self.METRICS}
        self.last = None                          # {"ok", "time", "text"}
        self.stop_ev = threading.Event()
        try:
            with open(path) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            try:
                self.data = self._merge(self.data, saved, strict=False)
            except ValueError:
                pass

    # ---- Prüfung
    @classmethod
    def _int(cls, v, lo, hi, error):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(error)
        return v

    @classmethod
    def _merge(cls, base, req, strict=True):
        """Neue Werte in die Einstellungen übernehmen. strict: ungültige Angaben werfen ValueError; sonst (gespeicherte Datei) bleibt der Standard."""
        new = json.loads(json.dumps(base))

        def take(fn):
            try:
                fn()
            except ValueError:
                if strict:
                    raise
        for key in ("enabled", "recover", "only_live"):
            if key in req:
                if isinstance(req[key], bool):
                    new[key] = req[key]
                elif strict:
                    raise ValueError("ja oder nein")
        if "cooldown" in req:
            def f():
                new["cooldown"] = cls._int(req["cooldown"], cls.COOLDOWN[0], cls.COOLDOWN[1], "Pause: Zahl von %d bis %d Minuten" % cls.COOLDOWN)
            take(f)
        for key, name, unit, _h, (lo, hi), _d in cls.METRICS:
            sub = req.get(key)
            if isinstance(sub, dict):
                if "on" in sub:
                    if isinstance(sub["on"], bool):
                        new[key]["on"] = sub["on"]
                    elif strict:
                        raise ValueError("ja oder nein")
                if "limit" in sub:
                    def g(key=key, sub=sub, name=name, unit=unit, lo=lo, hi=hi):
                        new[key]["limit"] = cls._int(sub["limit"], lo, hi, "%s: Zahl von %d bis %d" % (name, lo, hi))
                    take(g)
            elif sub is not None and strict:
                raise ValueError("Ungültige Anfrage")
        for key in ("msg_alert", "msg_ok"):
            if key in req:
                def h(key=key):
                    new[key] = TwitchStore._message(req[key])
                take(h)
        return new

    def _write(self, data):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def save(self, req):
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            new = self._merge(self.data, req, strict=True)
            try:
                self._write(new)
            except OSError:
                raise RuntimeError("Die Einstellungen konnten nicht gespeichert werden")
            self.data = new
            for st in self.state.values():
                st.update(over=0, retry=None)
        return self.status()

    # ---- Bausteine
    @staticmethod
    def render(template, name, value, limit):
        """{Name}, {Wert} und {Grenze} ersetzen (in einem Durchgang)."""
        vals = {"Name": name, "Wert": value, "Grenze": limit}
        return re.sub(r"\{(Name|Wert|Grenze)\}", lambda m: vals[m.group(1)], template)

    @staticmethod
    def fmt(v, unit):
        return "%d %s" % (round(v), unit)

    def values(self):
        """Aktuelle Messwerte {temp, cpu, ram} (None, wenn nicht messbar)."""
        try:
            s = self.sampler.sample() or {}
        except Exception:
            return {"temp": None, "cpu": None, "ram": None}

        def num(v):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
        return {"temp": num(s.get("temp_c")), "cpu": num((s.get("cpu") or {}).get("avg")), "ram": num((s.get("mem") or {}).get("used_pct"))}

    def _creds(self):
        cfg = self.notifier.store.notify_settings()
        return cfg if (cfg.get("login") and cfg.get("channel") and cfg.get("token")) else None

    def _live(self):
        return self.notifier._live()

    def _send(self, cfg, text):
        ok, msg = self.notifier._deliver(cfg, text, wait=True)
        with self.lock:
            self.last = {"ok": ok, "time": int(self.wall()), "text": text if ok else msg}
        return ok

    # ---- Dienst
    def tick(self):
        with self.lock:
            data = json.loads(json.dumps(self.data))
        if not data["enabled"]:
            self._reset()
            return
        cfg = self._creds()
        if cfg is None:
            self._reset()
            return
        if data["only_live"] and not self._live():
            self._reset(keep_alert=True)               # ohne Sendung nichts melden; eine laufende Warnung bleibt vermerkt
            return
        vals = self.values()
        now = self.mono()
        pause = data["cooldown"] * 60
        for key, name, unit, hyst, _r, _d in self.METRICS:
            m, v = data[key], vals[key]
            st = self.state[key]
            if not m["on"] or v is None:
                st.update(over=0)
                continue
            limit = m["limit"]
            st["over"] = st["over"] + 1 if v >= limit else 0
            if st["retry"] is not None and now < st["retry"]:
                continue                                  # wartet auf den nächsten Versuch
            if st["over"] >= self.SUSTAIN and (st["last"] is None or now - st["last"] >= pause):
                text = self.render(data["msg_alert"], name, self.fmt(v, unit), self.fmt(limit, unit))
                if self._send(cfg, text):
                    st.update(alerting=True, last=now, retry=None, pending_ok=data["recover"])
                else:
                    st["retry"] = now + self.RETRY_S
            elif st["alerting"] and v < limit - hyst:
                st["alerting"] = False
                if st["pending_ok"] and data["recover"]:
                    text = self.render(data["msg_ok"], name, self.fmt(v, unit), self.fmt(limit, unit))
                    if not self._send(cfg, text):
                        st["alerting"], st["retry"] = True, now + self.RETRY_S      # nicht gesendet: beim nächsten Versuch noch einmal
                        continue
                st["pending_ok"] = False

    def _reset(self, keep_alert=False):
        for st in self.state.values():
            st.update(over=0, retry=None)
            if not keep_alert:
                st.update(alerting=False, pending_ok=False)

    def run(self):
        while not self.stop_ev.is_set():
            try:
                self.tick()
            except Exception as e:                  # nie den Dienst beenden; vom Text der Ausnahme nur den Typ ausgeben
                print("sysalerts:", type(e).__name__)
            self.stop_ev.wait(self.POLL_S)

    # ---- für die Oberfläche
    def status(self):
        with self.lock:
            d = json.loads(json.dumps(self.data))
            last = dict(self.last) if self.last else None
        vals = self.values()
        return dict(d, ready=self._creds() is not None,
                    values={k: (None if vals[k] is None else round(vals[k], 1)) for k in vals},
                    alerting=[k for k, st in self.state.items() if st["alerting"]], last=last)
