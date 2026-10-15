"""Recompute alerts, trends, correlation, seasonality and NLP outputs.

    python -m scripts.run_analysis
"""
from __future__ import annotations

import json
import logging


from app.analysis import anomalies, correlation, nlp, trends
from app.db import session_scope
from app.fetchers.gdelt import HAZARD_QUERIES
from app.models import AnalysisResult
from app.timeutil import now_ist

log = logging.getLogger("samvedna.analysis")


def _save(kind: str, payload) -> None:
    with session_scope() as s:
        s.merge(AnalysisResult(kind=kind, computed_at=now_ist(),
                               payload=json.dumps(payload, default=str, allow_nan=False)))


def _clean_nan(obj):
    """JSON has no NaN: replace with None recursively."""
    if isinstance(obj, float) and obj != obj:
        return None
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    return obj


def run_all() -> dict:
    results = {}
    steps = {
        "alerts": lambda: len(anomalies.run()),
        "trends": trends.run_trends,
        "seasonal": trends.run_seasonal,
        "correlation": correlation.run,
        "nlp": lambda: nlp.run(list(HAZARD_QUERIES)),
    }
    for kind, fn in steps.items():
        try:
            results[kind] = fn()
            if kind != "alerts":
                _save(kind, _clean_nan(results[kind]))
        except Exception:
            log.exception("analysis step %s failed", kind)
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    res = run_all()
    print(f"\nalerts: {res.get('alerts')}")
    from sqlalchemy import select
    from app.models import Alert
    with session_scope() as s:
        for a in s.scalars(select(Alert).order_by(Alert.z_score.desc()).limit(8)):
            print(f"  [{a.severity}] {a.message}")
    print("\ntrends (p < 0.05):")
    for t in res.get("trends", []):
        if t["p_value"] < 0.05 and t["region"] in ("All states", "India"):
            print(f"  {t['label']} / {t['region']}: {t['trend']}, Sen slope {t['sens_slope']:.2f} {t['unit']}, "
                  f"p={t['p_value']:.3g} ({t['start']}..{t['end']})")
    print("\ncorrelation:")
    for c in res.get("correlation", []):
        if c["best_rho"] is not None:
            print(f"  {c['pair']}: rho={c['best_rho']:.2f} at lag {c['best_lag']}d (p={c['best_p']:.3g}, n={c['n']})")
    print("\nseasonal (All states):")
    for r in res.get("seasonal", []):
        if r["region"] == "All states":
            print(f"  {r['label']}: {r['current']:.2f} vs {r['last_year']:.2f} {r['unit']} "
                  f"({r['change_pct']:+.0f}%)" if r["change_pct"] is not None else "")
    for hz, d in res.get("nlp", {}).items():
        print(f"\nnlp {hz}: {d['n_articles']} articles; keywords: {[k['term'] for k in d['keywords'][:8]]}")


if __name__ == "__main__":
    main()
