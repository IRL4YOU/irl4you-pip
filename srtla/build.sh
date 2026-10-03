#!/bin/sh
# Baut den latenzbewussten srtla_send (BELABOX/srtla, Commit 37862da, AGPL-3.0) mit dem Patch aus diesem Ordner.
# Braucht gcc, make, git, patch und Internet. Installiert nach /usr/local/bin/srtla_send (das Original aus dem
# BELABOX-Paket bleibt unter /usr/bin unberührt und dient als Rückfall). Als root ausführen.
# SRTLA_OUT=/anderer/Ordner installiert dorthin (zum Testen).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${SRTLA_OUT:-/usr/local/bin}
COMMIT=37862da3d0c13b46956efd3f88877053293d97d6
D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT
git clone --quiet https://github.com/BELABOX/srtla "$D/srtla"
cd "$D/srtla"
git checkout --quiet "$COMMIT"
patch -p1 --quiet < "$HERE/srtla_send-latency-aware.patch"
make srtla_send CFLAGS='-O2 -Wall -DVERSION=\"irl4you-lat-2+37862da\"' >/dev/null
mkdir -p "$OUT"
install -m 755 srtla_send "$OUT/srtla_send.new"
mv -f "$OUT/srtla_send.new" "$OUT/srtla_send"      # ersetzen statt überschreiben: ein laufender Sender bleibt heil
echo "srtla_send gebaut: $OUT/srtla_send"
