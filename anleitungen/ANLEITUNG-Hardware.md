# Hardware: Bluetooth-Stick, WLAN-Stick und Kamera-WLAN

Kurze Hinweise zu USB-Sticks und zum WLAN der Kameras. Was nicht mit echter Hardware geprüft ist, steht dabei.

## 1. Bluetooth-Stick für die DJI-Kameras

Die eingebauten Bluetooth-Module der Boxen empfangen schlecht, ein USB-Stick ist besser. In der Karte **Verbindungen**, Abschnitt **Bluetooth**,
siehst du, welche Sticks laufen, ob gerade ein Treiber eingerichtet wird und ob ein Stick nicht unterstützt wird.

| Stick | Chip | Stand |
|---|---|---|
| ASUS USB-BT500 (`0b05:190e`) | Realtek RTL8761B | **getestet**, läuft ohne Zusatztreiber |
| UGREEN Bluetooth 5.4 / 6.0 (`33fa:0010`, `33fa:0012`) | Barrot BR8654/8554 | **getestet** (BT 6.0), die Box richtet den Treiber selbst ein |
| TP-Link UB500 (`2357:0604`) und weitere Realtek-Sticks (`2550:8761`, `2c4e:0115`, `0bda:8771`, `0bda:a725`, `2b89:8761`) | Realtek RTL8761B | Treiber wird selbst eingerichtet, **nicht geprüft** |

**Treiber beim Einstecken:** Manche Sticks kennt der Kernel 5.10 nicht oder startet sie falsch. Steckt so ein Stick, baut die Box aus den mitgelieferten
Kernel-Quellen (`bluetooth-src/`, GPL-2.0, unverändert, per SHA-256 geprüft) das Modul `btusb` neu und lädt es. Das dauert wenige Minuten.

- Nur auf dem passenden Kernel (5.10.160) mit vorhandenen Headern, **nie während einer Übertragung**; es wird nichts aus dem Internet geholt.
- Läuft danach kein Adapter oder lädt die Firmware nicht, wird alles zurückgerollt; der Standardtreiber wird nie überschrieben.
- Nach einem Kernel-Update prüft die Box nach dem Start und alle 15 Minuten und richtet den Treiber neu ein.
- Nach dem Wechsel auf einen anderen Stick fragt eine DJI-Kamera eventuell einmal nach der Kopplung.
- Rückweg: `/lib/modules/<Kernel>/updates/btusb.ko` löschen (macht `install.sh uninstall`).

## 2. WLAN-Stick UGREEN AX900 (Chip AIC8800D80)

Der Kernel 5.10 hat dafür keinen Treiber. Der Stick meldet sich zuerst als **USB-Laufwerk** (`a69c:5723`) und muss in den WLAN-Modus geschaltet werden.
Die Box erledigt das beim Einstecken selbst, **einmal mit Internet** (der Treiber wird geholt und gebaut, das dauert einige Minuten).

- **Getestet** an der Orange Pi 5 Plus (blauer USB-3-Anschluss): Treiber gebaut, Stick umgeschaltet, `wlan0` entsteht und verbindet sich; auch nach erneutem Einstecken und Neustart.
- Der Treiber kommt im festen Stand `1d1b8ff` (`shenmintao/aic8800d80`, Zweig `legacy-mcu1`, GPL-2.0) und wird gegen eine feste SHA-256-Summe geprüft.
  Neuere Firmware passt bei diesem Chip nicht in den Speicher (gemessen), deshalb dieser Stand.
- Der Helfer läuft mit eingeschränkten Rechten, nimmt keine Eingaben an und baut **nie während einer Übertragung**. Ohne Internet versucht er es später erneut.
- Rückweg: `sudo python3 /opt/pipbox/pipbox-wlandriver.py uninstall`.

## 3. WLAN der Kameras (Hinweise aus dem Betrieb)

DJI-Kameras setzen im WLAN gelegentlich für einige Sekunden aus. Was bei uns half (GL.iNet-Router mit Mobilfunk, vier Kameras, Orange Pi 5 Plus):

- **Nur 5 GHz, WPA2, 20 MHz Kanalbreite.** Nach der Umstellung von WPA3 auf WPA2 gab es über Stunden keinen Aussetzer mehr (vorher bei einer Kamera etwa einen pro Minute).
  Ein Kanalwechsel allein brachte nichts. WPA2 mit langem Passwort reicht für ein reines Kameranetz.
- **Kanal 36 bis 48 (kein DFS).** Die Osmo Action 5 Pro unterstützt laut Datenblatt im 5-GHz-Band nur 5150 bis 5250 und 5725 bis 5850 MHz. Bitte die Freigaben für deinen Standort selbst prüfen.
- **RTMP-Leerlaufgrenze.** Der RTMP-Server der BELABOX wirft eine Kamera, die 4 Sekunden nichts schickt, aus dem Bild (`drop_idle_publisher 4s`) und der Encoder startet neu.
  Dieses Paket setzt die Grenze auf 15 Sekunden (Sicherung `99-belabox-rtmp.conf.vor-pipbox`, ein apt-Haken stellt sie nach BELABOX-Updates wieder her).
  Die Aussetzer der Kamera beseitigt das nicht, es startet nur nichts mehr neu.
- **Sendewege nicht im Kamerafunk.** Lag der Hotspot eines Handys als Sendeweg auf demselben 5-GHz-Kanal wie das Kamera-WLAN, stiegen Laufzeitspitzen und Neuübertragungen
  (Beobachtung, nicht bewiesen). Besser: Sendewege **per Kabel** (USB-Tethering, Router per Ethernet) oder auf einem anderen Band.
- Die **Ampeln** in der Karte Status zeigen, welche Kamera im Bild ist und welcher Sendeweg trägt (Erklärung beim Darüberfahren).

---

Weitere Anleitungen und Themen: [irl4you.de](https://irl4you.de)
