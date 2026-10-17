#!/bin/sh
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"

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
    echo "PIPBOX-STEP pakete"
    need=""
    for p in bluez python3-dbus python3-gi gstreamer1.0-tools; do dpkg -s "$p" >/dev/null 2>&1 || need="$need $p"; done
    if [ -n "$need" ]; then
      echo "Installiere fehlende Pakete:$need"
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need \
        || { apt-get update >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need; } \
        || echo "WARNUNG: Pakete konnten nicht installiert werden (Internet?). Die DJI-Kameras gehen erst, wenn bluez, python3-dbus und python3-gi da sind; Alle Kameras immer bereit und die HDMI-Kamera brauchen gstreamer1.0-tools."
    fi
    if ! python3 -c "import bleak" 2>/dev/null; then
      echo "Installiere die Bluetooth-Bibliothek bleak (pip)"
      dpkg -s python3-pip >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-pip || true
      pipout=$(pip3 install --disable-pip-version-check "bleak>=0.22" 2>&1) || true
      printf '%s\n' "$pipout" | grep -v "Running pip as the 'root' user" || true
      if ! python3 -c "import bleak" 2>/dev/null; then
        echo "FEHLER: Die Bluetooth-Bibliothek bleak konnte nicht installiert werden (Internet? pip3?). Der DJI-Dienst braucht sie." >&2
        echo "Von Hand: sudo apt-get install -y python3-pip && sudo pip3 install bleak, danach install.sh erneut ausführen." >&2
        exit 1
      fi
    fi
    getent group bluetooth >/dev/null || groupadd --system bluetooth
    getent group input >/dev/null || groupadd --system input
    echo "PIPBOX-STEP dienst"
    systemctl stop pipbox.service 2>/dev/null || true
    if ! grep -q '^pipbox:' /etc/passwd; then
      useradd --system --no-create-home --home-dir /var/lib/pipbox --shell /usr/sbin/nologin \
        --groups bluetooth pipbox
    fi
    if [ -L /var/lib/pipbox ]; then
      old=$(readlink -f /var/lib/pipbox)
      rm /var/lib/pipbox
      mkdir -p /var/lib/pipbox
      cp -a "$old"/. /var/lib/pipbox/
      echo "Alte Daten aus $old übernommen (Original bleibt als Sicherung liegen)."
    fi
    mkdir -p /var/lib/pipbox
    chown -R pipbox:pipbox /var/lib/pipbox
    chmod 700 /var/lib/pipbox
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
    echo "PIPBOX-STEP dateien"
    install -d /opt/pipbox/web
    dji_changed=0
    for f in dji.py dji_daemon.py phone_battery.py controllers.py; do cmp -s "$HERE/$f" "/opt/pipbox/$f" || dji_changed=1; done
    cmp -s "$HERE/install/pipbox-dji.service" /etc/systemd/system/pipbox-dji.service || dji_changed=1
    hdmi_changed=0
    cmp -s "$HERE/hdmi_daemon.py" /opt/pipbox/hdmi_daemon.py || hdmi_changed=1
    cmp -s "$HERE/install/pipbox-hdmi.service" /etc/systemd/system/pipbox-hdmi.service || hdmi_changed=1
    install -m 644 "$HERE/server.py" /opt/pipbox/server.py
    [ -f "$HERE/twitch.py" ] && install -m 644 "$HERE/twitch.py" /opt/pipbox/twitch.py
    install -m 644 "$HERE/dji.py" /opt/pipbox/dji.py
    install -m 644 "$HERE/dji_daemon.py" /opt/pipbox/dji_daemon.py
    install -m 644 "$HERE/phone_battery.py" /opt/pipbox/phone_battery.py
    install -m 644 "$HERE/controllers.py" /opt/pipbox/controllers.py
    install -m 644 "$HERE/controller_keys.py" /opt/pipbox/controller_keys.py
    install -m 644 "$HERE/hdmi_daemon.py" /opt/pipbox/hdmi_daemon.py
    install -m 644 "$HERE/pipbox_send.py" /opt/pipbox/pipbox_send.py
    install -m 644 "$HERE/pipbox_send_ctl.py" /opt/pipbox/pipbox_send_ctl.py
    install -m 644 "$HERE/pipbox_live.py" /opt/pipbox/pipbox_live.py
    install -m 644 "$HERE/pipbox_watch.py" /opt/pipbox/pipbox_watch.py
    install -m 644 "$HERE/pipbox_preview.py" /opt/pipbox/pipbox_preview.py
    install -m 644 "$HERE/install/pipbox-preview.socket" /etc/systemd/system/pipbox-preview.socket
    install -m 644 "$HERE/install/pipbox-preview.service" /etc/systemd/system/pipbox-preview.service
    install -m 644 "$HERE/pipbox_always.py" /opt/pipbox/pipbox_always.py
    install -m 755 "$HERE/install/pipbox_health.py" /opt/pipbox/pipbox_health.py
    install -m 644 "$HERE/VERSION" /opt/pipbox/VERSION
    install -m 755 "$HERE/install/pipbox-swupdate.py" /opt/pipbox/pipbox-swupdate.py
    install -m 644 "$HERE/install/pipbox-swupdate.service" /etc/systemd/system/pipbox-swupdate.service
    install -m 644 "$HERE/install/pipbox-swupdate.path" /etc/systemd/system/pipbox-swupdate.path
    install -m 755 "$HERE/install/pipbox-remote.py" /opt/pipbox/pipbox-remote.py
    install -m 644 "$HERE/install/pipbox-remote.service" /etc/systemd/system/pipbox-remote.service
    install -m 644 "$HERE/install/pipbox-remote.path" /etc/systemd/system/pipbox-remote.path
    install -m 644 "$HERE/install/pipbox-remote-heal.service" /etc/systemd/system/pipbox-remote-heal.service
    install -m 644 "$HERE/install/pipbox-remote-heal.timer" /etc/systemd/system/pipbox-remote-heal.timer
    mkdir -p /etc/systemd/system/tailscaled.service.d
    install -m 644 "$HERE/install/tailscaled-pipbox-order.conf" /etc/systemd/system/tailscaled.service.d/pipbox-order.conf
    systemctl disable --now pipbox-funnel-guard.timer 2>/dev/null || true
    rm -f /etc/systemd/system/pipbox-funnel-guard.service /etc/systemd/system/pipbox-funnel-guard.timer
    install -d /opt/pipbox/btusb-src
    for f in btusb.c btintel.h btbcm.h btrtl.h COPYING README.md; do install -m 644 "$HERE/bluetooth-src/$f" "/opt/pipbox/btusb-src/$f"; done
    install -m 755 "$HERE/install/pipbox-btdriver.py" /opt/pipbox/pipbox-btdriver.py
    install -m 644 "$HERE/install/pipbox-btdriver.service" /etc/systemd/system/pipbox-btdriver.service
    install -m 644 "$HERE/install/pipbox-btdriver.timer" /etc/systemd/system/pipbox-btdriver.timer
    install -m 644 "$HERE/install/pipbox-btdriver.path" /etc/systemd/system/pipbox-btdriver.path
    install -m 644 "$HERE/install/80-pipbox-btdriver.rules" /etc/udev/rules.d/80-pipbox-btdriver.rules
    install -m 755 "$HERE/install/pipbox-btchip.py" /opt/pipbox/pipbox-btchip.py
    install -m 644 "$HERE/install/pipbox-btchip.service" /etc/systemd/system/pipbox-btchip.service
    install -m 644 "$HERE/install/pipbox-btchip.path" /etc/systemd/system/pipbox-btchip.path
    install -m 644 "$HERE/install/81-pipbox-btchip.rules" /etc/udev/rules.d/81-pipbox-btchip.rules
    install -m 755 "$HERE/install/pipbox-arp.py" /opt/pipbox/pipbox-arp.py
    install -m 644 "$HERE/install/pipbox-arp.service" /etc/systemd/system/pipbox-arp.service
    install -m 755 "$HERE/install/pipbox-wlandriver.py" /opt/pipbox/pipbox-wlandriver.py
    install -m 644 "$HERE/install/pipbox-wlandriver.service" /etc/systemd/system/pipbox-wlandriver.service
    install -m 644 "$HERE/install/pipbox-wlandriver.timer" /etc/systemd/system/pipbox-wlandriver.timer
    install -m 644 "$HERE/install/81-pipbox-wlandriver.rules" /etc/udev/rules.d/81-pipbox-wlandriver.rules
    udevadm control --reload-rules 2>/dev/null || true
    install -m 755 "$HERE/install/pipbox-wifi.py" /opt/pipbox/pipbox-wifi.py
    install -m 644 "$HERE/install/pipbox-wifi.service" /etc/systemd/system/pipbox-wifi.service
    install -m 644 "$HERE/install/pipbox-wifi.path" /etc/systemd/system/pipbox-wifi.path
    install -m 755 "$HERE/install/pipbox-power.py" /opt/pipbox/pipbox-power.py
    install -m 755 "$HERE/install/pipbox-logmode.py" /opt/pipbox/pipbox-logmode.py
    install -m 644 "$HERE/install/pipbox-logmode.service" /etc/systemd/system/pipbox-logmode.service
    install -m 644 "$HERE/install/pipbox-logmode.path" /etc/systemd/system/pipbox-logmode.path
    install -m 644 "$HERE/install/pipbox-logrotate.service" /etc/systemd/system/pipbox-logrotate.service
    install -m 644 "$HERE/install/pipbox-logrotate.timer" /etc/systemd/system/pipbox-logrotate.timer
    install -m 755 "$HERE/install/pipbox-netaddr.py" /opt/pipbox/pipbox-netaddr.py
    install -m 644 "$HERE/install/pipbox-netaddr.service" /etc/systemd/system/pipbox-netaddr.service
    install -m 644 "$HERE/install/pipbox-netaddr.path" /etc/systemd/system/pipbox-netaddr.path
    install -m 755 "$HERE/install/pipbox-logs.py" /opt/pipbox/pipbox-logs.py
    install -m 644 "$HERE/install/pipbox-logs.service" /etc/systemd/system/pipbox-logs.service
    install -m 644 "$HERE/install/pipbox-logs.path" /etc/systemd/system/pipbox-logs.path
    install -m 755 "$HERE/install/pipbox-ssh.py" /opt/pipbox/pipbox-ssh.py
    install -m 644 "$HERE/install/pipbox-ssh.service" /etc/systemd/system/pipbox-ssh.service
    install -m 644 "$HERE/install/pipbox-ssh.path" /etc/systemd/system/pipbox-ssh.path
    install -d /etc/pipbox
    if [ ! -f /etc/pipbox/logmode ]; then
      if [ -f /etc/systemd/journald.conf.d/pipbox-persistent.conf ]; then mode=ausfuehrlich; else mode=sparsam; fi
      python3 /opt/pipbox/pipbox-logmode.py --apply "$mode" || echo "WARNUNG: Protokoll-Modus konnte nicht gesetzt werden."
    fi
    python3 /opt/pipbox/pipbox-logmode.py --apply-nginx || true
    install -m 644 "$HERE/install/pipbox-power.service" /etc/systemd/system/pipbox-power.service
    install -m 644 "$HERE/install/pipbox-power.path" /etc/systemd/system/pipbox-power.path
    install -m 644 "$HERE/install/pipbox-health.service" /etc/systemd/system/pipbox-health.service
    install -m 644 "$HERE/web/index.html" /opt/pipbox/web/index.html
    install -m 644 "$HERE/web/login.html" /opt/pipbox/web/login.html
    install -m 644 "$HERE/web/statistik.html" /opt/pipbox/web/statistik.html
    install -m 644 "$HERE/web/i18n.js" /opt/pipbox/web/i18n.js
    for f in chat.js chat.css; do [ -f "$HERE/web/$f" ] && install -m 644 "$HERE/web/$f" "/opt/pipbox/web/$f"; done
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
    echo "PIPBOX-STEP baustein"
    install -d /opt/pipbox/gst-src
    gst_changed=0
    cmp -s "$HERE/gst/gstpbpip.c" /opt/pipbox/gst-src/gstpbpip.c || gst_changed=1
    install -m 644 "$HERE/gst/gstpbpip.c" /opt/pipbox/gst-src/gstpbpip.c
    install -m 755 "$HERE/gst/build.sh" /opt/pipbox/gst-src/build.sh
    if [ ! -f /opt/pipbox/gst/libgstpbpip.so ] || [ "$gst_changed" = 1 ]; then
      sh /opt/pipbox/gst-src/build.sh || echo "WARNUNG: Der PiP-Baustein konnte nicht gebaut werden; Bild-in-Bild bleibt gesperrt."
    fi
    echo "PIPBOX-STEP srtla"
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
    echo "PIPBOX-STEP belacoder"
    install -d /opt/pipbox/belacoder
    bc_changed=0
    for bp in belacoder-jitter-tolerant.patch belacoder-stall-output.patch belacoder-stats.patch belacoder-live-feeds.patch belacoder-frame-copy.patch belacoder-no-reconfigure.patch belacoder-audio-eos.patch belacoder-feed-shift.patch belacoder-fast-decrease.patch; do
      cmp -s "$HERE/belacoder/$bp" "/opt/pipbox/belacoder/$bp" || bc_changed=1
    done
    cmp -s "$HERE/belacoder/build.sh" /opt/pipbox/belacoder/build.sh || bc_changed=1
    [ -x /opt/pipbox/bin/belacoder ] || bc_changed=1
    if [ "$bc_changed" = 1 ]; then
      if sh "$HERE/belacoder/build.sh"; then
        for bp in belacoder-jitter-tolerant.patch belacoder-stall-output.patch belacoder-stats.patch belacoder-live-feeds.patch belacoder-frame-copy.patch belacoder-no-reconfigure.patch belacoder-audio-eos.patch belacoder-feed-shift.patch belacoder-fast-decrease.patch; do
          install -m 644 "$HERE/belacoder/$bp" "/opt/pipbox/belacoder/$bp"
        done
        install -m 644 "$HERE/belacoder/build.sh" /opt/pipbox/belacoder/build.sh
      else
        echo "WARNUNG: belacoder mit dem toleranten Regler konnte nicht gebaut werden (braucht git, gcc, patch, Internet). Es bleibt das Original."
      fi
    fi
    install -m 755 "$HERE/install/pipbox-nginx-guard.sh" /opt/pipbox/pipbox-nginx-guard.sh
    install -m 644 "$HERE/install/99pipbox-nginx" /etc/apt/apt.conf.d/99pipbox-nginx
    /opt/pipbox/pipbox-nginx-guard.sh
    echo "PIPBOX-STEP start"
    systemctl daemon-reload
    systemctl enable pipbox.service pipbox-dji.service pipbox-hdmi.service
    if [ "$dji_changed" = 1 ] || ! systemctl is-active --quiet pipbox-dji.service; then systemctl restart pipbox-dji.service; fi
    if [ "$hdmi_changed" = 1 ] || ! systemctl is-active --quiet pipbox-hdmi.service; then systemctl restart pipbox-hdmi.service; fi
    systemctl restart pipbox-health.service 2>/dev/null || true
    systemctl enable --now pipbox-update.path pipbox-send-ctl.path pipbox-health.service pipbox-swupdate.path pipbox-remote.path pipbox-remote-heal.timer pipbox-wifi.path pipbox-power.path pipbox-logmode.path pipbox-netaddr.path pipbox-logs.path pipbox-ssh.path pipbox-btdriver.timer pipbox-btdriver.path pipbox-wlandriver.timer pipbox-btchip.path pipbox-btchip.service pipbox-arp.service pipbox-logrotate.timer
    systemctl enable --now pipbox-preview.socket
    systemctl try-restart pipbox-preview.service 2>/dev/null || true
    systemctl try-restart pipbox-arp.service 2>/dev/null || true
    systemctl restart pipbox.service
    systemctl start --no-block pipbox-btdriver.service pipbox-wlandriver.service pipbox-btchip.service 2>/dev/null || true
    [ -x /opt/pipbox/pipbox-wifi.py ] && python3 /opt/pipbox/pipbox-wifi.py migrate || true
    ts_snapshot "nach der Installation"
    echo "IRL4YOU BOX läuft auf Port 8780 im lokalen Netz. Ersteinrichtung im Browser."
    ;;
  uninstall)
    [ -x /opt/pipbox/pipbox-netaddr.py ] && python3 /opt/pipbox/pipbox-netaddr.py --remove || true
    [ -x /opt/pipbox/pipbox-wlandriver.py ] && python3 /opt/pipbox/pipbox-wlandriver.py uninstall || true
    systemctl disable --now pipbox-arp.service 2>/dev/null || true
    [ -x /opt/pipbox/pipbox-arp.py ] && python3 /opt/pipbox/pipbox-arp.py uninstall || true
    [ -x /opt/pipbox/pipbox-btchip.py ] && python3 /opt/pipbox/pipbox-btchip.py uninstall || true
    [ -x /opt/pipbox/pipbox-btdriver.py ] && python3 /opt/pipbox/pipbox-btdriver.py uninstall || true
    systemctl disable --now pipbox-arp.service pipbox-btchip.path pipbox-btchip.service pipbox-btdriver.timer pipbox-btdriver.path pipbox-wlandriver.timer pipbox-funnel-guard.timer pipbox-send.service pipbox-hdmi.service pipbox-send-ctl.path pipbox-update.path pipbox-swupdate.path pipbox-remote.path pipbox-remote-heal.timer pipbox-wifi.path pipbox-power.path pipbox-logmode.path pipbox-netaddr.path pipbox-logs.path pipbox-ssh.path pipbox-health.service pipbox.service pipbox-dji.service pipbox-logrotate.timer || true
    rm -f /etc/systemd/system/pipbox-send.service /etc/systemd/system/pipbox-send-ctl.service /etc/systemd/system/pipbox-send-ctl.path /etc/systemd/system/pipbox.service /etc/systemd/system/pipbox-dji.service /etc/systemd/system/pipbox-hdmi.service /etc/systemd/system/pipbox-update.service /etc/systemd/system/pipbox-update.path /etc/systemd/system/pipbox-swupdate.service /etc/systemd/system/pipbox-swupdate.path /etc/systemd/system/pipbox-remote.service /etc/systemd/system/pipbox-remote.path /etc/systemd/system/pipbox-remote-heal.service /etc/systemd/system/pipbox-remote-heal.timer /etc/systemd/system/tailscaled.service.d/pipbox-order.conf /etc/systemd/system/pipbox-wifi.service /etc/systemd/system/pipbox-wifi.path /etc/systemd/system/pipbox-power.service /etc/systemd/system/pipbox-power.path /etc/systemd/system/pipbox-health.service /etc/systemd/system/pipbox-logmode.service /etc/systemd/system/pipbox-logmode.path /etc/systemd/system/pipbox-netaddr.service /etc/systemd/system/pipbox-netaddr.path /etc/systemd/system/pipbox-logs.service /etc/systemd/system/pipbox-logs.path /etc/systemd/system/pipbox-ssh.service /etc/systemd/system/pipbox-ssh.path /etc/systemd/system/pipbox-funnel-guard.service /etc/systemd/system/pipbox-funnel-guard.timer /etc/systemd/system/pipbox-btchip.service /etc/systemd/system/pipbox-btchip.path /etc/udev/rules.d/81-pipbox-btchip.rules /opt/pipbox/pipbox-btchip.py /etc/systemd/system/pipbox-arp.service /opt/pipbox/pipbox-arp.py /etc/systemd/system/pipbox-btdriver.service /etc/systemd/system/pipbox-btdriver.timer /etc/systemd/system/pipbox-btdriver.path /etc/udev/rules.d/80-pipbox-btdriver.rules
    rmdir /etc/systemd/system/tailscaled.service.d 2>/dev/null || true
    systemctl disable --now pipbox-preview.socket pipbox-preview.service 2>/dev/null || true
    rm -f /etc/systemd/system/pipbox-preview.socket /etc/systemd/system/pipbox-preview.service
    rm -f /etc/apt/apt.conf.d/99pipbox-nginx
    rm -f /etc/systemd/system/pipbox-logrotate.service /etc/systemd/system/pipbox-logrotate.timer
    NGX=/etc/nginx/modules-available/99-belabox-rtmp.conf
    if [ -f "$NGX.vor-pipbox" ]; then
      cp "$NGX.vor-pipbox" "$NGX" && rm -f "$NGX.vor-pipbox"
      nginx -t >/dev/null 2>&1 && nginx -s reload 2>/dev/null || true
    fi
    if [ -f /etc/nginx/conf.d/pipbox-log.conf ]; then
      rm -f /etc/nginx/conf.d/pipbox-log.conf
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
