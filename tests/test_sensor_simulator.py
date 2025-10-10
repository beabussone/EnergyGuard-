import math
from datetime import datetime, timezone

import pytest

import sensor_simulator


def test_diurnal_profile_midday_higher_than_night():
    midnight = sensor_simulator.diurnal_profile(0.0)
    midday = sensor_simulator.diurnal_profile(12.0)
    assert midday > midnight
    expected = 1.0 + 0.75 * math.sin((12.0 - 7.0) * math.pi / 12.0)
    assert midday == pytest.approx(expected, abs=1e-6)


def test_weekday_adjust_weekend_lower_load():
    workday = sensor_simulator.weekday_adjust(2)  # Wednesday
    weekend = sensor_simulator.weekday_adjust(6)  # Sunday
    assert workday > weekend
    assert workday == pytest.approx(1.02, abs=1e-6)
    assert weekend == pytest.approx(0.88, abs=1e-6)


def test_baseline_kw_for_floor_respects_floor_baseline(monkeypatch):
    dt = datetime(2024, 5, 20, 12, 0, tzinfo=timezone.utc)  # Monday midday
    monkeypatch.setitem(sensor_simulator.BASE_KW, 1, 5.0)
    expected = (
        5.0
        * sensor_simulator.diurnal_profile(dt.hour + dt.minute / 60.0)
        * sensor_simulator.weekday_adjust(dt.weekday())
    )
    assert sensor_simulator.baseline_kw_for_floor(1, dt) == pytest.approx(expected, rel=1e-6)
