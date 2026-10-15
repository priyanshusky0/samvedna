import numpy as np
import pandas as pd

from app.analysis.anomalies import rolling_z, severity, stl_residual_z
from app.analysis.correlation import lagged_spearman
from app.analysis.nlp import top_keywords, topics
from app.analysis.trends import mann_kendall, weekly


def test_rolling_z_excludes_today_and_flags_spike():
    s = pd.Series([10.0] * 20 + [11.0] * 10 + [40.0], index=pd.date_range("2026-01-01", periods=31))
    rz = rolling_z(s, 30, 14)
    last = rz.iloc[-1]
    assert last["mean"] < 11  # the spike itself is not in its own baseline
    assert last["z"] > 3 and severity(last["z"]) == "high"
    assert severity(2.5) == "medium" and severity(1.0) is None


def test_rolling_z_constant_baseline_gives_nan_not_inf():
    s = pd.Series([5.0] * 20 + [9.0], index=pd.date_range("2026-01-01", periods=21))
    assert np.isnan(rolling_z(s, 30, 14)["z"].iloc[-1])


def test_stl_requires_three_years():
    idx = pd.date_range("2024-01-01", periods=800)
    assert stl_residual_z(pd.Series(np.sin(np.arange(800) / 58), index=idx)) is None


def test_stl_flags_injected_spike():
    rng = np.random.default_rng(0)
    n = 5 * 365 + 30
    idx = pd.date_range("2021-09-01", periods=n)
    s = pd.Series(30 + 5 * np.sin(2 * np.pi * np.arange(n) / 365) + rng.normal(0, 0.8, n), index=idx)
    s.iloc[-40] += 5
    z = stl_residual_z(s)
    assert z is not None and z["z"].iloc[-40] > 3.5


def test_mann_kendall_detects_increase():
    idx = pd.date_range("2026-01-01", periods=7 * 30)
    s = pd.Series(np.arange(len(idx)) * 0.1 + np.random.default_rng(1).normal(0, 0.5, len(idx)), index=idx)
    w = weekly(s, "mean")
    res = mann_kendall(w)
    assert res["trend"] == "increasing" and res["p_value"] < 0.05 and res["sens_slope"] > 0


def test_weekly_drops_incomplete_weeks():
    idx = pd.date_range("2026-01-05", periods=21)  # Mon..Sun x3
    s = pd.Series(1.0, index=idx)
    s.iloc[3] = np.nan
    assert len(weekly(s, "sum")) == 2


def test_lagged_spearman_finds_lag():
    rng = np.random.default_rng(2)
    idx = pd.date_range("2026-01-01", periods=80)
    x = pd.Series(rng.gamma(1, 5, 80), index=idx)
    y = x.shift(2) + rng.normal(0, 0.5, 80)  # news follows hazard by 2 days
    res = lagged_spearman(x, y, 3)
    best = max((r for r in res if r["rho"] is not None), key=lambda r: r["rho"])
    assert best["lag_days"] == 2 and best["rho"] > 0.8


def test_nlp_keywords_and_topics():
    titles = ["Flood alert in Kerala as rivers rise"] * 6 + ["Cyclone nears Andhra coast, fishermen warned"] * 6 \
        + ["Landslide blocks highway in Wayanad"] * 6 + ["Heavy rain lashes Mumbai, trains delayed"] * 6
    df = pd.DataFrame({"title": titles, "url": [f"u{i}" for i in range(len(titles))]})
    kws = [k["term"] for k in top_keywords(df["title"])]
    assert "india" not in kws and len(kws) > 5
    tps = topics(df, 4)
    assert len(tps) >= 3 and abs(sum(t["share"] for t in tps) - 1) < 1e-6


def test_seasonal_mann_kendall_ignores_annual_cycle():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2021-01-03", periods=5 * 364, freq="D")
    cycle = 10 * np.sin(2 * np.pi * np.arange(len(idx)) / 364)
    flat = pd.Series(30 + cycle + rng.normal(0, 1, len(idx)), index=idx)
    res = mann_kendall(weekly(flat, "mean", max_weeks=None))
    assert res["test"].startswith("Seasonal") and res["trend"] == "no trend"
    warming = flat + np.arange(len(idx)) * (0.5 / 364)  # +0.5 °C per year
    res = mann_kendall(weekly(warming, "mean", max_weeks=None))
    assert res["trend"] == "increasing" and 0.3 < res["sens_slope"] < 0.7
