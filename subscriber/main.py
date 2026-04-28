import json
import os
import sqlite3
import statistics
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def resolve_topic(template: str, team_no: str) -> str:
    return template.replace("{TEAM_NO}", team_no)


TEAM_NO = env("TEAM_NO", "team01")
MQTT_HOST = env("MQTT_HOST", "localhost")
MQTT_PORT = int(env("MQTT_PORT", "1883"))
TELEMETRY_TOPIC = resolve_topic(env("TELEMETRY_TOPIC", "{TEAM_NO}/telemetry"), TEAM_NO)
DB_PATH = env("DB_PATH", "subscriber/data/telemetry.db")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DB_FILE = Path(DB_PATH)
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

NUMERIC_FIELDS = [
    "sicaklik",
    "nem",
    "mq135_ppm_est",
    "mq7_ppm_est",
    "mq2_ppm_est",
    "fan_pwm",
    "decision_score",
    "trend_score",
]

db_lock = threading.Lock()
app = FastAPI(title="Akilli Ev Havalandirma Subscriber")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def init_db() -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                sicaklik REAL,
                nem REAL,
                mq135_ppm_est REAL,
                mq7_ppm_est REAL,
                mq2_ppm_est REAL,
                fan_on INTEGER,
                fan_pwm INTEGER,
                buzzer_on INTEGER,
                decision_score REAL,
                trend_score REAL,
                decision_mode TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(telemetry)").fetchall()
        }
        migrations = {
            "mq2_ppm_est": "ALTER TABLE telemetry ADD COLUMN mq2_ppm_est REAL",
            "buzzer_on": "ALTER TABLE telemetry ADD COLUMN buzzer_on INTEGER",
            "trend_score": "ALTER TABLE telemetry ADD COLUMN trend_score REAL",
        }
        for col_name, ddl in migrations.items():
            if col_name not in existing_cols:
                conn.execute(ddl)
        conn.commit()


@contextmanager
def db_conn():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def parse_payload(payload: Dict) -> Optional[Dict]:
    if not isinstance(payload, dict):
        return None
    values = payload.get("values", {})
    if not isinstance(values, dict):
        return None

    try:
        return {
            "sensor_id": str(payload.get("sensor_id", "unknown")),
            "timestamp": str(payload.get("timestamp", datetime.now(timezone.utc).isoformat())),
            "sicaklik": float(values.get("sicaklik", 0.0)),
            "nem": float(values.get("nem", 0.0)),
            "mq135_ppm_est": float(values.get("mq135_ppm_est", 0.0)),
            "mq7_ppm_est": float(values.get("mq7_ppm_est", 0.0)),
            "mq2_ppm_est": float(values.get("mq2_ppm_est", 0.0)),
            "fan_on": 1 if bool(values.get("fan_on", False)) else 0,
            "fan_pwm": int(values.get("fan_pwm", 0)),
            "buzzer_on": 1 if bool(values.get("buzzer_on", False)) else 0,
            "decision_score": float(values.get("decision_score", 0.0)),
            "trend_score": float(values.get("trend_score", 0.0)),
            "decision_mode": str(values.get("decision_mode", "auto")),
            "raw_json": json.dumps(payload, ensure_ascii=False),
        }
    except (TypeError, ValueError):
        return None


def save_payload(record: Dict) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO telemetry (
                sensor_id, timestamp, sicaklik, nem,
                mq135_ppm_est, mq7_ppm_est, mq2_ppm_est, fan_on, fan_pwm,
                buzzer_on, decision_score, trend_score, decision_mode, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["sensor_id"],
                record["timestamp"],
                record["sicaklik"],
                record["nem"],
                record["mq135_ppm_est"],
                record["mq7_ppm_est"],
                record["mq2_ppm_est"],
                record["fan_on"],
                record["fan_pwm"],
                record["buzzer_on"],
                record["decision_score"],
                record["trend_score"],
                record["decision_mode"],
                record["raw_json"],
            ),
        )
        conn.commit()


def mqtt_worker() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(c: mqtt.Client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"[subscriber] MQTT baglandi: {MQTT_HOST}:{MQTT_PORT}")
            print(f"[subscriber] Topic dinleniyor: {TELEMETRY_TOPIC}")
            c.subscribe(TELEMETRY_TOPIC, qos=1)
        else:
            print(f"[subscriber] MQTT baglanti hatasi: {reason_code}")

    def on_message(c: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("[subscriber] Gecersiz JSON alindi.")
            return
        record = parse_payload(payload)
        if not record:
            print("[subscriber] Eksik veya uyumsuz telemetry kaydi yoksayildi.")
            return
        save_payload(record)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


def rows_to_history(rows: List[sqlite3.Row]) -> List[Dict]:
    history = []
    for row in rows:
        history.append(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "sensor_id": row["sensor_id"],
                "values": {
                    "sicaklik": row["sicaklik"],
                    "nem": row["nem"],
                    "mq135_ppm_est": row["mq135_ppm_est"],
                    "mq7_ppm_est": row["mq7_ppm_est"],
                    "mq2_ppm_est": row["mq2_ppm_est"],
                    "fan_on": bool(row["fan_on"]),
                    "fan_pwm": row["fan_pwm"],
                    "buzzer_on": bool(row["buzzer_on"]),
                    "decision_score": row["decision_score"],
                    "trend_score": row["trend_score"],
                    "decision_mode": row["decision_mode"],
                },
            }
        )
    return history


@app.on_event("startup")
def startup_event():
    init_db()
    t = threading.Thread(target=mqtt_worker, daemon=True)
    t.start()
    print("[subscriber] Servis hazir.")


@app.get("/")
def index():
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/api/latest")
def api_latest():
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"status": "empty"}
        return {"status": "ok", "data": rows_to_history([row])[0]}


@app.get("/api/history")
def api_history(limit: int = 200):
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=400, detail="limit 1..2000 araliginda olmali")
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM telemetry ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ordered_rows = list(reversed(rows))
    return {"status": "ok", "count": len(ordered_rows), "data": rows_to_history(ordered_rows)}


@app.get("/api/stats")
def api_stats(limit: int = 500):
    if limit < 2 or limit > 5000:
        raise HTTPException(status_code=400, detail="limit 2..5000 araliginda olmali")
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM telemetry ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return {"status": "empty"}

    values_map: Dict[str, List[float]] = {name: [] for name in NUMERIC_FIELDS}
    fan_on_count = 0
    for row in rows:
        for key in NUMERIC_FIELDS:
            v = row[key]
            if v is not None:
                values_map[key].append(float(v))
        if row["fan_on"] == 1:
            fan_on_count += 1

    stats = {}
    for key, vals in values_map.items():
        if not vals:
            continue
        stats[key] = {
            "min": min(vals),
            "max": max(vals),
            "avg": statistics.mean(vals),
            "variance": statistics.variance(vals) if len(vals) > 1 else 0.0,
        }

    return {
        "status": "ok",
        "sample_count": len(rows),
        "fan_on_ratio": fan_on_count / len(rows),
        "metrics": stats,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
