# MQTT Wetterstationen

Dieses Projekt simuliert verteilte Wetterstationen, die Messdaten über MQTT an einen Broker senden.

## Setup

1. Starte alles mit:
   ```bash
   docker-compose up --build
   ```

2. Öffne ein neues Terminal und starte den Subscriber:
   ```bash
   cd subscriber
   pip install -r requirements.txt
   python main.py
   ```

## Aufgabe

- Abonniere Wetterdaten vom Topic `weather`
- Speichere die Daten sinnvoll (z. B. JSON-Datei, SQLite)
- Erstelle einfache Auswertungen (Durchschnitt, Verlauf, usw.)


TUTORIAL: https://www.emqx.com/en/blog/how-to-use-mqtt-in-python



Zusatz: A & B umgesetzt


docker Token: ?



used here ci-cd.yml, bandit.yml, setup.cfg

test_weather_client.py
