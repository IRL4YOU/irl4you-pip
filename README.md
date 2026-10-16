# IRL4YOU PIP / IRL4YOU BOX

Zusatzpaket für eine BELABOX: mehrere Kameras zu einem Bild mischen und über mehrere Leitungen senden. Eigene Weboberfläche auf Port 8780, getrennt von der Original-Oberfläche. Beta.

**Version 1.0.65.** MIT-Lizenz; `srtla` AGPL-3.0, `belacoder` GPL-3.0, `bluetooth-src` GPL-2.0 (siehe `LICENSE` und `NOTICE.md`).

**Installieren** (als Benutzer mit sudo auf der Box):

```sh
cd /tmp && rm -rf irl4you-pip-main irl4you-pip.tar.gz && wget -q -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz && tar xzf irl4you-pip.tar.gz && cd irl4you-pip-main && sudo sh install/setup.sh
```

Danach im Browser `http://<Adresse der Box>:8780` öffnen und mit dem BELABOX-Passwort anmelden. Spätere Versionen spielt die Karte „Software-Update“ ein. Rückweg: `sudo sh install/install.sh uninstall`.

Änderungen: [CHANGELOG.md](CHANGELOG.md). Fragen und Fehler: [Discord](https://discord.gg/nrBCEarMup).

## Herkunft und Dank

- **DJI-Kameras per Bluetooth:** Das Protokoll stammt vom Projekt [Moblin](https://github.com/eerimoq/moblin) (Erik Moqvist, MIT-Lizenz). Geprüft wurde es gegen [datagutt/node-osmo](https://github.com/datagutt/node-osmo) und [dimadesu/dji-remote](https://github.com/dimadesu/dji-remote) (beide MIT). Ablauf der Sitzung, Verbindungsliste und Befehle folgen dem DJI-Dienst von [Bittersweet1987](https://github.com/Bittersweet1987), hier für IRL4YOU BOX angepasst.
- **BELABOX:** Sendekette, `srtla` und `belacoder` stammen aus dem [BELABOX-Projekt](https://github.com/BELABOX).
- Lizenztexte und genaue Angaben stehen in [NOTICE.md](NOTICE.md).

