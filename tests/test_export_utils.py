from __future__ import annotations

import pandas as pd

from src.export_utils import dataframe_to_csv_bytes


def test_csv_export_is_utf8_sig_and_neutralises_formulas():
    frame = pd.DataFrame({"订单ID": ["=2+3"], "数值": [-5]})
    payload = dataframe_to_csv_bytes(frame)
    assert payload.startswith(b"\xef\xbb\xbf")
    decoded = payload.decode("utf-8-sig")
    assert "'=2+3" in decoded
    assert ",-5" in decoded
