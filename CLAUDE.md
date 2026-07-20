# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Wohnungsmonitor: a kleinanzeigen.de apartment-listing crawler with a Flask web dashboard, meant to run as a single Docker container on a home server. No test suite, no build step, no linter configured — it's three plain Python files plus a template.

## Architecture

Two long-running Python processes started by `docker-entrypoint.sh` inside one container, communicating only through JSON files on a shared volume (`/data`, mounted from `./data` via `docker-compose.yml`) — there is no database and no in-process communication between them:

- **`app/crawler.py`** — background loop (`run_crawler()`). Polls kleinanzeigen.de search results every `check_interval_seconds` (±30% jitter), diffs against `data/listings.json`, writes new/updated listings back. Pauses during "Nachtruhe" (22:00–06:00, hardcoded) and instead runs a once-nightly housekeeping pass at `housekeeping_hour` that checks every stored listing's URL and drops ones that are gone/deactivated (aborts the whole pass if >30% look gone at once, to avoid wiping everything out on a captcha/IP-block false signal).
- **`app/dashboard.py`** — Flask app on `:5000`, serves `app/templates/index.html` and a small JSON API (`/api/listings`, `/api/stats`, `/api/config`, `/api/log`, `/api/mark_seen`, `/api/delete/<id>`) that just reads/writes the same `data/*.json` files. No auth — meant for local network only.

Data files (`/data`, not in git):
- `config.json` — searches + tuning knobs, auto-created from `DEFAULT_CONFIG` in `crawler.py` on first run if missing.
- `listings.json` — all known listings, capped at `max_listings_stored`, newest first.
- `housekeeping_state.json` — last date housekeeping ran, so it only runs once/night.
- `crawler.log` — plain log file, tailed by both `docker compose logs` and `/api/log`.

### Request pattern: no shared `requests.Session`

kleinanzeigen.de runs Akamai bot protection: a *reused* `requests.Session` gets correct results on its first request only — every subsequent request on that same session comes back HTTP 200 but with zero listings (silently, no error), because the `_abck` cookie set on the first response marks the session as a bot without real browser JS execution. Verified by direct reproduction (3/3 fresh sessions succeeded; every session-reuse attempt failed on the 2nd request). Because of this, `fetch_search()` and `check_listing_alive()` deliberately use `requests.get()` fresh per call rather than a session passed down from `run_crawler()`. **Do not reintroduce a shared session for kleinanzeigen.de calls** — it will silently zero out results after the first cycle following every container restart, which is hard to notice since requests still return 200.

### Listing filters happen in two stages

kleinanzeigen's own search filters (room count `zimmer_d`, `swap_s`) don't distinguish "whole apartment" from "WG-Zimmer" (single room in a shared flat) or "Zwischenmiete"/"Untermiete" (temporary sublet) — both are filed under the same "Wohnung mieten" category with the whole apartment's room count attached. So filtering happens in `fetch_search()` after `parse_listings()`, in order:
1. `exclude_keywords` (default: `wg-zimmer`, `wg zimmer`, `in wg`, `zwischenmiete`, `untermiete`, `befristet`, `befristung`) — substring match against `title + description`, case-insensitive. Configurable per-search, falls back to `DEFAULT_EXCLUDE_KEYWORDS` if the config doesn't set it.
2. `max_distance_km` — post-filter using a regex extracted from the location string (kleinanzeigen's own radius filter is coarser than this).

If tightening these filters further, test against real stored data first (dump `title`+`description` from `data/listings.json` and check which entries would be newly excluded) — keyword filters are prone to false positives like "4-Zimmer-Wohnung ... WG geeignet" (a normal whole-apartment listing that merely mentions it'd suit a shared household) being wrongly caught by a bare `"wg"` match.

## Config fields (`data/config.json`, per search)

See README.md's field table for the full list. Notable ones beyond kleinanzeigen's native URL params: `max_distance_km` and `exclude_keywords` are both client-side post-filters, not passed to kleinanzeigen's URL.

## Commands

No build/lint/test tooling exists in this repo — verify changes with `python3 -m py_compile app/*.py` and manual runs.

```bash
# Local dev run (needs DATA_DIR env var or it defaults to /data, which likely isn't writable locally)
DATA_DIR=./data python3 app/crawler.py
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

Because of the Nachtruhe window (22:00–06:00), a container restart during quiet hours does **not** trigger an immediate crawl — it just re-enters the sleep-until-06:00 branch. To force a one-off fetch outside the normal schedule, call `crawler.py`'s functions directly inside the running container (`docker exec wohnungsmonitor python3 -c "..."`, importing from `/app/app`) rather than restarting.
