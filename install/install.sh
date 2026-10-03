#!/bin/sh
# IRL4YOU BOX: Installation auf der Box (Version siehe Datei VERSION).
# Fasst nichts von BELABOX an. Rückweg: install.sh uninstall
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"

case "${1:-install}" in
  install)
    # Pakete, die ein frisches BELABOX-Image nicht mitbringt (Bluetooth für die DJI-Kameras). Ohne sie fehlt auch die Gruppe
    # "bluetooth", die der Benutzer unten braucht. Braucht Internet; schlägt es fehl, läuft alles außer den DJI-Kameras.
    need=""
    for p in bluez python3-dbus python3-gi; do dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"; done
    if [ -n "$need" ]; then
      echo "Installiere fehlende Pakete:$need"
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need \
        || echo "WARNUNG: Pakete konnten nicht installiert werden (Internet?). Die DJI-Kameras gehen erst, wenn bluez, python3-dbus und python3-gi da sind."
    fi
    getent group bluetooth >/dev/null || groupadd --system bluetooth
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
    install -d /opt/pipbox/web
    # Journal dauerhaft speichern (damit nach einem Absturz Spuren bleiben), aber höchstens 30 MB und 7 Tage behalten
    install -d /var/log/journal /etc/systemd/journald.conf.d
    printf '[Journal]\nStorage=persistent\nSystemMaxUse=30M\nSystemMaxFileSize=3M\nMaxRetentionSec=7day\nSyncIntervalSec=5min\n' > /etc/systemd/journald.conf.d/pipbox-persistent.conf
    systemctl restart systemd-journald 2>/dev/null || true
    # Bluetooth-Dienst nur neu starten, wenn sich seine Dateien ändern (sonst reißen die Kameras ab)
    dji_changed=0
    for f in dji.py dji_daemon.py; do cmp -s "$HERE/$f" "/opt/pipbox/$f" || dji_changed=1; done
    cmp -s "$HERE/install/pipbox-dji.service" /etc/systemd/system/pipbox-dji.service || dji_changed=1
    install -m 644 "$HERE/server.py" /opt/pipbox/server.py
    install -m 644 "$HERE/dji.py" /opt/pipbox/dji.py
    install -m 644 "$HERE/dji_daemon.py" /opt/pipbox/dji_daemon.py
    install -m 644 "$HERE/pipbox_send.py" /opt/pipbox/pipbox_send.py
    install -m 644 "$HERE/pipbox_send_ctl.py" /opt/pipbox/pipbox_send_ctl.py
    install -m 755 "$HERE/install/pipbox_health.py" /opt/pipbox/pipbox_health.py
    install -m 644 "$HERE/VERSION" /opt/pipbox/VERSION
    install -m 755 "$HERE/install/pipbox-swupdate.py" /opt/pipbox/pipbox-swupdate.py
    install -m 644 "$HERE/install/pipbox-swupdate.service" /etc/systemd/system/pipbox-swupdate.service
    install -m 644 "$HERE/install/pipbox-swupdate.path" /etc/systemd/system/pipbox-swupdate.path
    install -m 755 "$HERE/install/pipbox-remote.py" /opt/pipbox/pipbox-remote.py
    install -m 644 "$HERE/install/pipbox-remote.service" /etc/systemd/system/pipbox-remote.service
    install -m 644 "$HERE/install/pipbox-remote.path" /etc/systemd/system/pipbox-remote.path
    install -m 755 "$HERE/install/pipbox-wifi.py" /opt/pipbox/pipbox-wifi.py
    install -m 644 "$HERE/install/pipbox-wifi.service" /etc/systemd/system/pipbox-wifi.service
    install -m 644 "$HERE/install/pipbox-wifi.path" /etc/systemd/system/pipbox-wifi.path
    install -m 755 "$HERE/install/pipbox-power.py" /opt/pipbox/pipbox-power.py
    install -m 644 "$HERE/install/pipbox-power.service" /etc/systemd/system/pipbox-power.service
    install -m 644 "$HERE/install/pipbox-power.path" /etc/systemd/system/pipbox-power.path
    install -m 644 "$HERE/install/pipbox-health.service" /etc/systemd/system/pipbox-health.service
    install -m 644 "$HERE/web/index.html" /opt/pipbox/web/index.html
    install -m 644 "$HERE/web/login.html" /opt/pipbox/web/login.html
    install -m 755 "$HERE/install/pipbox-update.py" /opt/pipbox/pipbox-update.py
    install -m 644 "$HERE/install/pipbox.service" /etc/systemd/system/pipbox.service
    install -m 644 "$HERE/install/pipbox-dji.service" /etc/systemd/system/pipbox-dji.service
    for u in pipbox-send.service pipbox-send-ctl.service pipbox-send-ctl.path; do install -m 644 "$HERE/install/$u" "/etc/systemd/system/$u"; done
    install -m 644 "$HERE/install/pipbox-update.service" /etc/systemd/system/pipbox-update.service
    install -m 644 "$HERE/install/pipbox-update.path" /etc/systemd/system/pipbox-update.path
    # Bild-in-Bild-Baustein bauen, wenn er fehlt oder der Quelltext neuer ist (braucht Internet für die Header)
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
    install -d /opt/pipbox/srtla
    srtla_changed=0
    cmp -s "$HERE/srtla/srtla_send-latency-aware.patch" /opt/pipbox/srtla/srtla_send-latency-aware.patch || srtla_changed=1
    [ -x /usr/local/bin/srtla_send ] || srtla_changed=1
    if [ "$srtla_changed" = 1 ]; then
      if sh "$HERE/srtla/build.sh"; then
        install -m 644 "$HERE/srtla/srtla_send-latency-aware.patch" /opt/pipbox/srtla/srtla_send-latency-aware.patch
      else
        echo "WARNUNG: Der latenzbewusste Sender konnte nicht gebaut werden (braucht git, gcc, make, patch, Internet). Es bleibt der Original-Sender."
      fi
    fi
    systemctl daemon-reload
    systemctl enable pipbox.service pipbox-dji.service
    if [ "$dji_changed" = 1 ] || ! systemctl is-active --quiet pipbox-dji.service; then systemctl restart pipbox-dji.service; fi
    systemctl restart pipbox-health.service 2>/dev/null || true
    systemctl enable --now pipbox-update.path pipbox-send-ctl.path pipbox-health.service pipbox-swupdate.path pipbox-remote.path pipbox-wifi.path pipbox-power.path
    systemctl restart pipbox.service
    echo "IRL4YOU BOX läuft auf Port 8780 im lokalen Netz. Ersteinrichtung im Browser."
    ;;
  uninstall)
    systemctl disable --now pipbox-send.service pipbox-send-ctl.path pipbox-update.path pipbox-swupdate.path pipbox-remote.path pipbox-wifi.path pipbox-power.path pipbox-health.service pipbox.service pipbox-dji.service || true
    rm -f /etc/systemd/system/pipbox-send.service /etc/systemd/system/pipbox-send-ctl.service /etc/systemd/system/pipbox-send-ctl.path /etc/systemd/system/pipbox.service /etc/systemd/system/pipbox-dji.service /etc/systemd/system/pipbox-update.service /etc/systemd/system/pipbox-update.path /etc/systemd/system/pipbox-swupdate.service /etc/systemd/system/pipbox-swupdate.path /etc/systemd/system/pipbox-remote.service /etc/systemd/system/pipbox-remote.path /etc/systemd/system/pipbox-wifi.service /etc/systemd/system/pipbox-wifi.path /etc/systemd/system/pipbox-power.service /etc/systemd/system/pipbox-power.path /etc/systemd/system/pipbox-health.service
    rm -f /usr/local/bin/srtla_send
    rm -rf /opt/pipbox
    echo "Passwort und Kameraliste bleiben in /var/lib/pipbox (zum Löschen manuell entfernen)."
    systemctl daemon-reload
    echo "PIPBOX entfernt."
    ;;
  *) echo "Aufruf: install.sh [install|uninstall]"; exit 1 ;;
esac
