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
      "name": "Berlin – 1-2 Zimmer",
      "city": "berlin",
      "city_id": "l3331",
      "min_rooms": 1,
      "max_rooms": 2,
      "min_price": 500,
      "max_price": 1400,
      "keywords": ""
    }
  ],
  "check_interval_seconds": 300,
  "max_listings_stored": 500
}
```

Nach Änderungen an `data/config.json` den Container neu starten:

```bash
docker compose restart
```

### Stadtcodes (city_id)

Die `city_id` steht in der URL auf kleinanzeigen.de wenn du dort suchst:

| Stadt | city_id |
|-------|---------|
| Berlin | `l3331` |
| Hamburg | `l1055` |
| München | `l1276` |
| Köln | `l1705` |
| Frankfurt | `l1439` |
| Stuttgart | `l1353` |

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
│   ├── config.json       # Konfiguration
│   ├── listings.json     # Gefundene Inserate
│   └── crawler.log       # Log-Datei
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
