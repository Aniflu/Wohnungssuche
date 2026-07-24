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


if __name__ == "__main__":
    pass
