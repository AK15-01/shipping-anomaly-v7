from __future__ import annotations

from src.ai_analyst import (
    AIServiceError,
    build_analysis_prompt,
    generate_local_summary,
    generate_summary_with_fallback,
)
from src.business_metrics import build_summary_stats


def test_demo_mode_never_requires_api(metrics_result):
    stats = build_summary_stats(metrics_result)

    def should_not_run(*_args):
        raise AssertionError("API should not be called")

    summary, source, warning = generate_summary_with_fallback(
        stats, api_key="", call_fn=should_not_run
    )
    assert source == "local"
    assert warning is None
    assert "未调用大模型" in summary
    assert "严重异常 **9**" in summary


def test_api_failure_falls_back_to_local_summary(metrics_result):
    stats = build_summary_stats(metrics_result)

    def failing_call(*_args):
        raise AIServiceError("simulated failure")

    summary, source, warning = generate_summary_with_fallback(
        stats, api_key="wrong-key", call_fn=failing_call
    )
    assert source == "local"
    assert warning == "AI 服务暂时不可用，已切换为本地业务摘要。"
    assert "自动业务摘要" in summary


def test_prompt_contains_traceable_statistics(metrics_result):
    stats = build_summary_stats(metrics_result)
    prompt = build_analysis_prompt(stats)
    assert "订单总数：10" in prompt
    assert "异常订单：9" in prompt
    assert "严重/中等/轻微异常：9/0/0" in prompt
    assert "不得编造" in prompt
    assert "M-0" in prompt


def test_local_summary_uses_true_severity_count(metrics_result):
    stats = build_summary_stats(metrics_result)
    summary = generate_local_summary(stats)
    assert "严重异常 **9** 票" in summary
