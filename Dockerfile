FROM python:3.12-slim

# Python-Output sofort ausgeben (nicht puffern) - wichtig für "docker compose logs -f"
ENV PYTHONUNBUFFERED=1

# Arbeitsverzeichnis
WORKDIR /app

# Abhängigkeiten zuerst (Docker-Layer-Cache nutzen)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Code kopieren
COPY app/ ./app/

# Daten-Verzeichnis vorbereiten (wird als Volume gemountet)
RUN mkdir -p /data

# Beide Prozesse starten über ein Startup-Script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/docker-entrypoint.sh"]
