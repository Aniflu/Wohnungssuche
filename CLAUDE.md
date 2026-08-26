# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Wohnungsmonitor: an apartment-listing crawler (kleinanzeigen.de + gewobag.de + howoge.de) with a Flask web dashboard, meant to run as a single Docker container on a home server. No test suite, no build step, no linter configured.

## Architecture

Three long-running Python processes started by `docker-entrypoint.sh` inside one container, communicating only through JSON files on a shared volume (`/data`, mounted from `./data` via `docker-compose.yml`) — there is no database and no in-process communication between them:

- **`app/crawler.py`** — background loop (`run_crawler()`). Polls each configured kleinanzeigen/Gewobag search every `check_interval_seconds` (±30% jitter), diffs against `data/listings.json`, writes new/updated listings back. Pauses during "Nachtruhe" (22:00–06:00, hardcoded) and instead runs a once-nightly housekeeping pass at `housekeeping_hour` that checks every stored listing's URL and drops ones that are gone/deactivated (aborts the whole pass if >30% look gone at once, to avoid wiping everything out on a captcha/IP-block false signal).
- **`app/gewobag.py`** — gewobag.de-specific fetch/parse/alive-check functions (`fetch_gewobag_search()`, `parse_gewobag_listings()`, `check_gewobag_alive()`), mirroring `crawler.py`'s kleinanzeigen-specific equivalents. Kept as a separate module so site-specific scraping logic stays isolated from the shared orchestration (config loading, merging, housekeeping loop, Nachtruhe) in `crawler.py`. Gewobag has its own config file (`data/gewobag_config.json`, loaded via `load_gewobag_config()`) and its own dashboard tab, but still shares `crawler.py`'s process, main loop and `listings.json` — see "Multi-source dispatch" below.
- **`app/howoge_crawler.py`** — a **fully separate** background process/loop for howoge.de, not a source dispatched from `crawler.py` (deliberately different from how Gewobag was integrated — see "Two different multi-source patterns" below). Has its own config (`data/howoge_config.json`), its own listings file (`data/howoge_listings.json`), its own log (`data/howoge_crawler.log`), and shares zero code with `crawler.py`. Runs the same Nachtruhe/±30%-jitter shape as `crawler.py`, but re-derives it independently. Housekeeping is simpler here: HOWOGE's search endpoint always returns its *entire* current listing stock regardless of filters, so an unfiltered fetch each cycle is enough to diff against and drop stale entries — no separate nightly pass needed (same >30%-abort safety net as `crawler.py`, though).
- **`app/dashboard.py`** — Flask app on `:5000`, serves `app/templates/index.html` and a small JSON API (`/api/listings`, `/api/stats`, `/api/config`, `/api/log`, `/api/mark_seen`, `/api/delete/<id>`) that reads/writes the `data/*.json` files. No auth — meant for local network only. `/api/listings` and `/api/stats` are source-agnostic with respect to kleinanzeigen/Gewobag (both already merged into one `listings.json` by `crawler.py`) but **do** know about HOWOGE specifically: they merge `listings.json` + `howoge_listings.json` (sorted by `found_at`, newest first, across both). `/api/delete/<id>` and `/api/mark_seen` route by the `howoge-` ID prefix to the right file. `/api/config` and `/api/log` take an optional `?source=` query param (`kleinanzeigen` (default), `gewobag`, or `howoge`) to target the matching config/log file — `CONFIG_FILES_BY_SOURCE` in `dashboard.py` maps it.

### Two different multi-source patterns — don't mix them up

This repo grew two sources (Gewobag, HOWOGE) added at different times via genuinely different architectures — know which one you're extending before copying a pattern:
- **Gewobag** = own module (`app/gewobag.py`) *and* own config file (`data/gewobag_config.json`, own dashboard tab), but dispatched *inside* `crawler.py`'s existing process/loop/`listings.json`. `run_crawler()` loads both `config.json` (kleinanzeigen) and `gewobag_config.json` (Gewobag) each cycle and iterates both searches lists in the same loop/`listings.json`/housekeeping pass. No new container process, no new listings file.
- **HOWOGE** = own module *and* own process (`app/howoge_crawler.py`), own `docker-entrypoint.sh` entry, own `data/howoge_*` files (config, listings, log), merged into the dashboard only at the `dashboard.py` API layer. A bug/crash in the HOWOGE crawler cannot take down kleinanzeigen/Gewobag crawling (or vice versa), at the cost of a third always-running process and the dashboard needing source-aware merge/routing logic.

If adding a fourth source, decide explicitly which pattern fits before writing code — see `docs/superpowers/specs/2026-07-24-howoge-crawler-design.md` for the reasoning behind picking the separate-process pattern for HOWOGE (short version: user preference for stronger isolation, decided during brainstorming).

### Multi-source dispatch (`source` field)

`config.json["searches"]` holds kleinanzeigen searches; `gewobag_config.json["searches"]` holds Gewobag ones — two separate files/dashboard tabs, `run_crawler()` loads both each cycle and iterates both lists into the same `listings.json` via `merge_listings()`. (A legacy per-search `"source": "gewobag"` field inside `config.json` is still honored by the search-loop dispatch for backward compat, but new Gewobag searches belong in `gewobag_config.json`.) Every listing dict carries a `"source"` value (`kleinanzeigen`/`gewobag`/`howoge`) regardless of which config file it came from — `run_housekeeping()`'s per-listing loop dispatches on *that* field to call `check_listing_alive()` or `check_gewobag_alive()`. Gewobag listing IDs are prefixed `gewobag-` (the detail-page URL slug, e.g. `gewobag-6011-31046-0409-0382`) so they can never collide with kleinanzeigen's plain numeric ad IDs in the single shared `listings.json`/`existing_ids` set used by `merge_listings()`.

Unlike kleinanzeigen.de, gewobag.de's search results are plain server-rendered HTML (WordPress) with no observed bot-protection — `parse_gewobag_listings()` scrapes `article.gw-offer` blocks directly, and pagination is followed via the actual `a.next.page-numbers` link found in each page's HTML (its path differs from page 1's, so it's not constructed manually) rather than a fixed pattern. `fetch_gewobag_search()` still issues a fresh `requests.get()` per page as a conservative default, even though no session-reuse issue has actually been observed there (see the kleinanzeigen note below for why that discipline matters once bot protection *is* present).

`check_gewobag_alive()`'s "alive" signal is `class="angebot-image"` — that class only appears on a listing's own detail page (verified: absent from search-result pages, which instead use `angebot-title`/`angebot-slider` on the *card*, not the single-listing view). A removed/expired listing 404s directly (verified against a nonexistent slug) rather than redirecting, unlike kleinanzeigen's redirect-to-homepage behavior.

Data files (`/data`, not in git):
- `config.json` — kleinanzeigen searches + tuning knobs (`check_interval_seconds`, `max_listings_stored`, `housekeeping_hour`, ...), auto-created from `DEFAULT_CONFIG` in `crawler.py` on first run if missing.
- `gewobag_config.json` — Gewobag searches only (`bezirke`, `zimmer_von`/`bis`, ...), auto-created from `DEFAULT_GEWOBAG_CONFIG` in `crawler.py` on first run if missing. No tuning knobs of its own — `check_interval_seconds`/`max_listings_stored`/`housekeeping_hour` still come from `config.json` since Gewobag shares `crawler.py`'s loop. `run_crawler()` calls `load_config()`/`load_gewobag_config()` unconditionally at the very top of its `while True` loop, *before* the Nachtruhe check — a container that starts/restarts during quiet hours must still create both files immediately rather than only once the active (non-quiet) branch eventually runs. (Bug once seen in practice: `gewobag_config.json` was only being created from the active branch, so a deploy during Nachtruhe left it missing and the dashboard's Gewobag tab looked empty until 06:00.)
- `listings.json` — all known kleinanzeigen/Gewobag listings, capped at `max_listings_stored`, newest first.
- `housekeeping_state.json` — last date housekeeping ran, so it only runs once/night.
- `crawler.log` — plain log file, tailed by both `docker compose logs` and `/api/log`.
- `howoge_config.json` — HOWOGE searches + `check_interval_seconds`, auto-created from `DEFAULT_CONFIG` in `howoge_crawler.py` on first run if missing. Separate schema from `config.json` (see below), no `max_listings_stored` (HOWOGE's whole stock is small enough not to need a cap).
- `howoge_listings.json` — all known HOWOGE listings, IDs prefixed `howoge-<uid>`.
- `howoge_crawler.log` — HOWOGE crawler's own log file, tailed via `docker compose logs` and `/api/log?source=howoge`.

### Request pattern: no shared `requests.Session`

kleinanzeigen.de runs Akamai bot protection: a *reused* `requests.Session` gets correct results on its first request only — every subsequent request on that same session comes back HTTP 200 but with zero listings (silently, no error), because the `_abck` cookie set on the first response marks the session as a bot without real browser JS execution. Verified by direct reproduction (3/3 fresh sessions succeeded; every session-reuse attempt failed on the 2nd request). Because of this, `fetch_search()` and `check_listing_alive()` deliberately use `requests.get()` fresh per call rather than a session passed down from `run_crawler()`. **Do not reintroduce a shared session for kleinanzeigen.de calls** — it will silently zero out results after the first cycle following every container restart, which is hard to notice since requests still return 200.

### Listing filters happen in two stages

kleinanzeigen's own search filters (room count `zimmer_d`, `swap_s`) don't distinguish "whole apartment" from "WG-Zimmer" (single room in a shared flat) or "Zwischenmiete"/"Untermiete" (temporary sublet) — both are filed under the same "Wohnung mieten" category with the whole apartment's room count attached. So filtering happens in `fetch_search()` after `parse_listings()`, in order:
1. `exclude_title_keywords` (default: `gesuch`, `gesucht`, `suche `, `suchen `, `sucht `) — matched against the **title only**, via `matched_exclude_keyword()`. Catches *Gesuche* (someone looking for a flat), which kleinanzeigen mixes into Angebote results non-deterministically: verified in `crawler.log` that the exact same URL returned Gesuche on 2026-08-25 but only Angebote when re-fetched later, and that adding `anzeige:angebote` to the URL changed nothing (identical ID sets). So this is filtered client-side, not via the URL. Title-only on purpose — "Sie suchen …" is common marketing text in normal listings' descriptions and would cause mass false positives.
2. `exclude_keywords` (default: `wg-zimmer`, `wg zimmer`, `in wg`, `private room`, `privatzimmer`, `zwischenmiete`, `untermiete`, `befristet`, `befristung`, `nachmiete`, `auf zeit`, `zeitmiete`, `zwischenmieter`, `temporär`, `temporaer`, `sublease`, `sublet`, ` months`, `eine woche`, ` a week`, `möbliert`, `moebliert`, `furnished`) — substring match against `title + description`. Configurable per-search, falls back to `DEFAULT_EXCLUDE_KEYWORDS` if the config doesn't set it.

Both lists are compared after `_normalize()` (lowercase + `ä→ae`/`ö→oe`/`ü→ue`/`ß→ss`), so keyword and listing text match regardless of umlaut spelling. Two deliberate choices, both validated against the 102 real stored listings: `nachmiete` (not `nachmieter`) so `(Nachmiete)` is caught too — all 7 listings that matched only via description were genuine Nachmieter handovers, zero false positives; and `möbliert`/`furnished` excluded wholesale (user's call, 2026-08-26) because on kleinanzeigen furnished is almost always short-term — the accepted cost is that a genuinely unlimited furnished flat gets dropped with it. ` monate` was deliberately **left out**: it would false-positive on "3 Monate Kaution" in normal listings.
3. `max_distance_km` — post-filter using a regex extracted from the location string (kleinanzeigen's own radius filter is coarser than this).

If tightening these filters further, test against real stored data first (dump `title`+`description` from `data/listings.json` and check which entries would be newly excluded) — keyword filters are prone to false positives like "4-Zimmer-Wohnung ... WG geeignet" (a normal whole-apartment listing that merely mentions it'd suit a shared household) being wrongly caught by a bare `"wg"` match.

## Config fields (`data/config.json`, per search)

See README.md's field table for the full list. Notable ones beyond kleinanzeigen's native URL params: `max_distance_km` and `exclude_keywords` are both client-side post-filters, not passed to kleinanzeigen's URL.

Gewobag searches (`"source": "gewobag"`) use a different, district-based field set instead: `bezirke` (list of Gewobag district slugs, taken straight from their site's own filter URL, e.g. `pankow`, `pankow-prenzlauer-berg`, `friedrichshain-kreuzberg-friedrichshain`), `zimmer_von`/`zimmer_bis`, `gesamtmiete_von`/`gesamtmiete_bis`, `gesamtflaeche_von`/`gesamtflaeche_bis`, `objekttyp` (default `["wohnung"]`). `max_distance_km` doesn't apply (Gewobag has no radius concept, only districts); `exclude_keywords` still works if set, but defaults to `[]` since Gewobag's own "Wohnung" category is curated and doesn't mix in WG-Zimmer/Zwischenmiete the way kleinanzeigen's does.

There is no config-editing UI beyond the dashboard's raw JSON textarea (`/api/config`, no schema validation) — adding a Gewobag search means pasting a new entry into that textarea (or editing `data/config.json` directly on the server) and restarting.

HOWOGE has its own separate config file (`data/howoge_config.json`, not `config.json`) with a third field set: `kiez` (list of Berlin Bezirk names, passed to HOWOGE's API server-side), `wbs` (`"ja"`/`"nein"`, passed server-side too), `min_rooms`/`max_rooms` (client-side post-filter — HOWOGE's own API only accepts a single exact room count, not a range, so `howoge_crawler.py` fetches unfiltered on rooms and filters locally). The dashboard's config screen has a second textarea for this file (`/api/config?source=howoge`), next to the kleinanzeigen/Gewobag one.

## Commands

No build/lint/test tooling exists in this repo — verify changes with `python3 -m py_compile app/*.py` and manual runs.

```bash
# Local dev run (needs DATA_DIR env var or it defaults to /data, which likely isn't writable locally)
DATA_DIR=./data python3 app/crawler.py
DATA_DIR=./data python3 app/howoge_crawler.py
DATA_DIR=./data python3 app/dashboard.py

# Docker (see README.md for the full command list)
docker compose up -d --build
docker compose logs -f
docker compose restart
```

## Deployment

This repo is developed locally but **runs on a separate home server**, not this machine. The server is reachable via the SSH alias `homeserver` and has its own git clone at `/opt/wohnungsmonitor-docker` (same GitHub remote as local: `github.com/Aniflu/Wohnungssuche`), running via `docker compose` as the `wohnungsmonitor` container. To ship a change:

```bash
git push origin main   # from this local repo
ssh homeserver 'cd /opt/wohnungsmonitor-docker && git pull && docker compose up -d --build'
```

There's also an inactive/legacy `wohnungsmonitor-crawler`/`wohnungsmonitor-dashboard` systemd setup at `/home/robs/wohnungsmonitor` on the server — not the live deployment, ignore it unless told otherwise.

Because of the Nachtruhe window (22:00–06:00), a container restart during quiet hours does **not** trigger an immediate crawl — it just re-enters the sleep-until-06:00 branch. This applies independently to both `crawler.py` and `howoge_crawler.py` (separate processes, separate Nachtruhe checks). To force a one-off fetch outside the normal schedule, call the relevant module's functions directly inside the running container (`docker exec wohnungsmonitor python3 -c "..."`, importing from `/app/app` — `import crawler` or `import howoge_crawler`) rather than restarting.
