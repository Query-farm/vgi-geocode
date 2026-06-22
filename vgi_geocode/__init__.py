"""Offline reverse geocoding (lat/lon -> place) as a VGI worker.

The implementation is split so each concern stays focused:

- ``geocoder`` -- pure lookup logic over ``reverse_geocoder`` (a bundled
  GeoNames cities KD-tree) and ``timezonefinder`` (offline IANA timezone
  polygons), plus a pure haversine ``distance_km``. No Arrow or VGI dependency,
  directly unit-testable. Builds its spatial indexes once and caches them for
  the process lifetime.
- ``scalars`` -- per-row VGI scalar functions (positional-only; the full-record
  ``reverse_geocode`` returns a STRUCT via an explicit ``Returns(arrow_type=...)``).

``geocode_worker.py`` at the repo root assembles these into the ``geocode``
catalog and runs the worker over stdio (or HTTP), warming the indexes at spawn.
"""

from __future__ import annotations

__version__ = "0.1.0"
