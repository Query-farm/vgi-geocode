"""Pure offline reverse-geocoding logic -- no Arrow, no VGI.

Everything here is total and never raises on bad input: an out-of-range or NULL
coordinate yields ``None`` (the scalar layer maps that to SQL ``NULL``). The two
spatial indexes -- the ``reverse_geocoder`` KD-tree over GeoNames cities and the
``timezonefinder`` polygon index -- are **expensive to build once** (the KD-tree
load is ~1 s) but cheap to query, so both are constructed lazily and cached for
the lifetime of the process. ``warm_up()`` forces that construction ahead of any
query so first-query latency under load can't flake the E2E suite.

Reverse geocoding is "nearest land place": an ocean coordinate still resolves to
the closest city in the GeoNames database (the KD-tree has no concept of water),
which is documented behaviour.
"""

from __future__ import annotations

import math
import threading
from typing import Any, NamedTuple

# Imported lazily inside the index builders so merely importing this module (for
# the pure haversine, say) does not pay the heavyweight library import cost.

_RG_LOCK = threading.Lock()
_TF_LOCK = threading.Lock()

_rg: Any = None  # reverse_geocoder module (memoized; loads its KD-tree on import)
_tf: Any = None  # timezonefinder.TimezoneFinder instance


class Place(NamedTuple):
    """A resolved nearest-place record (all fields may be empty strings)."""

    city: str
    admin1: str
    admin2: str
    country_code: str
    place_lat: float
    place_lon: float


def _valid_coord(lat: float | None, lon: float | None) -> bool:
    """True iff lat/lon are present, finite, and in range (|lat|<=90, |lon|<=180)."""
    if lat is None or lon is None:
        return False
    try:
        flat = float(lat)
        flon = float(lon)
    except (TypeError, ValueError):
        return False
    if math.isnan(flat) or math.isnan(flon) or math.isinf(flat) or math.isinf(flon):
        return False
    return -90.0 <= flat <= 90.0 and -180.0 <= flon <= 180.0


def _get_rg() -> Any:
    """Return the ``reverse_geocoder`` module, importing (and loading) it once."""
    global _rg
    if _rg is None:
        with _RG_LOCK:
            if _rg is None:
                import reverse_geocoder as rg

                # ``search`` lazily builds the singleton KD-tree on first call;
                # do one throwaway lookup so the cost is paid here, not inline.
                rg.search([(0.0, 0.0)], mode=1)
                _rg = rg
    return _rg


def _get_tf() -> Any:
    """Return a cached ``TimezoneFinder`` instance, building it once."""
    global _tf
    if _tf is None:
        with _TF_LOCK:
            if _tf is None:
                from timezonefinder import TimezoneFinder

                _tf = TimezoneFinder(in_memory=True)
    return _tf


def warm_up() -> None:
    """Build both spatial indexes ahead of time. Best-effort; never fatal."""
    try:
        _get_rg()
    except Exception:
        pass
    try:
        _get_tf()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Reverse geocoding (nearest GeoNames city via the KD-tree).
# ---------------------------------------------------------------------------


def reverse_geocode(lat: float | None, lon: float | None) -> Place | None:
    """Nearest known place to ``(lat, lon)``, or ``None`` if out of range/NULL."""
    out = reverse_geocode_batch([(lat, lon)])
    return out[0]


def reverse_geocode_batch(
    coords: list[tuple[float | None, float | None]],
) -> list[Place | None]:
    """Vectorized reverse geocode: one KD-tree query for every valid coordinate.

    Invalid/NULL coordinates map to ``None`` without touching the index.
    """
    valid: list[tuple[float, float]] = []
    indices: list[int] = []
    for i, (lat, lon) in enumerate(coords):
        if _valid_coord(lat, lon):
            valid.append((float(lat), float(lon)))  # type: ignore[arg-type]
            indices.append(i)

    out: list[Place | None] = [None] * len(coords)
    if not valid:
        return out

    rg = _get_rg()
    results = rg.search(valid, mode=1)
    for idx, res in zip(indices, results, strict=True):
        try:
            place_lat = float(res["lat"])
            place_lon = float(res["lon"])
        except (TypeError, ValueError, KeyError):
            place_lat = float("nan")
            place_lon = float("nan")
        out[idx] = Place(
            city=res.get("name", "") or "",
            admin1=res.get("admin1", "") or "",
            admin2=res.get("admin2", "") or "",
            country_code=res.get("cc", "") or "",
            place_lat=place_lat,
            place_lon=place_lon,
        )
    return out


def nearest_city(lat: float | None, lon: float | None) -> str | None:
    """Name of the nearest city, or ``None``."""
    place = reverse_geocode(lat, lon)
    return place.city if place is not None and place.city else None


def country_code(lat: float | None, lon: float | None) -> str | None:
    """ISO-3166 alpha-2 country code of the nearest place, or ``None``."""
    place = reverse_geocode(lat, lon)
    return place.country_code if place is not None and place.country_code else None


def admin1(lat: float | None, lon: float | None) -> str | None:
    """First-level admin region (state/region) of the nearest place, or ``None``."""
    place = reverse_geocode(lat, lon)
    return place.admin1 if place is not None and place.admin1 else None


def admin2(lat: float | None, lon: float | None) -> str | None:
    """Second-level admin region (county/district) of the nearest place, or ``None``."""
    place = reverse_geocode(lat, lon)
    return place.admin2 if place is not None and place.admin2 else None


# ---------------------------------------------------------------------------
# Timezone (offline IANA tz polygons).
# ---------------------------------------------------------------------------


def timezone(lat: float | None, lon: float | None) -> str | None:
    """IANA timezone name for ``(lat, lon)``, or ``None`` if out of range/unknown."""
    if not _valid_coord(lat, lon):
        return None
    tf = _get_tf()
    try:
        tz = tf.timezone_at(lat=float(lat), lng=float(lon))  # type: ignore[arg-type]
    except Exception:
        return None
    return tz or None


# ---------------------------------------------------------------------------
# Haversine distance (pure; independent of the spatial indexes).
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0088


def distance_km(
    lat1: float | None,
    lon1: float | None,
    lat2: float | None,
    lon2: float | None,
) -> float | None:
    """Great-circle distance between two points in kilometres (haversine).

    Returns ``None`` if any coordinate is NULL or out of range.
    """
    if not (_valid_coord(lat1, lon1) and _valid_coord(lat2, lon2)):
        return None
    rlat1, rlon1, rlat2, rlon2 = (
        math.radians(float(lat1)),  # type: ignore[arg-type]
        math.radians(float(lon1)),  # type: ignore[arg-type]
        math.radians(float(lat2)),  # type: ignore[arg-type]
        math.radians(float(lon2)),  # type: ignore[arg-type]
    )
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2.0) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
