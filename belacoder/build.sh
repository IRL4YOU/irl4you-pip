#!/bin/sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${BELACODER_OUT:-/opt/pipbox/bin}
COMMIT=ccce9ca33c8e425b33353500b95795101e847964
B=/var/tmp/pbbuild
R=$B/root
mkdir -p "$B/debs" "$R" "$OUT"
if [ ! -f "$R/usr/include/gstreamer-1.0/gst/gst.h" ] || [ ! -f "$R/usr/include/gstreamer-1.0/gst/app/gstappsink.h" ]; then
  (cd "$B/debs" && apt-get -o APT::Sandbox::User=root download libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libglib2.0-dev \
    && for d in *.deb; do dpkg -x "$d" "$R"; done)
fi
[ -f /usr/include/srt/srt.h ] || { echo "FEHLER: /usr/include/srt/srt.h fehlt (Paket belabox-libsrt1)"; exit 1; }
D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT
git clone --quiet https://github.com/BELABOX/belacoder "$D/belacoder"
cd "$D/belacoder"
git checkout --quiet "$COMMIT"
patch -p1 --quiet < "$HERE/belacoder-jitter-tolerant.patch"
patch -p1 --quiet < "$HERE/belacoder-stall-output.patch"
patch -p1 --quiet < "$HERE/belacoder-stats.patch"
patch -p1 --quiet < "$HERE/belacoder-live-feeds.patch"
patch -p1 --quiet < "$HERE/belacoder-frame-copy.patch"
patch -p1 --quiet < "$HERE/belacoder-no-reconfigure.patch"
patch -p1 --quiet < "$HERE/belacoder-audio-eos.patch"
patch -p1 --quiet < "$HERE/belacoder-feed-shift.patch"
patch -p1 --quiet < "$HERE/belacoder-fast-decrease.patch"
DBG=""
[ "${BELACODER_DEBUG:-0}" = 1 ] && DBG="-DDEBUG=1"
gcc -O2 -Wall $DBG -DVERSION=\"${COMMIT%${COMMIT#???????}}-jt2-sb12-eos-sh-fd\" belacoder.c -o belacoder.new \
  -I"$R/usr/include/gstreamer-1.0" -I"$R/usr/lib/aarch64-linux-gnu/gstreamer-1.0/include" \
  -I"$R/usr/include/glib-2.0" -I"$R/usr/lib/aarch64-linux-gnu/glib-2.0/include" -I/usr/include/srt \
  -l:libgstapp-1.0.so.0 -l:libgstbase-1.0.so.0 -l:libgstreamer-1.0.so.0 -l:libgobject-2.0.so.0 -l:libglib-2.0.so.0 -lsrt -ldl
if [ -x "$OUT/belacoder" ] && [ ! -e "$OUT/belacoder.vor-stallpatch" ]; then cp -p "$OUT/belacoder" "$OUT/belacoder.vor-stallpatch"; fi
install -m 755 belacoder.new "$OUT/belacoder.new"
mv -f "$OUT/belacoder.new" "$OUT/belacoder"
echo "belacoder gebaut: $OUT/belacoder"
