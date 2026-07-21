"""VGI worker exposing OFFLINE reverse geocoding to DuckDB/SQL.

Assembles the scalar functions in ``vgi_geocode`` into a single ``geocode``
catalog and runs the worker over stdio (DuckDB subprocess) or HTTP. It turns a
latitude/longitude into the nearest place, country, admin regions and timezone
-- entirely offline (no API keys, no network) -- via bundled spatial databases:

- ``reverse_geocoder`` (LGPL-3.0): GeoNames cities KD-tree -> city, admin1,
  admin2, country code. Used as an UNMODIFIED pip dependency (see README).
- ``timezonefinder`` (MIT): offline IANA timezone polygons.

This module is the wheel-importable home of the catalog + worker. The repo-root
``geocode_worker.py`` is a thin PEP 723 shim that re-exports :class:`GeocodeWorker`
and :func:`main` from here so ``uv run geocode_worker.py`` keeps working, while
the installed package exposes the same worker via the ``vgi-geocode-worker``
console script and ``vgi-serve vgi_geocode.worker:GeocodeWorker``.

Usage:
    uv run geocode_worker.py             # serve over stdio (DuckDB subprocess)
    vgi-geocode-worker                   # same, via the installed console script

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
    "## What you can do\n\n"
    "The worker groups its capabilities into three areas:\n\n"
    "- **Place lookup** -- resolve a coordinate to the nearest city, its ISO-3166 "
    "country, and first- and second-level administrative regions (state/region and "
    "county/district), either one attribute at a time or as a single combined `STRUCT` "
    "record for the whole place.\n"
    "- **Timezone** -- map a coordinate to its IANA timezone name (e.g. "
    "`America/New_York`) for localizing timestamps.\n"
    "- **Distance** -- measure the great-circle (haversine) distance in kilometers "
    "between two coordinates.\n\n"
    "Every operation is a per-row scalar you can use inline in any projection or "
    "predicate, so enriching a whole coordinate column -- or filtering it by proximity "
    "-- is a single `SELECT` with no joins, staging tables, or API round-trips.\n\n"
    "Backed by GeoNames cities (`reverse_geocoder`, LGPL-3.0, used as an unmodified "
    "dependency) and offline IANA timezone polygons (`timezonefinder`, MIT)."
)

_SCHEMA_DESCRIPTION_LLM = (
    "Per-row scalar geocoding functions: nearest_city, country_code, admin1, admin2, "
    "reverse_geocode (full `STRUCT` of city/admin/country/place coordinates), timezone, "
    "and distance_km (haversine). All take coordinates positionally and return NULL "
    "for NULL or out-of-range inputs."
)

_SCHEMA_DESCRIPTION_MD = (
    "## Reverse-geocoding scalars\n\n"
    "Per-row scalar functions over Apache Arrow that turn a `(lat, lon)` coordinate "
    "into human-readable geography: the nearest city, its ISO-3166 country code, the "
    "first- and second-level administrative regions, and a single combined "
    "nearest-place record. All take coordinates positionally.\n\n"
    "## Timezone and distance\n\n"
    "The schema also resolves a coordinate to its IANA timezone name (for localizing "
    "timestamps) and computes the great-circle (haversine) distance in kilometers "
    "between two points.\n\n"
    "## Semantics\n\n"
    "Every function returns `NULL` for a `NULL` or out-of-range input, never an error, "
    "so it is safe to drop into large batch queries. Lookups are backed by GeoNames "
    "cities and offline IANA timezone polygons -- no API keys or network required."
)

# VGI506/VGI515: representative, self-contained (runnable), described example
# queries for the schema. Beyond single-call lookups these show the real analyst
# patterns -- enriching a coordinate column, grouping by country, proximity
# filtering with distance_km -- so an agent learns how the functions compose.
_SCHEMA_EXAMPLE_QUERIES = json.dumps(
    [
        {
            "description": "Enrich a set of coordinates with nearest city and ISO-3166 country code.",
            "sql": "SELECT lat, lon, geocode.main.nearest_city(lat, lon) AS city, "
            "geocode.main.country_code(lat, lon) AS country "
            "FROM (VALUES (40.7128, -74.0060), (48.8566, 2.3522), (35.6762, 139.6503)) AS t(lat, lon)",
        },
        {
            "description": "Count coordinates per country by reverse-geocoding to a country code.",
            "sql": "SELECT geocode.main.country_code(lat, lon) AS country, count(*) AS n "
            "FROM (VALUES (40.7, -74.0), (34.0, -118.2), (48.8, 2.35)) AS t(lat, lon) "
            "GROUP BY country ORDER BY n DESC",
        },
        {
            "description": "Keep only points within 1000 km of Paris using the haversine distance.",
            "sql": "SELECT name, round(geocode.main.distance_km(lat, lon, 48.8566, 2.3522)) AS km_to_paris "
            "FROM (VALUES ('London', 51.5074, -0.1278), ('New York', 40.7128, -74.0060)) AS t(name, lat, lon) "
            "WHERE geocode.main.distance_km(lat, lon, 48.8566, 2.3522) < 1000",
        },
        {
            "description": "Pull the first-level admin region straight out of the reverse_geocode STRUCT.",
            "sql": "SELECT geocode.main.reverse_geocode(40.7128, -74.0060).admin1 AS state",
        },
        {
            "description": "Resolve the IANA timezone name for a coordinate to localize timestamps.",
            "sql": "SELECT geocode.main.timezone(40.7128, -74.0060) AS tz",
        },
    ]
)


# VGI413 category registry for the `main` schema. Each entry is
# {"name","title","description"}; every function declares one via `vgi.category`.
_SCHEMA_CATEGORIES = json.dumps(
    [
        {
            "name": "reverse_geocoding",
            "title": "Reverse geocoding",
            "description": "Resolve a coordinate to the nearest city, its country, and administrative regions.",
        },
        {
            "name": "timezone",
            "title": "Timezone",
            "description": "Map a coordinate to its IANA timezone name for localizing timestamps.",
        },
        {
            "name": "distance",
            "title": "Distance",
            "description": "Great-circle (haversine) distance in kilometers between two coordinates.",
        },
    ]
)


# VGI152/VGI920 agent-suitability suite (catalog-level). Each task's `prompt` is the
# ONLY field shown to the analyst; `reference_sql`/`success_criteria` are grader-only.
# References are anchored on STABLE facts (country codes, IANA timezones, deterministic
# distances) rather than drift-prone GeoNames city labels, and `ignore_column_names`
# relaxes result-column aliasing. Together the seven tasks exercise every function.
_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "country_code_of_point",
            "prompt": "What ISO-3166 alpha-2 country code corresponds to latitude 48.8566, longitude 2.3522?",
            "reference_sql": "SELECT geocode.main.country_code(48.8566, 2.3522) AS cc",
            "success_criteria": "Returns the country code 'FR' (France) for the Paris coordinates.",
            "ignore_column_names": True,
        },
        {
            "name": "nearest_city_of_point",
            "prompt": "What is the name of the nearest known city to latitude 48.8566, longitude 2.3522?",
            "reference_sql": "SELECT geocode.main.nearest_city(48.8566, 2.3522) AS city",
            "success_criteria": "Returns a city name at/near Paris, France (e.g. 'Paris').",
            "ignore_column_names": True,
        },
        {
            "name": "admin1_of_point",
            "prompt": "Which first-level administrative region (state/prefecture/region) is nearest to "
            "latitude 35.6762, longitude 139.6503?",
            "reference_sql": "SELECT geocode.main.admin1(35.6762, 139.6503) AS region",
            "success_criteria": "Returns the first-level admin region for the Tokyo coordinates (e.g. 'Tokyo').",
            "ignore_column_names": True,
        },
        {
            "name": "admin2_of_point",
            "prompt": "Which second-level administrative region (county/district) is nearest to "
            "latitude 34.0522, longitude -118.2437?",
            "reference_sql": "SELECT geocode.main.admin2(34.0522, -118.2437) AS county",
            "success_criteria": "Returns the second-level admin region for the Los Angeles coordinates "
            "(e.g. 'Los Angeles County').",
            "ignore_column_names": True,
        },
        {
            "name": "reverse_geocode_country_field",
            "prompt": "Using the combined nearest-place lookup for latitude 40.7128, longitude -74.0060, "
            "return the ISO-3166 country code of that place.",
            "reference_sql": "SELECT geocode.main.reverse_geocode(40.7128, -74.0060).country_code AS cc",
            "success_criteria": "Extracts the country_code field ('US') from the reverse-geocode STRUCT.",
            "ignore_column_names": True,
        },
        {
            "name": "timezone_of_point",
            "prompt": "What IANA timezone name applies at latitude 40.7128, longitude -74.0060?",
            "reference_sql": "SELECT geocode.main.timezone(40.7128, -74.0060) AS tz",
            "success_criteria": "Returns the IANA timezone 'America/New_York'.",
            "ignore_column_names": True,
        },
        {
            "name": "distance_between_points",
            "prompt": "Compute the great-circle distance in kilometers, rounded to the nearest kilometer, "
            "between (latitude 40.7128, longitude -74.0060) and (latitude 51.5074, longitude -0.1278).",
            "reference_sql": "SELECT round(geocode.main.distance_km(40.7128, -74.0060, 51.5074, -0.1278)) AS km",
            "success_criteria": "Returns the rounded haversine distance (~5570 km) between New York and London.",
            "ignore_column_names": True,
        },
    ]
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
        # VGI152/VGI920 agent-suitability suite (see _AGENT_TEST_TASKS).
        "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
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
                # VGI413 navigation/SEO category registry; each function names one
                # of these via its own `vgi.category` tag.
                "vgi.categories": _SCHEMA_CATEGORIES,
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
