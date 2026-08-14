"""Explainable, deterministic multi-layer anomaly detection engine."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import RobustScaler


@dataclass(frozen=True)
class DetectorConfig:
    """Validated parameters that control every detector run."""

    zscore_threshold: float = 2.5
    iforest_contamination: float = 0.05
    min_group_size: int = 5
    random_state: int = 42

    def __post_init__(self) -> None:
        if not np.isfinite(self.zscore_threshold) or self.zscore_threshold <= 0:
            raise ValueError("Z-Score 阈值必须是大于 0 的有限数值。")
        if (
            not np.isfinite(self.iforest_contamination)
            or not 0 < self.iforest_contamination < 0.5
        ):
            raise ValueError("Isolation Forest contamination 必须满足 0 < contamination < 0.5。")
        if self.min_group_size < 3:
            raise ValueError("Z-Score 分组最小样本数不能小于 3。")


def assign_severity(row: pd.Series) -> str:
    """Central severity policy used by UI, statistics, AI, and reports."""

    if int(row["final_anomaly"]) != 1:
        return "正常"
    if bool(row.get("critical_rule", False)) or int(row["anomaly_votes"]) == 3:
        return "严重"
    if float(row["实际延误天数"]) > 7 or int(row["anomaly_votes"]) == 2:
        return "中等"
    return "轻微"


class ShippingAnomalyDetector:
    """Run business rules, grouped Z-Score, and Isolation Forest independently."""

    MODEL_FEATURES = [
        "实际延误天数",
        "延误偏差率",
        "货重_吨",
        "运费_USD",
        "计划运输天数",
    ]

    def __init__(
        self,
        orders: Union[pd.DataFrame, str, Path],
        throughput: Optional[Union[pd.DataFrame, str, Path]] = None,
        config: Optional[DetectorConfig] = None,
    ) -> None:
        self.config = config or DetectorConfig()
        self.orders = self._load_frame(orders)
        self.throughput = self._load_frame(throughput) if throughput is not None else None
        self._preprocess()

    @staticmethod
    def _load_frame(source: Union[pd.DataFrame, str, Path]) -> pd.DataFrame:
        if isinstance(source, pd.DataFrame):
            return source.copy()
        return pd.read_csv(source)

    def _preprocess(self) -> None:
        df = self.orders.copy()
        required = {
            "订单ID",
            "承运商",
            "运输方式",
            "起运港",
            "目的港",
            "计划发货日期",
            "实际延误天数",
            "计划运输天数",
            "货重_吨",
            "运费_USD",
        }
        missing = sorted(required.difference(df.columns))
        if missing:
            raise ValueError(f"检测器缺少必要字段：{'、'.join(missing)}")
        if df.empty:
            raise ValueError("检测器不能分析空数据。")

        if df["订单ID"].duplicated().any():
            raise ValueError("订单ID 必须唯一；请先通过数据校验层处理重复记录。")

        df["计划发货日期"] = pd.to_datetime(df["计划发货日期"], errors="coerce")
        for column in ["实际延误天数", "计划运输天数", "货重_吨", "运费_USD"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        if df[["实际延误天数", "计划运输天数", "货重_吨", "运费_USD"]].isna().any().any():
            raise ValueError("检测器收到缺失或非数值特征；请先通过数据校验层清洗。")

        df["延误偏差率"] = df["实际延误天数"] / df["计划运输天数"].clip(lower=1)
        df["发货月"] = df["计划发货日期"].dt.month
        df["发货年"] = df["计划发货日期"].dt.year
        df["路线"] = df["起运港"].astype(str) + "→" + df["目的港"].astype(str)
        df["单位运费_USD_每吨"] = df["运费_USD"] / df["货重_吨"].clip(lower=0.01)
        self.orders = df

    def rule_based_detection(self) -> pd.DataFrame:
        """Business rules remain authoritative for obvious operational breaches."""

        df = self.orders
        result = pd.DataFrame({"订单ID": df["订单ID"].to_numpy()})
        result["rule_anomaly"] = 0
        result["critical_rule"] = False
        reasons = pd.Series("", index=df.index, dtype="object")

        def add_reason(mask: pd.Series, text: str) -> None:
            nonlocal reasons
            separator = np.where(reasons.loc[mask].eq(""), "", "；")
            reasons.loc[mask] = reasons.loc[mask] + separator + text

        deviation_mask = df["延误偏差率"] > 0.5
        delay_mask = df["实际延误天数"] > 7
        critical_mask = df["实际延误天数"] > 14

        mode_size = df.groupby("运输方式")["单位运费_USD_每吨"].transform("size")
        mode_floor = df.groupby("运输方式")["单位运费_USD_每吨"].transform(
            lambda values: values.quantile(0.01)
        )
        low_cost_mask = (mode_size >= 20) & (
            df["单位运费_USD_每吨"] < mode_floor
        )

        add_reason(deviation_mask, "实际延误超过计划运输时长的 50%")
        add_reason(delay_mask, "实际延误超过 7 天")
        add_reason(critical_mask, "实际延误超过 14 天，触发关键业务规则")
        add_reason(low_cost_mask, "单位运费低于同运输方式的第 1 百分位，需复核数据或报价")

        any_rule = deviation_mask | delay_mask | low_cost_mask
        result["rule_anomaly"] = any_rule.astype(int).to_numpy()
        result["critical_rule"] = critical_mask.to_numpy()
        result["rule_reason"] = reasons.to_numpy()
        return result

    def zscore_detection(self, threshold: Optional[float] = None) -> pd.DataFrame:
        """Route-grouped leave-one-out Z-Score with small-group safeguards."""

        effective_threshold = self.config.zscore_threshold if threshold is None else threshold
        if not np.isfinite(effective_threshold) or effective_threshold <= 0:
            raise ValueError("Z-Score 阈值必须是大于 0 的有限数值。")

        results: list[pd.DataFrame] = []
        for _, group in self.orders.groupby("路线", sort=False, dropna=False):
            current = group[["订单ID", "实际延误天数", "路线"]].copy()
            values = current["实际延误天数"].astype(float)
            count = len(current)
            if count < self.config.min_group_size:
                current["z_score"] = 0.0
                current["zscore_anomaly"] = 0
                current["zscore_skipped"] = True
            else:
                reference_count = count - 1
                reference_mean = (values.sum() - values) / reference_count
                reference_sum_sq = (values.pow(2).sum() - values.pow(2))
                variance_numerator = reference_sum_sq - reference_count * reference_mean.pow(2)
                reference_std = np.sqrt(
                    variance_numerator.clip(lower=0) / max(reference_count - 1, 1)
                ).clip(lower=0.5)
                z_score = (values - reference_mean) / reference_std
                current["z_score"] = z_score.round(3)
                current["zscore_anomaly"] = (z_score.abs() > effective_threshold).astype(int)
                current["zscore_skipped"] = False
            results.append(
                current[["订单ID", "zscore_anomaly", "z_score", "zscore_skipped"]]
            )
        return pd.concat(results, ignore_index=True)

    def isolation_forest_detection(
        self, contamination: Optional[float] = None
    ) -> pd.DataFrame:
        """Isolation Forest with validated contamination and deterministic seed."""

        effective_contamination = (
            self.config.iforest_contamination if contamination is None else contamination
        )
        if (
            not np.isfinite(effective_contamination)
            or not 0 < effective_contamination < 0.5
        ):
            raise ValueError("Isolation Forest contamination 必须满足 0 < contamination < 0.5。")

        df = self.orders
        if len(df) < 10:
            return pd.DataFrame(
                {
                    "订单ID": df["订单ID"].to_numpy(),
                    "iforest_anomaly": np.zeros(len(df), dtype=int),
                    "iforest_score": np.zeros(len(df), dtype=float),
                    "iforest_skipped": np.ones(len(df), dtype=bool),
                }
            )

        model = make_pipeline(
            SimpleImputer(strategy="median"),
            RobustScaler(),
            IsolationForest(
                contamination=float(effective_contamination),
                random_state=self.config.random_state,
                n_estimators=200,
                n_jobs=1,
            ),
        )
        features = df[self.MODEL_FEATURES]
        predictions = model.fit_predict(features)
        scores = model.decision_function(features)
        return pd.DataFrame(
            {
                "订单ID": df["订单ID"].to_numpy(),
                "iforest_anomaly": (predictions == -1).astype(int),
                "iforest_score": (-scores).round(5),
                "iforest_skipped": np.zeros(len(df), dtype=bool),
            }
        )

    def run_all(
        self,
        *,
        zscore_threshold: Optional[float] = None,
        iforest_contamination: Optional[float] = None,
    ) -> pd.DataFrame:
        """Run all layers with explicit, validated parameter overrides."""

        effective_config = replace(
            self.config,
            zscore_threshold=(
                self.config.zscore_threshold
                if zscore_threshold is None
                else zscore_threshold
            ),
            iforest_contamination=(
                self.config.iforest_contamination
                if iforest_contamination is None
                else iforest_contamination
            ),
        )

        rule_result = self.rule_based_detection()
        zscore_result = self.zscore_detection(effective_config.zscore_threshold)
        iforest_result = self.isolation_forest_detection(
            effective_config.iforest_contamination
        )

        result = self.orders.copy()
        result = result.merge(rule_result, on="订单ID", validate="one_to_one")
        result = result.merge(zscore_result, on="订单ID", validate="one_to_one")
        result = result.merge(iforest_result, on="订单ID", validate="one_to_one")
        result["anomaly_votes"] = (
            result["rule_anomaly"]
            + result["zscore_anomaly"]
            + result["iforest_anomaly"]
        ).astype(int)

        # Business rules can stand alone; otherwise two independent methods must agree.
        result["final_anomaly"] = (
            (result["rule_anomaly"] == 1) | (result["anomaly_votes"] >= 2)
        ).astype(int)

        delay_component = (
            result["实际延误天数"].clip(lower=0, upper=14) / 14 * 25
        )
        result["anomaly_score"] = (
            result["anomaly_votes"] / 3 * 75 + delay_component
        ).clip(0, 100).round(1)

        result["anomaly_methods"] = result.apply(self._method_labels, axis=1)
        result["severity"] = result.apply(assign_severity, axis=1)
        result["reason"] = result.apply(
            lambda row: self._explain_row(row, effective_config.zscore_threshold), axis=1
        )
        return result

    @staticmethod
    def _method_labels(row: pd.Series) -> str:
        methods = []
        if int(row["rule_anomaly"]) == 1:
            methods.append("业务规则")
        if int(row["zscore_anomaly"]) == 1:
            methods.append("分组 Z-Score")
        if int(row["iforest_anomaly"]) == 1:
            methods.append("Isolation Forest")
        return "、".join(methods) if methods else "无"

    @staticmethod
    def _explain_row(row: pd.Series, threshold: float) -> str:
        parts: list[str] = []
        if int(row["rule_anomaly"]) == 1:
            parts.append(f"规则检测：{row['rule_reason']}")
        if int(row["zscore_anomaly"]) == 1:
            parts.append(
                f"分组 Z-Score：路线历史基线 Z={row['z_score']:.2f}，"
                f"超过阈值 {threshold:.2f}"
            )
        elif bool(row["zscore_skipped"]):
            parts.append("分组 Z-Score：路线样本不足，未参与判断")
        if int(row["iforest_anomaly"]) == 1:
            parts.append(
                f"Isolation Forest：多特征组合偏离常规（模型分数 {row['iforest_score']:.4f}）"
            )
        elif bool(row["iforest_skipped"]):
            parts.append("Isolation Forest：总样本不足 10 条，未参与判断")

        verdict = "判定为异常" if int(row["final_anomaly"]) == 1 else "未达到综合异常条件"
        parts.append(
            f"综合：3 种方法中 {int(row['anomaly_votes'])} 种命中，{verdict}，"
            f"严重等级为{row['severity']}"
        )
        return "；".join(parts)

    def get_summary_stats(self, result: pd.DataFrame) -> dict:
        """Backward-compatible entry point for the central statistics module."""

        try:
            from .business_metrics import build_summary_stats
        except ImportError:  # pragma: no cover - compatibility for direct src imports
            from business_metrics import build_summary_stats
        return build_summary_stats(result)
