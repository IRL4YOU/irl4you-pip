"""Tests für install/setup.sh (Komplett-Einrichtung: Grundsystem mit Fortschritt, Zusatzpaket mit Schrittzeilen, Ergebnis).
Alle Programme (apt-get, apt, systemctl, wget …) und install.sh sind Attrappen; getestet wird nur der Ablauf des Skripts."""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAKES = {
    "getent": '#!/bin/sh\necho "root:x:0:0::$FAKE_HOME:/bin/sh"\n',
    "pgrep": "#!/bin/sh\nexit 1\n",
    "wget": '#!/bin/sh\n[ "${FAKE_NONET:-0}" = 1 ] && exit 1\nexit 0\n',
    "hostname": '#!/bin/sh\necho "192.168.1.9 "\n',
    "systemctl": '#!/bin/sh\n'
                 '# systemctl is-active --quiet <Dienst>.service\n'
                 'for a in "$@"; do case "$a" in\n'
                 '  pipbox-send.service) [ "${FAKE_SENDING:-0}" = 1 ] && exit 0 || exit 1 ;;\n'
                 '  *.service) case " ${FAKE_DOWN:-} " in *" ${a%.service} "*) exit 1 ;; esac ;;\n'
                 'esac; done\nexit 0\n',
    "apt-get": '#!/bin/sh\n'
               'echo "apt-get $*" >> "$FAKE_CALLS"\n'
               'case " $* " in\n'
               '  *" update "*) echo "Hit:1 http://example.org jammy InRelease"; exit 0 ;;\n'
               '  *" dist-upgrade "*|*" install "*)\n'
               '    echo "Unpacking geheimes-paket (1.0) ..."\n'
               '    for p in 10.0 55.5 100.0; do echo "dlstatus:1:$p:Downloading" >&3; done\n'
               '    for p in 5.0 50.0 100.0; do echo "pmstatus:geheimes-paket:$p:Installing geheimes-paket" >&3; done\n'
               '    touch "$FAKE_DONE"; [ "${FAKE_APTFAIL:-0}" = 1 ] && exit 100; exit 0 ;;\n'
               'esac\nexit 0\n',
    "apt": '#!/bin/sh\n'
           'echo "Listing... Done"\n'
           '[ -e "$FAKE_DONE" ] && exit 0\n'
           '[ "${FAKE_UPTODATE:-0}" = 1 ] && exit 0\n'
           'echo "geheimes-paket/jammy-updates 1.1 arm64 [upgradable from: 1.0]"\n'
           '[ "${FAKE_KERNEL:-0}" = 1 ] && echo "belabox-linux-rk3588/stable 2.0 arm64 [upgradable from: 1.0]"\n'
           'exit 0\n',
}

INSTALL = ('#!/bin/sh\n'
           'echo "Installiere fehlende Pakete: bluez"\n'
           'for s in pakete dienst dateien baustein srtla belacoder start; do echo "PIPBOX-STEP $s"; echo "  viel Bauausgabe zu $s"; done\n'
           '[ -n "${FAKE_WARN:-}" ] && echo "WARNUNG: $FAKE_WARN"\n'
           'echo "IRL4YOU BOX läuft auf Port 8780 im lokalen Netz. Ersteinrichtung im Browser."\n'
           'exit ${FAKE_RC:-0}\n')


def run(**env):
    tmp = tempfile.mkdtemp()
    try:
        tree = os.path.join(tmp, "tree")
        os.makedirs(os.path.join(tree, "install"))
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        shutil.copy(os.path.join(ROOT, "install", "setup.sh"), os.path.join(tree, "install", "setup.sh"))
        open(os.path.join(tree, "VERSION"), "w").write("0.9.200")
        open(os.path.join(tree, "install", "install.sh"), "w").write(INSTALL)
        for name, text in FAKES.items():
            p = os.path.join(bindir, name)
            open(p, "w").write(text)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        calls = os.path.join(tmp, "calls.txt")
        e = dict(os.environ, PATH=bindir + ":/usr/bin:/bin", PB_TEST="1", FAKE_HOME=home, FAKE_CALLS=calls, FAKE_DONE=os.path.join(tmp, "done"),
                 PB_LOG=os.path.join(home, "install-ausgabe.txt"), **env)
        r = subprocess.run(["sh", os.path.join(tree, "install", "setup.sh")] + env.pop("ARGS", "").split(), capture_output=True, text=True, env=e, timeout=60)
        log = open(e["PB_LOG"]).read() if os.path.exists(e["PB_LOG"]) else ""
        cl = open(calls).read() if os.path.exists(calls) else ""
        return r.returncode, r.stdout + r.stderr, log, cl
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Setup(unittest.TestCase):
    def test_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", os.path.join(ROOT, "install", "setup.sh")]).returncode, 0)

    def test_clean_run_shows_progress_steps_and_success_but_no_package_names(self):
        rc, out, log, calls = run()
        self.assertEqual(rc, 0, out)
        self.assertIn("Schritt 1 von 2", out)
        self.assertIn("Pakete laden", out)
        self.assertIn("Pakete einrichten", out)
        self.assertIn("Grundsystem aktualisiert.", out)
        self.assertIn("Schritt 2 von 2", out)
        for t in ("Benötigte Pakete prüfen", "Bild-in-Bild-Baustein bauen", "SRTLA-Sender bauen", "Encoder bauen", "Dienste starten"):
            self.assertIn(t, out)
        self.assertIn("Alles fehlerfrei installiert.", out)
        self.assertIn("http://192.168.1.9:8780", out)
        self.assertNotIn("\033[", out)                                             # ohne Terminal keine Farbcodes
        self.assertNotIn("geheimes-paket", out)                                   # Paketnamen und Bauausgabe stehen nur im Protokoll
        self.assertNotIn("viel Bauausgabe", out)
        self.assertIn("geheimes-paket", log)
        self.assertIn("viel Bauausgabe zu srtla", log)
        self.assertIn("apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold -o APT::Status-Fd=3 dist-upgrade", calls)
        self.assertIn("apt-get -o Acquire::http::Timeout=30 update", calls)

    def test_warning_from_install_is_reported_and_exit_code_is_1(self):
        rc, out, log, _ = run(FAKE_WARN="Der Bild-in-Bild-Baustein konnte nicht gebaut werden")
        self.assertEqual(rc, 1)
        self.assertIn("es gibt Hinweise", out)
        self.assertIn("Der Bild-in-Bild-Baustein konnte nicht gebaut werden", out)
        self.assertNotIn("Alles fehlerfrei installiert.", out)
        self.assertIn("install-ausgabe.txt", out)

    def test_install_sh_failure_code_is_reported(self):
        rc, out, _, _ = run(FAKE_RC="3")
        self.assertEqual(rc, 1)
        self.assertIn("Fehlercode 3", out)

    def test_stopped_service_is_reported(self):
        rc, out, _, _ = run(FAKE_DOWN="nginx")
        self.assertEqual(rc, 1)
        self.assertIn("Diese Dienste laufen nicht: nginx", out)

    def test_no_internet_stops_before_anything_changes(self):
        rc, out, _, calls = run(FAKE_NONET="1")
        self.assertEqual(rc, 1)
        self.assertIn("Kein Internet", out)
        self.assertEqual(calls, "")

    def test_refuses_while_sending(self):
        rc, out, _, calls = run(FAKE_SENDING="1")
        self.assertEqual(rc, 1)
        self.assertIn("sendet gerade", out)
        self.assertEqual(calls, "")

    def test_kernel_packages_ask_for_a_restart(self):
        rc, out, _, _ = run(FAKE_KERNEL="1")
        self.assertEqual(rc, 0, out)
        self.assertIn("NEUSTART NÖTIG", out)
        self.assertIn("neuen Kernel", out)
        self.assertIn("muss jetzt neu gestartet werden", out)
        self.assertIn("sudo reboot", out)
        self.assertLess(out.index("http://192.168.1.9:8780"), out.index("NEUSTART NÖTIG"))    # erst der Link, dann der deutliche Hinweis

    def test_link_and_restart_notice_are_colored_on_a_terminal(self):
        rc, out, _, _ = run(FAKE_KERNEL="1", PB_COLOR="1")
        self.assertIn("\033[1;4;36mhttp://192.168.1.9:8780\033[0m", out)                     # Link: fett, unterstrichen, cyan
        self.assertIn("\033[1;30;43m NEUSTART NÖTIG \033[0m", out)                          # Hinweis: schwarz auf gelb
        self.assertIn("\033[1;32mAlles fehlerfrei installiert.\033[0m", out)

    def test_no_restart_notice_without_a_new_kernel(self):
        rc, out, _, _ = run()
        self.assertNotIn("NEUSTART", out)
        self.assertNotIn("sudo reboot", out)
        self.assertIn("http://192.168.1.9:8780", out)                                      # der Link steht auch ohne Neustart

    def test_up_to_date_system_skips_the_upgrade(self):
        rc, out, _, calls = run(FAKE_UPTODATE="1")
        self.assertEqual(rc, 0, out)
        self.assertIn("schon aktuell", out)
        self.assertNotIn("dist-upgrade", calls)
        self.assertNotIn("neuen Kernel", out)

    def test_system_update_failure_does_not_stop_the_installation(self):
        rc, out, log, _ = run(FAKE_APTFAIL="1")
        self.assertIn("Systemupdate meldete einen Fehler", out)
        self.assertIn("Schritt 2 von 2", out)
        self.assertIn("Alles fehlerfrei installiert.", out)

    def test_option_skips_the_system_update(self):
        rc, out, _, calls = run(ARGS="--ohne-systemupdate")
        self.assertEqual(rc, 0, out)
        self.assertIn("übersprungen", out)
        self.assertNotIn("apt-get", calls)


if __name__ == "__main__":
    unittest.main()
