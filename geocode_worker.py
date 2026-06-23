# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python>=0.8.3",
#     "reverse_geocoder>=1.5",
#     "timezonefinder>=6",
#     "numpy",
#     "scipy",
#     "pyarrow",
# ]
# ///
"""VGI worker exposing OFFLINE reverse geocoding to DuckDB/SQL.

Assembles the scalar functions in ``vgi_geocode`` into a single ``geocode``
catalog and runs the worker over stdio (DuckDB subprocess) or HTTP. It turns a
latitude/longitude into the nearest place, country, admin regions and timezone
-- entirely offline (no API keys, no network) -- via bundled spatial databases:

- ``reverse_geocoder`` (LGPL-3.0): GeoNames cities KD-tree -> city, admin1,
  admin2, country code. Used as an UNMODIFIED pip dependency (see README).
- ``timezonefinder`` (MIT): offline IANA timezone polygons.

Usage:
    uv run geocode_worker.py             # serve over stdio (DuckDB subprocess)

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'geocode' (TYPE vgi, LOCATION 'uv run geocode_worker.py');

    SELECT geocode.nearest_city(40.7128, -74.0060);          -- 'New York City'
    SELECT geocode.country_code(48.8566, 2.3522);            -- 'FR'
    SELECT geocode.admin1(35.6762, 139.6503);                -- 'Tokyo'
    SELECT geocode.timezone(40.7128, -74.0060);              -- 'America/New_York'
    SELECT geocode.reverse_geocode(40.7128, -74.0060);       -- STRUCT(...)
    SELECT geocode.reverse_geocode(40.7128, -74.0060).city;  -- 'New York City'
    SELECT geocode.distance_km(40.7128, -74.0060, 51.5074, -0.1278);  -- ~5570
"""

from __future__ import annotations

from typing import Any

from vgi import Worker
from vgi.catalog import Catalog, Schema

from vgi_geocode import geocoder
from vgi_geocode.scalars import SCALAR_FUNCTIONS

_GEOCODE_CATALOG = Catalog(
    name="geocode",
    default_schema="main",
    schemas=[
        Schema(
            name="main",
            comment="Offline reverse geocoding: lat/lon -> place, country, admin regions, timezone",
            functions=list(SCALAR_FUNCTIONS),
        ),
    ],
)


class GeocodeWorker(Worker):
    """Worker process hosting the ``geocode`` catalog."""

    catalog = _GEOCODE_CATALOG

    def run(self, otel_config: Any = None) -> None:
        """Warm the spatial indexes, then serve.

        Building the ``reverse_geocoder`` KD-tree (~1 s) and the
        ``timezonefinder`` polygon index is lazy, so without this the first query
        of every ATTACH pays that one-time cost inline -- a window in which a
        worker-pool teardown SIGTERM (or a heavily-loaded host) can kill the run
        mid-assertion and record a spurious E2E failure. Warming at spawn moves
        the cost ahead of any query, keeping the SQL suite deterministic without
        changing a single output value. Best-effort; never fatal.
        """
        geocoder.warm_up()
        super().run(otel_config=otel_config)


def main() -> None:
    """Run the geocode worker process (stdio or, via flags, HTTP)."""
    GeocodeWorker.main()


if __name__ == "__main__":
    main()
