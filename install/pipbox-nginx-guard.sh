#!/bin/sh
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
