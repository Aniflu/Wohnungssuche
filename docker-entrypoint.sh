#!/bin/sh
# Startet Crawler und Dashboard parallel im selben Container

echo "═══════════════════════════════════════"
echo "  Wohnungsmonitor starting..."
echo "═══════════════════════════════════════"

# Crawler im Hintergrund starten
python /app/app/crawler.py &
CRAWLER_PID=$!

# Dashboard im Hintergrund starten
python /app/app/dashboard.py &
DASHBOARD_PID=$!

shutdown() {
    echo "Shutting down..."
    kill "$CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
    wait "$CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
    exit 0
}
trap shutdown TERM INT

# Solange laufen lassen, bis EINER der beiden Prozesse endet (auch bei Crash).
# Danach den Container mit Fehlercode beenden, damit "restart: unless-stopped"
# beide Prozesse sauber neu startet, statt den toten Prozess unbemerkt liegen
# zu lassen.
while kill -0 "$CRAWLER_PID" 2>/dev/null && kill -0 "$DASHBOARD_PID" 2>/dev/null; do
    sleep 2
done

echo "Ein Prozess wurde beendet - Container wird gestoppt, damit ein Neustart erfolgen kann."
kill "$CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
wait "$CRAWLER_PID" "$DASHBOARD_PID" 2>/dev/null
exit 1
