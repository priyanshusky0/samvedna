"""Does news coverage track hazards, and with what delay?

Spearman's rank correlation (robust to the heavy skew of counts and rainfall)
between a daily hazard series x_t and news volume y_{t+k}, for lags k = 0..3
days. A peak at k > 0 means coverage follows the physical signal.
"""
from __future__ import annotations

import pandas as pd
from scipy.stats import spearmanr

from app.analysis.series import load, news_articles_daily, with_total
from app.config import get_settings

# (hazard metric, aggregate column, news hazard tag, description)
PAIRS = [
    ("rain", "All states", "flood", "Mean rainfall (10 state capitals) vs flood/cyclone news"),
    ("fires", "All states", "fire", "Fire hotspots (all states) vs wildfire news"),
    ("tmax", "All states", "heat_drought", "Mean max temperature vs heat/drought news"),
    ("quakes", "All states", "earthquake", "Earthquakes M2.5+ vs earthquake news"),
]
MIN_OVERLAP = 20


def lagged_spearman(x: pd.Series, y: pd.Series, max_lag: int) -> list[dict]:
    out = []
    for k in range(max_lag + 1):
        df = pd.concat([x, y.shift(-k)], axis=1).dropna()
        if len(df) < MIN_OVERLAP or df.iloc[:, 0].nunique() < 3 or df.iloc[:, 1].nunique() < 3:
            out.append({"lag_days": k, "rho": None, "p_value": None, "n": len(df)})
            continue
        rho, p = spearmanr(df.iloc[:, 0], df.iloc[:, 1])
        out.append({"lag_days": k, "rho": float(rho), "p_value": float(p), "n": len(df)})
    return out


def _summarise(lags: list[dict]) -> dict:
    valid = [l for l in lags if l["rho"] is not None]
    if not valid:
        return {"best_lag": None, "best_rho": None, "best_p": None, "n": max((l["n"] for l in lags), default=0)}
    best = max(valid, key=lambda l: abs(l["rho"]))
    return {"best_lag": best["lag_days"], "best_rho": best["rho"], "best_p": best["p_value"], "n": best["n"]}


def run() -> list[dict]:
    max_lag = get_settings().max_lag_days
    rows = []
    for metric, col, hazard, desc in PAIRS:
        x = with_total(load(metric), metric).get(col)
        y = load(f"news_{hazard}").get("India")
        if x is None or y is None:
            continue
        lags = lagged_spearman(x.astype(float), y.astype(float), max_lag)
        rows.append({"pair": desc, "hazard_metric": metric, "news": hazard, "scope": "India",
                     "lags": lags, **_summarise(lags)})

    # Per-state: rainfall vs flood headlines geotagged to that state.
    rain = load("rain")
    flood_articles = news_articles_daily("flood")
    if not flood_articles.empty:
        start = flood_articles.index.min()
        for state in rain.columns:
            y = flood_articles[state] if state in flood_articles else None
            if y is None or y.sum() < 10:
                continue
            x = rain[state][rain.index >= start]
            lags = lagged_spearman(x, y.astype(float), max_lag)
            rows.append({"pair": f"Rainfall vs flood headlines mentioning {state}", "hazard_metric": "rain",
                         "news": "flood", "scope": state, "lags": lags, **_summarise(lags)})
    return rows
