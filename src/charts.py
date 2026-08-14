"""
图表生成模块 - 生成报告所需的所有可视化图表
"""
import os
import platform
import tempfile
import pandas as pd

# Use an ephemeral writable cache on local/hosted runtimes; never write into the project.
os.environ.setdefault(
    "MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "shipping_anomaly_matplotlib")
)
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)
import matplotlib
matplotlib.use("Agg")           # 无界面环境下必须在 import pyplot 前设置
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np


def _setup_chinese_font():
    """
    跨平台中文字体配置
    优先级：Windows系统字体 → Linux系统字体 → 项目内置字体 → 无中文支持
    """
    # ── 1. Windows ──────────────────────────────────────────────
    if platform.system() == "Windows":
        for name in ["Microsoft YaHei", "SimHei", "FangSong"]:
            if any(f.name == name for f in fm.fontManager.ttflist):
                plt.rcParams["font.family"] = name
                return

    # ── 2. Linux / Streamlit Cloud ──────────────────────────────
    linux_fonts = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for path in linux_fonts:
        if os.path.exists(path):
            fm.fontManager.addfont(path)
            prop = fm.FontProperties(fname=path)
            plt.rcParams["font.family"] = prop.get_name()
            return

    # ── 3. macOS ─────────────────────────────────────────────────
    mac_fonts = [
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ]
    for path in mac_fonts:
        if os.path.exists(path):
            fm.fontManager.addfont(path)
            prop = fm.FontProperties(fname=path)
            plt.rcParams["font.family"] = prop.get_name()
            return

    # ── 4. 项目内置字体（兜底）──────────────────────────────────
    # 如果以上都找不到，尝试项目 fonts/ 目录
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(base, "fonts", "NotoSansSC-Regular.ttf")
    if os.path.exists(local):
        fm.fontManager.addfont(local)
        prop = fm.FontProperties(fname=local)
        plt.rcParams["font.family"] = prop.get_name()
        return

    # 完全找不到中文字体：用默认字体，中文会显示方块，但不崩溃
    print("[charts] 警告：未找到中文字体，图表中文可能显示为方块")


_setup_chinese_font()
plt.rcParams["axes.unicode_minus"] = False   # 负号正常显示

COLORS = {
    "primary": "#1A3C5E",
    "accent":  "#E8412B",
    "warn":    "#F5A623",
    "ok":      "#27AE60",
    "light":   "#EEF2F7",
    "mid":     "#6B8CAE",
}


def _save(fig, name, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, name)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ── 异常总览（3 图）────────────────────────────────────────────
def plot_anomaly_overview(stats, output_dir):
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fig.patch.set_facecolor("white")

    # 1. 严重等级饼图
    ax   = axes[0]
    severity_order = ["严重", "中等", "轻微", "正常"]
    severity_counts = stats["severity_counts"]
    dist = pd.Series(
        {level: int(severity_counts.get(level, 0)) for level in severity_order}
    )
    dist = dist[dist > 0]
    cmap = {"严重": COLORS["accent"], "中等": COLORS["warn"],
             "轻微": COLORS["mid"],    "正常": COLORS["ok"]}
    colors = [cmap.get(k, "#ccc") for k in dist.index]
    ax.pie(
        dist.values,
        labels=dist.index,
        colors=colors,
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontsize": 10},
    )
    ax.set_title("异常等级分布", fontsize=13, fontweight="bold", color=COLORS["primary"])

    # 2. 承运商异常率
    ax = axes[1]
    cs = stats["carrier_stats"].sort_values("异常率")
    bar_colors = [COLORS["accent"] if v > 0.1 else COLORS["mid"] for v in cs["异常率"]]
    bars = ax.barh(cs["承运商"], cs["异常率"] * 100, color=bar_colors)
    ax.axvline(cs["异常率"].mean() * 100, color=COLORS["warn"],
               linestyle="--", linewidth=1.5, label="均值")
    ax.set_xlabel("异常率 (%)", fontsize=10)
    ax.set_title("各承运商异常率", fontsize=13, fontweight="bold", color=COLORS["primary"])
    ax.legend(fontsize=9)
    for bar, val in zip(bars, cs["异常率"]):
        ax.text(bar.get_width() + 0.2, bar.get_y() + bar.get_height() / 2,
                f"{val*100:.1f}%", va="center", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)

    # 3. 月度异常趋势
    ax = axes[2]
    mt = stats["monthly_trend"].copy().sort_values(["发货年", "发货月"])
    mt["标签"] = mt["发货年"].astype(str) + "-" + mt["发货月"].astype(str).str.zfill(2)
    x  = range(len(mt))
    ax.plot(x, mt["异常率"] * 100, color=COLORS["accent"],
            linewidth=2, marker="o", markersize=4)
    ax.fill_between(x, mt["异常率"] * 100, alpha=0.15, color=COLORS["accent"])
    tick_step = max(1, int(np.ceil(len(mt) / 8)))
    tick_positions = list(range(0, len(mt), tick_step))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(mt["标签"].iloc[tick_positions], rotation=30, fontsize=8)
    ax.set_ylabel("异常率 (%)", fontsize=10)
    ax.set_title("月度异常率趋势", fontsize=13, fontweight="bold", color=COLORS["primary"])
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    return _save(fig, "anomaly_overview.png", output_dir)


# ── 承运商绩效（2 图）─────────────────────────────────────────
def plot_carrier_performance(stats, output_dir):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    cs = stats["carrier_stats"]

    # 散点矩阵
    ax    = axes[0]
    sizes = cs["总订单数"] / max(float(cs["总订单数"].max()), 1.0) * 400 + 100
    sc    = ax.scatter(cs["平均延误天数"], cs["异常率"] * 100,
                       s=sizes, c=cs["异常率"], cmap="RdYlGn_r",
                       alpha=0.85, edgecolors="white")
    for _, row in cs.iterrows():
        ax.annotate(row["承运商"], (row["平均延误天数"], row["异常率"] * 100),
                    fontsize=9, ha="center", va="bottom", color=COLORS["primary"])
    ax.set_xlabel("平均延误天数", fontsize=11)
    ax.set_ylabel("异常率 (%)", fontsize=11)
    ax.set_title("承运商表现矩阵\n（气泡大小=订单量）",
                 fontsize=12, fontweight="bold", color=COLORS["primary"])
    ax.spines[["top", "right"]].set_visible(False)
    plt.colorbar(sc, ax=ax, label="异常率")

    # 柱状图
    ax   = axes[1]
    mean = cs["平均延误天数"].mean()
    bar_colors = [COLORS["accent"] if v > mean else COLORS["ok"]
                  for v in cs["平均延误天数"]]
    bars = ax.bar(range(len(cs)), cs["平均延误天数"],
                  color=bar_colors, width=0.6, edgecolor="white")
    ax.set_xticks(range(len(cs)))
    ax.set_xticklabels(cs["承运商"], fontsize=10)
    ax.set_ylabel("平均延误天数", fontsize=11)
    ax.set_title("承运商平均延误天数",
                 fontsize=12, fontweight="bold", color=COLORS["primary"])
    ax.axhline(mean, color=COLORS["warn"], linestyle="--", linewidth=1.5)
    for bar, val in zip(bars, cs["平均延误天数"]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                f"{val:.1f}d", ha="center", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    return _save(fig, "carrier_performance.png", output_dir)


# ── 港口吞吐量（2 图）─────────────────────────────────────────
def plot_port_throughput(throughput_df, output_dir):
    if throughput_df is None or throughput_df.empty:
        raise ValueError("港口吞吐量数据为空，无法生成港口图表。")
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    top_ports = (
        throughput_df.groupby("港口")["货物吞吐量_万吨"]
        .sum()
        .nlargest(5)
        .index.tolist()
    )
    port_colors = [COLORS["primary"], COLORS["accent"], COLORS["ok"],
                   COLORS["warn"],    COLORS["mid"]]

    # 年度趋势折线
    ax = axes[0]
    for port, color in zip(top_ports, port_colors):
        sub    = throughput_df[throughput_df["港口"] == port]
        annual = sub.groupby("年份")["货物吞吐量_万吨"].sum().reset_index()
        ax.plot(annual["年份"], annual["货物吞吐量_万吨"] / 10000,
                marker="o", color=color, linewidth=2, label=port)
    ax.set_xlabel("年份", fontsize=11)
    ax.set_ylabel("年吞吐量（亿吨）", fontsize=11)
    ax.set_title("主要港口年度吞吐量趋势",
                 fontsize=12, fontweight="bold", color=COLORS["primary"])
    ax.legend(fontsize=9, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)

    # 季节性热力图
    ax    = axes[1]
    pivot = throughput_df[throughput_df["港口"].isin(top_ports)].pivot_table(
        values="货物吞吐量_万吨", index="港口", columns="月份", aggfunc="mean"
    )
    pivot = pivot.reindex(index=top_ports, columns=range(1, 13))
    pivot = pivot.apply(lambda row: row.fillna(row.mean()), axis=1).fillna(0)
    row_means = pivot.mean(axis=1).replace(0, 1)
    pivot_norm = pivot.div(row_means, axis=0)
    im = ax.imshow(pivot_norm.values, cmap="RdYlGn", aspect="auto", vmin=0.7, vmax=1.3)
    ax.set_xticks(range(12))
    ax.set_xticklabels([f"{m}月" for m in range(1, 13)], fontsize=9)
    ax.set_yticks(range(len(pivot_norm.index)))
    ax.set_yticklabels(pivot_norm.index, fontsize=9)
    ax.set_title("港口月度吞吐量季节性热力图\n（相对于年均值）",
                 fontsize=12, fontweight="bold", color=COLORS["primary"])
    plt.colorbar(im, ax=ax, label="相对强度")

    plt.tight_layout()
    return _save(fig, "port_throughput.png", output_dir)


# ── 延误分布（2 图）──────────────────────────────────────────
def plot_delay_distribution(result_df, output_dir):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # 正常 vs 异常直方图
    ax      = axes[0]
    normal  = result_df[result_df["final_anomaly"] == 0]["实际延误天数"]
    anomaly = result_df[result_df["final_anomaly"] == 1]["实际延误天数"]
    if not normal.empty:
        ax.hist(normal, bins=30, color=COLORS["ok"], alpha=0.6, label="正常", density=True)
    if not anomaly.empty:
        ax.hist(anomaly, bins=20, color=COLORS["accent"], alpha=0.7, label="异常", density=True)
    ax.set_xlabel("延误天数", fontsize=11)
    ax.set_ylabel("频率密度", fontsize=11)
    ax.set_title("延误天数分布：正常 vs 异常",
                 fontsize=12, fontweight="bold", color=COLORS["primary"])
    if not normal.empty or not anomaly.empty:
        ax.legend(fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)

    # 运输方式箱线图
    ax    = axes[1]
    modes = result_df["运输方式"].unique()
    data  = [result_df[result_df["运输方式"] == m]["实际延误天数"].values for m in modes]
    bp = ax.boxplot(
        data,
        patch_artist=True,
        notch=False,
        medianprops={"color": COLORS["accent"], "linewidth": 2},
    )
    ax.set_xticks(range(1, len(modes) + 1))
    ax.set_xticklabels(modes)
    box_colors = [COLORS["primary"], COLORS["mid"], COLORS["ok"]]
    for index, patch in enumerate(bp["boxes"]):
        color = box_colors[index % len(box_colors)]
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_ylabel("延误天数", fontsize=11)
    ax.set_title("不同运输方式的延误分布",
                 fontsize=12, fontweight="bold", color=COLORS["primary"])
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    return _save(fig, "delay_distribution.png", output_dir)
