"""Shipping Anomaly Detection — Streamlit Final Portfolio Edition."""

from __future__ import annotations

import hashlib
import html
import os
from typing import Any, Optional

import pandas as pd
import streamlit as st

from src.ai_analyst import (
    AIServiceError,
    generate_ai_carrier_insight,
    generate_local_carrier_insight,
    generate_local_summary,
    generate_summary_with_fallback,
)
from src.business_metrics import get_severity_count
from src.data_validator import (
    DataValidationError,
    read_csv_compatible,
    validate_orders,
    validate_throughput,
)
from src.detector import DetectorConfig
from src.export_utils import dataframe_to_csv_bytes
from src.generate_data import create_demo_orders, create_demo_throughput
from src.pipeline import analyze_orders, generate_chart_images
from src.report_generator import generate_report


st.set_page_config(
    page_title="航运运输异常监测与分析平台",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)


ANALYSIS_STATE_KEYS = [
    "analysis_bundle",
    "analysis_complete",
    "raw_orders",
    "cleaned_orders",
    "detector_result",
    "statistics",
    "anomaly_results",
    "kpis",
    "chart_data",
    "report_data",
    "carrier_insights",
]


@st.cache_data(show_spinner=False)
def get_demo_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cache only non-sensitive deterministic demo data."""

    return create_demo_orders(), create_demo_throughput()


def initialise_state() -> None:
    defaults = {
        "analysis_bundle": None,
        "analysis_complete": False,
        "carrier_insights": {},
        "deepseek_temp_input": "",
        "ai_mode": "自动业务摘要（无需 Key）",
        "flash_message": "",
        "flash_level": "success",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def save_analysis_bundle(bundle: dict[str, Any]) -> None:
    """Persist both a cohesive bundle and the explicit lifecycle artifacts."""

    st.session_state.analysis_bundle = bundle
    st.session_state.analysis_complete = True
    st.session_state.raw_orders = bundle["raw_orders"]
    st.session_state.cleaned_orders = bundle["cleaned_orders"]
    st.session_state.detector_result = bundle["result"]
    st.session_state.statistics = bundle["stats"]
    st.session_state.anomaly_results = bundle["result"].loc[
        bundle["result"]["final_anomaly"].eq(1)
    ]
    st.session_state.kpis = {
        key: bundle["stats"][key]
        for key in ("total_orders", "total_anomalies", "anomaly_rate", "avg_delay")
    }
    st.session_state.chart_data = bundle["charts"]
    st.session_state.report_data = bundle["report_bytes"]
    st.session_state.carrier_insights = bundle.get("carrier_insights", {})


def clear_analysis_state(*, reset_inputs: bool = False) -> None:
    for key in ANALYSIS_STATE_KEYS:
        st.session_state.pop(key, None)
    st.session_state.analysis_bundle = None
    st.session_state.analysis_complete = False
    st.session_state.carrier_insights = {}
    if reset_inputs:
        for key in [
            "data_mode",
            "orders_upload",
            "throughput_upload",
            "zscore_widget",
            "contamination_widget",
            "ai_mode",
            "deepseek_temp_input",
            "detail_severity",
            "detail_carrier",
            "detail_route",
            "carrier_select",
            "detail_explain_order",
        ]:
            st.session_state.pop(key, None)


def _secret_api_key() -> str:
    for key in ("DEEPSEEK_API_KEY", "deepseek_api_key", "DEEPSEEK_KEY", "deepseek_key"):
        try:
            value = st.secrets.get(key, "")
        except Exception:
            value = ""
        if value:
            return str(value).strip()
    try:
        value = st.secrets.get("deepseek", {}).get("api_key", "")
    except Exception:
        value = ""
    return str(value).strip() if value else ""


def resolve_api_key(user_input: str = "") -> tuple[str, Optional[str]]:
    """Resolve key by the documented priority without exposing its value."""

    secret = _secret_api_key()
    if secret:
        return secret, "Streamlit Secrets"
    environment = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if environment:
        return environment, "环境变量"
    temporary = (user_input or "").strip()
    if temporary:
        return temporary, "本次会话临时输入"
    return "", None


def _hash_bytes(payload: Optional[bytes]) -> str:
    return hashlib.sha256(payload or b"").hexdigest()[:16]


def input_signature(data_mode: str, orders_upload, throughput_upload) -> str:
    order_bytes = orders_upload.getvalue() if orders_upload is not None else b""
    throughput_bytes = throughput_upload.getvalue() if throughput_upload is not None else b""
    return f"{data_mode}:{_hash_bytes(order_bytes)}:{_hash_bytes(throughput_bytes)}"


def category_options(series: pd.Series, limit: int = 200) -> tuple[list[str], bool]:
    values = series.astype("string").str.strip()
    values = values[values.notna() & values.ne("")]
    counts = values.value_counts()
    return counts.head(limit).index.astype(str).tolist(), len(counts) > limit


def _prepare_inputs(data_mode: str, orders_upload, throughput_upload):
    if data_mode == "使用模拟演示数据":
        raw_orders, raw_throughput = get_demo_data()
        orders_validation = validate_orders(raw_orders)
        throughput_validation = validate_throughput(raw_throughput)
        return (
            raw_orders.copy(),
            orders_validation,
            raw_throughput.copy(),
            throughput_validation,
            "demo",
        )

    if orders_upload is None:
        raise DataValidationError("请先上传必选的运输订单 CSV。")
    raw_orders = read_csv_compatible(orders_upload.getvalue(), "订单")
    orders_validation = validate_orders(raw_orders)

    raw_throughput = None
    throughput_validation = None
    if throughput_upload is not None:
        raw_throughput = read_csv_compatible(throughput_upload.getvalue(), "吞吐量")
        throughput_validation = validate_throughput(raw_throughput)
    return raw_orders, orders_validation, raw_throughput, throughput_validation, "uploaded"


def _report_label(source: str) -> str:
    return "DeepSeek AI 分析" if source == "deepseek" else "自动业务摘要"


def rebuild_report(bundle: dict[str, Any]) -> bytes:
    return generate_report(
        bundle["stats"],
        bundle["result"],
        bundle["charts"],
        insight=bundle["ai_summary"],
        insight_label=_report_label(bundle["ai_source"]),
        throughput_available=bundle["throughput"] is not None,
    )


def run_analysis(
    data_mode: str,
    orders_upload,
    throughput_upload,
    config: DetectorConfig,
    signature: str,
) -> dict[str, Any]:
    raw_orders, orders_validation, raw_throughput, throughput_validation, source = _prepare_inputs(
        data_mode, orders_upload, throughput_upload
    )
    throughput = throughput_validation.data if throughput_validation is not None else None
    result, stats = analyze_orders(orders_validation.data, config)
    stats["data_quality"] = orders_validation.quality
    charts = generate_chart_images(stats, result, throughput)
    summary = generate_local_summary(stats)
    bundle = {
        "source": source,
        "input_signature": signature,
        "config": config,
        "raw_orders": raw_orders,
        "cleaned_orders": orders_validation.data,
        "throughput_raw": raw_throughput,
        "throughput": throughput,
        "quality": orders_validation.quality,
        "quality_warnings": orders_validation.warnings
        + (throughput_validation.warnings if throughput_validation is not None else []),
        "result": result,
        "stats": stats,
        "charts": charts,
        "ai_summary": summary,
        "ai_source": "local",
        "carrier_insights": {},
    }
    bundle["report_bytes"] = rebuild_report(bundle)
    return bundle


def render_hero() -> None:
    """Render the product identity without coupling it to analysis state."""

    st.markdown(
        """
        <section class="hero-panel">
          <div class="hero-eyebrow">SHIPPING ANOMALY DETECTION · FINAL PORTFOLIO EDITION</div>
          <h1>航运运输异常监测与分析平台</h1>
          <p>基于业务规则、统计检测与 Isolation Forest 的多层航运物流异常分析工具</p>
          <div class="method-tags" aria-label="检测能力">
            <span>Rule Detection</span><span>Z-Score</span><span>Isolation Forest</span><span>AI Insight</span>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_source_banner(source: str, *, upload_ready: bool = False) -> None:
    if source == "demo":
        label = "当前使用模拟演示数据"
        detail = "用于展示平台完整分析流程，不代表真实企业生产数据。"
        style = "demo"
        badge = "DEMO DATA"
    elif source == "uploaded":
        label = "当前使用用户上传数据"
        detail = "检测结果、业务指标与报告均来自本次上传文件。"
        style = "real"
        badge = "USER DATA"
    else:
        label = "已选择用户上传数据" if upload_ready else "等待上传运输订单 CSV"
        detail = "上传完成后点击“开始分析”，系统将先执行数据质量校验。"
        style = "pending"
        badge = "DATA INPUT"
    st.markdown(
        f"""
        <div class="source-card {style}">
          <div><span class="source-badge">{badge}</span><strong>{label}</strong></div>
          <p>{detail}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_title(title: str, subtitle: str = "") -> None:
    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle)
    subtitle_html = f"<p>{safe_subtitle}</p>" if safe_subtitle else ""
    st.markdown(
        f"""
        <div class="section-heading">
          <span class="section-accent"></span>
          <div><h2>{safe_title}</h2>{subtitle_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpi_cards(stats: dict[str, Any]) -> None:
    cards = [
        ("总订单量", f"{stats['total_orders']:,}", "本次纳入分析", "blue"),
        ("异常订单数", f"{stats['total_anomalies']:,}", "综合判定异常", "orange"),
        ("异常率", f"{stats['anomaly_rate'] * 100:.1f}%", "异常订单 / 总订单", "violet"),
        ("平均延误", f"{stats['avg_delay']:.1f} 天", "全部订单平均值", "teal"),
        ("严重异常", f"{get_severity_count(stats, '严重'):,}", "需优先业务复核", "red"),
    ]
    for column, (label, value, note, tone) in zip(st.columns(5), cards):
        with column:
            st.markdown(
                f"""
                <div class="kpi-card {tone}">
                  <div class="kpi-label">{label}</div>
                  <div class="kpi-value">{value}</div>
                  <div class="kpi-note">{note}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_severity_cards(stats: dict[str, Any]) -> None:
    total = max(int(stats["total_orders"]), 1)
    cards = [
        ("严重", "critical", "优先复核"),
        ("中等", "moderate", "重点关注"),
        ("轻微", "minor", "持续观察"),
        ("正常", "normal", "未达异常条件"),
    ]
    for column, (level, style, note) in zip(st.columns(4), cards):
        count = get_severity_count(stats, level)
        with column:
            st.markdown(
                f"""
                <div class="severity-card {style}">
                  <span class="severity-badge">{level} · {count:,} 条</span>
                  <strong>{count / total * 100:.1f}%</strong>
                  <small>{note}</small>
                </div>
                """,
                unsafe_allow_html=True,
            )


def severity_cell_style(value: str) -> str:
    styles = {
        "严重": "background-color:#f7dede;color:#8d3636;font-weight:700;border-left:3px solid #b95b5b",
        "中等": "background-color:#f8e5ca;color:#86551b;font-weight:700;border-left:3px solid #cb8731",
        "轻微": "background-color:#f4ebbd;color:#756015;font-weight:700;border-left:3px solid #c4a634",
        "正常": "background-color:#dcefe3;color:#306747;font-weight:700;border-left:3px solid #4f9568",
    }
    return styles.get(str(value), "")


def display_quality_summary(bundle: dict[str, Any]) -> None:
    quality = bundle["quality"]
    duplicate_rows = int(quality["exact_duplicates_removed"]) + int(
        quality["duplicate_order_ids_removed"]
    )
    extreme_values = int(sum(quality.get("extreme_value_counts", {}).values()))
    issue_count = int(quality["dropped_rows"]) + extreme_values
    cards = [
        ("原始记录", quality["raw_rows"]),
        ("有效记录", quality["valid_rows"]),
        ("缺失必填", quality["missing_required_value_rows"]),
        ("重复记录", duplicate_rows),
        ("数据问题", issue_count),
    ]

    render_section_title("数据质量摘要", "先验证数据，再运行异常检测")
    for column, (label, value) in zip(st.columns(5), cards):
        with column:
            st.markdown(
                f"""
                <div class="quality-card">
                  <span>{label}</span><strong>{int(value):,}</strong>
                </div>
                """,
                unsafe_allow_html=True,
            )

    if issue_count == 0 and not bundle["quality_warnings"]:
        st.markdown(
            '<div class="quality-status passed"><strong>数据质量检查通过</strong>'
            '<span>必要字段、日期顺序与核心数值均可用于分析。</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="quality-status attention"><strong>数据质量检查完成，存在需关注项</strong>'
            '<span>无效记录已安全剔除；保留的极端值继续参与异常检测。</span></div>',
            unsafe_allow_html=True,
        )

    with st.expander("查看数据质量明细", expanded=bool(quality.get("dropped_rows"))):
        st.caption(
            f"剔除记录：{quality['dropped_rows']}；日期错误："
            f"{quality['invalid_date_rows'] + quality['invalid_date_sequence_rows']}；"
            f"数值错误：{quality['invalid_numeric_rows']}；非正货重/运费："
            f"{quality['nonpositive_numeric_rows']}；保留极端值：{extreme_values}。"
        )
        for warning in bundle["quality_warnings"]:
            st.warning(warning, icon=":material/warning:")


def render_risk_cards(
    frame: pd.DataFrame,
    *,
    entity_column: str,
    total_column: str,
    anomaly_column: str,
) -> None:
    top_risks = frame.head(3)
    if top_risks.empty:
        st.info("当前没有可展示的风险排名。")
        return
    for rank, (column, (_, row)) in enumerate(zip(st.columns(len(top_risks)), top_risks.iterrows()), 1):
        entity = html.escape(str(row[entity_column]))
        adjusted_rate = float(row["调整后异常率"]) * 100
        with column:
            st.markdown(
                f"""
                <div class="risk-card">
                  <span class="risk-rank">TOP {rank}</span>
                  <strong>{entity}</strong>
                  <div class="risk-rate">{adjusted_rate:.1f}% <small>调整风险率</small></div>
                  <p>{int(row[total_column]):,} 单 · {int(row[anomaly_column]):,} 条异常</p>
                </div>
                """,
                unsafe_allow_html=True,
            )


initialise_state()


st.markdown(
    """
<style>
  :root {
    --navy: #173a5e;
    --blue: #2f6ea5;
    --muted: #66768a;
    --line: #dbe4ee;
    --panel: #f7f9fc;
  }
  .block-container {max-width: 1440px; padding-top: 1.35rem; padding-bottom: 3rem;}
  [data-testid="stSidebar"] {border-right: 1px solid #dfe7ef;}
  [data-testid="stSidebar"] .block-container {padding-top: 1.15rem;}
  [data-testid="stSidebar"] .stButton > button {border-radius: 9px; font-weight: 650;}
  .hero-panel {
    background: linear-gradient(135deg, #f9fbfe 0%, #eef4fa 100%);
    border: 1px solid #dbe6f0;
    border-left: 5px solid #2f6ea5;
    border-radius: 16px;
    padding: 1.55rem 1.75rem 1.45rem;
    box-shadow: 0 8px 24px rgba(23, 58, 94, .07);
    margin-bottom: 1rem;
  }
  .hero-eyebrow {font-size: .72rem; letter-spacing: .105em; font-weight: 750; color: #52708d;}
  .hero-panel h1 {font-size: clamp(1.75rem, 3vw, 2.55rem); line-height: 1.18; margin: .42rem 0 .45rem; color: #173a5e;}
  .hero-panel p {font-size: 1rem; color: #53677b; margin: 0 0 1rem;}
  .method-tags {display: flex; flex-wrap: wrap; gap: .48rem;}
  .method-tags span {
    background: rgba(255,255,255,.82); border: 1px solid #cfdce9; color: #294f72;
    border-radius: 999px; padding: .28rem .68rem; font-size: .76rem; font-weight: 650;
  }
  .source-card {border-radius: 11px; padding: .78rem 1rem; margin: .45rem 0 1.1rem; border: 1px solid;}
  .source-card > div {display:flex; align-items:center; gap:.58rem; flex-wrap:wrap;}
  .source-card p {margin:.24rem 0 0; color:#5f6f7f; font-size:.86rem;}
  .source-card.demo {background:#fffaf0; border-color:#efd9a5; border-left:4px solid #d99a23;}
  .source-card.real {background:#f1faf5; border-color:#bfe0cc; border-left:4px solid #3b8d62;}
  .source-card.pending {background:#f5f8fb; border-color:#d8e1ea; border-left:4px solid #71869a;}
  .source-badge {font-size:.66rem; letter-spacing:.06em; font-weight:800; padding:.2rem .48rem; border-radius:999px; background:rgba(255,255,255,.82);}
  .section-heading {display:flex; align-items:flex-start; gap:.68rem; margin:1.45rem 0 .72rem;}
  .section-accent {width:4px; height:2rem; border-radius:999px; background:linear-gradient(180deg,#2f6ea5,#74a3c9); flex:0 0 auto;}
  .section-heading h2 {font-size:1.18rem; line-height:1.2; color:#173a5e; margin:0; font-weight:760;}
  .section-heading p {font-size:.82rem; color:#728196; margin:.24rem 0 0;}
  .kpi-card {
    min-height: 126px; padding:1rem 1.05rem; border-radius:13px; border:1px solid #dce5ee;
    background:linear-gradient(145deg,#ffffff 15%,#f5f8fb 100%); box-shadow:0 5px 15px rgba(23,58,94,.06);
    position:relative; overflow:hidden;
  }
  .kpi-card::before {content:""; position:absolute; inset:0 auto 0 0; width:4px; background:#537c9f;}
  .kpi-card.orange::before {background:#cb8731;} .kpi-card.violet::before {background:#7767a8;}
  .kpi-card.teal::before {background:#318b86;} .kpi-card.red::before {background:#b95b5b;}
  .kpi-label {font-size:.78rem; color:#617286; font-weight:680;}
  .kpi-value {font-size:clamp(1.45rem,2.2vw,2.05rem); line-height:1.1; color:#173a5e; font-weight:790; margin:.48rem 0 .42rem; white-space:nowrap;}
  .kpi-note {font-size:.72rem; color:#8793a2;}
  .quality-card {display:flex; align-items:center; justify-content:space-between; gap:.5rem; background:#f8fafc; border:1px solid #e0e7ef; border-radius:10px; padding:.72rem .78rem;}
  .quality-card span {font-size:.75rem; color:#6d7c8d;} .quality-card strong {font-size:1.12rem; color:#294b6a;}
  .quality-status {display:flex; align-items:center; justify-content:space-between; gap:1rem; flex-wrap:wrap; margin:.72rem 0 .25rem; padding:.7rem .9rem; border-radius:9px; border-left:4px solid;}
  .quality-status strong {font-size:.87rem;} .quality-status span {font-size:.8rem; color:#617083;}
  .quality-status.passed {background:#f0f8f3; border-color:#4f9568; color:#2e6c47;}
  .quality-status.attention {background:#fff8eb; border-color:#d39a37; color:#8a611e;}
  .severity-card {border:1px solid; border-left:4px solid; border-radius:11px; padding:.85rem .95rem; background:#fff; min-height:106px;}
  .severity-card .severity-badge {display:block; width:max-content; max-width:100%; border-radius:999px; padding:.2rem .52rem; font-size:.75rem; font-weight:720;}
  .severity-card strong {display:block; font-size:1.45rem; color:#33485c; margin:.36rem 0 .08rem;}
  .severity-card small {color:#7a8795; font-size:.72rem;}
  .severity-card.critical {background:#fff5f5; border-color:#e8bcbc; color:#9f4545;} .severity-card.critical .severity-badge {background:#f7dede;}
  .severity-card.moderate {background:#fff8ef; border-color:#edc99d; color:#9a651f;} .severity-card.moderate .severity-badge {background:#f8e5ca;}
  .severity-card.minor {background:#fffced; border-color:#eadb99; color:#826d18;} .severity-card.minor .severity-badge {background:#f4ebbd;}
  .severity-card.normal {background:#f1f8f4; border-color:#b9dbc5; color:#39724f;} .severity-card.normal .severity-badge {background:#dcefe3;}
  .risk-card {border:1px solid #dce5ee; border-top:3px solid #527da1; border-radius:11px; padding:.9rem 1rem; background:#fbfcfe; min-height:132px;}
  .risk-rank {font-size:.65rem; letter-spacing:.08em; color:#6f8295; font-weight:800; display:block; margin-bottom:.28rem;}
  .risk-card > strong {display:block; color:#234967; font-size:.95rem; min-height:2.35rem;}
  .risk-rate {font-size:1.35rem; color:#9b552f; font-weight:760; margin:.3rem 0 .1rem;} .risk-rate small {font-size:.68rem; color:#7a8795; font-weight:500;}
  .risk-card p {font-size:.73rem; color:#7b8997; margin:0;}
  .insight-label {display:inline-flex; align-items:center; width:max-content; border-radius:999px; padding:.25rem .62rem; font-size:.72rem; font-weight:760; letter-spacing:.025em; margin-bottom:.4rem;}
  .insight-label.local {background:#eaf1f8; color:#315d82;} .insight-label.ai {background:#eeeafb; color:#5c4e92;}
  .pipeline-card {background:#f8fafc; border:1px solid #dfe7ef; border-radius:12px; padding:1rem 1.15rem; color:#38536d; font-weight:620; line-height:1.85;}
  .capability-card {border:1px solid #e0e7ee; border-radius:10px; padding:.8rem .9rem; background:#fff; min-height:92px;}
  .capability-card strong {display:block; color:#274d6d; font-size:.86rem; margin-bottom:.28rem;} .capability-card span {color:#758395; font-size:.75rem; line-height:1.45;}
  .stTabs [data-baseweb="tab-list"] {gap:.2rem; border-bottom:1px solid #dce5ee;}
  .stTabs [data-baseweb="tab"] {height:2.85rem; padding:0 .82rem; color:#607286; font-weight:650; border-radius:8px 8px 0 0;}
  .stTabs [aria-selected="true"] {color:#173a5e; background:#eef4f9;}
  .stDownloadButton > button {border-radius:9px; font-weight:650;}
  @media (max-width: 900px) {
    .hero-panel {padding:1.2rem 1.1rem;} .kpi-card {min-height:112px; padding:.8rem;}
    .kpi-value {font-size:1.35rem;} .section-heading {margin-top:1.15rem;}
  }
</style>
""",
    unsafe_allow_html=True,
)


with st.sidebar:
    st.markdown("## 🚢 航运异常检测")
    st.caption("Shipping Anomaly Detection · Portfolio Edition")

    st.markdown("### 数据输入")
    data_mode = st.radio(
        "订单数据来源",
        ["使用模拟演示数据", "上传运输订单 CSV"],
        key="data_mode",
    )
    orders_upload = None
    if data_mode == "上传运输订单 CSV":
        orders_upload = st.file_uploader(
            "运输订单 CSV（必选）",
            type=["csv"],
            key="orders_upload",
            help="支持 UTF-8、UTF-8-SIG、GBK/GB18030，最大 25 MB。",
        )
    throughput_upload = st.file_uploader(
        "港口吞吐量 CSV（可选）",
        type=["csv"],
        key="throughput_upload",
        help="仅用于补充港口运营分析，不参与核心异常检测或投票。",
    )
    st.caption("吞吐量数据可选，仅补充港口运营背景，不会改变订单异常判定。")

    st.markdown("### 检测参数")
    zscore_threshold = st.slider(
        "Z-Score 阈值",
        min_value=1.5,
        max_value=4.0,
        value=2.5,
        step=0.1,
        key="zscore_widget",
        help="数值越低，统计异常检测越敏感；重新分析时会真实传入分组 Z-Score。",
    )
    contamination = st.slider(
        "Isolation Forest contamination",
        min_value=0.01,
        max_value=0.20,
        value=0.05,
        step=0.01,
        key="contamination_widget",
        help="Isolation Forest 预期异常样本比例；重新分析时会真实传入模型。",
    )

    st.markdown("### AI 模式")
    ai_mode = st.radio(
        "分析模式",
        ["自动业务摘要（无需 Key）", "DeepSeek AI（高级）", "关闭页面洞察"],
        key="ai_mode",
    )
    if ai_mode == "DeepSeek AI（高级）":
        st.text_input(
            "临时 DeepSeek API Key",
            type="password",
            key="deepseek_temp_input",
            placeholder="sk-...",
            help="优先读取 Secrets，其次环境变量，最后使用本次会话输入；不会写入文件或报告。",
        )
        _, key_source = resolve_api_key(st.session_state.deepseek_temp_input)
        if key_source:
            st.success(f"已检测到 Key 来源：{key_source}")
        else:
            st.info("未配置 Key；点击真实 AI 时会安全回退到本地摘要。")

    st.markdown("### 操作")
    analyse_clicked = st.button(
        "🚀 重新分析" if st.session_state.analysis_complete else "🚀 开始分析",
        type="primary",
        width="stretch",
        key="analysis_primary",
    )
    if st.session_state.analysis_complete:
        col_clear, col_reset = st.columns(2)
        with col_clear:
            if st.button("清除结果", width="stretch"):
                clear_analysis_state()
                st.rerun()
        with col_reset:
            if st.button("全部重置", width="stretch"):
                clear_analysis_state(reset_inputs=True)
                st.rerun()


signature = input_signature(data_mode, orders_upload, throughput_upload)
if analyse_clicked:
    try:
        detector_config = DetectorConfig(
            zscore_threshold=zscore_threshold,
            iforest_contamination=contamination,
        )
        with st.spinner("正在校验数据、运行三层检测并生成报告..."):
            analysis_bundle = run_analysis(
                data_mode,
                orders_upload,
                throughput_upload,
                detector_config,
                signature,
            )
        for filter_key in (
            "detail_severity",
            "detail_carrier",
            "detail_route",
            "carrier_select",
            "detail_explain_order",
        ):
            st.session_state.pop(filter_key, None)
        save_analysis_bundle(analysis_bundle)
        st.session_state.flash_message = "分析完成，结果已保存在当前会话中。"
        st.session_state.flash_level = "success"
        st.rerun()
    except DataValidationError as exc:
        st.error(str(exc))
    except ValueError as exc:
        st.error(str(exc))
    except Exception:
        st.error("分析未能完成。请检查 CSV 格式和字段内容后重试；平台未显示服务器路径或内部 traceback。")


bundle = st.session_state.analysis_bundle
if not st.session_state.analysis_complete or bundle is None:
    render_hero()
    if data_mode == "使用模拟演示数据":
        render_source_banner("demo")
    else:
        render_source_banner("pending", upload_ready=orders_upload is not None)
    render_section_title("分析流程", "从数据质量到业务洞察与可交付报告")
    st.markdown(
        '<div class="pipeline-card">CSV 数据校验 &nbsp;→&nbsp; 业务规则 &nbsp;→&nbsp; '
        '路线分组 Z-Score &nbsp;→&nbsp; Isolation Forest &nbsp;→&nbsp; 综合判断与严重等级 '
        '&nbsp;→&nbsp; KPI / 承运商 / 路线 &nbsp;→&nbsp; AI 洞察 &nbsp;→&nbsp; CSV / Word</div>',
        unsafe_allow_html=True,
    )
    capability_columns = st.columns(4)
    capabilities = [
        ("可解释异常定位", "逐条展示检测方法、风险分数、严重等级与中文原因。"),
        ("业务风险分析", "同时查看 KPI、承运商样本量修正和高风险路线。"),
        ("双模式洞察", "本地自动摘要无需 Key，DeepSeek 失败时安全回退。"),
        ("可交付输出", "下载异常 CSV、完整检测结果、图表与 Word 报告。"),
    ]
    for column, (title, description) in zip(capability_columns, capabilities):
        with column:
            st.markdown(
                f'<div class="capability-card"><strong>{title}</strong><span>{description}</span></div>',
                unsafe_allow_html=True,
            )
    with st.expander("查看运输订单 CSV 必要字段"):
        st.code(
            "订单ID, 承运商, 运输方式, 起运港, 目的港, 计划发货日期, 计划到达日期, "
            "实际到达日期, 货重_吨, 运费_USD",
            language="text",
        )
        st.caption("计划运输天数和实际延误天数会按日期统一重新计算；货物类型为可选字段。")
    st.stop()


render_hero()
render_source_banner(bundle["source"])

if st.session_state.flash_message:
    if st.session_state.get("flash_level") == "warning":
        st.warning(st.session_state.flash_message, icon=":material/info:")
    else:
        st.success(st.session_state.flash_message, icon=":material/check_circle:")
    st.session_state.flash_message = ""
    st.session_state.flash_level = "success"

current_config = (round(zscore_threshold, 4), round(contamination, 4))
saved_config = (
    round(bundle["config"].zscore_threshold, 4),
    round(bundle["config"].iforest_contamination, 4),
)
if current_config != saved_config or bundle["input_signature"] != signature:
    st.warning(
        "检测参数或数据来源已改变，请重新运行分析以更新结果。当前页面仍保留上一次分析结果。",
        icon=":material/tune:",
    )

display_quality_summary(bundle)

stats = bundle["stats"]
result = bundle["result"]
charts = bundle["charts"]

render_section_title("核心 KPI", "所有指标均由当前持久化检测结果统一计算")
render_kpi_cards(stats)

overview_tab, detail_tab, carrier_tab, route_tab, insight_tab, export_tab = st.tabs(
    ["📊 异常概览", "📋 异常明细", "🏢 承运商表现", "🗺️ 路线风险", "🤖 AI 洞察", "📥 导出结果"]
)

with overview_tab:
    render_section_title("异常概览", "三层检测命中情况与严重等级分布")
    st.image(charts["anomaly_overview"], width="stretch")
    render_section_title("严重等级", "颜色仅用于风险识别，数量来自统一 severity_counts")
    render_severity_cards(stats)
    render_section_title("延误分布", "查看整体延误形态与异常订单位置")
    st.image(charts["delay_distribution"], width="stretch")
    st.caption(
        "严重等级只由 src.detector.assign_severity 集中计算；UI、自动摘要和 Word 报告读取同一 severity_counts。"
    )

with detail_tab:
    render_section_title("异常明细", "按严重等级、承运商和路线定位需复核订单")
    anomaly_frame = result.loc[result["final_anomaly"].eq(1)].copy()
    severity_options = [
        level for level in ("严重", "中等", "轻微") if level in set(anomaly_frame["severity"])
    ]
    carrier_options, carriers_limited = category_options(anomaly_frame["承运商"])
    route_options, routes_limited = category_options(anomaly_frame["路线"])
    filter_columns = st.columns(3)
    with filter_columns[0]:
        selected_severity = st.multiselect("严重等级", severity_options, key="detail_severity")
    with filter_columns[1]:
        selected_carriers = st.multiselect("承运商", carrier_options, key="detail_carrier")
    with filter_columns[2]:
        selected_routes = st.multiselect("路线", route_options, key="detail_route")
    if carriers_limited or routes_limited:
        st.caption("类别超过 200 个时，仅按当前数据出现频次展示前 200 个筛选项。")

    filtered = anomaly_frame
    if selected_severity:
        filtered = filtered.loc[filtered["severity"].isin(selected_severity)]
    if selected_carriers:
        filtered = filtered.loc[filtered["承运商"].isin(selected_carriers)]
    if selected_routes:
        filtered = filtered.loc[filtered["路线"].isin(selected_routes)]

    display_columns = [
        "订单ID",
        "承运商",
        "路线",
        "severity",
        "anomaly_methods",
        "reason",
        "anomaly_score",
        "运输方式",
        "实际延误天数",
    ]
    display_frame = filtered[display_columns].rename(
        columns={
            "anomaly_methods": "异常方法",
            "anomaly_score": "异常分数",
            "severity": "严重等级",
            "reason": "异常原因",
        }
    )
    st.markdown(f"**筛选结果：{len(filtered):,} 条异常记录**")
    styled_frame = display_frame.style.map(severity_cell_style, subset=["严重等级"])
    st.dataframe(
        styled_frame,
        hide_index=True,
        height=460,
        column_config={
            "订单ID": st.column_config.TextColumn("订单号", width="medium", pinned=True),
            "承运商": st.column_config.TextColumn("承运商", width="medium"),
            "路线": st.column_config.TextColumn("路线", width="medium"),
            "严重等级": st.column_config.TextColumn("异常等级", width="small"),
            "异常方法": st.column_config.TextColumn("异常方法", width="medium"),
            "异常原因": st.column_config.TextColumn("异常原因", width="large"),
            "异常分数": st.column_config.NumberColumn("异常分数", format="%.1f", width="small"),
            "实际延误天数": st.column_config.NumberColumn(
                "实际延误天数", format="%.1f 天", width="small"
            ),
        },
    )
    if not filtered.empty:
        explain_options = filtered["订单ID"].astype(str).tolist()
        if st.session_state.get("detail_explain_order") not in explain_options:
            st.session_state.pop("detail_explain_order", None)
        with st.expander("查看异常解释"):
            selected_order = st.selectbox(
                "选择异常订单",
                explain_options,
                key="detail_explain_order",
            )
            selected_row = filtered.loc[
                filtered["订单ID"].astype(str).eq(selected_order)
            ].iloc[0]
            safe_order = html.escape(str(selected_order))
            safe_level = html.escape(str(selected_row["severity"]))
            safe_methods = html.escape(str(selected_row["anomaly_methods"]))
            st.markdown(
                f"**订单 {safe_order}**　·　异常等级：**{safe_level}**　·　"
                f"异常方法：**{safe_methods}**　·　风险分数：**{float(selected_row['anomaly_score']):.1f}**"
            )
            st.info(str(selected_row["reason"]), icon=":material/search_insights:")
    st.download_button(
        "下载当前筛选异常 CSV",
        data=dataframe_to_csv_bytes(filtered[display_columns]),
        file_name="shipping_anomalies_filtered.csv",
        mime="text/csv",
        icon=":material/download:",
    )

with carrier_tab:
    render_section_title("承运商表现", "风险排序已考虑样本量，避免少量订单造成异常率失真")
    render_risk_cards(
        stats["carrier_stats"],
        entity_column="承运商",
        total_column="总订单数",
        anomaly_column="异常订单数",
    )
    st.image(charts["carrier_performance"], width="stretch")
    carrier_display = stats["carrier_stats"].copy()
    for rate_column in ("延误率", "异常率", "调整后异常率"):
        carrier_display[rate_column] = carrier_display[rate_column] * 100
    st.dataframe(
        carrier_display,
        hide_index=True,
        column_config={
            "承运商": st.column_config.TextColumn("承运商", pinned=True, width="medium"),
            "总订单数": st.column_config.NumberColumn("订单量", format="%d"),
            "异常订单数": st.column_config.NumberColumn("异常数", format="%d"),
            "延误率": st.column_config.NumberColumn("延误率", format="%.1f%%"),
            "异常率": st.column_config.NumberColumn("原始异常率", format="%.1f%%"),
            "调整后异常率": st.column_config.NumberColumn(
                "调整风险率", format="%.1f%%", help="使用 10 条先验权重平滑后的风险率。"
            ),
        },
    )
    st.caption("风险排序使用带 10 条先验权重的调整后异常率，避免 1 单 1 异常直接被误判为最差承运商；同时保留原始订单量、异常数和异常率。")

    carrier_names = stats["carrier_stats"]["承运商"].astype(str).tolist()
    if st.session_state.get("carrier_select") not in carrier_names:
        st.session_state.pop("carrier_select", None)
    selected_carrier = st.selectbox("查看承运商洞察", carrier_names, key="carrier_select")
    carrier_row = stats["carrier_stats"].loc[
        stats["carrier_stats"]["承运商"].astype(str).eq(selected_carrier)
    ].iloc[0].to_dict()
    insight_key = selected_carrier
    carrier_insight = bundle["carrier_insights"].get(
        insight_key, generate_local_carrier_insight(selected_carrier, carrier_row)
    )
    with st.container(border=True):
        st.markdown('<span class="insight-label local">Local Insight</span>', unsafe_allow_html=True)
        st.markdown(carrier_insight)
    if ai_mode == "DeepSeek AI（高级）" and st.button("生成该承运商 DeepSeek 洞察"):
        api_key, _ = resolve_api_key(st.session_state.deepseek_temp_input)
        if not api_key:
            st.warning("未配置 API Key，保留本地承运商洞察。")
        else:
            try:
                with st.spinner("DeepSeek 正在分析该承运商..."):
                    carrier_insight = generate_ai_carrier_insight(
                        selected_carrier, carrier_row, api_key
                    )
                bundle["carrier_insights"][insight_key] = carrier_insight
                save_analysis_bundle(bundle)
                st.rerun()
            except AIServiceError:
                st.warning("AI 服务暂时不可用，已保留本地承运商洞察。")

with route_tab:
    render_section_title("路线风险", "Top Risk Routes 同时展示样本量、异常数与调整风险率")
    render_risk_cards(
        stats["route_stats"],
        entity_column="路线",
        total_column="订单数",
        anomaly_column="异常数",
    )
    if "port_throughput" in charts:
        st.image(charts["port_throughput"], width="stretch")
        st.info("港口吞吐量为可选运营背景，只用于本页和报告图表，不参与订单异常检测。")
    else:
        st.info("本次未提供港口吞吐量 CSV；核心订单检测和路线统计仍正常运行。")
    route_display = stats["route_stats"].copy()
    for rate_column in ("异常率", "调整后异常率"):
        route_display[rate_column] = route_display[rate_column] * 100
    st.dataframe(
        route_display,
        hide_index=True,
        column_config={
            "路线": st.column_config.TextColumn("起点 → 终点", pinned=True, width="medium"),
            "订单数": st.column_config.NumberColumn("样本量", format="%d"),
            "异常数": st.column_config.NumberColumn("异常数", format="%d"),
            "异常率": st.column_config.NumberColumn("原始异常率", format="%.1f%%"),
            "调整后异常率": st.column_config.NumberColumn(
                "风险指标", format="%.1f%%", help="使用 10 条先验权重平滑后的路线风险率。"
            ),
            "平均延误": st.column_config.NumberColumn("平均延误", format="%.1f 天"),
            "最大延误": st.column_config.NumberColumn("最大延误", format="%.1f 天"),
        },
    )
    st.caption("路线风险同样同时展示样本量、异常数、原始异常率与调整后异常率。")

with insight_tab:
    render_section_title("AI / 自动业务洞察", "洞察只读取结构化统计摘要，不上传完整原始 DataFrame")
    if ai_mode == "关闭页面洞察":
        st.info("页面洞察已关闭。Word 报告仍包含标记清楚的本地自动业务摘要。")
    else:
        if bundle["ai_source"] == "deepseek":
            badge_html = '<span class="insight-label ai">AI Enhanced</span>'
            source_note = "以下内容由 DeepSeek 基于当前结构化分析摘要生成。"
        else:
            badge_html = '<span class="insight-label local">Local Insight</span>'
            source_note = "根据当前检测结果自动生成，未调用大模型。"
        with st.container(border=True):
            st.markdown(badge_html, unsafe_allow_html=True)
            st.caption(source_note)
            st.markdown(bundle["ai_summary"])

    if ai_mode == "DeepSeek AI（高级）":
        if st.button(
            "生成 / 刷新 DeepSeek 分析",
            type="primary",
            icon=":material/auto_awesome:",
        ):
            api_key, _ = resolve_api_key(st.session_state.deepseek_temp_input)
            if not api_key:
                st.warning("未检测到 API Key，已继续使用本地自动业务摘要。")
            else:
                with st.spinner("DeepSeek 正在基于结构化摘要生成分析..."):
                    summary, source, warning = generate_summary_with_fallback(stats, api_key)
                bundle["ai_summary"] = summary
                bundle["ai_source"] = source
                bundle["report_bytes"] = rebuild_report(bundle)
                save_analysis_bundle(bundle)
                if warning:
                    st.session_state.flash_message = warning
                    st.session_state.flash_level = "warning"
                else:
                    st.session_state.flash_message = "DeepSeek 分析已生成，并已写入最新 Word 报告。"
                    st.session_state.flash_level = "success"
                st.rerun()

with export_tab:
    render_section_title("导出结果", "下载可复核的异常明细与完整分析报告")
    export_columns = [
        "订单ID",
        "承运商",
        "路线",
        "运输方式",
        "计划发货日期",
        "计划到达日期",
        "实际到达日期",
        "计划运输天数",
        "实际延误天数",
        "rule_anomaly",
        "zscore_anomaly",
        "iforest_anomaly",
        "anomaly_methods",
        "anomaly_score",
        "severity",
        "final_anomaly",
        "reason",
    ]
    anomaly_export = result.loc[result["final_anomaly"].eq(1), export_columns]
    csv_column, report_column = st.columns(2)
    with csv_column:
        with st.container(border=True):
            st.markdown("#### 异常结果 CSV")
            st.caption("包含异常方法、风险分数、严重等级和逐条中文原因。")
            st.download_button(
                "下载异常结果 CSV",
                data=dataframe_to_csv_bytes(anomaly_export),
                file_name="shipping_anomalies.csv",
                mime="text/csv",
                width="stretch",
                icon=":material/download:",
            )
    with report_column:
        with st.container(border=True):
            st.markdown("#### 完整分析 Word 报告")
            st.caption(f"当前洞察来源：{_report_label(bundle['ai_source'])}。")
            st.download_button(
                "下载 Word 分析报告",
                data=bundle["report_bytes"],
                file_name="shipping_anomaly_report.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                width="stretch",
                icon=":material/description:",
            )
    st.caption(
        "报告包含 KPI、异常检测、严重等级、承运商分析、路线风险及 AI / 自动业务摘要。"
    )

    with st.expander("更多数据与图表下载"):
        st.download_button(
            "下载完整检测结果 CSV",
            data=dataframe_to_csv_bytes(result[export_columns]),
            file_name="shipping_anomaly_results.csv",
            mime="text/csv",
            width="stretch",
            icon=":material/table_view:",
        )
        chart_labels = {
            "anomaly_overview": "异常检测总览",
            "carrier_performance": "承运商绩效",
            "delay_distribution": "延误分布",
            "port_throughput": "港口吞吐量背景",
        }
        chart_columns = st.columns(2)
        for index, (key, label) in enumerate(chart_labels.items()):
            if key not in charts:
                continue
            with chart_columns[index % 2]:
                st.download_button(
                    f"下载{label}",
                    data=charts[key],
                    file_name=f"{key}.png",
                    mime="image/png",
                    width="stretch",
                    key=f"download_{key}",
                    icon=":material/image:",
                )
