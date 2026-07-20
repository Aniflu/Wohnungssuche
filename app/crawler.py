#!/usr/bin/env python3
"""
Wohnungsmonitor – Crawler
Läuft als Hintergrundprozess im Docker-Container.
Daten werden in /data (Volume) gespeichert.
"""

import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Pfade ─────────────────────────────────────────────────────────────────────
DATA_DIR       = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE    = DATA_DIR / "config.json"
LISTINGS_FILE  = DATA_DIR / "listings.json"
LOG_FILE       = DATA_DIR / "crawler.log"
HOUSEKEEPING_STATE_FILE = DATA_DIR / "housekeeping_state.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Standardkonfiguration ──────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "searches": [
        {
            "name": "Berlin – 3-5 Zimmer",
            "postal_code": "10437",
            "location_id": "3491",   # Kleinanzeigen interne Orts-ID (aus der Such-URL ablesen)
            "category_id": "203",    # Wohnungen mieten
            "radius_km": 5,
            "min_rooms": 3,
            "max_rooms": 5,
            "no_swap": True,
            "max_distance_km": 5
        }
    ],
    "check_interval_seconds": 300,
    "max_listings_stored": 500,
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "housekeeping_hour": 2
}

# Kleinanzeigen filtert Zimmeranzahl/Tausch nur lose: WG-Zimmer (Vermietung
# nur eines Raums) und Zwischenmiete/Untermiete (befristete Vermietung)
# tauchen trotzdem in den Ergebnissen auf, weil sie zur Kategorie
# "Wohnung mieten" gehören und die Zimmerzahl der Gesamtwohnung angeben.
# Wird daher zusätzlich per Stichwort auf Titel+Beschreibung gefiltert.
DEFAULT_EXCLUDE_KEYWORDS = [
    "wg-zimmer", "wg zimmer", "in wg",
    "zwischenmiete", "untermiete", "befristet", "befristung",
]

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


def load_housekeeping_state() -> dict:
    if HOUSEKEEPING_STATE_FILE.exists():
        try:
            return json.loads(HOUSEKEEPING_STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_housekeeping_state(state: dict):
    HOUSEKEEPING_STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def build_url(search: dict) -> str:
    """Baut die Kleinanzeigen-Such-URL zusammen.
    Unterstützt zwei Formate:
    - Neu (bevorzugt): postal_code + location_id + category_id + radius_km
      → /s-wohnung-mieten/{plz}/c{cat}l{loc}r{radius}+wohnung_mieten.zimmer_d:X%2CY
    - Alt: city_id
      → /s-wohnung-mieten/{city_id}/anzeige:angebote/preis:X:Y/zimmer:X:Y
    """
    base = "https://www.kleinanzeigen.de/s-wohnung-mieten"

    if search.get("location_id"):
        plz = search.get("postal_code", "")
        cat = search.get("category_id", "203")
        loc = search["location_id"]
        rad = search.get("radius_km", 5)
        loc_seg = f"c{cat}l{loc}r{rad}"

        if search.get("min_rooms") or search.get("max_rooms"):
            lo = int(search.get("min_rooms", 1))
            hi = int(search.get("max_rooms", lo))
            loc_seg += f"+wohnung_mieten.zimmer_d:{lo}%2C{hi}"

        if search.get("no_swap", False):
            loc_seg += "+wohnung_mieten.swap_s:nein"

        url = f"{base}/{plz}/{loc_seg}"
    else:
        # Altes Format mit city_id
        parts = [base]
        if search.get("city_id"):
            parts.append(search["city_id"])
        parts.append("anzeige:angebote")
        url = "/".join(parts)

        params = []
        if search.get("min_price") or search.get("max_price"):
            lo = search.get("min_price", "")
            hi = search.get("max_price", "")
            params.append(f"preis:{lo}:{hi}")
        if search.get("min_rooms") or search.get("max_rooms"):
            lo = search.get("min_rooms", "")
            hi = search.get("max_rooms", "")
            params.append(f"zimmer:{lo}:{hi}")
        if params:
            url += "/" + "/".join(params)

    return url


def parse_listings(html: str, search_name: str) -> list:
    """Parst die Suchergebnisseite und gibt eine Liste von Inseraten zurück."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    articles = soup.select("article.aditem") or soup.select("li[data-adid]")

    for art in articles:
        try:
            ad_id = (
                art.get("data-adid") or
                art.select_one("[data-adid]") and art.select_one("[data-adid]").get("data-adid") or
                ""
            )
            if not ad_id:
                link_el = art.select_one("a[href*='/s-anzeige/']")
                if link_el:
                    href = link_el.get("href", "")
                    parts = [p for p in href.split("/") if p.isdigit()]
                    ad_id = parts[-1] if parts else ""

            if not ad_id:
                continue

            title_el = art.select_one(".ellipsis, .aditem-title, h2.text-module-begin")
            title = title_el.get_text(strip=True) if title_el else "–"

            price_el = art.select_one(".aditem-main--middle--price-shipping--price, p.aditem-main--middle--price")
            price_raw = price_el.get_text(strip=True) if price_el else ""

            loc_el = art.select_one(".aditem-main--top--left, .aditem-location")
            location = " ".join(loc_el.get_text().split()) if loc_el else ""

            desc_el = art.select_one(".aditem-main--middle--description, p.aditem-main--middle--description")
            description = desc_el.get_text(strip=True) if desc_el else ""

            link_el = art.select_one("a[href*='/s-anzeige/']") or art.select_one("a.ellipsis")
            href = link_el.get("href", "") if link_el else ""
            url = f"https://www.kleinanzeigen.de{href}" if href.startswith("/") else href

            img_el = art.select_one("img[src]")
            img = img_el.get("src", "") if img_el else ""

            results.append({
                "id": str(ad_id),
                "title": title,
                "price": price_raw,
                "location": location,
                "description": description,
                "url": url,
                "image": img,
                "search_name": search_name,
                "found_at": datetime.now().isoformat(),
                "is_new": True,
            })

        except Exception as e:
            log.debug(f"Fehler beim Parsen eines Inserats: {e}")
            continue

    return results


def fetch_search(search: dict) -> list:
    url = build_url(search)
    log.info(f"[{search['name']}] Abrufe URL: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        # Bewusst eine frische Session pro Request: kleinanzeigen.de (Akamai
        # Bot-Schutz) liefert bei einer wiederverwendeten Session ab dem
        # zweiten Request zuverlässig 0 Treffer (200 OK, aber ohne Inserate),
        # da das dabei gesetzte _abck-Cookie ohne echte Browser-JS-Ausführung
        # als Bot markiert wird. Reproduziert: 3/3 frische Sessions liefern
        # korrekte Ergebnisse, jede Zweitanfrage einer wiederverwendeten
        # Session schlägt fehl.
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        listings = parse_listings(resp.text, search["name"])

        exclude_keywords = search.get("exclude_keywords", DEFAULT_EXCLUDE_KEYWORDS)
        if exclude_keywords:
            before = len(listings)

            def is_excluded(l: dict) -> bool:
                text = f"{l.get('title', '')} {l.get('description', '')}".lower()
                return any(kw.lower() in text for kw in exclude_keywords)

            listings = [l for l in listings if not is_excluded(l)]
            log.info(f"[{search['name']}] {len(listings)}/{before} Inserate nach Stichwort-Filter (WG/Zwischenmiete)")

        max_km = search.get("max_distance_km")
        if max_km is not None:
            before = len(listings)

            def extract_km(loc: str) -> float:
                m = re.search(r"\((?:ca\.\s*)?(\d+(?:[.,]\d+)?)\s*km\)", loc)
                return float(m.group(1).replace(",", ".")) if m else float("inf")

            listings = [l for l in listings if extract_km(l.get("location", "")) <= max_km]
            log.info(f"[{search['name']}] {len(listings)}/{before} Inserate nach Distanz-Filter (≤{max_km} km)")
        else:
            log.info(f"[{search['name']}] {len(listings)} Inserate gefunden")
        return listings
    except requests.exceptions.HTTPError as e:
        log.warning(f"[{search['name']}] HTTP-Fehler: {e}")
    except requests.exceptions.ConnectionError:
        log.warning(f"[{search['name']}] Verbindungsfehler – kein Internet?")
    except Exception as e:
        log.error(f"[{search['name']}] Unerwarteter Fehler: {e}")
    return []


def merge_listings(existing: list, fresh: list, max_store: int) -> tuple[list, int]:
    """Fügt neue Inserate zur bestehenden Liste hinzu. Gibt (merged, new_count) zurück."""
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

    existing = existing[:max_store]
    return existing, new_count


def check_listing_alive(url: str) -> str:
    """Prüft, ob eine Kleinanzeigen-Anzeige noch aktiv ist.

    Rückgabe: "alive", "gone" oder "unknown" (bei Netzwerkfehlern –
    wird nie als gelöscht gewertet, um Fehlalarme durch temporäre
    Verbindungsprobleme auszuschließen).

    Erkennung: aktive Anzeigen laden direkt (ohne Redirect) und enthalten
    `window.pageType = 'VIP'`. Gelöschte/abgelaufene/deaktivierte Anzeigen
    leiten Kleinanzeigen.de dagegen automatisch auf eine Kategorie- oder
    Startseite um (pageType 'ResultsBrowse'/'Homepage', kein VIP) – das
    ist ein stabileres Signal als deutsche Textphrasen, die je nach
    A/B-Test oder Redesign wechseln können.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
        "DNT": "1",
        "Connection": "keep-alive",
    }
    try:
        # Frische Session aus demselben Grund wie in fetch_search().
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        log.debug(f"[Housekeeping] Unklar (Netzwerkfehler) bei {url}: {e}")
        return "unknown"

    if resp.status_code == 404:
        return "gone"
    if resp.status_code != 200:
        log.debug(f"[Housekeeping] Unklar (HTTP {resp.status_code}) bei {url}")
        return "unknown"

    is_vip = "pagetype = 'vip'" in resp.text.lower() or 'pagetype = "vip"' in resp.text.lower()
    if is_vip and not resp.history:
        return "alive"
    if resp.history and not is_vip:
        return "gone"

    log.debug(f"[Housekeeping] Unklar (kein eindeutiges Signal) bei {url}")
    return "unknown"


def run_housekeeping(listings: list) -> tuple[list, int, bool]:
    """Prüft alle gespeicherten Anzeigen und entfernt endgültig gelöschte/
    deaktivierte Einträge. Bricht sicherheitshalber ohne Löschung ab, wenn
    auffällig viele Anzeigen als "gone" erkannt werden (z. B. bei einer
    IP-Sperre oder Captcha-Seite, die pauschal wie "gelöscht" aussehen würde),
    statt versehentlich fast die ganze Liste zu leeren.

    Rückgabe: (verbleibende Anzeigen, Anzahl entfernt, ob abgebrochen wurde)
    """
    ABORT_THRESHOLD = 0.3
    MIN_FOR_THRESHOLD = 5

    kept = []
    gone = []
    for l in listings:
        status = check_listing_alive(l.get("url", ""))
        if status == "gone":
            gone.append(l)
        else:
            kept.append(l)
            if status == "unknown":
                log.warning(f"[Housekeeping] Unklar, wird behalten: {l.get('id')} – {l.get('url')}")
        time.sleep(3)

    total = len(listings)
    if total >= MIN_FOR_THRESHOLD and len(gone) / total > ABORT_THRESHOLD:
        log.warning(
            f"[Housekeeping] Abgebrochen: {len(gone)}/{total} Anzeigen als "
            f"gelöscht erkannt – das ist ungewöhnlich viel (evtl. IP-Sperre "
            f"oder Captcha). Es wird nichts gelöscht, nächster Versuch beim "
            f"nächsten Zyklus."
        )
        return listings, 0, True

    for l in gone:
        log.info(f"[Housekeeping] Entfernt: {l.get('id')} – {l.get('title')} ({l.get('url')})")

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
    log.info("  Wohnungsmonitor gestartet")
    log.info("═" * 50)

    while True:
        # Nachtruhe: zwischen 22 und 6 Uhr kein Crawling
        if is_quiet_hours(22, 6):
            config = load_config()
            hk_hour = config.get("housekeeping_hour", 2)

            if hk_hour >= 22 or hk_hour < 6:
                hk_target = next_occurrence(hk_hour)
                hk_date   = hk_target.strftime("%Y-%m-%d")
                hk_state  = load_housekeeping_state()

                if hk_state.get("last_run_date") != hk_date:
                    wait = (hk_target - datetime.now()).total_seconds()
                    if wait > 0:
                        log.info(f"Nachtruhe – Housekeeping um {hk_target.strftime('%H:%M Uhr')} ({int(wait // 3600)}h {int((wait % 3600) // 60)}min)")
                        time.sleep(wait)

                    log.info("─" * 50)
                    log.info("  Housekeeping: prüfe gespeicherte Anzeigen auf Löschung/Deaktivierung")
                    log.info("─" * 50)

                    listings = load_listings()
                    listings, removed, aborted = run_housekeeping(listings)
                    if not aborted:
                        save_listings(listings)
                        hk_state["last_run_date"] = hk_date
                        save_housekeeping_state(hk_state)
                    log.info(f"✓ Housekeeping: {removed} Anzeige(n) entfernt" if removed else "✓ Housekeeping: keine Änderungen")
            else:
                log.warning(f"housekeeping_hour={hk_hour} liegt außerhalb der Nachtruhe (22-6 Uhr) und wird ignoriert.")

            sleep_until(6)
            continue

        config = load_config()
        listings = load_listings()

        total_new = 0
        for search in config.get("searches", []):
            fresh = fetch_search(search)
            listings, new_count = merge_listings(
                listings, fresh, config.get("max_listings_stored", 500)
            )
            total_new += new_count
            time.sleep(random.uniform(2, 6))  # zufällige Pause zwischen Suchen

        save_listings(listings)

        if total_new > 0:
            log.info(f"✓ {total_new} neue Inserate gespeichert!")
        else:
            log.info("✓ Keine neuen Inserate.")

        # Zufälliges Intervall ±30% um Muster zu vermeiden
        base = config.get("check_interval_seconds", 300)
        jitter = random.uniform(-0.3, 0.3)
        interval = int(base * (1 + jitter))
        next_min = interval // 60
        next_sec = interval % 60
        log.info(f"Nächster Check in {next_min}m {next_sec}s...")
        time.sleep(interval)


if __name__ == "__main__":
    run_crawler()
