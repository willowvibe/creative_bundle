# India states and districts: sources and licences

`india_states.json` and `india_districts.json` are built by
`build_india_admin.py` (stdlib only) from the downloads below. The Natural Earth
and geoBoundaries data were fetched 2026-11-12; the Andhra Pradesh district
override was fetched from OpenStreetMap on 2026-11-26.

| File | Source | Version | Licence |
|---|---|---|---|
| `india_states.json` (36 units) | Natural Earth 10m Admin 1 – States, Provinces, `ne_10m_admin_1_states_provinces.zip` (naciscdn.org) | 5.1.1 | Public domain |
| `india_districts.json` (all states except Andhra Pradesh) | geoBoundaries gbOpen IND ADM2, `geoBoundaries-IND-ADM2_simplified.geojson` (commit `9469f09`), from Pathways Data Pvt. Ltd. / lgdirectory.gov.in, 2021 | build 2023-12-12 | **ODbL 1.0** |
| Andhra Pradesh districts (28 units, in `india_districts.json`) | OpenStreetMap via Nominatim, `overrides/andhra_pradesh_districts_osm_2026-11-26.geojson` | fetched 2026-11-26 | **ODbL 1.0**, © OpenStreetMap contributors |

## Credit line (required wherever district shapes appear)

> District boundaries © geoBoundaries / Pathways Data Pvt. Ltd. and
> © OpenStreetMap contributors, ODbL 1.0.

`india_districts.json` is a derived database and stays under ODbL 1.0. Videos
that show district shapes are Produced Works: add the credit line to the
description. States (public domain) need no credit.

## The disputed north

Natural Earth draws admin-1 *de facto*: Jammu and Kashmir and Ladakh stop at
the Line of Control, while the map plate uses India's own outline
(`worldview_ind.json`). Those two units and their districts carry
`"disputed": true`, and `find_admin` refuses them, so a scene can never fill a
border that contradicts the base map.

## Known gaps

- The geoBoundaries district set is from 2021, so districts created after it
  are only separate where an override supplies them (Andhra Pradesh is current
  as of 2026).
- District names follow the source's spelling (`Thoothukkudi`); lookups ignore
  case, spaces and punctuation and suggest close spellings on a miss.
- The geoBoundaries state layer was not used: it is from 2011 and has no
  Telangana.

## Rebuild

```bash
uv run python -m video_editor_maps.geo.build_india_admin \
  --states <dir>/ne_10m_admin_1_states_provinces.shp \
  --districts <dir>/geoBoundaries-IND-ADM2_simplified.geojson \
  --override "Andhra Pradesh=packages/maps/src/video_editor_maps/geo/overrides/andhra_pradesh_districts_osm_2026-11-26.geojson" \
  --out packages/maps/src/video_editor_maps/geo
```

When the original shapefile is not on disk, patch the committed district file
in place:

```bash
uv run python -m video_editor_maps.geo.build_india_admin \
  --patch-existing \
  --override "Andhra Pradesh=packages/maps/src/video_editor_maps/geo/overrides/andhra_pradesh_districts_osm_2026-11-26.geojson" \
  --out packages/maps/src/video_editor_maps/geo
```

## Refreshing the Andhra Pradesh override

The override was fetched from OpenStreetMap through Nominatim: Overpass was
unreachable (HTTP 406) on 2026-11-26, so the ad-hoc fetch is now saved as
`overrides/fetch_osm_districts.py` (stdlib only; it sleeps between requests to
respect Nominatim's usage policy). Re-fetch the current districts, then patch
the committed file:

```bash
python3 packages/maps/src/video_editor_maps/geo/overrides/fetch_osm_districts.py \
  --state "Andhra Pradesh" \
  --out packages/maps/src/video_editor_maps/geo/overrides/andhra_pradesh_districts_osm_YYYY-MM-DD.geojson

uv run python -m video_editor_maps.geo.build_india_admin \
  --patch-existing \
  --override "Andhra Pradesh=packages/maps/src/video_editor_maps/geo/overrides/andhra_pradesh_districts_osm_YYYY-MM-DD.geojson" \
  --out packages/maps/src/video_editor_maps/geo
```

Use `--names` or `--names-file` to override the built-in Andhra Pradesh list,
and `--check` to report overlapping districts when shapely is installed.
Replace the date in both paths and the table above after refreshing.
