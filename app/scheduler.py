"""In-process APScheduler jobs: periodic fetches, each followed by re-analysis.

FIRMS / USGS / EONET hourly, GDELT every 2 h, weather daily. Jobs never run
concurrently with themselves (max_instances=1) and a failed fetch is already
recorded in fetch_log by FetchRun, so the scheduler just logs and moves on.
"""
from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from app.fetchers import eonet, firms, gdelt, openmeteo, usgs

log = logging.getLogger("samvedna.scheduler")
_analysis_lock = threading.Lock()


def _analyse() -> None:
    from scripts.run_analysis import run_all
    # Several fetch jobs can finish together; one analysis at a time is enough.
    if not _analysis_lock.acquire(blocking=False):
        return
    try:
        run_all()
    finally:
        _analysis_lock.release()


def _job(name: str, fn):
    def wrapped():
        try:
            fn()
        except Exception:
            log.exception("scheduled fetch %s failed", name)
        _analyse()
    wrapped.__name__ = f"fetch_{name}"
    return wrapped


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Kolkata", job_defaults={"max_instances": 1, "coalesce": True})
    sched.add_job(_job("firms", lambda: firms.run(days=2)), "interval", hours=1, id="firms")
    sched.add_job(_job("usgs", lambda: usgs.run(days=2)), "interval", hours=1, id="usgs")
    sched.add_job(_job("eonet", lambda: eonet.run(days=30)), "interval", hours=1, id="eonet")
    sched.add_job(_job("gdelt", lambda: gdelt.run(days=2, window_days=2)), "interval", hours=2, id="gdelt")
    sched.add_job(_job("openmeteo", lambda: openmeteo.run(history_days=30)), "interval", days=1, id="openmeteo")
    sched.start()
    log.info("scheduler started: %s", [j.id for j in sched.get_jobs()])
    return sched
