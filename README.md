# 🌦️ MQTT Weather Station – CI/CD Project

Dieses Projekt simuliert eine oder mehrere **MQTT-Wetterstationen**, die periodisch Messdaten senden, sowie ein **Dashboard**, das diese Daten validiert, auswertet und live darstellt.

Der Fokus des Projekts liegt auf einer **sauberen CI/CD-Pipeline mit GitHub Actions**, inklusive Tests, Linting und automatischem Docker-Image-Publish.

---

## 🚀 Features

- 📡 MQTT-basierte Wetterstation (Simulation)
- 📊 Live-Dashboard mit:
  - Temperatur & Luftfeuchtigkeit
  - Status (OK / STALE / INVALID / OFFLINE)
  - 5-Minuten-Durchschnitt
- ✅ Datenvalidierung & Fehlererkennung
- 🧪 Automatisierte Tests mit `pytest`
- 🧹 Code-Qualität mit `flake8`
- 🐳 Docker-Image wird automatisch auf Docker Hub veröffentlicht
- 🔐 CI/CD über GitHub Actions

---

## 🧱 Projektstruktur (vereinfacht)

```
mqtt-weather-project/
├── stations/
│   └── Dockerfile
├── station1.py
├── weather_client.py
├── test_weather_client.py
├── setup.cfg
└── docker-compose.yml (optional, lokal)
.github/
└── workflows/
    └── ci-cd.yml
```

---

## 🔄 CI/CD Pipeline (Kurzüberblick)

### Pull Request auf `main`

- ✔ Lint (flake8)
- ✔ Tests (pytest)
- ✔ Security Scan (Bandit)
- ❌ **Kein Docker Push**

### Push auf `main`

- ✔ Lint → Tests → Security Scan
- 🐳 Docker Build & Push nach Docker Hub  
  → `larsstalder/mqtt-weather-station`
- 🔎 Container-Scan mit Trivy

---

## 🐳 Docker Image

Das Image wird automatisch veröffentlicht unter:

```
larsstalder/mqtt-weather-station
```

Beispiel:

```bash
docker pull larsstalder/mqtt-weather-station
```

---

## 🧪 Tests lokal ausführen

```bash
cd mqtt-weather-project
pytest
```

---

## 🧹 Lint lokal ausführen

```bash
flake8 mqtt-weather-project
```

---

## ⚙️ Konfiguration (Environment Variablen)

| Variable      | Beschreibung          | Default     |
| ------------- | --------------------- | ----------- |
| `BROKER_HOST` | MQTT Broker Host      | `localhost` |
| `BROKER_PORT` | MQTT Broker Port      | `1883`      |
| `TOPIC`       | MQTT Topic            | `weather`   |
| `STATION_ID`  | ID der Wetterstation  | `WS-XX`     |
| `INTERVAL`    | Sendeintervall (Sek.) | `5`         |

---

## 🧠 Lernziele

- Aufbau einer klar strukturierten CI/CD-Pipeline
- Trennung von Lint, Tests und Build
- Automatisches Publizieren von Docker Images
- Sicherer Umgang mit Secrets (Docker Hub Token)
- Praxisnahe Anwendung von Python, Docker und GitHub Actions

---

## 📌 Hinweis

Dieses Projekt wurde im Rahmen eines Ausbildungsmoduls erstellt  
mit Fokus auf **Verständnis, Wartbarkeit und Automatisierung**.
