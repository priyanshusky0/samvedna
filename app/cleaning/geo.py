"""Point -> state tagging against geoBoundaries India ADM1 polygons.

Points that fall inside a selected state get that state's name. Points just
offshore (coastal quakes, cyclone centres) are assigned to the nearest selected
state if within `COASTAL_BUFFER_DEG`; everything else is dropped (None).
"""
from __future__ import annotations

import json
import unicodedata
from functools import lru_cache

import numpy as np
import pandas as pd
from shapely import STRtree, points
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from app.config import get_settings

COASTAL_BUFFER_DEG = 0.25  # ~25 km at these latitudes


def ascii_name(name: str) -> str:
    """'Mahārāshtra' -> 'Maharashtra' (geoBoundaries uses diacritics)."""
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))


@lru_cache
def load_states() -> tuple[list[str], list[BaseGeometry]]:
    settings = get_settings()
    wanted = {s.lower() for s in settings.states}
    data = json.loads(settings.geo_path.read_text())
    names, geoms = [], []
    for feat in data["features"]:
        name = ascii_name(feat["properties"]["shapeName"])
        if name.lower() in wanted:
            names.append(name)
            geoms.append(shape(feat["geometry"]).buffer(0))  # buffer(0) fixes invalid rings
    missing = wanted - {n.lower() for n in names}
    if missing:
        raise ValueError(f"States not found in boundary file: {sorted(missing)}")
    return names, geoms


@lru_cache
def _tree() -> STRtree:
    return STRtree(load_states()[1])


def tag_states(lat: pd.Series | np.ndarray, lon: pd.Series | np.ndarray) -> np.ndarray:
    """Vectorised point-in-polygon. Returns an object array of state names / None."""
    names, _ = load_states()
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    out = np.full(len(lat), None, dtype=object)
    if len(lat) == 0:
        return out
    pts = points(lon, lat)
    pt_idx, poly_idx = _tree().query(pts, predicate="intersects")
    out[pt_idx] = np.array(names, dtype=object)[poly_idx]

    # Coastal fallback for points that missed every polygon.
    miss = np.flatnonzero(pd.isna(out))
    if len(miss):
        near_pt, near_poly = _tree().query_nearest(
            pts[miss], max_distance=COASTAL_BUFFER_DEG, all_matches=False
        )
        out[miss[near_pt]] = np.array(names, dtype=object)[near_poly]
    return out


def tag_state(lat: float, lon: float) -> str | None:
    return tag_states([lat], [lon])[0]


@lru_cache
def states_geojson(tolerance: float = 0.02) -> dict:
    """Simplified boundaries of the selected states, for the dashboard map."""
    names, geoms = load_states()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": n},
                "geometry": mapping(g.simplify(tolerance, preserve_topology=True)),
            }
            for n, g in zip(names, geoms)
        ],
    }
