# HOWOGE-Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, fully independent crawler process that pulls rental listings from HOWOGE's public JSON search endpoint and surfaces them in the existing dashboard alongside the Kleinanzeigen listings.

**Architecture:** A new standalone script `app/howoge_crawler.py` (no shared code with `app/crawler.py`) polls HOWOGE's `immoList` JSON endpoint on its own config/interval/Nachtruhe cycle, writes to its own `howoge_listings.json`. `app/dashboard.py` reads both listing files, tags entries with a `source` field, and routes delete/mark-seen/config/log operations to the right file based on that source or an explicit `?source=` query param. `app/templates/index.html` gets a small source badge on each card and a second config textarea for HOWOGE.

**Tech Stack:** Python 3, `requests` (already a dependency), Flask (already a dependency), no new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-07-24-howoge-crawler-design.md`

## Global Constraints

- No test framework (pytest, unittest-as-a-runner, etc.) exists in this repo and none should be introduced. Per `CLAUDE.md`, verification is `python3 -m py_compile app/*.py` plus manual runs. Verification steps in this plan use standalone `python3 -c` snippets with plain `assert` (using `unittest.mock` only as a stdlib patching utility inside a snippet, not as a test runner) and Flask's built-in `test_client()` (already part of the `flask` dependency) — never add `pytest` to `requirements.txt`.
- `app/howoge_crawler.py` must not import from or share code with `app/crawler.py` — each crawler is a fully independent process (per spec, so a bug in one can't take down the other).
- All HOWOGE listing IDs are prefixed `howoge-<uid>` to avoid collisions with Kleinanzeigen's numeric IDs once both lists are merged in the dashboard.
- Nachtruhe window is hardcoded 22:00–06:00 (same as the existing crawler), interval jitter is ±30 %.
- Housekeeping abort threshold: don't delete anything if more than 30% of stored listings look "gone" in one pass, and only apply the threshold once at least 5 listings are stored (both values copied from the existing `crawler.py` pattern).
- `kiez` and `wbs` are passed straight through to HOWOGE's API (server-side filtering verified working); `rooms` is **not** passed to the API (it only accepts a single exact value) — `min_rooms`/`max_rooms` are filtered client-side instead.
- Existing Kleinanzeigen listing entries have no `source` field on disk; code that merges listings must treat a missing `source` as `"kleinanzeigen"` rather than requiring a migration.

---

### Task 1: `howoge_crawler.py` — config & data file I/O

**Files:**
- Create: `app/howoge_crawler.py`

**Interfaces:**
- Produces: `DATA_DIR: Path`, `CONFIG_FILE: Path`, `LISTINGS_FILE: Path`, `LOG_FILE: Path`, `DEFAULT_CONFIG: dict`, `load_config() -> dict`, `load_listings() -> list`, `save_listings(listings: list) -> None`, module logger `log`.

- [ ] **Step 1: Write the failing verification snippet**

Run (expected to fail — the module doesn't exist yet):

```bash
python3 -c "
import sys, tempfile, os
sys.path.insert(0, 'app')
os.environ['DATA_DIR'] = tempfile.mkdtemp()
import howoge_crawler as hc

cfg = hc.load_config()
assert cfg['searches'][0]['name'] == 'Berlin – 3 Zimmer'
assert cfg['searches'][0]['kiez'] == ['Friedrichshain-Kreuzberg', 'Mitte', 'Pankow']
assert cfg['searches'][0]['min_rooms'] == 3
assert cfg['searches'][0]['max_rooms'] == 6
assert cfg['searches'][0]['wbs'] == 'nein'
assert cfg['check_interval_seconds'] == 300
assert hc.CONFIG_FILE.exists()

hc.save_listings([{'id': 'howoge-1'}])
assert hc.load_listings() == [{'id': 'howoge-1'}]
print('OK')
"
```

Expected: `ModuleNotFoundError: No module named 'howoge_crawler'`

- [ ] **Step 2: Create `app/howoge_crawler.py` with the config/data-file skeleton**

```python
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
```

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK` printed, no assertion errors.

- [ ] **Step 4: Compile check**

```bash
python3 -m py_compile app/howoge_crawler.py
```

Expected: no output (success).

- [ ] **Step 5: Commit**

```bash
git add app/howoge_crawler.py
git commit -m "Add HOWOGE crawler config/data-file skeleton"
```

---

### Task 2: `howoge_crawler.py` — fetch + parse HOWOGE listings

**Files:**
- Modify: `app/howoge_crawler.py`

**Interfaces:**
- Consumes: nothing new from Task 1 besides the module itself.
- Produces: `fetch_immo_objects(kiez: list, wbs: str) -> list` (raw API objects; raises `requests.exceptions.RequestException` on network/HTTP failure), `parse_immo_objects(raw_objects: list, search: dict) -> list` (pure, no network — returns listings already in our internal schema: `id`, `title`, `price`, `location`, `description`, `url`, `image`, `search_name`, `source`, `found_at`, `is_new`).

- [ ] **Step 1: Write the failing verification snippet (pure parsing, no network)**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
import howoge_crawler as hc

raw = [
    {
        'uid': 1, 'title': 'Teststr. 1, 10115 Berlin', 'district': 'Mitte',
        'rent': 900, 'area': 70, 'rooms': 4, 'wbs': 'nein',
        'features': ['Balkon', 'Aufzug'],
        'link': '/immobiliensuche/wohnungssuche/detail/1-2-3.html',
        'image': '/fileadmin/img.webp', 'notice': '4-Zimmer-Wohnung'
    },
    {
        'uid': 2, 'title': 'Teststr. 2, 10115 Berlin', 'district': 'Mitte',
        'rent': 500, 'area': 30, 'rooms': 1, 'wbs': 'ja',
        'features': [], 'link': '/x.html', 'image': '/y.webp', 'notice': ''
    },
]
search = {'name': 'Test-Suche', 'min_rooms': 3, 'max_rooms': 6, 'kiez': ['Mitte'], 'wbs': 'nein'}
result = hc.parse_immo_objects(raw, search)

assert len(result) == 1, result
r = result[0]
assert r['id'] == 'howoge-1'
assert r['title'] == 'Teststr. 1, 10115 Berlin'
assert r['price'] == '900 €'
assert r['location'] == 'Mitte'
assert r['url'] == 'https://www.howoge.de/immobiliensuche/wohnungssuche/detail/1-2-3.html'
assert r['image'] == 'https://www.howoge.de/fileadmin/img.webp'
assert r['search_name'] == 'Test-Suche'
assert r['source'] == 'howoge'
assert r['is_new'] is True
assert 'Balkon' in r['description']
assert '4-Zimmer-Wohnung' in r['description']
print('OK')
"
```

Expected: `AttributeError: module 'howoge_crawler' has no attribute 'parse_immo_objects'`

- [ ] **Step 2: Add `fetch_immo_objects` and `parse_immo_objects` to `app/howoge_crawler.py`**

Insert after `save_listings`:

```python
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
```

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK`.

- [ ] **Step 4: Manual live smoke check (requires internet access)**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
import howoge_crawler as hc

raw = hc.fetch_immo_objects(['Pankow'], 'nein')
print('n objects:', len(raw))
assert isinstance(raw, list)
if raw:
    assert 'uid' in raw[0] and 'rooms' in raw[0]
print('OK')
"
```

Expected: `n objects: <some number>` followed by `OK` (the exact count depends on HOWOGE's current live stock, so don't assert a fixed value).

- [ ] **Step 5: Compile check**

```bash
python3 -m py_compile app/howoge_crawler.py
```

- [ ] **Step 6: Commit**

```bash
git add app/howoge_crawler.py
git commit -m "Add HOWOGE fetch + parse logic"
```

---

### Task 3: `howoge_crawler.py` — `fetch_search()` wrapper with error handling

**Files:**
- Modify: `app/howoge_crawler.py`

**Interfaces:**
- Consumes: `fetch_immo_objects`, `parse_immo_objects` from Task 2.
- Produces: `fetch_search(search: dict) -> list` (never raises; logs and returns `[]` on failure).

- [ ] **Step 1: Write the failing verification snippet**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
import howoge_crawler as hc

# Suche mit garantiert kaputter Konfiguration (kein Netzwerkfehler nötig,
# ein fehlendes 'name'-Feld reicht um den Fehlerpfad zu triggern)
result = hc.fetch_search({'kiez': ['Mitte']})
assert result == []
print('OK')
"
```

Expected: `AttributeError: module 'howoge_crawler' has no attribute 'fetch_search'`

- [ ] **Step 2: Add `fetch_search` to `app/howoge_crawler.py`**

Insert after `parse_immo_objects`:

```python
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
```

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK` (a search dict without `"name"` makes `parse_immo_objects` raise a `KeyError` on `search["name"]`, which `fetch_search`'s catch-all `except Exception` turns into a logged warning and `[]`).

- [ ] **Step 4: Manual live smoke check**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
import howoge_crawler as hc

result = hc.fetch_search({'name': 'Smoke-Test', 'kiez': ['Pankow'], 'min_rooms': 1, 'max_rooms': 6, 'wbs': ''})
assert isinstance(result, list)
print('n:', len(result))
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add app/howoge_crawler.py
git commit -m "Add HOWOGE fetch_search error handling wrapper"
```

---

### Task 4: `howoge_crawler.py` — merge logic

**Files:**
- Modify: `app/howoge_crawler.py`

**Interfaces:**
- Produces: `merge_listings(existing: list, fresh: list) -> tuple[list, int]` (inserts new items at the front of `existing`, marks re-seen items `is_new = False`, returns `(merged_list, new_count)`).

- [ ] **Step 1: Write the failing verification snippet**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
import howoge_crawler as hc

existing = [{'id': 'howoge-1', 'title': 'A', 'is_new': False}]
fresh = [
    {'id': 'howoge-1', 'title': 'A'},
    {'id': 'howoge-2', 'title': 'B'},
]
merged, new_count = hc.merge_listings(existing, fresh)
assert new_count == 1
assert len(merged) == 2
ids = [l['id'] for l in merged]
assert 'howoge-1' in ids and 'howoge-2' in ids
b = next(l for l in merged if l['id'] == 'howoge-2')
assert b['title'] == 'B'
print('OK')
"
```

Expected: `AttributeError: module 'howoge_crawler' has no attribute 'merge_listings'`

- [ ] **Step 2: Add `merge_listings` to `app/howoge_crawler.py`**

Insert after `fetch_search`:

```python
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
```

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add app/howoge_crawler.py
git commit -m "Add HOWOGE listing merge logic"
```

---

### Task 5: `howoge_crawler.py` — housekeeping

**Files:**
- Modify: `app/howoge_crawler.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `fetch_all_active_ids() -> set | None` (returns `None` on network failure instead of raising, so callers can skip housekeeping safely that cycle), `run_housekeeping(listings: list, active_ids: set) -> tuple[list, int, bool]` (returns `(kept_listings, removed_count, aborted)`).

- [ ] **Step 1: Write the failing verification snippet**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
import howoge_crawler as hc

listings = [
    {'id': 'howoge-1', 'title': 'A'},
    {'id': 'howoge-2', 'title': 'B'},
    {'id': 'howoge-3', 'title': 'C'},
    {'id': 'howoge-4', 'title': 'D'},
    {'id': 'howoge-5', 'title': 'E'},
]

# Normalfall: 1 von 5 (20%) fehlt -> unter der 30%-Schwelle, wird entfernt
active_ids = {'howoge-1', 'howoge-2', 'howoge-3', 'howoge-4'}
kept, removed, aborted = hc.run_housekeeping(listings, active_ids)
assert aborted is False
assert removed == 1
assert len(kept) == 4
assert all(l['id'] != 'howoge-5' for l in kept)

# Sicherheitsnetz: 4 von 5 (80%) fehlen -> Abbruch, nichts wird gelöscht
kept2, removed2, aborted2 = hc.run_housekeeping(listings, {'howoge-1'})
assert aborted2 is True
assert removed2 == 0
assert len(kept2) == 5
print('OK')
"
```

Expected: `AttributeError: module 'howoge_crawler' has no attribute 'run_housekeeping'`

- [ ] **Step 2: Add housekeeping functions to `app/howoge_crawler.py`**

Insert after `merge_listings`:

```python
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
```

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK`.

- [ ] **Step 4: Manual live smoke check**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
import howoge_crawler as hc

ids = hc.fetch_all_active_ids()
assert ids is not None
assert all(i.startswith('howoge-') for i in ids)
print('n active:', len(ids))
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add app/howoge_crawler.py
git commit -m "Add HOWOGE housekeeping logic"
```

---

### Task 6: `howoge_crawler.py` — Nachtruhe, jitter, main loop

**Files:**
- Modify: `app/howoge_crawler.py`

**Interfaces:**
- Consumes: `load_config`, `load_listings`, `save_listings` (Task 1), `fetch_search` (Task 3), `merge_listings` (Task 4), `fetch_all_active_ids`, `run_housekeeping` (Task 5).
- Produces: `is_quiet_hours(quiet_start: int = 22, quiet_end: int = 6) -> bool`, `next_occurrence(hour: int, minute: int = 0) -> datetime`, `sleep_until(hour: int, minute: int = 0) -> None`, `run_crawler() -> None` (infinite loop — never returns under normal operation).

- [ ] **Step 1: Write the failing verification snippet (pure helpers only — `run_crawler` itself is an infinite loop and is verified manually in Step 4)**

```bash
python3 -c "
import sys
sys.path.insert(0, 'app')
from unittest import mock
from datetime import datetime
import howoge_crawler as hc

with mock.patch('howoge_crawler.datetime') as mock_dt:
    mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0)
    assert hc.is_quiet_hours(22, 6) is True
    mock_dt.now.return_value = datetime(2026, 1, 1, 10, 0)
    assert hc.is_quiet_hours(22, 6) is False

    mock_dt.now.return_value = datetime(2026, 1, 1, 10, 0)
    target = hc.next_occurrence(6)
    assert target == datetime(2026, 1, 2, 6, 0)  # 06:00 ist schon vorbei -> morgen
    target2 = hc.next_occurrence(12)
    assert target2 == datetime(2026, 1, 1, 12, 0)  # 12:00 kommt noch heute
print('OK')
"
```

Expected: `AttributeError: module 'howoge_crawler' has no attribute 'is_quiet_hours'`

- [ ] **Step 2: Add Nachtruhe helpers and the main loop to `app/howoge_crawler.py`**

Insert after `run_housekeeping`:

```python
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
```

Remove the now-redundant `if __name__ == "__main__": pass` placeholder from Task 1 (it's replaced by the block above).

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK`.

- [ ] **Step 4: Manual live smoke run**

```bash
mkdir -p /tmp/howoge_smoke_data
DATA_DIR=/tmp/howoge_smoke_data python3 app/howoge_crawler.py &
PID=$!
sleep 20
kill "$PID"
cat /tmp/howoge_smoke_data/howoge_crawler.log
cat /tmp/howoge_smoke_data/howoge_listings.json | python3 -m json.tool | head -30
```

Expected: log shows `HOWOGE-Crawler gestartet`, at least one `[Berlin – 3 Zimmer] N Inserate gefunden` line, and either `✓ N neue HOWOGE-Inserate gespeichert!` or `✓ Keine neuen HOWOGE-Inserate.`; `howoge_listings.json` contains valid JSON (an empty `[]` is fine if there happen to be zero live matches right now).

- [ ] **Step 5: Compile check**

```bash
python3 -m py_compile app/howoge_crawler.py
```

- [ ] **Step 6: Commit**

```bash
git add app/howoge_crawler.py
git commit -m "Add HOWOGE crawler main loop with Nachtruhe and jitter"
```

---

### Task 7: `docker-entrypoint.sh` — start the third process

**Files:**
- Modify: `docker-entrypoint.sh`

**Interfaces:**
- Consumes: `app/howoge_crawler.py` (Task 6) must be runnable via `python /app/app/howoge_crawler.py`.

- [ ] **Step 1: Read the current file to confirm line numbers before editing**

```bash
cat -n docker-entrypoint.sh
```

- [ ] **Step 2: Replace the file contents to start and watch three processes**

```bash
#!/bin/sh
# Startet Crawler, HOWOGE-Crawler und Dashboard parallel im selben Container

echo "═══════════════════════════════════════"
echo "  Wohnungsmonitor starting..."
echo "═══════════════════════════════════════"

# Kleinanzeigen-Crawler im Hintergrund starten
python /app/app/crawler.py &
CRAWLER_PID=$!

# HOWOGE-Crawler im Hintergrund starten
python /app/app/howoge_crawler.py &
HOWOGE_CRAWLER_PID=$!

# Dashboard im Hintergrund starten
python /app/app/dashboard.py &
DASHBOARD_PID=$!

shutdown() {
    echo "Shutting down..."
    kill "$CRAWLER_PID" "$HOWOGE_CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
    wait "$CRAWLER_PID" "$HOWOGE_CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
    exit 0
}
trap shutdown TERM INT

# Solange laufen lassen, bis EINER der drei Prozesse endet (auch bei Crash).
# Danach den Container mit Fehlercode beenden, damit "restart: unless-stopped"
# alle drei Prozesse sauber neu startet, statt die toten Prozesse unbemerkt
# liegen zu lassen.
while kill -0 "$CRAWLER_PID" 2>/dev/null && kill -0 "$HOWOGE_CRAWLER_PID" 2>/dev/null && kill -0 "$DASHBOARD_PID" 2>/dev/null; do
    sleep 2
done

echo "Ein Prozess wurde beendet - Container wird gestoppt, damit ein Neustart erfolgen kann."
kill "$CRAWLER_PID" "$HOWOGE_CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
wait "$CRAWLER_PID" "$HOWOGE_CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
exit 1
```

- [ ] **Step 3: Syntax check**

```bash
sh -n docker-entrypoint.sh
```

Expected: no output (success).

- [ ] **Step 4: Commit**

```bash
git add docker-entrypoint.sh
git commit -m "Start HOWOGE crawler as a third container process"
```

---

### Task 8: `dashboard.py` — merge HOWOGE listings into `/api/listings` and `/api/stats`

**Files:**
- Modify: `app/dashboard.py:13-28` (paths + loaders), `app/dashboard.py:42-72` (`api_listings`, `api_stats`)

**Interfaces:**
- Produces: `HOWOGE_LISTINGS_FILE: Path`, `HOWOGE_CONFIG_FILE: Path`, `HOWOGE_LOG_FILE: Path`, `load_howoge_listings() -> list`, `load_all_listings() -> list` (Kleinanzeigen entries default to `source="kleinanzeigen"` if the field is missing).

- [ ] **Step 1: Write the failing verification snippet**

```bash
python3 -c "
import sys, json, tempfile, os
sys.path.insert(0, 'app')
os.environ['DATA_DIR'] = tempfile.mkdtemp()
import dashboard

dashboard.LISTINGS_FILE.write_text(json.dumps([
    {'id': '123', 'title': 'Alte Anzeige', 'is_new': True}
]))
dashboard.HOWOGE_LISTINGS_FILE.write_text(json.dumps([
    {'id': 'howoge-9', 'title': 'HOWOGE Anzeige', 'source': 'howoge', 'is_new': True}
]))

client = dashboard.app.test_client()
r = client.get('/api/listings')
data = r.get_json()
assert len(data) == 2, data
sources = {l['id']: l.get('source') for l in data}
assert sources['123'] == 'kleinanzeigen'
assert sources['howoge-9'] == 'howoge'

r2 = client.get('/api/stats')
stats = r2.get_json()
assert stats['total'] == 2
print('OK')
"
```

Expected: `AttributeError: module 'dashboard' has no attribute 'HOWOGE_LISTINGS_FILE'`

- [ ] **Step 2: Add HOWOGE paths and loaders, update `api_listings`/`api_stats`**

Read the current file first:

```bash
cat -n app/dashboard.py
```

Replace lines 13-28 (the `DATA_DIR`/`LISTINGS_FILE`/... block through `load_listings`) with:

```python
# ── Pfade ─────────────────────────────────────────────────────────────────────
DATA_DIR      = Path(os.environ.get("DATA_DIR", "/data"))
LISTINGS_FILE = DATA_DIR / "listings.json"
CONFIG_FILE   = DATA_DIR / "config.json"
LOG_FILE      = DATA_DIR / "crawler.log"

HOWOGE_LISTINGS_FILE = DATA_DIR / "howoge_listings.json"
HOWOGE_CONFIG_FILE   = DATA_DIR / "howoge_config.json"
HOWOGE_LOG_FILE      = DATA_DIR / "howoge_crawler.log"

app = Flask(__name__, template_folder="templates")


def load_listings() -> list:
    if LISTINGS_FILE.exists():
        try:
            return json.loads(LISTINGS_FILE.read_text())
        except Exception:
            return []
    return []


def load_howoge_listings() -> list:
    if HOWOGE_LISTINGS_FILE.exists():
        try:
            return json.loads(HOWOGE_LISTINGS_FILE.read_text())
        except Exception:
            return []
    return []


def load_all_listings() -> list:
    listings = load_listings()
    for l in listings:
        l.setdefault("source", "kleinanzeigen")
    return listings + load_howoge_listings()
```

Then in `api_listings` (originally lines 42-58), change the first line of the function body from:

```python
    listings    = load_listings()
```

to:

```python
    listings    = load_all_listings()
```

And in `api_stats` (originally lines 61-72), change:

```python
    listings  = load_listings()
```

to:

```python
    listings  = load_all_listings()
```

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK`.

- [ ] **Step 4: Compile check**

```bash
python3 -m py_compile app/dashboard.py
```

- [ ] **Step 5: Commit**

```bash
git add app/dashboard.py
git commit -m "Merge HOWOGE listings into dashboard API"
```

---

### Task 9: `dashboard.py` — source-aware delete and mark-seen

**Files:**
- Modify: `app/dashboard.py` (`mark_seen`, `delete_listing`)

**Interfaces:**
- Consumes: `HOWOGE_LISTINGS_FILE`, `load_howoge_listings` (Task 8).
- Produces: `mark_seen()` and `delete_listing(listing_id)` now operate correctly across both files.

- [ ] **Step 1: Write the failing verification snippet**

```bash
python3 -c "
import sys, json, tempfile, os
sys.path.insert(0, 'app')
os.environ['DATA_DIR'] = tempfile.mkdtemp()
import dashboard

dashboard.LISTINGS_FILE.write_text(json.dumps([{'id': '123', 'title': 'A', 'is_new': True}]))
dashboard.HOWOGE_LISTINGS_FILE.write_text(json.dumps([{'id': 'howoge-9', 'title': 'B', 'is_new': True}]))

client = dashboard.app.test_client()

client.delete('/api/delete/howoge-9')
ka = json.loads(dashboard.LISTINGS_FILE.read_text())
ho = json.loads(dashboard.HOWOGE_LISTINGS_FILE.read_text())
assert len(ka) == 1, ka
assert len(ho) == 0, ho

dashboard.HOWOGE_LISTINGS_FILE.write_text(json.dumps([{'id': 'howoge-9', 'title': 'B', 'is_new': True}]))
client.post('/api/mark_seen')
ka2 = json.loads(dashboard.LISTINGS_FILE.read_text())
ho2 = json.loads(dashboard.HOWOGE_LISTINGS_FILE.read_text())
assert all(not l['is_new'] for l in ka2)
assert all(not l['is_new'] for l in ho2)
print('OK')
"
```

Expected: `AssertionError` on the `len(ho) == 0` check (delete currently only touches `LISTINGS_FILE`, so `howoge-9` survives and `123` gets deleted instead since `delete_listing` doesn't know about the prefix yet).

- [ ] **Step 2: Update `mark_seen` and `delete_listing` in `app/dashboard.py`**

Replace the `mark_seen` route:

```python
@app.route("/api/mark_seen", methods=["POST"])
def mark_seen():
    listings = load_listings()
    for l in listings:
        l["is_new"] = False
    LISTINGS_FILE.write_text(json.dumps(listings, indent=2, ensure_ascii=False))

    howoge = load_howoge_listings()
    for l in howoge:
        l["is_new"] = False
    HOWOGE_LISTINGS_FILE.write_text(json.dumps(howoge, indent=2, ensure_ascii=False))

    return jsonify({"ok": True})
```

Replace the `delete_listing` route:

```python
@app.route("/api/delete/<listing_id>", methods=["DELETE"])
def delete_listing(listing_id):
    if listing_id.startswith("howoge-"):
        listings = [l for l in load_howoge_listings() if l.get("id") != listing_id]
        HOWOGE_LISTINGS_FILE.write_text(json.dumps(listings, indent=2, ensure_ascii=False))
    else:
        listings = [l for l in load_listings() if l.get("id") != listing_id]
        LISTINGS_FILE.write_text(json.dumps(listings, indent=2, ensure_ascii=False))
    return jsonify({"ok": True})
```

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add app/dashboard.py
git commit -m "Route delete/mark-seen to the correct listing source"
```

---

### Task 10: `dashboard.py` — source-aware config and log endpoints

**Files:**
- Modify: `app/dashboard.py` (`load_config`, `api_config`, `api_log`)

**Interfaces:**
- Produces: `/api/config?source=howoge` and `/api/log?source=howoge` read/write the HOWOGE files; the default (no `source` param, or `source=kleinanzeigen`) keeps today's behavior.

- [ ] **Step 1: Write the failing verification snippet**

```bash
python3 -c "
import sys, json, tempfile, os
sys.path.insert(0, 'app')
os.environ['DATA_DIR'] = tempfile.mkdtemp()
import dashboard

dashboard.CONFIG_FILE.write_text(json.dumps({'searches': [], 'check_interval_seconds': 300}))
dashboard.HOWOGE_CONFIG_FILE.write_text(json.dumps({'searches': [{'name': 'X'}], 'check_interval_seconds': 600}))

client = dashboard.app.test_client()
r1 = client.get('/api/config')
assert r1.get_json()['check_interval_seconds'] == 300
r2 = client.get('/api/config?source=howoge')
assert r2.get_json()['check_interval_seconds'] == 600

dashboard.LOG_FILE.write_text('kleinanzeigen line\n')
dashboard.HOWOGE_LOG_FILE.write_text('howoge line\n')
l1 = client.get('/api/log').get_json()
l2 = client.get('/api/log?source=howoge').get_json()
assert l1 == ['kleinanzeigen line'], l1
assert l2 == ['howoge line'], l2
print('OK')
"
```

Expected: `AssertionError` on the `source=howoge` config check (currently always reads `CONFIG_FILE` regardless of query param).

- [ ] **Step 2: Update `api_config` and `api_log` in `app/dashboard.py`**

Replace the `api_config` route:

```python
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    source = request.args.get("source", "kleinanzeigen")
    cfg_file = HOWOGE_CONFIG_FILE if source == "howoge" else CONFIG_FILE

    if request.method == "GET":
        if cfg_file.exists():
            return jsonify(json.loads(cfg_file.read_text()))
        return jsonify({})
    cfg = request.get_json()
    cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return jsonify({"ok": True})
```

Replace the `api_log` route:

```python
@app.route("/api/log")
def api_log():
    source = request.args.get("source", "kleinanzeigen")
    log_file = HOWOGE_LOG_FILE if source == "howoge" else LOG_FILE

    if log_file.exists():
        lines = log_file.read_text().splitlines()[-200:]
        return jsonify(lines[::-1])
    return jsonify([])
```

The old module-level `load_config()` helper (used only by the now-removed inline logic) can be deleted if nothing else calls it — check first:

```bash
grep -n "load_config" app/dashboard.py
```

If it's only referenced in its own definition, remove the standalone `load_config()` function since `api_config` no longer calls it.

- [ ] **Step 3: Re-run the verification snippet from Step 1**

Expected: `OK`.

- [ ] **Step 4: Compile check**

```bash
python3 -m py_compile app/dashboard.py
```

- [ ] **Step 5: Commit**

```bash
git add app/dashboard.py
git commit -m "Add source-aware config/log endpoints for HOWOGE"
```

---

### Task 11: `index.html` — source badge on listing cards

**Files:**
- Modify: `app/templates/index.html` (`cardHTML` function, currently around line 665-687)

**Interfaces:**
- Consumes: `l.source` field from `/api/listings` (Task 8) — `"howoge"` or `"kleinanzeigen"`.

- [ ] **Step 1: Read the current `cardHTML` function to confirm exact text**

```bash
grep -n "card-tag" app/templates/index.html
```

- [ ] **Step 2: Update the `card-tag` line inside `cardHTML`**

Find this line:

```js
      <span class="card-tag">${esc(l.search_name || '')}</span>
```

Replace it with:

```js
      <span class="card-tag">${esc(l.search_name || '')} · ${l.source === 'howoge' ? 'HOWOGE' : 'Kleinanzeigen'}</span>
```

- [ ] **Step 3: Manual verification**

```bash
DATA_DIR=/tmp/howoge_smoke_data python3 app/dashboard.py &
PID=$!
sleep 2
curl -s http://localhost:5000/ | grep -o "l.source === 'howoge' ? 'HOWOGE' : 'Kleinanzeigen'"
kill "$PID"
```

Expected: the grep prints the matching snippet, confirming the template served by Flask contains the updated JS. Then open `http://localhost:5000` in a browser once with real data present to visually confirm the badge renders correctly on both Kleinanzeigen and HOWOGE cards (per project convention, UI changes should be eyeballed in an actual browser, not just grepped).

- [ ] **Step 4: Commit**

```bash
git add app/templates/index.html
git commit -m "Show listing source badge on cards"
```

---

### Task 12: `index.html` — second config textarea for HOWOGE

**Files:**
- Modify: `app/templates/index.html` (mobile config screen ~lines 484-498, desktop config tab builder ~lines 777-790, `loadConfig`/`saveConfig` ~lines 708-726, `showScreen` ~lines 747-755, `nav-config` button ~line 523)

**Interfaces:**
- Consumes: `/api/config?source=howoge` and `/api/config` (Task 10).

- [ ] **Step 1: Read the current relevant sections to confirm exact text before editing**

```bash
grep -n "config-json\|loadConfig\|saveConfig\|screen-config\|nav-config\|showDesktopTab" app/templates/index.html
```

- [ ] **Step 2: Add a second textarea block to the mobile config screen**

Find:

```html
  <!-- ── Screen: Konfiguration ── -->
  <div class="screen" id="screen-config">
    <div class="config-screen">
      <h2>KONFIGURATION</h2>
      <div class="config-info">
        Nach dem Speichern Crawler neu starten:<br>
        <code>docker compose restart</code>
      </div>
      <textarea id="config-json" spellcheck="false"></textarea>
      <div style="margin-top:10px">
        <button class="action-btn primary" onclick="saveConfig()">
          <span class="action-icon">💾</span> Speichern
        </button>
      </div>
    </div>
  </div>
```

Replace with:

```html
  <!-- ── Screen: Konfiguration ── -->
  <div class="screen" id="screen-config">
    <div class="config-screen">
      <h2>KONFIGURATION – KLEINANZEIGEN</h2>
      <div class="config-info">
        Nach dem Speichern Crawler neu starten:<br>
        <code>docker compose restart</code>
      </div>
      <textarea id="config-json" spellcheck="false"></textarea>
      <div style="margin-top:10px">
        <button class="action-btn primary" onclick="saveConfig('kleinanzeigen')">
          <span class="action-icon">💾</span> Speichern
        </button>
      </div>

      <h2 style="margin-top:24px">KONFIGURATION – HOWOGE</h2>
      <textarea id="config-json-howoge" spellcheck="false"></textarea>
      <div style="margin-top:10px">
        <button class="action-btn primary" onclick="saveConfig('howoge')">
          <span class="action-icon">💾</span> Speichern
        </button>
      </div>
    </div>
  </div>
```

- [ ] **Step 3: Update `loadConfig`/`saveConfig` to take a `source` argument**

Find:

```js
async function loadConfig() {
  const r = await fetch('/api/config');
  const cfg = await r.json();
  const el = document.getElementById('config-json');
  if (el) el.value = JSON.stringify(cfg, null, 2);
}

async function saveConfig() {
  try {
    const val = document.getElementById('config-json').value;
    const cfg = JSON.parse(val);
    await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    alert('✓ Gespeichert! Bitte Crawler-Service neu starten.');
  } catch(e) { alert('Ungültiges JSON: ' + e.message); }
}
```

Replace with:

```js
async function loadConfig(source = 'kleinanzeigen') {
  const r = await fetch('/api/config?source=' + source);
  const cfg = await r.json();
  const elId = source === 'howoge' ? 'config-json-howoge' : 'config-json';
  const el = document.getElementById(elId);
  if (el) el.value = JSON.stringify(cfg, null, 2);
}

async function saveConfig(source = 'kleinanzeigen') {
  try {
    const elId = source === 'howoge' ? 'config-json-howoge' : 'config-json';
    const val = document.getElementById(elId).value;
    const cfg = JSON.parse(val);
    await fetch('/api/config?source=' + source, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    alert('✓ Gespeichert! Bitte Crawler-Service neu starten.');
  } catch(e) { alert('Ungültiges JSON: ' + e.message); }
}
```

- [ ] **Step 4: Update `showScreen` to load both configs and simplify the nav button**

Find:

```js
function showScreen(name) {
  if (isDesktop) return;
  ['listings','stats','config','log'].forEach(s => {
    document.getElementById('screen-' + s).classList.toggle('active', s === name);
    document.getElementById('nav-' + s).classList.toggle('active', s === name);
  });
  if (name === 'config') loadConfig();
  if (name === 'log')    loadLog();
}
```

Replace with:

```js
function showScreen(name) {
  if (isDesktop) return;
  ['listings','stats','config','log'].forEach(s => {
    document.getElementById('screen-' + s).classList.toggle('active', s === name);
    document.getElementById('nav-' + s).classList.toggle('active', s === name);
  });
  if (name === 'config') { loadConfig('kleinanzeigen'); loadConfig('howoge'); }
  if (name === 'log')    loadLog();
}
```

Find the bottom-nav config button:

```html
  <button class="nav-item" id="nav-config" onclick="showScreen('config'); loadConfig()">
```

Replace with:

```html
  <button class="nav-item" id="nav-config" onclick="showScreen('config')">
```

- [ ] **Step 5: Update the desktop config tab builder**

Find (inside `showDesktopTab`):

```js
  } else if (name === 'config') {
    const r = await fetch('/api/config');
    const cfg = await r.json();
    dc.innerHTML = `
      <div style="padding:20px">
        <div class="config-info" style="margin-bottom:12px">
          Nach dem Speichern Crawler neu starten:<br>
          <code>docker compose restart</code>
        </div>
        <textarea id="config-json" spellcheck="false" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;color:var(--text);font-size:12px;font-family:'DM Mono',monospace;line-height:1.7;outline:none;min-height:400px;resize:vertical">${JSON.stringify(cfg, null, 2)}</textarea>
        <div style="margin-top:12px">
          <button class="action-btn primary" onclick="saveConfig()" style="max-width:200px">💾 Speichern</button>
        </div>
      </div>`;
  } else if (name === 'log') {
```

Replace with:

```js
  } else if (name === 'config') {
    const r = await fetch('/api/config');
    const cfg = await r.json();
    const rh = await fetch('/api/config?source=howoge');
    const cfgHowoge = await rh.json();
    dc.innerHTML = `
      <div style="padding:20px">
        <div class="config-info" style="margin-bottom:12px">
          Nach dem Speichern Crawler neu starten:<br>
          <code>docker compose restart</code>
        </div>
        <h2 style="font-size:11px;letter-spacing:0.1em;color:var(--muted);margin-bottom:6px">KLEINANZEIGEN</h2>
        <textarea id="config-json" spellcheck="false" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;color:var(--text);font-size:12px;font-family:'DM Mono',monospace;line-height:1.7;outline:none;min-height:300px;resize:vertical">${JSON.stringify(cfg, null, 2)}</textarea>
        <div style="margin-top:12px">
          <button class="action-btn primary" onclick="saveConfig('kleinanzeigen')" style="max-width:200px">💾 Speichern</button>
        </div>
        <h2 style="font-size:11px;letter-spacing:0.1em;color:var(--muted);margin:20px 0 6px">HOWOGE</h2>
        <textarea id="config-json-howoge" spellcheck="false" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px;color:var(--text);font-size:12px;font-family:'DM Mono',monospace;line-height:1.7;outline:none;min-height:300px;resize:vertical">${JSON.stringify(cfgHowoge, null, 2)}</textarea>
        <div style="margin-top:12px">
          <button class="action-btn primary" onclick="saveConfig('howoge')" style="max-width:200px">💾 Speichern</button>
        </div>
      </div>`;
  } else if (name === 'log') {
```

- [ ] **Step 6: Manual verification**

```bash
DATA_DIR=/tmp/howoge_smoke_data python3 app/dashboard.py &
PID=$!
sleep 2
curl -s http://localhost:5000/ | grep -o 'id="config-json-howoge"' | head -1
curl -s "http://localhost:5000/api/config?source=howoge" | python3 -m json.tool | head -10
kill "$PID"
```

Expected: the first `grep` finds the new textarea id in the served HTML; the `curl` to `/api/config?source=howoge` returns the HOWOGE config JSON (the `DEFAULT_CONFIG` seeded by Task 1/6 if `howoge_crawler.py` already ran once against `/tmp/howoge_smoke_data`). Then open `http://localhost:5000` in a browser, go to the config screen, and confirm both textareas load and save independently (edit one, save, reload, confirm only that one changed).

- [ ] **Step 7: Commit**

```bash
git add app/templates/index.html
git commit -m "Add HOWOGE config editor to dashboard UI"
```

---

## Post-implementation checklist (not a task — a reminder for whoever ships this)

- Update `README.md`'s field table / architecture notes if it documents `config.json` fields (out of scope for this plan since the current repo state wasn't checked for a README field table describing HOWOGE — verify manually before shipping).
- Deploy per `CLAUDE.md`: `git push origin main` then `ssh homeserver 'cd /opt/wohnungsmonitor-docker && git pull && docker compose up -d --build'`.
- After deploy, check `docker compose logs -f` for the new `howoge_crawler` output alongside the existing `crawler`/`dashboard` logs, since all three still log to the same container's stdout.
