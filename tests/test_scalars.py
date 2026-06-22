"""End-to-end tests for the per-row scalar geocoding functions.

These spawn ``geocode_worker.py`` as a subprocess via ``vgi.client.Client`` and
call each scalar exactly as DuckDB would after ``ATTACH``. The ``lat`` / ``lon``
columns travel in the input batch (``Param`` arguments); there are no constant
arguments, so ``positional`` is always empty.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pytest
from vgi import Arguments
from vgi.client import Client

_WORKER = str(Path(__file__).resolve().parent.parent / "geocode_worker.py")


@pytest.fixture(scope="module")
def client() -> Iterator[Client]:
    # Current interpreter (deps already installed) + worker_limit=1 so output
    # order matches input order for deterministic per-row assertions.
    with Client(f"{sys.executable} {_WORKER}", worker_limit=1) as c:
        yield c


def _two(
    client: Client,
    name: str,
    lats: list[float | None],
    lons: list[float | None],
) -> list:
    batch = pa.RecordBatch.from_pydict(
        {
            "lat": pa.array(lats, type=pa.float64()),
            "lon": pa.array(lons, type=pa.float64()),
        }
    )
    results = list(
        client.scalar_function(
            function_name=name,
            input=iter([batch]),
            arguments=Arguments(positional=[]),
        )
    )
    return results[0]["result"].to_pylist()


def _four(client: Client, name: str, rows: list[tuple]) -> list:
    cols = list(zip(*rows, strict=True))
    batch = pa.RecordBatch.from_pydict(
        {
            "a": pa.array(cols[0], type=pa.float64()),
            "b": pa.array(cols[1], type=pa.float64()),
            "c": pa.array(cols[2], type=pa.float64()),
            "d": pa.array(cols[3], type=pa.float64()),
        }
    )
    results = list(
        client.scalar_function(
            function_name=name,
            input=iter([batch]),
            arguments=Arguments(positional=[]),
        )
    )
    return results[0]["result"].to_pylist()


class TestCountryCode:
    def test_known(self, client: Client) -> None:
        out = _two(
            client,
            "country_code",
            [40.7128, 48.8566, 35.6762],
            [-74.0060, 2.3522, 139.6503],
        )
        assert out == ["US", "FR", "JP"]

    def test_null_and_out_of_range(self, client: Client) -> None:
        out = _two(client, "country_code", [None, 91.0, 0.0], [0.0, 0.0, 181.0])
        assert out == [None, None, None]


class TestTimezone:
    def test_known(self, client: Client) -> None:
        out = _two(client, "timezone", [40.7128, 48.8566], [-74.0060, 2.3522])
        assert out == ["America/New_York", "Europe/Paris"]


class TestNearestCity:
    def test_present(self, client: Client) -> None:
        out = _two(client, "nearest_city", [40.7128], [-74.0060])
        assert out[0] is not None and "York" in out[0]


class TestReverseGeocodeStruct:
    def test_struct_fields(self, client: Client) -> None:
        out = _two(client, "reverse_geocode", [48.8566], [2.3522])
        rec = out[0]
        assert rec["country_code"] == "FR"
        assert rec["city"]
        assert isinstance(rec["place_lat"], float)

    def test_null_struct(self, client: Client) -> None:
        out = _two(client, "reverse_geocode", [None], [None])
        assert out[0] is None


class TestAdmin:
    def test_admin1(self, client: Client) -> None:
        out = _two(client, "admin1", [40.7128], [-74.0060])
        assert out[0]  # truthy


class TestDistanceKm:
    def test_nyc_to_london(self, client: Client) -> None:
        out = _four(client, "distance_km", [(40.7128, -74.0060, 51.5074, -0.1278)])
        assert out[0] is not None and math.isclose(out[0], 5570.0, abs_tol=60.0)

    def test_null(self, client: Client) -> None:
        out = _four(client, "distance_km", [(None, 0.0, 0.0, 0.0)])
        assert out[0] is None
