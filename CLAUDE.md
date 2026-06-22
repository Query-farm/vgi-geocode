# CLAUDE.md — vgi-geocode

Contributor/agent notes. User-facing docs live in `README.md`; this is the
"how it's built and where the sharp edges are" companion.

## What this is

A [VGI](https://query.farm) worker that does **offline reverse geocoding** —
lat/lon → nearest place, country, admin regions, IANA timezone — as DuckDB
scalar functions, plus a pure haversine `distance_km`. Backed by
`reverse_geocoder` (a GeoNames cities KD-tree; **LGPL-3.0** — see below) and
`timezonefinder` (offline IANA tz polygons; MIT). `geocode_worker.py` assembles
every function into one `geocode` catalog (single `main` schema) over stdio.
Sibling style/tooling to `vgi-conform` / `vgi-calendar`.

## Layout

```
geocode_worker.py      repo-root stdio entry point; PEP 723 inline deps; main(); warms indexes in run()
vgi_geocode/
  geocoder.py          pure offline lookup + haversine; no Arrow/VGI; unit-testable; cached indexes
  scalars.py           per-row scalars; reverse_geocode returns a STRUCT
  schema_utils.py      pa.Field comment / column-doc helper
tests/                 pytest: test_geocoder (pure), test_scalars (Client RPC)
test/sql/*.test        haybarn-unittest sqllogictest — authoritative E2E
Makefile               test / test-unit / test-sql / lint
```

To add a function: implement the logic in `geocoder.py` (pure, total — never
raises on garbage; returns `None` for out-of-range/NULL), wrap it as a scalar in
`scalars.py`, register it in `scalars.SCALAR_FUNCTIONS` (the worker pulls that
list).

## Scalars are positional-only — and STRUCT returns are explicit (read first)

- **All functions are scalars.** The VGI SDK makes scalar functions
  **positional-only** (`name := value` named args are a table-function/macro
  feature). None of these functions have optional args, so there are **no arity
  overloads** — each is one class.
- **`reverse_geocode` returns a STRUCT, which REQUIRES an explicit
  `Returns(arrow_type=...)`.** The SDK cannot infer a struct schema and will
  raise otherwise. The struct type is declared once as `_REVERSE_TYPE` in
  `scalars.py` and reused in both the `compute` return annotation **and**
  `on_bind` (`BindResult(_REVERSE_TYPE)`). If you add another struct/list-
  returning scalar, do the same — declare the Arrow type and wire it through
  both places.

## Sharp edges (learned the hard way)

1. **`haybarn-unittest` skips `require vgi`.** Under haybarn the extension is not
   autoloaded for `require`, so a `.test` using `require vgi` is silently
   SKIPPED. Use an explicit `statement ok` / `LOAD vgi;` instead (every `.test`
   here already does).
2. **NULL vs out-of-range — both → NULL, never an error.** A NULL coordinate or
   one with `|lat| > 90` / `|lon| > 180` yields NULL for every function (the
   whole STRUCT is NULL for `reverse_geocode`). This is enforced in
   `geocoder._valid_coord` (also rejects NaN/inf) and tested in both the pytest
   suite and the SQL suite. Boundary values (`±90`, `±180`) are **in range**.
3. **Expensive index build is done ONCE and cached.** The `reverse_geocoder`
   KD-tree (~1 s) and the `timezonefinder` polygon index are built lazily and
   memoized in module globals (`_rg`, `_tf`) behind locks. `geocoder.warm_up()`
   forces both, and `GeocodeWorker.run()` calls it at spawn — so the first query
   of every ATTACH doesn't pay the build cost inline (which is the classic E2E
   flake window where a teardown SIGTERM kills a mid-build run). Don't move the
   build into `compute`.
4. **Reverse geocoding is "nearest land place".** The KD-tree has no concept of
   water, so an ocean point returns the closest land city. Documented; don't
   "fix" it.
5. **City names drift; country code + timezone are stable.** Unit tests assert
   the country code and IANA timezone exactly, but only substring-check the city
   (GeoNames labels shift between snapshots). Keep new assertions on stable
   facts.
6. **The unit suite can pass while the RPC path is broken.** `test_geocoder.py`
   calls pure functions directly; only `test_scalars.py` (real
   `vgi.client.Client` subprocess) and `test/sql/*.test` (real `ATTACH`+`SELECT`)
   exercise the wire. **Run the SQL suite** — it's authoritative. If `make
   test-sql` flakes intermittently, re-run 2–3×; only a CONSISTENT failure is
   real.

## `reverse_geocoder` is LGPL-3.0 (licensing note)

`reverse_geocoder` (the reverse-lookup KD-tree backend) is **LGPL-3.0**. We use
it as an **unmodified, separately pip-installed dependency** — imported, never
vendored or patched. That's the "using the library" case, not "modifying" it, so
**vgi-geocode's own code stays MIT and is fine for commercial use**. The standard
LGPL relink/replace obligation is satisfied automatically because the package is
an ordinary, swappable PyPI dependency (a recipient can `pip install` a different
version). If you ever vendor or patch `reverse_geocoder`, those changes must be
offered under the LGPL — don't do that without intent. `timezonefinder` is MIT
and `numpy`/`scipy` are BSD, all permissive with no such caveat.

## Coverage caveats

- City/admin labels and coordinates are only as current as the GeoNames snapshot
  bundled inside `reverse_geocoder`. `admin2` (county/district) is often empty.
- `timezone` returns `NULL` for points outside every tz polygon (rare; some
  uninhabited ocean cells map to `Etc/GMT±N` instead, which IS a valid name).

## Testing

```sh
uv run pytest -q              # unit: pure logic + Client RPC scalars
make test-sql                 # E2E: haybarn-unittest over test/sql/*  (authoritative)
make test                     # both
uv run ruff check . && uv run mypy vgi_geocode/
```

`make test-sql` sets `VGI_GEOCODE_WORKER="uv run --python 3.13
geocode_worker.py"`, puts `~/.local/bin` on PATH, and runs `haybarn-unittest
--test-dir . "test/sql/*"`. Install the runner once with
`uv tool install haybarn-unittest`. CI (`.github/workflows/ci.yml`) runs unit +
lint + a gated `e2e` job that installs haybarn-unittest and runs `make test-sql`.

Everything is pure/offline (no network, no API keys, no model downloads), so the
suite is fast and hermetic.
