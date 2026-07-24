#!/usr/bin/env python3
"""
Wohnungsmonitor – Flask Dashboard
Lauscht auf 0.0.0.0:5000 im Docker-Container.
"""

import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/listings")
def api_listings():
    listings    = load_all_listings()
    search_name = request.args.get("search")
    only_new    = request.args.get("new") == "1"
    query       = request.args.get("q", "").lower()

    if search_name:
        listings = [l for l in listings if l.get("search_name") == search_name]
    if only_new:
        listings = [l for l in listings if l.get("is_new")]
    if query:
        listings = [l for l in listings if query in " ".join([
            l.get("title", ""), l.get("description", ""), l.get("location", "")
        ]).lower()]

    return jsonify(listings)


@app.route("/api/stats")
def api_stats():
    listings  = load_all_listings()
    new_count = sum(1 for l in listings if l.get("is_new"))
    searches  = sorted({l.get("search_name", "–") for l in listings})
    last_found = listings[0].get("found_at") if listings else None
    return jsonify({
        "total":      len(listings),
        "new":        new_count,
        "searches":   searches,
        "last_found": last_found,
    })


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


@app.route("/api/log")
def api_log():
    source = request.args.get("source", "kleinanzeigen")
    log_file = HOWOGE_LOG_FILE if source == "howoge" else LOG_FILE

    if log_file.exists():
        lines = log_file.read_text().splitlines()[-200:]
        return jsonify(lines[::-1])
    return jsonify([])


@app.route("/api/delete/<listing_id>", methods=["DELETE"])
def delete_listing(listing_id):
    if listing_id.startswith("howoge-"):
        listings = [l for l in load_howoge_listings() if l.get("id") != listing_id]
        HOWOGE_LISTINGS_FILE.write_text(json.dumps(listings, indent=2, ensure_ascii=False))
    else:
        listings = [l for l in load_listings() if l.get("id") != listing_id]
        LISTINGS_FILE.write_text(json.dumps(listings, indent=2, ensure_ascii=False))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
