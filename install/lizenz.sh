# Lizenzabfrage vor dem Laden der Erkennung (wird von install.sh und install-datenschutz.sh eingelesen).
# Angenommen ist die Lizenz, wenn /var/lib/pipbox/datenschutz.json "lizenz_ok": true und die Prüfsumme des Lizenztextes enthält (so schreibt es auch die Karte Verpixeln).
# Am Terminal wird gefragt; ohne Terminal (Update über die Oberfläche) wird nichts geladen, solange die Lizenz nicht angenommen ist.
PB_STATE=${PB_STATE:-/var/lib/pipbox}

lizenz_angenommen() {
  python3 - "$1" "$PB_STATE/datenschutz.json" <<'P'
import hashlib, json, sys
try:
    sha = hashlib.sha256(open(sys.argv[1], encoding="utf-8").read().encode("utf-8")).hexdigest()
    c = json.load(open(sys.argv[2], encoding="utf-8"))
    ok = isinstance(c, dict) and c.get("lizenz_ok") is True and c.get("lizenz_sha") == sha
except Exception:
    ok = False
raise SystemExit(0 if ok else 1)
P
}

lizenz_speichern() {
  python3 - "$1" "$PB_STATE/datenschutz.json" <<'P'
import hashlib, json, os, sys
sha = hashlib.sha256(open(sys.argv[1], encoding="utf-8").read().encode("utf-8")).hexdigest()
try:
    c = json.load(open(sys.argv[2], encoding="utf-8"))
    st = os.stat(sys.argv[2])
except Exception:
    c, st = {}, None
if not isinstance(c, dict):
    c = {}
c["lizenz_ok"], c["lizenz_sha"] = True, sha
tmp = sys.argv[2] + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(c, f, indent=1, ensure_ascii=False)
if st:
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o777)
else:
    os.chmod(tmp, 0o644)
os.replace(tmp, sys.argv[2])
P
}

lizenz_pruefen() {
  lizenz_angenommen "$1" && return 0
  if [ -t 0 ] || [ "${PB_LIZENZ_FRAGEN:-}" = 1 ]; then
    echo
    echo "Die Erkennung (Köpfe und Kennzeichen) ist keine Open-Source-Software, alle Rechte sind vorbehalten."
    echo "Es gilt die Lizenz LICENSE-ERKENNUNG.md: kostenlos für Privatpersonen und Streamer zum Eigengebrauch, nicht für Unternehmen,"
    echo "nicht zum Verkauf oder zur Weitergabe. Text: $1"
    echo "und https://github.com/IRL4YOU/irl4you-pip/blob/main/LICENSE-ERKENNUNG.md"
    printf 'Lizenz lesen und annehmen? [j/N] '
    read -r antwort || antwort=""
    case "$antwort" in
      j|J|ja|Ja|JA|y|Y|yes|Yes) lizenz_speichern "$1" && echo "Lizenz angenommen." && return 0 ;;
    esac
    echo "Nicht angenommen: Es wird nichts geladen."
  fi
  return 1
}
