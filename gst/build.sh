#!/bin/sh
# Baut den PiP-Baustein auf der Box. Lädt nur die Header-Pakete herunter und entpackt sie
# in ein temporäres Verzeichnis (keine 58 Entwicklerpakete installieren). Als root ausführen.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${PBOUT:-/opt/pipbox/gst}
B=/var/tmp/pbbuild
R=$B/root
mkdir -p "$B/debs" "$R" "$OUT"
if [ ! -f "$R/usr/include/gstreamer-1.0/gst/video/gstvideofilter.h" ]; then
  (cd "$B/debs" && apt-get -o APT::Sandbox::User=root download libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libglib2.0-dev \
    && for d in *.deb; do dpkg -x "$d" "$R"; done)
fi
gcc -O2 -Wall -fPIC -shared -DPACKAGE=\"irl4you-pip\" "$HERE/gstpbpip.c" -o "$OUT/libgstpbpip.so.new" \
  -I"$R/usr/include/gstreamer-1.0" -I"$R/usr/lib/aarch64-linux-gnu/gstreamer-1.0/include" \
  -I"$R/usr/include/glib-2.0" -I"$R/usr/lib/aarch64-linux-gnu/glib-2.0/include" \
  -l:libgstvideo-1.0.so.0 -l:libgstbase-1.0.so.0 -l:libgstreamer-1.0.so.0 \
  -l:libgobject-2.0.so.0 -l:libglib-2.0.so.0
mv -f "$OUT/libgstpbpip.so.new" "$OUT/libgstpbpip.so"   # ersetzen statt überschreiben: ein laufender belacoder bleibt heil
echo "Baustein gebaut: $OUT/libgstpbpip.so"
