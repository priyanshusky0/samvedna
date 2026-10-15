"""The analysis pipeline must not crash on an empty database (first boot)."""
from app.analysis import anomalies, correlation, trends


def test_analysis_on_empty_tables_is_quiet(monkeypatch):
    import app.analysis.series as series
    empty = series._empty(["Goa"])
    monkeypatch.setattr(series, "_read", lambda stmt: __import__("pandas").DataFrame(
        columns=["state", "acq_datetime_ist", "time_ist", "date", "precip_mm", "tmax", "volume", "seen_at_ist"]))
    assert empty.empty
    assert anomalies.seasonal_fire_alerts(__import__("datetime").date.today()) == []
    assert trends.run_trends() == []
    assert trends.run_seasonal() == []
    assert isinstance(correlation.run(), list)
