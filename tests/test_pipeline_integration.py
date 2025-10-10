from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import pytest
import requests

import anomaly_detector
import ingestion_service


@dataclass
class FakeResponse:
    status_code: int
    payload: Any

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}", response=self)


class FakeCoordinator:
    def __init__(self):
        self.kv: Dict[str, Any] = {}
        self.thresholds = {
            "default": dict(anomaly_detector.DEFAULT_THRESHOLD_SET),
            "per_floor": {},
        }

    def put(self, url: str, json: Dict[str, Any], timeout: float | None = None):
        assert "value" in json, "ingestor must wrap payload under 'value'"
        key = url.split("/key/", 1)[1]
        self.kv[key] = json["value"]
        return FakeResponse(200, {"status": "ok"})

    def get(self, url: str, timeout: float | None = None):
        if url.endswith("/admin/thresholds"):
            return FakeResponse(200, self.thresholds)
        if "/key/" in url:
            key = url.split("/key/", 1)[1]
            if key not in self.kv:
                return FakeResponse(404, {"detail": "not found"})
            return FakeResponse(200, self.kv[key])
        return FakeResponse(404, {"detail": "unsupported"})


@pytest.fixture
def fake_coord(monkeypatch: pytest.MonkeyPatch) -> FakeCoordinator:
    coord = FakeCoordinator()

    def fake_put(url: str, json: Dict[str, Any], timeout: float | None = None):
        return coord.put(url, json, timeout)

    def fake_get(url: str, timeout: float | None = None):
        return coord.get(url, timeout)

    monkeypatch.setattr(ingestion_service.requests, "put", fake_put)
    monkeypatch.setattr(ingestion_service.requests, "get", fake_get)
    monkeypatch.setattr(anomaly_detector.requests, "get", fake_get)
    return coord


def test_ingestion_and_detection_integration(fake_coord: FakeCoordinator):
    base_url = "http://fake-coordinator"
    sample = {
        "piano": 1,
        "sensore2_kw": 15.0,
        "sensore3_corrente": 20.0,
        "sensore3_tensione": 248.0,
        "delta_kwh": 0.25,
        "timestamp": "2024-05-20T10:00:00+00:00",
    }

    piano, ts = ingestion_service.handle_reading(base_url, sample, index_size=10)

    assert piano == 1
    assert ts == sample["timestamp"]
    assert f"energy:p1:{ts}" in fake_coord.kv
    assert "energy:p1:latest" in fake_coord.kv
    assert "energy:p1:index" in fake_coord.kv
    assert fake_coord.kv["energy:p1:index"]["timestamps"] == [ts]

    thresholds_cfg = anomaly_detector.get_thresholds(base_url)
    thresholds = anomaly_detector.resolve_thresholds_for_floor(thresholds_cfg, 1)
    latest = anomaly_detector.get_latest_floor(base_url, 1)
    assert latest is not None

    events = anomaly_detector.check_anomalies(latest, thresholds)
    kinds = {event["tipo"] for event in events}
    assert "kw_over" in kinds
    assert "amp_over" in kinds
