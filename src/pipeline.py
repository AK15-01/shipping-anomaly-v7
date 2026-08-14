"""Pure orchestration functions shared by Streamlit and automated tests."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

import pandas as pd

from .business_metrics import build_summary_stats
from .charts import (
    plot_anomaly_overview,
    plot_carrier_performance,
    plot_delay_distribution,
    plot_port_throughput,
)
from .detector import DetectorConfig, ShippingAnomalyDetector


def analyze_orders(
    orders: pd.DataFrame,
    config: DetectorConfig,
) -> tuple[pd.DataFrame, dict]:
    """Run detection and statistics without Streamlit side effects."""

    detector = ShippingAnomalyDetector(orders, config=config)
    result = detector.run_all()
    stats = build_summary_stats(result)
    stats["detector_config"] = asdict(config)
    return result, stats


def generate_chart_images(
    stats: dict,
    result: pd.DataFrame,
    throughput: Optional[pd.DataFrame] = None,
) -> dict[str, bytes]:
    """Render charts once and return session-safe PNG bytes."""

    with TemporaryDirectory(prefix="shipping_charts_") as temp_dir:
        chart_dir = Path(temp_dir)
        paths = {
            "anomaly_overview": plot_anomaly_overview(stats, str(chart_dir)),
            "carrier_performance": plot_carrier_performance(stats, str(chart_dir)),
            "delay_distribution": plot_delay_distribution(result, str(chart_dir)),
        }
        if throughput is not None and not throughput.empty:
            paths["port_throughput"] = plot_port_throughput(throughput, str(chart_dir))
        return {name: Path(path).read_bytes() for name, path in paths.items()}
