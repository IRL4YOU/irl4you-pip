#!/bin/sh
set -u
HERE=${1:-$(cd "$(dirname "$0")/.." && pwd)}
D=/opt/pipbox/privacy
RT_VER=2.3.2
RAW=https://github.com/airockchip/rknn-toolkit2/raw/v$RT_VER
DATA_URL=https://github.com/IRL4YOU/irl4you-pip/releases/download/test-1/testdaten-1.tar
DATA_SHA=6b104edd1039bc52dbe367195ccf5a452147f07a371e92359685e617d913a1c1
mkdir -p "$D/modelle" "$D/privacy"
install -m 644 "$HERE/pipbox_privacy.py" "$D/pipbox_privacy.py"
for f in "$HERE"/privacy/*.py; do install -m 644 "$f" "$D/privacy/$(basename "$f")"; done
[ -f "$HERE/privacy-modelle/yunet.onnx" ] && install -m 644 "$HERE/privacy-modelle/yunet.onnx" "$D/modelle/yunet.onnx"
install -m 755 "$HERE/install/pipbox-privacy.py" /opt/pipbox/pipbox-privacy.py
install -m 644 "$HERE/install/pipbox-privacy.service" /etc/systemd/system/pipbox-privacy.service
if [ ! -f "$D/modelle/yolox_s_fp16.rknn" ] || [ ! -f "$D/modelle/plate_raw_fp16.rknn" ]; then
  if [ -f "$HERE/privacy-modelle/yolox_s_fp16.rknn" ] && [ -f "$HERE/privacy-modelle/plate_raw_fp16.rknn" ]; then
    install -m 644 "$HERE"/privacy-modelle/*.rknn "$D/modelle/"
  else
    python3 - "$DATA_URL" "$DATA_SHA" "$D/modelle" <<'P' || echo "WARNUNG: Die Daten für die Testfunktion konnten nicht geladen werden."
import hashlib, io, sys, tarfile, urllib.request
url, sha, dest = sys.argv[1:4]
req = urllib.request.Request(url, headers={"User-Agent": "irl4you-box-update"})
with urllib.request.urlopen(req, timeout=60) as r:
    if not r.geturl().startswith("https://"):
        raise SystemExit("keine HTTPS-Antwort")
    data = r.read(120 * 1024 * 1024 + 1)
if len(data) > 120 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != sha:
    raise SystemExit("Prüfsumme stimmt nicht")
with tarfile.open(fileobj=io.BytesIO(data)) as t:
    for m in t:
        if m.isfile() and "/" not in m.name and m.name.endswith(".rknn"):
            open(dest + "/" + m.name, "wb").write(t.extractfile(m).read())
P
  fi
fi
if [ ! -f /usr/lib/librknnrt.so ]; then
  python3 - "$RAW/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so" <<'P' || echo "WARNUNG: Eine Laufzeitdatei konnte nicht geladen werden."
import os, sys, urllib.request
open("/usr/lib/librknnrt.so.new", "wb").write(urllib.request.urlopen(sys.argv[1], timeout=120).read())
os.replace("/usr/lib/librknnrt.so.new", "/usr/lib/librknnrt.so")
P
fi
if [ ! -x "$D/venv/bin/python" ] || ! "$D/venv/bin/python" -c "import cv2, numpy, rknnlite.api" 2>/dev/null; then
  python3 -m venv "$D/venv" 2>/dev/null || { apt-get install -y python3-venv >/dev/null 2>&1 && python3 -m venv "$D/venv"; } || { echo "WARNUNG: Python-Umgebung konnte nicht angelegt werden."; exit 0; }
  "$D/venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1
  "$D/venv/bin/python" -m pip install --quiet numpy opencv-python-headless psutil ruamel.yaml fast-histogram \
    "$RAW/rknn-toolkit-lite2/packages/rknn_toolkit_lite2-$RT_VER-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl" \
    || echo "WARNUNG: Python-Pakete für die Testfunktion konnten nicht installiert werden."
fi
id -nG pipbox | grep -qw video || usermod -aG video,render pipbox 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now pipbox-privacy.service
systemctl restart pipbox-privacy.service
echo "Testfunktionen eingerichtet."
