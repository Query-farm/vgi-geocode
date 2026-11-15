# Offline Reverse Geocoding examples

[How close is the place selected by reverse geocoding?](01_audit_selected_place_distance.ipynb) preserves the distance audit and adds a comparison of individual place labels with the combined record. Nine authored coordinates include city centres, offshore and Arctic points, an invalid latitude and a missing latitude. Three SVG figures explain label availability, point-to-selected-place distance and an inclusive proximity screen.

[Open in Google Colab](https://colab.research.google.com/github/Query-farm/vgi-geocode/blob/main/notebooks/01_audit_selected_place_distance.ipynb), then run all cells. The notebook installs the shared base package and needs no companion files or worker checkout. For local publication, install [uv](https://docs.astral.sh/uv/) and Git, then:

```sh
cd notebooks
uv sync --locked
uv run --locked vgi-notebooks --timeout 300
```

The separate notebook environment uses the shared runner; it does not modify root dependencies. The visible connection launches the public **v0.4.0** release:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'geocode' (
    TYPE vgi,
    LOCATION 'uvx --python 3.13 --from git+https://github.com/Query-farm/vgi-geocode@v0.4.0 vgi-geocode-worker'
);
```

Initial code, library and extension installation needs network access. The geocoding analysis itself is offline and requires no API key. The release owns its dependency ranges; a fresh installation can resolve newer library/data versions. [Publication provenance](coverage/runtime-data.json) records the actual versions and bundled-data hashes used for the saved outputs. The recorded libraries include reverse-geocoder 1.5.1 and timezonefinder 9.2.0; their data vintage is not inferred from the execution date.

## Try the radius experiment

Change `RADIUS_KM = 50` to `500` in Colab and restart/run all, or save a separate local experiment:

```sh
uv run --locked vgi-notebooks --timeout 300 \
  --parameters '{"RADIUS_KM":500}' --output-dir artifacts/radius-500
```

The screen accepts finite numbers from 1 through 2,000 km. Seven inputs resolve to places; increasing the radius changes passing matches from **3 to 6**. Selected names, coordinates, countries, timezones and distances stay unchanged. Four matched records have no second-level administrative label. All scalar labels agree with the STRUCT after normalizing its empty strings to NULL.

The selected place is the gazetteer's degree-coordinate KD-tree match, not a guaranteed nearest place by great-circle distance. Its country/admin labels describe that selected record. Timezone lookup uses the original input against an independent polygon index. Distance is spherical haversine separation with radius 6,371.0088 km, not a travel route or a geographic boundary test. These chosen coordinates do not measure population accuracy or confidence. Bundled data retain their underlying licenses; reverse-geocoder is an unmodified, separately installed dependency.

## Coverage and validation

All **7/7** public logical surfaces are demonstrated: `reverse_geocode`, `nearest_city`, `country_code`, `admin1`, `admin2`, `timezone` and `distance_km`. [Coverage evidence](coverage.json) separates the actual released runtime from the compatible current source interface. Current changes add example/agent metadata; current source was reviewed, not separately executed.

The saved default and detached radius-500 experiment each pass 20 code cells, nine visible SQL cells, nine SQL invariants, the scalar/STRUCT equality check, and curve monotonicity/denominator checks. Original analytical tables remain unchanged. Three runner guards preserve successful outputs; seven invalid parameter cases are rejected. Every changed chart was inspected. A notebook-only Python 3.13 setup with five libraries already imported preserved their versions and passed `pip check`; hosted Colab itself was not manually tested. Existing green source CI at the unchanged worker revision was reused without worker builds or full-suite reruns.

The shared [standard](https://github.com/Query-farm/vgi-example-notebooks/blob/main/docs/standard.md), presentation tools and runner live in [vgi-example-notebooks](https://github.com/Query-farm/vgi-example-notebooks). Published outputs use content-addressed SVG assets for GitHub; interactive reruns produce native SVG. The [worker repository](https://github.com/Query-farm/vgi-geocode) and Colab links also appear at the notebook's top.
