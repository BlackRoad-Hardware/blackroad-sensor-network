# blackroad-sensor-network

> **Production-grade IoT sensor aggregator** — readings, time-series, Z-score anomaly detection, location aggregation, and threshold alerting.

Part of [BlackRoad-Hardware](https://github.com/BlackRoad-Hardware) — IoT & hardware intelligence platform.

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: Proprietary](https://img.shields.io/badge/license-Proprietary-red.svg)](#license)
[![Tests](https://img.shields.io/badge/tests-pytest-green.svg)](#testing)

---

## Table of Contents

1. [Platform Overview](#platform-overview)
2. [Features](#features)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Quick Start](#quick-start)
6. [API Reference](#api-reference)
   - [SensorNetwork](#sensornetwork)
   - [Sensor](#sensor)
   - [SensorReading](#sensorreading)
   - [Alert](#alert)
7. [Configuration](#configuration)
8. [Anomaly Detection](#anomaly-detection)
9. [Threshold Alerts](#threshold-alerts)
10. [Location Aggregation](#location-aggregation)
11. [Architecture](#architecture)
12. [Testing](#testing)
13. [Billing & Stripe Integration](#billing--stripe-integration)
14. [BlackRoad Platform Index](#blackroad-platform-index)
15. [Contributing](#contributing)
16. [License](#license)

---

## Platform Overview

`blackroad-sensor-network` is the data-ingestion backbone of the BlackRoad Hardware platform. It collects sensor readings from any IoT device, stores them in a local SQLite database with WAL durability, and exposes a clean Python API for real-time querying, anomaly detection, and alerting.

---

## Features

- **Multi-sensor support** — temperature, humidity, pressure, CO₂, motion, light, and custom types
- **Calibration offsets** — per-sensor hardware calibration applied at read time
- **Time-series storage** — indexed SQLite with WAL mode for concurrent access
- **Z-score anomaly detection** — sliding-window baseline with configurable threshold
- **Threshold alerting** — per-sensor min/max rules stored in the database
- **Location aggregation** — roll-up statistics across sensor groups by physical location
- **Quality tagging** — readings outside expected range are automatically marked `suspect`
- **Thread-safe** — global write lock protects all mutations
- **Self-initialising** — database and indexes are created automatically on first run

---

## Requirements

- Python 3.9 or higher
- No external runtime dependencies (stdlib only)
- `pytest >= 7.0.0` for the test suite

---

## Installation

```bash
# Clone the repository
git clone https://github.com/BlackRoad-Hardware/blackroad-sensor-network.git
cd blackroad-sensor-network

# Install test dependencies
pip install -r requirements.txt
```

---

## Quick Start

```python
from sensor_network import SensorNetwork, Sensor

net = SensorNetwork()          # creates sensor_network.db automatically

# Register a sensor
temp = Sensor(
    id="s-temp-01",
    type="temperature",
    location="warehouse_a",
    unit="°C",
    calibration_offset=0.5,
    min_expected=-20,
    max_expected=60,
)
net.register_sensor(temp)

# Record a reading
reading = net.record_reading("s-temp-01", 22.0)
print(reading.calibrated_value)   # 22.5

# Detect anomalies
result = net.detect_anomaly("s-temp-01")
print(result["anomaly"], result.get("z_score"))
```

Run the built-in demo:

```bash
python sensor_network.py
```

---

## API Reference

### SensorNetwork

The main entry point. Pass `db_path` to use a custom database location.

```python
net = SensorNetwork(db_path="sensor_network.db")
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `register_sensor` | `(sensor: Sensor) → Sensor` | Upsert a sensor record |
| `get_sensor` | `(sensor_id: str) → Optional[Sensor]` | Fetch a sensor by ID |
| `list_sensors` | `(location?, sensor_type?) → List[Sensor]` | Filter sensors by location and/or type |
| `record_reading` | `(sensor_id, value, timestamp?) → SensorReading` | Persist a raw reading; applies calibration and checks thresholds |
| `get_latest` | `(sensor_id) → Optional[SensorReading]` | Most recent reading for a sensor |
| `get_time_series` | `(sensor_id, hours=1.0) → List[SensorReading]` | Readings for the last N hours |
| `detect_anomaly` | `(sensor_id, window=60, threshold=2.5) → dict` | Z-score anomaly check |
| `aggregate_by_location` | `() → Dict[str, dict]` | Latest readings keyed by location |
| `get_location_stats` | `(location, sensor_type, hours=1.0) → dict` | Mean, min, max, range for a location/type slice |
| `alert_on_threshold` | `(sensor_id, min_val?, max_val?) → None` | Set or update threshold rule |
| `get_alerts` | `(sensor_id?, unacknowledged_only=False) → List[Alert]` | Query stored alerts |
| `acknowledge_alert` | `(alert_id: int) → None` | Mark an alert as acknowledged |

### Sensor

```python
@dataclass
class Sensor:
    id: str
    type: str               # "temperature" | "humidity" | "pressure" | "co2" | "motion" | "light"
    location: str           # physical location identifier, e.g. "warehouse_a"
    unit: str               # "°C" | "%" | "hPa" | "ppm" | "lux"
    calibration_offset: float = 0.0
    min_expected: float = -999.0
    max_expected: float = 999.0
    active: bool = True
    firmware: str = "1.0.0"
    created_at: str         # ISO-8601 UTC timestamp
```

### SensorReading

```python
@dataclass
class SensorReading:
    sensor_id: str
    raw_value: float
    calibrated_value: float   # raw_value + calibration_offset
    unit: str
    timestamp: str            # ISO-8601 UTC
    quality: str              # "good" | "suspect"
```

### Alert

```python
@dataclass
class Alert:
    sensor_id: str
    alert_type: str    # "threshold_high" | "threshold_low" | "anomaly"
    value: float
    message: str
    ts: str            # ISO-8601 UTC
    acknowledged: bool
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DB_PATH` | `sensor_network.db` | SQLite database file path |
| `window` | `60` | Number of recent readings used as the Z-score baseline |
| `threshold` | `2.5` | Standard-deviation threshold for anomaly classification |

Change the database path at construction time:

```python
net = SensorNetwork(db_path="/data/prod/sensors.db")
```

---

## Anomaly Detection

The Z-score detector uses a sliding window of the most-recent `window` readings as a statistical baseline and flags the latest reading as an anomaly when:

```
|z_score| = |(latest − mean) / std| > threshold
```

```python
result = net.detect_anomaly("s-temp-01", window=60, threshold=2.5)
# {
#   "sensor_id": "s-temp-01",
#   "anomaly": True,
#   "latest_value": 55.0,
#   "mean": 20.32,
#   "std": 0.98,
#   "z_score": 35.39,
#   "threshold": 2.5,
#   "timestamp": "2026-03-01T00:00:00"
# }
```

When an anomaly is detected, an `Alert` of type `"anomaly"` is automatically persisted.

---

## Threshold Alerts

Register min/max bounds for any sensor. Every `record_reading` call checks the rule automatically.

```python
net.alert_on_threshold("s-temp-01", min_val=10.0, max_val=35.0)

net.record_reading("s-temp-01", 40.0)   # triggers threshold_high alert

alerts = net.get_alerts("s-temp-01", unacknowledged_only=True)
net.acknowledge_alert(alerts[0].id)      # mark as handled
```

---

## Location Aggregation

```python
# Latest reading per sensor, grouped by location
snapshot = net.aggregate_by_location()
# {"warehouse_a": {"sensors": ["s-temp-01", "s-hum-01"], "readings": {...}}}

# Statistical summary for a specific location + type slice
stats = net.get_location_stats("warehouse_a", "temperature", hours=24)
# {"location": "warehouse_a", "sensor_type": "temperature",
#  "count": 1440, "mean": 21.3, "min": 18.1, "max": 25.7, "range": 7.6}
```

---

## Architecture

```
sensor_network.py
├── Dataclasses          Sensor · SensorReading · Alert
├── Database layer       SQLite (WAL) — auto-init with indexes
│   ├── sensors          PRIMARY KEY id
│   ├── readings         INDEX (sensor_id, timestamp)
│   ├── alerts           FK → sensors
│   └── threshold_rules  PRIMARY KEY sensor_id
├── SensorNetwork        Thread-safe aggregator (global write lock)
│   ├── Sensor CRUD
│   ├── Readings ingestion + calibration
│   ├── Anomaly detection (Z-score)
│   ├── Location aggregation
│   └── Threshold alerting
└── demo()               Self-contained runnable demonstration
```

- **Pure Python** — no external runtime dependencies
- **SQLite WAL mode** — concurrent reads, serialised writes
- **Thread-safe** — `threading.Lock` guards all write paths
- **Self-initialising** — `init_db()` is called automatically on construction
- **Dataclass-based domain model** — typed, lightweight, easy to serialise

---

## Testing

```bash
# Install test dependencies
pip install -r requirements.txt

# Run the full test suite
pytest test_sensor_network.py -v
```

The test suite covers:

| Test | What it validates |
|------|-------------------|
| `test_record_and_get_latest` | Read ingestion and latest-value retrieval |
| `test_calibration_applied` | Calibration offset applied correctly |
| `test_time_series` | Time-windowed series retrieval |
| `test_anomaly_insufficient_data` | Graceful handling of sparse data |
| `test_anomaly_detected` | Z-score anomaly flagged for outlier readings |
| `test_threshold_alert_high` | High-threshold alert generated |
| `test_threshold_alert_low` | Low-threshold alert generated |
| `test_aggregate_by_location` | Location rollup includes all sensors |
| `test_suspect_quality_out_of_range` | Out-of-range readings tagged `suspect` |
| `test_unknown_sensor_raises` | `ValueError` raised for unknown sensor ID |

---

## Billing & Stripe Integration

BlackRoad Hardware platform subscriptions are managed via **Stripe**. If you are building a commercial deployment on top of this library, use the Stripe Python SDK to gate access by subscription tier:

```bash
pip install stripe
```

```python
import stripe
stripe.api_key = "sk_live_..."

# Verify active subscription before allowing data ingestion
subscription = stripe.Subscription.retrieve("sub_...")
if subscription.status != "active":
    raise PermissionError("Active Stripe subscription required")

net = SensorNetwork()
net.record_reading("s-temp-01", 22.0)
```

Refer to the [Stripe Python docs](https://stripe.com/docs/api?lang=python) for webhook handling, metered billing, and usage-based pricing.

---

## BlackRoad Platform Index

| Repository | Description |
|------------|-------------|
| [blackroad-smart-home](https://github.com/BlackRoad-Hardware/blackroad-smart-home) | Smart home controller — scenes, scheduling, device groups |
| **[blackroad-sensor-network](https://github.com/BlackRoad-Hardware/blackroad-sensor-network)** | **IoT sensor aggregator — this repo** |
| [blackroad-automation-hub](https://github.com/BlackRoad-Hardware/blackroad-automation-hub) | Rules engine — triggers, conditions, actions |
| [blackroad-energy-optimizer](https://github.com/BlackRoad-Hardware/blackroad-energy-optimizer) | Energy tracking — peak analysis, CO₂ equivalent |
| [blackroad-fleet-tracker](https://github.com/BlackRoad-Hardware/blackroad-fleet-tracker) | Fleet GPS tracking — geofencing, idle detection |

---

## Contributing

1. Fork the repository and create a feature branch.
2. Write or update tests in `test_sensor_network.py` to cover your change.
3. Run `pytest test_sensor_network.py -v` and confirm all tests pass.
4. Open a pull request with a clear description of the change and motivation.

---

## License

© BlackRoad OS, Inc. All rights reserved.
