# Twitch-Chat in der IRL4YOU BOX

Mit dem Bereich **Chat** liest und schreibst du im Twitch-Chat deines Kanals, direkt in der Oberfläche der Box
(Port 8780). Du kannst moderieren, Follows und Kanalpunkte als Karten sehen und dich bei leerem Kamera-Akku
im Chat warnen lassen. Alles ist freiwillig; ohne Anmeldung kannst du den Chat nur lesen.

**Stand:** Die Anmeldung (Streamer, Moderator, Zuschauer), Chat lesen und schreiben und die Moderation hat der Entwickler am 10. Oktober 2026 mit einem echten Konto getestet; das **Bot-Konto** schon am 8. Oktober. Die **Akku-Warnung** hat bei einem wirklich niedrigen Kamera-Akku funktioniert. **EventSub** (Follows, Kanalpunkte) ist bisher nur gegen nachgebaute Testserver geprüft, nicht gegen echtes Twitch. Wenn etwas nicht
klappt, sag es bitte.

## 1. Mit Twitch anmelden

Du gibst dabei **kein Passwort und keinen Token** in die Box ein. Die Anmeldung läuft wie am Fernseher mit einem Code.

1. Öffne in der Oberfläche den Bereich **Chat** und tippe auf **Mit Twitch anmelden**.
2. Die Box zeigt einen **Code**, einen **Link** (twitch.tv/activate) und einen **QR-Code**.
3. Öffne den Link (oder scanne den QR-Code mit dem Handy) und melde dich **auf twitch.tv** an.
4. Gib dort den Code ein, falls Twitch danach fragt, und bestätige die angezeigten Rechte.
5. In der Box steht kurz „Warte auf die Bestätigung bei Twitch …“, danach „Angemeldet als …“.
   Mit **Abbrechen** brichst du den Vorgang ab.

Die Anmeldung wird im Hintergrund erneuert. Wird sie **30 Tage lang nicht benutzt**, läuft sie ab
(„Anmeldung abgelaufen“); dann meldest du dich einfach neu an.

## 2. Chat lesen

- Der Chat zeigt den Kanal, mit dem du angemeldet bist. Lesen geht auch ohne Anmeldung.
- Du siehst Uhrzeit, farbige Namen und **Abzeichen** (Streamer, Mod, VIP, Abonnent).
- **Links** im Chat sind anklickbar und öffnen in einem **neuen Tab** (zum Beispiel ein Clip). Angezeigt wird der Rechnername der Adresse, lange Adressen sind gekürzt; der Mauszeiger zeigt die ganze. Nur `http` und `https` werden zu Links.
- **Emotes** von Twitch, **7TV, BetterTTV und FrankerFaceZ** erscheinen als Bild, auch die **persönlichen** Emotes der Zuschauer (die Liste, die jemand bei BTTV oder 7TV für sich eingerichtet hat; sie ist öffentlich, **eine Anmeldung ist nicht nötig**). Die Box fragt sie je Schreiber einmal ab. **BTTV-Pro-Emotes** (persönlich, in keiner Liste) kommen über BTTVs Live-Verbindung: Die Box hört dazu im eigenen Kanal mit (nur zuhören, ohne Anmeldung) und zeigt das Emote ab der nächsten Nachricht des Zuschauers; bei der **ersten** Nachricht eines Zuschauers erscheint das Emote deshalb erst nach ein bis zwei Sekunden. Ohne Verbindung zu diesen Diensten
  bleibt das Wort als Text stehen.
- **Ereigniskarten:** Sub, Resub, Geschenk-Abos (eine Sammelaktion als eine Karte), Raid, Cheer (Bits) und Ankündigungen
  erscheinen als farbige Karte; neue leuchten kurz auf. Dazu kommen Follow und Kanalpunkte, wenn du sie einschaltest
  (Abschnitt 5). Hast du hochgescrollt, erscheint „Neues Ereignis ↓“.
- Kopfzeile: links ein Verlauf der Senderate der letzten 30 Sekunden (grün gut, gelb mäßig, rot schlecht, grau keine
  Sendung), rechts bis zu vier Punkte, einer je Kamera.

**Ansicht anpassen:** Tippe oben rechts auf das **Zahnrad (⚙)**. Dort stellst du ein:
**Uhrzeit**, **Abzeichen**, **Emotes als Bild**, **Trennlinien**, **Signal bei Ereignissen** (zwei kurze Töne und
Vibration, standardmäßig aus) und die **Schrift** (klein, normal, groß). Die Auswahl gilt für dein Gerät.

## 3. Schreiben

Nach der Anmeldung erscheint unter dem Chat ein Eingabefeld.

1. Nachricht eintippen (bis 450 Zeichen), **Senden** tippen.
2. Hat Twitch die Nachricht verworfen (zum Beispiel wegen Spam- oder Tempofilter, nur Abonnenten, langsamer Modus),
   nennt die Box den Grund von Twitch in der Meldung.

Texte, die mit `/` oder `.` beginnen, werden nie als normale Nachricht gesendet. Erlaubt sind nur die
Moderationsbefehle aus Abschnitt 4.

## 4. Moderation

Die Moderation ist **aus**, bis du sie einschaltest. Dein Konto muss **Streamer oder Moderator** des Kanals sein.

1. Tippe auf das **Zahnrad (⚙)** und dann auf **Moderation einschalten**.
2. Twitch fragt einmal nach **neuen Rechten**. Bestätige sie wie bei der Anmeldung (Code und Link, Abschnitt 1).
   Es sind **alle Rechte auf einmal** (Löschen, Bann, Befehle wie VIP und Raid, dazu die Ereignisse aus Abschnitt 5).
   Danach steht hinter dem Zahnrad „Angemeldet als … · Moderation an“.
3. An den Nachrichten **anderer** Zuschauer erscheint ein **⋯**. Dahinter findest du: **Löschen**, **Alle löschen**
   (räumt alle Nachrichten der Person ab), **Timeout 10 Min**, **Timeout 1 Std** und **Bannen**.
4. Es gibt keine Rückfrage, dafür nach Timeout und Bann **Rückgängig** (kurz sichtbar). Bei „Alle löschen“ gibt es
   kein Rückgängig.
5. Im Eingabefeld gehen auch Befehle. Sie laufen über die Schnittstelle von Twitch und landen nie als Text im Chat:

   | Befehl | Wirkung |
   |---|---|
   | `/ban Name`, `/timeout Name [Sekunden]`, `/unban Name` | Bann, Timeout, Bann aufheben |
   | `/clear` | Chat leeren |
   | `/slow [Sekunden]`, `/slowoff` | Langsamer Modus (3 bis 120 Sekunden, Standard 30) |
   | `/followers [Minuten]`, `/followersoff` | nur Follower (Standard: alle Follower) |
   | `/subscribers`, `/subscribersoff` | nur Abonnenten |
   | `/emoteonly`, `/emoteonlyoff` | nur Emotes |
   | `/announce Text` | hervorgehobene Ankündigung |
   | `/vip Name`, `/unvip Name` | VIP geben und nehmen (nur Kanalinhaber) |
   | `/mod Name`, `/unmod Name` | Moderator ernennen und entfernen (nur Kanalinhaber) |
   | `/raid Name`, `/unraid` | Raid starten und abbrechen (nur Kanalinhaber) |
   | `/marker [Text]` | Marker im laufenden Stream setzen (nur Kanalinhaber, Stream muss live sein) |
   | `/title Text`, `/game Kategorie` | Titel und Kategorie ändern (nur Kanalinhaber) |

   Twitch lehnt manches ab, zum Beispiel VIP für jemanden, der schon VIP oder Moderator ist, oder wenn das VIP-Limit erreicht ist.
   Die Meldung steht dann unter dem Eingabefeld.

**Wichtig – wer mit welchem Konto schreibt und moderiert:** Auf der Box ist **ein** Twitch-Konto angemeldet, das des Streamers. **Wer der Streamer ist, legt das Feld „Kanal (Konto, auf dem gestreamt wird)“ fest**
(dasselbe Feld wie bei der Akku-Warnung). Du änderst es in den **Chat-Einstellungen** (Zahnrad). Ist es noch leer, steht es zuerst oben im Chat, damit du den Kanal eintragen kannst; danach verschwindet die Zeile. Es wird nie ein Name
vorbelegt. Nur dieses Konto kann Streamer werden: Meldet sich ein anderes Konto (zum Beispiel ein VIP-Konto) als Streamer an, lehnt die Box es
ab, auch wenn noch niemand angemeldet ist.
Die Box merkt sich, **in welchem Browser** sich der Streamer angemeldet hat: Der Browser erzeugt dabei einen Zufallsschlüssel
und behält ihn bei sich, die Box speichert nur einen Fingerabdruck davon.

- **Im Browser des Streamers** geht alles: Schreiben, Löschen, Timeout, Bann und alle Befehle, auch die „nur Kanalinhaber“
  (`/vip`, `/mod`, `/raid`, `/marker`, `/title`, `/game`). Ein zweiter Browser des Streamers (zum Beispiel das Handy):
  **Ich bin der Streamer** antippen und bei Twitch **dasselbe Konto** bestätigen (höchstens fünf Browser).
  **Abmelden** (Zahnrad) löscht alle Schlüssel.
- **In jedem anderen Browser** (zum Beispiel bei einem Helfer, der dir bei den Kameras hilft und den Link zur Box hat) kann man den Chat
  zunächst nur **lesen**. Schreiben und Moderieren sind aus. Die Einstellungen für Kameras und die Box darf er weiter benutzen.
  Wer mehr will, meldet sich **mit dem eigenen Twitch-Konto** an. Es gibt drei Wege, je nachdem, wer er ist:
  - **Als Zuschauer anmelden:** ganz normale Anmeldung wie bei einem Zuschauer. Er darf im Chat **schreiben** (als er selbst),
    sonst nichts. Bei Twitch werden dafür nur die Chat-Rechte abgefragt, keine zum Moderieren.
  - **Als Moderator anmelden:** zusätzlich die Moderatorenrechte (Löschen, Timeout, Bann, `/clear`, `/slow`, `/followers`,
    `/subscribers`, `/emoteonly`, `/announce`). Er darf nur, was er bei Twitch im Kanal als Moderator darf. Die Box fragt bei
    Twitch nach (mit dem Konto des Streamers, höchstens alle fünf Minuten), ob er im Kanal **wirklich Moderator** ist; wenn nicht,
    bleibt er Zuschauer. Kann die Box nicht nachfragen (zum Beispiel wenn der Chat nicht der eigene Kanal des Streamers ist),
    gelten die angemeldeten Rechte, und Twitch lehnt unerlaubte Aktionen selbst ab. Die Befehle des Kanalinhabers bleiben gesperrt.
    Wer sich zuerst als Zuschauer angemeldet hat, kann später **Moderation einschalten** (die Rechte kommen dazu).
  - **Ich bin der Streamer:** nur für den Streamer (zum Beispiel am Handy): Twitch muss **dasselbe Konto** wie das der Box bestätigen.
  Die Anmeldung jedes Helfers liegt getrennt von der des Streamers auf der Box (höchstens zwölf Browser), **Abmelden** (Zahnrad)
  löscht nur seine eigene.
- Ein Fremder, der sich als „Ich bin der Streamer“ ausgeben will, scheitert: Die Box übernimmt die Anmeldung nur, wenn Twitch
  **dasselbe Konto** wie das des Streamers bestätigt. Das Konto der Box ersetzt er damit nie.

Das Schreiben ist für alle zusammen auf eine Nachricht je Sekunde begrenzt.

**Ausschalten:** Zahnrad, **Moderation ausschalten**. Die ⋯ und die Befehle sind sofort weg; **Moderation einschalten**
geht danach ohne neue Bestätigung. Twitch erlaubt aber nicht, einem Zugang Rechte wieder wegzunehmen. Sie fallen
erst weg, wenn du dich **abmeldest und ohne Moderation neu anmeldest**.

## 5. Ereignisse einschalten (Follows und Kanalpunkte)

Follows und Kanalpunkte-Einlösungen kommen nicht aus dem Chat, sondern über EventSub von Twitch.

1. Zahnrad, **Ereignisse einschalten**.
2. Bestätige bei Twitch die **neuen Rechte** (Abschnitt 1). Danach steht dort „Ereignisse an“.
3. Es erscheinen Karten „Follow“ und „Kanalpunkte“ (mit Belohnung und Text des Zuschauers).

Das geht nur für deinen **eigenen** Kanal. Subs, Geschenk-Abos, Raids und Cheers brauchen diesen Schalter nicht.
Die Moderation bleibt, falls eingeschaltet, erhalten.

## 6. Akku-Warnung im Twitch-Chat (nur DJI)

Fällt der Akku einer per Bluetooth verbundenen **DJI-Kamera** unter die Schwelle, schreibt die Box eine Nachricht in
den Chat. Eine Kamera nur am HDMI-Kabel meldet keinen Akku; hier hilft der Schalter „Nur Akkustand lesen“ in der
Karte der DJI-Kamera. Das gilt auch für eine Kamera, die selbst per RTMP sendet (zum Beispiel die Osmo Pocket 3).

**Einrichten:** Karte **Kameras**, Untermenü **Akku-Warnung im Twitch-Chat (nur bei DJI)**.

1. **Einschalten** ankreuzen.
2. **Warnen bei (%)**: die Schwelle (Standard 10 %).
3. **Nachricht**: dein Text. `{Kamera}` wird durch den Kameranamen, `{Prozent}` durch den Akkustand ersetzt.
   Standard: „Akkustand niedrig, bitte Akku wechseln: {Kamera} ({Prozent} %)“.
4. **Nur während der Sendung** angekreuzt: Gewarnt wird nur, solange gesendet wird.
5. **Speichern**, dann **Testnachricht senden**. Sie lautet „Test: IRL4YOU BOX“. Der Knopf geht erst nach dem
   Speichern. Unter dem Menü steht die letzte Meldung mit Uhrzeit oder der Fehler.

Pro Kamera kommt eine Warnung, bis sie lädt oder ihr Akku mindestens 10 Prozentpunkte über der Schwelle liegt
(zum Beispiel nach dem Akkuwechsel). Schlägt das Senden fehl, versucht es die Box ein paar Mal erneut.

**Wer schreibt?**

- **Bot-Konto:** Mit **Bot-Konto anmelden** meldest du ein zweites Twitch-Konto per Code an. Wähle bei Twitch das
  Bot-Konto, am besten in einem privaten Browserfenster. Der Bot bekommt nur die Rechte zum Lesen und Schreiben im
  Chat, keine Moderation und keine Ereignisse. **Bot abmelden** entfernt ihn wieder.
- **Ohne Bot** schreibt dein **angemeldetes Hauptkonto**.
- **Ohne beides** gilt ein von Hand eingetragener Token (Recht `chat:edit`, wie bei NOALBS). Er muss zum Bot-Konto
  gehören. Einfacher ist die Anmeldung per Code.
- Ist der Chat **nur für Follower oder Abonnenten**, muss der Bot **Moderator oder VIP** sein.
- Der Chat in der Oberfläche (Lesen, Schreiben, Moderation) bleibt immer beim Hauptkonto, nie beim Bot.
- Ist die Anmeldung des Bots abgelaufen, schweigt die Meldung, bis du ihn neu anmeldest oder abmeldest.
  Sie schreibt nie heimlich mit dem Hauptkonto.
- Ist nur der Bot angemeldet, trägst du den **Kanal** selbst ein.

## 7. Der Chat nutzt die Sendewege der Box

Die Verbindung des Chats zu Twitch läuft über die **Sendewege** der Box (zum Beispiel Mobilfunk): zuerst über den
besten Weg, bei Fehler oder Stille über den nächsten, zuletzt über die normale Verbindung. Ein toter Weg fällt nach
etwa 100 Sekunden auf. Eine einzelne Chat-Verbindung wird nicht auf mehrere Wege verteilt, sie weicht nur aus.

## 8. Sicherheit

- Deine Twitch-Zugangsdaten bleiben **auf der Box** (Dateien mit Zugriff nur für den Dienst). Sie erscheinen nie in der
  Oberfläche, in Meldungen oder Protokollen.
- Die Box kennt dein Twitch-Passwort nie; du bestätigst alles auf twitch.tv selbst.
- In die Einstellungssicherung kommt die Akku-Warnung **ohne Token** und ohne Anmeldung.
- **Abmelden:** Zahnrad, **Abmelden**. Der Bot hat sein eigenes **Bot abmelden**.

## 9. Wenn etwas nicht geht

| Meldung oder Problem | Ursache und Lösung |
|---|---|
| „Kein Kanal in der Twitch-Karte eingetragen.“ | Es ist kein Kanal bekannt. Mit dem Hauptkonto anmelden oder den Kanal in der Akku-Warnung eintragen. |
| „Keine Verbindung zu Twitch.“ | Die Box hat kein Internet oder Twitch ist nicht erreichbar. Sendewege und Netz prüfen. |
| „Noch keine Nachrichten.“ | Verbunden, aber im Chat wurde noch nichts geschrieben. |
| Kein Eingabefeld | Du bist nicht angemeldet. Zahnrad oder **Mit Twitch anmelden**. |
| „Zuerst mit Twitch anmelden“ | Siehe Abschnitt 1. |
| „Anmeldung abgelaufen, bitte neu anmelden“ | Neu anmelden (Abschnitt 1). Das kann nach 30 Tagen ohne Nutzung passieren. |
| „Die Anmeldung wurde abgelehnt oder ist abgelaufen“ | Der Code galt nicht mehr oder du hast auf twitch.tv abgelehnt. Neu anfangen. |
| „Twitch hat die Nachricht nicht gesendet: …“ | Twitch hat sie verworfen. Der Grund steht dahinter (Spamfilter, nur Abonnenten, langsamer Modus …). |
| „Verbindung gestört, unklar ob es angekommen ist“ | Schau im Chat nach, ob die Nachricht da ist, bevor du sie erneut sendest. |
| „Moderation ist noch nicht eingeschaltet“ | Zahnrad, **Moderation einschalten** (Abschnitt 4). |
| „Dazu fehlt die Berechtigung (Moderator im Kanal?)“ | Das Konto ist weder Streamer noch Moderator des Kanals, oder die Rechte fehlen. |
| Keine Follow- oder Kanalpunkte-Karten | Zahnrad, **Ereignisse einschalten** und bestätigen. Gilt nur für den eigenen Kanal. |
| Akku-Warnung kommt nicht | Eingeschaltet und gespeichert? Kamera per Bluetooth verbunden? Bei „Nur während der Sendung“ muss gesendet werden. Mit **Testnachricht senden** prüfen. |
| Testnachricht-Knopf grau | Erst **Speichern**; außerdem muss ein Kanal und ein Konto (oder Token) bekannt sein. |
| Bot schreibt nicht | Bot neu anmelden („Anmeldung des Bots abgelaufen“). Bei Chat nur für Follower oder Abonnenten: Bot zum Moderator oder VIP machen. |
| Chat zeigt Emotes als Text | 7TV, BTTV oder FFZ nicht erreichbar, oder **Emotes als Bild** ist im Zahnrad aus. |
| Bildschirm geht am Handy aus | Der Schalter „Bildschirm anlassen“ (Glühbirne in der Kopfzeile) braucht https, zum Beispiel über den Fernzugriff mit Tailscale. |

---

Weitere Anleitungen und Themen: [irl4you.de](https://irl4you.de)
