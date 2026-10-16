#!/bin/sh
# IRL4YOU BOX: hält die RTMP-Leerlaufgrenze der Kameras auf 15 s.
#
# Der RTMP-Server der BELABOX (Paket belabox-rtmp-server) wirft eine Kamera, die 4 s lang nichts schickt, aus dem Bild
# (drop_idle_publisher 4s). DJI-Kameras setzen im WLAN gelegentlich 4 bis 10 s aus; jedes Mal startet der Encoder mehrfach neu.
# Die Datei gehört dem Paket und ist KEINE Konfigurationsdatei im Sinne von dpkg: jedes Update von belabox-rtmp-server
# überschreibt sie stillschweigend wieder mit 4 s. Deshalb ruft dieses Skript install.sh auf, und ein apt-Haken
# (/etc/apt/apt.conf.d/99pipbox-nginx) ruft es nach jedem Paketlauf auf, auch nach Updates über die BELABOX-Oberfläche.
#
# Ändert nur die Zeile mit "4s;" (steht schon etwas anderes da, passiert nichts). Sicherung: <Datei>.vor-pipbox (zeigt immer
# den letzten unveränderten Stand des Pakets; "install.sh uninstall" stellt ihn her). nginx wird nur neu geladen, wenn
# gerade nicht gesendet wird, denn ein Neuladen trennt alle Kameras (ca. 1 min ohne Bild). Sonst gilt der neue Wert beim
# nächsten Start von nginx (spätestens nach dem nächsten Neustart der Box). Beendet sich immer mit 0 (darf apt nie stören).
NGX=/etc/nginx/modules-available/99-belabox-rtmp.conf
TMP="$NGX.pipbox-neu"

[ -f "$NGX" ] || exit 0
grep -Eq '^[[:space:]]*drop_idle_publisher[[:space:]]+4s;' "$NGX" || exit 0

sending=0
for d in /proc/[0-9]*; do
  [ "$(cat "$d/comm" 2>/dev/null)" = belacoder ] && sending=1
done

cp "$NGX" "$NGX.vor-pipbox" 2>/dev/null || exit 0
sed '/drop_idle_publisher/s/4s;/15s;/' "$NGX.vor-pipbox" > "$TMP" && cat "$TMP" > "$NGX"
rm -f "$TMP"
if ! grep -Eq '^[[:space:]]*drop_idle_publisher[[:space:]]+15s;' "$NGX" || ! nginx -t >/dev/null 2>&1; then
  cat "$NGX.vor-pipbox" > "$NGX"
  echo "WARNUNG: Die RTMP-Einstellung konnte nicht geändert werden oder nginx lehnt sie ab; sie wurde zurückgesetzt."
  logger -t pipbox "RTMP-Leerlaufgrenze nicht geändert (Änderung oder nginx -t fehlgeschlagen)" 2>/dev/null
  exit 0
fi
if [ "$sending" = 0 ]; then
  nginx -s reload 2>/dev/null
  echo "RTMP-Leerlaufgrenze der Kameras auf 15 s gesetzt (Sicherung: $NGX.vor-pipbox)."
else
  echo "RTMP-Leerlaufgrenze auf 15 s gesetzt; wirkt nach dem nächsten Start von nginx, denn es wird gerade gesendet."
fi
logger -t pipbox "RTMP-Leerlaufgrenze der Kameras auf 15 s gesetzt (Senden: $sending)" 2>/dev/null
exit 0
