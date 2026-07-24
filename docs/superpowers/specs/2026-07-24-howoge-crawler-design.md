# HOWOGE-Crawler – Design

## Kontext

Der bestehende Wohnungsmonitor crawlt bisher nur kleinanzeigen.de. Zusätzlich soll
HOWOGE (`www.howoge.de`), eine Berliner städtische Wohnungsbaugesellschaft,
gecrawlt werden, damit deren Angebote ebenfalls im bestehenden Dashboard
erscheinen.

Ursprünglich angefragte Quelle war immowelt.de, die aber hinter DataDome sitzt:
Selbst ein per Playwright gesteuerter, mit `playwright-stealth` präparierter
Headless-Browser bekam denselben 403-Block wie ein einfacher `curl`-Request
(identische Antwort, 1733 Bytes) – ein Hinweis auf IP-Reputations-basiertes statt
JS-Fingerprint-basiertes Blocking, das ohne kostenpflichtigen
Residential-Proxy-Dienst nicht zu umgehen ist. immowelt.de wird daher nicht
integriert (weder eigene noch Drittanbieter-API sind für den Anwendungsfall
"alle passenden Angebote finden" geeignet – die offizielle immowelt-API ist nur
für Anbieter gedacht, die eigene Inserate einspeisen).

HOWOGE dagegen hat keinen Bot-Schutz: Die Suchseite lädt Ergebnisse per AJAX von
einem JSON-Endpoint (`GET /?type=999&tx_howrealestate_json_list[action]=immoList&...`),
der ohne Header-Tricks sauberes JSON liefert.

## Technische Eckdaten zum HOWOGE-Endpoint (verifiziert per curl)

- URL: `https://www.howoge.de/?type=999&tx_howrealestate_json_list[action]=immoList`
- Query-Parameter:
  - `kiez[]` – Berliner Bezirk (z. B. `Friedrichshain-Kreuzberg`, `Mitte`,
    `Pankow`), mehrfach angebbar. **Wird server-seitig korrekt gefiltert**
    (verifiziert: kombinierte Anfrage mit 3 Bezirken + `rooms=3` + `wbs=no`
    lieferte exakt 1 passenden Treffer).
  - `rooms` – **exakter Wert, keine Range** (z. B. `3` = genau 3 Zimmer, nicht
    "3+"). Serverseitig korrekt gefiltert.
  - `wbs` – `yes` / `no` / leer (WBS-Erfordernis). Serverseitig korrekt gefiltert.
  - `page`, `limit` – **werden vom Server ignoriert**: jede Anfrage liefert
    unabhängig von `limit`/`page` immer den kompletten aktuell passenden
    Bestand zurück (verifiziert: `limit=5`, `limit=12`, `page=1` und `page=2`
    liefern identische, vollständige Ergebnismengen).
- Antwortformat: `{"immocount": int, "teasercount": int, "immoobjects": [...],
  "projectteaser": [...], "badges": [...]}`. `immoobjects` enthält je Anzeige:
  `uid`, `title` (Adresse), `image`, `district` (Kiez-Name, feiner als der
  Bezirks-Filter), `rent`, `area`, `rooms`, `wbs` (`"ja"`/`"nein"`), `features`
  (Liste), `coordinates` ({lat, lng}), `link` (relativer Pfad zur
  Detailseite), `favorite`, `notice`.
- Kein Bot-Schutz-Header (kein DataDome/Akamai o. ä.), Standard-TYPO3-Setup.

## Architektur

Dritter unabhängiger Hintergrundprozess `app/howoge_crawler.py`, analog zu
`app/crawler.py`, aber komplett eigenständiger Code (keine gemeinsamen
Imports zwischen den beiden Crawlern) – ein Bug im einen Crawler soll den
anderen nicht mitreißen.

Eigene Datendateien in `/data`:
- `howoge_config.json` – Suchen + Intervall, analog zu `config.json`
- `howoge_listings.json` – gefundene HOWOGE-Anzeigen
- `howoge_crawler.log` – eigenes Log-File

### Config-Schema (`howoge_config.json`)

```json
{
  "searches": [
    {
      "name": "Berlin – 3 Zimmer",
      "kiez": ["Friedrichshain-Kreuzberg", "Mitte", "Pankow"],
      "min_rooms": 3,
      "max_rooms": 6,
      "wbs": "nein"
    }
  ],
  "check_interval_seconds": 300
}
```

- `kiez` und `wbs` werden 1:1 als Query-Parameter an den HOWOGE-Endpoint
  durchgereicht (serverseitig gefiltert, siehe oben).
- `rooms` wird **nicht** an den Server übergeben (da nur exakte Werte
  unterstützt werden); stattdessen wird ungefiltert nach Zimmerzahl geholt und
  `min_rooms`/`max_rooms` clientseitig gefiltert – analog zum bestehenden
  `max_distance_km`-Muster in `crawler.py`.

### Crawl-Zyklus

Pro konfigurierter Suche: ein `requests.get()` auf den immoList-Endpoint mit
`kiez[]` + `wbs` als Query-Parameter (kein `rooms`), Response-JSON parsen,
Objekte auf `min_rooms <= rooms <= max_rooms` clientseitig filtern, in eigenes
Listing-Schema übersetzen:

```json
{
  "id": "howoge-<uid>",
  "title": "<title>",
  "price": "<rent> €",
  "location": "<district>",
  "description": "<notice> – <features, kommasepariert>",
  "url": "https://www.howoge.de<link>",
  "image": "https://www.howoge.de<image>",
  "search_name": "<search.name>",
  "source": "howoge",
  "found_at": "<ISO-Zeitstempel>",
  "is_new": true
}
```

`id`-Präfix `howoge-` verhindert Kollisionen mit den rein numerischen
Kleinanzeigen-IDs, sobald beide Listen im Dashboard zusammengeführt werden.

Merge-Logik (neue Anzeigen einfügen, bekannte als "nicht mehr neu" markieren)
wird eigenständig in `howoge_crawler.py` nachgebaut (kleine, unabhängige
Kopie der Logik aus `crawler.py`, kein Shared-Import).

### Nachtruhe & Intervall

Identisches Verhalten wie beim Kleinanzeigen-Crawler, aber als eigener,
unabhängiger Sleep-Zyklus (kein gemeinsamer Code):
- Pause zwischen 22:00 und 06:00 Uhr (`is_quiet_hours`/`sleep_until`-Äquivalent).
- Intervall zwischen den Zyklen: `check_interval_seconds` ± 30 % Jitter, um
  Anfragemuster zu vermeiden (obwohl HOWOGE keinen Bot-Schutz zeigt, aus
  Konsistenz-/Höflichkeitsgründen beibehalten).

### Housekeeping

Vereinfacht gegenüber Kleinanzeigen: Da eine ungefilterte Anfrage an den
immoList-Endpoint bereits den kompletten aktuellen Bestand liefert, reicht ein
einziger zusätzlicher ungefilterter Request pro regulärem Zyklus (kein
separater nächtlicher Housekeeping-Lauf nötig). Abgleich: gespeicherte
`howoge-<uid>` gegen aktuell gelieferte UIDs; nicht mehr vorhandene werden
entfernt. Gleiches Sicherheitsnetz wie bei Kleinanzeigen: Abbruch ohne
Löschung, falls auffällig viele (> 30 %) auf einmal fehlen (Schutz vor
Falsch-Alarm vom Server, z. B. bei einem API-Ausfall).

## Dashboard-Integration (`dashboard.py` + `index.html`)

- `dashboard.py` liest zusätzlich `howoge_listings.json` und merged beide
  Listen für `/api/listings` und `/api/stats`.
- Jeder Listing-Eintrag bekommt implizit ein `source`-Feld
  (`"kleinanzeigen"` oder `"howoge"`); alte Kleinanzeigen-Einträge ohne dieses
  Feld gelten als `"kleinanzeigen"`.
- `/api/delete/<id>` und `/api/mark_seen` erkennen anhand des `howoge-`-Präfix,
  welche Datei sie anfassen müssen.
- `/api/config` und `/api/log` bekommen ein optionales `?source=howoge`, um
  wahlweise die HOWOGE- oder die Kleinanzeigen-Config/-Log zu lesen/schreiben.
- Frontend: Karten zeigen ein kleines Quellen-Badge (HOWOGE vs.
  Kleinanzeigen). Der Konfigurations-Screen bekommt einen zweiten
  Textbereich für `howoge_config.json` mit eigenem Speichern-Button.

## `docker-entrypoint.sh`

Dritter Prozess (`howoge_crawler.py`) wird zusätzlich gestartet und dessen PID
in die bestehende Watchdog-Logik aufgenommen: Stirbt einer der drei Prozesse,
stoppt der Container (Exit-Code 1), damit `restart: unless-stopped` alle drei
sauber neu startet.

## Out of Scope

- immowelt.de (siehe Kontext oben – technisch nicht sinnvoll ohne bezahlten
  Proxy-Dienst).
- Preis-/Flächen-Filter für HOWOGE (der Endpoint bietet dafür keine
  Server-Parameter; könnte bei Bedarf später clientseitig ergänzt werden,
  analog zu `min_rooms`/`max_rooms`).
