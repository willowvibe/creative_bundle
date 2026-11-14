"""One-off: the bloody accident aftermath (v09) on OpenAI's image models via PoYo, for comparison with Qwen.

usage: v09_gpt_test.py    -> edit/v09_test/<model>.png
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from consistency_test import upload  # noqa: E402
from frames import SPEC, prompt_for  # noqa: E402
from refs import collect, submit  # noqa: E402

P = Path(__file__).resolve().parents[1]
OUT = P / "edit" / "v09_test"
OUT.mkdir(exist_ok=True)
v = next(x for x in SPEC["visuals"] if x["id"] == "v09")
prompt, refs = prompt_for(v)
urls = [upload(r) for r in refs]
jobs = {
    "gpt-image-1-edit-official": {"prompt": prompt, "image_urls": urls, "size": "1536x1024", "n": 1},
    "gpt-image-1.5-edit-official": {"prompt": prompt, "image_urls": urls, "size": "1536x1024", "n": 1},
    "gpt-image-2.5-flare": {"prompt": prompt, "image_urls": urls, "size": "16:9", "resolution": "2K", "quality": "high",
                            "output_format": "png"},
}
tasks = {}
for model, inp in jobs.items():
    out = OUT / f"{model}.png"
    if not out.exists():
        try:
            tasks[out] = submit(model, inp)
            print("submitted", model, flush=True)
        except Exception as e:  # a refusal at submit time
            print("REFUSED at submit", model, str(e)[:200], flush=True)
collect(tasks)
