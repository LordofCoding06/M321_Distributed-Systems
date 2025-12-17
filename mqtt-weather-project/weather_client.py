import os
import json
import time
import threading
import warnings
from datetime import datetime, timezone
from typing import Any, Dict
from collections import defaultdict, deque

import paho.mqtt.client as mqtt
from rich import box
from rich.live import Live
from rich.table import Table

warnings.filterwarnings("ignore", category=DeprecationWarning)

# MQTT Konfiguration (per ENV überschreibbar)
BROKER_HOST = os.getenv("BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", "1883"))
TOPIC = os.getenv("TOPIC", "weather")

STALE_AFTER_SECONDS = 30


def validate(temp, hum):
    """Validiert Temperatur und Luftfeuchtigkeit."""
    errors = []

    try:
        t = float(temp)
        if t == -999 or t < -50 or t > 60:
            errors.append(f"invalid temperature {t}")
    except Exception:
        errors.append(f"temperature not a number: {temp}")

    try:
        h = float(hum)
        if h < 0 or h > 100:
            errors.append(f"invalid humidity {h}")
    except Exception:
        errors.append(f"humidity not a number: {hum}")

    return len(errors) == 0, errors


def parse_iso(ts):
    """Parst ISO-8601 Timestamp in UTC."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fmt(value, unit=""):
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}{(' ' + unit) if unit else ''}"
    return "n/a" if value is None else str(value)


class App:
    def __init__(self):
        self.client = mqtt.Client(
            client_id="weather_dashboard",
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        self.lock = threading.Lock()
        self.stations: Dict[str, Dict[str, Any]] = {}
        self.outage_log = []

    def _to_local_date(self, dt_utc: datetime) -> str:
        return dt_utc.astimezone().strftime("%Y-%m-%d")

    def _to_local_hour(self, dt_utc: datetime) -> str:
        return dt_utc.astimezone().strftime("%Y-%m-%d %H:00")

    def _float_or_none(self, value):
        try:
            return float(value)
        except Exception:
            return None

    def _update_daily(self, station, recv_at, temp, hum):
        day = self._to_local_date(recv_at)
        daily = station["daily"]

        if daily["date"] != day:
            daily.update(
                {
                    "date": day,
                    "t_min": None,
                    "t_max": None,
                    "h_min": None,
                    "h_max": None,
                }
            )

        if temp is not None:
            daily["t_min"] = (
                temp if daily["t_min"] is None else min(daily["t_min"], temp)
            )
            daily["t_max"] = (
                temp if daily["t_max"] is None else max(daily["t_max"], temp)
            )

        if hum is not None:
            daily["h_min"] = hum if daily["h_min"] is None else min(daily["h_min"], hum)
            daily["h_max"] = hum if daily["h_max"] is None else max(daily["h_max"], hum)

    def _update_hourly(self, station, recv_at, temp, hum):
        key = self._to_local_hour(recv_at)
        hourly = station["hourly"][key]
        hourly["count"] += 1

        if temp is not None:
            hourly["t_sum"] += temp
            hourly["t_min"] = (
                temp if hourly["t_min"] is None else min(hourly["t_min"], temp)
            )
            hourly["t_max"] = (
                temp if hourly["t_max"] is None else max(hourly["t_max"], temp)
            )

        if hum is not None:
            hourly["h_sum"] += hum
            hourly["h_min"] = (
                hum if hourly["h_min"] is None else min(hourly["h_min"], hum)
            )
            hourly["h_max"] = (
                hum if hourly["h_max"] is None else max(hourly["h_max"], hum)
            )

    def _avg_last_minutes(self, station, minutes=5):
        if not station["buffer"]:
            return "n/a", "n/a"

        cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
        temps, hums = [], []

        for ts, t, h in reversed(station["buffer"]):
            if ts.timestamp() < cutoff:
                break
            if isinstance(t, (int, float)):
                temps.append(t)
            if isinstance(h, (int, float)):
                hums.append(h)

        t_avg = f"{sum(temps) / len(temps):.1f}" if temps else "n/a"
        h_avg = f"{sum(hums) / len(hums):.1f}" if hums else "n/a"
        return t_avg, h_avg

    def on_connect(self, client, userdata, flags, rc, properties=None):
        rc_val = getattr(rc, "value", rc)
        print(f"[MQTT] Connected rc={rc_val}")
        if rc_val == 0:
            client.subscribe(TOPIC, qos=1)
            client.subscribe(f"{TOPIC}/#", qos=1)

    def on_disconnect(self, client, userdata, rc, properties=None):
        rc_val = getattr(rc, "value", rc)
        print(f"[MQTT] Disconnected rc={rc_val}")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
        except Exception:
            return

        sid = payload.get("stationId")
        if not isinstance(sid, str):
            return

        temp = payload.get("temperature")
        hum = payload.get("humidity")
        ts = payload.get("timestamp")

        valid, errors = validate(temp, hum)
        recv_at = datetime.now(timezone.utc)

        with self.lock:
            station = self.stations.setdefault(
                sid,
                {
                    "temperature": None,
                    "humidity": None,
                    "payload_ts": None,
                    "recv_at": None,
                    "valid": False,
                    "errors": [],
                    "buffer": deque(maxlen=2000),
                    "daily": {
                        "date": None,
                        "t_min": None,
                        "t_max": None,
                        "h_min": None,
                        "h_max": None,
                    },
                    "hourly": defaultdict(
                        lambda: {
                            "count": 0,
                            "t_sum": 0.0,
                            "h_sum": 0.0,
                            "t_min": None,
                            "t_max": None,
                            "h_min": None,
                            "h_max": None,
                        }
                    ),
                },
            )

            station["temperature"] = temp
            station["humidity"] = hum
            station["payload_ts"] = parse_iso(ts)
            station["recv_at"] = recv_at
            station["valid"] = valid
            station["errors"] = errors

            t_f = self._float_or_none(temp)
            h_f = self._float_or_none(hum)
            station["buffer"].append((recv_at, t_f, h_f))
            self._update_daily(station, recv_at, t_f, h_f)
            self._update_hourly(station, recv_at, t_f, h_f)

    def start(self):
        self.client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def render(self):
        table = Table(title="MQTT Wetterdashboard", box=box.SIMPLE_HEAVY)
        for col in [
            "Station",
            "Temp",
            "Humidity",
            "Ø5m T",
            "Ø5m H",
            "Payload TS (UTC)",
            "Last Seen (UTC)",
            "Status",
        ]:
            table.add_column(col)

        now = datetime.now(timezone.utc)

        with self.lock:
            for sid, s in sorted(self.stations.items()):
                last = s["recv_at"]
                if not last:
                    status = "OFFLINE"
                elif (now - last).total_seconds() > STALE_AFTER_SECONDS:
                    status = "STALE"
                elif not s["valid"]:
                    status = "INVALID"
                else:
                    status = "OK"

                t_avg, h_avg = self._avg_last_minutes(s)
                table.add_row(
                    sid,
                    _fmt(s["temperature"], "°C"),
                    _fmt(s["humidity"], "%"),
                    t_avg,
                    h_avg,
                    (
                        s["payload_ts"].isoformat(timespec="seconds")
                        if s["payload_ts"]
                        else "n/a"
                    ),
                    (
                        s["recv_at"].isoformat(timespec="seconds")
                        if s["recv_at"]
                        else "n/a"
                    ),
                    status,
                )
        return table


def main():
    app = App()
    app.start()
    try:
        with Live(app.render(), refresh_per_second=4):
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


if __name__ == "__main__":
    main()
