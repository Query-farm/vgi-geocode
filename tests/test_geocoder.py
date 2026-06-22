"""Unit tests for the pure offline geocoding logic (no Arrow / VGI).

These call ``vgi_geocode.geocoder`` functions directly. Known landmark
coordinates are asserted "within reason" -- a city name can shift as the
GeoNames database updates, so we assert on the stable facts (country code,
timezone, country-level admin) and only sanity-check the city.
"""

from __future__ import annotations

import math

import pytest

from vgi_geocode import geocoder

# (lat, lon, expected_cc, expected_tz, city_substring_hint)
KNOWN = [
    (40.7128, -74.0060, "US", "America/New_York", "York"),
    (48.8566, 2.3522, "FR", "Europe/Paris", "Paris"),
    (35.6762, 139.6503, "JP", "Asia/Tokyo", "Tokyo"),
    (51.5074, -0.1278, "GB", "Europe/London", "London"),
]


class TestReverseGeocode:
    @pytest.mark.parametrize("lat,lon,cc,tz,city_hint", KNOWN)
    def test_country_code(self, lat, lon, cc, tz, city_hint) -> None:
        assert geocoder.country_code(lat, lon) == cc

    @pytest.mark.parametrize("lat,lon,cc,tz,city_hint", KNOWN)
    def test_timezone(self, lat, lon, cc, tz, city_hint) -> None:
        assert geocoder.timezone(lat, lon) == tz

    @pytest.mark.parametrize("lat,lon,cc,tz,city_hint", KNOWN)
    def test_nearest_city_present(self, lat, lon, cc, tz, city_hint) -> None:
        city = geocoder.nearest_city(lat, lon)
        assert city is not None and len(city) > 0

    def test_nearest_city_new_york(self) -> None:
        # GeoNames labels the NYC point "New York City"; assert it's NY-ish.
        city = geocoder.nearest_city(40.7128, -74.0060)
        assert city is not None and "York" in city

    def test_full_struct(self) -> None:
        place = geocoder.reverse_geocode(48.8566, 2.3522)
        assert place is not None
        assert place.country_code == "FR"
        assert place.city
        assert -90.0 <= place.place_lat <= 90.0
        assert -180.0 <= place.place_lon <= 180.0

    def test_admin1_and_admin2(self) -> None:
        # admin1 is reliably populated for major cities; admin2 may be empty.
        assert geocoder.admin1(40.7128, -74.0060)  # truthy (e.g. "New York")
        # admin2 returns None or a non-empty string -- never an empty string.
        a2 = geocoder.admin2(34.0522, -118.2437)
        assert a2 is None or len(a2) > 0


class TestOceanPoint:
    def test_ocean_returns_nearest_land(self) -> None:
        # Mid-Atlantic: still returns the nearest land city (documented).
        place = geocoder.reverse_geocode(0.0, -30.0)
        assert place is not None
        assert place.city


class TestEdgesNullAndRange:
    @pytest.mark.parametrize(
        "lat,lon",
        [
            (None, None),
            (None, 10.0),
            (10.0, None),
            (91.0, 0.0),
            (-91.0, 0.0),
            (0.0, 181.0),
            (0.0, -181.0),
            (float("nan"), 0.0),
            (0.0, float("inf")),
        ],
    )
    def test_out_of_range_or_null_is_none(self, lat, lon) -> None:
        assert geocoder.reverse_geocode(lat, lon) is None
        assert geocoder.nearest_city(lat, lon) is None
        assert geocoder.country_code(lat, lon) is None
        assert geocoder.admin1(lat, lon) is None
        assert geocoder.admin2(lat, lon) is None
        assert geocoder.timezone(lat, lon) is None

    def test_poles_and_antimeridian_in_range(self) -> None:
        # These ARE valid coordinates -> a place is returned (boundary values).
        assert geocoder.reverse_geocode(90.0, 0.0) is not None
        assert geocoder.reverse_geocode(-90.0, 0.0) is not None
        assert geocoder.reverse_geocode(0.0, 180.0) is not None
        assert geocoder.reverse_geocode(0.0, -180.0) is not None


class TestDistanceKm:
    def test_nyc_to_london(self) -> None:
        d = geocoder.distance_km(40.7128, -74.0060, 51.5074, -0.1278)
        assert d is not None
        assert math.isclose(d, 5570.0, abs_tol=60.0)

    def test_zero_distance(self) -> None:
        assert geocoder.distance_km(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-6)

    def test_symmetry(self) -> None:
        a = geocoder.distance_km(40.7128, -74.0060, 51.5074, -0.1278)
        b = geocoder.distance_km(51.5074, -0.1278, 40.7128, -74.0060)
        assert a is not None and b is not None
        assert math.isclose(a, b, abs_tol=1e-6)

    @pytest.mark.parametrize(
        "args",
        [
            (None, 0.0, 0.0, 0.0),
            (0.0, None, 0.0, 0.0),
            (0.0, 0.0, None, 0.0),
            (0.0, 0.0, 0.0, None),
            (91.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 181.0),
        ],
    )
    def test_null_or_out_of_range(self, args) -> None:
        assert geocoder.distance_km(*args) is None
