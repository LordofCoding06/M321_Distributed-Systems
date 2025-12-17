import time, warnings, json, threading
from datetime import datetime, timezone
from typing import Any, Dict
from collections import defaultdict, deque

import paho.mqtt.client as mqtt
warnings.filterwarnings("ignore", category=DeprecationWarning)

from rich.live import Live
from rich.table import Table
from rich import box

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC = "weather"

STALE_AFTER_SECONDS = 30

def validate(temp, hum):
    errs = []
    try:
        t = float(temp)
        if t == -999 or t < -50 or t > 60:
            errs.append(f"invalid temperature {t}")
    except Exception:
        errs.append(f"temperature not a number: {temp}")
    try:
        h = float(hum)
        if h < 0 or h > 100:
            errs.append(f"invalid humidity {h}")
    except Exception:
        errs.append(f"humidity not a number: {hum}")
    return (len(errs) == 0, errs)

def parse_iso(ts):
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _fmt(v, unit=""):
    if isinstance(v, (int, float)):
        return f"{float(v):.1f}{(' ' + unit) if unit else ''}" 
    return "n/a" if v is None else str(v)

class App:
    def __init__(self):
        self.client = mqtt.Client(client_id="weather_step5", clean_session=True, protocol=mqtt.MQTTv311)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.outage_log = []

        self.lock = threading.Lock()
        self.stations: Dict[str, Dict[str, Any]] = {}

    def _to_local_date(self, dt_utc: datetime) -> str:
        "Convert UTC datetime to local date string YYYY-MM-DD"
        return dt_utc.astimezone().strftime("%Y-%m-%d")
    
    def _to_local_hour(self, dt_utc: datetime) -> str:
        "Convert UTC datetime to local hour string YYYY-MM-DD HH:00"
        return dt_utc.astimezone().strftime("%Y-%m-%d %H:00")
    
    def _float_or_none(self, x):
        try:
            return float(x)
        except Exception:
            return None
    
    def _update_daily(self, s: dict, recv_at_utc: datetime, t: float|None, h: float|None):
        day = self._to_local_date(recv_at_utc)
        d = s["daily"]
        if d["date"] != day:
            d["date"] = day
            d["t_min"] = d["t_max"] = d["h_min"] = d["h_max"] = None
        if t is not None:
            d["t_min"] = t if d["t_min"] is None else min(d["t_min"], t)
            d["t_max"] = t if d["t_max"] is None else max(d["t_max"], t)
        if h is not None:
            d["h_min"] = h if d["h_min"] is None else min(d["h_min"], h)
            d["h_max"] = h if d["h_max"] is None else max(d["h_max"], h)

    def _update_hourly(self, s: dict, recv_at_utc: datetime, t: float|None, h: float|None):
        hour_key = self._to_local_hour(recv_at_utc)
        agg = s["hourly"][hour_key]
        agg["count"] += 1
        if t is not None:
            agg["t_sum"] += t
            agg["t_min"] = t if agg["t_min"] is None else min(agg["t_min"], t)
            agg["t_max"] = t if agg["t_max"] is None else max(agg["t_max"], t)
        if h is not None:
            agg["h_sum"] += h
            agg["h_min"] = h if agg["h_min"] is None else min(agg["h_min"], h)
            agg["h_max"] = h if agg["h_max"] is None else max(agg["h_max"], h)

    def _avg_last_minutes(self, s: dict, minutes: int = 5) -> tuple[str, str]:
        if not s["buffer"]:
            return ("n/a", "n/a")
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - minutes*60
        t_vals, h_vals = [], []
        for ts, t, h in reversed(s["buffer"]):
            if ts.timestamp() < cutoff:
                break
            if isinstance(t, (int, float)):
                t_vals.append(float(t))
            if isinstance(h, (int, float)):
                h_vals.append(float(h))
        t_avg = f"{(sum(t_vals)/len(t_vals)):.1f}" if t_vals else "n/a"
        h_avg = f"{(sum(h_vals)/len(h_vals)):.1f}" if h_vals else "n/a"
        return (t_avg, h_avg)
    
    def _hourly_lines(self, s: dict, last_hours: int = 6) -> list[str]:
        if not s["hourly"]:
            return []
        keys = sorted(s["hourly"].keys())[-last_hours:]
        lines = []
        for k in keys:
            a = s["hourly"][k]
            if a["count"] == 0:
                lines.append(f"{k[-2:]} : 0")
                continue
            t_avg = f"{(a['t_sum']/a['count']):.1f}C" if a['t_sum'] else "n/a"
            h_avg = f"{(a['h_sum']/a['count']):.1f}%" if a['h_sum'] else "n/a"
            lines.append(f"{k[-2:]}: {a['count']} / {t_avg} / {h_avg}")
        return lines
    
    def on_connect(self, client, userdata, flags, rc, properties=None):
        rc_val = getattr(rc, "value", rc)
        print(f"[MQTT] Connected rc={rc_val}")
        if rc_val == 0:
            client.subscribe(TOPIC, qos=1)
            client.subscribe(f"{TOPIC}/#", qos=1)
            print(f"[MQTT] Subscribed to '{TOPIC}' and '{TOPIC}/#'")
        else:
            print(f"[MQTT] Connect failed rc={rc_val}")

    def on_disconnect(self, client, userdata, rc, properties=None):
        rc_val = getattr(rc, "value", rc)
        if rc_val != 0:
            print(f"[MQTT] Unexpected disconnect (rc={rc_val}), attempting reconnect...")
        else:
            print(f"[MQTT] Disconnected (rc={rc_val})")

    def on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="ignore"))
        except Exception:
            print(f"[{msg.topic}] <invalid JSON>")
            return

        if not hasattr(self, "_printed_keys_once"):
            print(f"[debug] first payload keys on {msg.topic}: {list(data.keys())}")
            self._printed_keys_once = True

        sid = data.get("stationId")
        if not isinstance(sid, str) or not sid.strip():
            print(f"[debug] missing stationId in payload on {msg.topic}: {data}")
            return

        temp = data.get("temperature")
        hum  = data.get("humidity")
        ts   = data.get("timestamp")

        ok, errs = validate(temp, hum)
        with self.lock:
            s = self.stations.setdefault(sid, {
                "temperature": None, "humidity": None,
                "payload_ts": None, "recv_at": None,
                "valid": False, "errors": ["first seen"],
                "outage_active": False,
                "buffer": deque(maxlen=2000),
                "daily": {
                    "date": None,
                    "t_min": None, "t_max": None,
                    "h_min": None, "h_max": None,
                },
                "hourly": defaultdict(lambda: {"count": 0, "t_sum": 0.0, "h_sum": 0.0,
                                               "t_min": None, "t_max": None,
                                               "h_min": None, "h_max": None}),
            })
            s["temperature"] = temp
            s["humidity"]   = hum
            s["payload_ts"] = parse_iso(ts)
            s["recv_at"]    = datetime.now(timezone.utc)
            s["valid"]      = ok
            s["errors"]     = errs

        t_f = self._float_or_none(temp)
        h_f = self._float_or_none(hum)
        s["buffer"].append((s["recv_at"], t_f, h_f))
        self._update_daily(s, s["recv_at"], t_f, h_f)
        self._update_hourly(s, s["recv_at"], t_f, h_f)

        print(f"[ingest] {sid}: temp={temp}, hum={hum}, ts={ts}, valid={ok}")

    def start(self):
        self.client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def render(self):
        table = Table(title="MQTT Wetterdashboard", box=box.SIMPLE_HEAVY)
        for col in ["Station","Temp","Humidity","Ø5m T","Ø5m H","Payload TS (UTC)","Last Seen (UTC)","Status","Hinweis"]:
            table.add_column(col, style="bold" if col=="Station" else None, overflow="fold")

        now = datetime.now(timezone.utc)
        with self.lock:
            for sid in sorted(self.stations.keys()):
                s = self.stations[sid]
                last = s["recv_at"]

                if last is None:
                    status, note = "OFFLINE", "no data received yet"
                else:
                    age = (now - last).total_seconds()
                    if age > STALE_AFTER_SECONDS:
                        status, note = "STALE", f">{STALE_AFTER_SECONDS}s no update"
                    elif not s["valid"]:
                        status, note = "INVALID", "; ".join(s["errors"]) if s["errors"] else "invalid data"
                    else:
                        status, note = "OK", "all good"

                t_avg, h_avg = self._avg_last_minutes(s, minutes=5)

                d = s.get("daily", {})
                if d and d.get("date"):
                    d_part = (
                        f" | {d['date']} Tmin/Tmax: "
                        f"{_fmt(d.get('t_min'), '°C')} / {_fmt(d.get('t_max'), '°C')}, "
                        f"Hmin/Hmax: {_fmt(d.get('h_min'), '%')} / {_fmt(d.get('h_max'), '%')}"
                    )
                    base = note if note != "all good" else "-"
                    note = (base + d_part).lstrip(" |")

                prev = s.get("outage_active", False)
                now_outage = (status in ("STALE", "OFFLINE"))

                if now_outage and not prev:
                    s["outage_active"] = True
                    entry = {"station": sid, "start": datetime.now(timezone.utc), "end": None}
                    self.outage_log.append(entry)
                    print(f"\a[ALARM] {sid} outage START at {entry['start'].isoformat()}")  # \a = Terminal-Beep
                    try:
                        import winsound
                        winsound.Beep(880, 300)
                    except Exception:
                        pass

                elif (not now_outage) and prev:
                    s["outage_active"] = False
                    for e in reversed(self.outage_log):
                        if e["station"] == sid and e["end"] is None:
                            e["end"] = datetime.now(timezone.utc)
                            dur = (e["end"] - e["start"]).total_seconds()
                            print(f"[ALARM] {sid} outage END at {e['end'].isoformat()} (duration {dur:.1f}s)")
                            break
                
                t = _fmt(s["temperature"], "°C")
                h = _fmt(s["humidity"], "%")
                ts_payload = s["payload_ts"].isoformat(timespec="seconds") if s["payload_ts"] else "n/a"
                last_seen  = s["recv_at"].isoformat(timespec="seconds") if s["recv_at"] else "n/a"
                hourly_lines = self._hourly_lines(s, last_hours=6)
                if hourly_lines:
                    note = (note + " | H: " + " ; ".join(hourly_lines)).strip()
                table.add_row(sid, t, h, t_avg, h_avg, ts_payload, last_seen, status, note)
        return table

def main():
    app = App(); app.start()
    try:
        with Live(app.render(), refresh_per_second=4, screen=False) as live:
            while True:
                time.sleep(0.5)
                live.update(app.render())
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()

if __name__ == "__main__":
    main()