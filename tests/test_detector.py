from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data_validator import validate_orders
from src.detector import DetectorConfig, ShippingAnomalyDetector, assign_severity
from src.generate_data import create_demo_orders


def test_rule_detection_flags_critical_delay(order_factory):
    validated = validate_orders(order_factory([0] * 19 + [16])).data
    result = ShippingAnomalyDetector(validated).rule_based_detection()
    critical = result.loc[result["订单ID"].eq("T-0019")].iloc[0]
    assert critical["rule_anomaly"] == 1
    assert bool(critical["critical_rule"])
    assert "14 天" in critical["rule_reason"]


def test_zscore_threshold_changes_real_results(order_factory):
    delays = [0] * 20 + [1] * 8 + [2] * 5 + [3, 4, 5, 7, 10, 14, 20]
    validated = validate_orders(order_factory(delays)).data
    detector = ShippingAnomalyDetector(validated)
    counts = [
        int(detector.zscore_detection(threshold)["zscore_anomaly"].sum())
        for threshold in (1.5, 2.5, 4.0)
    ]
    assert counts[0] > counts[1] > counts[2]


def test_run_all_uses_configured_zscore_threshold(order_factory):
    delays = [0] * 20 + [1] * 8 + [2] * 5 + [3, 4, 5, 7, 10, 14, 20]
    validated = validate_orders(order_factory(delays)).data
    counts = []
    for threshold in (1.5, 2.5, 4.0):
        detector = ShippingAnomalyDetector(
            validated, config=DetectorConfig(zscore_threshold=threshold)
        )
        counts.append(int(detector.run_all()["zscore_anomaly"].sum()))
    assert counts[0] > counts[1] > counts[2]


def test_isolation_forest_contamination_changes_predictions():
    validated = validate_orders(create_demo_orders(n_orders=400)).data
    counts = []
    for contamination in (0.02, 0.08, 0.18):
        detector = ShippingAnomalyDetector(
            validated,
            config=DetectorConfig(iforest_contamination=contamination),
        )
        counts.append(int(detector.isolation_forest_detection()["iforest_anomaly"].sum()))
    assert counts[0] < counts[1] < counts[2]


@pytest.mark.parametrize("value", [0, -0.01, 0.5, 0.8, np.nan, np.inf])
def test_invalid_contamination_is_rejected(value):
    with pytest.raises(ValueError, match="0 < contamination < 0.5"):
        DetectorConfig(iforest_contamination=value)


def test_small_groups_and_constant_variance_are_safe(order_factory):
    validated = validate_orders(order_factory([2, 2, 2, 2, 2, 2])).data
    result = ShippingAnomalyDetector(validated).zscore_detection()
    assert result["z_score"].notna().all()
    assert int(result["zscore_anomaly"].sum()) == 0


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"final_anomaly": 0, "critical_rule": False, "anomaly_votes": 0, "实际延误天数": 0}, "正常"),
        ({"final_anomaly": 1, "critical_rule": False, "anomaly_votes": 1, "实际延误天数": 2}, "轻微"),
        ({"final_anomaly": 1, "critical_rule": False, "anomaly_votes": 2, "实际延误天数": 5}, "中等"),
        ({"final_anomaly": 1, "critical_rule": True, "anomaly_votes": 1, "实际延误天数": 16}, "严重"),
    ],
)
def test_severity_policy_is_central_and_deterministic(row, expected):
    assert assign_severity(pd.Series(row)) == expected


def test_ensemble_outputs_explanations(order_factory):
    validated = validate_orders(order_factory([0] * 19 + [16])).data
    result = ShippingAnomalyDetector(validated).run_all()
    anomaly = result.loc[result["订单ID"].eq("T-0019")].iloc[0]
    assert anomaly["final_anomaly"] == 1
    assert anomaly["severity"] == "严重"
    assert "业务规则" in anomaly["anomaly_methods"]
    assert 0 <= anomaly["anomaly_score"] <= 100
    assert "综合：3 种方法中" in anomaly["reason"]
