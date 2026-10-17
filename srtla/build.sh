#!/bin/sh
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
patch -p1 --quiet < "$HERE/srtla_send-min-share.patch"
make srtla_send CFLAGS='-O2 -Wall -DVERSION=\"irl4you-lat-3+37862da\"' >/dev/null
mkdir -p "$OUT"
install -m 755 srtla_send "$OUT/srtla_send.new"
mv -f "$OUT/srtla_send.new" "$OUT/srtla_send"
echo "srtla_send gebaut: $OUT/srtla_send"
