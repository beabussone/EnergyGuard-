import anomaly_detector


def test_normalize_thresholds_payload_accepts_flat_dict():
    payload = {
        "delta_kwh_max": 0.2,
        "kw_max": 8.0,
        "corrente_max": 10.0,
        "tensione_max": 220.0,
    }
    normalized = anomaly_detector._normalize_thresholds_payload(payload)
    assert normalized["default"]["kw_max"] == 8.0
    assert normalized["per_floor"] == {}


def test_resolve_thresholds_for_floor_prefers_override():
    cfg = anomaly_detector._normalize_thresholds_payload({
        "default": anomaly_detector.DEFAULT_THRESHOLD_SET,
        "per_floor": {1: {"kw_max": 5.0}},
    })
    resolved = anomaly_detector.resolve_thresholds_for_floor(cfg, 1)
    assert resolved["kw_max"] == 5.0
    fallback = anomaly_detector.resolve_thresholds_for_floor(cfg, 2)
    assert fallback["kw_max"] == anomaly_detector.DEFAULT_THRESHOLD_SET["kw_max"]


def test_check_anomalies_emits_events_when_threshold_exceeded():
    sample = {
        "sensore2_kw": 15.0,
        "sensore3_corrente": 20.0,
        "sensore3_tensione": 250.0,
        "delta_kwh": 0.3,
    }
    thresholds = anomaly_detector.DEFAULT_THRESHOLD_SET
    events = anomaly_detector.check_anomalies(sample, thresholds)
    kinds = {event["tipo"] for event in events}
    assert {"kw_over", "amp_over", "volt_over", "dkwh_over"}.issubset(kinds)
