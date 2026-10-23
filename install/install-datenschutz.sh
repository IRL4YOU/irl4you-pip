#!/bin/sh
set -u
HERE=${1:-$(cd "$(dirname "$0")/.." && pwd)}
D=/opt/pipbox/privacy
RT_VER=2.3.2
RAW=https://github.com/airockchip/rknn-toolkit2/raw/v$RT_VER
mkdir -p "$D/modelle" "$D/privacy"
echo "PIPBOX-ADDON programme"
MODELL_URL=${PB_MODELL_URL:-https://github.com/IRL4YOU/irl4you-pip/releases/download/erkennung-5}
laden() {
  python3 - "$1" "$2" <<'P'
import sys, urllib.request
open(sys.argv[2], "wb").write(urllib.request.urlopen(sys.argv[1], timeout=180).read())
P
}
if [ -f "$HERE/pipbox_privacy.py" ]; then
  install -m 644 "$HERE/pipbox_privacy.py" "$D/pipbox_privacy.py"
  for f in "$HERE"/privacy/*.py; do install -m 644 "$f" "$D/privacy/$(basename "$f")"; done
  rm -f "$D/pipbox_privacy.pyc" "$D"/privacy/*.pyc
else
  read -r cn csha rest < "$HERE/privacy-modelle/CODE"
  f="$HERE/privacy-modelle/$cn"
  if [ ! -f "$f" ] || [ "$(sha256sum "$f" | cut -d' ' -f1)" != "$csha" ]; then
    rm -f "$D/$cn.laden"
    laden "$MODELL_URL/$cn" "$D/$cn.laden" || { echo "WARNUNG: Die Programme der Erkennung konnten nicht geladen werden."; rm -f "$D/$cn.laden"; exit 1; }
    f="$D/$cn.laden"
    [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$csha" ] || { echo "WARNUNG: Die Programme der Erkennung haben eine falsche Prüfsumme."; rm -f "$f"; exit 1; }
  fi
  python3 - "$f" "$D" <<'P' || { echo "WARNUNG: Die Programme der Erkennung konnten nicht eingespielt werden."; rm -f "$D/$cn.laden"; exit 1; }
import os, re, sys, tarfile
ok = re.compile(r"^(pipbox_privacy\.pyc|privacy/[a-z_]+\.pyc)$")
with tarfile.open(sys.argv[1]) as t:
    ms = t.getmembers()
    if not ms or any(not m.isfile() or not ok.match(m.name) for m in ms):
        raise SystemExit("unzulässiger Inhalt")
    for m in ms:
        dst = os.path.join(sys.argv[2], m.name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst + ".neu", "wb") as out:
            out.write(t.extractfile(m).read())
        os.replace(dst + ".neu", dst)
P
  rm -f "$D/$cn.laden" "$D/pipbox_privacy.py" "$D"/privacy/*.py
  echo "PIPBOX-ADDON programme"
fi
install -m 644 "$HERE/LICENSE-ERKENNUNG.md" "$D/LICENSE-ERKENNUNG.md"
echo "PIPBOX-ADDON modelle"
fehlt=0
while read -r nm sha rest; do
  [ -n "$nm" ] || continue
  opt=0; [ "$nm" = "kopf_klein" ] && opt=1
  dst="$D/modelle/${nm}_fp16.rknn"
  if [ -f "$dst" ] && [ "$(sha256sum "$dst" | cut -d' ' -f1)" = "$sha" ]; then continue; fi
  f="$HERE/privacy-modelle/${nm}_fp16.rknn"
  if [ ! -f "$f" ] || [ "$(sha256sum "$f" | cut -d' ' -f1)" != "$sha" ]; then
    laden "$MODELL_URL/${nm}_fp16.rknn" "$D/modelle/${nm}_fp16.rknn.laden" || { echo "WARNUNG: Modell $nm konnte nicht geladen werden."; rm -f "$D/modelle/${nm}_fp16.rknn.laden"; [ "$opt" = 1 ] || fehlt=1; continue; }
    f="$D/modelle/${nm}_fp16.rknn.laden"
    if [ "$(sha256sum "$f" | cut -d' ' -f1)" != "$sha" ]; then echo "WARNUNG: Modell $nm hat eine falsche Prüfsumme, wird nicht eingespielt."; rm -f "$f"; [ "$opt" = 1 ] || fehlt=1; continue; fi
  fi
  install -m 644 "$f" "$dst.neu" && mv -f "$dst.neu" "$dst"
  rm -f "$D/modelle/${nm}_fp16.rknn.laden"
done < "$HERE/privacy-modelle/MODELLE"
[ "$fehlt" = 0 ] || { echo "WARNUNG: Modelle fehlen, die Erkennung ist nicht eingerichtet."; exit 1; }
for f in yolox_s_fp16.rknn plate_raw_fp16.rknn yunet.onnx kopfnetz_fp16.rknn; do rm -f "$D/modelle/$f"; done
for f in backends.py detect.py track.py; do rm -f "$D/privacy/$f"; done
rm -f "$D/modelle/module.json"
install -m 755 "$HERE/install/pipbox-privacy.py" /opt/pipbox/pipbox-privacy.py
install -m 644 "$HERE/install/pipbox-privacy.service" /etc/systemd/system/pipbox-privacy.service
if [ ! -f /opt/pipbox/gst/libgstpbpixel.so ] && [ -f /opt/pipbox/gst-src/build.sh ]; then
  sh /opt/pipbox/gst-src/build.sh 2>&1 | tail -3
fi
[ -f /opt/pipbox/gst/libgstpbpixel.so ] || { echo "WARNUNG: Der Bildbaustein der Erkennung fehlt (Internet für die Header nötig)."; exit 1; }
echo "PIPBOX-ADDON npu"
if [ ! -f /usr/lib/librknnrt.so ]; then
  python3 - "$RAW/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so" <<'P' || echo "WARNUNG: Die NPU-Laufzeit konnte nicht geladen werden."
import os, sys, urllib.request
open("/usr/lib/librknnrt.so.new", "wb").write(urllib.request.urlopen(sys.argv[1], timeout=120).read())
os.replace("/usr/lib/librknnrt.so.new", "/usr/lib/librknnrt.so")
P
fi
echo "PIPBOX-ADDON umgebung"
mkvenv() {
  rm -rf "$D/venv"
  python3 -m venv "$D/venv" >/dev/null 2>&1 && return 0
  rm -rf "$D/venv"
  python3 -m venv --without-pip "$D/venv" >/dev/null 2>&1 || return 1
  python3 - "$D/get-pip.py" <<'P' || return 1
import sys, urllib.request
open(sys.argv[1], "wb").write(urllib.request.urlopen("https://bootstrap.pypa.io/get-pip.py", timeout=60).read())
P
  "$D/venv/bin/python" "$D/get-pip.py" -q >/dev/null 2>&1
  r=$?
  rm -f "$D/get-pip.py"
  return $r
}
if ! "$D/venv/bin/python" -c "import cv2, numpy, rknnlite.api; assert numpy.__version__ == \"1.26.4\" and cv2.__version__.startswith(\"4.11\")" 2>/dev/null; then
  echo "Richte die Python-Umgebung der Erkennung ein (einmalig, einige Minuten) ..."
  mkvenv || { echo "WARNUNG: Python-Umgebung konnte nicht angelegt werden."; exit 1; }
  "$D/venv/bin/python" -m pip install --quiet numpy==1.26.4 opencv-python-headless==4.11.0.86 psutil \
    "$RAW/rknn-toolkit-lite2/packages/rknn_toolkit_lite2-$RT_VER-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl" >/dev/null 2>&1 || true
  "$D/venv/bin/python" -c "import cv2, numpy, rknnlite.api; assert numpy.__version__ == \"1.26.4\" and cv2.__version__.startswith(\"4.11\")" 2>/dev/null || { echo "WARNUNG: Python-Pakete der Erkennung fehlen."; exit 1; }
fi
chown -R pipbox:pipbox "$D" 2>/dev/null || true
echo "PIPBOX-ADDON dienst"
id -nG pipbox | grep -qw video || usermod -aG video,render pipbox 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now pipbox-privacy.service
systemctl restart pipbox-privacy.service
echo "Erkennung (Datenschutz) eingerichtet."
