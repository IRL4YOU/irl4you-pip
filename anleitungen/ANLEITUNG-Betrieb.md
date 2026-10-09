# IRL4YOU BOX: Betrieb und Pflege

Diese Anleitung zeigt dir, wie du die IRL4YOU BOX installierst, aktuell hältst, sicherst und bei Problemen Hilfe bekommst. Die Weboberfläche läuft getrennt von der Original-Oberfläche der BELABOX auf Port 8780. Es ist eine Beta: Neue Versionen kommen oft, und zurück auf eine frühere Version geht in der Oberfläche.

## 1. Installation und erste Anmeldung

Du brauchst eine BELABOX mit dem BELABOX-Image (getestet: Radxa ROCK 5B+ und Orange Pi 5 Plus), Internet auf der Box und ein Terminal auf der Box (SSH oder Tastatur). Nicht während einer Übertragung installieren.

1. **BELABOX-Passwort:** Hat deine BELABOX noch kein Passwort (frisches Image), lege zuerst eines in der BELABOX-Oberfläche fest (`http://<Adresse der Box>/`). Die Oberfläche der IRL4YOU BOX nutzt dasselbe Passwort. Einen eigenen Setup-Code gibt es nicht.
2. **Installieren:** Gib auf der Box im Terminal ein:

```sh
cd /tmp
wget -O irl4you-pip.tar.gz https://github.com/IRL4YOU/irl4you-pip/archive/refs/heads/main.tar.gz
tar xzf irl4you-pip.tar.gz
cd irl4you-pip-main
sudo sh install/install.sh
```

   Die Installation lädt fehlende Pakete nach und baut den Bild-in-Bild-Baustein und den SRTLA-Sender selbst. Das kann einige Minuten dauern. Gelingt die Installation der Bluetooth-Bibliothek `bleak` (für den DJI-Dienst) nicht, bricht sie mit einer Meldung ab, bevor etwas verändert wurde.
3. **Anmelden:** Öffne im Browser `http://<Adresse der Box>:8780` und melde dich mit dem BELABOX-Passwort an. Mit dem Haken **Angemeldet bleiben** bleibst du 30 Tage angemeldet, auch über Updates hinweg. Ist noch kein Passwort gesetzt, steht auf der Seite, dass du es zuerst in der BELABOX-Oberfläche festlegen musst; die Seite zeigt die Anmeldung dann von selbst.

Spätere Versionen spielst du über die Karte **Software-Update** ein, ein erneutes Installieren ist nicht nötig.

**Rückweg:** `sudo sh install/install.sh uninstall` entfernt Dienste und Programme. Zustand (`/var/lib/pipbox`), Sicherungen (`/var/lib/pipbox-backup`), Protokolle (`/var/log/pipbox-*.log`) und der Benutzer `pipbox` bleiben liegen.

## 2. Updates

### Software-Update (die IRL4YOU BOX selbst)

Ein **gelber Punkt in der Kopfleiste** ("Update Oberfläche") zeigt, dass eine neuere Version da ist. Kameras, Server und Einstellungen bleiben beim Update erhalten.

1. Beende eine laufende Übertragung. Während einer Übertragung ist das Update gesperrt.
2. Öffne die Karte **Software-Update (IRL4YOU BOX)** und klicke **Nach Updates suchen**.
3. Klicke **Update installieren** und bestätige. Die Oberfläche startet dabei kurz neu (etwa eine halbe Minute), ein Balken zeigt den Fortschritt. Danach lädt die Seite von selbst neu.

**Änderungen ansehen:** Ist ein Update da, zeigt die Karte die Änderungen **aller** neuen Versionen. Mit **Alle Änderungen** lädst du den ganzen Verlauf von GitHub (nicht während einer Übertragung); im Suchfeld darüber findest du mit Stichworten, mit welcher Version etwas kam (alle Wörter müssen vorkommen, Treffer sind markiert). Die Liste nennt die Ergebnisse jeder Version, nicht jeden einzelnen Testschritt.

Die jetzige Version wird vorher gesichert, die letzten 5 Versionen bleiben gesichert. Klappt ein Update nicht, stellt der Update-Helfer die vorige Version automatisch wieder her.

**Zurück auf eine frühere Version:** Wähle in der Karte unter "Auf eine bestimmte Version wechseln (auch ältere)" eine Version aus der Liste (oder trage sie ein) und klicke **Wechseln**. Es gehen gesicherte Versionen und Releases auf GitHub. Gibt es eine vorherige Version, erscheint zusätzlich **Zurück zur vorherigen Version**. Auch das geht nicht während einer Übertragung.

### System-Updates (BELABOX)

Die Karte **System-Updates (BELABOX)** installiert echte Systemupdates (Kernel, BELABOX-Pakete). Die Box sucht nach jedem Start und danach alle 6 Stunden still nach der Paketliste, nie während einer Übertragung. Liegen Updates bereit, erscheint der gelbe Punkt "System" in der Kopfleiste.

1. Nur ohne Übertragung und mit stabiler Stromversorgung. Bei einem Kernel-Update kann die Box nach einem Stromausfall unbrauchbar werden.
2. In der Karte **Nach Updates suchen**, optional **Probelauf**, dann **Updates installieren**.
3. Danach ist meist ein Neustart nötig (**Neu starten**), und du solltest die Kameras neu prüfen. Ein automatisches Zurückrollen gibt es hier nicht.

## 3. Einstellungen sichern und einspielen

Praktisch zum Beispiel nach dem Neu-Aufspielen der SD-Karte. Öffne die Karte **Einstellungen sichern**.

**Sichern:**
1. Lass **Passwörter mitnehmen** (WLAN, Hotspot, DJI, Stream-ID) angehakt oder nimm den Haken weg.
2. Mit Passwörtern ist die Datei immer verschlüsselt (AES-256). Gib ein Passwort ein (mindestens 8 Zeichen) und wiederhole es. Das Passwort wird nirgends gespeichert: Merke es dir gut.
3. Klicke **Datei herunterladen** und bewahre die Datei sicher auf.

**Einspielen:** Nur ohne Sendung.
1. Wähle die Datei aus. Ist sie verschlüsselt, gib ihr Passwort ein und klicke **Öffnen**.
2. Wähle die Teile, die du einspielen willst, und klicke **Ausgewählte Teile einspielen**. Sie ersetzen die jetzigen Einstellungen.
3. Der Stand davor wird gesichert. Mit **Letzten Stand zurückholen** kommst du dahin zurück.

**Enthalten** sind unter anderem: Kameras, Bildaufbau, SRTLA-Server, DJI-Kameras (Einstellungen), HDMI-Eingang, Akku-Warnung im Twitch-Chat (ohne Token), Hotspots und gespeicherte WLAN-Netze. **Nicht enthalten** sind die Einstellungen unter **Optionen** (Reihenfolge und Ausblenden der Menüs, Überschriften von Chat und Vorschau): Sie gelten je Gerät und stehen nur im jeweiligen Browser.
**Nicht enthalten:** das Passwort der Oberfläche, SSH, Schlüssel, der Twitch-Token samt Twitch-Anmeldung und die Bluetooth-Kopplung der DJI-Kameras (diese musst du neu koppeln). Unternehmens-WLANs lassen sich nicht übertragen.
Das Einspielen von WLAN-Netzen und DJI-Kameras ist noch nicht mit einer echten Box geprüft.

## 4. Protokolle

**Protokolle herunterladen:** In der Karte **Protokolle** erzeugt dieser Knopf eine Textdatei mit den Meldungen der Box (Kameras, Senden, Updates, Netzwerk), etwa für ein GitHub-Issue. Vorher werden Passwörter, Stream-ID, Servername, WLAN-Namen sowie IP- und MAC-Adressen durch Platzhalter ersetzt. **Sieh die Datei trotzdem kurz durch**, bevor du sie weitergibst.

**Zwei Stufen** (gleiche Karte):
- **Sparsam** (Standard bei neuen Installationen): Protokolle nur im Arbeitsspeicher. Das schont die Speicherkarte. Nach einem Absturz oder Stromausfall bleibt aber keine Spur.
- **Ausführlich:** Protokolle dauerhaft auf der Karte (Journal höchstens 30 MB und 7 Tage, Zustandsprotokoll höchstens 4 MB, alle 10 Sekunden). Gut für die Fehlersuche, etwa wenn sich die Box von selbst ausschaltet. Jeder Schreibvorgang belastet die Speicherkarte: Danach wieder auf "Sparsam" stellen.

## 5. Ausschalten, Neustart, Entwickler-Karte

Karte **Box ausschalten und abmelden:**
- **Herunterfahren** fährt die Box sauber herunter. Eingeschaltet wird sie nicht über die Oberfläche, sondern durch kurzes Trennen des Stroms oder mit der Ein-Taste.
- **Neu starten** startet die Box neu; die Seite lädt nach etwa einer Minute wieder.
- Läuft eine Übertragung, warnt dich die Abfrage: Sie bricht beim Herunterfahren oder Neustart ab.
- **Abmelden** beendet die Anmeldung auf diesem Gerät, auch eine mit "Angemeldet bleiben" gemerkte.

Karte **Entwickler** (SSH-Zugang wie in der Original-Oberfläche der BELABOX):
- **SSH einschalten** oder **SSH ausschalten** startet bzw. beendet nur den SSH-Dienst. Beim ersten Einschalten wird bei Bedarf ein zufälliges Passwort erzeugt.
- **Passwort anzeigen** zeigt das SSH-Passwort, **Passwort zurücksetzen** erzeugt ein neues (das alte gilt danach nicht mehr).
- Lass SSH ausgeschaltet, wenn du es nicht brauchst.

## 6. Sprachen, Hell/Dunkel, Streammodus, Handy

- **Sprache:** Die Auswahl erscheint beim ersten Öffnen und danach in der Kopfleiste. Es gibt 14 Sprachen: Englisch (Standard), Deutsch, Französisch, Spanisch, Portugiesisch (Brasilien), Italienisch, Niederländisch, Polnisch, Türkisch, Russisch, Chinesisch (vereinfacht), Japanisch, Koreanisch und Thai. Außer Deutsch sind die Texte maschinell übersetzt und nicht von Muttersprachlern geprüft.
- **Hell/Dunkel:** Der Knopf mit Sonne bzw. Mond in der Kopfleiste wechselt das Aussehen. Die Wahl merkt sich der Browser.
- **Verbindungsanzeige (Punkt und Linie neben „Chat“):** Der Punkt pulsiert grün, gelb oder rot, die Linie zeigt die Senderate der letzten 30 Sekunden in Mbit/s. Sie startet mit 4 Punkten; **grün** sind 3 oder 4, **gelb** genau 2, **rot** höchstens 1, **grau** heißt: es wird nicht gesendet. Abzüge: ein gewählter Sendeweg ist nicht aktiv (−1); Laufzeit über 400 ms (−1) oder über 1000 ms (−2); Sendepuffer über 1500 ms (−1) oder über 3000 ms (−2); zu wenig gesendet: nur wenn der Sendepuffer über 300 ms anwächst und weniger als 80 % der Ziel-Bitrate rausgehen (−1) oder weniger als 50 % (−2), oder wenn fast nichts (unter 10 %) rausgeht (−2). Dass der Encoder bei einer ruhigen Szene weniger als das Maximum (zum Beispiel 8 statt 12 Mbit/s) liefert, zählt **nicht** als Fehler.
- **Menüs ordnen und ausblenden (Karte Optionen):** Reihenfolge der Hauptmenüs, ausgeblendete Menüpunkte und die Überschriften von Chat und Vorschau gelten **je Gerät (Browser)**, nicht für die ganze Box: Handy und Rechner können verschieden eingestellt sein. Sie stehen nicht in der Sicherung. Ein neues Gerät oder ein gelöschter Browserspeicher beginnt mit dem Standard.
- **Streammodus:** Der Knopf in der Kopfleiste (und in der Live-Karte) blendet Adressen, Namen und Protokolle aus, wenn der Bildschirm mitgefilmt wird. Auch das merkt sich nur der Browser.
- **Handy-Ansicht:** Die feste **Fußleiste** unten richtet sich nach **Optionen → „Fußleiste am Handy“** (je Gerät): **Automatisch** blendet sie beim Runterscrollen aus und beim Hochscrollen wieder ein, aber nur, solange Chat oder Vorschau zu sehen sind (ganz oben und ganz unten ist sie immer da; ohne Chat und Vorschau bleibt sie immer stehen); **Immer anzeigen** lässt sie stehen; **Ausblenden bei Chat oder Vorschau** nimmt sie weg, solange eines von beiden zu sehen ist; **Immer ausblenden** zeigt sie nie. Der Chat ist am Handy (Bildschirm bis 620 px) besonders dicht gesetzt: kleine Schrift, Zeilenabstand 1,15, keine Luft zwischen den Nachrichten, damit möglichst viele auf einmal zu sehen sind. Am Handy zeigt die Kopfleiste einen Knopf zum Ein- und Ausklappen weiterer Knöpfe. Bei "Bild in Bild" steht unten eine feste Fußleiste mit **Live/Stop**, einem Knopf je Kamera und dem Ton-Knopf. Kamera: kurzer Druck macht sie zum Hauptbild, Doppeltipp blendet das kleine Bild aus oder ein, langer Druck deaktiviert die Kamera (nochmal lang aktiviert sie wieder). Ton: kurzer Druck wechselt die Tonspur, langer Druck schaltet stumm. Die Fußleiste ist mit Testquellen geprüft, noch nicht mit echten Kameras.

## 7. Sicherheit

- Die Oberfläche (Port 8780) nimmt nur Anfragen aus privaten Netzen, von der Box selbst und über Tailscale an. Öffentliche Adressen werden abgewiesen. Nach 5 Fehlversuchen bei der Anmeldung wird gesperrt.
- **Im Netz, über das die Box sendet (zum Beispiel ein fremdes WLAN), ist die Verbindung unverschlüsselt (HTTP).** Dort kann jeder mitlesen. Benutze die Oberfläche dort nur über Tailscale (HTTPS), oder schalte in der Karte **Verbindungen** den Schalter **Über fremde WLANs sperren** ein.
- Von unterwegs: Nimm Tailscale statt einer öffentlichen Freigabe. Die Einrichtung steht in [ANLEITUNG-Fernzugriff.md](../ANLEITUNG-Fernzugriff.md). Der Knopf "Funnel" macht die Oberfläche öffentlich im Internet, geschützt nur durch das BELABOX-Passwort: Nur einschalten, wenn du das wirklich willst.
- Zugangsdaten liegen nur auf der Box in `/var/lib/pipbox`, nicht im Repository.
- Bei Updates wird Code aus dem Repository als root ausgeführt: Vertraue also dem Repository.

## 8. Fehler melden

**Am einfachsten über die Box:** Die Karte **Problem melden / Wunsch äußern** bereitet das Formular auf GitHub vor.

- Wähle **Problem** (Titel Pflicht, Beschreibung freiwillig) oder **Wunsch** (Titel und Beschreibung Pflicht), trage den Titel ein und klicke **Formular vorbereiten**.
- Bei einem Problem bekommst du eine **Support-Nummer** (`IRL-XXXXXX`). Sie steht im Titel (`[ISSUE] Titel (Nummer)`); damit könnt ihr beide das Issue wiederfinden. Mit dem Haken **Bereinigtes Protokoll mitnehmen** lädt die Box das Protokoll als Datei, die du auf GitHub ins Textfeld ziehst.
- Der Knopf **Formular auf GitHub öffnen** öffnet einen neuen Tab. Dort brauchst du ein **GitHub-Konto** (kostenlos) und klickst auf „Submit new issue“. Die Box sendet selbst nichts, sie hat dafür keinen Zugang zu GitHub.
- Unter **Meine Fälle** stehen deine Support-Nummern mit Datum, mit einer Suche auf GitHub. **Erledigt** gilt nur auf der Box; das Issue schließt du auf GitHub selbst.

Von Hand geht es auch:

1. Beschreibe, was du getan hast, was du erwartet hast und was passiert ist. Nenne die Version (steht in der Karte Software-Update unter "Installiert") und dein Gerät.
2. Lade in der Karte **Protokolle** die Protokolle herunter und sieh sie durch (siehe oben). Hilft bei seltenen Fehlern, vorher die Stufe "Ausführlich" einzuschalten.
3. Öffne ein **Issue** im GitHub-Projekt [IRL4YOU/irl4you-pip](https://github.com/IRL4YOU/irl4you-pip) und hänge das Protokoll an.
4. Oder frag in der Community auf Discord: https://discord.gg/nrBCEarMup

### Wächter der Sendekette

Im Modus **Alle Kameras immer bereit** prüft die Box jede Sekunde drei Dinge und startet bei einem Fehler **nur den Encoder (belacoder)** neu, nie die Verbindung zum Server:
- **Statistik steht still:** Die Statistikdatei von belacoder ist älter als 15 Sekunden.
- **Threads hängen im Kernel:** Mindestens ein Thread von belacoder bleibt länger als 12 Sekunden im Zustand D (wartet im Kernel, meist im Treiber des Hardware-Dekoders). Das ist dieselbe Zahl wie „Prozess(e) blockiert (D-State)“ in der Oberfläche.
- **Kamera ohne Bild im Mischer:** Eine Kamera sendet mit Bild bei nginx und ihr Zweig gilt als laufend, im Mischer kommen aber seit 25 Sekunden keine Bilder an.

In den ersten 30 Sekunden nach dem Start wird nichts bewertet. Zwischen zwei Eingriffen liegen mindestens 90 Sekunden, und es gibt höchstens vier je Stunde (sonst gäbe ein Dauerfehler eine Neustartschleife). Vor dem Neustart schreibt der Wächter ein **Diagnosepaket** nach `/var/lib/pipbox/hang-diagnose.txt` (die letzten sechs bleiben): alle Threads von belacoder mit Zustand, Wartestelle im Kernel und CPU-Zeit, alle Threads im Zustand D im ganzen System mit dem Kernel-Stapel, die Statistik und die letzten Ereignisse der Sendekette, je Platz was nginx meldet. Es enthält keine Schlüssel und keine Adressen und erscheint in den Protokollen (Karte Protokolle, Abschnitt „Hänger der Sendekette“). Das Journal nennt den Eingriff mit einer Zeile „send: Hänger erkannt …“. Der Status der Sendekette zählt sie als `hang_restarts`.

## 9. Wenn etwas nicht geht

| Problem | Was du tun kannst |
|---|---|
| Die Seite auf Port 8780 öffnet sich nicht | Adresse der Box und `:8780` prüfen. Du musst in einem privaten Netz sein (oder Tailscale nutzen), öffentliche Adressen werden abgewiesen. |
| Anmeldung geht nicht | Es gilt das BELABOX-Passwort. Nach 5 Fehlversuchen gibt es eine Sperre. Steht auf der Seite, dass zuerst ein Passwort gesetzt werden muss, lege es in der BELABOX-Oberfläche fest. |
| Installation bricht ab | Meldung lesen. Häufig fehlt Internet auf der Box (`bleak` oder Pakete konnten nicht installiert werden). Internet prüfen und `install.sh` erneut ausführen. |
| Software-Update lässt sich nicht starten | Läuft gerade eine Übertragung? Dann ist es gesperrt. Übertragung beenden. Steht dort, der Update-Helfer sei nicht installiert, einmal `install.sh` erneut ausführen. |
| Update schlägt fehl | Der Update-Helfer stellt die vorige Version automatisch wieder her. Nach dem Neuladen steht die Meldung in der Karte. Protokoll herunterladen und melden. |
| Neue Version macht Probleme | In der Karte Software-Update auf eine frühere Version wechseln. |
| Upload bricht ein, Bitrate fällt auf unter 1 Mbit/s oder „Prozesse blockiert (D-State)“ erscheint (hinter dem **i** stehen die wartenden Kernel-Aufgaben, im Protokoll bei „Zustandsprotokoll“ als `blocked=… d=Name@Wartestelle`; oft ein USB-Modem oder -Stick, der sich zusätzlich als CD-Laufwerk meldet, das hat mit dem Mischer nichts zu tun) | Meist hängt der Mischer der Sendekette nach dem Ausfall und Wiederkommen einer Kamera (Issue #51). Die Box erkennt das seit 0.9.170 selbst (Statistik steht still, Threads im Kernel-Zustand D, oder eine sendende Kamera liefert dem Mischer keine Bilder), sichert ein Diagnosepaket und startet den Encoder neu (dauert einige Sekunden). Das Paket steht in den Protokollen unter „Hänger der Sendekette“; bitte Protokoll herunterladen und melden. Hilft der Neustart nicht, Stop und dann Live drücken. |
| Nach dem Neustart ist die Seite weg | Etwa eine Minute warten, dann neu laden. |
| Box nach dem Herunterfahren aus | Strom kurz trennen oder Ein-Taste nutzen. |
| Box schaltet sich von selbst aus | Stromversorgung prüfen. Stufe "Ausführlich" einschalten, damit nach einem Ausfall Spuren bleiben, und das Protokoll melden. |
| Einspielen einer Sicherung geht nicht | Es darf nicht gesendet werden. Bei verschlüsselter Datei das richtige Passwort eingeben. Mit "Letzten Stand zurückholen" geht es zurück. |
| SSH-Passwort unbekannt | Karte Entwickler: **Passwort anzeigen** oder **Passwort zurücksetzen**. |
| Seite in fremdem WLAN nicht erreichbar | Ist "Über fremde WLANs sperren" an? Dann nur über Tailscale, Ethernet, eigenen Hotspot oder USB gehen. |
