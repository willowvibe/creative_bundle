---
name: deveekitchen-slate-trend-scan
description: "DeveeKitchen next videos chosen from a 2026-11-26 YouTube outlier + Google Trends scan; district food map Shorts in progress (Andhra done, Telangana next)"
metadata:
  node_type: memory
  type: project
  originSessionId: cbeb8302-ec2f-4327-a2a0-7c4e91e9bb2d
  modified: 2026-11-26T16:57:56.745Z
---

On 2026-11-26 the DeveeKitchen slate after the tiffin video was re-chosen from data (YouTube API scan ranked by views ÷ subscribers, plus Google Trends TG/AP): (1) district food map Shorts of AP/Telangana; (2) "How is it made?" series (Tirupati laddu / pootharekulu); (3) 2D animated nostalgia story, pending the owner's call on stylised AI animation vs the no-AI-food rule. Oil video shelved (no search demand), chilli deferred. Plan: Shorts first; a long version only if a Short gets ≥20K views in 7 days or ≥100 district comments.

State at end of 2026-11-26: Andhra Short v9 rendered (`video-projects/videos/2026-11-26/district-food-map/`, 50 s, QA all pass, ≈$0.31 total), `publish.yaml` validated, not uploaded — the `deveekitchen` account is not set up in social-engine (owner must `social auth` + `social login`). Telangana Short next; Telangana district food is barely documented online, so the owner supplies those pairings (research.md lists the gaps).

Map data: AP's 28 current districts are in video-editor's `india_districts.json` (OSM via Nominatim; Overpass returns 406 from this machine; refresh script `geo/overrides/fetch_osm_districts.py`).

**Why:** the owner wants picks backed by what is currently breaking out, not only explainers.
**How to apply:** for new channel ideas, rerun `video-projects/docs/research/yt_trend_scan.py` (key in `research-engine/.env`) and rank by outlier ratio before proposing. Build map Shorts with `video-editor/docs/MAP_SHORTS.md`. Related: [[explainer-playbook-order]], [[deveekitchen-shorts-style]].
