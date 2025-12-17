import json
import os
import threading
import time
import warnings
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

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
    problems = []

    try:
        t = float(temp)
        if t == -999 or t < -50 or t > 60:
            problems.append(f"invalid temperature {t}")
    except Exception:
        problems.append(f"temperature not a number: {temp}")

    try:
        h = float(hum)
        if h < 0 or h > 100:
            problems.append(f"invalid humidity {h}")
    except Exception:
        problems.append(f"humidity not a number: {hum}")

    return len(problems) == 0, problems


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


def _fmt(value, unit: str = "") -> str:
    if isinstance(value, (int, float)):
        unit_suffix = f" {unit}" if unit else ""
        return f"{float(value):.1f}{unit_suffix}"
    return "n/a" if value is None else str(value)


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _default_hour_bucket() -> Dict[str, Any]:
    return {
        "count": 0,
        "t_sum": 0.0,
        "h_sum": 0.0,
        "t_min": None,
        "t_max": None,
        "h_min": None,
        "h_max": None,
    }


class App:
    def __init__(self) -> None:
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
        self.outage_log = []  # bleibt für spätere Erweiterungen

    @staticmethod
    def _local_day(dt_utc: datetime) -> str:
        return dt_utc.astimezone().strftime("%Y-%m-%d")

    @staticmethod
    def _local_hour_key(dt_utc: datetime) -> str:
        return dt_utc.astimezone().strftime("%Y-%m-%d %H:00")

    def _ensure_station(self, sid: str) -> Dict[str, Any]:
        return self.stations.setdefault(
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
                "hourly": defaultdict(_default_hour_bucket),
            },
        )

    def _update_daily(
        self,
        station: Dict[str, Any],
        recv_at: datetime,
        t: Optional[float],
        h: Optional[float],
    ) -> None:
        today = self._local_day(recv_at)
        daily = station["daily"]

        if daily["date"] != today:
            daily.update(
                {
                    "date": today,
                    "t_min": None,
                    "t_max": None,
                    "h_min": None,
                    "h_max": None,
                }
            )

        if t is not None:
            daily["t_min"] = t if daily["t_min"] is None else min(daily["t_min"], t)
            daily["t_max"] = t if daily["t_max"] is None else max(daily["t_max"], t)

        if h is not None:
            daily["h_min"] = h if daily["h_min"] is None else min(daily["h_min"], h)
            daily["h_max"] = h if daily["h_max"] is None else max(daily["h_max"], h)

    def _update_hourly(
        self,
        station: Dict[str, Any],
        recv_at: datetime,
        t: Optional[float],
        h: Optional[float],
    ) -> None:
        key = self._local_hour_key(recv_at)
        bucket = station["hourly"][key]
        bucket["count"] += 1

        if t is not None:
            bucket["t_sum"] += t
            bucket["t_min"] = t if bucket["t_min"] is None else min(bucket["t_min"], t)
            bucket["t_max"] = t if bucket["t_max"] is None else max(bucket["t_max"], t)

        if h is not None:
            bucket["h_sum"] += h
            bucket["h_min"] = h if bucket["h_min"] is None else min(bucket["h_min"], h)
            bucket["h_max"] = h if bucket["h_max"] is None else max(bucket["h_max"], h)

    def _avg_last_minutes(
        self, station: Dict[str, Any], minutes: int = 5
    ) -> Tuple[str, str]:
        buf = station.get("buffer")
        if not buf:
            return "n/a", "n/a"

        cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
        temps, hums = [], []

        for dt, t, h in reversed(buf):
            if dt.timestamp() < cutoff:
                break
            if isinstance(t, (int, float)):
                temps.append(t)
            if isinstance(h, (int, float)):
                hums.append(h)

        t_avg = f"{sum(temps) / len(temps):.1f}" if temps else "n/a"
        h_avg = f"{sum(hums) / len(hums):.1f}" if hums else "n/a"
        return t_avg, h_avg

    # --- MQTT callbacks ---
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

        ok, problems = validate(temp, hum)
        recv_at = datetime.now(timezone.utc)

        t_f = _to_float(temp)
        h_f = _to_float(hum)
        payload_dt = parse_iso(ts)

        with self.lock:
            station = self._ensure_station(sid)

            station["temperature"] = temp
            station["humidity"] = hum
            station["payload_ts"] = payload_dt
            station["recv_at"] = recv_at
            station["valid"] = ok
            station["errors"] = problems

            station["buffer"].append((recv_at, t_f, h_f))
            self._update_daily(station, recv_at, t_f, h_f)
            self._update_hourly(station, recv_at, t_f, h_f)

    # --- lifecycle ---
    def start(self) -> None:
        self.client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    # --- UI ---
    def _status_for(self, station: Dict[str, Any], now: datetime) -> str:
        last = station.get("recv_at")
        if not last:
            return "OFFLINE"
        if (now - last).total_seconds() > STALE_AFTER_SECONDS:
            return "STALE"
        if not station.get("valid", False):
            return "INVALID"
        return "OK"

    def render(self) -> Table:
        table = Table(title="MQTT Wetterdashboard", box=box.SIMPLE_HEAVY)
        headers = [
            "Station",
            "Temp",
            "Humidity",
            "Ø5m T",
            "Ø5m H",
            "Payload TS (UTC)",
            "Last Seen (UTC)",
            "Status",
        ]
        for h in headers:
            table.add_column(h)

        now = datetime.now(timezone.utc)

        with self.lock:
            for sid, station in sorted(self.stations.items()):
                status = self._status_for(station, now)
                t_avg, h_avg = self._avg_last_minutes(station)

                payload_ts = station.get("payload_ts")
                recv_at = station.get("recv_at")

                table.add_row(
                    sid,
                    _fmt(station.get("temperature"), "°C"),
                    _fmt(station.get("humidity"), "%"),
                    t_avg,
                    h_avg,
                    payload_ts.isoformat(timespec="seconds") if payload_ts else "n/a",
                    recv_at.isoformat(timespec="seconds") if recv_at else "n/a",
                    status,
                )

        return table


def main() -> None:
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
