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


if __name__ == "__main__":
    pass
