from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data_validator import (
    DataValidationError,
    read_csv_compatible,
    validate_orders,
    validate_throughput,
)


def test_missing_required_columns_has_clear_message(order_factory):
    frame = order_factory([0, 1]).drop(columns=["承运商", "起运港"])
    with pytest.raises(DataValidationError, match="承运商.*起运港"):
        validate_orders(frame)


def test_nan_and_infinite_numeric_rows_are_removed(order_factory):
    frame = order_factory([0, 1, 2])
    frame["运费_USD"] = frame["运费_USD"].astype(float)
    frame.loc[0, "货重_吨"] = np.nan
    frame.loc[1, "运费_USD"] = np.inf
    validated = validate_orders(frame)
    assert len(validated.data) == 1
    assert validated.quality["invalid_numeric_rows"] == 2


def test_numeric_strings_are_converted(order_factory):
    frame = order_factory([0, 1])
    frame["货重_吨"] = frame["货重_吨"].astype(str)
    frame["运费_USD"] = frame["运费_USD"].astype(str)
    validated = validate_orders(frame)
    assert pd.api.types.is_numeric_dtype(validated.data["货重_吨"])
    assert len(validated.data) == 2


def test_invalid_date_and_sequence_are_counted(order_factory):
    frame = order_factory([0, 1, 2])
    frame.loc[0, "实际到达日期"] = "not-a-date"
    frame.loc[1, "计划到达日期"] = "2023-01-01"
    validated = validate_orders(frame)
    assert len(validated.data) == 1
    assert validated.quality["invalid_date_rows"] == 1
    assert validated.quality["invalid_date_sequence_rows"] == 1


def test_derived_day_fields_use_dates(order_factory):
    frame = order_factory([3])
    frame.loc[0, "计划运输天数"] = 999
    frame.loc[0, "实际延误天数"] = 999
    validated = validate_orders(frame)
    assert validated.data.loc[0, "计划运输天数"] == 10
    assert validated.data.loc[0, "实际延误天数"] == 3
    assert validated.quality["corrected_delay_days_rows"] == 1


def test_empty_csv_is_rejected():
    with pytest.raises(DataValidationError, match="为空"):
        read_csv_compatible(b"")


def test_binary_parse_failure_is_user_friendly():
    with pytest.raises(DataValidationError, match="不像有效的文本 CSV"):
        read_csv_compatible(b"a,b\x00c,d")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030", "gbk"])
def test_common_csv_encodings_are_supported(order_factory, encoding):
    payload = order_factory([0, 1]).to_csv(index=False).encode(encoding)
    parsed = read_csv_compatible(payload)
    assert parsed.loc[0, "承运商"] == "承运商A"


def test_empty_order_dataframe_is_rejected(order_factory):
    with pytest.raises(DataValidationError, match="没有数据行"):
        validate_orders(order_factory([]))


def test_optional_throughput_validation():
    frame = pd.DataFrame(
        {
            "港口": ["上海港", "坏月份"],
            "年份": [2024, 2024],
            "月份": [1, 13],
            "货物吞吐量_万吨": [100.0, 20.0],
        }
    )
    validated = validate_throughput(frame)
    assert len(validated.data) == 1
    assert validated.quality["dropped_rows"] == 1
