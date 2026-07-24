#!/usr/bin/env python3
"""
Wohnungsmonitor – HOWOGE Crawler
Läuft als eigener Hintergrundprozess im Docker-Container, unabhängig von
crawler.py (kein gemeinsamer Code – ein Bug hier soll den Kleinanzeigen-
Crawler nicht mitreißen).
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ── Pfade ─────────────────────────────────────────────────────────────────────
DATA_DIR      = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE   = DATA_DIR / "howoge_config.json"
LISTINGS_FILE = DATA_DIR / "howoge_listings.json"
LOG_FILE      = DATA_DIR / "howoge_crawler.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Standardkonfiguration ──────────────────────────────────────────────────────
DEFAULT_CONFIG = {
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

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False))
        log.info(f"Standardkonfiguration erstellt: {CONFIG_FILE}")
    return json.loads(CONFIG_FILE.read_text())


def load_listings() -> list:
    if LISTINGS_FILE.exists():
        try:
            return json.loads(LISTINGS_FILE.read_text())
        except json.JSONDecodeError:
            return []
    return []


def save_listings(listings: list):
    LISTINGS_FILE.write_text(json.dumps(listings, indent=2, ensure_ascii=False))


def fetch_immo_objects(kiez: list, wbs: str) -> list:
    """Ruft den HOWOGE-immoList-Endpoint ab. Eine leere `kiez`-Liste bzw. ein
    leerer `wbs`-String bedeuten "kein Filter" (alle Bezirke / unabhängig von
    WBS-Status). `page`/`limit` gibt es beim Endpoint zwar, werden vom Server
    aber ignoriert – jede Anfrage liefert immer den kompletten aktuell
    passenden Bestand zurück (per curl verifiziert), daher werden sie hier
    gar nicht erst mitgeschickt.
    """
    params = {
        "type": "999",
        "tx_howrealestate_json_list[action]": "immoList",
    }
    if kiez:
        params["tx_howrealestate_json_list[kiez][]"] = kiez
    if wbs:
        params["tx_howrealestate_json_list[wbs]"] = wbs

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "de-DE,de;q=0.9",
    }
    resp = requests.get("https://www.howoge.de/", params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json().get("immoobjects", [])


def parse_immo_objects(raw_objects: list, search: dict) -> list:
    """Wandelt rohe immoList-Objekte in unser Listing-Schema um und filtert
    nach Zimmerzahl (min_rooms/max_rooms), da die HOWOGE-API dafür nur exakte
    Werte akzeptiert, keine Ranges.
    """
    min_rooms = search.get("min_rooms", 1)
    max_rooms = search.get("max_rooms", 99)
    results = []

    for obj in raw_objects:
        rooms = obj.get("rooms")
        if rooms is None or not (min_rooms <= rooms <= max_rooms):
            continue

        features = ", ".join(obj.get("features", []))
        notice = (obj.get("notice") or "").strip()
        description = f"{notice} – {features}" if notice and features else (notice or features)

        link = obj.get("link", "")
        image = obj.get("image", "")
        url = f"https://www.howoge.de{link}" if link.startswith("/") else link
        img = f"https://www.howoge.de{image}" if image.startswith("/") else image

        results.append({
            "id": f"howoge-{obj['uid']}",
            "title": obj.get("title", "–"),
            "price": f"{obj.get('rent', '')} €".strip(),
            "location": obj.get("district", ""),
            "description": description,
            "url": url,
            "image": img,
            "search_name": search["name"],
            "source": "howoge",
            "found_at": datetime.now().isoformat(),
            "is_new": True,
        })

    return results


def fetch_search(search: dict) -> list:
    name = search.get("name", "?")
    try:
        raw = fetch_immo_objects(search.get("kiez", []), search.get("wbs", ""))
        listings = parse_immo_objects(raw, search)
        log.info(f"[{name}] {len(listings)} Inserate gefunden")
        return listings
    except requests.exceptions.HTTPError as e:
        log.warning(f"[{name}] HTTP-Fehler: {e}")
    except requests.exceptions.ConnectionError:
        log.warning(f"[{name}] Verbindungsfehler – kein Internet?")
    except Exception as e:
        log.error(f"[{name}] Unerwarteter Fehler: {e}")
    return []


def merge_listings(existing: list, fresh: list) -> tuple[list, int]:
    """Fügt neue HOWOGE-Inserate zur bestehenden Liste hinzu. Gibt
    (merged, new_count) zurück."""
    existing_ids = {l["id"] for l in existing}
    new_count = 0

    for item in fresh:
        if item["id"] not in existing_ids:
            existing.insert(0, item)
            existing_ids.add(item["id"])
            new_count += 1
        else:
            for ex in existing:
                if ex["id"] == item["id"]:
                    ex["is_new"] = False
                    break

    return existing, new_count


def fetch_all_active_ids() -> set | None:
    """Ungefilterte Anfrage an den immoList-Endpoint, um alle aktuell aktiven
    HOWOGE-IDs zu ermitteln. Gibt bei einem Netzwerkfehler None zurück (statt
    zu werfen), damit der Aufrufer Housekeeping diesen Zyklus einfach
    überspringen kann, statt versehentlich alles als "gone" zu werten."""
    try:
        raw = fetch_immo_objects([], "")
        return {f"howoge-{obj['uid']}" for obj in raw if obj.get("uid") is not None}
    except requests.exceptions.RequestException as e:
        log.warning(f"[Housekeeping] Verbindungsfehler: {e}")
        return None
    except Exception as e:
        log.warning(f"[Housekeeping] Fehler: {e}")
        return None


def run_housekeeping(listings: list, active_ids: set) -> tuple[list, int, bool]:
    """Entfernt Anzeigen, die nicht mehr im aktuellen HOWOGE-Bestand
    auftauchen. Bricht sicherheitshalber ohne Löschung ab, wenn auffällig
    viele Anzeigen auf einmal fehlen (z. B. bei einem API-Ausfall), statt
    versehentlich fast die ganze Liste zu leeren.

    Rückgabe: (verbleibende Anzeigen, Anzahl entfernt, ob abgebrochen wurde)
    """
    ABORT_THRESHOLD = 0.3
    MIN_FOR_THRESHOLD = 5

    kept = [l for l in listings if l["id"] in active_ids]
    gone = [l for l in listings if l["id"] not in active_ids]

    total = len(listings)
    if total >= MIN_FOR_THRESHOLD and len(gone) / total > ABORT_THRESHOLD:
        log.warning(
            f"[Housekeeping] Abgebrochen: {len(gone)}/{total} Anzeigen als "
            f"gelöscht erkannt – das ist ungewöhnlich viel. Es wird nichts "
            f"gelöscht, nächster Versuch beim nächsten Zyklus."
        )
        return listings, 0, True

    for l in gone:
        log.info(f"[Housekeeping] Entfernt: {l.get('id')} – {l.get('title')}")

    return kept, len(gone), False


def next_occurrence(hour: int, minute: int = 0) -> datetime:
    """Nächster Zeitpunkt mit der angegebenen Uhrzeit (heute oder morgen)."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def sleep_until(hour: int, minute: int = 0):
    """Schläft bis zur nächsten Uhrzeit (heute oder morgen)."""
    target = next_occurrence(hour, minute)
    secs = (target - datetime.now()).total_seconds()
    log.info(f"Nachtruhe – nächster Check um {target.strftime('%H:%M Uhr')} ({int(secs // 3600)}h {int((secs % 3600) // 60)}min)")
    time.sleep(max(secs, 0))


def is_quiet_hours(quiet_start: int = 22, quiet_end: int = 6) -> bool:
    """True wenn aktuelle Stunde in der Ruhephase liegt."""
    h = datetime.now().hour
    if quiet_start > quiet_end:
        return h >= quiet_start or h < quiet_end
    return quiet_start <= h < quiet_end


def run_crawler():
    log.info("═" * 50)
    log.info("  HOWOGE-Crawler gestartet")
    log.info("═" * 50)

    while True:
        if is_quiet_hours(22, 6):
            sleep_until(6)
            continue

        config = load_config()
        listings = load_listings()

        total_new = 0
        for search in config.get("searches", []):
            fresh = fetch_search(search)
            listings, new_count = merge_listings(listings, fresh)
            total_new += new_count
            time.sleep(random.uniform(2, 6))

        active_ids = fetch_all_active_ids()
        removed = 0
        if active_ids is not None:
            listings, removed, aborted = run_housekeeping(listings, active_ids)

        save_listings(listings)

        if total_new > 0:
            log.info(f"✓ {total_new} neue HOWOGE-Inserate gespeichert!")
        else:
            log.info("✓ Keine neuen HOWOGE-Inserate.")
        if removed:
            log.info(f"✓ Housekeeping: {removed} Anzeige(n) entfernt")

        base = config.get("check_interval_seconds", 300)
        jitter = random.uniform(-0.3, 0.3)
        interval = int(base * (1 + jitter))
        log.info(f"Nächster Check in {interval // 60}m {interval % 60}s...")
        time.sleep(interval)


if __name__ == "__main__":
    run_crawler()
