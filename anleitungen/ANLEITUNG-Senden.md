# Senden mit der IRL4YOU BOX

Diese Anleitung zeigt dir, wie du Server, Sendewege und Bild einstellst und live gehst. Die Oberfläche öffnest du im Browser unter `http://<Adresse der Box>:8780`.

> **Beta:** Das Paket läuft im Alltag, ist aber nicht in jedem Punkt lange und unterwegs getestet. Was noch ungetestet ist, steht im jeweiligen Abschnitt und gesammelt unter "Was noch nicht geprüft ist".

## 1. SRTLA-Server eintragen und umschalten

Karte **SRTLA**, Bereich **SRTLA-Server**:

1. Name, Adresse und Port deines Servers eintragen. Die Stream-ID ist optional.
2. **Server hinzufügen** klicken.
3. Zum Umschalten wählst du den Server in der Auswahl oben aus. **Bearbeiten** und **Löschen** stehen daneben.

Die **Stream-ID** ist ein Zugangsschlüssel. Die Box speichert sie und zeigt sie **nie wieder an**. Beim Bearbeiten trägst du sie bei Bedarf neu ein.

Läuft gerade eine Sendung, fragt die Oberfläche nach einer Änderung, ob die Sendung neu starten soll. Bis dahin gilt der alte Stand.

## 2. Sendewege wählen

Karte **Verbindungen**, Bereich **Verbindungen zum Senden (Upload)**:

1. Setze ein Häkchen bei jedem Netz, über das die Box senden soll. Das können Ethernet, WLAN, ein USB-/Mobilfunk-Router oder ein Hotspot sein.
2. Das Häkchen wird sofort gespeichert. Es wirkt auch während der Sendung nach wenigen Sekunden, ohne Neustart.

Die Box bündelt alle angehakten Netze gleichzeitig (SRTLA). Mindestens ein vorhandenes Netz muss angehakt sein.

**Verteilung** (Karte SRTLA, Bereich **Leitungssteuerung**, danach **Verteilung speichern**):

| Einstellung | Was passiert |
|---|---|
| **Beste Leitung bevorzugen** (empfohlen, stabiler) | Der Sender misst Laufzeit und Schwankung jeder Leitung. Er nutzt die guten und hält die übrigen als Reserve. |
| **Alle Leitungen gleichzeitig nutzen** | Auch langsamere Leitungen senden mit. Jede geeignete Leitung bekommt dabei **mindestens 10 Prozent** der Pakete, damit sie warm bleibt und beim Ausfall der besten nicht erst anlaufen muss. |

Den Mindestanteil stellst du nicht selbst ein. Er gilt automatisch bei "alle". Er ist für **einen klar besseren Weg plus schwächere Zusatzwege** abgestimmt. Sind alle Wege gleich unruhig, steigen die Neuübertragungen. Über drei Mobilfunkwege gab es bisher nur einen kurzen Test, Starlink neben 5G ist nicht geprüft.

**Ampel für die Sendewege** (Karte **Status**, Kasten Up- und Download):

- **Grün:** Der Weg trägt Pakete.
- **Gelb:** Verbunden, aber in Reserve (Laufzeit zu hoch oder unruhig).
- **Rot:** Nicht verbunden oder kein Netz.
- **Grau:** Keine Sendung.

**Tipp:** Kabel-Sendewege (USB-Tethering, Router per Ethernet) sind besser als ein Handy-Hotspot im selben 5-GHz-Kanal wie das Kamera-WLAN. Das ist eine Beobachtung vom 6. Oktober 2026 und nicht bewiesen.

## 3. WLAN und Hotspot

Karte **Verbindungen**, Bereich **WLAN-Verbindungen**:

1. **Netze suchen** klicken.
2. WLAN-Karte, Netzname und Passwort eintragen. Bei einem versteckten Netz setzt du das Häkchen "verstecktes Netz".
3. **Verbinden** klicken. **Trennen** beendet die Verbindung.
4. Willst du über dieses WLAN senden, setze in der Zeile der WLAN-Karte das Häkchen bei **Zum Senden verwenden**. Es wird sofort gespeichert.

Das Passwort gibt die Oberfläche nur an das Netzwerkprogramm der Box weiter. Dieses Projekt speichert es nicht selbst.

**Hotspot-Modus der Box:** Unter jeder WLAN-Karte schaltest du **Hotspot-Modus** auf An. Mit **Einstellen** legst du Name, Passwort (8 bis 63 Zeichen), 2,4 oder 5 GHz und Kanal fest. Die Box macht dann aus dem Stick ein eigenes WLAN, zum Beispiel für DJI-Kameras oder ein Handy. Der Stick ist dann nicht mehr mit einem anderen WLAN verbunden. Hast du die Oberfläche über dieses WLAN geöffnet, bricht sie beim Einschalten ab. Du erreichst sie danach über den Hotspot (Adresse meist `10.42.0.1`) oder ein anderes Netz.

*Geprüft mit einem echten WLAN-Stick und einer GoPro Hero 8: Die GoPro sendet über den Hotspot der Box (Adresse im Netz `10.42.0.x`) dauerhaft zur Box.*

**Über fremde WLANs sperren** (Karte Verbindungen, Bereich **Zugriff auf diese Oberfläche**): Die Oberfläche ist unverschlüsselt (HTTP). In einem fremden WLAN (Hotel, Handy-Hotspot) kann dort jeder mitlesen. Ist der Schalter an, antwortet die Box in solchen WLANs nicht. Erreichbar bleiben Ethernet, der eigene Hotspot der Box, USB und Tailscale (HTTPS). Sperrst du dich aus, schaltest du den Schalter über Ethernet oder Tailscale wieder aus. Der Schalter ist standardmäßig aus.

## 4. Bildaufbau

Karte **Bildaufbau**. Ohne gespeicherten Bildaufbau kann nicht gesendet werden.

1. Bei **Art** wählst du **Eine Kamera** oder **Bild-in-Bild** (Hauptbild plus bis zu drei kleine Bilder).
2. Wähle die Kamera für das **Hauptbild** und bei Bild-in-Bild die Kameras für **Kleines Bild 1** bis **3**.
3. Jedes kleine Bild hat eine **Ecke** und eine **Skalierung (%)**. Die kleinen Bilder kannst du in der **Vorschau** mit Maus oder Finger verschieben.
4. Bei **Ton von** wählst du die Kamera, deren Ton gesendet wird.
5. **Bildaufbau speichern** klicken. Läuft gerade eine Sendung, startet sie dabei kurz neu.

Unter jedem kleinen Bild findest du weitere Einstellungen für Aussehen (zum Beispiel Beschnitt und Rahmen). Die Vorschau zeigt sie sofort.

**Hauptbild wählen:** Bei Bild-in-Bild steht in der Live-Karte oben unter **Hauptbild** eine Auswahl der Kameras im Bild. Ein Klick auf eine andere Kamera tauscht sie mit dem Hauptbild. Läuft die Sendung, startet der Encoder dafür neu, und das Bild ist etwa 5 Sekunden unterbrochen. *Experimentell:* Mit "Hauptbild tauschen ohne Unterbrechung" (Hauptbild und erstes kleines Bild, oder alle Kameras) läuft der Tausch als harter Schnitt ohne Neustart. Dafür dekodiert die Box jede Kamera doppelt, das kostet mehr Rechenleistung. Das ist im Heimnetz mit vier DJI-Kameras geprüft, aber noch nicht über längere Zeit und unterwegs.

**Gleichlauf (Verzögerung):** Jede Kamera hat einen Regler von 0 bis 3000 ms (auch als Zahl eintragbar). So gleichst du aus, wenn eine Kamera dem Bild hinterherhinkt. Der Regler wirkt bei laufender Sendung ohne Neustart. So gehst du vor:

1. Öffne `tools/stopwatch.html` aus dem Paket auf einem Bildschirm.
2. Richte **alle Kameras** auf die laufende Zahl.
3. Sieh dir das gesendete Bild an (zum Beispiel beim Empfänger, oder in der Vorschau der Oberfläche): Zeigen die Kameras verschiedene Zahlen, ist die Kamera mit der **höchsten** Zahl die schnellste. Erhöhe ihre Verzögerung, bis alle dieselbe Zahl zeigen. Die Zehntel- und Hundertstelstellen zählen. (Dieser Ablauf ist aus der Funktionsweise abgeleitet und nicht ausführlich beschrieben; sag Bescheid, wenn er bei dir anders ist.)

**Automatisch umschalten:** Fällt eine Kamera aus, schaltet die Box nach etwa 5 Sekunden ohne Signal auf die übrigen um. Die Kamera kommt nach 60 Sekunden stabilem Signal zurück. Das siehst du an der Kamera-Ampel (grün: sendet und ist im Bild, gelb: sendet, ist aber noch nicht im Bild, rot: kein Signal, grau: unbekannt).

**Alle Kameras immer bereit (Beta, standardmäßig aus):** Der Schalter steht im Bildaufbau bei Bild-in-Bild. Kameras können dann jederzeit dazukommen, ausfallen und zurückkehren, ohne dass die Sendung neu startet. Fällt die Hauptkamera aus, übernimmt die nächste. Ohne Kamera sendet die Box Schwarz und Stille. Jede Kamera wird doppelt dekodiert, das braucht mehr Rechenleistung. Bisher wurde das einige Stunden mit vier Kameras getestet, aber noch nicht lange und nicht unterwegs. Fehlt die nötige Software, läuft die Sendung im normalen Modus weiter, und die Live-Karte nennt den Grund.

## 5. Live gehen und Stop

- **Live gehen:** Knopf **Live** in der Kopfleiste (auf dem Handy auch in der Fußleiste). Bei laufender Sendung steht dort **LIVE**, und der Knopf beendet sie. Zum Starten brauchst du einen gewählten Server und einen gespeicherten Bildaufbau. Gründe, warum es nicht startet, nennt die **Live-Karte**.
- **Hat sich etwas geändert**, zeigt die Live-Karte "Noch nicht übernommen" mit dem Knopf **Stream neu starten**. Erst danach gilt die Änderung.
- **Automatisch live nach dem Start:** In der Karte SRTLA setzt du das Häkchen bei **Beim Start der Box automatisch live gehen** (standardmäßig aus). Das gilt einmal pro Start. Sobald eine Kamera sendet, geht die Box live. Sie wartet dafür bis zu 10 Minuten. **Live beenden** bricht die Automatik für diesen Start ab. Das verbraucht Mobilfunkdaten.
- **Streammodus** (Knopf in der Kopfleiste): blendet Adressen, Namen und Protokolle aus, wenn der Bildschirm mitgefilmt wird. Der Browser merkt sich die Einstellung.

**Fußleiste am Handy** (Bildschirmbreite bis 620 Pixel, bei Bild-in-Bild): Dort stehen **Live/Stop**, ein Knopf je Kamera und der **Ton-Knopf** in einer Reihe. Das ⓘ erklärt die Bedienung:

| Knopf | Kurz drücken | Doppelt tippen | Lang drücken |
|---|---|---|---|
| Kamera | Zum Hauptbild machen | Kleines Bild aus- oder einblenden (Ton bleibt) | Kamera deaktivieren oder wieder aktivieren |
| Ton | Nächste Tonspur | – | Stumm oder wieder laut |

Eine deaktivierte Kamera ist nicht im Stream, und ihr Ton ist nicht nutzbar. Ein Wechsel des Hauptbilds kann den Encoder neu starten (siehe "Hauptbild wählen"). *Mit Testquellen geprüft, noch nicht mit echten Kameras.*

### Vorschau: das gesendete Bild ansehen

Die Karte **Vorschau** (oben, unter dem Live-Bereich; unter **Optionen** lässt sie sich wie die anderen Karten ausblenden und verschieben) zeigt das **fertig gemischte Bild so, wie es gesendet wird**, nicht einzelne Kameras. So siehst du, ob eine Kamera schief steht, die Drohne nicht dorthin zeigt, wo du es erwartest, oder ein kleines Bild an der falschen Stelle sitzt.

- **Start / Stop** sitzt in der Kopfzeile der Karte. Die Vorschau gibt es nur, **während die Box sendet**. Sie startet nie von selbst.
- **Im Heimnetz** sind es 30 Bilder pro Sekunde in 640 × 360, **außerhalb des Heimnetzes** (öffentliche Adresse, Tailscale) 5 Bilder pro Sekunde und ein kleineres Bild (480 Pixel breit), das spart Bandbreite der Box (etwa 0,8 statt 5 Mbit/s). Weniger Bilder sparen vor allem Verkehr; die Rechenzeit der Box sinkt dadurch nur etwa um die Hälfte (siehe unten), weil sie den ganzen Strom immer mitlesen und dekodieren muss. Ob du im Heimnetz bist, entscheidet die Box an der Adresse, von der die Anfrage kommt (private Adressen zählen als Heimnetz), nicht am Namen in der Adresszeile. Bei voller Bildrate sind es je nach Bildinhalt etwa 3 bis 7 Mbit/s zum Browser (ruhige Bilder weniger, detailreiche mehr). **Dieser Verkehr der Vorschau wird in der Anzeige des Uploads herausgerechnet** (Karte Status, Kopfzeile): Die Zahlen zeigen weiter nur die Sendung, ob die Vorschau läuft oder nicht. Bei einem Zugriff über einen Proxy auf der Box (Tailscale) wird er der Schnittstelle der Standardroute abgezogen; eine kleine Restabweichung (Wiederholungen, Verschlüsselung) ist möglich.
- **Ende:** nach 10 Minuten (dann steht dort **Weiter ansehen**), beim Zuklappen oder Ausblenden der Karte, beim Schließen der Seite oder wenn der Browser im Hintergrund liegt, und wenn die Sendung endet. Es laufen höchstens zwei Vorschauen zugleich.
- **Dauerhaft an (nur in diesem Browser):** Das Häkchen **„Mir ist bewusst, dass die Vorschau Rechenleistung kostet. Ich möchte sie in diesem Browser dauerhaft eingeschaltet lassen.“** hebt die Grenze von 10 Minuten auf. Die Vorschau startet dann in diesem Browser nach einem Seitenwechsel, nach dem Neuladen, nach einer Sendepause und wenn die Seite wieder sichtbar wird von selbst neu (es steht „Die Vorschau startet wieder, sobald die Box sendet“). Mit gesetztem Häkchen verschwindet das Feld, damit die Karte klein bleibt; das **⚙** in der Kopfzeile der Karte holt es zurück (zum Entfernen des Häkchens). Das Häkchen wird nur im Browser gespeichert, nicht auf der Box, und gilt nur dort, wo du es setzt. **Zuklappen oder Ausblenden der Karte und der Stop-Knopf beenden die Vorschau immer**, auch mit Häkchen, weil die Rechenleistung anderswo gebraucht wird.
- **Platz sparen:** Unter **Optionen → Menüpunkte anzeigen** blendet **Überschriften von Chat und Vorschau** die Kopfzeilen beider Karten aus; Start/Stop, ⚙ und ⓘ der Vorschau und das ⚙ des Chats bleiben als kleine Symbole oben rechts über dem Inhalt. Beide Karten sind dann immer aufgeklappt.
- **Was es kostet:** (gemessen auf der Box, 12 s je Einstellung, Dienst auf den kleinen Kernen) 30 Bilder pro Sekunde in 640 Pixeln: 69 % eines kleinen Kerns und 5,9 Mbit/s; 10 Bilder in 480: 47 % und 1,5 Mbit/s; 5 Bilder in 480: 41 % und 0,8 Mbit/s; 1 Bild in 480: 37 % und 0,2 Mbit/s. Nur Schlüsselbilder zu dekodieren oder den Strom unverändert weiterzureichen spart kaum Rechenzeit und braucht viel mehr Verkehr (rund 15 Mbit/s), und viele Browser können H.265 nicht abspielen. etwa die Hälfte eines der vier kleinen Prozessorkerne, solange sie läuft (die schnellen Kerne bleiben der Sendung); ohne Vorschau läuft nichts davon. Die Sendung bleibt unberührt.
- **Wie es funktioniert:** Die Box liest den Datenstrom mit, den der Encoder lokal an `srtla_send` schickt, und dekodiert ihn mit dem Hardware-Dekoder. Dafür braucht es **kein** zusätzliches Programm und keinen Eingriff in die Sendekette. Das Mitlesen macht ein eigener kleiner Dienst (`pipbox-preview`), der nur das Recht für rohe Netzwerkpakete hat und erst startet, wenn jemand die Vorschau öffnet; nach einer Minute ohne Zuschauer beendet er sich wieder. Der Webserver und der Browser bekommen **keine** zusätzlichen Rechte, der Webserver reicht den Strom nur durch. Das Bild kommt etwa 2 Sekunden nach dem Start (das erste Schlüsselbild wird abgewartet) und liegt kurz hinter dem echten Bild zurück.

## 6. Bitrate-Regler und Stabilität

Karte SRTLA, Bereich **Bitrate und Latenz**: Hier stellst du **Mindestbitrate** und **Höchstbitrate** in kbit/s sowie die **Latenz** in ms ein und klickst **Bitrate und Latenz speichern**. Die Änderung gilt nach einem Neustart der Sendung. Auch die Einstellungen des Empfängers (SRT-Latenz, Umordnungstoleranz) beeinflussen die Bitrate.

Drei kleine Verbesserungen sind eingebaut:

- **Toleranterer Regler im Encoder:** Das Original senkte die Bitrate schon bei kleinen Schwankungen der Laufzeit und erholte sich danach kaum. Jetzt darf die Laufzeit stärker schwanken, bevor die Bitrate sinkt, und sie kommt nach einer kurzen Überlast wieder hoch.
- **Stall-Wächter nur am Ausgang:** Setzt eine kleine Kamera kurz aus (bei DJI im WLAN normal), bleibt die Sendung bestehen. Nur ihr Bild verschwindet. Steht der Ausgang selbst still, startet der Encoder neu.
- **Latenzbewusster Sender:** Er misst Laufzeit und Schwankung je Leitung und verhindert den Bitrate-Einbruch bei Leitungen mit sehr verschiedener Laufzeit. Fällt ein Weg aus, bekommt er keine Pakete mehr, solange ein anderer bereitsteht. Das half in einem Test mit zwei Mobilfunkwegen auf einer Box.

## 7. Wenn etwas nicht geht

| Problem | Was du tun kannst |
|---|---|
| Live startet nicht ("Nicht gestartet") | Grund in der Live-Karte lesen. Prüfe, ob ein Server gewählt und der Bildaufbau gespeichert ist. |
| Änderung wirkt nicht | Steht "Noch nicht übernommen" da, klicke **Stream neu starten**. Sendewege wirken dagegen ohne Neustart. |
| Sendeweg ist rot | Nicht verbunden oder kein Netz. Prüfe Kabel, Router und WLAN. |
| Sendeweg ist gelb | Der Weg steht in Reserve (Laufzeit hoch oder unruhig). Bei "beste Leitung bevorzugen" ist das gewollt. |
| Bitrate bricht ein oder bleibt niedrig | Sendewege per Kabel oder auf anderem Band als das Kamera-WLAN nutzen. SRT-Latenz und Umordnungstoleranz am Empfänger prüfen. |
| Kamera fehlt im Bild | Ampel ansehen. Nach einem Ausfall kehrt sie erst nach 60 Sekunden stabilem Signal zurück. |
| Bild ist beim Hauptbild-Tausch kurz weg | Normal ohne "Tausch ohne Unterbrechung" (etwa 5 Sekunden). |
| Kamera hinkt dem Bild hinterher | Verzögerung im Bildaufbau erhöhen (siehe Gleichlauf). |
| Oberfläche weg nach Hotspot-Start | Über die Hotspot-Adresse (meist `10.42.0.1`) oder ein anderes Netz öffnen. |
| Oberfläche weg nach "Über fremde WLANs sperren" | Über Ethernet oder Tailscale öffnen und den Schalter ausschalten. |
| Stream-ID vergessen | Wird nie angezeigt. Beim Bearbeiten des Servers neu eintragen. |

## Was noch nicht geprüft ist

- Fußleiste mit echten Kameras.
- "Hauptbild tauschen ohne Unterbrechung": nur kurz im Heimnetz.
- "Alle Kameras immer bereit": noch kein langer Lauf, keine Fahrt.
- Mehrere Mobilfunkwege: nur ein kurzer Test, Starlink nicht.
- Langzeitbetrieb unterwegs im Freien (zu Hause: 47,5 Stunden am Stück ohne Absturz belegt).

Mehr dazu in `README.md` und `CHANGELOG.md`.
