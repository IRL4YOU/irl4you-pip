"""Tests für den Wächter der Sendekette (pipbox_watch.py und seine Einbindung in pipbox_send.py, Issue #51): Erkennung der drei Hänger (Statistik steht still,
Threads im Kernel-Zustand D, sendende Kamera ohne Bilder im Mischer), Wartezeiten, Ruhezeit und Höchstzahl je Stunde, das Diagnosepaket (Threads mit Zustand
und Wartestelle, Threads im Zustand D, ohne Schlüssel und Adressen) und das Anhängen der Pakete. Die Hänger selbst ließen sich auf der Box nicht nachstellen."""
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
if not hasattr(os, "killpg"):                   # Entwicklungsrechner ohne Linux-Teile (wie in test_live.py)
    os.killpg = lambda *a, **k: None
    for _m in ("pwd", "grp", "fcntl", "termios"):
        sys.modules.setdefault(_m, mock.MagicMock())
import pipbox_watch as W  # noqa: E402

LIVE = ("t=1 br=400000 comp=30.8/0/44 comp_new=0.0 in_f0=%s/0/0 in_f1=%s/0/37 vr_f0=0.0/0/0 dec_f0=%s/0/0 dmx_f0=%s/0/0 dmx_f1=29.8/0/37 "
        "feed_sbf0=3 feed_sbf1=3 feed_sbf2=1 feed_sbf3=1")


def line(f0="29.8", f1="29.8", dec0="29.8", dmx0="29.8"):
    return LIVE % (f0, f1, dec0, dmx0)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.w = 1_700_000_000.0

    def mono(self):
        return self.t

    def wall(self):
        return self.w

    def advance(self, s):
        self.t += s
        self.w += s


class Parse(unittest.TestCase):
    def test_rates_and_feed_states(self):
        v = W.parse_live(line(f0="0.0", dec0="0.0", dmx0="0.0"))
        self.assertEqual((v["in"][0], v["in"][1], v["dmx"][0], v["dec"][0]), (0.0, 29.8, 0.0, 0.0))
        self.assertEqual(v["feed"], {0: 3, 1: 3, 2: 1, 3: 1})

    def test_empty_or_odd_text_is_harmless(self):
        for t in ("", None, "t=1 nothing here"):
            v = W.parse_live(t)
            self.assertEqual((v["in"], v["feed"]), ({}, {}))


class Detection(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.w = W.HangWatch(clock=self.c.mono, wall=self.c.wall)
        self.w.started_now()

    def check(self, text=None, mtime="now", expected=None, d=0):
        mt = self.c.w if mtime == "now" else mtime
        return self.w.check(mt, text if text is not None else line(), expected if expected is not None else {0: True, 1: True}, d)

    def test_quiet_while_everything_runs(self):
        for _ in range(120):
            self.c.advance(1)
            self.assertIsNone(self.check())

    def test_nothing_is_judged_right_after_the_start(self):
        self.c.advance(W.START_GRACE_S - 1)
        self.assertIsNone(self.check(mtime=None))
        self.assertIsNone(self.check(d=3))
        self.assertIsNone(self.check(text=line(f0="0.0", dmx0="0.0")))

    def test_frozen_statistics_file(self):
        self.c.advance(W.START_GRACE_S + 1)
        self.assertIsNone(self.check(mtime=self.c.w - 5))                                   # 5 s alt: noch in Ordnung
        hit = self.check(mtime=self.c.w - (W.STALE_S + 1))
        self.assertEqual(hit["kind"], "stats")
        self.assertIn("steht still", hit["text"])

    def test_missing_statistics_file_counts_after_the_grace_time(self):
        self.c.advance(W.START_GRACE_S + 1)
        self.assertEqual(self.check(mtime=None)["kind"], "stats")

    def test_threads_stuck_in_state_d_for_a_while(self):
        self.c.advance(W.START_GRACE_S + 1)
        self.assertIsNone(self.check(d=2))                                                  # gerade erst
        self.c.advance(W.DSTATE_S - 1)
        self.assertIsNone(self.check(d=2))
        self.c.advance(2)
        hit = self.check(d=2)
        self.assertEqual(hit["kind"], "dstate")
        self.assertIn("2 Thread", hit["text"])

    def test_a_short_state_d_does_not_count_and_the_timer_restarts(self):
        self.c.advance(W.START_GRACE_S + 1)
        self.check(d=1)
        self.c.advance(W.DSTATE_S - 2)
        self.assertIsNone(self.check(d=0))                                                  # wieder frei
        self.c.advance(5)
        self.assertIsNone(self.check(d=1))                                                  # neu begonnen
        self.c.advance(W.DSTATE_S - 1)
        self.assertIsNone(self.check(d=1))

    def test_camera_sends_but_no_pictures_reach_the_mixer(self):
        self.c.advance(W.START_GRACE_S + 1)
        dead = line(f0="0.0", dec0="0.0", dmx0="0.0")
        for _ in range(int(W.FEED_DEAD_S) - 1):
            self.c.advance(1)
            self.assertIsNone(self.check(text=dead))
        self.c.advance(2)
        hit = self.check(text=dead)
        self.assertEqual((hit["kind"], hit["slot"]), ("feed", 0))
        self.assertIn("Platz 0", hit["text"])

    def test_pictures_coming_back_reset_the_timer(self):
        self.c.advance(W.START_GRACE_S + 1)
        dead = line(f0="0.0", dec0="0.0", dmx0="0.0")
        for _ in range(int(W.FEED_DEAD_S) - 3):
            self.c.advance(1)
            self.check(text=dead)
        self.c.advance(1)
        self.assertIsNone(self.check())                                                     # Bilder da
        for _ in range(int(W.FEED_DEAD_S) - 3):
            self.c.advance(1)
            self.assertIsNone(self.check(text=dead))                                        # neu gezählt, noch nicht genug

    def test_a_camera_that_does_not_send_or_whose_branch_is_stopped_is_not_judged(self):
        self.c.advance(W.START_GRACE_S + 1)
        dead = line(f0="0.0", dec0="0.0", dmx0="0.0")
        for _ in range(int(W.FEED_DEAD_S) + 10):
            self.c.advance(1)
            self.assertIsNone(self.check(text=dead, expected={1: True}))                    # Platz 0 sendet nicht (nicht erwartet)

    def test_demux_still_delivering_means_no_feed_hang(self):
        self.c.advance(W.START_GRACE_S + 1)
        odd = line(f0="0.0", dec0="0.0", dmx0="30.0")                                       # Zweig liefert, nur der Zähler im Mischer ist 0: nicht eingreifen
        for _ in range(int(W.FEED_DEAD_S) + 10):
            self.c.advance(1)
            self.assertIsNone(self.check(text=odd))

    def test_restart_of_belacoder_gives_a_new_grace_time(self):
        self.c.advance(W.START_GRACE_S + 5)
        self.assertIsNotNone(self.check(mtime=None))
        self.w.started_now()
        self.c.advance(5)
        self.assertIsNone(self.check(mtime=None))


class Limits(unittest.TestCase):
    def test_pause_after_an_action_and_at_most_four_per_hour(self):
        c = Clock()
        w = W.HangWatch(clock=c.mono, wall=c.wall)
        self.assertTrue(w.allow())
        w.acted_now()
        self.assertFalse(w.allow())
        c.advance(W.COOLDOWN_S + 1)
        self.assertTrue(w.allow())
        for _ in range(W.MAX_PER_HOUR - 1):
            w.acted_now()
            c.advance(W.COOLDOWN_S + 1)
        self.assertFalse(w.allow())                                                         # Höchstzahl erreicht
        c.advance(3600)
        self.assertTrue(w.allow())                                                          # nach einer Stunde wieder frei


class FakeProc:
    """Ein nachgebautes /proc mit zwei Prozessen: belacoder (PID 100, drei Threads, einer in D) und ein fremder Prozess (PID 200, ein Thread in D)."""

    def __init__(self, root):
        self.root = root
        self.thread(100, 100, "belacoder", "S", 50, "futex_wait_queue_me")
        self.thread(100, 101, "pipcomp:src", "R", 900, "0")
        self.thread(100, 102, "sbf0_dec:src", "D", 20, "mpp_dev_ioctl", stack="[<0>] rkvdec_wait+0x40/0x100\n[<0>] mpp_dev_ioctl+0x90/0x200\n")
        self.thread(200, 200, "kworker/u16:2", "D", 5, "wait_on_page")
        os.makedirs(f"{root}/100", exist_ok=True)
        with open(f"{root}/100/status", "w") as f:
            f.write("Name:\tbelacoder\nState:\tS (sleeping)\nThreads:\t3\nVmRSS:\t60588 kB\nUid:\t0\t0\t0\t0\nGroups:\t0\n")
        with open(f"{root}/stat", "w") as f:
            f.write("cpu  1 2 3 4\nprocs_running 1\nprocs_blocked 2\n")

    def thread(self, pid, tid, name, state, cpu, wchan, stack=""):
        d = f"{self.root}/{pid}/task/{tid}"
        os.makedirs(d, exist_ok=True)
        # Felder nach dem Namen: Zustand, ppid, pgrp, session, tty, tpgid, flags, minflt, cminflt, majflt, cmajflt, utime, stime ...
        with open(f"{d}/stat", "w") as f:
            f.write("%d (%s) %s 1 1 1 0 -1 0 0 0 0 0 %d %d 0 0 20 0\n" % (tid, name, state, cpu, 0))
        with open(f"{d}/comm", "w") as f:
            f.write(name + "\n")
        with open(f"{d}/wchan", "w") as f:
            f.write(wchan)
        if stack:
            with open(f"{d}/stack", "w") as f:
                f.write(stack)


def read_report(d):
    with open(os.path.join(d, W.REPORT_FILE)) as f:
        return f.read()


class Report(unittest.TestCase):
    read = staticmethod(read_report)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc = FakeProc(self.tmp)

    def test_thread_table_shows_state_wait_place_and_name(self):
        t = W.thread_table(100, proc=self.tmp, sleep=lambda s: None)
        for needle in ("belacoder", "pipcomp:src", "sbf0_dec:src", "mpp_dev_ioctl", "futex_wait_queue_me"):
            self.assertIn(needle, t)
        self.assertEqual(W.d_state_threads(100, proc=self.tmp), 1)
        self.assertEqual(W.d_state_threads(999, proc=self.tmp), 0)                          # unbekannte PID

    def test_blocked_tasks_list_the_whole_system_with_kernel_stack(self):
        t = W.blocked_tasks(proc=self.tmp)
        self.assertIn("procs_blocked laut /proc/stat: 2", t)
        self.assertIn("sbf0_dec:src (102): mpp_dev_ioctl", t)
        self.assertIn("rkvdec_wait+0x40/0x100", t)
        self.assertIn("kworker/u16:2", t)

    def test_report_has_the_diagnosis_and_no_secrets(self):
        hit = {"kind": "feed", "slot": 0, "text": "Platz 0: Kamera sendet, aber seit 26 s kommen keine Bilder im Mischer an"}
        r = W.build_report(hit, 100, 321, line(f0="0.0", dec0="0.0", dmx0="0.0"), ["10:36:21 Feed sbf0 started"],
                           [(0, "Bild 1920x1080, Ton ja", 3), (1, "sendet nicht", 1)], proc=self.tmp, sleep=lambda s: None, wall=lambda: 1_700_000_000)
        for needle in (W.REPORT_MARK, "2023-11-14", "Art: feed", "seit 321 s", "VmRSS", "Threads (Messung", "Threads im Zustand D", "mpp_dev_ioctl",
                       "Platz 0: Bild 1920x1080, Ton ja | Zweig-Zustand 3", "Platz 1: sendet nicht", "in_f0=0.0/0/0", "Feed sbf0 started"):
            self.assertIn(needle, r)
        self.assertNotIn("Uid:", r)                                                         # nur ausgewählte Zeilen der Statusdatei
        self.assertNotIn("dji-", r)
        self.assertNotRegex(r, r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

    def test_reports_are_appended_and_only_the_last_ones_stay(self):
        d = tempfile.mkdtemp()
        for i in range(W.REPORT_KEEP + 3):
            self.assertTrue(W.write_report(d, "%sPaket %d\nZeile\n" % (W.REPORT_MARK, i)))
        text = self.read(d)
        self.assertEqual(text.count(W.REPORT_MARK), W.REPORT_KEEP)
        self.assertNotIn("Paket 0\n", text)
        self.assertIn("Paket %d\n" % (W.REPORT_KEEP + 2), text)
        self.assertEqual(os.stat(os.path.join(d, W.REPORT_FILE)).st_mode & 0o777, 0o644)

    def test_the_file_is_limited_in_size(self):
        d = tempfile.mkdtemp()
        W.write_report(d, W.REPORT_MARK + "x" * 200000 + "\n")
        self.assertLessEqual(len(self.read(d)), 60000)

    def test_an_unwritable_folder_does_not_raise(self):
        self.assertFalse(W.write_report("/proc/nicht/da", "x"))


class SenderIntegration(unittest.TestCase):
    """Sender.hang_check mit nachgebauten Teilen: Statistikdatei, Regler, Prozess."""
    read = staticmethod(read_report)

    def setUp(self):
        import pipbox_send as ps
        import pipbox_live
        self.ps, self.live = ps, pipbox_live
        self.tmp = tempfile.mkdtemp()
        self.stats = os.path.join(self.tmp, "belacoder-live.txt")
        self.clock = Clock()
        self.log = []
        s = ps.Sender.__new__(ps.Sender)
        s.live = True
        s.always = SimpleNamespace(binder=SimpleNamespace(slot_key=["cam-a", "cam-b", "", ""]),
                                   pub={"cam-a": {"w": 1920, "h": 1080, "audio": True}, "cam-b": {"w": 1280, "h": 720, "audio": False}},
                                   states={0: 3, 1: 3}, inactive=[], desired=["cam-a", "cam-b", "", ""])
        s.watch = W.HangWatch(clock=self.clock.mono, wall=self.clock.wall)
        s.watch.started_now()
        s.hang_n = 0
        s._hang_t = 0.0
        s.events = [("10:00:00", "Feed sbf0 started")]
        s.lock = threading.Lock()
        s.state = "running"
        self.killed = []
        s.procs = {"belacoder": SimpleNamespace(pid=4242, poll=lambda: None, kill=lambda: self.killed.append("kill"))}
        s.args = lambda name: ["belacoder"]
        s.stop_proc = lambda name: self.log.append(("stop", name))
        s.spawn = lambda name, args, env=None: self.log.append(("spawn", name))
        self.sender = s
        self.patches = [mock.patch.object(pipbox_live, "LIVE_STATS", self.stats), mock.patch.object(ps, "STATE", self.tmp),
                        mock.patch.object(ps.time, "monotonic", self.clock.mono),
                        mock.patch.object(W, "d_state_threads", lambda pid: self.d), mock.patch.object(W, "thread_table", lambda *a, **k: "(Threads)"),
                        mock.patch.object(W, "blocked_tasks", lambda *a, **k: "(D)")]
        self.d = 0
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def write_stats(self, text):
        with open(self.stats, "w") as f:
            f.write(text + "\nwarn x\n")
        os.utime(self.stats, (self.clock.w, self.clock.w))

    def tick(self, seconds=1):
        self.clock.advance(seconds)
        self.sender.hang_check({})

    def test_healthy_chain_is_left_alone(self):
        for _ in range(60):
            self.write_stats(line())
            self.tick()
        self.assertEqual((self.log, self.sender.hang_n), ([], 0))

    def test_dead_feed_gets_a_report_and_a_restart_of_belacoder_only(self):
        for _ in range(int(W.START_GRACE_S) + 2):
            self.write_stats(line())
            self.tick()
        for _ in range(int(W.FEED_DEAD_S) + 3):
            self.write_stats(line(f0="0.0", dec0="0.0", dmx0="0.0"))
            self.tick()
        self.assertEqual(self.sender.hang_n, 1)
        self.assertEqual(self.log, [("stop", "belacoder"), ("spawn", "belacoder")])
        self.assertEqual(self.killed, [])                                                   # läuft noch: kein harter Abbruch, stop_proc genügt
        report = self.read(self.tmp)
        self.assertIn("Art: feed", report)
        self.assertIn("Platz 0: Bild 1920x1080, Ton ja | Zweig-Zustand 3", report)
        self.assertNotIn("cam-a", report)
        self.assertNotIn("cam-b", report)

    def test_frozen_statistics_is_killed_hard_first(self):
        for _ in range(int(W.START_GRACE_S) + 2):
            self.write_stats(line())
            self.tick()
        for _ in range(int(W.STALE_S) + 3):
            self.tick()                                                                      # die Datei wird nicht mehr geschrieben
        self.assertEqual(self.sender.hang_n, 1)
        self.assertEqual(self.killed, ["kill"])
        self.assertEqual(self.log, [("stop", "belacoder"), ("spawn", "belacoder")])
        self.assertIn("Art: stats", self.read(self.tmp))

    def test_threads_in_state_d_trigger_too(self):
        for _ in range(int(W.START_GRACE_S) + 2):
            self.write_stats(line())
            self.tick()
        self.d = 2
        for _ in range(int(W.DSTATE_S) + 2):
            self.write_stats(line())
            self.tick()
        self.assertEqual(self.sender.hang_n, 1)
        self.assertIn("Art: dstate", self.read(self.tmp))

    def test_after_an_action_there_is_a_pause_even_if_the_fault_stays(self):
        for _ in range(int(W.START_GRACE_S) + 2):
            self.write_stats(line())
            self.tick()
        self.d = 1
        for _ in range(int(W.DSTATE_S) + 2):
            self.write_stats(line())
            self.tick()
        self.assertEqual(self.sender.hang_n, 1)
        self.sender.watch.started_now()                                                      # der neue belacoder hängt gleich wieder
        for _ in range(int(W.START_GRACE_S + W.DSTATE_S) + 2):
            self.write_stats(line())
            self.tick()
        self.assertEqual(self.sender.hang_n, 1)                                              # innerhalb der Ruhezeit kein zweiter Eingriff

    def test_other_modes_without_the_live_engine_are_not_watched(self):
        self.sender.live = False
        for _ in range(int(W.START_GRACE_S) + int(W.STALE_S) + 5):
            self.tick()
        self.assertEqual((self.log, self.sender.hang_n), ([], 0))

    def test_belacoder_not_running_is_not_judged(self):
        self.sender.procs["belacoder"] = SimpleNamespace(pid=1, poll=lambda: 1, kill=lambda: None)
        for _ in range(int(W.START_GRACE_S) + int(W.STALE_S) + 5):
            self.tick()
        self.assertEqual(self.sender.hang_n, 0)


if __name__ == "__main__":
    unittest.main()
