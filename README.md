# IRL4YOU PIP / IRL4YOU BOX

Bild-in-Bild (PIP, Picture-in-Picture) für eine BELABOX: mehrere Kameras zu einem Bild mischen und über mehrere Leitungen senden. Eigene Weboberfläche auf Port 8780, getrennt von der Original-Oberfläche. Beta.

## Einrichtung

**Voraussetzungen:** eine BELABOX mit laufendem BELABOX-Image, im selben Netz wie dein Rechner, und ein Benutzer mit `sudo` (SSH-Zugang). Die Box braucht für die Installation Internet.

**1. Installieren** (per SSH auf der Box):

```sh
cd /tmp && rm -rf irl4you-pip-main irl4you-pip.tar.gz && wget -q -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz && tar xzf irl4you-pip.tar.gz && cd irl4you-pip-main && sudo sh install/setup.sh
```

Das dauert einige Minuten, weil unter anderem der Encoder (`belacoder`) gebaut wird. Am Ende nennt das Skript die Adresse der Oberfläche.

**2. Anmelden:** im Browser `http://<Adresse der Box>:8780` öffnen und mit dem BELABOX-Passwort anmelden.

**3. Server eintragen:** Karte „SRTLA: Server, Bitrate und Latenz“: Name, Adresse, Port und (falls dein Server sie verlangt) die Stream-ID eintragen, Server auswählen. Darunter Mindest- und Höchstbitrate sowie die Latenz einstellen. Live geht erst, wenn ein Server gewählt ist.

**4. Netze wählen:** Karte „Verbindungen“: die Netze (SIM, WLAN, LAN) auswählen, über die gesendet wird.

**5. Kameras verbinden:** Karte „Kameras“: DJI-Kameras (Osmo Action 3, 4, 5 Pro, 6, Osmo Pocket 3, 4, 4 Pro) per Bluetooth koppeln, außerdem Handys, HDMI-Eingänge und USB-Kameras. Welche Kamera Hauptbild oder Bild-in-Bild ist, stellst du in der Karte „Bildaufbau“ ein.

**6. Live gehen:** Fußleiste „Live“. Die Karte „Vorschau“ zeigt das gesendete Bild.

**Update und Rückweg:** Spätere Versionen spielt die Karte „Software-Update (IRL4YOU BOX)“ ein. Entfernen: `sudo sh install/install.sh uninstall`.

MIT-Lizenz; `srtla` AGPL-3.0, `belacoder` GPL-3.0, `bluetooth-src` GPL-2.0 (siehe `LICENSE` und `NOTICE.md`). **Version 1.0.66.**

Änderungen: [CHANGELOG.md](CHANGELOG.md).

## Hilfe

Fragen und Fehler: [Discord](https://discord.gg/nrBCEarMup). Bei Hardware-Fragen (Kameras, Bluetooth-Sticks, WLAN, Strom) bitte dort an **Akinos01** wenden.

## Herkunft und Dank

* DJI-Kameras per Bluetooth: Das Protokoll stammt vom Projekt [Moblin](https://github.com/eerimoq/moblin) (Erik Moqvist, MIT-Lizenz). Geprüft wurde es gegen [datagutt/node-osmo](https://github.com/datagutt/node-osmo) und [dimadesu/dji-remote](https://github.com/dimadesu/dji-remote) (beide MIT). Ablauf der Sitzung, Verbindungsliste und Befehle folgen dem DJI-Dienst von [Bittersweet1987](https://github.com/Bittersweet1987), hier für IRL4YOU BOX angepasst.
* BELABOX: Sendekette, `srtla` und `belacoder` stammen aus dem [BELABOX-Projekt](https://github.com/BELABOX).
* Lizenztexte und genaue Angaben stehen in [NOTICE.md](https://github.com/IRL4YOU/irl4you-pip/blob/main/NOTICE.md).

Zusätzlich:

* Osmo Pocket 4 Pro: Die Erkennung folgt einer Änderung im Projekt [Moblin](https://github.com/eerimoq/moblin) (Commit 0034402).
* Kameras im laufenden Betrieb starten, stoppen und das Bild zwischen ihnen wechseln (`belacoder`): [Bittersweet1987](https://github.com/Bittersweet1987) (Pull Request #27).
* Steuerung des Kamerabetriebs, damit das Bild sauber geliefert wird (Puffer, Verzögerung, Regelung): von Bittersweet1987 begonnen, von Akinos01 maßgeblich erweitert und umgebaut.
