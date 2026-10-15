"""Clock helpers. All stored times are naive Asia/Kolkata, so "now" must be IST
regardless of the server's own timezone (Render and Docker run in UTC)."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Current IST wall-clock time as a naive datetime (matches stored columns)."""
    return datetime.now(IST).replace(tzinfo=None)


def today_ist() -> date:
    return now_ist().date()
