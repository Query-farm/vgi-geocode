<p align="center">
  <img src="https://raw.githubusercontent.com/Query-farm/vgi/main/docs/vgi-logo.png" alt="Vector Gateway Interface (VGI)" width="320">
</p>

<p align="center"><em>A <a href="https://query.farm">Query.Farm</a> VGI worker for DuckDB.</em></p>

# vgi-geocode

[![CI](https://github.com/Query-farm/vgi-geocode/actions/workflows/ci.yml/badge.svg)](https://github.com/Query-farm/vgi-geocode/actions/workflows/ci.yml)

A [VGI](https://query.farm) worker that brings **offline reverse geocoding**
into DuckDB/SQL. Give it a latitude/longitude and it returns the **nearest
place, country, admin regions and IANA timezone** — as plain SQL scalar
functions, with **no API keys and no network calls**. It is backed by two
bundled spatial databases: [`reverse_geocoder`](https://pypi.org/project/reverse_geocoder/)
(a GeoNames cities KD-tree; **LGPL-3.0** — see the licensing note below) and
[`timezonefinder`](https://pypi.org/project/timezonefinder/) (offline IANA
timezone polygons; MIT).

```sql
INSTALL vgi FROM community; LOAD vgi;
ATTACH 'geocode' (TYPE vgi, LOCATION 'uv run geocode_worker.py');

SELECT geocode.nearest_city(40.7128, -74.0060);          -- 'New York City'
SELECT geocode.country_code(48.8566, 2.3522);            -- 'FR'
SELECT geocode.admin1(35.6762, 139.6503);                -- 'Tokyo'
SELECT geocode.admin2(34.0522, -118.2437);               -- county / district
SELECT geocode.timezone(40.7128, -74.0060);              -- 'America/New_York'
SELECT geocode.reverse_geocode(40.7128, -74.0060);       -- STRUCT(...)
SELECT geocode.reverse_geocode(40.7128, -74.0060).city;  -- 'New York City'
SELECT geocode.distance_km(40.7128, -74.0060, 51.5074, -0.1278);  -- ~5570 km
```

Everything runs **offline and deterministically** — there are no network or API
lookups, so the worker is fast and hermetic, and the same input always gives the
same answer. The two spatial indexes are built **once at startup** (warmed at
worker spawn) and cached for the process lifetime, so the first query never pays
the index-build cost.

## Scalars only (per-row), positional arguments

Every answer here is per-row, so all functions are **scalars**. VGI/DuckDB
scalar functions take **positional** arguments (`name := value` is a
table-function/macro feature). None of these functions have optional arguments,
so there are no arity overloads — each is a single function:

```sql
SELECT id, geocode.country_code(lat, lon) AS cc      FROM pings;
SELECT id, geocode.timezone(lat, lon)     AS tz      FROM pings;
SELECT geocode.reverse_geocode(lat, lon).admin1      FROM pings;
SELECT geocode.distance_km(a_lat, a_lon, b_lat, b_lon) FROM trips;
```

**NULL / out-of-range semantics.** A NULL coordinate — or one out of range
(`|lat| > 90` or `|lon| > 180`) — yields **NULL** for every function (never an
error). For `reverse_geocode` the whole STRUCT is NULL. Boundary values (the
poles `±90`, the antimeridian `±180`) are **in range** and resolve normally.

**Ocean points.** Reverse geocoding is "nearest **land** place": an ocean
coordinate still resolves to the closest city in the GeoNames database (the
KD-tree has no concept of water). This is documented, intentional behaviour.

## Function catalog

| Function | Form | Signature | Returns |
| --- | --- | --- | --- |
| `nearest_city` | scalar | `(lat, lon)` | `VARCHAR` (NULL if out of range) |
| `country_code` | scalar | `(lat, lon)` | `VARCHAR` — ISO-3166 alpha-2 |
| `admin1` | scalar | `(lat, lon)` | `VARCHAR` — state / region |
| `admin2` | scalar | `(lat, lon)` | `VARCHAR` — county / district |
| `reverse_geocode` | scalar | `(lat, lon)` | `STRUCT(city, admin1, admin2, country_code, place_lat, place_lon)` |
| `timezone` | scalar | `(lat, lon)` | `VARCHAR` — IANA tz name |
| `distance_km` | scalar | `(lat1, lon1, lat2, lon2)` | `DOUBLE` — haversine km |

All coordinates are `DOUBLE` (degrees). The `reverse_geocode` STRUCT is declared
with an explicit Arrow type (the SDK cannot infer a struct schema); its
`place_lat` / `place_lon` are the coordinates of the **matched place**, not the
query point.

### Reverse geocoding

`nearest_city`, `country_code`, `admin1`, `admin2` and `reverse_geocode` all
share a single KD-tree nearest-neighbour lookup over the bundled GeoNames cities
database. `admin1` is reliably populated for most places (e.g. a US state);
`admin2` (county/district) is populated where GeoNames has it and is `NULL`
otherwise. Use the STRUCT form when you want several fields at once:

```sql
SELECT r.city, r.admin1, r.country_code, r.place_lat, r.place_lon
FROM (SELECT geocode.reverse_geocode(lat, lon) AS r FROM pings);
```

### Timezone

`timezone(lat, lon)` returns the IANA timezone name (e.g. `America/New_York`,
`Europe/Paris`, `Asia/Tokyo`) from `timezonefinder`'s offline polygon index, or
`NULL` if the point is out of range or falls outside every timezone polygon.

### Distance

`distance_km(lat1, lon1, lat2, lon2)` is a pure **haversine** great-circle
distance in kilometres (Earth radius 6371.0088 km). It is independent of the
spatial indexes and handy on its own:

```sql
SELECT geocode.distance_km(40.7128, -74.0060, 51.5074, -0.1278);  -- ~5570 km (NYC -> London)
```

## Dependencies & licensing

| Component | License | Notes |
| --- | --- | --- |
| `vgi-geocode` (this worker) | **MIT** | This repository's own code. |
| [`reverse_geocoder`](https://pypi.org/project/reverse_geocoder/) | **LGPL-3.0** | GeoNames cities KD-tree (reverse lookup). **See the LGPL note below.** |
| [`timezonefinder`](https://pypi.org/project/timezonefinder/) | **MIT** | Offline IANA timezone polygons. |
| [`numpy`](https://pypi.org/project/numpy/) | **BSD-3-Clause** | Numerics (used by the libraries above). |
| [`scipy`](https://pypi.org/project/scipy/) | **BSD-3-Clause** | KD-tree backend. |
| [`vgi-python`](https://github.com/Query-farm/vgi-python) | Query Farm Source-Available | The VGI SDK. |

### LGPL note for `reverse_geocoder`

`reverse_geocoder` is licensed under the **LGPL-3.0**. This worker uses it as an
**unmodified, separately-installed pip dependency** — it is imported, never
copied into or modified within this repository. Under the LGPL that is the
"using the library" case (not "modifying" it), so **`vgi-geocode`'s own code
remains MIT and is fine for commercial use**. The standard LGPL obligation
applies: a recipient of a distributed bundle must be able to **relink or
replace** the LGPL component with a modified version — which is automatically
satisfied here because `reverse_geocoder` is resolved from PyPI as an ordinary,
swappable dependency (you can `pip install` a different version at any time). If
you ever vendor or patch `reverse_geocoder`, those changes must themselves be
offered under the LGPL.

City labels, admin regions and coordinates are only as current as the bundled
GeoNames snapshot inside `reverse_geocoder`; treat city names as approximate and
prefer the stable facts (country code, timezone) for exact matching.

## Local development

```sh
uv sync --extra dev      # create .venv with vgi-python + reverse_geocoder + timezonefinder + dev tools
make test                # pytest (unit + integration) + SQL end-to-end
make test-unit           # pytest only
make test-sql            # DuckDB sqllogictest files via haybarn-unittest
uv run ruff check .      # lint
uv run mypy vgi_geocode/
```

`tests/test_geocoder.py` covers the pure lookup/distance logic (known landmarks,
poles/antimeridian boundaries, out-of-range and NULL edges, ocean points);
`tests/test_scalars.py` spawns `geocode_worker.py` over the VGI client/RPC stack
exactly as DuckDB would after `ATTACH`. The `test/sql/*.test` files are DuckDB
sqllogictest cases run by
[`haybarn-unittest`](https://pypi.org/project/haybarn-unittest/)
(`uv tool install haybarn-unittest`) against a real `ATTACH` + `SELECT`.

## Layout

```
geocode_worker.py        entry point; assembles the `geocode` catalog (inline uv script metadata);
                         warms the spatial indexes at spawn via run()
Makefile                 test / test-unit / test-sql targets
vgi_geocode/
  geocoder.py            pure offline lookup + haversine logic (no Arrow/VGI); cached indexes
  scalars.py             per-row scalars; reverse_geocode returns a STRUCT
  schema_utils.py        Arrow field/comment helper
tests/
  test_geocoder.py       pure-logic unit + edge tests
  test_scalars.py        per-row scalar lifecycle via vgi.client.Client
test/sql/
  *.test                 DuckDB sqllogictest end-to-end cases (haybarn-unittest)
```

---

## Authorship & License

Written by [Query.Farm](https://query.farm) — every VGI worker is designed and built by Query.Farm.

Copyright 2026 Query Farm LLC - https://query.farm

