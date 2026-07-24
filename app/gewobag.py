#!/usr/bin/env python3
"""
Wohnungsmonitor – Gewobag-Quelle
Eigenständiges Modul für gewobag.de, analog zu den Kleinanzeigen-Funktionen
in crawler.py. Wird von crawler.py per "source"-Feld pro Suche eingebunden.

Im Gegensatz zu kleinanzeigen.de liefert gewobag.de Suchergebnisse als
serverseitig gerendertes HTML (WordPress) ohne erkennbaren Bot-Schutz
(robots.txt erlaubt die Suchseiten, keine Session-Auffälligkeiten bei
wiederholten Requests). Trotzdem wird hier – wie bei Kleinanzeigen – bewusst
eine frische Anfrage pro Request verwendet, statt einer wiederverwendeten
Session, um beim geringsten Anlass auf der sicheren Seite zu sein.
"""

import logging
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://www.gewobag.de/fuer-mietinteressentinnen/mietangebote/wohnung/"
MAX_PAGES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    "DNT": "1",
    "Connection": "keep-alive",
}


def build_gewobag_url(search: dict) -> str:
    """Baut die Gewobag-Such-URL mit Bezirks-/Zimmer-/Preis-/Flächenfiltern."""
    params = []

    for typ in search.get("objekttyp", ["wohnung"]):
        params.append(("objekttyp[]", typ))
    for bezirk in search.get("bezirke", []):
        params.append(("bezirke_filter[]", bezirk))

    for field in (
        "zimmer_von", "zimmer_bis",
        "gesamtmiete_von", "gesamtmiete_bis",
        "gesamtflaeche_von", "gesamtflaeche_bis",
    ):
        value = search.get(field)
        if value not in (None, ""):
            params.append((field, value))

    return f"{BASE_URL}?{urlencode(params, doseq=True)}"


def parse_gewobag_listings(html: str, search_name: str) -> list:
    """Parst eine Gewobag-Suchergebnisseite und gibt eine Liste von Inseraten zurück."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for art in soup.select("article.gw-offer"):
        try:
            link_el = art.select_one("a.read-more-link[href]")
            href = link_el.get("href", "") if link_el else ""
            if not href:
                continue

            slug = href.rstrip("/").rsplit("/", 1)[-1]
            listing_id = f"gewobag-{slug}"

            title_el = art.select_one(".angebot-title")
            title = title_el.get_text(strip=True) if title_el else "–"

            region_el = art.select_one(".angebot-region td")
            region = region_el.get_text(strip=True) if region_el else ""
            address_el = art.select_one(".angebot-address address")
            address = address_el.get_text(strip=True) if address_el else ""
            location = " – ".join(p for p in (region, address) if p)

            price_el = art.select_one(".angebot-kosten td")
            price = price_el.get_text(strip=True) if price_el else ""

            desc_parts = []
            area_el = art.select_one(".angebot-area td")
            if area_el:
                desc_parts.append(" ".join(area_el.get_text().split()))
            avail_el = art.select_one(".availability td")
            if avail_el:
                desc_parts.append(f"Frei ab {avail_el.get_text(strip=True)}")
            characteristics = [
                li.get_text(strip=True)
                for li in art.select(".angebot-characteristics li")
            ]
            if characteristics:
                desc_parts.append(", ".join(characteristics))
            description = " | ".join(desc_parts)

            img_el = art.select_one("img.swiper__image[src]")
            img = img_el.get("src", "") if img_el else ""

            results.append({
                "id": listing_id,
                "title": title,
                "price": price,
                "location": location,
                "description": description,
                "url": href,
                "image": img,
                "search_name": search_name,
                "found_at": datetime.now().isoformat(),
                "is_new": True,
                "source": "gewobag",
            })

        except Exception as e:
            log.debug(f"Fehler beim Parsen eines Gewobag-Inserats: {e}")
            continue

    return results


def _find_next_page_url(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    next_el = soup.select_one("a.next.page-numbers[href]")
    return next_el.get("href", "") if next_el else ""


def fetch_gewobag_search(search: dict) -> list:
    url = build_gewobag_url(search)
    log.info(f"[{search['name']}] Abrufe URL: {url}")

    all_listings = []
    try:
        for page_num in range(1, MAX_PAGES + 1):
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()

            page_listings = parse_gewobag_listings(resp.text, search["name"])
            if not page_listings:
                break
            all_listings.extend(page_listings)

            next_url = _find_next_page_url(resp.text)
            if not next_url:
                break
            url = next_url
            time.sleep(2)

        exclude_keywords = search.get("exclude_keywords", [])
        if exclude_keywords:
            before = len(all_listings)

            def is_excluded(l: dict) -> bool:
                text = f"{l.get('title', '')} {l.get('description', '')}".lower()
                return any(kw.lower() in text for kw in exclude_keywords)

            all_listings = [l for l in all_listings if not is_excluded(l)]
            log.info(f"[{search['name']}] {len(all_listings)}/{before} Inserate nach Stichwort-Filter")
        else:
            log.info(f"[{search['name']}] {len(all_listings)} Inserate gefunden")

        return all_listings
    except requests.exceptions.HTTPError as e:
        log.warning(f"[{search['name']}] HTTP-Fehler: {e}")
    except requests.exceptions.ConnectionError:
        log.warning(f"[{search['name']}] Verbindungsfehler – kein Internet?")
    except Exception as e:
        log.error(f"[{search['name']}] Unerwarteter Fehler: {e}")
    return []


def check_gewobag_alive(url: str) -> str:
    """Prüft, ob ein Gewobag-Mietangebot noch aktiv ist.

    Rückgabe: "alive", "gone" oder "unknown" (bei Netzwerkfehlern/Unklarheit –
    wird nie als gelöscht gewertet, um Fehlalarme auszuschließen).
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        log.debug(f"[Housekeeping] Unklar (Netzwerkfehler) bei {url}: {e}")
        return "unknown"

    if resp.status_code == 404:
        return "gone"
    if resp.status_code != 200:
        log.debug(f"[Housekeeping] Unklar (HTTP {resp.status_code}) bei {url}")
        return "unknown"

    on_detail_page = "/mietangebote/" in resp.url and resp.url.rstrip("/") != BASE_URL.rstrip("/")
    has_listing_markup = 'class="angebot-image"' in resp.text

    if on_detail_page and has_listing_markup and not resp.history:
        return "alive"
    if resp.history and (not on_detail_page or not has_listing_markup):
        return "gone"

    log.debug(f"[Housekeeping] Unklar (kein eindeutiges Signal) bei {url}")
    return "unknown"
