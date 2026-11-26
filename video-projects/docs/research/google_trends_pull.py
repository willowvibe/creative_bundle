import time, sys
from pytrends.request import TrendReq
pt = TrendReq(hl="en-IN", tz=330, timeout=(10,30))
groups = [
 ["fake paneer","food adulteration","biryani","tiffin","pani puri"],
 ["millets","protein","air fryer","cooking oil","ghee"],
]
for geo in ["IN-TG","IN-AP"]:
  for g in groups:
    for prop in ["", "youtube"]:
      try:
        pt.build_payload(g, timeframe="today 3-m", geo=geo, gprop=prop)
        df = pt.interest_over_time()
        print(geo, prop or "web", df.drop(columns="isPartial").mean().round(1).to_dict(), "last4w", df.drop(columns="isPartial").tail(4).mean().round(1).to_dict())
      except Exception as e: print(geo, prop, g, "ERR", e)
      time.sleep(4)
  try:
    pt.build_payload(["food"], timeframe="today 3-m", geo=geo, gprop="youtube")
    rq = pt.related_queries()["food"]
    print(geo,"RISING youtube 'food':", rq["rising"].head(20).to_dict("records") if rq["rising"] is not None else None)
    print(geo,"TOP youtube 'food':", rq["top"].head(15).to_dict("records") if rq["top"] is not None else None)
  except Exception as e: print("related ERR", e)
  time.sleep(4)
