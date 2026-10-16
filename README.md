# IRL4YOU PIP / IRL4YOU BOX

Zusatzpaket für eine BELABOX: mehrere Kameras zu einem Bild mischen und über mehrere Leitungen senden. Eigene Weboberfläche auf Port 8780, getrennt von der Original-Oberfläche. Beta.

**Version 1.0.60.** MIT-Lizenz; `srtla` AGPL-3.0, `belacoder` GPL-3.0, `bluetooth-src` GPL-2.0 (siehe `LICENSE` und `NOTICE.md`).

**Installieren** (als Benutzer mit sudo auf der Box):

```sh
cd /tmp && rm -rf irl4you-pip-main irl4you-pip.tar.gz && wget -q -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz && tar xzf irl4you-pip.tar.gz && cd irl4you-pip-main && sudo sh install/setup.sh
```

Danach im Browser `http://<Adresse der Box>:8780` öffnen und mit dem BELABOX-Passwort anmelden. Spätere Versionen spielt die Karte „Software-Update“ ein. Rückweg: `sudo sh install/install.sh uninstall`.

Änderungen: [CHANGELOG.md](CHANGELOG.md). Fragen und Fehler: [Discord](https://discord.gg/nrBCEarMup).
