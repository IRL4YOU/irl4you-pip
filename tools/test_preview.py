"""Tests für die Vorschau des Sendebilds (pipbox_preview.py, Issue #52): der Kernel-Filter (mit einem kleinen BPF-Interpreter geprüft, damit die Sprünge stimmen),
das Lesen der SRT-Datenpakete, die Ordnung der Pakete (Wiederholungen, Lücken, Zählerumlauf), die Kommandozeile des Dekoders, die Grenzen (Bildrate, Breite, Zuschauer)
und der ganze Ablauf mit einem nachgebauten Mitlese-Socket und einem Ersatz für den Dekoder (Ende durch Browser, Zeit, Sender, fehlendes Bild)."""
import os
import socket
import struct
import sys
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

    def test_limits(self):
        self.assertEqual(P.clamp(99, *P.FPS_RANGE, 30), 30)
        self.assertEqual(P.clamp(0, *P.FPS_RANGE, 30), 1)
        self.assertEqual(P.clamp("x", *P.FPS_RANGE, 30), 30)
        self.assertEqual(P.clamp(5000, *P.WIDTH_RANGE, 640), 1280)
        self.assertEqual(P.clamp("320", *P.WIDTH_RANGE, 640), 320)


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

    def spawn(self, fps, width):
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


if __name__ == "__main__":
    unittest.main()
