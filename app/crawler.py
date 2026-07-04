#!/usr/bin/env python3
"""
Wohnungsmonitor – Crawler
Läuft als Hintergrundprozess im Docker-Container.
Daten werden in /data (Volume) gespeichert.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Pfade ─────────────────────────────────────────────────────────────────────
DATA_DIR      = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE   = DATA_DIR / "config.json"
LISTINGS_FILE = DATA_DIR / "listings.json"
LOG_FILE      = DATA_DIR / "crawler.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Standardkonfiguration ──────────────────────────────────────────────────────
DEFAULT_CONFIG = {
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


def build_url(search: dict) -> str:
    base = "https://www.kleinanzeigen.de/s-wohnung-mieten"
    parts = [base]

    if search.get("keywords"):
        parts.append(search["keywords"].replace(" ", "-").lower())

    if search.get("city_id"):
        parts.append(search["city_id"])

    parts.append("anzeige:angebote")
    url = "/".join(parts)

    filters = []
    if search.get("min_price") or search.get("max_price"):
        filters.append(f"preis:{search.get('min_price', '')}:{search.get('max_price', '')}")
    if search.get("min_rooms") or search.get("max_rooms"):
        filters.append(f"zimmer:{search.get('min_rooms', '')}:{search.get('max_rooms', '')}")

    if filters:
        url += "/" + "/".join(filters)

    return url


def parse_listings(html: str, search_name: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    results = []

    articles = soup.select("article.aditem") or soup.select("li[data-adid]")

    for art in articles:
        try:
            ad_id = art.get("data-adid", "")
            if not ad_id:
                link_el = art.select_one("a[href*='/s-anzeige/']")
                if link_el:
                    parts = [p for p in link_el.get("href", "").split("/") if p.isdigit()]
                    ad_id = parts[-1] if parts else ""
            if not ad_id:
                continue

            title_el = art.select_one(".ellipsis, .aditem-title, h2.text-module-begin")
            price_el  = art.select_one(".aditem-main--middle--price-shipping--price, p.aditem-main--middle--price")
            loc_el    = art.select_one(".aditem-main--top--left, .aditem-location")
            desc_el   = art.select_one(".aditem-main--middle--description, p.aditem-main--middle--description")
            link_el   = art.select_one("a[href*='/s-anzeige/']") or art.select_one("a.ellipsis")

            href = link_el.get("href", "") if link_el else ""
            url  = f"https://www.kleinanzeigen.de{href}" if href.startswith("/") else href

            results.append({
                "id":          str(ad_id),
                "title":       title_el.get_text(strip=True) if title_el else "–",
                "price":       price_el.get_text(strip=True) if price_el else "",
                "location":    loc_el.get_text(strip=True)   if loc_el   else "",
                "description": desc_el.get_text(strip=True)  if desc_el  else "",
                "url":         url,
                "search_name": search_name,
                "found_at":    datetime.now().isoformat(),
                "is_new":      True,
            })
        except Exception as e:
            log.debug(f"Parse-Fehler: {e}")

    return results


def fetch_search(search: dict, session: requests.Session) -> list:
    url = build_url(search)
    log.info(f"[{search['name']}] GET {url}")

    headers = {
        "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
        "DNT":             "1",
        "Connection":      "keep-alive",
    }
    try:
        resp = session.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        found = parse_listings(resp.text, search["name"])
        log.info(f"[{search['name']}] {len(found)} Inserate geparst")
        return found
    except requests.exceptions.HTTPError as e:
        log.warning(f"[{search['name']}] HTTP {e}")
    except requests.exceptions.ConnectionError:
        log.warning(f"[{search['name']}] Verbindungsfehler")
    except Exception as e:
        log.error(f"[{search['name']}] Fehler: {e}")
    return []


def merge(existing: list, fresh: list, max_store: int) -> tuple[list, int]:
    seen_ids = {l["id"] for l in existing}
    new_count = 0
    for item in fresh:
        if item["id"] not in seen_ids:
            existing.insert(0, item)
            seen_ids.add(item["id"])
            new_count += 1
        else:
            for ex in existing:
                if ex["id"] == item["id"]:
                    ex["is_new"] = False
                    break
    return existing[:max_store], new_count


def run():
    log.info("═" * 48)
    log.info("  Wohnungsmonitor Crawler gestartet")
    log.info("═" * 48)

    session = requests.Session()

    while True:
        config   = load_config()
        listings = load_listings()
        total_new = 0

        for search in config.get("searches", []):
            fresh = fetch_search(search, session)
            listings, n = merge(listings, fresh, config.get("max_listings_stored", 500))
            total_new += n
            time.sleep(3)

        save_listings(listings)
        log.info(f"✓ {total_new} neue Inserate" if total_new else "✓ Keine neuen Inserate")

        interval = config.get("check_interval_seconds", 300)
        log.info(f"Nächster Check in {interval // 60} Min.")
        time.sleep(interval)


if __name__ == "__main__":
    run()
