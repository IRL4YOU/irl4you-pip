"""Tests für die Vorschau des Sendebilds (pipbox_preview.py, Issue #52): der Kernel-Filter (mit einem kleinen BPF-Interpreter geprüft, damit die Sprünge stimmen),
das Lesen der SRT-Datenpakete, die Ordnung der Pakete (Wiederholungen, Lücken, Zählerumlauf), die Kommandozeile des Dekoders, die Grenzen (Bildrate, Breite, Zuschauer)
und der ganze Ablauf mit einem nachgebauten Mitlese-Socket und einem Ersatz für den Dekoder (Ende durch Browser, Zeit, Sender, fehlendes Bild)."""
import json
import os
import socket
import struct
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pipbox_preview as P  # noqa: E402


def udp(dport, payload, ihl=20, proto=17, frag=0):
    ip = bytearray(ihl)
    ip[0] = 0x40 | (ihl // 4)
    ip[6:8] = struct.pack("!H", frag)
    ip[9] = proto
    return bytes(ip) + struct.pack("!HHHH", 5000, dport, 8 + len(payload), 0) + payload


def srt(seq, ts=b"G" + b"\0" * 187, enc=0, ctrl=False):
    w0 = seq | (0x80000000 if ctrl else 0)
    w1 = (0b11 << 30) | (enc << 27)
    return struct.pack("!IIII", w0, w1, 0, 0) + ts


def run_bpf(prog, pkt, pkttype=0):
    """Mini-Interpreter für die Befehle des Filters (ldb, ldh, ld, ldxb, jeq, jset, ret)."""
    ins = [struct.unpack("HBBI", prog[i:i + 8]) for i in range(0, len(prog), 8)]
    a = x = pc = 0
    while True:
        c, jt, jf, k = ins[pc]
        pc += 1
        if c == 0x30:
            a = pkt[k] if k < len(pkt) else None
        elif c == 0x28:
            a = struct.unpack("!H", pkt[k:k + 2])[0] if k + 2 <= len(pkt) else None
        elif c == 0x48:
            a = struct.unpack("!H", pkt[x + k:x + k + 2])[0] if x + k + 2 <= len(pkt) else None
        elif c == 0x20:
            assert k == 0xfffff004
            a = pkttype
        elif c == 0xb1:
            x = (pkt[k] & 0xf) * 4
        elif c == 0x15:
            pc += jt if a == k else jf
        elif c == 0x45:
            pc += jt if (a & k) else jf
        elif c == 0x06:
            return k
        else:
            raise AssertionError("unbekannter Befehl %#x" % c)
        if a is None:
            return 0                                   # Zugriff hinter dem Paket: der Kernel verwirft


class Filter(unittest.TestCase):
    def setUp(self):
        self.prog, self.n = P.bpf_program(9100)

    def test_size(self):
        self.assertEqual(len(self.prog), self.n * 8)

    def test_accepts_udp_to_port(self):
        self.assertGreater(run_bpf(self.prog, udp(9100, srt(1))), 0)

    def test_accepts_longer_ip_header(self):
        self.assertGreater(run_bpf(self.prog, udp(9100, srt(1), ihl=24)), 0)

    def test_drops_other_port(self):
        self.assertEqual(run_bpf(self.prog, udp(1935, b"x" * 100)), 0)

    def test_drops_source_port_only(self):
        pkt = bytearray(udp(8000, srt(1)))
        pkt[20:22] = struct.pack("!H", 9100)                        # Quellport 9100, Ziel 8000
        self.assertEqual(run_bpf(self.prog, bytes(pkt)), 0)

    def test_drops_tcp(self):
        self.assertEqual(run_bpf(self.prog, udp(9100, b"x" * 40, proto=6)), 0)

    def test_drops_fragment(self):
        self.assertEqual(run_bpf(self.prog, udp(9100, srt(1), frag=0x0010)), 0)

    def test_drops_outgoing_copy(self):
        self.assertEqual(run_bpf(self.prog, udp(9100, srt(1)), pkttype=4), 0)

    def test_other_port_number(self):
        prog, _ = P.bpf_program(9200)
        self.assertGreater(run_bpf(prog, udp(9200, srt(1))), 0)
        self.assertEqual(run_bpf(prog, udp(9100, srt(1))), 0)


class SrtData(unittest.TestCase):
    def test_data_packet(self):
        ts = b"G" + b"\x01" * 187
        self.assertEqual(P.srt_data(udp(9100, srt(77, ts))), (77, ts))

    def test_control_packet(self):
        self.assertIsNone(P.srt_data(udp(9100, srt(77, ctrl=True))))

    def test_encrypted(self):
        self.assertIsNone(P.srt_data(udp(9100, srt(77, enc=1))))
        self.assertIsNone(P.srt_data(udp(9100, srt(77, enc=2))))

    def test_short_or_not_udp_or_ipv6(self):
        self.assertIsNone(P.srt_data(b"\x45" + b"\0" * 20))
        self.assertIsNone(P.srt_data(udp(9100, srt(1), proto=6)))
        self.assertIsNone(P.srt_data(b"\x60" + udp(9100, srt(1))[1:]))
        self.assertIsNone(P.srt_data(udp(9100, srt(1))[:40]))

    def test_sequence_uses_31_bits(self):
        self.assertEqual(P.srt_data(udp(9100, srt(0x7fffffff)))[0], 0x7fffffff)


class Order(unittest.TestCase):
    def feed(self, seqs, window=P.REORDER_WINDOW):
        r = P.Reorder(window)
        out = []
        for q in seqs:
            out += r.push(q, b"%d," % q)
        return b"".join(out).decode(), r

    def test_in_order(self):
        self.assertEqual(self.feed([5, 6, 7])[0], "5,6,7,")

    def test_swapped(self):
        self.assertEqual(self.feed([5, 7, 6, 8])[0], "5,6,7,8,")

    def test_duplicate_ignored(self):
        self.assertEqual(self.feed([5, 6, 6, 5, 7])[0], "5,6,7,")

    def test_retransmission_fills_gap(self):
        self.assertEqual(self.feed([1, 2, 4, 5, 3, 6])[0], "1,2,3,4,5,6,")

    def test_gap_given_up(self):
        out, r = self.feed([1, 2] + list(range(4, 12)), window=5)
        self.assertEqual(out, "1,2," + ",".join(map(str, range(4, 12))) + ",")
        self.assertEqual(r.skipped, 1)

    def test_late_after_giveup_dropped(self):
        out, _ = self.feed([1, 2] + list(range(4, 12)) + [3], window=5)
        self.assertNotIn("3,", out.split("2,", 1)[1][:2])
        self.assertTrue(out.endswith("11,"))

    def test_wraparound(self):
        self.assertEqual(self.feed([0x7ffffffe, 0x7fffffff, 0, 1])[0], "%d,%d,0,1," % (0x7ffffffe, 0x7fffffff))

    def test_wraparound_swapped(self):
        self.assertEqual(self.feed([0x7ffffffe, 0, 0x7fffffff, 1])[0], "%d,%d,0,1," % (0x7ffffffe, 0x7fffffff))


class Command(unittest.TestCase):
    def test_codec(self):
        self.assertEqual(P.detect_codec("... mpph265enc zero-copy-pkt=0 ! h265parse"), "h265")
        self.assertEqual(P.detect_codec("... mpph264enc ! h264parse"), "h264")
        self.assertEqual(P.detect_codec("... x265enc"), "h265")
        self.assertEqual(P.detect_codec(None), "h264")

    def test_argv_h265(self):
        a = P.gst_argv("h265", 30, 640)
        s = " ".join(a)
        self.assertIn("video/x-h265", s)
        self.assertIn("h265parse ! mppvideodec", s)
        self.assertIn("framerate=30/1", s)
        self.assertIn("width=640,height=360", s)
        self.assertIn("method=nearest-neighbour", s)
        self.assertIn("multipartmux boundary=pbframe", s)
        self.assertEqual(a[0], "gst-launch-1.0")

    def test_argv_h264_and_size(self):
        s = " ".join(P.gst_argv("h264", 10, 481))
        self.assertIn("h264parse", s)
        self.assertIn("width=480,height=270", s)
        self.assertIn("framerate=10/1", s)

    def test_audio_branch_never_blocks(self):
        s = " ".join(P.gst_argv("h265", 30, 640))
        self.assertIn("queue leaky=downstream ! fakesink sync=false async=false", s)

    def test_taskset_on_little_cores(self):
        with mock.patch.object(P.shutil, "which", return_value="/usr/bin/taskset"):
            a = P.gst_argv("h265", 30, 640, [0, 1, 2, 3])
        self.assertEqual(a[:3], ["taskset", "-c", "0,1,2,3"])

    def test_no_taskset_without_cores(self):
        self.assertEqual(P.gst_argv("h265", 30, 640, [])[0], "gst-launch-1.0")

    def test_little_cpus(self):
        with mock.patch.object(P.os, "cpu_count", return_value=8):
            self.assertEqual(P.little_cpus([4, 5, 6, 7]), [0, 1, 2, 3])
            self.assertEqual(P.little_cpus([]), [])

    def test_time_limit_short_and_long(self):
        self.assertEqual(P.Preview.limit(), P.MAX_SECONDS)
        self.assertEqual(P.Preview.limit(False), 600)
        self.assertEqual(P.Preview.limit(True), P.LONG_SECONDS)
        self.assertGreater(P.LONG_SECONDS, P.MAX_SECONDS)

    def test_limits(self):
        self.assertEqual(P.clamp(99, *P.FPS_RANGE, 30), 30)
        self.assertEqual(P.clamp(0, *P.FPS_RANGE, 30), 1)
        self.assertEqual(P.clamp("x", *P.FPS_RANGE, 30), 30)
        self.assertEqual(P.clamp(5000, *P.WIDTH_RANGE, 640), 1280)
        self.assertEqual(P.clamp("320", *P.WIDTH_RANGE, 640), 320)


def nal(t, payload=b"\x88\x84\x21\xa0", ref=3):
    return bytes([(ref << 5) | t]) + payload


SPS = bytes([0x67, 0x4D, 0x40, 0x1F, 0xE9, 0x02, 0x80, 0x2D, 0xD0, 0x80, 0x00, 0x00, 0x03, 0x00, 0x80])      # Profil main, Stufe 3.1 (Beispiel; Inhalt nur für avcC)
PPS = bytes([0x68, 0xEE, 0x3C, 0x80])


def idr(n=0):
    return nal(5, b"\x88\x84" + bytes([n, 1, 2, 3]))


def pframe(n=0):
    return nal(1, b"\x9a\x00" + bytes([n, 5, 6, 7, 8]), ref=2)


def annexb(nals, four=True):
    return b"".join((b"\x00\x00\x00\x01" if (four or i % 2) else b"\x00\x00\x01") + n for i, n in enumerate(nals))


def boxes(data):
    out, i = [], 0
    while i < len(data):
        size, typ = struct.unpack(">I4s", data[i:i + 8])
        assert size >= 8 and i + size <= len(data), "Kiste reicht über das Ende"
        out.append((typ.decode(), data[i + 8:i + size]))
        i += size
    return out


def find(payload, path):
    for name in path:
        d = dict(boxes(payload))
        payload = d[name]
    return payload


class Mp4(unittest.TestCase):
    """Das fragmentierte MP4 aus dem H.264-Byte-Strom (Init-Segment, Häppchen, Zeiten, Schlüsselbilder)."""

    def stream_bytes(self, frames=14):
        nals = [SPS, PPS, idr(0)] + [pframe(i) for i in range(1, 7)] + [idr(7)] + [pframe(i) for i in range(8, frames)]
        # Bilder beginnen mit SPS/PPS vor jedem Schlüsselbild (config-interval=-1), dazwischen Folgebilder
        return nals

    def test_init_segment_structure(self):
        init = P.init_segment(SPS, PPS, 640, 360)
        top = boxes(init)
        self.assertEqual([n for n, _ in top], ["ftyp", "moov"])
        moov = dict(boxes(top[1][1]))
        self.assertEqual(set(moov), {"mvhd", "trak", "mvex"})
        stsd = find(top[1][1], ["trak", "mdia", "minf", "stbl", "stsd"])
        avc1 = boxes(stsd[8:])[0]
        self.assertEqual(avc1[0], "avc1")
        w, h = struct.unpack(">HH", avc1[1][24:28])
        self.assertEqual((w, h), (640, 360))
        avcc = dict(boxes(avc1[1][78:]))["avcC"]
        self.assertEqual(avcc[:4], bytes([1, 0x4D, 0x40, 0x1F]))                         # Profil, Verträglichkeit, Stufe aus dem SPS
        n = struct.unpack(">H", avcc[6:8])[0]
        self.assertEqual(avcc[8:8 + n], SPS)
        self.assertEqual(avcc[8 + n + 3:], PPS)
        self.assertEqual(find(top[1][1], ["trak", "tkhd"])[-8:], struct.pack(">II", 640 << 16, 360 << 16))
        self.assertEqual(find(top[1][1], ["trak", "mdia", "mdhd"])[12:16], struct.pack(">I", 90000))

    def test_media_segment_offsets_sizes_and_flags(self):
        samples = [(b"A" * 10, 3000, True), (b"B" * 20, 3000, False), (b"C" * 5, 3000, False)]
        seg = P.media_segment(7, 123456, samples)
        top = boxes(seg)
        self.assertEqual([n for n, _ in top], ["moof", "mdat"])
        moof_size = 8 + len(top[0][1])
        traf = dict(boxes(dict(boxes(top[0][1]))["traf"]))
        self.assertEqual(struct.unpack(">I", dict(boxes(top[0][1]))["mfhd"][4:8])[0], 7)
        self.assertEqual(struct.unpack(">Q", traf["tfdt"][4:12])[0], 123456)
        self.assertEqual(struct.unpack(">I", traf["tfhd"][:4])[0] & 0xFFFFFF, 0x020000)       # default-base-is-moof
        trun = traf["trun"]
        count, offset = struct.unpack(">Ii", trun[4:12])
        self.assertEqual(count, 3)
        self.assertEqual(offset, moof_size + 8)                                                # zeigt auf den Anfang der Nutzdaten im mdat
        rows = [struct.unpack(">III", trun[12 + 12 * i:24 + 12 * i]) for i in range(3)]
        self.assertEqual([r[1] for r in rows], [10, 20, 5])
        self.assertEqual([r[2] for r in rows], [0x02000000, 0x01010000, 0x01010000])
        self.assertEqual(top[1][1], b"A" * 10 + b"B" * 20 + b"C" * 5)

    def run_mux(self, chunks, **kw):
        m = P.Fmp4(**kw)
        out = []
        for c in chunks:
            out += m.feed(c)
        return out

    def test_first_output_is_init_then_fragments(self):
        data = annexb(self.stream_bytes(), four=True) + annexb([SPS, PPS, idr(99)])        # das letzte Bild erscheint erst mit dem nächsten
        out = self.run_mux([data], frames=6)
        self.assertEqual(boxes(out[0])[0][0], "ftyp")
        self.assertEqual([boxes(o)[0][0] for o in out[1:]], ["moof"] * (len(out) - 1))
        self.assertGreaterEqual(len(out), 3)
        seqs = [struct.unpack(">I", dict(boxes(dict(boxes(o))["moof"]))["mfhd"][4:8])[0] for o in out[1:]]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        t = [struct.unpack(">Q", dict(boxes(dict(boxes(dict(boxes(o))["moof"]))["traf"]))["tfdt"][4:12])[0] for o in out[1:]]
        self.assertEqual(t, [i * 6 * 3000 for i in range(len(t))])                          # 30 Bilder/s, 6 Bilder je Häppchen
        first = dict(boxes(dict(boxes(dict(boxes(out[1]))["moof"]))["traf"]))["trun"]
        flags = [struct.unpack(">I", first[12 + 12 * i + 8:12 + 12 * i + 12])[0] for i in range(6)]
        self.assertEqual(flags[0], 0x02000000)                                              # beginnt mit einem Schlüsselbild
        self.assertTrue(all(f == 0x01010000 for f in flags[1:]))

    def test_samples_are_length_prefixed_without_parameter_sets(self):
        data = annexb([SPS, PPS, idr(0), pframe(1), pframe(2), SPS, PPS, idr(3)])
        out = self.run_mux([data], frames=2)
        mdat = boxes(out[1])[1][1]
        i0 = idr(0)
        self.assertEqual(mdat[:4 + len(i0)], struct.pack(">I", len(i0)) + i0)               # Schlüsselbild, ohne SPS und PPS
        p1 = pframe(1)
        self.assertEqual(mdat[4 + len(i0):8 + len(i0) + len(p1)], struct.pack(">I", len(p1)) + p1)

    def test_byte_by_byte_gives_the_same_output(self):
        data = annexb(self.stream_bytes(20), four=False) + annexb([SPS, PPS, idr(50)])
        a = self.run_mux([data], frames=4)
        b = self.run_mux([data[i:i + 1] for i in range(len(data))], frames=4)
        c = self.run_mux([data[i:i + 7] for i in range(0, len(data), 7)], frames=4)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_waits_for_the_first_key_frame_with_parameter_sets(self):
        data = annexb([pframe(1), pframe(2), idr(3), pframe(4), SPS, PPS, idr(5), pframe(6), SPS, PPS, idr(7)])
        out = self.run_mux([data], frames=1)
        self.assertEqual(boxes(out[0])[0][0], "ftyp")
        first = boxes(out[1])[1][1]
        self.assertEqual(first[4:4 + len(idr(5))], idr(5))                                   # der Strom beginnt erst am Schlüsselbild mit SPS und PPS

    def test_nothing_before_a_complete_picture(self):
        self.assertEqual(self.run_mux([annexb([SPS, PPS, idr(0)])], frames=1), [])

    def test_four_byte_start_codes_do_not_leave_zero_bytes(self):
        data = annexb([SPS, PPS, idr(0), pframe(1), SPS, PPS, idr(2)], four=True)
        out = self.run_mux([data], frames=1)
        self.assertEqual(boxes(out[1])[1][1][4:4 + len(idr(0))], idr(0))

    def test_command_line_per_format(self):
        mp4 = " ".join(P.gst_argv("h265", 30, 640, fmt="mp4"))
        self.assertIn("mpph264enc", mp4)
        self.assertIn("stream-format=byte-stream", mp4)
        self.assertIn("gop=30", mp4)
        self.assertNotIn("mppjpegenc", mp4)
        self.assertNotIn("mp4mux", mp4)
        jpg = " ".join(P.gst_argv("h265", 30, 640, fmt="mjpeg"))
        self.assertIn("mppjpegenc", jpg)
        self.assertNotIn("mpph264enc", jpg)


class FakePreview(P.Preview):
    """Preview mit nachgebautem Mitlese-Socket und einem Dekoder-Ersatz, der stdin nach stdout kopiert."""

    def __init__(self, a, b, cmd=None):
        super().__init__(pipeline_path="/nonexistent")
        self.a, self.b = a, b
        self.cmd = cmd or [sys.executable, "-c", "import sys,os\nwhile True:\n d=os.read(0,65536)\n if not d: break\n os.write(1,d)"]
        self.procs = []

    def open_capture(self):
        self.b.settimeout(0.5)
        return self.b

    def spawn(self, fps, width, fmt="mjpeg"):
        import subprocess
        p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.procs.append(p)
        return p


class Flow(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.pv = FakePreview(self.a, self.b)
        self.got = bytearray()

    def tearDown(self):
        for s in (self.a, self.b):
            try:
                s.close()
            except OSError:
                pass

    def send(self, seqs):
        for q in seqs:
            self.a.send(udp(9100, srt(q, bytes([65 + q % 26]) * 188)))

    def test_bytes_arrive_in_order(self):
        def write(d):
            self.got += d
            if len(self.got) >= 188 * 3:
                raise OSError("Browser weg")
        self.send([10, 12, 11])
        why = self.pv.stream(write, 30, 640, lambda: True, max_seconds=5)
        self.assertEqual(why, "client")
        self.assertEqual(bytes(self.got), bytes([65 + 10]) * 188 + bytes([65 + 11]) * 188 + bytes([65 + 12]) * 188)

    def test_time_limit(self):
        self.send([1])
        t = time.monotonic()
        why = self.pv.stream(lambda d: None, 30, 640, lambda: True, max_seconds=1)
        self.assertEqual(why, "zeit")
        self.assertLess(time.monotonic() - t, 4)

    def test_sender_stops(self):
        self.send([1])
        with mock.patch.object(P, "CHECK_EVERY", 0.2):
            why = self.pv.stream(lambda d: None, 30, 640, lambda: False, max_seconds=30)
        self.assertEqual(why, "sender")

    def test_no_picture(self):
        with mock.patch.object(P, "FIRST_FRAME_WAIT", 0.5):
            why = self.pv.stream(lambda d: None, 30, 640, lambda: True, max_seconds=30)
        self.assertEqual(why, "kein-bild")

    def test_decoder_dies(self):
        pv = FakePreview(self.a, self.b, cmd=[sys.executable, "-c", "pass"])
        self.send([1])
        why = pv.stream(lambda d: None, 30, 640, lambda: True, max_seconds=10)
        self.assertEqual(why, "fehler")

    def test_cleanup_after_end(self):
        self.send([1])
        self.pv.stream(lambda d: (_ for _ in ()).throw(OSError()), 30, 640, lambda: True, max_seconds=5)
        self.assertEqual(self.pv.viewers, 0)
        for p in self.pv.procs:
            self.assertIsNotNone(p.poll())
        with self.assertRaises(OSError):
            self.b.recv(1)                                 # Socket ist zu

    def test_cleanup_after_error(self):
        pv = FakePreview(self.a, self.b)
        with mock.patch.object(pv, "spawn", side_effect=P.Unavailable("gst")):
            with self.assertRaises(P.Unavailable):
                pv.stream(lambda d: None, 30, 640, lambda: True)
        self.assertEqual(pv.viewers, 0)

    def test_viewer_limit(self):
        pv = FakePreview(self.a, self.b)
        for _ in range(P.MAX_VIEWERS):
            pv._acquire()
        with self.assertRaises(P.Busy):
            pv.stream(lambda d: None, 30, 640, lambda: True)
        self.assertEqual(pv.viewers, P.MAX_VIEWERS)
        pv._release()
        self.assertEqual(pv.viewers, P.MAX_VIEWERS - 1)

    def test_status(self):
        with mock.patch.object(P.shutil, "which", return_value="/usr/bin/gst-launch-1.0"), mock.patch.object(P.os, "geteuid", return_value=0):
            self.assertTrue(self.pv.status(True)["available"])
            self.assertEqual(self.pv.status(False)["why"], "off")
            for _ in range(P.MAX_VIEWERS):
                self.pv._acquire()
            self.assertEqual(self.pv.status(True)["why"], "busy")
        with mock.patch.object(P.shutil, "which", return_value=None):
            self.assertEqual(self.pv.status(True)["why"], "gst")
        with mock.patch.object(P.shutil, "which", return_value="x"), mock.patch.object(P.os, "geteuid", return_value=1000):
            self.assertEqual(self.pv.status(True)["why"], "root")

    def test_status_reports_limit_and_codec(self):
        with mock.patch.object(P.shutil, "which", return_value="x"), mock.patch.object(P.os, "geteuid", return_value=0):
            st = self.pv.status(True)
        self.assertEqual(st["max_seconds"], P.MAX_SECONDS)
        self.assertEqual(st["codec"], "h264")


class Helper(unittest.TestCase):
    """Der Dienst hinter dem Unix-Socket und der Client des Webservers, mit einer echten Socket-Datei und dem nachgebauten Dekoder."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "p.sock")
        self.a, self.b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.pv = FakePreview(self.a, self.b)
        self.limits = []
        orig = P.Preview.limit

        def limit(long=False):
            self.limits.append(long)
            return orig(long)
        self.pv.limit = limit
        self.sending = [True]
        self.svc = P.Service(self.pv, sending=lambda: self.sending[0], idle=60)
        self.ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ls.bind(self.path)
        self.ls.listen(4)
        self.t = threading.Thread(target=self.svc.serve, args=(self.ls,), daemon=True)
        self.t.start()
        self.patches = [mock.patch.object(P.shutil, "which", return_value="/usr/bin/x"), mock.patch.object(P.os, "geteuid", return_value=0)]
        for m in self.patches:
            m.start()
        self.client = P.Client(self.path, timeout=5)

    def tearDown(self):
        for m in self.patches:
            m.stop()
        for x in (self.a, self.b, self.ls):
            try:
                x.close()
            except OSError:
                pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_viewers(self, n, secs=5):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if self.pv.viewers == n:
                return True
            time.sleep(0.05)
        return False

    def test_status(self):
        st = self.client.status()
        self.assertTrue(st["available"])
        self.assertEqual(st["max_seconds"], P.MAX_SECONDS)

    def test_status_when_not_sending(self):
        self.sending[0] = False
        st = self.client.status()
        self.assertFalse(st["available"])
        self.assertEqual(st["why"], "off")

    def test_stream_delivers_the_decoder_output(self):
        s, head = self.client.open(30, 640, fmt="mjpeg")
        self.assertEqual(head, {"ok": True})
        self.a.send(udp(9100, srt(5, b"Q" * 188)))
        s.settimeout(5)
        self.assertEqual(s.recv(188), b"Q" * 188)
        s.close()
        self.assertTrue(self.wait_viewers(0), "Platz wird nach dem Schließen frei")

    def test_long_flag_reaches_the_limit(self):
        s, _ = self.client.open(30, 640, long=True, fmt="mjpeg")
        s.close()
        self.assertTrue(self.wait_viewers(0))
        self.a, self.b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)        # der nachgebaute Mitlese-Socket wird je Vorschau geschlossen
        self.pv.a, self.pv.b = self.a, self.b
        s, _ = self.client.open(30, 640, fmt="mjpeg")
        s.close()
        self.assertTrue(self.wait_viewers(0))
        self.assertEqual(self.limits, [True, False])

    def test_not_sending_is_refused(self):
        self.sending[0] = False
        s, head = self.client.open(30, 640, fmt="mjpeg")
        self.assertIsNone(s)
        self.assertEqual(head["error"], "off")

    def test_busy(self):
        for _ in range(P.MAX_VIEWERS):
            self.pv._acquire()
        s, head = self.client.open(30, 640, fmt="mjpeg")
        self.assertIsNone(s)
        self.assertEqual(head["error"], "busy")

    def test_garbage_request(self):
        for raw in (b"kein json\n", b"[1,2]\n", b'{"cmd": "etwas"}\n', b'{"cmd": 5}\n'):
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(5)
            c.connect(self.path)
            c.sendall(raw)
            self.assertEqual(json.loads(P.read_line(c)), {"error": "anfrage"}, raw)
            c.close()

    def test_oversized_request_is_dropped(self):
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(5)
        c.connect(self.path)
        c.sendall(b"x" * 2000)
        self.assertEqual(json.loads(P.read_line(c)), {"error": "anfrage"})        # kurz abgewiesen, nichts gepuffert
        c.close()
        self.assertTrue(self.client.status()["available"], "der Dienst läuft weiter")

    def test_service_survives_a_client_that_hangs_up(self):
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(self.path)
        c.close()
        self.assertTrue(self.client.status()["available"])

    def test_client_without_service(self):
        c = P.Client(os.path.join(self.tmp, "gibt-es-nicht.sock"))
        self.assertEqual(c.status(), {"available": False, "why": "missing"})
        with self.assertRaises(OSError):
            c.open(30, 640)

    def test_client_limits_are_clamped(self):
        got = []
        orig = self.pv.stream

        def spy(write, fps, width, *a, **k):
            got.append((fps, width))
            return orig(write, fps, width, *a, **k)
        self.pv.stream = spy
        s, _ = self.client.open(999, 5000, fmt="mjpeg")
        s.close()
        self.assertTrue(self.wait_viewers(0))
        self.assertEqual(got, [(30, 1280)])

    def test_service_ends_after_idle_time(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        pv = FakePreview(a, b)
        path = os.path.join(self.tmp, "idle.sock")
        ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        ls.bind(path)
        ls.listen(1)
        svc = P.Service(pv, sending=lambda: True, idle=1)
        done = []
        t = threading.Thread(target=lambda: (svc.serve(ls), done.append(1)), daemon=True)
        t0 = time.monotonic()
        t.start()
        t.join(6)
        self.assertEqual(done, [1])
        self.assertLess(time.monotonic() - t0, 5)
        ls.close()
        a.close()
        b.close()


class Environment(unittest.TestCase):
    def test_sender_status(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "status.json")
        self.assertFalse(P.sending_now(p), "Datei fehlt")
        def put(**kw):
            with open(p, "w") as f:
                json.dump(kw, f)
        put(state="running", time=1000)
        self.assertTrue(P.sending_now(p, now=lambda: 1005))
        self.assertFalse(P.sending_now(p, now=lambda: 1016), "älter als 15 s")
        put(state="stopping", time=1000)
        self.assertFalse(P.sending_now(p, now=lambda: 1001))
        put(state="refused", time=1000)
        self.assertFalse(P.sending_now(p, now=lambda: 1001))
        with open(p, "w") as f:
            f.write("kaputt")
        self.assertFalse(P.sending_now(p))

    def test_big_cpus(self):
        import tempfile
        d = tempfile.mkdtemp()
        for n, c in enumerate([512, 512, 512, 512, 1024, 1024, 1024, 1024]):
            os.makedirs("%s/cpu%d" % (d, n))
            with open("%s/cpu%d/cpu_capacity" % (d, n), "w") as f:
                f.write(str(c))
        self.assertEqual(P.big_cpus(d), [4, 5, 6, 7])
        self.assertEqual(P.big_cpus(d + "/gibt-es-nicht"), [])

    def test_read_line_limit(self):
        a, b = socket.socketpair()
        a.sendall(b"abc\nrest")
        self.assertEqual(P.read_line(b), b"abc")
        self.assertEqual(b.recv(4), b"rest")            # Nutzdaten nach der Zeile bleiben unberührt
        a.sendall(b"x" * 600)
        with self.assertRaises(ValueError):
            P.read_line(b)
        a.close()
        with self.assertRaises(ValueError):
            P.read_line(b)
        b.close()

    def test_unit_files_give_only_the_raw_packet_right(self):
        root = os.path.dirname(HERE)
        svc = open(os.path.join(root, "install", "pipbox-preview.service"), encoding="utf-8").read()
        sock = open(os.path.join(root, "install", "pipbox-preview.socket"), encoding="utf-8").read()
        self.assertIn("CapabilityBoundingSet=CAP_NET_RAW\n", svc)
        self.assertIn("NoNewPrivileges=yes", svc)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_PACKET", svc)
        self.assertIn("ProtectSystem=strict", svc)
        self.assertIn("--serve", svc)
        self.assertIn("SocketMode=0660", sock)
        self.assertIn("SocketGroup=pipbox", sock)
        self.assertIn("ListenStream=/run/pipbox-preview.sock", sock)
        self.assertEqual(P.SOCKET, "/run/pipbox-preview.sock")

    def test_installer_sets_up_the_service(self):
        root = os.path.dirname(HERE)
        sh = open(os.path.join(root, "install", "install.sh"), encoding="utf-8").read()
        for needle in ("pipbox_preview.py", "pipbox-preview.socket", "pipbox-preview.service", "enable --now pipbox-preview.socket"):
            self.assertIn(needle, sh)
        self.assertEqual(sh.count("pipbox-preview.socket /etc/systemd/system/"), 1)

    def test_web_server_only_passes_the_stream_through(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        self.assertIn("pipbox_preview.Client()", src)
        self.assertIn('"/api/preview/stream"', src)
        self.assertNotIn("AF_PACKET", src)               # der Webserver braucht das Recht nicht und bekommt es nicht
        self.assertIn('(q.get("long") or [""])[0] == "1"', src)



class TrafficAccounting(unittest.TestCase):
    """Der Verkehr der Vorschau zum Browser wird aus der Anzeige des Uploads herausgerechnet."""

    def traffic(self, addrs, default="eth0"):
        return P.Traffic(addrs=lambda names: {n: a for n, a in addrs.items() if n in names}, default=lambda: default)

    def test_subtracts_on_the_interface_of_the_connection(self):
        t = self.traffic({"eth0": "192.168.1.5", "wlan0": "10.0.0.2"})
        t.add("10.0.0.2", 1_000_000)
        out = t.adjust({"eth0": (5_000_000, 90_000_000), "wlan0": (2_000_000, 40_000_000)})
        self.assertEqual(out["eth0"], (5_000_000, 90_000_000))
        self.assertEqual(out["wlan0"], (2_000_000 - 10_000, 40_000_000 - 1_046_000))

    def test_adds_up_connections_on_the_same_address(self):
        t = self.traffic({"eth0": "192.168.1.5"})
        t.add("192.168.1.5", 400_000)
        t.add("192.168.1.5", 600_000)
        self.assertEqual(t.adjust({"eth0": (0, 10_000_000)})["eth0"][1], 10_000_000 - 1_046_000)

    def test_proxy_on_the_box_uses_the_default_route(self):
        t = self.traffic({"eth0": "192.168.1.5", "wlan0": "10.0.0.2"}, default="wlan0")
        t.add("127.0.0.1", 1_000_000)
        out = t.adjust({"eth0": (0, 50_000_000), "wlan0": (0, 40_000_000)})
        self.assertEqual(out["eth0"][1], 50_000_000)
        self.assertEqual(out["wlan0"][1], 40_000_000 - 1_046_000)

    def test_unknown_interface_is_left_alone(self):
        t = self.traffic({"eth0": "192.168.1.5"}, default=None)
        t.add("127.0.0.1", 1_000_000)
        self.assertEqual(t.adjust({"eth0": (1, 2)}), {"eth0": (1, 2)})
        t2 = self.traffic({"eth0": "192.168.1.5"}, default="usb0")
        t2.add("127.0.0.1", 1_000_000)
        self.assertEqual(t2.adjust({"eth0": (1, 2)}), {"eth0": (1, 2)})

    def test_nothing_without_a_preview(self):
        t = self.traffic({"eth0": "192.168.1.5"})
        net = {"eth0": (7, 8)}
        self.assertIs(t.adjust(net), net)

    def test_never_negative(self):
        t = self.traffic({"eth0": "192.168.1.5"})
        t.add("192.168.1.5", 10_000_000)
        self.assertEqual(t.adjust({"eth0": (100, 200)})["eth0"], (0, 0))

    def test_default_route_parser(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "route")
        with open(p, "w") as f:
            f.write("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
                    "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
                    "eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
                    "eth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n")
        self.assertEqual(P.default_route_iface(p), "eth0")
        self.assertIsNone(P.default_route_iface(p + ".fehlt"))

    def test_sampler_uses_the_adjustment(self):
        import server
        tr = self.traffic({"eth0": "192.168.1.5"})
        tr.add("192.168.1.5", 2_000_000)
        proc = ("Inter-|   Receive\n face |bytes\n"
                "  eth0: 1000 0 0 0 0 0 0 0 30000000 0 0 0 0 0 0 0\n")
        with mock.patch.object(P, "TRAFFIC", tr), \
                mock.patch.object(server, "read", lambda p, d=None: proc if p == "/proc/net/dev" else "up"):
            out = server.Sampler(False).net_bytes()
        self.assertEqual(out["eth0"], (0, 30_000_000 - 2_092_000))

    def test_server_counts_the_bytes_it_passes_on(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        self.assertIn("pipbox_preview.TRAFFIC.add(lip, len(data))", src)
        self.assertIn("pipbox_preview.TRAFFIC.adjust(res)", src)


class FullFrameRate(unittest.TestCase):
    """Immer volle 30 Bilder pro Sekunde in 640 x 360, auch außerhalb des Heimnetzes (weniger Bilder wirken am Handy ruckelig und sparen kaum Rechenzeit)."""

    def setUp(self):
        self.html = open(os.path.join(os.path.dirname(HERE), "web", "index.html"), encoding="utf-8").read()

    def test_one_mode_only(self):
        self.assertIn('"&fps=30&w=640"+(always()?"&long=1":"")', self.html)                    # immer 30 Bilder pro Sekunde in 640 x 360
        self.assertIn('"/api/preview/stream?fmt=mp4"+query()', self.html)                       # Video (H.264, fragmentiertes MP4)
        self.assertIn('"/api/preview/stream?fmt=mjpeg"+query()', self.html)                     # Ausweichlösung: Einzelbilder
        self.assertNotIn("home?30:5", self.html)
        self.assertNotIn("st.home", self.html)

    def test_server_has_no_network_guessing_left(self):
        src = open(os.path.join(os.path.dirname(HERE), "server.py"), encoding="utf-8").read()
        self.assertNotIn("is_home_address", src)
        self.assertNotIn("home=home", src)
        self.assertFalse(hasattr(P, "is_home_address"))

    def test_card_stays_small(self):
        self.assertIn('id="prev_set"', self.html)                                   # Zahnrad holt das Häkchen-Feld zurück
        self.assertIn("keepRow.hidden=keep.checked&&!gearOpen", self.html)
        self.assertIn("#prev_img,#prev_vid{display:block;width:100%;max-width:640px;", self.html)
        self.assertNotIn("Außerhalb des Heimnetzes: 10 Bilder", self.html)

    def test_compact_mode_hides_the_buttons_over_a_running_picture(self):
        self.assertIn("html.nohead #c_prev.prevrun>summary{visibility:hidden;", self.html)
        self.assertIn("html.nohead #c_prev.prevrun.showctl>summary{visibility:visible;", self.html)
        self.assertIn('card.classList.add("prevrun")', self.html)
        self.assertIn('card.classList.remove("prevrun","showctl")', self.html)
        self.assertIn('card.addEventListener("pointermove"', self.html)
        self.assertIn('box.addEventListener("click"', self.html)

    def test_headings_option(self):
        self.assertIn('row(g,"heads","Überschriften von Chat und Vorschau",false)', self.html)
        self.assertIn('document.documentElement.classList.toggle("nohead",compact)', self.html)


if __name__ == "__main__":
    unittest.main()
