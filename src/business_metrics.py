"""Single source of truth for KPIs, severity, carrier, and route statistics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


SEVERITY_LEVELS = ("严重", "中等", "轻微", "正常")


def get_severity_count(stats: dict[str, Any], level: str) -> int:
    """Read a severity count through one stable interface."""

    counts = stats.get("severity_counts", {})
    return int(counts.get(level, 0))


def _add_smoothed_rate(
    grouped: pd.DataFrame,
    *,
    total_column: str,
    anomaly_column: str,
    global_rate: float,
    output_column: str = "调整后异常率",
    prior_strength: int = 10,
) -> pd.DataFrame:
    grouped[output_column] = (
        grouped[anomaly_column] + global_rate * prior_strength
    ) / (grouped[total_column] + prior_strength)
    grouped[output_column] = grouped[output_column].round(4)
    return grouped


def build_summary_stats(result: pd.DataFrame) -> dict[str, Any]:
    """Compute all displayed and exported business metrics from one result frame."""

    if not isinstance(result, pd.DataFrame) or result.empty:
        raise ValueError("无法从空的检测结果计算统计指标。")

    required = {
        "订单ID",
        "承运商",
        "路线",
        "运输方式",
        "计划发货日期",
        "发货年",
        "发货月",
        "实际延误天数",
        "计划运输天数",
        "final_anomaly",
        "severity",
        "rule_anomaly",
        "zscore_anomaly",
        "iforest_anomaly",
        "rule_reason",
        "anomaly_methods",
        "anomaly_score",
        "reason",
    }
    missing = sorted(required.difference(result.columns))
    if missing:
        raise ValueError(f"统计模块缺少字段：{'、'.join(missing)}")

    total_orders = int(len(result))
    anomaly_mask = result["final_anomaly"].astype(int).eq(1)
    anomalies = result.loc[anomaly_mask].copy()
    total_anomalies = int(anomaly_mask.sum())
    anomaly_rate = total_anomalies / total_orders

    raw_severity = result["severity"].value_counts().to_dict()
    severity_counts = {level: int(raw_severity.get(level, 0)) for level in SEVERITY_LEVELS}

    carrier_stats = (
        result.groupby("承运商", dropna=False)
        .agg(
            总订单数=("订单ID", "count"),
            平均延误天数=("实际延误天数", "mean"),
            延误率=("实际延误天数", lambda values: (values > 0).mean()),
            异常订单数=("final_anomaly", "sum"),
            严重异常数=("severity", lambda values: (values == "严重").sum()),
        )
        .reset_index()
    )
    carrier_stats["异常率"] = carrier_stats["异常订单数"] / carrier_stats["总订单数"]
    carrier_stats = _add_smoothed_rate(
        carrier_stats,
        total_column="总订单数",
        anomaly_column="异常订单数",
        global_rate=anomaly_rate,
    )
    carrier_stats["样本提示"] = np.where(
        carrier_stats["总订单数"] < 5, "样本较少", "样本充足"
    )
    for column in ["平均延误天数", "延误率", "异常率"]:
        carrier_stats[column] = carrier_stats[column].round(4)
    carrier_stats = carrier_stats.sort_values(
        ["调整后异常率", "总订单数"], ascending=[False, False]
    ).reset_index(drop=True)

    route_stats = (
        result.groupby("路线", dropna=False)
        .agg(
            订单数=("订单ID", "count"),
            平均延误=("实际延误天数", "mean"),
            最大延误=("实际延误天数", "max"),
            异常数=("final_anomaly", "sum"),
            严重异常数=("severity", lambda values: (values == "严重").sum()),
        )
        .reset_index()
    )
    route_stats["异常率"] = route_stats["异常数"] / route_stats["订单数"]
    route_stats = _add_smoothed_rate(
        route_stats,
        total_column="订单数",
        anomaly_column="异常数",
        global_rate=anomaly_rate,
    )
    route_stats["样本提示"] = np.where(route_stats["订单数"] < 5, "样本较少", "样本充足")
    for column in ["平均延误", "最大延误", "异常率"]:
        route_stats[column] = route_stats[column].round(4)
    route_stats = route_stats.sort_values(
        ["调整后异常率", "订单数", "平均延误"], ascending=[False, False, False]
    ).reset_index(drop=True)

    monthly = (
        result.groupby(["发货年", "发货月"], dropna=False)
        .agg(
            订单数=("订单ID", "count"),
            异常数=("final_anomaly", "sum"),
            平均延误=("实际延误天数", "mean"),
        )
        .reset_index()
        .sort_values(["发货年", "发货月"])
    )
    monthly["异常率"] = (monthly["异常数"] / monthly["订单数"]).round(4)
    monthly["平均延误"] = monthly["平均延误"].round(2)

    method_counts = {
        "业务规则": int(result["rule_anomaly"].sum()),
        "分组 Z-Score": int(result["zscore_anomaly"].sum()),
        "Isolation Forest": int(result["iforest_anomaly"].sum()),
    }
    rule_reasons = (
        anomalies.loc[anomalies["rule_reason"].fillna("").ne(""), "rule_reason"]
        .str.split("；")
        .explode()
    )
    rule_reasons = rule_reasons[rule_reasons.fillna("").ne("")]
    top_reason_counts = {str(key): int(value) for key, value in rule_reasons.value_counts().head(8).items()}

    top_columns = [
        "订单ID",
        "承运商",
        "路线",
        "运输方式",
        "实际延误天数",
        "anomaly_methods",
        "anomaly_score",
        "severity",
        "reason",
    ]
    top_anomalies = (
        anomalies.sort_values(
            ["anomaly_score", "实际延误天数"], ascending=[False, False]
        )
        .head(10)[top_columns]
        .reset_index(drop=True)
    )

    ship_dates = pd.to_datetime(result["计划发货日期"], errors="coerce").dropna()
    date_range = {
        "start": ship_dates.min().date().isoformat() if not ship_dates.empty else None,
        "end": ship_dates.max().date().isoformat() if not ship_dates.empty else None,
    }

    return {
        "total_orders": total_orders,
        "total_anomalies": total_anomalies,
        "anomaly_rate": round(anomaly_rate, 6),
        "avg_delay": round(float(result["实际延误天数"].mean()), 2),
        "avg_planned_transit": round(float(result["计划运输天数"].mean()), 2),
        "max_delay": round(float(result["实际延误天数"].max()), 2),
        "severity_counts": severity_counts,
        "carrier_stats": carrier_stats,
        "route_stats": route_stats,
        "monthly_trend": monthly,
        "method_counts": method_counts,
        "top_reason_counts": top_reason_counts,
        "top_anomalies": top_anomalies,
        "date_range": date_range,
    }
