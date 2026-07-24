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
