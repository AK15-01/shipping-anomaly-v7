from __future__ import annotations

from src.business_metrics import build_summary_stats, get_severity_count


def test_severity_and_anomaly_rate_are_single_source(metrics_result):
    stats = build_summary_stats(metrics_result)
    assert get_severity_count(stats, "严重") == 9
    assert stats["severity_counts"]["严重"] == 9
    assert stats["total_anomalies"] == 9
    assert stats["anomaly_rate"] == 0.9


def test_carrier_statistics_include_sample_size(metrics_result):
    stats = build_summary_stats(metrics_result)
    carrier_a = stats["carrier_stats"].loc[
        stats["carrier_stats"]["承运商"].eq("A")
    ].iloc[0]
    assert carrier_a["总订单数"] == 6
    assert carrier_a["异常订单数"] == 6
    assert carrier_a["异常率"] == 1.0
    assert carrier_a["样本提示"] == "样本充足"


def test_route_statistics_are_computed_from_current_data(metrics_result):
    stats = build_summary_stats(metrics_result)
    assert stats["route_stats"]["订单数"].sum() == 10
    assert stats["route_stats"]["异常数"].sum() == 9
    assert set(stats["route_stats"]["路线"]) == {"上海→新加坡", "宁波→汉堡"}
