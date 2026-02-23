"""
blackroad-sensor-network — IoT Sensor Aggregator
Production: readings, time-series, Z-score anomaly detection, location aggregation, threshold alerts.
"""

from __future__ import annotations
import sqlite3
import json
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

DB_PATH = "sensor_network.db"
_LOCK = threading.Lock()


# ─────────────────────────── Dataclasses ────────────────────────────

@dataclass
class Sensor:
    id: str
    type: str            # temperature / humidity / pressure / co2 / motion / light
    location: str        # e.g. "warehouse_a", "living_room"
    unit: str            # °C, %, hPa, ppm, lux
    calibration_offset: float = 0.0
    min_expected: float = -999.0
    max_expected: float = 999.0
    active: bool = True
    firmware: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def calibrate(self, raw: float) -> float:
        return raw + self.calibration_offset


@dataclass
class SensorReading:
    sensor_id: str
    raw_value: float
    calibrated_value: float
    unit: str
    timestamp: str
    quality: str = "good"   # good / suspect / bad

    @classmethod
    def build(cls, sensor: Sensor, raw: float,
              ts: Optional[str] = None) -> "SensorReading":
        cal = sensor.calibrate(raw)
        quality = "good"
        if not sensor.min_expected <= cal <= sensor.max_expected:
            quality = "suspect"
        return cls(
            sensor_id=sensor.id, raw_value=raw,
            calibrated_value=cal, unit=sensor.unit,
            timestamp=ts or datetime.utcnow().isoformat(),
            quality=quality
        )


@dataclass
class Alert:
    sensor_id: str
    alert_type: str       # threshold_high / threshold_low / anomaly
    value: float
    message: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    acknowledged: bool = False


# ─────────────────────────── Database ───────────────────────────────

def _get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    with _get_conn(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sensors (
            id                  TEXT PRIMARY KEY,
            type                TEXT NOT NULL,
            location            TEXT NOT NULL,
            unit                TEXT NOT NULL,
            calibration_offset  REAL NOT NULL DEFAULT 0.0,
            min_expected        REAL NOT NULL DEFAULT -999.0,
            max_expected        REAL NOT NULL DEFAULT 999.0,
            active              INTEGER NOT NULL DEFAULT 1,
            firmware            TEXT NOT NULL DEFAULT '1.0.0',
            created_at          TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS readings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id           TEXT NOT NULL,
            raw_value           REAL NOT NULL,
            calibrated_value    REAL NOT NULL,
            unit                TEXT NOT NULL,
            timestamp           TEXT NOT NULL,
            quality             TEXT NOT NULL DEFAULT 'good',
            FOREIGN KEY(sensor_id) REFERENCES sensors(id)
        );
        CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts
            ON readings(sensor_id, timestamp);
        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id       TEXT NOT NULL,
            alert_type      TEXT NOT NULL,
            value           REAL NOT NULL,
            message         TEXT NOT NULL,
            ts              TEXT NOT NULL,
            acknowledged    INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(sensor_id) REFERENCES sensors(id)
        );
        CREATE TABLE IF NOT EXISTS threshold_rules (
            sensor_id   TEXT NOT NULL,
            min_val     REAL,
            max_val     REAL,
            PRIMARY KEY(sensor_id)
        );
        """)
    logger.info("sensor_network DB initialised at %s", db_path)


# ─────────────────────────── Aggregator ─────────────────────────────

class SensorNetwork:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        init_db(db_path)

    # ── Sensor CRUD ───────────────────────────────────────────────────

    def register_sensor(self, sensor: Sensor) -> Sensor:
        with _LOCK, _get_conn(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sensors VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sensor.id, sensor.type, sensor.location, sensor.unit,
                 sensor.calibration_offset, sensor.min_expected,
                 sensor.max_expected, int(sensor.active),
                 sensor.firmware, sensor.created_at)
            )
        return sensor

    def get_sensor(self, sensor_id: str) -> Optional[Sensor]:
        with _get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM sensors WHERE id=?", (sensor_id,)
            ).fetchone()
        if not row:
            return None
        return Sensor(
            id=row["id"], type=row["type"], location=row["location"],
            unit=row["unit"], calibration_offset=row["calibration_offset"],
            min_expected=row["min_expected"], max_expected=row["max_expected"],
            active=bool(row["active"]), firmware=row["firmware"],
            created_at=row["created_at"]
        )

    def list_sensors(self, location: Optional[str] = None,
                     sensor_type: Optional[str] = None) -> List[Sensor]:
        q = "SELECT * FROM sensors WHERE 1=1"
        params: list = []
        if location:
            q += " AND location=?"; params.append(location)
        if sensor_type:
            q += " AND type=?"; params.append(sensor_type)
        with _get_conn(self.db_path) as conn:
            rows = conn.execute(q, params).fetchall()
        return [
            Sensor(id=r["id"], type=r["type"], location=r["location"],
                   unit=r["unit"], calibration_offset=r["calibration_offset"],
                   min_expected=r["min_expected"], max_expected=r["max_expected"],
                   active=bool(r["active"]), firmware=r["firmware"],
                   created_at=r["created_at"])
            for r in rows
        ]

    # ── Readings ──────────────────────────────────────────────────────

    def record_reading(self, sensor_id: str, value: float,
                       timestamp: Optional[str] = None) -> SensorReading:
        sensor = self.get_sensor(sensor_id)
        if not sensor:
            raise ValueError(f"Sensor {sensor_id!r} not found")
        reading = SensorReading.build(sensor, value, timestamp)
        with _LOCK, _get_conn(self.db_path) as conn:
            conn.execute(
                "INSERT INTO readings "
                "(sensor_id, raw_value, calibrated_value, unit, timestamp, quality) "
                "VALUES (?,?,?,?,?,?)",
                (reading.sensor_id, reading.raw_value, reading.calibrated_value,
                 reading.unit, reading.timestamp, reading.quality)
            )
        # auto-check thresholds
        self._check_threshold(sensor_id, reading.calibrated_value)
        return reading

    def get_latest(self, sensor_id: str) -> Optional[SensorReading]:
        with _get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM readings WHERE sensor_id=? ORDER BY timestamp DESC LIMIT 1",
                (sensor_id,)
            ).fetchone()
        if not row:
            return None
        return SensorReading(
            sensor_id=row["sensor_id"], raw_value=row["raw_value"],
            calibrated_value=row["calibrated_value"], unit=row["unit"],
            timestamp=row["timestamp"], quality=row["quality"]
        )

    def get_time_series(self, sensor_id: str,
                        hours: float = 1.0) -> List[SensorReading]:
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with _get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM readings WHERE sensor_id=? AND timestamp>=? "
                "ORDER BY timestamp ASC",
                (sensor_id, since)
            ).fetchall()
        return [
            SensorReading(
                sensor_id=r["sensor_id"], raw_value=r["raw_value"],
                calibrated_value=r["calibrated_value"], unit=r["unit"],
                timestamp=r["timestamp"], quality=r["quality"]
            )
            for r in rows
        ]

    # ── Anomaly Detection (Z-score) ───────────────────────────────────

    def detect_anomaly(self, sensor_id: str, window: int = 60,
                       threshold: float = 2.5) -> Dict[str, Any]:
        """
        Sliding window Z-score anomaly detection.
        window = number of most-recent readings to use as baseline.
        threshold = how many standard deviations = anomaly.
        """
        with _get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT calibrated_value, timestamp FROM readings "
                "WHERE sensor_id=? ORDER BY timestamp DESC LIMIT ?",
                (sensor_id, window + 1)
            ).fetchall()

        if len(rows) < 5:
            return {"sensor_id": sensor_id, "anomaly": False, "reason": "insufficient_data",
                    "data_points": len(rows)}

        values = [r["calibrated_value"] for r in rows]
        latest = values[0]
        baseline = values[1:]  # exclude the latest

        n = len(baseline)
        mean = sum(baseline) / n
        variance = sum((v - mean) ** 2 for v in baseline) / n
        std = math.sqrt(variance) if variance > 0 else 0.0

        z_score = (latest - mean) / std if std > 0 else 0.0
        is_anomaly = abs(z_score) > threshold

        result = {
            "sensor_id": sensor_id,
            "anomaly": is_anomaly,
            "latest_value": latest,
            "mean": round(mean, 4),
            "std": round(std, 4),
            "z_score": round(z_score, 4),
            "threshold": threshold,
            "timestamp": rows[0]["timestamp"]
        }

        if is_anomaly:
            self._store_alert(
                sensor_id, "anomaly", latest,
                f"Z-score {z_score:.2f} exceeds threshold {threshold}"
            )
        return result

    # ── Location Aggregation ──────────────────────────────────────────

    def aggregate_by_location(self) -> Dict[str, Dict[str, Any]]:
        sensors = self.list_sensors()
        locations: Dict[str, Dict[str, Any]] = {}
        for sensor in sensors:
            loc = sensor.location
            if loc not in locations:
                locations[loc] = {"sensors": [], "readings": {}}
            latest = self.get_latest(sensor.id)
            locations[loc]["sensors"].append(sensor.id)
            if latest:
                locations[loc]["readings"][sensor.id] = {
                    "type": sensor.type,
                    "value": latest.calibrated_value,
                    "unit": latest.unit,
                    "quality": latest.quality,
                    "ts": latest.timestamp
                }
        return locations

    def get_location_stats(self, location: str,
                           sensor_type: str, hours: float = 1.0) -> Dict[str, Any]:
        sensors = self.list_sensors(location=location, sensor_type=sensor_type)
        all_values = []
        for s in sensors:
            ts = self.get_time_series(s.id, hours=hours)
            all_values.extend(r.calibrated_value for r in ts)
        if not all_values:
            return {"location": location, "type": sensor_type, "count": 0}
        n = len(all_values)
        mean = sum(all_values) / n
        minimum = min(all_values)
        maximum = max(all_values)
        return {
            "location": location, "sensor_type": sensor_type,
            "count": n, "mean": round(mean, 4),
            "min": minimum, "max": maximum,
            "range": round(maximum - minimum, 4)
        }

    # ── Threshold Alerts ──────────────────────────────────────────────

    def alert_on_threshold(self, sensor_id: str,
                           min_val: Optional[float],
                           max_val: Optional[float]) -> None:
        with _LOCK, _get_conn(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO threshold_rules VALUES (?,?,?)",
                (sensor_id, min_val, max_val)
            )

    def _check_threshold(self, sensor_id: str, value: float) -> None:
        with _get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM threshold_rules WHERE sensor_id=?", (sensor_id,)
            ).fetchone()
        if not row:
            return
        if row["min_val"] is not None and value < row["min_val"]:
            self._store_alert(sensor_id, "threshold_low", value,
                              f"Value {value} below min {row['min_val']}")
        if row["max_val"] is not None and value > row["max_val"]:
            self._store_alert(sensor_id, "threshold_high", value,
                              f"Value {value} above max {row['max_val']}")

    def _store_alert(self, sensor_id: str, alert_type: str,
                     value: float, message: str) -> None:
        ts = datetime.utcnow().isoformat()
        with _LOCK, _get_conn(self.db_path) as conn:
            conn.execute(
                "INSERT INTO alerts (sensor_id, alert_type, value, message, ts) "
                "VALUES (?,?,?,?,?)",
                (sensor_id, alert_type, value, message, ts)
            )
        logger.warning("ALERT [%s] %s: %s", alert_type, sensor_id, message)

    def get_alerts(self, sensor_id: Optional[str] = None,
                   unacknowledged_only: bool = False) -> List[Alert]:
        q = "SELECT * FROM alerts WHERE 1=1"
        params: list = []
        if sensor_id:
            q += " AND sensor_id=?"; params.append(sensor_id)
        if unacknowledged_only:
            q += " AND acknowledged=0"
        q += " ORDER BY ts DESC"
        with _get_conn(self.db_path) as conn:
            rows = conn.execute(q, params).fetchall()
        return [
            Alert(sensor_id=r["sensor_id"], alert_type=r["alert_type"],
                  value=r["value"], message=r["message"], ts=r["ts"],
                  acknowledged=bool(r["acknowledged"]))
            for r in rows
        ]

    def acknowledge_alert(self, alert_id: int) -> None:
        with _LOCK, _get_conn(self.db_path) as conn:
            conn.execute(
                "UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,)
            )


def demo() -> None:
    import os; os.remove(DB_PATH) if os.path.exists(DB_PATH) else None
    import random

    net = SensorNetwork()
    temp = Sensor("s-temp-01", "temperature", "warehouse_a", "°C",
                  calibration_offset=0.5, min_expected=-20, max_expected=60)
    hum  = Sensor("s-hum-01",  "humidity",    "warehouse_a", "%",
                  min_expected=0, max_expected=100)
    net.register_sensor(temp)
    net.register_sensor(hum)

    net.alert_on_threshold("s-temp-01", min_val=10, max_val=35)

    for i in range(50):
        v = 20 + random.gauss(0, 1)
        net.record_reading("s-temp-01", v)
    # inject anomaly
    net.record_reading("s-temp-01", 55.0)

    anomaly = net.detect_anomaly("s-temp-01")
    print(f"Anomaly: {anomaly['anomaly']} z={anomaly.get('z_score')}")

    for i in range(20):
        net.record_reading("s-hum-01", random.uniform(40, 70))

    print(net.aggregate_by_location())
    alerts = net.get_alerts()
    print(f"Total alerts: {len(alerts)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo()
