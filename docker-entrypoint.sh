#!/bin/sh
# Startet Crawler und Dashboard parallel im selben Container

echo "═══════════════════════════════════════"
echo "  Wohnungsmonitor starting..."
echo "═══════════════════════════════════════"

# Crawler im Hintergrund starten
python /app/app/crawler.py &
CRAWLER_PID=$!

# Dashboard im Vordergrund (hält den Container am Leben)
python /app/app/dashboard.py &
DASHBOARD_PID=$!

# Auf Beendigung warten und beide Prozesse sauber stoppen
wait_and_shutdown() {
    echo "Shutting down..."
    kill $CRAWLER_PID $DASHBOARD_PID 2>/dev/null
    wait $CRAWLER_PID $DASHBOARD_PID 2>/dev/null
    exit 0
}

trap wait_and_shutdown SIGTERM SIGINT

# Warten bis einer der Prozesse beendet
wait
