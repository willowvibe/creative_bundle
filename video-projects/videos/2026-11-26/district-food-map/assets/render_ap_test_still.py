import json, sys
from video_editor_core.designs import load_design
from video_editor_maps.scenes import render_map
out = sys.argv[1]
feats = json.load(open("packages/maps/src/video_editor_maps/geo/india_districts.json"))["features"]
ap = [f["properties"] for f in feats if f["properties"]["state"] == "Andhra Pradesh"]
pal = ["#2E6B5A","#6B5A2E","#5A2E6B","#2E4F6B","#6B2E3F","#4F6B2E","#6B4A2E"]
regions = [{"district": p["name"], "color": pal[i % len(pal)], "label": p.get("name_te") or p["name"]} for i, p in enumerate(ap)]
r = render_map({"id": "s", "type": "map", "props": {"country": "India", "frame": "full", "regions": regions,
    "bbox": [76.6, 12.5, 84.9, 19.3]}}, load_design("night_map"), index=1, duration=6.0, width=1080, height=1920)
force = "*{animation-delay:-60s!important;animation-play-state:paused!important;}"
open(out, "w").write(f"<!doctype html><html><head><meta charset=utf-8><style>html,body{{margin:0;width:1080px;height:1920px;overflow:hidden;background:#000}}{r.css}{force}</style></head><body>{r.markup}</body></html>")
print(len(ap), "AP districts")
