#!/bin/sh
# IRL4YOU BOX: Installation auf der Box (Version siehe Datei VERSION).
# Ändert an BELABOX nur eine Einstellung des RTMP-Servers (Leerlaufgrenze, mit Sicherung). Rückweg: install.sh uninstall
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"

# Fehlersuche: Zustand der Tailscale-Freigaben (privat und öffentlich) vor und nach der Installation ins Protokoll des Update-Helfers schreiben.
# Anlass: Auf einer Box ging die öffentliche Freigabe angeblich bei Updates verloren. Verändert nichts, schlägt nie fehl (die Installation geht weiter).
ts_snapshot() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tsto=""
  if command -v timeout >/dev/null 2>&1; then tsto="timeout 10"; fi
  {
    echo "$(date '+%Y-%m-%d %H:%M:%S') Tailscale $1:"
    $tsto tailscale serve status 2>&1 | sed 's/^/    serve: /'
    $tsto tailscale funnel status 2>&1 | sed 's/^/    funnel: /'
  } >> "${PIPBOX_SWUPDATE_LOG:-/var/log/pipbox-swupdate.log}" 2>/dev/null || true
  return 0
}

case "${1:-install}" in
  install)
    ts_snapshot "vor der Installation"
    # Pakete, die ein frisches BELABOX-Image nicht mitbringt (Bluetooth für die DJI-Kameras; gstreamer1.0-tools mit gst-launch-1.0 für die Zubringer von "alle Kameras immer bereit" und die HDMI-Kamera). Ohne sie fehlt auch die Gruppe
    # "bluetooth", die der Benutzer unten braucht. Braucht Internet; schlägt es fehl, läuft alles außer den DJI-Kameras.
    echo "PIPBOX-STEP pakete"      # Fortschrittsanzeige des Update-Helfers (pipbox-swupdate.py liest diese Zeilen)
    need=""
    for p in bluez python3-dbus python3-gi gstreamer1.0-tools; do dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"; done
    if [ -n "$need" ]; then
      echo "Installiere fehlende Pakete:$need"
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need \
        || { apt-get update >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need; } \
        || echo "WARNUNG: Pakete konnten nicht installiert werden (Internet?). Die DJI-Kameras gehen erst, wenn bluez, python3-dbus und python3-gi da sind; Alle Kameras immer bereit und die HDMI-Kamera brauchen gstreamer1.0-tools."
    fi
    # DJI-Dienst: Bluetooth-Bibliothek bleak. Für Ubuntu 22.04 gibt es kein Paket dafür, deshalb pip (braucht Internet). Fehlt sie
    # danach, bricht die Installation hier ab, bevor etwas verändert wurde: Der Update-Helfer stellt dann die vorige Version wieder
    # her, statt die DJI-Kameras still lahmzulegen.
    if ! python3 -c "import bleak" 2>/dev/null; then
      echo "Installiere die Bluetooth-Bibliothek bleak (pip)"
      dpkg -s python3-pip >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-pip || true
      # pip 22.0.2 (Ubuntu 22.04) kennt "--root-user-action" nicht: Die harmlose Warnung "Running pip as the 'root' user" (die Installation läuft ohnehin als root)
      # wird aus der Ausgabe gefiltert, alles andere bleibt sichtbar.
      pipout=$(pip3 install --disable-pip-version-check "bleak>=0.22" 2>&1) || true
      printf '%s\n' "$pipout" | grep -v "Running pip as the 'root' user" || true
      if ! python3 -c "import bleak" 2>/dev/null; then
        echo "FEHLER: Die Bluetooth-Bibliothek bleak konnte nicht installiert werden (Internet? pip3?). Der DJI-Dienst braucht sie." >&2
        echo "Von Hand: sudo apt-get install -y python3-pip && sudo pip3 install bleak, danach install.sh erneut ausführen." >&2
        exit 1
      fi
    fi
    getent group bluetooth >/dev/null || groupadd --system bluetooth
    getent group input >/dev/null || groupadd --system input        # Controller-Tasten: Lesezugriff auf /dev/input
    echo "PIPBOX-STEP dienst"      # Fortschrittsanzeige des Update-Helfers (pipbox-swupdate.py liest diese Zeilen)
    systemctl stop pipbox.service 2>/dev/null || true
    # Fester, rechteloser Benutzer (der D-Bus-Daemon akzeptiert keine DynamicUser-Benutzer).
    # In /etc/passwd prüfen: "id" findet sonst einen noch laufenden temporären Benutzer.
    if ! grep -q '^pipbox:' /etc/passwd; then
      useradd --system --no-create-home --home-dir /var/lib/pipbox --shell /usr/sbin/nologin \
        --groups bluetooth pipbox
    fi
    if [ -L /var/lib/pipbox ]; then   # Daten aus der früheren DynamicUser-Version übernehmen
      old=$(readlink -f /var/lib/pipbox)
      rm /var/lib/pipbox
      mkdir -p /var/lib/pipbox
      cp -a "$old"/. /var/lib/pipbox/
      echo "Alte Daten aus $old übernommen (Original bleibt als Sicherung liegen)."
    fi
    mkdir -p /var/lib/pipbox
    chown -R pipbox:pipbox /var/lib/pipbox
    chmod 700 /var/lib/pipbox
    # Einmalige Aufräumung: Testquellen aus der Entwicklung (Schlüssel tst-a bis tst-d), die eine frühere Version automatisch als Kamera
    # aufgenommen hat, kommen wieder aus der Kameraliste. Der Dienst steht hier still. Andere Kameras bleiben unberührt.
    if [ -f /var/lib/pipbox/cameras.json ]; then
      python3 - <<'PY' || true
import json, os, re
p = "/var/lib/pipbox/cameras.json"
try:
    cams = json.load(open(p))
    keep = [c for c in cams if not re.fullmatch(r"tst-[a-d]", str(c.get("key", "")))]
    if len(keep) != len(cams):
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(keep, f, indent=1)
        st = os.stat(p)
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o777)
        os.replace(tmp, p)
        print("Testkameras aus der Kameraliste entfernt:", len(cams) - len(keep))
except Exception as e:
    print("Hinweis: Die Kameraliste wurde nicht bereinigt:", e)
PY
    fi
    echo "PIPBOX-STEP dateien"      # Fortschrittsanzeige des Update-Helfers (pipbox-swupdate.py liest diese Zeilen)
    install -d /opt/pipbox/web
    # Bluetooth-Dienst nur neu starten, wenn sich seine Dateien ändern (sonst reißen die Kameras ab)
    dji_changed=0
    for f in dji.py dji_daemon.py phone_battery.py controllers.py; do cmp -s "$HERE/$f" "/opt/pipbox/$f" || dji_changed=1; done
    cmp -s "$HERE/install/pipbox-dji.service" /etc/systemd/system/pipbox-dji.service || dji_changed=1
    # HDMI-Dienst: ebenso nur neu starten, wenn sich seine Dateien ändern (er speist den HDMI-Eingang als Kamera ein)
    hdmi_changed=0
    cmp -s "$HERE/hdmi_daemon.py" /opt/pipbox/hdmi_daemon.py || hdmi_changed=1
    cmp -s "$HERE/install/pipbox-hdmi.service" /etc/systemd/system/pipbox-hdmi.service || hdmi_changed=1
    install -m 644 "$HERE/server.py" /opt/pipbox/server.py
    install -m 644 "$HERE/dji.py" /opt/pipbox/dji.py
    install -m 644 "$HERE/dji_daemon.py" /opt/pipbox/dji_daemon.py
    install -m 644 "$HERE/phone_battery.py" /opt/pipbox/phone_battery.py
    install -m 644 "$HERE/controllers.py" /opt/pipbox/controllers.py
    install -m 644 "$HERE/controller_keys.py" /opt/pipbox/controller_keys.py
    install -m 644 "$HERE/hdmi_daemon.py" /opt/pipbox/hdmi_daemon.py
    install -m 644 "$HERE/pipbox_send.py" /opt/pipbox/pipbox_send.py
    install -m 644 "$HERE/pipbox_send_ctl.py" /opt/pipbox/pipbox_send_ctl.py
    install -m 644 "$HERE/pipbox_live.py" /opt/pipbox/pipbox_live.py       # Engine "alle Kameras immer bereit" mit Compositor und Kamera-Zweigen (braucht den belacoder mit -sb11)
    install -m 644 "$HERE/pipbox_always.py" /opt/pipbox/pipbox_always.py       # Zubringer und Auswahl für "alle Kameras immer bereit" (nicht in der Pflichtliste des Update-Helfers: ein Rückweg auf ältere Versionen muss möglich bleiben)
    install -m 755 "$HERE/install/pipbox_health.py" /opt/pipbox/pipbox_health.py
    install -m 644 "$HERE/VERSION" /opt/pipbox/VERSION
    install -m 755 "$HERE/install/pipbox-swupdate.py" /opt/pipbox/pipbox-swupdate.py
    install -m 644 "$HERE/install/pipbox-swupdate.service" /etc/systemd/system/pipbox-swupdate.service
    install -m 644 "$HERE/install/pipbox-swupdate.path" /etc/systemd/system/pipbox-swupdate.path
    install -m 755 "$HERE/install/pipbox-remote.py" /opt/pipbox/pipbox-remote.py
    install -m 644 "$HERE/install/pipbox-remote.service" /etc/systemd/system/pipbox-remote.service
    install -m 644 "$HERE/install/pipbox-remote.path" /etc/systemd/system/pipbox-remote.path
    # Frühere Versionen beendeten die öffentliche Freigabe (Funnel) nach 8 Stunden mit einem Zeitgeber: entfällt, sie bleibt bis zum Beenden
    systemctl disable --now pipbox-funnel-guard.timer 2>/dev/null || true
    rm -f /etc/systemd/system/pipbox-funnel-guard.service /etc/systemd/system/pipbox-funnel-guard.timer
    # Bluetooth-Treiber für Realtek-Sticks, die der Kernel nicht kennt (TP-Link UB500 u. a.): Quellen, Helfer, Zeitgeber und udev-Regel (beim Einstecken)
    install -d /opt/pipbox/btusb-src
    for f in btusb.c btintel.h btbcm.h btrtl.h COPYING README.md; do install -m 644 "$HERE/bluetooth-src/$f" "/opt/pipbox/btusb-src/$f"; done
    install -m 755 "$HERE/install/pipbox-btdriver.py" /opt/pipbox/pipbox-btdriver.py
    install -m 644 "$HERE/install/pipbox-btdriver.service" /etc/systemd/system/pipbox-btdriver.service
    install -m 644 "$HERE/install/pipbox-btdriver.timer" /etc/systemd/system/pipbox-btdriver.timer
    install -m 644 "$HERE/install/80-pipbox-btdriver.rules" /etc/udev/rules.d/80-pipbox-btdriver.rules
    udevadm control --reload-rules 2>/dev/null || true
    install -m 755 "$HERE/install/pipbox-wifi.py" /opt/pipbox/pipbox-wifi.py
    install -m 644 "$HERE/install/pipbox-wifi.service" /etc/systemd/system/pipbox-wifi.service
    install -m 644 "$HERE/install/pipbox-wifi.path" /etc/systemd/system/pipbox-wifi.path
    install -m 755 "$HERE/install/pipbox-power.py" /opt/pipbox/pipbox-power.py
    install -m 755 "$HERE/install/pipbox-logmode.py" /opt/pipbox/pipbox-logmode.py
    install -m 644 "$HERE/install/pipbox-logmode.service" /etc/systemd/system/pipbox-logmode.service
    install -m 644 "$HERE/install/pipbox-logmode.path" /etc/systemd/system/pipbox-logmode.path
    install -m 755 "$HERE/install/pipbox-logs.py" /opt/pipbox/pipbox-logs.py
    install -m 644 "$HERE/install/pipbox-logs.service" /etc/systemd/system/pipbox-logs.service
    install -m 644 "$HERE/install/pipbox-logs.path" /etc/systemd/system/pipbox-logs.path
    install -m 755 "$HERE/install/pipbox-ssh.py" /opt/pipbox/pipbox-ssh.py
    install -m 644 "$HERE/install/pipbox-ssh.service" /etc/systemd/system/pipbox-ssh.service
    install -m 644 "$HERE/install/pipbox-ssh.path" /etc/systemd/system/pipbox-ssh.path
    # Protokoll-Modus: Boxen mit dem früheren dauerhaften Journal bleiben "ausfuehrlich", neue Installationen starten "sparsam"
    # (Journal und Zustandsprotokoll nur im Arbeitsspeicher, schont die Speicherkarte). Umschalten in der Oberfläche.
    install -d /etc/pipbox
    if [ ! -f /etc/pipbox/logmode ]; then
      if [ -f /etc/systemd/journald.conf.d/pipbox-persistent.conf ]; then mode=ausfuehrlich; else mode=sparsam; fi
      python3 /opt/pipbox/pipbox-logmode.py --apply "$mode" || echo "WARNUNG: Protokoll-Modus konnte nicht gesetzt werden."
    fi
    install -m 644 "$HERE/install/pipbox-power.service" /etc/systemd/system/pipbox-power.service
    install -m 644 "$HERE/install/pipbox-power.path" /etc/systemd/system/pipbox-power.path
    install -m 644 "$HERE/install/pipbox-health.service" /etc/systemd/system/pipbox-health.service
    install -m 644 "$HERE/web/index.html" /opt/pipbox/web/index.html
    install -m 644 "$HERE/web/login.html" /opt/pipbox/web/login.html
    install -m 644 "$HERE/web/i18n.js" /opt/pipbox/web/i18n.js
    install -d /opt/pipbox/web/i18n
    rm -f /opt/pipbox/web/i18n/*.json
    for f in "$HERE"/web/i18n/*.json; do [ -f "$f" ] && install -m 644 "$f" "/opt/pipbox/web/i18n/$(basename "$f")"; done
    install -m 755 "$HERE/install/pipbox-update.py" /opt/pipbox/pipbox-update.py
    install -m 644 "$HERE/install/pipbox.service" /etc/systemd/system/pipbox.service
    install -m 644 "$HERE/install/pipbox-dji.service" /etc/systemd/system/pipbox-dji.service
    install -m 644 "$HERE/install/pipbox-hdmi.service" /etc/systemd/system/pipbox-hdmi.service
    for u in pipbox-send.service pipbox-send-ctl.service pipbox-send-ctl.path; do install -m 644 "$HERE/install/$u" "/etc/systemd/system/$u"; done
    install -m 644 "$HERE/install/pipbox-update.service" /etc/systemd/system/pipbox-update.service
    install -m 644 "$HERE/install/pipbox-update.path" /etc/systemd/system/pipbox-update.path
    # Bild-in-Bild-Baustein bauen, wenn er fehlt oder der Quelltext neuer ist (braucht Internet für die Header)
    echo "PIPBOX-STEP baustein"      # Fortschrittsanzeige des Update-Helfers (pipbox-swupdate.py liest diese Zeilen)
    install -d /opt/pipbox/gst-src
    # Nur neu bauen, wenn der Quelltext sich wirklich geändert hat (oder der Baustein fehlt)
    gst_changed=0
    cmp -s "$HERE/gst/gstpbpip.c" /opt/pipbox/gst-src/gstpbpip.c || gst_changed=1
    install -m 644 "$HERE/gst/gstpbpip.c" /opt/pipbox/gst-src/gstpbpip.c
    install -m 755 "$HERE/gst/build.sh" /opt/pipbox/gst-src/build.sh
    if [ ! -f /opt/pipbox/gst/libgstpbpip.so ] || [ "$gst_changed" = 1 ]; then
      sh /opt/pipbox/gst-src/build.sh || echo "WARNUNG: Der PiP-Baustein konnte nicht gebaut werden; Bild-in-Bild bleibt gesperrt."
    fi
    # Latenzbewusster SRTLA-Sender (verteilt nach Laufzeit je Leitung, verhindert Bitrate-Einbrüche bei ungleichen
    # Leitungen). Wird nur gebaut, wenn der Patch neuer ist; schlägt das fehl, bleibt der Original-Sender aktiv.
    echo "PIPBOX-STEP srtla"      # Fortschrittsanzeige des Update-Helfers (pipbox-swupdate.py liest diese Zeilen)
    install -d /opt/pipbox/srtla
    srtla_changed=0
    for sp in srtla_send-latency-aware.patch srtla_send-min-share.patch; do
      cmp -s "$HERE/srtla/$sp" "/opt/pipbox/srtla/$sp" || srtla_changed=1
    done
    [ -x /usr/local/bin/srtla_send ] || srtla_changed=1
    if [ "$srtla_changed" = 1 ]; then
      if sh "$HERE/srtla/build.sh"; then
        for sp in srtla_send-latency-aware.patch srtla_send-min-share.patch; do
          install -m 644 "$HERE/srtla/$sp" "/opt/pipbox/srtla/$sp"
        done
      else
        echo "WARNUNG: Der latenzbewusste Sender konnte nicht gebaut werden (braucht git, gcc, make, patch, Internet). Es bleibt der Original-Sender."
      fi
    fi
    # belacoder mit tolerantem Bitraten-Regler (verhindert, dass die Bitrate nach einer kurzen Überlast auf dem Minimum hängen
    # bleibt) und Stall-Wächter, der nur den Ausgang prüft (ein Aussetzer einer kleinen Kamera beendet die Sendung nicht mehr).
    # Wird nur gebaut, wenn ein Patch neuer ist; schlägt das fehl, bleibt die bisherige Fassung (oder das BELABOX-Original) aktiv.
    echo "PIPBOX-STEP belacoder"      # Fortschrittsanzeige des Update-Helfers (pipbox-swupdate.py liest diese Zeilen)
    install -d /opt/pipbox/belacoder
    bc_changed=0
    for bp in belacoder-jitter-tolerant.patch belacoder-stall-output.patch belacoder-stats.patch belacoder-live-feeds.patch belacoder-frame-copy.patch; do
      cmp -s "$HERE/belacoder/$bp" "/opt/pipbox/belacoder/$bp" || bc_changed=1
    done
    [ -x /opt/pipbox/bin/belacoder ] || bc_changed=1
    if [ "$bc_changed" = 1 ]; then
      if sh "$HERE/belacoder/build.sh"; then
        for bp in belacoder-jitter-tolerant.patch belacoder-stall-output.patch belacoder-stats.patch belacoder-live-feeds.patch belacoder-frame-copy.patch; do
          install -m 644 "$HERE/belacoder/$bp" "/opt/pipbox/belacoder/$bp"
        done
      else
        echo "WARNUNG: belacoder mit dem toleranten Regler konnte nicht gebaut werden (braucht git, gcc, patch, Internet). Es bleibt das Original."
      fi
    fi
    # RTMP-Server der BELABOX: Eine Kamera, die kurz nichts schickt (WLAN-Hänger, 4 bis 10 s), wirft er nach 4 s raus; sie muss
    # sich neu verbinden und der Encoder startet jedes Mal neu. Die Grenze wird auf 15 s gesetzt. Das ist die einzige Änderung an
    # einer BELABOX-Datei: Sicherung als .vor-pipbox, "install.sh uninstall" stellt sie wieder her. Die Datei gehört dem Paket
    # belabox-rtmp-server (keine dpkg-Konfigurationsdatei): ein Update überschreibt sie wieder mit 4 s. Darum übernimmt ein kleines
    # Skript die Änderung und ein apt-Haken ruft es nach jedem Paketlauf erneut auf. Nginx wird nur neu geladen, wenn gerade nicht
    # gesendet wird (das Neuladen trennt die Kameras kurz; sie verbinden sich selbst wieder).
    install -m 755 "$HERE/install/pipbox-nginx-guard.sh" /opt/pipbox/pipbox-nginx-guard.sh
    install -m 644 "$HERE/install/99pipbox-nginx" /etc/apt/apt.conf.d/99pipbox-nginx
    /opt/pipbox/pipbox-nginx-guard.sh
    echo "PIPBOX-STEP start"      # Fortschrittsanzeige des Update-Helfers (pipbox-swupdate.py liest diese Zeilen)
    systemctl daemon-reload
    systemctl enable pipbox.service pipbox-dji.service pipbox-hdmi.service
    # Kein Trennen der Kameras vor dem Neustart des Bluetooth-Dienstes: BlueZ hält die Verbindung, der neue Dienst verwendet sie weiter. Ein Trennen
    # ließe die Kamera etwa eine Minute lang keine Verbindung annehmen.
    if [ "$dji_changed" = 1 ] || ! systemctl is-active --quiet pipbox-dji.service; then systemctl restart pipbox-dji.service; fi
    if [ "$hdmi_changed" = 1 ] || ! systemctl is-active --quiet pipbox-hdmi.service; then systemctl restart pipbox-hdmi.service; fi
    systemctl restart pipbox-health.service 2>/dev/null || true
    systemctl enable --now pipbox-update.path pipbox-send-ctl.path pipbox-health.service pipbox-swupdate.path pipbox-remote.path pipbox-wifi.path pipbox-power.path pipbox-logmode.path pipbox-logs.path pipbox-ssh.path pipbox-btdriver.timer
    systemctl restart pipbox.service
    systemctl start --no-block pipbox-btdriver.service 2>/dev/null || true      # steckt schon ein passender Stick, gleich prüfen (sonst tut der Dienst nichts)
    ts_snapshot "nach der Installation"
    echo "IRL4YOU BOX läuft auf Port 8780 im lokalen Netz. Ersteinrichtung im Browser."
    ;;
  uninstall)
    [ -x /opt/pipbox/pipbox-btdriver.py ] && python3 /opt/pipbox/pipbox-btdriver.py uninstall || true     # eingespieltes Bluetooth-Modul entfernen (Standardmodul gilt nach dem nächsten Neustart)
    systemctl disable --now pipbox-btdriver.timer pipbox-funnel-guard.timer pipbox-send.service pipbox-hdmi.service pipbox-send-ctl.path pipbox-update.path pipbox-swupdate.path pipbox-remote.path pipbox-wifi.path pipbox-power.path pipbox-logmode.path pipbox-logs.path pipbox-ssh.path pipbox-health.service pipbox.service pipbox-dji.service || true
    rm -f /etc/systemd/system/pipbox-send.service /etc/systemd/system/pipbox-send-ctl.service /etc/systemd/system/pipbox-send-ctl.path /etc/systemd/system/pipbox.service /etc/systemd/system/pipbox-dji.service /etc/systemd/system/pipbox-hdmi.service /etc/systemd/system/pipbox-update.service /etc/systemd/system/pipbox-update.path /etc/systemd/system/pipbox-swupdate.service /etc/systemd/system/pipbox-swupdate.path /etc/systemd/system/pipbox-remote.service /etc/systemd/system/pipbox-remote.path /etc/systemd/system/pipbox-wifi.service /etc/systemd/system/pipbox-wifi.path /etc/systemd/system/pipbox-power.service /etc/systemd/system/pipbox-power.path /etc/systemd/system/pipbox-health.service /etc/systemd/system/pipbox-logmode.service /etc/systemd/system/pipbox-logmode.path /etc/systemd/system/pipbox-logs.service /etc/systemd/system/pipbox-logs.path /etc/systemd/system/pipbox-ssh.service /etc/systemd/system/pipbox-ssh.path /etc/systemd/system/pipbox-funnel-guard.service /etc/systemd/system/pipbox-funnel-guard.timer /etc/systemd/system/pipbox-btdriver.service /etc/systemd/system/pipbox-btdriver.timer /etc/udev/rules.d/80-pipbox-btdriver.rules
    rm -f /etc/apt/apt.conf.d/99pipbox-nginx
    NGX=/etc/nginx/modules-available/99-belabox-rtmp.conf
    if [ -f "$NGX.vor-pipbox" ]; then
      cp "$NGX.vor-pipbox" "$NGX" && rm -f "$NGX.vor-pipbox"
      nginx -t >/dev/null 2>&1 && nginx -s reload 2>/dev/null || true
    fi
    rm -f /usr/local/bin/srtla_send
    rm -f /etc/systemd/journald.conf.d/pipbox-journal.conf /etc/systemd/journald.conf.d/pipbox-persistent.conf
    rm -rf /etc/pipbox
    systemctl restart systemd-journald 2>/dev/null || true
    rm -rf /opt/pipbox
    echo "Passwort und Kameraliste bleiben in /var/lib/pipbox (zum Löschen manuell entfernen)."
    udevadm control --reload-rules 2>/dev/null || true
    systemctl daemon-reload
    echo "PIPBOX entfernt."
    ;;
  *) echo "Aufruf: install.sh [install|uninstall]"; exit 1 ;;
esac
