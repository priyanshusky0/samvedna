"""Pull the last N days from every source, then run the analysis.

    python -m scripts.backfill               # 90 days, all sources
    python -m scripts.backfill --only gdelt  # a single source
    python -m scripts.backfill --if-empty    # no-op if data already exists (Docker)
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.fetchers import eonet, firms, gdelt, openmeteo, usgs
from app.models import Earthquake, FetchLog, FireHotspot, NaturalEvent, NewsArticle, NewsTimeline, WeatherDaily

log = logging.getLogger("samvedna.backfill")


def counts() -> dict[str, tuple[int, str, str]]:
    """rows, min date, max date per table."""
    tables = {
        "fire_hotspots": (FireHotspot, FireHotspot.acq_datetime_ist),
        "earthquakes": (Earthquake, Earthquake.time_ist),
        "weather_daily": (WeatherDaily, WeatherDaily.date),
        "natural_events": (NaturalEvent, NaturalEvent.date_ist),
        "news_articles": (NewsArticle, NewsArticle.seen_at_ist),
        "news_timeline": (NewsTimeline, NewsTimeline.date),
    }
    out = {}
    with session_scope() as s:
        for name, (model, col) in tables.items():
            n, lo, hi = s.execute(select(func.count(), func.min(col), func.max(col)).select_from(model)).one()
            out[name] = (n, str(lo)[:10], str(hi)[:10])
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=cfg.backfill_days)
    p.add_argument("--only", choices=["firms", "firms_archive", "usgs", "openmeteo", "eonet", "gdelt"])
    p.add_argument("--if-empty", action="store_true")
    p.add_argument("--skip-analysis", action="store_true")
    args = p.parse_args()

    if args.if_empty:
        with session_scope() as s:
            if s.scalar(select(func.count()).select_from(FetchLog)):
                log.info("Database already has data; skipping backfill")
                return

    jobs = {
        # Quakes are rare here: take 10 years so the catalogue has context.
        "usgs": lambda: usgs.run(days=max(args.days, 3650)),
        "eonet": lambda: eonet.run(days=max(args.days, 365)),
        "openmeteo": lambda: openmeteo.run(history_days=cfg.weather_history_days),
        "firms": lambda: firms.run(days=args.days),
        # Previous full year from FIRMS' public yearly file: seasonal baseline.
        "firms_archive": lambda: firms.run_archive(date.today().year - 1),
        "gdelt": lambda: gdelt.run(days=args.days),
    }
    for name, job in jobs.items():
        if args.only and name != args.only:
            continue
        try:
            job()
        except Exception:  # keep going: one broken source shouldn't block the rest
            log.exception("%s backfill failed", name)

    if not args.skip_analysis:
        from scripts.run_analysis import run_all
        run_all()

    print(f"\n{'table':<16}{'rows':>8}  date range")
    for name, (n, lo, hi) in counts().items():
        print(f"{name:<16}{n:>8}  {lo} .. {hi}")


if __name__ == "__main__":
    main()
