# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.5",
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

import json
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
    "# Offline Reverse Geocoding in SQL\n\n"
    "**Turn latitude/longitude coordinates into the nearest city, country, admin "
    "regions and IANA timezone -- entirely offline, with no API keys, no rate limits "
    "and no network calls -- directly in DuckDB.**\n\n"
    "`geocode` is a VGI worker that brings fast, hermetic **reverse geocoding** to "
    "SQL. Point it at any column of `(lat, lon)` coordinates and it enriches each row "
    "with the nearest known place name, its ISO-3166 country code, first- and "
    "second-level administrative regions (state/region, county/district), and the "
    "local IANA timezone. Because every lookup runs against bundled spatial databases "
    "rather than a hosted geocoding API, it works on air-gapped hosts, scales to "
    "millions of rows without per-request billing, and returns deterministic results. "
    "It is built for data engineers, analysts and GIS practitioners who need to attach "
    "human-readable geography and time zones to coordinate data sets -- IoT and GPS "
    "traces, telemetry, event logs, store/asset locations -- without leaving the "
    "database.\n\n"
    "Under the hood, place lookups are powered by "
    "[`reverse_geocoder`](https://github.com/thampiman/reverse-geocoder) "
    "([PyPI](https://pypi.org/project/reverse_geocoder/)), which queries a "
    "[GeoNames](https://www.geonames.org/) cities KD-tree to find the nearest "
    "populated place in sub-millisecond time. Timezone resolution uses "
    "[`timezonefinder`](https://github.com/jannikmi/timezonefinder) "
    "([documentation](https://timezonefinder.readthedocs.io/)), which maps a point "
    "to its [IANA time-zone](https://www.iana.org/time-zones) polygon offline. "
    "Distances are computed with the great-circle "
    "[haversine formula](https://en.wikipedia.org/wiki/Haversine_formula). Every "
    "function is total and NULL-safe: a NULL or out-of-range coordinate yields `NULL` "
    "rather than an error, so the functions are safe to drop into large batch "
    "queries.\n\n"
    "The catalog exposes seven per-row scalar functions in the `main` schema. Use "
    "`nearest_city(lat, lon)` for a place label, `country_code(lat, lon)` for the "
    "ISO-3166 country, and `admin1(lat, lon)` / `admin2(lat, lon)` for state/region "
    "and county/district. `reverse_geocode(lat, lon)` returns the full result as a "
    "single `STRUCT` (city, admin1, admin2, country code, and the matched place "
    "coordinates) so you can extract fields with `.city`, `.country_code`, and so on. "
    "`timezone(lat, lon)` returns the IANA timezone name (e.g. `America/New_York`), "
    "and `distance_km(lat1, lon1, lat2, lon2)` returns the great-circle distance in "
    "kilometers between two points. For example, "
    "`SELECT geocode.reverse_geocode(40.7128, -74.0060).city` resolves to "
    "`New York City`, and `SELECT geocode.timezone(48.8566, 2.3522)` resolves to "
    "`Europe/Paris`.\n\n"
    "Backed by GeoNames cities (`reverse_geocoder`, LGPL-3.0, used as an unmodified "
    "dependency) and offline IANA timezone polygons (`timezonefinder`, MIT)."
)

_SCHEMA_DESCRIPTION_LLM = (
    "Per-row scalar geocoding functions: nearest_city, country_code, admin1, admin2, "
    "reverse_geocode (full STRUCT of city/admin/country/place coordinates), timezone, "
    "and distance_km (haversine). All take coordinates positionally and return NULL "
    "for NULL or out-of-range inputs."
)

_SCHEMA_DESCRIPTION_MD = (
    "Per-row offline reverse-geocoding scalars over Apache Arrow: `nearest_city`, "
    "`country_code`, `admin1`, `admin2`, `reverse_geocode` (full STRUCT), `timezone`, "
    "and `distance_km` (haversine). All take coordinates positionally and return `NULL` "
    "for `NULL` or out-of-range inputs, never an error. Backed by GeoNames cities and "
    "offline IANA timezone polygons -- no API keys or network required."
)

_SCHEMA_EXAMPLE_QUERIES = (
    "SELECT geocode.main.nearest_city(40.7128, -74.0060);\n"
    "SELECT geocode.main.country_code(48.8566, 2.3522);\n"
    "SELECT geocode.main.admin1(35.6762, 139.6503);\n"
    "SELECT geocode.main.admin2(34.0522, -118.2437);\n"
    "SELECT geocode.main.reverse_geocode(40.7128, -74.0060);\n"
    "SELECT geocode.main.timezone(40.7128, -74.0060);\n"
    "SELECT geocode.main.distance_km(40.7128, -74.0060, 51.5074, -0.1278);"
)


_GEOCODE_CATALOG = Catalog(
    name="geocode",
    default_schema="main",
    comment="Offline reverse geocoding: lat/lon -> place, country, admin regions, timezone, plus haversine distance",
    source_url=_REPO_URL,
    tags={
        "vgi.title": "Offline Reverse Geocoding",
        "vgi.keywords": json.dumps(
            [
                "geocode",
                "reverse geocode",
                "geocoding",
                "lat lon",
                "latitude longitude",
                "coordinates",
                "city",
                "country",
                "country code",
                "state",
                "region",
                "county",
                "admin",
                "timezone",
                "iana timezone",
                "haversine",
                "distance",
                "offline",
                "geonames",
            ]
        ),
        "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
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
                "vgi.title": "Geocode - main",
                "vgi.keywords": json.dumps(
                    [
                        "geocode",
                        "reverse geocode",
                        "nearest_city",
                        "country_code",
                        "admin1",
                        "admin2",
                        "reverse_geocode",
                        "timezone",
                        "distance_km",
                        "lat lon",
                        "coordinates",
                        "offline",
                        "geonames",
                    ]
                ),
                # VGI123 classifying tags (BARE keys: domain/category/topic).
                "domain": "geospatial",
                "category": "geocoding",
                "topic": "reverse-geocoding",
                "vgi.doc_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.doc_md": _SCHEMA_DESCRIPTION_MD,
                # VGI506 representative example queries for the schema.
                "vgi.example_queries": _SCHEMA_EXAMPLE_QUERIES,
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
