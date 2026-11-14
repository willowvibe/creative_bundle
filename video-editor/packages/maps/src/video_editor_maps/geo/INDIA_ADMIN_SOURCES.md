# India states and districts: sources and licences

`india_states.json` and `india_districts.json` are built by
`build_india_admin.py` (stdlib only) from the two downloads below, fetched
2026-11-12.

| File | Source | Version | Licence |
|---|---|---|---|
| `india_states.json` (36 units) | Natural Earth 10m Admin 1 – States, Provinces, `ne_10m_admin_1_states_provinces.zip` (naciscdn.org) | 5.1.1 | Public domain |
| `india_districts.json` (735 units) | geoBoundaries gbOpen IND ADM2, `geoBoundaries-IND-ADM2_simplified.geojson` (commit `9469f09`), from Pathways Data Pvt. Ltd. / lgdirectory.gov.in, 2021 | build 2023-12-12 | **ODbL 1.0** |

## Credit line (required wherever district shapes appear)

> District boundaries © geoBoundaries / Pathways Data Pvt. Ltd., ODbL 1.0.

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

- The district set is from 2021: newer districts (e.g. Eluru, carved from West
  Godavari in 2022) are not separate yet.
- District names follow the source's spelling (`Thoothukkudi`); lookups ignore
  case, spaces and punctuation and suggest close spellings on a miss.
- The geoBoundaries state layer was not used: it is from 2011 and has no
  Telangana.

## Rebuild

```bash
uv run python -m video_editor_maps.geo.build_india_admin \
  --states <dir>/ne_10m_admin_1_states_provinces.shp \
  --districts <dir>/geoBoundaries-IND-ADM2_simplified.geojson \
  --out packages/maps/src/video_editor_maps/geo
```
