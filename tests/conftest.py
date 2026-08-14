from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_order_frame(
    delays,
    *,
    carriers=None,
    origins=None,
    destinations=None,
    modes=None,
) -> pd.DataFrame:
    delays = list(delays)
    count = len(delays)
    carriers = carriers or ["承运商A"] * count
    origins = origins or ["上海"] * count
    destinations = destinations or ["新加坡"] * count
    modes = modes or ["海运"] * count
    start = datetime(2024, 1, 1)
    rows = []
    for index, delay in enumerate(delays):
        ship_date = start + timedelta(days=index)
        planned_arrival = ship_date + timedelta(days=10)
        actual_arrival = planned_arrival + timedelta(days=float(delay))
        rows.append(
            {
                "订单ID": f"T-{index:04d}",
                "承运商": carriers[index],
                "货物类型": "测试货物",
                "运输方式": modes[index],
                "起运港": origins[index],
                "目的港": destinations[index],
                "计划发货日期": ship_date.strftime("%Y-%m-%d"),
                "计划到达日期": planned_arrival.strftime("%Y-%m-%d"),
                "实际到达日期": actual_arrival.strftime("%Y-%m-%d"),
                "计划运输天数": 10,
                "实际延误天数": delay,
                "货重_吨": 10 + index * 0.5,
                "运费_USD": 1000 + index * 37,
            }
        )
    columns = [
        "订单ID",
        "承运商",
        "货物类型",
        "运输方式",
        "起运港",
        "目的港",
        "计划发货日期",
        "计划到达日期",
        "实际到达日期",
        "计划运输天数",
        "实际延误天数",
        "货重_吨",
        "运费_USD",
    ]
    return pd.DataFrame(rows, columns=columns)


@pytest.fixture
def order_factory():
    return make_order_frame


@pytest.fixture
def metrics_result() -> pd.DataFrame:
    rows = []
    for index in range(10):
        severe = index < 9
        rows.append(
            {
                "订单ID": f"M-{index}",
                "承运商": "A" if index < 6 else "B",
                "路线": "上海→新加坡" if index % 2 == 0 else "宁波→汉堡",
                "运输方式": "海运",
                "计划发货日期": pd.Timestamp("2024-01-01") + pd.to_timedelta(index, unit="D"),
                "发货年": 2024,
                "发货月": 1,
                "实际延误天数": 16 if severe else 0,
                "计划运输天数": 10,
                "final_anomaly": 1 if severe else 0,
                "severity": "严重" if severe else "正常",
                "rule_anomaly": 1 if severe else 0,
                "zscore_anomaly": 1 if index < 5 else 0,
                "iforest_anomaly": 1 if index < 3 else 0,
                "rule_reason": "实际延误超过 14 天，触发关键业务规则" if severe else "",
                "anomaly_methods": "业务规则" if severe else "无",
                "anomaly_score": 100.0 if severe else 0.0,
                "reason": "测试异常原因" if severe else "未达到综合异常条件",
            }
        )
    return pd.DataFrame(rows)
