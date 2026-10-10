# Fernzugriff auf die IRL4YOU BOX (Tailscale)

Mit dem Fernzugriff erreichen Sie die Oberfläche der Box von unterwegs, **ohne** etwas am Router freizugeben und **ohne**
dass die Box öffentlich im Internet steht. Das ist freiwillig: Ohne Fernzugriff funktioniert alles wie bisher im lokalen Netz.

**So funktioniert es:** Tailscale baut ein **privates Netz** nur zwischen Ihren eigenen Geräten (Box, Handy, Rechner). Nur
Geräte, die in Ihrem Tailscale-Konto angemeldet sind, kommen an die Box. Die Anmeldung mit dem BELABOX-Passwort bleibt davor.

## Was Sie brauchen

- Ein **kostenloses Tailscale-Konto** (tailscale.com). Die Anmeldung läuft über ein bestehendes Konto: GitHub, Google,
  Microsoft oder Apple. Ein eigenes Tailscale-Passwort gibt es nicht.
- Die **Tailscale-App** auf jedem Gerät, das zugreifen soll (Handy, Rechner). In der Oberfläche unter „Fernzugriff“
  gibt es dafür Knöpfe zu den offiziellen Stores.
- Die Box braucht Internet (über LAN oder Router).

## Einrichten (etwa 10 Minuten)

1. **Konto anlegen:** Auf tailscale.com „Get started“ wählen und sich mit GitHub, Google, Microsoft oder Apple anmelden.
   Danach fragt Tailscale, ein erstes Gerät hinzuzufügen: **überspringen** (die Box verbinden Sie im nächsten Schritt).
   Eine kurze Umfrage von Tailscale ist freiwillig.
2. **Box verbinden:** In der Oberfläche die Karte **„Fernzugriff (Tailscale)“** öffnen.
   - **„Ja, einrichten“** installiert Tailscale auf der Box (offizielle Paketquelle, ein Programm).
   - **„Verbinden“** zeigt einen **Anmeldelink**. Öffnen, mit dem **gleichen Konto** anmelden und **„Connect“** klicken.
3. **Freigeben:** **„Oberfläche im privaten Netz freigeben“** klicken. Beim ersten Mal verlangt Tailscale, **HTTPS** für Ihr Netz
   einzuschalten: den angezeigten Link öffnen, **„Tailscale Funnel“ ausgeschaltet lassen**, **„Enable HTTPS“** klicken und danach
   erneut freigeben. Die Karte zeigt jetzt die **Adresse im privaten Netz**, z. B. `https://irl4you-box.xxxx.ts.net/`.
4. **Handy und Rechner:** Die Tailscale-App installieren, mit **demselben Konto** anmelden und die **Verbindung einschalten**.
5. **Zugriff:** Die Adresse aus Schritt 3 im Browser öffnen und mit dem BELABOX-Passwort anmelden.

## Wenn etwas nicht geht

| Problem | Ursache und Lösung |
|---|---|
| „Adresse wird nicht gefunden“ | Das Gerät ist **nicht im Tailscale-Netz**. App öffnen, mit demselben Konto anmelden, Verbindung einschalten. Die Karte zeigt unter „Weitere Geräte in Ihrem Netz“, wer angemeldet ist. |
| Freigabe verlangt „Serve“/„HTTPS“ | Den Link in der Karte öffnen, **Funnel aus**, **Enable HTTPS** klicken, dann erneut freigeben. |
| Anmeldelink abgelaufen | In der Karte „Neuen Anmeldelink holen“. |
| Verbunden, aber keine Seite | In der Karte prüfen, ob „freigegeben“ dasteht. Sonst „Oberfläche im privaten Netz freigeben“. |
| Box zeigt „getrennt“ | „Verbinden“ klicken (keine neue Anmeldung nötig). |
| Neue oder neu geflashte Speicherkarte | Tailscale (Anmeldung und Freigaben) ist **nicht in der Sicherung** der Einstellungen und kommt beim Einspielen nicht mit. Die Box ist für Tailscale ein **neues Gerät**: Einrichtung wie oben wiederholen („Verbinden“, Link öffnen, anmelden), danach die Freigabe wieder einschalten. Das alte Gerät der früheren Karte in der Tailscale-Verwaltung (login.tailscale.com, Maschinen) entfernen, sonst stehen zwei Boxen in der Liste, und der neue Gerätename bekommt eine Zahl angehängt. Nach dem Einspielen einer Sicherung erinnert der Hinweis „Nach dem Einspielen noch zu erledigen“ daran. |

## Sicherheit

- **Funnel (öffentlich im Internet) ist standardmäßig aus und wird nie von selbst eingeschaltet.** Er macht die Oberfläche für **jeden**
  im Internet erreichbar, der die Adresse kennt, auch ohne Tailscale. Nur wer ihn ausdrücklich braucht, schaltet ihn ein (siehe unten);
  die Karte warnt rot, solange er an ist.
- **HTTPS-Zertifikate:** Damit steht der **Gerätename** der Box (z. B. `irl4you-box.xxxx.ts.net`) in einem öffentlichen
  Zertifikatsverzeichnis. Nur der Name, keine Inhalte, kein Zugriff. Er lässt sich dort nicht löschen.
- Schützen Sie Ihr **Tailscale-Konto** (Zwei-Faktor beim GitHub/Google-Konto), denn wer sich dort anmelden kann, kann Geräte
  hinzufügen. Nicht mehr benötigte Geräte in der Tailscale-Verwaltung (login.tailscale.com/admin) löschen.
- Nutzen Sie ein **starkes BELABOX-Passwort**.

## Öffentlich im Internet erreichbar machen (Funnel, optional)

Normalerweise reicht der private Zugriff: Jedes Gerät mit der Tailscale-App in Ihrem Konto (Handy, Rechner) erreicht die Box von überall.
Nur wenn jemand **ohne Tailscale-App** zugreifen soll, gibt es **„Öffentlich im Internet freigeben (Funnel) …“** in der Karte (sichtbar,
wenn die Box mit Tailscale verbunden ist).

- Nach einer Warnung und Ihrer Bestätigung ist die Oberfläche unter der Adresse der Box (`https://<Gerätename>.<netz>.ts.net/`) für **jeden**
  im Internet erreichbar. Geschützt ist sie dann **nur durch das BELABOX-Passwort**. Verwenden Sie dafür ein **langes, starkes Passwort**.
- Der Gerätename steht in öffentlichen Zertifikatslisten; Suchprogramme finden die Seite und probieren die Anmeldung aus. Wiederholte
  falsche Anmeldungen sperren den jeweiligen Absender für 5 Minuten.
- Die Freigabe hat **keine Zeitgrenze**: Sie bleibt an, auch nach einem Neustart der Box, bis Sie sie beenden. Die Karte zeigt eine rote
  Warnung, solange sie an ist. **„Öffentliche Freigabe beenden“** schaltet sie sofort ab; die Freigabe im privaten Netz bleibt.
- Tailscale muss Funnel für Ihr Netz **einmal erlauben**: Verlangt es das, zeigt die Karte einen Link zur Tailscale-Verwaltung.
  Danach erneut auf den Knopf klicken.
- Beim Beenden setzt Tailscale **alle** Freigaben der Box zurück (`tailscale funnel reset`) und die Box stellt nur die der Oberfläche
  im privaten Netz wieder her. Eigene, andere Freigaben auf der Box gingen dabei verloren.

## Abschalten und entfernen

- **Freigabe beenden:** Die Oberfläche ist nicht mehr über Tailscale erreichbar, die Box bleibt im Netz.
- **Verbindung trennen:** Die Box verlässt das Tailscale-Netz vorübergehend; „Verbinden“ stellt es ohne neue Anmeldung wieder her.
- **Vom Konto abmelden:** Die Box wird aus Ihrem Tailscale-Konto abgemeldet.
- **Ganz entfernen** (per SSH): `sudo apt-get remove tailscale` und `sudo rm /etc/apt/sources.list.d/tailscale.list`.

## Technik (für Interessierte)

Der Dienst der Oberfläche läuft ohne Root-Rechte und darf Tailscale nur lesen. Verändert wird Tailscale von einem getrennten
Root-Helfer (`pipbox-remote`), der nur feste Stichworte annimmt (installieren, verbinden, trennen, freigeben, Freigabe beenden,
abmelden) und nie Adressen oder Befehle aus der Oberfläche ausführt. Die Oberfläche wird mit `tailscale serve` (HTTPS, nur im
privaten Netz) auf `127.0.0.1:8780` weitergeleitet.

---

Weitere Anleitungen und Themen: [irl4you.de](https://irl4you.de)
