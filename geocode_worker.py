# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.4",
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

_REPO_URL = "https://github.com/Query-farm/vgi-geocode"

_CATALOG_DESCRIPTION_LLM = (
    "Offline reverse geocoding for SQL: turn a latitude/longitude into the nearest "
    "known place (city), its ISO-3166 country code, first- and second-level admin "
    "regions (state/region, county/district), and IANA timezone -- entirely offline "
    "with no API keys or network. Also computes great-circle (haversine) distance in "
    "kilometers between two points. Backed by the GeoNames cities KD-tree "
    "(reverse_geocoder) and offline IANA timezone polygons (timezonefinder). Use to "
    "enrich coordinate columns with place names, countries, regions and timezones, or "
    "to measure distances, directly in DuckDB."
)

_CATALOG_DESCRIPTION_MD = (
    "# geocode\n\n"
    "Offline reverse geocoding and great-circle distance over Apache Arrow -- no API "
    "keys, no network.\n\n"
    "Turns `(lat, lon)` into the nearest place, country, admin regions and timezone, "
    "and measures haversine distance between two points.\n\n"
    "Scalars: `nearest_city`, `country_code`, `admin1`, `admin2`, `reverse_geocode` "
    "(full STRUCT), `timezone`, `distance_km`.\n\n"
    "Backed by GeoNames cities (`reverse_geocoder`, LGPL-3.0) and offline IANA "
    "timezone polygons (`timezonefinder`, MIT)."
)

_SCHEMA_DESCRIPTION_LLM = (
    "Per-row scalar geocoding functions: nearest_city, country_code, admin1, admin2, "
    "reverse_geocode (full STRUCT of city/admin/country/place coordinates), timezone, "
    "and distance_km (haversine). All take coordinates positionally and return NULL "
    "for NULL or out-of-range inputs."
)

_SCHEMA_DESCRIPTION_MD = (
    "Per-row offline reverse-geocoding scalars over Apache Arrow: `nearest_city`, "
    "`country_code`, `admin1`, `admin2`, `reverse_geocode`, `timezone`, `distance_km`."
)

_GEOCODE_CATALOG = Catalog(
    name="geocode",
    default_schema="main",
    comment="Offline reverse geocoding: lat/lon -> place, country, admin regions, timezone, plus haversine distance",
    source_url=_REPO_URL,
    tags={
        "vgi.description_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.description_md": _CATALOG_DESCRIPTION_MD,
        "vgi.author": "Query.Farm",
        "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
        "vgi.license": "MIT",
        "vgi.support_contact": f"{_REPO_URL}/issues",
        "vgi.support_policy_url": f"{_REPO_URL}/blob/main/README.md",
    },
    schemas=[
        Schema(
            name="main",
            comment="Offline reverse geocoding: lat/lon -> place, country, admin regions, timezone",
            tags={
                "vgi.description_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.description_md": _SCHEMA_DESCRIPTION_MD,
            },
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
