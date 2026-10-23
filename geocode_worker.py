# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.46.1",
#     "reverse_geocoder>=1.5",
#     "timezonefinder>=6",
#     "numpy",
#     "scipy",
#     "pyarrow",
# ]
# ///
"""Repo-root stdio/HTTP entry point for the vgi-geocode worker (PEP 723 shim).

The catalog, :class:`GeocodeWorker`, and :func:`main` now live in the
wheel-importable :mod:`vgi_geocode.worker` module so the built wheel actually
contains the worker (and the Docker image / ``vgi-geocode-worker`` console
script can serve it). This file stays a thin PEP 723 shim that re-exports them,
so ``uv run geocode_worker.py`` -- used by the Makefile, ``ci/run-integration.sh``
and ``tests/test_scalars.py`` -- keeps working unchanged.

Usage:
    uv run geocode_worker.py             # serve over stdio (DuckDB subprocess)

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'geocode' (TYPE vgi, LOCATION 'uv run geocode_worker.py');

    SELECT geocode.nearest_city(40.7128, -74.0060);          -- 'New York City'
    SELECT geocode.timezone(40.7128, -74.0060);              -- 'America/New_York'
    SELECT geocode.distance_km(40.7128, -74.0060, 51.5074, -0.1278);  -- ~5570
"""

from __future__ import annotations

from vgi_geocode.worker import GeocodeWorker, main

__all__ = ["GeocodeWorker", "main"]


if __name__ == "__main__":
    main()
