"""Deterministic simulated datasets used only for the portfolio demo flow."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import random

import numpy as np
import pandas as pd


def create_demo_throughput(seed: int = 42) -> pd.DataFrame:
    """Create reproducible simulated port-throughput context data."""

    rng = np.random.default_rng(seed)
    ports = ["上海港", "宁波舟山港", "深圳港", "广州港", "青岛港", "天津港", "厦门港", "大连港"]
    base = {
        "上海港": 4200,
        "宁波舟山港": 3100,
        "深圳港": 2600,
        "广州港": 2100,
        "青岛港": 1900,
        "天津港": 1700,
        "厦门港": 1200,
        "大连港": 900,
    }
    seasonal = [0.92, 0.78, 0.95, 0.98, 1.02, 1.05, 1.03, 1.04, 1.08, 1.10, 1.07, 1.01]

    records = []
    for port in ports:
        for year in [2022, 2023, 2024]:
            for month in range(1, 13):
                trend = 1 + 0.04 * (year - 2022)
                shock = 0.82 if year == 2022 and month in [4, 5] else 1.0
                value = base[port] * seasonal[month - 1] * trend * rng.normal(1.0, 0.03) * shock
                records.append(
                    {
                        "港口": port,
                        "年份": year,
                        "月份": month,
                        "货物吞吐量_万吨": round(value, 1),
                        "集装箱吞吐量_万TEU": round(value * 0.18 + rng.normal(0, 5), 1),
                    }
                )
    return pd.DataFrame(records)


def create_demo_orders(n_orders: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Create reproducible simulated orders with realistic carrier risk differences."""

    if n_orders <= 0:
        raise ValueError("模拟订单数量必须大于 0。")
    rng = np.random.default_rng(seed)
    chooser = random.Random(seed)

    carriers = ["中远海运", "马士基", "中外运", "德邦物流", "顺丰快运"]
    cargo_types = ["电子元器件", "机械设备", "化工原料", "农产品", "消费品", "纺织品"]
    routes = [
        ("上海", "洛杉矶"),
        ("上海", "鹿特丹"),
        ("宁波", "新加坡"),
        ("深圳", "汉堡"),
        ("青岛", "迪拜"),
        ("广州", "巴塞罗那"),
        ("天津", "首尔"),
        ("厦门", "横滨"),
    ]
    transport_modes = ["海运", "空运", "铁路"]
    mode_weights = [0.65, 0.20, 0.15]
    carrier_performance = {
        "中远海运": {"delay_rate": 0.18, "avg_delay": 2.5},
        "马士基": {"delay_rate": 0.14, "avg_delay": 1.8},
        "中外运": {"delay_rate": 0.22, "avg_delay": 3.1},
        "德邦物流": {"delay_rate": 0.12, "avg_delay": 1.5},
        "顺丰快运": {"delay_rate": 0.08, "avg_delay": 0.9},
    }

    records = []
    start_date = datetime(2023, 1, 1)
    for index in range(n_orders):
        carrier = chooser.choice(carriers)
        route = chooser.choice(routes)
        mode = chooser.choices(transport_modes, weights=mode_weights)[0]
        plan_days = {
            "海运": chooser.randint(18, 35),
            "空运": chooser.randint(3, 7),
            "铁路": chooser.randint(12, 20),
        }[mode]
        ship_date = start_date + timedelta(days=chooser.randint(0, 730))
        planned_arrival = ship_date + timedelta(days=plan_days)

        performance = carrier_performance[carrier]
        actual_delay = 0
        labelled_anomaly = 0
        if chooser.random() < performance["delay_rate"]:
            actual_delay = max(0, int(rng.exponential(performance["avg_delay"])) + 1)
            if chooser.random() < 0.05:
                actual_delay = chooser.randint(10, 25)
                labelled_anomaly = 1

        actual_arrival = planned_arrival + timedelta(days=actual_delay)
        weight = round(float(rng.lognormal(3.5, 0.8)), 1)
        records.append(
            {
                "订单ID": f"ORD-{2023 + index // 1000}-{index:05d}",
                "承运商": carrier,
                "货物类型": chooser.choice(cargo_types),
                "运输方式": mode,
                "起运港": route[0],
                "目的港": route[1],
                "计划发货日期": ship_date.strftime("%Y-%m-%d"),
                "计划到达日期": planned_arrival.strftime("%Y-%m-%d"),
                "实际到达日期": actual_arrival.strftime("%Y-%m-%d"),
                "计划运输天数": plan_days,
                "实际延误天数": actual_delay,
                "货重_吨": weight,
                "运费_USD": round(weight * chooser.uniform(80, 350) + chooser.uniform(200, 800), 2),
                "标注异常": labelled_anomaly,
            }
        )
    return pd.DataFrame(records)


def generate_port_throughput(base_dir: str) -> pd.DataFrame:
    """Backward-compatible file writer used by local scripts."""

    frame = create_demo_throughput()
    data_dir = Path(base_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(data_dir / "port_throughput.csv", index=False, encoding="utf-8-sig")
    return frame


def generate_shipment_orders(base_dir: str) -> pd.DataFrame:
    """Backward-compatible file writer used by local scripts."""

    frame = create_demo_orders()
    data_dir = Path(base_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(data_dir / "shipment_orders.csv", index=False, encoding="utf-8-sig")
    return frame
