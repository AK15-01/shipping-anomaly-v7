"""Local business summaries and optional DeepSeek analysis with safe fallback."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Callable, Optional

import pandas as pd

from .business_metrics import get_severity_count


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"


class AIServiceError(RuntimeError):
    """Raised for all recoverable external AI failures."""


def _call_deepseek(api_key: str, prompt: str, timeout: float = 30.0) -> str:
    """Call DeepSeek without logging or persisting the API key."""

    if not api_key or not api_key.strip():
        raise AIServiceError("未提供 DeepSeek API Key。")
    payload = json.dumps(
        {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一位资深航运物流数据分析师。只使用用户提供的统计数据，"
                        "不得编造数字；明确区分统计发现与可能原因；数据不足时直接说明。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 900,
            "stream": False,
            "thinking": {"type": "disabled"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise AIServiceError(f"AI 服务返回 HTTP {status}。")
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise AIServiceError(f"AI 服务返回 HTTP {exc.code}，请检查 Key 或稍后重试。") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise AIServiceError("AI 服务连接超时或网络不可用。") from None
    except AIServiceError:
        raise
    except Exception:
        raise AIServiceError("AI 服务请求失败。") from None

    try:
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise AIServiceError("AI 服务返回了无法解析的响应。") from None
    if not isinstance(content, str) or not content.strip():
        raise AIServiceError("AI 服务返回内容为空。")
    return content.strip()


def _format_carriers(stats: dict, limit: int = 5) -> str:
    carrier_stats = stats.get("carrier_stats")
    if not isinstance(carrier_stats, pd.DataFrame) or carrier_stats.empty:
        return "- 数据不足"
    lines = []
    for _, row in carrier_stats.head(limit).iterrows():
        lines.append(
            f"- {row['承运商']}：订单 {int(row['总订单数'])}，异常 {int(row['异常订单数'])}，"
            f"异常率 {float(row['异常率']) * 100:.1f}%，平均延误 {float(row['平均延误天数']):.1f} 天，"
            f"样本提示 {row['样本提示']}"
        )
    return "\n".join(lines)


def _format_routes(stats: dict, limit: int = 5) -> str:
    route_stats = stats.get("route_stats")
    if not isinstance(route_stats, pd.DataFrame) or route_stats.empty:
        return "- 数据不足"
    lines = []
    for _, row in route_stats.head(limit).iterrows():
        lines.append(
            f"- {row['路线']}：订单 {int(row['订单数'])}，异常 {int(row['异常数'])}，"
            f"异常率 {float(row['异常率']) * 100:.1f}%，最大延误 {float(row['最大延误']):.1f} 天，"
            f"样本提示 {row['样本提示']}"
        )
    return "\n".join(lines)


def build_analysis_prompt(stats: dict) -> str:
    """Build a bounded, traceable prompt from structured statistics only."""

    date_range = stats.get("date_range", {})
    reasons = stats.get("top_reason_counts", {})
    reason_text = "\n".join(f"- {reason}：{count} 条" for reason, count in list(reasons.items())[:6])
    if not reason_text:
        reason_text = "- 暂无可归纳的规则原因"

    top_cases = stats.get("top_anomalies")
    case_lines = []
    if isinstance(top_cases, pd.DataFrame):
        for _, row in top_cases.head(5).iterrows():
            case_lines.append(
                f"- {row['订单ID']} | {row['承运商']} | {row['路线']} | "
                f"{row['severity']} | 分数 {row['anomaly_score']} | {row['anomaly_methods']}"
            )
    case_text = "\n".join(case_lines) or "- 无代表性异常案例"

    return f"""
请基于以下结构化检测摘要输出 300-500 字中文业务分析。

【数据范围】
- 时间：{date_range.get('start') or '未知'} 至 {date_range.get('end') or '未知'}
- 订单总数：{stats['total_orders']}
- 异常订单：{stats['total_anomalies']}
- 异常率：{stats['anomaly_rate'] * 100:.2f}%
- 平均延误：{stats['avg_delay']} 天；最长延误：{stats['max_delay']} 天
- 严重/中等/轻微异常：{get_severity_count(stats, '严重')}/{get_severity_count(stats, '中等')}/{get_severity_count(stats, '轻微')}

【高风险承运商（排序已考虑样本量）】
{_format_carriers(stats)}

【高风险路线（排序已考虑样本量）】
{_format_routes(stats)}

【主要规则原因】
{reason_text}

【代表性案例】
{case_text}

输出必须覆盖：1）异常总体情况；2）最主要风险；3）需关注的承运商/路线；
4）可能的业务原因；5）下一步行动建议。不得编造输入中不存在的数据；
把“统计发现”和“可能原因”明确分开；样本量较少时必须提示不确定性。
""".strip()


def generate_local_summary(stats: dict) -> str:
    """Generate a truthful local business summary without any external API."""

    rate = float(stats.get("anomaly_rate", 0))
    if rate >= 0.15:
        risk_label = "较高"
    elif rate >= 0.05:
        risk_label = "需要关注"
    else:
        risk_label = "相对较低"

    carriers = stats.get("carrier_stats")
    if isinstance(carriers, pd.DataFrame) and not carriers.empty:
        carrier = carriers.iloc[0]
        carrier_text = (
            f"{carrier['承运商']}（{int(carrier['总订单数'])} 票，"
            f"异常 {int(carrier['异常订单数'])} 票，异常率 {float(carrier['异常率']) * 100:.1f}%）"
        )
        if carrier["样本提示"] == "样本较少":
            carrier_text += "；该样本较少，不宜单独据此判定承运商优劣"
    else:
        carrier_text = "暂无足够承运商数据"

    routes = stats.get("route_stats")
    if isinstance(routes, pd.DataFrame) and not routes.empty:
        route = routes.iloc[0]
        route_text = (
            f"{route['路线']}（{int(route['订单数'])} 票，"
            f"异常率 {float(route['异常率']) * 100:.1f}%）"
        )
        if route["样本提示"] == "样本较少":
            route_text += "；需补充样本后复核"
    else:
        route_text = "暂无足够路线数据"

    reasons = stats.get("top_reason_counts", {})
    reason_text = "、".join(f"{reason}（{count} 条）" for reason, count in list(reasons.items())[:3])
    if not reason_text:
        reason_text = "当前没有足够的规则命中原因可归纳"

    return f"""
### 自动业务摘要

- **总体情况：** 共分析 **{int(stats['total_orders']):,}** 票订单，识别异常 **{int(stats['total_anomalies']):,}** 票，异常率 **{rate * 100:.1f}%**，当前风险水平**{risk_label}**。严重异常 **{get_severity_count(stats, '严重')}** 票，中等异常 **{get_severity_count(stats, '中等')}** 票。
- **重点对象：** 调整样本量影响后，当前排序靠前的承运商为 {carrier_text}；路线为 {route_text}。
- **统计发现：** 主要规则原因包括 {reason_text}。这些是检测结果中的事实汇总。
- **可能原因：** 港口拥堵、天气、船期变更、计划缓冲不足或数据录入问题均可能造成异常，但仅凭当前订单数据无法确认具体因果，需要结合业务记录复核。
- **建议：** 优先人工核查严重异常及高分订单；按“订单量、异常数、异常率”联合复盘承运商；对高风险路线检查计划时长和节点衔接。

> 此内容由本地规则根据真实检测结果自动生成，未调用大模型。
""".strip()


def generate_ai_summary(stats: dict, api_key: str, timeout: float = 30.0) -> str:
    return _call_deepseek(api_key, build_analysis_prompt(stats), timeout=timeout)


def generate_summary_with_fallback(
    stats: dict,
    api_key: Optional[str] = None,
    call_fn: Optional[Callable[[dict, str], str]] = None,
) -> tuple[str, str, Optional[str]]:
    """Return summary, source label, and optional user-safe fallback warning."""

    if not api_key or not api_key.strip():
        return generate_local_summary(stats), "local", None
    caller = call_fn or generate_ai_summary
    try:
        return caller(stats, api_key.strip()), "deepseek", None
    except AIServiceError:
        return (
            generate_local_summary(stats),
            "local",
            "AI 服务暂时不可用，已切换为本地业务摘要。",
        )
    except Exception:
        return (
            generate_local_summary(stats),
            "local",
            "AI 服务暂时不可用，已切换为本地业务摘要。",
        )


def generate_local_carrier_insight(carrier_name: str, carrier_data: dict) -> str:
    sample_note = (
        "样本量较少，当前结论仅供初步观察。"
        if int(carrier_data.get("总订单数", 0)) < 5
        else "样本量可用于当前数据集内的横向比较。"
    )
    return (
        f"**{carrier_name} 自动洞察：** 共 {int(carrier_data['总订单数'])} 票订单，"
        f"异常 {int(carrier_data['异常订单数'])} 票（{float(carrier_data['异常率']) * 100:.1f}%），"
        f"平均延误 {float(carrier_data['平均延误天数']):.1f} 天。{sample_note}"
        "建议结合路线分布和具体异常原因进行复核。"
    )


def generate_ai_carrier_insight(
    carrier_name: str,
    carrier_data: dict,
    api_key: str,
    timeout: float = 30.0,
) -> str:
    prompt = f"""
承运商「{carrier_name}」的当前数据集统计如下：
- 订单量：{int(carrier_data['总订单数'])}
- 平均延误：{float(carrier_data['平均延误天数']):.2f} 天
- 延误率：{float(carrier_data['延误率']) * 100:.2f}%
- 异常订单：{int(carrier_data['异常订单数'])}
- 异常率：{float(carrier_data['异常率']) * 100:.2f}%
- 样本提示：{carrier_data.get('样本提示', '未知')}

请用 2-3 句话给出绩效观察和建议。不得编造数据；样本较少时必须提示不确定性。
""".strip()
    return _call_deepseek(api_key, prompt, timeout=timeout)
