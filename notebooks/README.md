# Executed selected-place distance walkthrough

[How close is the place selected by reverse geocoding?](01_audit_selected_place_distance.ipynb) audits actual offline worker results for explicit city-centre, offshore, Arctic, invalid and missing demonstration coordinates. It measures point-to-selected-place distances, verifies haversine arithmetic in SQL and varies a proximity screen without recomputing lookups.

## Install and run

Requirements: [uv](https://docs.astral.sh/uv/), Git, Python 3.13 or 3.14, and initial network access for code/dependency installation. The analysis itself is offline. Copy all of notebooks/ to run without the worker source checkout, root environment or a globally installed kernel.

```sh
cd notebooks
uv sync --frozen
uv run --frozen python execute.py --timeout 240
```

The separate environment pins published Haybarn 2.0.0rc11. The runner creates a temporary kernel using its own interpreter and notebook-directory cwd. Successful outputs are saved atomically; failures preserve the previous notebook. Root dependencies and lock are not discovered or modified.

For interactive editing:

```sh
uv run --frozen python -m ipykernel install --user --name vgi-geocode-notebooks --display-name 'Python (geocode notebooks)'
```

Keep cwd here, choose the kernel, edit the parameter, restart and run all. Parameters and SQL remain expanded; Python presentation and cleanup are folded. Official branding is local and unchanged.

## Focused radius experiment

`RADIUS_KM=50` accepts finite numbers from 1 to 2000 km; equality passes the screen. Run a separate 500 km copy:

```sh
uv run --frozen python execute.py --timeout 240 --parameters '{"RADIUS_KM":500}' --output-dir artifacts/radius-500
```

Reset 50 for defaults. Overrides require a separate output destination. Experiment copies include branding, helpers, README and environment files; artifacts/logs/chart extracts are ignored. The coordinate set, worker results and selected-place distances remain unchanged by this parameter.

## Portable launch and provenance

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'geocode' (TYPE vgi, LOCATION 'uvx --python 3.13 --with vgi-python==0.51.0 --with vgi-rpc==0.53.1 --with pyarrow==26.0.1 --with reverse-geocoder==1.5.1 --with timezonefinder==9.2.0 --with numpy==2.5.4 --with scipy==1.18.1 --from git+https://github.com/Query-farm/vgi-geocode@v0.4.0 vgi-geocode-worker');
```

The tested Git tag resolves to `5142f5245558c20d852c2d35a6fd99b21c709242`. The data-bearing libraries are pinned: reverse_geocoder 1.5.1 includes its GeoNames CSV and timezonefinder 9.2.0 includes its timezone index. No dataset refresh or public API occurs during the analysis. Bundled-data age is not inferred from execution time. Upgrading means explicitly changing a tag/library pin and rerunning both scenarios and checks; selected labels can change with source snapshots.

Coordinates are authored examples visible in SQL, not measured positions, personal location data or a representative sample. Latitude/longitude are decimal degrees. Invalid and missing inputs remain NULL results, never zero distances. The selected record's country describes that gazetteer place, not territorial containment of the input. Timezone is an independent polygon lookup at the original coordinate, and ocean `Etc/GMT…` names are allowed.

The pinned reverse_geocoder constructs and queries its KD-tree directly in latitude/longitude degree coordinates. Its chosen place is **not guaranteed to be nearest by great-circle distance**, especially near poles or the antimeridian. The worker's separate `distance_km` uses a spherical haversine formula with radius 6371.0088 km. This notebook audits that chosen match and independently verifies the formula in SQL; it does not replace the worker lookup with Python spatial calculations.

The screen is a chosen proximity rule, not a confidence score, land/water test, legal boundary determination, travel route or survey distance. The small sample's exact threshold curve describes its own returned matches only. Provider library/data licenses remain those of the underlying packages; reverse_geocoder is an unmodified separately installed LGPL dependency.

## Validation

Verified through published Haybarn on macOS arm64:

- Detached notebooks-only defaults and radius-500 experiment pass in 4.1 s and 4.5 s. Each has 14 code cells, six visible SQL cells, two charts, nine SQL invariants plus monotonicity and resolved-denominator checks. All four charts were reviewed; numeric log labels, threshold lines and result annotations are legible.
- Nine inputs produce seven valid place matches, one invalid-input result and one missing-input result. Relaxing 50→500 km changes passing matches 3→6. All selected names, coordinates, countries, timezones and measured distances stay fixed; the Arctic match remains about 1574 km away and beyond either screen.
- Three runner guards preserve the successful default. Both notebooks pass structural review; notebook Ruff check/format, official branding and source-character checks pass.
- Required source gates pass: 44 pytest tests, 67 SQL assertions in three cases, Ruff format/lint, strict mypy (five files), isolated pydoclint, and metadata 100 L2 with zero findings at fail-on-info. Metadata example execution was enabled; external-link and paid AI passes were disabled.

The root lock and worker source remain unchanged. Ignored artifacts contain validation logs, extracted charts and the separate radius experiment.
