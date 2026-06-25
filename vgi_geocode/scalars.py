"""Per-row scalar geocoding functions.

Every function here is a true DuckDB **scalar** -- one row in, one value out --
so it can be used inline in any projection or predicate:

    SELECT geocode.nearest_city(lat, lon)            FROM pings;
    SELECT geocode.country_code(lat, lon)            FROM pings;
    SELECT geocode.timezone(lat, lon)                FROM pings;
    SELECT geocode.reverse_geocode(lat, lon).city    FROM pings;
    SELECT geocode.distance_km(a_lat, a_lon, b_lat, b_lon) FROM trips;

A note on argument syntax
-------------------------
VGI / DuckDB *scalar* functions take **positional** arguments (the ``name :=
value`` named-argument syntax is a property of table functions and macros, not
scalars). None of the functions here have optional arguments, so there are no
arity overloads -- each is a single class.

NULL / out-of-range semantics
-----------------------------
A NULL coordinate, or one out of range (``|lat| > 90`` or ``|lon| > 180``),
yields NULL output for every function (never an error). This is uniform across
``nearest_city``, ``country_code``, ``admin1``, ``admin2``, ``timezone``,
``reverse_geocode`` (the whole STRUCT is NULL), and ``distance_km``.

The STRUCT-returning ``reverse_geocode`` REQUIRES an explicit
``Returns(arrow_type=...)`` (the SDK cannot infer a struct schema), so its
struct type is declared once as ``_REVERSE_TYPE`` and reused in both the
``compute`` annotation and ``on_bind``.
"""

from __future__ import annotations

import json
from typing import Annotated

import pyarrow as pa
from vgi.arguments import Param, Returns
from vgi.metadata import FunctionExample, NullHandling
from vgi.scalar_function import BindParameters, BindResult, ScalarFunction

from . import geocoder
from .geocoder import Place
from .schema_utils import field

# ---------------------------------------------------------------------------
# Per-object discovery/description tags (vgi-lint strict profile).
#
# Every function surfaces, via its ``Meta.tags`` dict, the discovery/description
# tags the strict profile gates on:
#   - ``vgi.title`` (VGI124)      human-friendly display name (must not
#                                 normalize-equal the machine name -> VGI125)
#   - ``vgi.doc_llm`` (VGI112)    Markdown narrative aimed at LLMs/agents
#   - ``vgi.doc_md`` (VGI113)     Markdown narrative for human docs (DISTINCT
#                                 from doc_llm -- identical values are flagged)
#   - ``vgi.keywords`` (VGI138)   JSON array of search-term/synonym strings
#
# ``vgi.source_url`` is intentionally NOT set per object (VGI139): the source
# link belongs only on the catalog object, so it is declared once there.
# ---------------------------------------------------------------------------


def _object_tags(
    title: str,
    doc_llm: str,
    doc_md: str,
    keywords: list[str],
) -> dict[str, str]:
    """Build the standard per-object discovery/description tags.

    Args:
        title: Human-friendly display name for the function (VGI124).
        doc_llm: Markdown narrative aimed at LLMs/agents (VGI112).
        doc_md: Markdown narrative for human documentation (VGI113).
        keywords: Search terms/synonyms; serialized to a JSON array of strings
            for ``vgi.keywords`` as required by VGI138.

    Returns:
        The tag dictionary to assign to the function's ``Meta.tags``.
    """
    return {
        "vgi.title": title,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
        "vgi.keywords": json.dumps(keywords),
    }


# Guaranteed-runnable, catalog-qualified examples (VGI509). Each ``sql`` is
# self-contained and re-runnable against an attached ``geocode`` worker.
# ``expected_result`` is omitted deliberately: the linter only needs each query
# to execute cleanly, and pinning GeoNames labels (which drift between snapshots)
# would be brittle.
_EXECUTABLE_EXAMPLES = (
    "["
    '{"description": "Nearest city to New York coordinates.", '
    '"sql": "SELECT geocode.main.nearest_city(40.7128, -74.0060) AS city"},'
    '{"description": "ISO-3166 country code for Paris coordinates.", '
    '"sql": "SELECT geocode.main.country_code(48.8566, 2.3522) AS cc"},'
    '{"description": "First-level admin region (state) for Tokyo.", '
    '"sql": "SELECT geocode.main.admin1(35.6762, 139.6503) AS region"},'
    '{"description": "Full nearest-place STRUCT for New York.", '
    '"sql": "SELECT geocode.main.reverse_geocode(40.7128, -74.0060) AS place"},'
    '{"description": "IANA timezone for New York coordinates.", '
    '"sql": "SELECT geocode.main.timezone(40.7128, -74.0060) AS tz"},'
    '{"description": "Great-circle distance from New York to London (km).", '
    '"sql": "SELECT geocode.main.distance_km(40.7128, -74.0060, 51.5074, -0.1278) AS km"}'
    "]"
)


# ---------------------------------------------------------------------------
# Struct type for reverse_geocode (explicit -- the SDK cannot infer it).
# ---------------------------------------------------------------------------

_REVERSE_TYPE = pa.struct(
    [
        field("city", pa.string(), "Nearest city / place name."),
        field("admin1", pa.string(), "First-level admin region (state / region)."),
        field("admin2", pa.string(), "Second-level admin region (county / district)."),
        field("country_code", pa.string(), "ISO-3166 alpha-2 country code."),
        field("place_lat", pa.float64(), "Latitude of the matched place."),
        field("place_lon", pa.float64(), "Longitude of the matched place."),
    ]
)

_LAT_DOC = "Latitude in degrees (-90..90); NULL or out of range -> NULL."
_LON_DOC = "Longitude in degrees (-180..180); NULL or out of range -> NULL."


def _place_to_dict(place: Place | None) -> dict[str, object] | None:
    if place is None:
        return None
    return {
        "city": place.city or None,
        "admin1": place.admin1 or None,
        "admin2": place.admin2 or None,
        "country_code": place.country_code or None,
        "place_lat": place.place_lat,
        "place_lon": place.place_lon,
    }


# ===========================================================================
# nearest_city / country_code / admin1 / admin2  -- one VARCHAR each.
# ===========================================================================


class NearestCityFunction(ScalarFunction):
    """``nearest_city(lat, lon)`` -- name of the nearest known city."""

    class Meta:
        """Function metadata."""

        name = "nearest_city"
        description = "Name of the nearest known city to (lat, lon); NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.main.nearest_city(40.7128, -74.0060)",
                description="Nearest city to New York coordinates",
            ),
        ]
        tags = {
            **_object_tags(
                "Find Nearest City",
                "Return the name of the nearest known populated place (city/town) to a "
                "given latitude/longitude, **entirely offline** using a GeoNames cities "
                "KD-tree.\n\n"
                "## When to use\n"
                "Reach for this when you have raw coordinate columns (GPS pings, device "
                "telemetry, store/asset locations) and want a human-readable place label "
                "to group, filter, or display by.\n\n"
                "## Inputs / outputs\n"
                "- `lat` DOUBLE in `[-90, 90]`, `lon` DOUBLE in `[-180, 180]`.\n"
                "- Returns a `VARCHAR` city name, or `NULL`.\n\n"
                "## Behavior & edge cases\n"
                "- This is a *nearest-land-place* lookup: the index has no notion of "
                "water, so a point in the ocean resolves to the closest coastal city.\n"
                "- A `NULL` coordinate or one out of range yields `NULL` (never an error).\n"
                "- City labels track the bundled GeoNames snapshot and can drift slightly "
                "between releases.",
                "# nearest_city\n\n"
                "Name of the nearest known city to `(lat, lon)`, computed offline from a "
                "GeoNames cities KD-tree.\n\n"
                "## Usage\n\n"
                "```sql\n"
                "SELECT geocode.main.nearest_city(40.7128, -74.0060);  -- 'New York City'\n"
                "```\n\n"
                "## Notes\n\n"
                "Returns `NULL` for `NULL` or out-of-range inputs. Ocean points resolve to "
                "the nearest land city; labels follow the bundled GeoNames snapshot.",
                [
                    "nearest city",
                    "reverse geocode",
                    "city name",
                    "place name",
                    "locality",
                    "town",
                    "geocoding",
                    "lat lon to city",
                    "coordinates to city",
                    "geonames",
                ],
            ),
            # VGI509: at least one object ships guaranteed-runnable examples.
            "vgi.executable_examples": _EXECUTABLE_EXAMPLES,
        }

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array(
            [(p.city or None) if p is not None else None for p in places],
            type=pa.string(),
        )


class CountryCodeFunction(ScalarFunction):
    """``country_code(lat, lon)`` -- ISO-3166 alpha-2 country code."""

    class Meta:
        """Function metadata."""

        name = "country_code"
        description = "ISO-3166 alpha-2 country code of the nearest place; NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.main.country_code(48.8566, 2.3522)",
                description="Country code for Paris coordinates ('FR')",
            ),
        ]
        tags = _object_tags(
            "ISO Country Code",
            "Return the **ISO-3166 alpha-2** country code (e.g. `US`, `FR`, `JP`) of "
            "the nearest known place to a latitude/longitude, computed **offline** from "
            "the bundled GeoNames cities index.\n\n"
            "## When to use\n"
            "Use it to attribute coordinate data to a country for grouping, filtering, "
            "compliance, or joining to country reference tables -- without any geocoding "
            "API.\n\n"
            "## Inputs / outputs\n"
            "- `lat` DOUBLE in `[-90, 90]`, `lon` DOUBLE in `[-180, 180]`.\n"
            "- Returns a two-letter `VARCHAR` country code, or `NULL`.\n\n"
            "## Behavior & edge cases\n"
            "- Resolved from the *nearest land place*, so an offshore point yields the "
            "code of the closest coastal country.\n"
            "- `NULL` or out-of-range coordinates return `NULL` (never an error).",
            "# country_code\n\n"
            "ISO-3166 alpha-2 country code of the nearest place to `(lat, lon)`, computed "
            "offline.\n\n"
            "## Usage\n\n"
            "```sql\n"
            "SELECT geocode.main.country_code(48.8566, 2.3522);  -- 'FR'\n"
            "```\n\n"
            "## Notes\n\n"
            "Two-letter uppercase code. Returns `NULL` for `NULL`/out-of-range inputs; "
            "offshore points map to the nearest coastal country.",
            [
                "country code",
                "iso 3166",
                "iso country",
                "alpha-2",
                "country",
                "nationality",
                "reverse geocode",
                "lat lon to country",
                "coordinates to country",
                "geonames",
            ],
        )

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array(
            [(p.country_code or None) if p is not None else None for p in places],
            type=pa.string(),
        )


class Admin1Function(ScalarFunction):
    """``admin1(lat, lon)`` -- first-level admin region (state / region)."""

    class Meta:
        """Function metadata."""

        name = "admin1"
        description = "First-level admin region (state / region) of the nearest place; NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.main.admin1(40.7128, -74.0060)",
                description="State / region for New York coordinates",
            ),
        ]
        tags = _object_tags(
            "First-Level Admin Region",
            "Return the **first-level administrative region** -- state, province, or "
            "region -- of the nearest known place to a latitude/longitude, computed "
            "**offline** from the bundled GeoNames index.\n\n"
            "## When to use\n"
            "Use it to roll coordinate data up to a state/province for regional "
            "reporting, territory assignment, or joins to administrative reference "
            "data.\n\n"
            "## Inputs / outputs\n"
            "- `lat` DOUBLE in `[-90, 90]`, `lon` DOUBLE in `[-180, 180]`.\n"
            "- Returns the admin1 name as `VARCHAR`, or `NULL`.\n\n"
            "## Behavior & edge cases\n"
            "- Names follow GeoNames conventions (e.g. `New York`, `Tokyo`, `Bavaria`) "
            "and can vary in spelling/transliteration between snapshots.\n"
            "- `NULL` or out-of-range coordinates return `NULL` (never an error).",
            "# admin1\n\n"
            "First-level administrative region (state / province / region) of the nearest "
            "place to `(lat, lon)`, computed offline.\n\n"
            "## Usage\n\n"
            "```sql\n"
            "SELECT geocode.main.admin1(40.7128, -74.0060);  -- 'New York'\n"
            "```\n\n"
            "## Notes\n\n"
            "GeoNames admin1 label. Returns `NULL` for `NULL`/out-of-range inputs; "
            "spelling can drift between bundled snapshots.",
            [
                "admin1",
                "state",
                "province",
                "region",
                "first-level admin",
                "administrative region",
                "reverse geocode",
                "lat lon to state",
                "coordinates to region",
                "geonames",
            ],
        )

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array(
            [(p.admin1 or None) if p is not None else None for p in places],
            type=pa.string(),
        )


class Admin2Function(ScalarFunction):
    """``admin2(lat, lon)`` -- second-level admin region (county / district)."""

    class Meta:
        """Function metadata."""

        name = "admin2"
        description = "Second-level admin region (county/district) of the nearest place; NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.main.admin2(34.0522, -118.2437)",
                description="County / district for Los Angeles coordinates",
            ),
        ]
        tags = _object_tags(
            "Second-Level Admin Region",
            "Return the **second-level administrative region** -- county, district, or "
            "borough -- of the nearest known place to a latitude/longitude, computed "
            "**offline** from the bundled GeoNames index.\n\n"
            "## When to use\n"
            "Use it for finer-grained geographic rollups than `admin1` (state): county- "
            "or district-level reporting, routing, or joins.\n\n"
            "## Inputs / outputs\n"
            "- `lat` DOUBLE in `[-90, 90]`, `lon` DOUBLE in `[-180, 180]`.\n"
            "- Returns the admin2 name as `VARCHAR`, or `NULL`.\n\n"
            "## Behavior & edge cases\n"
            "- admin2 is **frequently empty** in GeoNames for many countries, so `NULL` "
            "results are common and expected -- not an error.\n"
            "- `NULL` or out-of-range coordinates also return `NULL`.",
            "# admin2\n\n"
            "Second-level administrative region (county / district) of the nearest place "
            "to `(lat, lon)`, computed offline.\n\n"
            "## Usage\n\n"
            "```sql\n"
            "SELECT geocode.main.admin2(34.0522, -118.2437);  -- 'Los Angeles County'\n"
            "```\n\n"
            "## Notes\n\n"
            "Often `NULL` -- admin2 coverage in GeoNames is sparse outside a few "
            "countries. Also `NULL` for `NULL`/out-of-range inputs.",
            [
                "admin2",
                "county",
                "district",
                "borough",
                "second-level admin",
                "administrative region",
                "reverse geocode",
                "lat lon to county",
                "coordinates to district",
                "geonames",
            ],
        )

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array(
            [(p.admin2 or None) if p is not None else None for p in places],
            type=pa.string(),
        )


# ===========================================================================
# reverse_geocode -- full STRUCT record (explicit Returns(arrow_type=...)).
# ===========================================================================


class ReverseGeocodeFunction(ScalarFunction):
    """``reverse_geocode(lat, lon)`` -- full nearest-place STRUCT record."""

    class Meta:
        """Function metadata."""

        name = "reverse_geocode"
        description = (
            "Nearest place as STRUCT(city, admin1, admin2, country_code, place_lat, place_lon); NULL if out of range"
        )
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.main.reverse_geocode(40.7128, -74.0060)",
                description="Full nearest-place record for New York coordinates",
            ),
            FunctionExample(
                sql="SELECT geocode.main.reverse_geocode(35.6762, 139.6503).city",
                description="City field of the nearest place (Tokyo)",
            ),
        ]
        tags = _object_tags(
            "Reverse Geocode To Struct",
            "Resolve a latitude/longitude to the **full nearest-place record** in a "
            "single call, returning a `STRUCT(city, admin1, admin2, country_code, "
            "place_lat, place_lon)` computed **entirely offline**.\n\n"
            "## When to use\n"
            "Prefer this over the single-field helpers (`nearest_city`, `country_code`, "
            "`admin1`, `admin2`) when you need several attributes at once -- it does the "
            "KD-tree lookup once and lets you pluck fields with dot access.\n\n"
            "## Inputs / outputs\n"
            "- `lat` DOUBLE in `[-90, 90]`, `lon` DOUBLE in `[-180, 180]`.\n"
            "- Returns a `STRUCT` with `city`, `admin1`, `admin2` (`VARCHAR`), "
            "`country_code` (`VARCHAR`), and `place_lat`/`place_lon` (`DOUBLE`, the "
            "coordinates of the matched GeoNames place).\n\n"
            "## Behavior & edge cases\n"
            "- For a `NULL` or out-of-range coordinate the **entire struct is `NULL`**.\n"
            "- Individual fields may be `NULL` (notably `admin2`) even for a valid match.\n"
            "- Access fields with `reverse_geocode(lat, lon).city`, etc.",
            "# reverse_geocode\n\n"
            "Full nearest-place record for `(lat, lon)` as a `STRUCT`, computed offline.\n\n"
            "## Columns (struct fields)\n\n"
            "| field | type | description |\n"
            "|---|---|---|\n"
            "| `city` | VARCHAR | Nearest city / place name. |\n"
            "| `admin1` | VARCHAR | First-level admin region (state / region). |\n"
            "| `admin2` | VARCHAR | Second-level admin region (county / district). |\n"
            "| `country_code` | VARCHAR | ISO-3166 alpha-2 country code. |\n"
            "| `place_lat` | DOUBLE | Latitude of the matched place. |\n"
            "| `place_lon` | DOUBLE | Longitude of the matched place. |\n\n"
            "## Usage\n\n"
            "```sql\n"
            "SELECT geocode.main.reverse_geocode(40.7128, -74.0060);        -- STRUCT(...)\n"
            "SELECT geocode.main.reverse_geocode(35.6762, 139.6503).city;   -- 'Tokyo'\n"
            "```\n\n"
            "## Notes\n\n"
            "Whole struct is `NULL` for `NULL`/out-of-range inputs; `admin2` is often `NULL`.",
            [
                "reverse geocode",
                "struct",
                "full record",
                "place",
                "city admin country",
                "geocoding",
                "lat lon to place",
                "coordinates to address",
                "geonames",
                "point lookup",
            ],
        )

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        """Declare the STRUCT output type at plan time."""
        return BindResult(_REVERSE_TYPE)

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_REVERSE_TYPE)]:
        """Map each input row to its output value."""
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array([_place_to_dict(p) for p in places], type=_REVERSE_TYPE)


# ===========================================================================
# timezone -- IANA tz name.
# ===========================================================================


class TimezoneFunction(ScalarFunction):
    """``timezone(lat, lon)`` -- IANA timezone name."""

    class Meta:
        """Function metadata."""

        name = "timezone"
        description = "IANA timezone name (e.g. 'America/New_York') for (lat, lon); NULL if out of range"
        categories = ["geocode", "timezone"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.main.timezone(40.7128, -74.0060)",
                description="IANA timezone for New York coordinates",
            ),
        ]
        tags = _object_tags(
            "IANA Timezone Lookup",
            "Return the **IANA timezone name** (e.g. `America/New_York`, `Europe/Paris`, "
            "`Asia/Tokyo`) for a latitude/longitude, computed **offline** from bundled "
            "IANA timezone polygons (`timezonefinder`).\n\n"
            "## When to use\n"
            "Use it to localize timestamps, derive local time, or bucket coordinate data "
            "by timezone -- the returned string drops straight into DuckDB's "
            "`timezone(name, ts)` / `AT TIME ZONE`.\n\n"
            "## Inputs / outputs\n"
            "- `lat` DOUBLE in `[-90, 90]`, `lon` DOUBLE in `[-180, 180]`.\n"
            "- Returns the IANA tz name as `VARCHAR`, or `NULL`.\n\n"
            "## Behavior & edge cases\n"
            "- Uses true timezone *polygons*, not a nearest-place lookup, so it is exact "
            "at borders on land.\n"
            "- Points outside every tz polygon return `NULL`; some uninhabited ocean "
            "cells resolve to a valid `Etc/GMT±N` name instead.\n"
            "- `NULL` or out-of-range coordinates return `NULL` (never an error).",
            "# timezone\n\n"
            "IANA timezone name for `(lat, lon)`, computed offline from timezone "
            "polygons.\n\n"
            "## Usage\n\n"
            "```sql\n"
            "SELECT geocode.main.timezone(40.7128, -74.0060);  -- 'America/New_York'\n"
            "```\n\n"
            "## Notes\n\n"
            "Polygon-accurate. Returns `NULL` outside all tz polygons and for "
            "`NULL`/out-of-range inputs; some ocean cells map to `Etc/GMT±N`.",
            [
                "timezone",
                "iana timezone",
                "tz",
                "time zone",
                "olson",
                "lat lon to timezone",
                "coordinates to timezone",
                "timezonefinder",
                "local time",
                "utc offset",
            ],
        )

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        lats = lat.to_pylist()
        lons = lon.to_pylist()
        return pa.array(
            [geocoder.timezone(la, lo) for la, lo in zip(lats, lons, strict=True)],
            type=pa.string(),
        )


# ===========================================================================
# distance_km -- pure haversine over four coordinates.
# ===========================================================================


class DistanceKmFunction(ScalarFunction):
    """``distance_km(lat1, lon1, lat2, lon2)`` -- great-circle distance (km)."""

    class Meta:
        """Function metadata."""

        name = "distance_km"
        description = "Great-circle (haversine) distance in km between two points; NULL if out of range"
        categories = ["geocode", "distance"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.main.distance_km(40.7128, -74.0060, 51.5074, -0.1278)",
                description="Distance from New York to London (~5570 km)",
            ),
        ]
        tags = _object_tags(
            "Great-Circle Distance In Km",
            "Compute the **great-circle (haversine) distance in kilometers** between two "
            "`(lat, lon)` points on a spherical Earth. Pure arithmetic -- no index, no "
            "lookup, no network.\n\n"
            "## When to use\n"
            "Use it for proximity filtering ('within N km of'), nearest-of ranking, trip "
            "length, or any straight-line distance between coordinate pairs in SQL.\n\n"
            "## Inputs / outputs\n"
            "- Four `DOUBLE` arguments: `lat1, lon1, lat2, lon2`, each in the usual "
            "lat `[-90, 90]` / lon `[-180, 180]` ranges.\n"
            "- Returns the distance as a `DOUBLE` in kilometers, or `NULL`.\n\n"
            "## Behavior & edge cases\n"
            "- Uses a mean Earth radius (~6371 km); it is a sphere approximation, not a "
            "geodesic on the WGS-84 ellipsoid, so expect sub-percent error.\n"
            "- If **any** of the four coordinates is `NULL` or out of range the result "
            "is `NULL` (never an error).\n"
            "- Distance is symmetric and zero for identical points.",
            "# distance_km\n\n"
            "Great-circle (haversine) distance in kilometers between two points.\n\n"
            "## Usage\n\n"
            "```sql\n"
            "SELECT geocode.main.distance_km(40.7128, -74.0060, 51.5074, -0.1278);  -- ~5570\n"
            "```\n\n"
            "## Notes\n\n"
            "Spherical approximation (~6371 km radius), sub-percent error vs. WGS-84. "
            "Returns `NULL` if any coordinate is `NULL`/out of range.",
            [
                "distance",
                "haversine",
                "great circle",
                "great-circle",
                "km",
                "kilometers",
                "proximity",
                "nearby",
                "radius",
                "between two points",
                "lat lon distance",
                "geodistance",
            ],
        )

    @classmethod
    def compute(
        cls,
        lat1: Annotated[pa.DoubleArray, Param(doc="Latitude of point 1 (-90..90).")],
        lon1: Annotated[pa.DoubleArray, Param(doc="Longitude of point 1 (-180..180).")],
        lat2: Annotated[pa.DoubleArray, Param(doc="Latitude of point 2 (-90..90).")],
        lon2: Annotated[pa.DoubleArray, Param(doc="Longitude of point 2 (-180..180).")],
    ) -> Annotated[pa.DoubleArray, Returns()]:
        """Map each input row to its output value."""
        a, b, c, d = lat1.to_pylist(), lon1.to_pylist(), lat2.to_pylist(), lon2.to_pylist()
        return pa.array(
            [geocoder.distance_km(*row) for row in zip(a, b, c, d, strict=True)],
            type=pa.float64(),
        )


SCALAR_FUNCTIONS: list[type] = [
    NearestCityFunction,
    CountryCodeFunction,
    Admin1Function,
    Admin2Function,
    ReverseGeocodeFunction,
    TimezoneFunction,
    DistanceKmFunction,
]
