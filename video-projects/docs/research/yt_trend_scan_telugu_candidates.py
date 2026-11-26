"""Scan YouTube for top recent videos in food + sister niches. Writes results2.json."""
import json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

KEY = os.environ["YOUTUBE_API_KEY"]
API = "https://www.googleapis.com/youtube/v3/"
AFTER = sys.argv[1] if len(sys.argv) > 1 else "2026-08-01T00:00:00Z"
OUT = os.path.join(os.path.dirname(__file__), "results2.json")

# (niche, query, relevanceLanguage)
QUERIES = [
    ("v-factory","ఎలా తయారు చేస్తారు factory","te"),
    ("v-district","famous food of each district andhra telangana","te"),
    ("v-airfryer","air fryer telugu","te"),
    ("v-protein","protein food telugu","te"),
    ("v-economics","ఎంత సంపాదిస్తారు business","te"),
    ("v-ghibli","telugu animation story food","te"),
]
OLD = [
    ("te-food", "telugu food", "te"),
    ("te-food", "తెలుగు వంటలు", "te"),
    ("te-street", "hyderabad street food", "te"),
    ("te-street", "street food telugu", "te"),
    ("te-business", "business telugu food stall earning", "te"),
    ("te-business", "how much earn per day telugu", "te"),
    ("te-explainer", "food facts telugu", "te"),
    ("te-explainer", "health food myths telugu", "te"),
    ("te-explainer", "packaged food reality telugu", "te"),
    ("te-biryani", "biryani hyderabad", "te"),
    ("te-village", "village cooking telugu", "te"),
    ("te-hacks", "kitchen tips telugu", "te"),
    ("te-review", "restaurant review telugu", "te"),
    ("te-grocery", "price comparison telugu", "te"),
    ("in-business", "street food business profit india", "en"),
    ("in-business", "how much does a food stall earn", "en"),
    ("in-explainer", "food explained india", "en"),
    ("in-explainer", "how it's made india factory food", "en"),
    ("in-history", "history of indian food", "en"),
    ("in-adulteration", "fake food adulteration test india", "en"),
    ("in-street", "indian street food", "en"),
    ("in-challenge", "food challenge india", "en"),
    ("global-explainer", "why food is so expensive explained", "en"),
    ("global-business", "how this restaurant makes money", "en"),
    ("global-map", "food map animation", "en"),
    ("global-test", "cheap vs expensive food", "en"),
    ("ta-food", "tamil food business", "ta"),
    ("hi-food", "food business kaise kamaye street", "hi"),
]


def get(path, **params):
    params["key"] = KEY
    url = API + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def iso_dur(s):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def main():
    hits = {}
    for niche, q, lang in QUERIES:
        try:
            res = get("search", part="snippet", q=q, type="video", order="viewCount",
                      publishedAfter=AFTER, regionCode="IN", relevanceLanguage=lang, maxResults=25)
        except Exception as e:  # quota or network
            print("search failed", q, e, file=sys.stderr)
            continue
        for it in res.get("items", []):
            vid = it["id"]["videoId"]
            hits.setdefault(vid, {"niches": set(), "queries": set()})
            hits[vid]["niches"].add(niche)
            hits[vid]["queries"].add(q)
    ids = list(hits)
    videos, chan_ids = [], set()
    for i in range(0, len(ids), 50):
        res = get("videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50]))
        for v in res.get("items", []):
            st, sn = v["statistics"], v["snippet"]
            pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
            days = max((datetime.now(timezone.utc) - pub).days, 1)
            views = int(st.get("viewCount", 0))
            videos.append({
                "id": v["id"], "title": sn["title"], "channel": sn["channelTitle"],
                "channelId": sn["channelId"], "published": sn["publishedAt"][:10],
                "views": views, "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "dur_s": iso_dur(v["contentDetails"].get("duration")),
                "views_per_day": round(views / days),
                "lang": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
                "niches": sorted(hits[v["id"]]["niches"]),
            })
            chan_ids.add(sn["channelId"])
    chan_ids = list(chan_ids)
    subs = {}
    for i in range(0, len(chan_ids), 50):
        res = get("channels", part="statistics", id=",".join(chan_ids[i:i + 50]))
        for c in res.get("items", []):
            subs[c["id"]] = int(c["statistics"].get("subscriberCount", 0) or 0)
    for v in videos:
        s = subs.get(v["channelId"], 0)
        v["subs"] = s
        v["outlier"] = round(v["views"] / s, 2) if s else None
    videos.sort(key=lambda v: -v["views"])
    json.dump(videos, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(len(videos), "videos ->", OUT)


if __name__ == "__main__":
    main()
