# 🏠 Wohnungsmonitor

Wohnungs-Crawler (Ebay Kleinanzeigen + Gewobag + HOWOGE) mit Web-Dashboard für den Heimserver.  
Läuft als Docker-Container, Dashboard erreichbar im lokalen Netzwerk.

## Voraussetzungen

- Docker + Docker Compose installiert
- Git installiert

## Schnellstart

```bash
# 1. Repo klonen
git clone <deine-repo-url> wohnungsmonitor
cd wohnungsmonitor

# 2. Starten
docker compose up -d

# 3. Dashboard öffnen
# http://<server-ip>:5000
```

## Konfiguration

Beim ersten Start wird automatisch `data/config.json` erstellt:

```json
{
  "searches": [
    {
      "name": "Berlin – 3-5 Zimmer",
      "postal_code": "10437",
      "location_id": "3491",
      "category_id": "203",
      "radius_km": 5,
      "min_rooms": 3,
      "max_rooms": 5,
      "no_swap": true,
      "max_distance_km": 5
    }
  ],
  "check_interval_seconds": 300,
  "max_listings_stored": 500,
  "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
  "housekeeping_hour": 2
}
```

Nach Änderungen an `data/config.json` den Container neu starten:

```bash
docker compose restart
```

### Feldbeschreibung

| Feld | Typ | Beschreibung |
|---|---|---|
| `postal_code` | string | PLZ (nur für den URL-Aufbau) |
| `location_id` | string | Kleinanzeigen-interne Orts-ID |
| `category_id` | string | `"203"` = Wohnungen mieten |
| `radius_km` | int | Suchradius (5 = Minimum bei Kleinanzeigen) |
| `min_rooms` / `max_rooms` | int | Zimmeranzahl-Bereich |
| `no_swap` | bool | `true` = keine Tauschwohnungen |
| `max_distance_km` | int | Post-Filter: nur Inserate ≤ X km (aus dem Location-Text extrahiert) |
| `exclude_keywords` | string[] | Post-Filter: Inserate mit einem dieser Begriffe (Titel/Beschreibung, Groß-/Kleinschreibung egal) werden verworfen. Default: `wg-zimmer`, `wg zimmer`, `in wg`, `zwischenmiete`, `untermiete`, `befristet`, `befristung` – Kleinanzeigens Zimmer-/Tausch-Filter schließt WG-Zimmer und Zwischenmiete/Untermiete nicht zuverlässig aus, da sie zur selben Kategorie zählen und die Zimmerzahl der Gesamtwohnung angeben. Leere Liste `[]` deaktiviert den Filter. |
| `source` | string | Legacy-Feld, nur für ältere Configs relevant: `"gewobag"` innerhalb von `config.json` wurde früher genutzt, um eine Suche an die Gewobag-Logik statt Kleinanzeigen zu schicken. Neue Gewobag-Suchen gehören in `data/gewobag_config.json` (siehe unten) und brauchen dieses Feld nicht mehr. |

**`postal_code` + `location_id` ermitteln:** Auf kleinanzeigen.de → Wohnungen mieten → Ort/PLZ eingeben → Filter setzen. Aus der Browser-URL `c{category_id}l{location_id}r{radius_km}` ablesen (z.B. `c203l3491r5`).

Ältere Konfigurationen mit `city_id` + `min_price`/`max_price`/`keywords` werden weiterhin unterstützt (Fallback), liefern aber ohne `location_id` oft ungenaue Ergebnisse.

Der Crawler pausiert außerdem automatisch zwischen 22:00 und 06:00 Uhr (Nachtruhe) und variiert das Check-Intervall um ±30 %, um kein festes Abfragemuster zu erzeugen.

### Gewobag konfigurieren

Gewobag hat eine eigene Konfigurationsdatei, `data/gewobag_config.json` (analog zu HOWOGE, siehe unten), und einen eigenen Tab im Dashboard-Konfigurationsbereich. Sie wird beim ersten Start automatisch mit einem Beispiel-Suchauftrag angelegt und läuft weiterhin im selben Crawler-Prozess wie Kleinanzeigen (teilt sich `check_interval_seconds`, `max_listings_stored` und `housekeeping_hour` mit `config.json`).

Feld-Set pro Suche (bezirksbasiert statt radius-basiert wie bei Kleinanzeigen):

| Feld | Typ | Beschreibung |
|---|---|---|
| `bezirke` | string[] | Gewobag-interne Bezirks-Slugs, direkt aus der Filter-URL auf gewobag.de ablesbar (z.B. `pankow`, `pankow-prenzlauer-berg`, `friedrichshain-kreuzberg-friedrichshain`) |
| `zimmer_von` / `zimmer_bis` | int | Zimmeranzahl-Bereich |
| `gesamtmiete_von` / `gesamtmiete_bis` | int | Gesamtmiete-Bereich (€) |
| `gesamtflaeche_von` / `gesamtflaeche_bis` | int | Wohnfläche-Bereich (m²) |
| `objekttyp` | string[] | Default `["wohnung"]` |
| `exclude_keywords` | string[] | Wie bei Kleinanzeigen, Default hier `[]` (Gewobags "Wohnung"-Kategorie ist bereits kuratiert) |

Beispiel-Inhalt von `data/gewobag_config.json` (über den "GEWOBAG"-Tab im Dashboard editierbar):

```json
{
  "searches": [
    {
      "name": "Gewobag – Friedrichshain/Pankow",
      "bezirke": [
        "friedrichshain-kreuzberg-friedrichshain",
        "pankow",
        "pankow-prenzlauer-berg",
        "pankow-rosenthal",
        "pankow-weissensee"
      ],
      "zimmer_von": 3
    }
  ]
}
```

Danach wie gewohnt `docker compose restart` (oder den Container neu starten), damit der Crawler die geänderte Config einliest.

### HOWOGE konfigurieren

HOWOGE läuft als eigenständiger dritter Prozess (`app/howoge_crawler.py`, unabhängig von Kleinanzeigen/Gewobag) mit eigener Config-Datei `data/howoge_config.json`, eigenem "HOWOGE"-Tab im Dashboard und eigenem `check_interval_seconds` (unabhängig von `config.json`, da eigener Loop).

| Feld | Typ | Beschreibung |
|---|---|---|
| `kiez` | string[] | Berlin-Bezirksnamen (z.B. `"Friedrichshain-Kreuzberg"`, `"Mitte"`, `"Pankow"`), wird serverseitig an HOWOGEs API übergeben |
| `wbs` | string | `"ja"` / `"nein"`, serverseitig gefiltert |
| `min_rooms` / `max_rooms` | int | Zimmeranzahl-Bereich – clientseitiger Post-Filter, da HOWOGEs API nur exakte Zimmerzahlen akzeptiert, keine Bereiche |
| `check_interval_seconds` | int | Eigenes Poll-Intervall, unabhängig vom Kleinanzeigen/Gewobag-Intervall |

Beispiel-Inhalt von `data/howoge_config.json`:

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

HOWOGEs Housekeeping läuft anders als bei Kleinanzeigen/Gewobag: statt einer nächtlichen Einzelprüfung wird bei jedem Zyklus der komplette aktuelle Bestand ungefiltert abgerufen und damit abgeglichen, welche gespeicherten Anzeigen nicht mehr enthalten sind (kein separater Nachtlauf nötig). Auch hier bricht ein Abgleich ohne Löschung ab, wenn ungewöhnlich viele Anzeigen auf einmal fehlen.

### Housekeeping (nächtliche Aufräum-Prüfung)

Gilt für Kleinanzeigen und Gewobag (beide teilen sich `crawler.py`s Prozess). Einmal
pro Nacht, zur in `housekeeping_hour` konfigurierten Stunde (Standard: 2 Uhr,
lokale Containerzeit), prüft der Crawler innerhalb der Nachtruhe jede gespeicherte
Anzeige einzeln darauf, ob sie auf kleinanzeigen.de bzw. gewobag.de inzwischen
gelöscht, deaktiviert oder abgelaufen ist, und entfernt betroffene Einträge endgültig
aus `data/listings.json`. `housekeeping_hour` muss innerhalb des Nachtruhe-Fensters
(22–6 Uhr) liegen, sonst wird der Wert ignoriert (Warnung im Log). Ergebnisse
(entfernte Anzeigen, evtl. Abbruch bei ungewöhnlich vielen Treffern – z. B. bei
einer IP-Sperre) stehen in `data/crawler.log`. (HOWOGEs Housekeeping läuft anders,
siehe Abschnitt "HOWOGE konfigurieren" oben.)

## Updates einspielen

```bash
git pull
docker compose up -d --build
```

Die Daten in `data/` bleiben dabei erhalten (Docker Volume).

## Nützliche Befehle

```bash
# Status prüfen
docker compose ps

# Live-Logs
docker compose logs -f

# Nur Crawler-Output
docker compose logs -f | grep -v "werkzeug"

# Container stoppen
docker compose down

# Komplett neu bauen (z.B. nach requirements.txt Änderung)
docker compose up -d --build --force-recreate
```

## Projektstruktur

```
wohnungsmonitor/
├── app/
│   ├── crawler.py        # Kleinanzeigen-Crawler-Logik + Gewobag-Dispatch
│   ├── gewobag.py        # Gewobag-spezifische Fetch-/Parse-/Alive-Check-Logik
│   ├── dashboard.py      # Flask Web-Server
│   └── templates/
│       └── index.html    # Dashboard UI (Mobile + Desktop)
├── data/                 # Laufzeit-Daten (nicht im Git)
│   ├── config.json               # Kleinanzeigen-Konfiguration
│   ├── gewobag_config.json       # Gewobag-Konfiguration
│   ├── listings.json             # Gefundene Kleinanzeigen-/Gewobag-Inserate
│   ├── housekeeping_state.json   # Merkt sich den letzten Housekeeping-Lauf
│   └── crawler.log               # Log-Datei
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh
├── requirements.txt
└── README.md
```

## Port ändern

In `docker-compose.yml` die Port-Zeile anpassen:

```yaml
ports:
  - "8080:5000"  # Dashboard dann unter :8080 erreichbar
```
