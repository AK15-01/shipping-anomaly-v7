"""CSV decoding and data-quality validation for shipping datasets."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, BinaryIO, Union

import numpy as np
import pandas as pd
from pandas.errors import EmptyDataError, ParserError


MAX_UPLOAD_BYTES = 25 * 1024 * 1024

ORDER_REQUIRED_COLUMNS = [
    "订单ID",
    "承运商",
    "运输方式",
    "起运港",
    "目的港",
    "计划发货日期",
    "计划到达日期",
    "实际到达日期",
    "货重_吨",
    "运费_USD",
]
ORDER_DATE_COLUMNS = ["计划发货日期", "计划到达日期", "实际到达日期"]
ORDER_TEXT_COLUMNS = ["订单ID", "承运商", "运输方式", "起运港", "目的港"]
ORDER_POSITIVE_NUMERIC_COLUMNS = ["货重_吨", "运费_USD"]

THROUGHPUT_REQUIRED_COLUMNS = ["港口", "年份", "月份", "货物吞吐量_万吨"]


class DataValidationError(ValueError):
    """A user-correctable data validation error."""


@dataclass
class ValidationResult:
    """Validated dataframe plus a UI-friendly data-quality summary."""

    data: pd.DataFrame
    quality: dict[str, Any]
    warnings: list[str]


CsvSource = Union[bytes, bytearray, BytesIO, BinaryIO, str, Path]


def _source_to_bytes(source: CsvSource) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        payload = bytes(source)
    elif isinstance(source, (str, Path)):
        payload = Path(source).read_bytes()
    elif hasattr(source, "getvalue"):
        payload = bytes(source.getvalue())
    elif hasattr(source, "read"):
        current_position = source.tell() if hasattr(source, "tell") else None
        payload = source.read()
        if current_position is not None and hasattr(source, "seek"):
            source.seek(current_position)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
    else:
        raise DataValidationError("无法读取上传文件，请重新选择 CSV 文件。")

    if not payload or not payload.strip():
        raise DataValidationError("CSV 文件为空，请上传至少包含表头和一条记录的文件。")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise DataValidationError("CSV 文件超过 25 MB，请拆分后再上传。")
    if b"\x00" in payload:
        raise DataValidationError("文件不像有效的文本 CSV，请确认未上传 Excel 或二进制文件。")
    return payload


def read_csv_compatible(source: CsvSource, data_name: str = "数据") -> pd.DataFrame:
    """Read a CSV using common Chinese encodings with clear errors."""

    payload = _source_to_bytes(source)
    parse_errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        try:
            frame = pd.read_csv(StringIO(text))
            frame.columns = [str(column).replace("\ufeff", "").strip() for column in frame.columns]
            if len(frame.columns) == 0:
                raise EmptyDataError("no columns")
            return frame
        except (EmptyDataError, ParserError, UnicodeError, ValueError) as exc:
            parse_errors.append(str(exc))

    detail = "，可能存在列数不一致或未闭合引号" if parse_errors else ""
    raise DataValidationError(
        f"无法解析{data_name} CSV{detail}。请另存为 UTF-8、UTF-8-SIG 或 GB18030 编码后重试。"
    )


def _parse_datetime_series(series: pd.Series) -> pd.Series:
    """Parse mixed date formats across supported pandas versions."""

    try:
        return pd.to_datetime(series, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return series.map(lambda value: pd.to_datetime(value, errors="coerce"))


def _normalise_text(series: pd.Series) -> pd.Series:
    normalised = series.astype("string").str.strip()
    return normalised.mask(normalised.isin(["", "nan", "NaN", "None", "null"]))


def _extreme_counts(frame: pd.DataFrame, columns: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    if len(frame) < 8:
        return {column: 0 for column in columns}
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if values.empty:
            counts[column] = 0
            continue
        q1, q3 = values.quantile([0.25, 0.75])
        iqr = q3 - q1
        if not np.isfinite(iqr) or iqr == 0:
            counts[column] = 0
            continue
        lower, upper = q1 - 3 * iqr, q3 + 3 * iqr
        counts[column] = int(((values < lower) | (values > upper)).sum())
    return counts


def validate_orders(frame: pd.DataFrame) -> ValidationResult:
    """Validate, clean, and derive the fields used by the detector."""

    if not isinstance(frame, pd.DataFrame):
        raise DataValidationError("订单数据格式无效，请上传 CSV 文件。")

    df = frame.copy()
    df.columns = [str(column).replace("\ufeff", "").strip() for column in df.columns]
    missing_columns = [column for column in ORDER_REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        missing_text = "、".join(missing_columns)
        raise DataValidationError(f"数据缺少以下必要字段：{missing_text}，请检查文件模板。")
    if df.empty:
        raise DataValidationError("订单 CSV 没有数据行，无法开始分析。")

    raw_rows = len(df)
    warnings: list[str] = []

    for column in ORDER_TEXT_COLUMNS:
        df[column] = _normalise_text(df[column])

    exact_duplicate_mask = df.duplicated(keep="first")
    exact_duplicates_removed = int(exact_duplicate_mask.sum())
    df = df.loc[~exact_duplicate_mask].copy()

    duplicate_id_mask = df["订单ID"].notna() & df["订单ID"].duplicated(keep="first")
    duplicate_order_ids_removed = int(duplicate_id_mask.sum())
    df = df.loc[~duplicate_id_mask].copy()

    original_dates = {column: df[column].copy() for column in ORDER_DATE_COLUMNS}
    for column in ORDER_DATE_COLUMNS:
        df[column] = _parse_datetime_series(df[column])

    invalid_date_mask = df[ORDER_DATE_COLUMNS].isna().any(axis=1)
    invalid_date_rows = int(invalid_date_mask.sum())

    for column in ORDER_POSITIVE_NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        df[column] = df[column].replace([np.inf, -np.inf], np.nan)

    invalid_numeric_mask = df[ORDER_POSITIVE_NUMERIC_COLUMNS].isna().any(axis=1)
    invalid_numeric_rows = int(invalid_numeric_mask.sum())
    nonpositive_numeric_mask = (df[ORDER_POSITIVE_NUMERIC_COLUMNS] <= 0).any(axis=1)
    nonpositive_numeric_rows = int(nonpositive_numeric_mask.fillna(False).sum())

    missing_text_mask = df[ORDER_TEXT_COLUMNS].isna().any(axis=1)
    missing_text_rows = int(missing_text_mask.sum())

    invalid_sequence_mask = (
        (df["计划到达日期"] < df["计划发货日期"])
        | (df["实际到达日期"] < df["计划发货日期"])
    ).fillna(False)
    invalid_sequence_rows = int(invalid_sequence_mask.sum())

    planned_days = (
        (df["计划到达日期"] - df["计划发货日期"]).dt.total_seconds() / 86400
    ).round(2)
    delay_days = (
        (df["实际到达日期"] - df["计划到达日期"]).dt.total_seconds() / 86400
    ).round(2)

    corrected_plan_rows = 0
    if "计划运输天数" in df.columns:
        supplied = pd.to_numeric(df["计划运输天数"], errors="coerce")
        corrected_plan_rows = int(((supplied - planned_days).abs() > 0.01).fillna(False).sum())
    corrected_delay_rows = 0
    if "实际延误天数" in df.columns:
        supplied = pd.to_numeric(df["实际延误天数"], errors="coerce")
        corrected_delay_rows = int(((supplied - delay_days).abs() > 0.01).fillna(False).sum())

    df["计划运输天数"] = planned_days
    df["实际延误天数"] = delay_days

    invalid_mask = (
        invalid_date_mask
        | invalid_numeric_mask
        | nonpositive_numeric_mask
        | missing_text_mask
        | invalid_sequence_mask
        | (planned_days <= 0).fillna(False)
    )
    cleaned = df.loc[~invalid_mask].copy().reset_index(drop=True)
    if cleaned.empty:
        raise DataValidationError(
            "订单数据没有可用于分析的有效记录。请检查必填值、日期先后关系、货重和运费。"
        )

    if "货物类型" not in cleaned.columns:
        cleaned["货物类型"] = "未提供"
    else:
        cleaned["货物类型"] = _normalise_text(cleaned["货物类型"]).fillna("未提供")

    extreme_counts = _extreme_counts(
        cleaned, ["实际延误天数", "计划运输天数", "货重_吨", "运费_USD"]
    )
    extreme_total = sum(extreme_counts.values())
    if extreme_total:
        warnings.append(f"检测到 {extreme_total} 个极端数值；这些记录被保留并交由异常模型判断。")
    if corrected_plan_rows or corrected_delay_rows:
        warnings.append(
            "计划运输天数和实际延误天数已统一按日期重新计算，避免上传字段与日期不一致。"
        )
    if invalid_date_rows:
        warnings.append(f"已剔除 {invalid_date_rows} 条日期为空或无法解析的记录。")
    if invalid_sequence_rows:
        warnings.append(f"已剔除 {invalid_sequence_rows} 条起运日期晚于到达日期的记录。")

    quality = {
        "raw_rows": int(raw_rows),
        "valid_rows": int(len(cleaned)),
        "dropped_rows": int(raw_rows - len(cleaned)),
        "exact_duplicates_removed": exact_duplicates_removed,
        "duplicate_order_ids_removed": duplicate_order_ids_removed,
        "missing_required_value_rows": missing_text_rows,
        "invalid_date_rows": invalid_date_rows,
        "invalid_numeric_rows": invalid_numeric_rows,
        "nonpositive_numeric_rows": nonpositive_numeric_rows,
        "invalid_date_sequence_rows": invalid_sequence_rows,
        "corrected_plan_days_rows": corrected_plan_rows,
        "corrected_delay_days_rows": corrected_delay_rows,
        "extreme_value_counts": extreme_counts,
        "date_columns_checked": list(original_dates),
    }
    return ValidationResult(cleaned, quality, warnings)


def validate_throughput(frame: pd.DataFrame) -> ValidationResult:
    """Validate optional port-throughput data used only for contextual charts."""

    if not isinstance(frame, pd.DataFrame):
        raise DataValidationError("港口吞吐量数据格式无效，请上传 CSV 文件。")
    df = frame.copy()
    df.columns = [str(column).replace("\ufeff", "").strip() for column in df.columns]
    missing_columns = [column for column in THROUGHPUT_REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        missing_text = "、".join(missing_columns)
        raise DataValidationError(f"吞吐量数据缺少字段：{missing_text}。")
    if df.empty:
        raise DataValidationError("吞吐量 CSV 没有数据行。")

    raw_rows = len(df)
    df["港口"] = _normalise_text(df["港口"])
    numeric_columns = ["年份", "月份", "货物吞吐量_万吨"]
    if "集装箱吞吐量_万TEU" in df.columns:
        numeric_columns.append("集装箱吞吐量_万TEU")
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce").replace([np.inf, -np.inf], np.nan)

    invalid_mask = (
        df[["港口", *numeric_columns]].isna().any(axis=1)
        | ~df["月份"].between(1, 12)
        | ~df["年份"].between(1900, 2100)
        | (df["货物吞吐量_万吨"] < 0)
    )
    cleaned = df.loc[~invalid_mask].drop_duplicates().copy().reset_index(drop=True)
    if cleaned.empty:
        raise DataValidationError("吞吐量数据没有可用于分析的有效记录。")

    cleaned["年份"] = cleaned["年份"].astype(int)
    cleaned["月份"] = cleaned["月份"].astype(int)
    dropped = raw_rows - len(cleaned)
    warnings = [f"吞吐量数据已剔除 {dropped} 条无效或重复记录。"] if dropped else []
    quality = {
        "raw_rows": int(raw_rows),
        "valid_rows": int(len(cleaned)),
        "dropped_rows": int(dropped),
    }
    return ValidationResult(cleaned, quality, warnings)


def load_and_validate_orders(source: CsvSource) -> ValidationResult:
    return validate_orders(read_csv_compatible(source, "订单"))


def load_and_validate_throughput(source: CsvSource) -> ValidationResult:
    return validate_throughput(read_csv_compatible(source, "吞吐量"))
