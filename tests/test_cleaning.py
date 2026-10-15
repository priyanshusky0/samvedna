from collections import Counter

import pandas as pd

from app.cleaning.gazetteer import gazetteer
from app.cleaning.geo import ascii_name, tag_state
from app.cleaning.normalize import (
    clean_eonet, clean_firms, clean_gdelt_articles, clean_usgs, clean_weather, dedup_hotspots,
    geotag_title, normalise_title, to_ist,
)


def test_ascii_name_strips_diacritics():
    assert ascii_name("Mahārāshtra") == "Maharashtra"
    assert ascii_name("Tamil Nādu") == "Tamil Nadu"


def test_state_tagging():
    assert tag_state(19.07, 72.88) == "Maharashtra"      # Mumbai
    assert tag_state(12.97, 77.59) == "Karnataka"        # Bengaluru
    assert tag_state(28.61, 77.21) is None               # Delhi: not a selected state
    assert tag_state(15.0, 60.0) is None                 # open Arabian Sea


def test_to_ist_converts_utc():
    out = to_ist(pd.Series(["2026-10-01T00:00:00Z"]))
    assert str(out.iloc[0]) == "2026-10-01 05:30:00"


def firms_row(**kw):
    base = {"latitude": 19.9, "longitude": 75.3, "bright_ti4": 330.0, "acq_date": "2026-10-10",
            "acq_time": "745", "satellite": "N", "confidence": "n", "frp": 5.0, "daynight": "D"}
    return {**base, **kw}


def test_clean_firms_rules():
    raw = pd.DataFrame([
        firms_row(),
        firms_row(confidence="l"),                               # low confidence -> dropped
        firms_row(latitude=40.0),                                # outside bbox
        firms_row(latitude=21.25, longitude=81.63),             # Raipur: in bbox, not a selected state
        firms_row(satellite="N20", acq_time="0800", confidence="h", frp=9.0,
                  latitude=19.9005, longitude=75.3005),          # same fire, other satellite, 15 min later
        firms_row(acq_time="1900", latitude=21.0, longitude=79.0),
    ])
    clean, rej = clean_firms(raw)
    assert rej["low_confidence"] == 1
    assert rej["outside_bbox"] == 1
    assert rej["outside_states"] == 1
    assert rej["cross_satellite_duplicate"] == 1
    assert len(clean) == 2
    # the higher-confidence NOAA-20 detection wins the duplicate pair
    assert "N20" in set(clean["satellite"])
    # 08:00 UTC -> 13:30 IST
    assert clean.loc[clean["satellite"] == "N20", "acq_datetime_ist"].iloc[0].strftime("%H:%M") == "13:30"


def test_firms_ids_are_deterministic():
    raw = pd.DataFrame([firms_row()])
    a, _ = clean_firms(raw)
    b, _ = clean_firms(raw)
    assert a["id"].tolist() == b["id"].tolist()


def test_dedup_keeps_far_points():
    df = pd.DataFrame({
        "lat": [19.0, 19.1], "lon": [75.0, 75.0], "confidence": ["nominal", "high"], "frp": [1.0, 2.0],
        "satellite": ["N", "N20"], "acq_datetime_ist": pd.to_datetime(["2026-10-10 13:00", "2026-10-10 13:10"]),
    })
    out, n = dedup_hotspots(df, 375, 30)
    assert n == 0 and len(out) == 2


def test_clean_usgs():
    feats = [
        {"id": "us1", "properties": {"time": 1760000000000, "mag": 3.1, "place": "Koyna", "type": "earthquake"},
         "geometry": {"coordinates": [73.75, 17.4, 10.0]}},
        {"id": "us2", "properties": {"time": 1760000000000, "mag": -1, "place": "x"},
         "geometry": {"coordinates": [73.75, 17.4, 10.0]}},
    ]
    df, rej = clean_usgs(feats)
    assert list(df["usgs_id"]) == ["us1"] and df["state"].iloc[0] == "Maharashtra"
    assert rej["bad_magnitude"] == 1


def test_clean_weather_keeps_missing_as_missing():
    daily = {"time": ["2026-10-01", "2026-10-02", "2026-10-03"], "precipitation_sum": [1.0, None, -3.0],
             "temperature_2m_max": [30, 31, 32], "temperature_2m_min": [20, 21, 22], "wind_speed_10m_max": [5, 6, 7]}
    df, rej = clean_weather("Goa", daily, is_forecast=False)
    assert rej == Counter({"negative_precip": 1})
    assert len(df) == 2 and pd.isna(df["precip_mm"].iloc[1])


def test_clean_eonet_points_and_polygons():
    events = [{"id": "E1", "title": "Wildfire", "categories": [{"title": "Wildfires"}], "sources": [{"url": "u"}],
               "geometry": [{"type": "Point", "date": "2026-10-01T00:00:00Z", "coordinates": [76.9, 8.5]},
                            {"type": "Polygon", "date": "2026-10-02T00:00:00Z",
                             "coordinates": [[[77.5, 12.9], [77.7, 12.9], [77.7, 13.1], [77.5, 13.1]]]}]}]
    df, _ = clean_eonet(events)
    assert set(df["state"]) == {"Kerala", "Karnataka"}
    assert df["eonet_id"].is_unique


def test_gdelt_dedup_and_geotag():
    arts = [
        {"url": "a", "title": "Heavy rain floods Chennai streets", "seendate": "20261001T031500Z", "domain": "x"},
        {"url": "a", "title": "Heavy rain floods Chennai streets", "seendate": "20261001T031500Z", "domain": "x"},
        {"url": "b", "title": "Heavy rain floods Chennai streets!", "seendate": "20261001T041500Z", "domain": "y"},
        {"url": "c", "title": "Stock market rallies", "seendate": "20261001T041500Z", "domain": "y"},
    ]
    df, rej = clean_gdelt_articles(arts, "flood", gazetteer(), ["flood", "rain"])
    assert len(df) == 1 and df["state"].iloc[0] == "Tamil Nadu"
    assert rej["duplicate_url"] == 1 and rej["near_duplicate_title"] == 1 and rej["off_topic_headline"] == 1
    assert str(df["seen_at_ist"].iloc[0]) == "2026-10-01 08:45:00"


def test_geotag_first_mention_wins_and_whole_words():
    g = gazetteer()
    assert geotag_title("Kerala and Karnataka brace for rain", g) == "Kerala"
    assert geotag_title("Goalkeeper injured", g) is None   # 'goa' must be a whole word
    assert normalise_title("  Floods: KERALA!! ") == "floods kerala"


def test_gdelt_windows_are_day_aligned_and_cover_range():
    from datetime import datetime, timedelta, timezone

    from app.fetchers.gdelt import windows
    now = datetime(2026, 10, 15, 6, 30, tzinfo=timezone.utc)
    ws = windows(now, 30, 14)
    assert ws[0] == (datetime(2026, 10, 15, tzinfo=timezone.utc), now)
    assert all(e.hour == 0 for _, e in ws[1:])
    assert ws[-1][0] == now - timedelta(days=30)
    assert all(a[0] == b[1] for a, b in zip(ws, ws[1:]))  # contiguous, no gaps


def test_weather_falls_back_to_nasa_power(monkeypatch):
    import httpx

    from app.fetchers import openmeteo

    req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    resp = httpx.Response(429, request=req, text='{"reason":"Daily API request limit exceeded"}')

    def boom(*a, **k):
        raise httpx.HTTPStatusError("429", request=req, response=resp)

    power_calls = []

    def fake_power(state, lat, lon, start):
        power_calls.append(state)
        df, _ = clean_weather(state, {"time": ["2026-10-10"], "precipitation_sum": [1.0], "temperature_2m_max": [30],
                                      "temperature_2m_min": [20], "wind_speed_10m_max": [10]}, is_forecast=False)
        df["source"] = "nasa-power"
        return df, 1, {}

    monkeypatch.setattr(openmeteo, "_open_meteo", boom)
    monkeypatch.setattr(openmeteo, "_nasa_power", fake_power)
    openmeteo.run(history_days=30)
    assert len(power_calls) == 10  # every state still gets data


def test_firms_id_uses_own_row_after_filtering():
    """Regression: ids were zipped against an unfiltered time column, so a
    dropped row shifted every later id onto the wrong acquisition time."""
    from app.cleaning.normalize import sha1
    raw = pd.DataFrame([firms_row(confidence="l", acq_time="0100"), firms_row(acq_time="0745")])
    clean, _ = clean_firms(raw)
    assert clean["id"].iloc[0] == sha1("N", 19.9, 75.3, "2026-10-10", "0745")
    # Same row alone gives the same id: ids don't depend on what else was filtered.
    alone, _ = clean_firms(pd.DataFrame([firms_row(acq_time="0745")]))
    assert alone["id"].iloc[0] == clean["id"].iloc[0]


def test_quota_exhausted_only_for_long_window_limits():
    import httpx

    from app.fetchers.base import quota_exhausted

    def err(text):
        req = httpx.Request("GET", "https://x")
        return httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req, text=text))

    assert quota_exhausted(err('{"error":true,"reason":"Daily API request limit exceeded. Please try again tomorrow."}'))
    gdelt = "Please limit requests to one every 5 seconds ... For trend analysis, please see our daily newsletter"
    assert not quota_exhausted(err(gdelt))
