"""Tests for blackroad-sensor-network."""
import pytest, math
from sensor_network import SensorNetwork, Sensor, SensorReading


@pytest.fixture
def net(tmp_path):
    n = SensorNetwork(db_path=str(tmp_path / "test.db"))
    n.register_sensor(Sensor("t1", "temperature", "room_a", "°C",
                             calibration_offset=0.5, min_expected=-10, max_expected=50))
    n.register_sensor(Sensor("h1", "humidity", "room_a", "%",
                             min_expected=0, max_expected=100))
    return n


def test_record_and_get_latest(net):
    net.record_reading("t1", 22.0)
    r = net.get_latest("t1")
    assert r is not None
    assert r.calibrated_value == pytest.approx(22.5)


def test_calibration_applied(net):
    net.record_reading("t1", 20.0)
    r = net.get_latest("t1")
    assert r.calibrated_value == pytest.approx(20.5)


def test_time_series(net):
    for v in [18, 19, 20, 21, 22]:
        net.record_reading("t1", float(v))
    ts = net.get_time_series("t1", hours=1)
    assert len(ts) >= 5


def test_anomaly_insufficient_data(net):
    net.record_reading("t1", 22.0)
    result = net.detect_anomaly("t1")
    assert result["anomaly"] is False
    assert result["reason"] == "insufficient_data"


def test_anomaly_detected(net):
    for _ in range(40):
        net.record_reading("t1", 22.0)
    net.record_reading("t1", 100.0)  # extreme outlier
    result = net.detect_anomaly("t1")
    assert result["anomaly"] is True
    assert abs(result["z_score"]) > 2.5


def test_threshold_alert_high(net):
    net.alert_on_threshold("t1", min_val=10, max_val=30)
    net.record_reading("t1", 50.0)  # calibrated = 50.5, above 30
    alerts = net.get_alerts("t1", unacknowledged_only=True)
    assert any(a.alert_type == "threshold_high" for a in alerts)


def test_threshold_alert_low(net):
    net.alert_on_threshold("t1", min_val=15, max_val=50)
    net.record_reading("t1", 5.0)  # calibrated = 5.5, below 15
    alerts = net.get_alerts("t1")
    assert any(a.alert_type == "threshold_low" for a in alerts)


def test_aggregate_by_location(net):
    net.record_reading("t1", 22.0)
    net.record_reading("h1", 55.0)
    agg = net.aggregate_by_location()
    assert "room_a" in agg
    assert "t1" in agg["room_a"]["readings"]


def test_suspect_quality_out_of_range(net):
    reading = net.record_reading("t1", 200.0)  # calibrated 200.5, above max 50
    assert reading.quality == "suspect"


def test_unknown_sensor_raises(net):
    with pytest.raises(ValueError, match="not found"):
        net.record_reading("unknown", 10.0)
