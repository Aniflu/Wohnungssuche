# 🏠 Wohnungsmonitor

Ebay Kleinanzeigen Crawler mit Web-Dashboard für den Heimserver.  
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

**`postal_code` + `location_id` ermitteln:** Auf kleinanzeigen.de → Wohnungen mieten → Ort/PLZ eingeben → Filter setzen. Aus der Browser-URL `c{category_id}l{location_id}r{radius_km}` ablesen (z.B. `c203l3491r5`).

Ältere Konfigurationen mit `city_id` + `min_price`/`max_price`/`keywords` werden weiterhin unterstützt (Fallback), liefern aber ohne `location_id` oft ungenaue Ergebnisse.

Der Crawler pausiert außerdem automatisch zwischen 22:00 und 06:00 Uhr (Nachtruhe) und variiert das Check-Intervall um ±30 %, um kein festes Abfragemuster zu erzeugen.

### Housekeeping (nächtliche Aufräum-Prüfung)

Einmal pro Nacht, zur in `housekeeping_hour` konfigurierten Stunde (Standard: 2 Uhr,
lokale Containerzeit), prüft der Crawler innerhalb der Nachtruhe jede gespeicherte
Anzeige einzeln darauf, ob sie auf kleinanzeigen.de inzwischen gelöscht, deaktiviert
oder abgelaufen ist, und entfernt betroffene Einträge endgültig aus
`data/listings.json`. `housekeeping_hour` muss innerhalb des Nachtruhe-Fensters
(22–6 Uhr) liegen, sonst wird der Wert ignoriert (Warnung im Log). Ergebnisse
(entfernte Anzeigen, evtl. Abbruch bei ungewöhnlich vielen Treffern – z. B. bei
einer IP-Sperre) stehen in `data/crawler.log`.

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
│   ├── crawler.py        # Crawler-Logik
│   ├── dashboard.py      # Flask Web-Server
│   └── templates/
│       └── index.html    # Dashboard UI (Mobile + Desktop)
├── data/                 # Laufzeit-Daten (nicht im Git)
│   ├── config.json               # Konfiguration
│   ├── listings.json             # Gefundene Inserate
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
