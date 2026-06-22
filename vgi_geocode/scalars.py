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

from typing import Annotated

import pyarrow as pa
from vgi.arguments import Param, Returns
from vgi.metadata import FunctionExample, NullHandling
from vgi.scalar_function import BindParameters, BindResult, ScalarFunction

from . import geocoder
from .geocoder import Place
from .schema_utils import field

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
        name = "nearest_city"
        description = "Name of the nearest known city to (lat, lon); NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.nearest_city(40.7128, -74.0060)",
                description="Nearest city to New York coordinates",
            ),
        ]

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array(
            [(p.city or None) if p is not None else None for p in places],
            type=pa.string(),
        )


class CountryCodeFunction(ScalarFunction):
    """``country_code(lat, lon)`` -- ISO-3166 alpha-2 country code."""

    class Meta:
        name = "country_code"
        description = "ISO-3166 alpha-2 country code of the nearest place; NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.country_code(48.8566, 2.3522)",
                description="Country code for Paris coordinates ('FR')",
            ),
        ]

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array(
            [(p.country_code or None) if p is not None else None for p in places],
            type=pa.string(),
        )


class Admin1Function(ScalarFunction):
    """``admin1(lat, lon)`` -- first-level admin region (state / region)."""

    class Meta:
        name = "admin1"
        description = "First-level admin region (state / region) of the nearest place; NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.admin1(40.7128, -74.0060)",
                description="State / region for New York coordinates",
            ),
        ]

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array(
            [(p.admin1 or None) if p is not None else None for p in places],
            type=pa.string(),
        )


class Admin2Function(ScalarFunction):
    """``admin2(lat, lon)`` -- second-level admin region (county / district)."""

    class Meta:
        name = "admin2"
        description = "Second-level admin region (county/district) of the nearest place; NULL if out of range"
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.admin2(34.0522, -118.2437)",
                description="County / district for Los Angeles coordinates",
            ),
        ]

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
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
        name = "reverse_geocode"
        description = (
            "Nearest place as STRUCT(city, admin1, admin2, country_code, place_lat, place_lon); "
            "NULL if out of range"
        )
        categories = ["geocode", "reverse"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.reverse_geocode(40.7128, -74.0060)",
                description="Full nearest-place record for New York coordinates",
            ),
            FunctionExample(
                sql="SELECT geocode.reverse_geocode(35.6762, 139.6503).city",
                description="City field of the nearest place (Tokyo)",
            ),
        ]

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        return BindResult(_REVERSE_TYPE)

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_REVERSE_TYPE)]:
        places = geocoder.reverse_geocode_batch(list(zip(lat.to_pylist(), lon.to_pylist(), strict=True)))
        return pa.array([_place_to_dict(p) for p in places], type=_REVERSE_TYPE)


# ===========================================================================
# timezone -- IANA tz name.
# ===========================================================================


class TimezoneFunction(ScalarFunction):
    """``timezone(lat, lon)`` -- IANA timezone name."""

    class Meta:
        name = "timezone"
        description = "IANA timezone name (e.g. 'America/New_York') for (lat, lon); NULL if out of range"
        categories = ["geocode", "timezone"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.timezone(40.7128, -74.0060)",
                description="IANA timezone for New York coordinates",
            ),
        ]

    @classmethod
    def compute(
        cls,
        lat: Annotated[pa.DoubleArray, Param(doc=_LAT_DOC)],
        lon: Annotated[pa.DoubleArray, Param(doc=_LON_DOC)],
    ) -> Annotated[pa.StringArray, Returns()]:
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
        name = "distance_km"
        description = "Great-circle (haversine) distance in km between two points; NULL if out of range"
        categories = ["geocode", "distance"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT geocode.distance_km(40.7128, -74.0060, 51.5074, -0.1278)",
                description="Distance from New York to London (~5570 km)",
            ),
        ]

    @classmethod
    def compute(
        cls,
        lat1: Annotated[pa.DoubleArray, Param(doc="Latitude of point 1 (-90..90).")],
        lon1: Annotated[pa.DoubleArray, Param(doc="Longitude of point 1 (-180..180).")],
        lat2: Annotated[pa.DoubleArray, Param(doc="Latitude of point 2 (-90..90).")],
        lon2: Annotated[pa.DoubleArray, Param(doc="Longitude of point 2 (-180..180).")],
    ) -> Annotated[pa.DoubleArray, Returns()]:
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
