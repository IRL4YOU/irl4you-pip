"""Tests für die Freigabe im privaten Tailscale-Netz (install/pipbox-remote.py, do_serve_on und do_funnel_on) mit einem falschen "tailscale" im temporären Ordner.
Anlass: Bei einem Tailscale-Konto, in dem "Serve" (HTTPS) noch nicht erlaubt ist, druckt "tailscale serve" den Freischaltlink und wartet dann. Der Helfer brach nach
40 Sekunden ab und zeigte nur "timed out after 40 seconds", ohne den Link. Es wird nie eine Verbindung zu Tailscale aufgebaut."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load():
    path = os.path.join(ROOT, "install", "pipbox-remote.py")
    loader = importlib.machinery.SourceFileLoader("pipbox_remote_under_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Serve(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = self.bin + os.pathsep + self.old_path
        self.m = load()
        self.m.RUN = os.path.join(self.dir, "run")
        self.m.STATUS = os.path.join(self.m.RUN, "status.json")
        self.m.LOG = os.path.join(self.dir, "remote.log")
        self.m.SERVE_TIMEOUT = 2
        self.m.funnel_active = lambda cfg=None: False

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        shutil.rmtree(self.dir, ignore_errors=True)

    def fake(self, body):
        p = os.path.join(self.bin, "tailscale")
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, stat.S_IRWXU)

    def status(self):
        with open(self.m.STATUS) as f:
            return json.load(f)

    LINK = "https://login.tailscale.com/f/serve?node=n1234567890"

    def test_waiting_for_the_tailnet_to_allow_serve_still_shows_the_link(self):
        self.fake('echo "Serve is not enabled on your tailnet."; echo "To enable, visit:"; echo "        %s"; exec sleep 30' % self.LINK)
        self.m.do_serve_on()                                          # darf nicht abstürzen und nicht 40 s verlieren
        st = self.status()
        self.assertEqual(st["state"], "needs_serve")
        self.assertEqual(st["hint_url"], self.LINK)
        self.assertIn("Enable HTTPS", st["message"])

    def test_the_old_behaviour_returning_at_once_with_the_link_still_works(self):
        self.fake('echo "Serve is not enabled on your tailnet. To enable, visit: %s" >&2; exit 1' % self.LINK)
        self.m.do_serve_on()
        self.assertEqual((self.status()["state"], self.status()["hint_url"]), ("needs_serve", self.LINK))

    def test_a_silent_hang_gives_a_helpful_message_not_a_bare_timeout(self):
        self.fake("exec sleep 30")
        with self.assertRaises(RuntimeError) as cm:
            self.m.do_serve_on()
        text = str(cm.exception)
        self.assertIn("nicht innerhalb von 2 Sekunden", text)
        self.assertNotIn("TimeoutExpired", text)
        self.assertNotIn("timed out", text)

    def test_success_and_plain_failure(self):
        self.fake('echo "Available within your tailnet:"; exit 0')
        self.m.do_serve_on()
        self.assertEqual(self.status()["state"], "idle")
        self.fake('echo "etwas ist schiefgegangen"; exit 3')
        with self.assertRaises(RuntimeError) as cm:
            self.m.do_serve_on()
        self.assertIn("Freigabe fehlgeschlagen", str(cm.exception))
        self.assertIn("schiefgegangen", str(cm.exception))

    def test_funnel_waiting_for_permission_shows_the_link_too(self):
        self.fake('echo "Funnel is not enabled on your tailnet. To enable, visit: %s"; exec sleep 30' % self.LINK.replace("serve", "funnel"))
        self.m.do_funnel_on()
        st = self.status()
        self.assertEqual(st["state"], "needs_funnel")
        self.assertIn("login.tailscale.com", st["hint_url"])

    def test_run_capture_keeps_output_after_the_deadline_and_kills_the_command(self):
        rc, out, timed_out = self.m.run_capture(["sh", "-c", "echo hallo; exec sleep 30"], 1)
        self.assertEqual((rc, timed_out), (None, True))
        self.assertIn("hallo", out)


if __name__ == "__main__":
    unittest.main()
