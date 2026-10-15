"""Render the report's figures from the live database into report/figures/.

    python -m scripts.make_figures      (needs requirements-dev.txt)
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.ticker  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from app.analysis.anomalies import rolling_z  # noqa: E402
from app.analysis.series import SNPP, fires_daily, load, with_total  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.timeutil import today_ist  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "report" / "figures"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GREY, AMBER, RED, INK, MUTED = "#8f8e86", "#fab219", "#d03b3b", "#1c1c19", "#55544c"

plt.rcParams.update({
    "font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#bdbbb2",
    "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED, "axes.titleweight": "bold",
    "axes.titlesize": 10, "axes.titlelocation": "left", "axes.grid": True, "grid.color": "#e6e5de",
    "axes.axisbelow": True, "figure.dpi": 160, "savefig.bbox": "tight",
})


def fire_seasonality() -> None:
    wide = fires_daily(satellite=SNPP)
    y = wide[(wide.index >= "2025-01-01") & (wide.index <= "2025-12-31")]
    monthly = y.sum(axis=1).groupby(y.index.month).sum()
    fig, ax = plt.subplots(figsize=(7, 2.8))
    ax.bar(monthly.index, monthly.values, color=ORANGE, width=0.8)
    ax.set_xticks(range(1, 13), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("detections / month")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_title("Fire hotspots in the 10 states, 2025 (VIIRS Suomi-NPP, cleaned)")
    top = monthly.idxmax()
    ax.annotate(f"{monthly.max():,.0f}", (top, monthly.max()), ha="center", va="bottom", color=INK, fontsize=8)
    fig.savefig(OUT / "fig1_fire_seasonality.png")
    plt.close(fig)


def fire_yoy() -> None:
    wide = fires_daily(satellite=SNPP)
    cur = wide[wide.index >= pd.Timestamp(today_ist().replace(day=1))].dropna(how="all")
    prev = wide.reindex(cur.index - pd.DateOffset(years=1))
    c, p = cur.sum(), prev.sum()
    order = c.sort_values().index
    fig, ax = plt.subplots(figsize=(7, 3.2))
    yy = range(len(order))
    ax.barh([i + 0.2 for i in yy], p[order], height=0.38, color=BLUE, label=f"{prev.index.min():%d}–{prev.index.max():%d %b %Y}")
    ax.barh([i - 0.2 for i in yy], c[order], height=0.38, color=ORANGE, label=f"{cur.index.min():%d}–{cur.index.max():%d %b %Y}")
    ax.set_yticks(list(yy), order)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("detections (Suomi-NPP only, same calendar days)")
    ax.set_title("Fire hotspots this October vs the same days last year")
    ax.legend(frameon=False, loc="lower right")
    fig.savefig(OUT / "fig2_fire_yoy.png")
    plt.close(fig)


def rain_anomaly(state: str = "Tamil Nadu") -> None:
    cfg = get_settings()
    s = load("rain")[state]
    rz = rolling_z(s, cfg.anomaly_window_days, cfg.anomaly_min_periods)
    rz = rz[(rz.index >= pd.Timestamp(today_ist() - timedelta(days=89))) & (rz.index <= pd.Timestamp(today_ist()))]
    fig, ax = plt.subplots(figsize=(7, 2.8))
    ax.fill_between(rz.index, (rz["mean"] - 2 * rz["std"]).clip(lower=0), rz["mean"] + 2 * rz["std"],
                    color=GREY, alpha=0.18, lw=0, label="±2σ band")
    ax.plot(rz.index, rz["mean"], color=GREY, lw=1.2, ls="--", label="30-day mean")
    ax.plot(rz.index, rz["value"], color=BLUE, lw=1.8, label="daily rainfall")
    for lvl, col, lab in [(cfg.z_high, RED, "z ≥ 3"), (cfg.z_medium, AMBER, "z ≥ 2")]:
        hit = rz[(rz["z"] >= lvl) & (rz["value"] >= 15) & ((rz["z"] < cfg.z_high) if lvl == cfg.z_medium else True)]
        ax.scatter(hit.index, hit["value"], s=36, color=col, edgecolor="white", lw=1.2, zorder=3, label=lab)
    ax.set_ylabel("mm / day")
    ax.set_title(f"Daily rainfall, {state} (state-capital point), last 90 days")
    ax.legend(frameon=False, ncol=5, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.savefig(OUT / "fig3_rain_anomaly.png")
    plt.close(fig)


def news_vs_hazard() -> None:
    news = load("news_flood")["India"]
    rain = with_total(load("rain"), "rain")["All states"]
    start = news.dropna().index.min()
    idx = pd.date_range(start, pd.Timestamp(today_ist()))
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7, 3.6), sharex=True)
    a1.bar(idx, news.reindex(idx), color=BLUE, width=0.85)
    a1.set_ylabel("articles / day")
    a1.set_title("Flood / cyclone news volume (GDELT, Indian sources)")
    a2.plot(idx, rain.reindex(idx), color=ORANGE, lw=1.8)
    a2.set_ylabel("mm / day")
    a2.set_title("Mean rainfall across the 10 state capitals")
    for a in (a1, a2):
        a.grid(axis="x", visible=False)
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.tight_layout()
    fig.savefig(OUT / "fig4_news_vs_rain.png")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for fn in (fire_seasonality, fire_yoy, rain_anomaly, news_vs_hazard):
        try:
            fn()
            print("ok", fn.__name__)
        except Exception as exc:  # noqa: BLE001
            print("failed", fn.__name__, exc)


if __name__ == "__main__":
    main()
