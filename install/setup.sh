#!/bin/sh
# IRL4YOU BOX: Komplett-Einrichtung auf einer BELABOX (zum Beispiel auf einer frisch geflashten Karte).
#   1. Grundsystem aktualisieren: nur ein Fortschrittsbalken, die Paketliste steht im Protokoll (so wie die Update-Funktion von belaUI: apt-get update, dist-upgrade).
#   2. Zusatzpaket installieren (install/install.sh): eine Zeile je Schritt.
#   3. Ergebnis: "Alles fehlerfrei installiert" oder die Hinweise, die es gab.
# Aufruf: sudo sh install/setup.sh [--ohne-systemupdate]
# Das vollständige Protokoll (alles, was apt und install.sh ausgeben) liegt in install-ausgabe.txt im Ordner des aufrufenden Benutzers.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
SKIP_SYS=0
for a in "$@"; do [ "$a" = "--ohne-systemupdate" ] && SKIP_SYS=1; done

if [ "${PB_TEST:-0}" != 1 ] && [ "$(id -u)" != 0 ]; then
  echo "Bitte mit sudo ausführen: sudo sh install/setup.sh"; exit 1
fi
U="${SUDO_USER:-root}"
H=$(getent passwd "$U" | cut -d: -f6); [ -n "$H" ] || H=/root
LOG="${PB_LOG:-$H/install-ausgabe.txt}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
VERSION=$(cat "$HERE/VERSION" 2>/dev/null || echo "?")
TTY=0; [ -t 1 ] && TTY=1
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
# Liste der noch aktualisierbaren Pakete (nur Namen)
upgradable() { LC_ALL=C apt list --upgradable 2>/dev/null | sed -n 's#^\([^/]*\)/.*#\1#p'; }

say() { printf '%s\n' "$*"; }
# Fortschrittsbalken: bar <Prozent> <Text>. Am Bildschirm eine Zeile, die sich aktualisiert; in einer Datei oder Pipe nur Zeilen bei vollen 10 Prozent.
LASTP=-1
bar() {
  p=$1; t=$2; [ "$p" -gt 100 ] && p=100; [ "$p" -lt 0 ] && p=0
  if [ "$TTY" = 1 ]; then
    n=$((p * 30 / 100)); full=$(printf '%*s' "$n" '' | tr ' ' '#'); rest=$(printf '%*s' $((30 - n)) '' | tr ' ' '.')
    printf '\r  [%s%s] %3d %%  %-34s' "$full" "$rest" "$p" "$t"
  else
    q=$((p / 10 * 10)); [ "$q" = "$LASTP" ] && return; LASTP=$q
    printf '  [%3d %%] %s\n' "$p" "$t"
  fi
}
bar_end() { [ "$TTY" = 1 ] && printf '\n'; LASTP=-1; return 0; }

{
  echo "IRL4YOU BOX Einrichtung, Version $VERSION, $(date '+%Y-%m-%d %H:%M:%S')"
  echo "System: $(uname -sr), $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-?}")"
} > "$LOG"

say "IRL4YOU BOX: Komplett-Einrichtung (Version $VERSION)"
say "Das vollständige Protokoll: $LOG"
say ""

# --- Vorprüfungen -------------------------------------------------------------------------------------------------------------------------------
if systemctl is-active --quiet pipbox-send.service 2>/dev/null || pgrep -x belacoder >/dev/null 2>&1; then
  say "Die Box sendet gerade. Bitte erst die Sendung beenden und dann noch einmal starten."; exit 1
fi
if ! wget -q --spider --timeout=10 https://github.com 2>/dev/null; then
  say "Kein Internet auf der Box (github.com nicht erreichbar). Bitte Netzwerk prüfen und noch einmal starten."; exit 1
fi

# --- Schritt 1: Grundsystem ---------------------------------------------------------------------------------------------------------------------
NEED_REBOOT=0
# apt mit Fortschritt: apt_run <Anfangstext> <Argumente von apt-get ...>. apt schreibt seinen Fortschritt auf Dateikennung 3 (Status-Fd), alles andere ins Protokoll.
apt_run() {
  label=$1; shift
  rc="$TMP/rc"; : > "$rc"
  {
    apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -o APT::Status-Fd=3 "$@" 3>&1 >>"$LOG" 2>&1
    echo $? > "$rc"
  } | while IFS=: read -r kind a pct c; do
    case "$kind" in
      dlstatus) v=${pct%%.*}; case "$v" in ''|*[!0-9]*) continue ;; esac; bar $((v * 40 / 100)) "Pakete laden" ;;
      pmstatus) v=${pct%%.*}; case "$v" in ''|*[!0-9]*) continue ;; esac; bar $((40 + v * 60 / 100)) "Pakete einrichten" ;;
    esac
  done
  bar_end
  [ "$(cat "$rc" 2>/dev/null)" = 0 ]
}

if [ "$SKIP_SYS" = 1 ]; then
  say "Schritt 1 von 2: Grundsystem (übersprungen)"
else
  say "Schritt 1 von 2: Grundsystem aktualisieren (dauert je nach Stand einige Minuten)"
  {
    echo; echo "===== apt-get update ====="
  } >> "$LOG"
  bar 0 "Paketlisten holen"
  if ! apt-get -o Acquire::http::Timeout=30 update >>"$LOG" 2>&1; then
    bar_end; say "  Die Paketlisten ließen sich nicht laden. Details im Protokoll. Weiter mit Schritt 2."
  else
    bar 100 "Paketlisten geholt"; bar_end
    up=$(upgradable || true)
    if [ -z "$up" ]; then
      say "  Das Grundsystem ist schon aktuell."
    else
      case " $(echo "$up" | tr '\n' ' ') " in *" l4t"*|*" belabox-linux-"*|*" belabox-network-config"*) NEED_REBOOT=1 ;; esac
      echo "Zu aktualisieren: $(echo "$up" | wc -l) Pakete" >> "$LOG"
      {
        echo; echo "===== apt-get dist-upgrade ====="
      } >> "$LOG"
      if ! apt_run "Pakete" dist-upgrade; then
        say "  Das Systemupdate meldete einen Fehler (Details im Protokoll). Weiter mit Schritt 2."
      else
        held=$(upgradable | tr '\n' ' ' || true)
        if [ -n "${held# }" ]; then            # zurückgehaltene Pakete (zum Beispiel neue Kernelpakete): gezielt nachinstallieren, wie es belaUI macht
          { echo; echo "===== apt-get install (zurückgehaltene Pakete) ====="; } >> "$LOG"
          # shellcheck disable=SC2086
          apt_run "Pakete" install $held || say "  Einige zurückgehaltene Pakete ließen sich nicht einrichten (Details im Protokoll)."
        fi
        say "  Grundsystem aktualisiert."
      fi
    fi
  fi
fi
say ""

# --- Schritt 2: Zusatzpaket ---------------------------------------------------------------------------------------------------------------------
say "Schritt 2 von 2: IRL4YOU BOX installieren (das Bauen der Bausteine dauert einige Minuten)"
{ echo; echo "===== install/install.sh ====="; } >> "$LOG"
rc="$TMP/rc2"; : > "$rc"
{
  sh "$HERE/install/install.sh" install 2>&1
  echo $? > "$rc"
} | while IFS= read -r line; do
  printf '%s\n' "$line" >> "$LOG"
  case "$line" in
    "PIPBOX-STEP pakete")    bar 12 "Benötigte Pakete prüfen" ;;
    "PIPBOX-STEP dienst")    bar 25 "Benutzer und Daten einrichten" ;;
    "PIPBOX-STEP dateien")   bar 38 "Programme und Oberfläche kopieren" ;;
    "PIPBOX-STEP baustein")  bar 50 "Bild-in-Bild-Baustein bauen" ;;
    "PIPBOX-STEP srtla")     bar 66 "SRTLA-Sender bauen" ;;
    "PIPBOX-STEP belacoder") bar 80 "Encoder bauen" ;;
    "PIPBOX-STEP start")     bar 94 "Dienste starten" ;;
  esac
done
bar 100 "Fertig"; bar_end
RC2=$(cat "$rc" 2>/dev/null || echo 1)
say ""

# --- Ergebnis -----------------------------------------------------------------------------------------------------------------------------------
HINTS=$(sed -n '/===== install\/install.sh =====/,$p' "$LOG" | grep -E '^(WARNUNG|FEHLER)' || true)
BAD=""
for s in pipbox pipbox-dji nginx; do systemctl is-active --quiet "$s.service" 2>/dev/null || BAD="$BAD $s"; done
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ "$RC2" = 0 ] && [ -z "$HINTS" ] && [ -z "$BAD" ]; then
  say "Alles fehlerfrei installiert."
  say "IRL4YOU BOX Version $VERSION läuft: http://${IP:-<Adresse der Box>}:8780"
  RESULT=0
else
  RESULT=1
  say "Die Installation ist durchgelaufen, aber es gibt Hinweise:"
  [ "$RC2" = 0 ] || say "  - install.sh endete mit Fehlercode $RC2."
  [ -z "$HINTS" ] || printf '%s\n' "$HINTS" | sed 's/^/  - /'
  [ -z "$BAD" ] || say "  - Diese Dienste laufen nicht:$BAD"
  say "Bitte das Protokoll schicken: $LOG"
fi
if [ "$NEED_REBOOT" = 1 ] || [ -f /var/run/reboot-required ]; then
  say ""
  say "Das Systemupdate bringt einen neuen Kernel mit: Bitte die Box jetzt neu starten (sudo reboot)."
fi
exit $RESULT
