"""Sound design for the episode: fetch CC0 effects, place them on the cut, render one bed.

usage: sfx.py list | fetch [cue ...] | place | all

Sources, in this order:
  * **Freesound** CC0 only (`license:"Creative Commons 0"`), HQ preview mp3. CC0 is public domain, so nothing
    has to be credited and the series can stay commercial; the licence of every downloaded file is re-read from
    its own sound record and stored in `audio/sfx/cues.json` anyway.
  * **earshot**'s code-synthesised bank (`~/tools/earshot`, local, no models) for the designed hits, shimmers
    and swells a stock library does not carry. Rendered at the level they will be mixed at.

The cue table is the whole design: `t` is absolute seconds on the cut (the values come from `assemble.py plan`),
`dur` is how much of the sound is used, `gain` is dB relative to the source, and `loop` repeats a short take.
Keeping every sound under the voice is the point — the brief is light SFX, not a Foley pass.
"""

import json
import subprocess
import sys
from pathlib import Path

import httpx

P = Path(__file__).resolve().parents[1]
OUT = P / "audio" / "sfx"
NOTES = OUT / "cues.json"
BED = P / "edit" / "sfx_bed.wav"
ENV = dict(l.split("=", 1) for l in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
           if "=" in l and not l.startswith("#"))
FS_KEY = ENV["FREESOUND_API_KEY"].strip().strip("'\"")
EARSHOT = Path.home() / "tools" / "earshot" / ".venv" / "bin" / "earshot"

# (id, source, ref, t, dur, level, fade in, fade out, loop)
#   source "fs"       ref = Freesound id
#   source "earshot"  ref = bank id
#   level = ("rms", dBFS) for a bed that must sit under dialogue, ("peak", dBFS) for a one-shot that must land.
#           It is measured on the used segment and corrected there — stock levels differ by up to 35 dB between
#           recordings, so a fixed dB offset per cue would make half of them inaudible and the rest overpowering.
CUES = [
    # --- scene 1-2: the home, and the morning that ends it
    dict(id="home_birds",   src="fs", ref=563547, t=0.0,   dur=33.0, level=("rms", -36), fi=2.0, fo=3.0),
    dict(id="plate_set",    src="earshot", ref="knock/desk", t=8.0, dur=0.6, level=("peak", -30), fi=0.0, fo=0.3),
    dict(id="scooter_go",   src="fs", ref=448131, t=29.0,  dur=4.2,  level=("peak", -16), fi=0.1, fo=1.2),
    dict(id="street_bed",   src="fs", ref=689711, t=36.6,  dur=15.6, level=("rms", -36), fi=2.0, fo=2.5),
    dict(id="crash",        src="fs", ref=807437, t=41.80, dur=1.3,  level=("peak", -8), fi=0.0, fo=0.6),
    dict(id="crash_tail",   src="fs", ref=132872, t=42.2,  dur=7.0,  level=("rms", -34), fi=0.3, fo=3.0),

    # --- scene 3-5: the crowd, the call, the ride to the hospital
    dict(id="crowd_walla",  src="fs", ref=None, query="street crowd talking", t=57.0, dur=10.0,
         level=("rms", -36), fi=1.5, fo=2.0),
    dict(id="siren_far",    src="fs", ref=754991, t=67.3,  dur=10.0, level=("rms", -38), fi=2.5, fo=3.0),
    dict(id="phone_ring",   src="fs", ref=132740, t=81.5,  dur=7.0,  level=("peak", -18), fi=0.0, fo=0.5),
    dict(id="temple_bell",  src="fs", ref=668647, t=101.6, dur=6.0,  level=("peak", -20), fi=0.0, fo=3.0),

    # --- scene 7-9: the hospital, the long night
    # The beep source ends *inside* a beep, so looping the raw file fires a premature beat every 3.323 s (a pair
    # 0.3 s apart where the rest are ~0.95 s). `loop_span` is one whole cycle: the source's beeps land at
    # 0.26 / 1.23 / 2.16 / 3.12 s, so 0.24 + 2.86 starts and ends 20 ms inside the silence either side of a beat,
    # and the wrap interval equals the source's own 0.96 s instead of adding 40 ms every cycle. The level rises
    # into the crisis instead of the bed fading out and a second cue restarting it 0.1 s later.
    dict(id="hosp_atmo",    src="fs", ref=201637, t=107.7, dur=42.0, level=("rms", -38), fi=2.0, fo=3.0),
    dict(id="monitor",      src="fs", ref=668978, t=107.7, dur=47.14, level=("peak", -30), fi=1.5, fo=0.4,
         loop=True, loop_span=(0.24, 2.86), ramp=(147.7, 154.84, 0.0, 6.0)),
    dict(id="door",         src="fs", ref=457358, t=126.9, dur=1.6,  level=("peak", -24), fi=0.0, fo=0.4),
    dict(id="prayer_bell",  src="fs", ref=668646, t=132.0, dur=8.0,  level=("peak", -26), fi=0.0, fo=4.0),
    dict(id="flatline",     src="fs", ref=581549, t=154.84, dur=6.8, level=("peak", -18), fi=0.0, fo=0.5),
    dict(id="defib",        src="fs", ref=321800, t=161.44, dur=3.4, level=("peak", -11), fi=0.0, fo=0.4),

    # --- scene 10-12: the flashback, the mantra, the doothas
    dict(id="memory",       src="earshot", ref="reverse_swell/ring", t=166.5, dur=1.8, level=("peak", -22),
         fi=0.0, fo=0.4),
    dict(id="divine_light", src="earshot", ref="pro_shimmer/m4", t=195.3, dur=3.2, level=("peak", -22),
         fi=0.6, fo=1.5),
    dict(id="dootha",       src="fs", ref=132872, t=217.62, dur=8.0, level=("rms", -34), fi=0.4, fo=3.0),
    dict(id="chains",       src="fs", ref=401621, t=221.4, dur=5.0, level=("peak", -22), fi=0.3, fo=1.5),
    dict(id="shiva_hit",    src="earshot", ref="impact/deep", t=234.64, dur=4.2, level=("peak", -12),
         fi=0.0, fo=2.0),
    dict(id="shiva_shine",  src="earshot", ref="pro_shimmer/m0", t=236.2, dur=3.6, level=("peak", -22),
         fi=0.5, fo=1.8),
    dict(id="release",      src="earshot", ref="reverse_swell/ring", t=244.4, dur=2.0, level=("peak", -24),
         fi=0.3, fo=0.8),

    # --- scene 13-16: back in the room
    # v44 is the shot with nothing in it: the room after he is gone, before the beep comes back.
    dict(id="flat_wait",    src="fs", ref=581549, t=247.75, dur=4.75, level=("peak", -26), fi=0.2, fo=0.6),
    dict(id="beep_return",  src="fs", ref=668978, t=252.6, dur=45.0, level=("peak", -28), fi=1.0, fo=4.0,
         loop=True, loop_span=(0.24, 2.86)),
    dict(id="shiva_voice",  src="fs", ref=132872, t=297.4, dur=10.5, level=("rms", -40), fi=2.0, fo=2.5),
]


def note() -> dict:
    return json.loads(NOTES.read_text()) if NOTES.exists() else {}


def fs_client() -> httpx.Client:
    # Freesound answers the slash-less sound path with a 301 to the slashed one; httpx does not follow by default.
    return httpx.Client(base_url="https://freesound.org/apiv2", timeout=60, follow_redirects=True)


def search(client: httpx.Client, query: str) -> dict:
    """The best CC0 hit for a description. Freesound's default ranking is by relevance already."""
    r = client.get("/search/text/", params={
        "query": query, "filter": 'license:"Creative Commons 0"', "page_size": 5,
        "fields": "id,name,duration,previews,username,license", "token": FS_KEY})
    r.raise_for_status()
    hits = r.json().get("results") or []
    if not hits:
        raise SystemExit(f"no CC0 hit for {query!r}")
    return hits[0]


def fs_fetch(client: httpx.Client, cue: dict) -> dict:
    """Download one cue's preview and convert to 48 kHz stereo. The sound record, not the search filter, is the
    authority on the licence — the filter is a query, the record is the fact."""
    sound = (client.get(f"/sounds/{cue['ref']}/", params={"fields": "id,name,license,username,duration,previews",
                                                         "token": FS_KEY}).json()
             if cue.get("ref") else search(client, cue["query"]))
    lic = sound["license"]
    if "publicdomain/zero" not in lic:
        raise SystemExit(f"{cue['id']}: {sound['name']} is {lic}, not CC0")
    out = OUT / f"{cue['id']}.wav"
    if not out.exists():
        prev = sound["previews"]["preview-hq-mp3"]
        wav = OUT / f"{cue['id']}.mp3"
        wav.write_bytes(httpx.get(prev, timeout=120, follow_redirects=True).content)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ar", "48000", "-ac", "2", str(out)],
                       check=True)
        wav.unlink()
    return {"file": out.name, "name": sound["name"], "licence": lic, "author": sound["username"],
            "url": f"https://freesound.org/s/{sound['id']}/", "source": "freesound"}


def earshot_fetch(cue: dict) -> dict:
    out = OUT / f"{cue['id']}.wav"
    if not out.exists():
        subprocess.run([str(EARSHOT), "sfx", "render", cue["ref"], "-o", str(out), "--dur", f"{cue['dur']:.3f}",
                        "--sr", "48000", "--peak", "-3"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"file": out.name, "name": cue["ref"], "licence": "earshot bank (generated)", "author": "earshot",
            "url": "https://github.com/Leonxlnx/earshot", "source": "earshot"}


def trim_assets() -> None:
    """Cut each source down to the part the cue table uses (+2 s of slack): the street bed alone was 60 MB for the
    15.6 s of it that reach the mix. Re-running `fetch` restores the full file."""
    for cue in CUES:
        f = OUT / f"{cue['id']}.wav"
        if not f.exists():
            continue
        keep = cue["dur"] + 2.0
        if float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(f)], capture_output=True, text=True).stdout) <= keep:
            continue
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(f), "-t", f"{keep:.3f}",
                        "-ar", "48000", "-ac", "2", str(f.with_suffix(".cut.wav"))], check=True)
        f.with_suffix(".cut.wav").replace(f)
        print("trimmed", cue["id"], flush=True)


def fetch(only: set[str] | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    notes = note()
    with fs_client() as c:
        for cue in CUES:
            if only and cue["id"] not in only:
                continue
            notes[cue["id"]] = fs_fetch(c, cue) if cue["src"] == "fs" else earshot_fetch(cue)
            print("fetched", cue["id"], "->", notes[cue["id"]]["name"][:50], flush=True)
    NOTES.write_text(json.dumps(notes, indent=1) + "\n")


def loop_file(cue: dict) -> Path | None:
    """A seamless loop for a source that does not end where it begins. `beeps for heart monitor` stops inside a
    beep, so repeating the raw file lands a beat 0.3 s after the last one instead of ~0.95 s; `loop_span` is
    (start, length) of one whole cycle. The span is in the file name, so retuning it re-cuts instead of silently
    reusing the last cut."""
    if not cue.get("loop_span"):
        return None
    start, length = cue["loop_span"]
    src = OUT / f"{cue['id']}.wav"
    out = OUT / f"{cue['id']}.loop-{start:.2f}-{length:.2f}.wav"
    if not out.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
                        "-i", str(src), "-ar", "48000", "-ac", "2", str(out)], check=True)
    return out


def level_of(cue: dict, path: Path) -> float:
    """Measure the used segment, not the file: a crash with 2 s of silence before it has a calm RMS and a
    deafening peak, and the cue asks for whichever one it names."""
    pre = ["-stream_loop", "-1"] if cue.get("loop") else []
    dur = min(cue["dur"], cue["loop_span"][1]) if cue.get("loop_span") else cue["dur"]
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *pre, "-i", str(path),
                          "-t", f"{dur:.3f}", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    want = "mean_volume" if cue["level"][0] == "rms" else "max_volume"
    line = next(l for l in out.splitlines() if want in l)
    return float(line.rsplit(":", 1)[1].split("dB")[0])


def ramp_filter(cue: dict) -> str:
    """An optional level envelope, in absolute seconds on the cut: (from, to, dB at `from`, dB at `to`). Used where
    a cue has to build rather than restart."""
    a, b, d0, d1 = cue["ramp"]
    la, lb = a - cue["t"], b - cue["t"]
    g0, g1 = 10 ** (d0 / 20), 10 ** (d1 / 20)
    expr = (f"if(lt(t,{la:.3f}),{g0:.4f},if(lt(t,{lb:.3f}),"
            f"{g0:.4f}*pow(10,({d1} - {d0})/20*(t - {la:.3f})/{max(lb - la, 0.001):.3f}),{g1:.4f}))")
    return f"volume='{expr}':eval=frame"


def place() -> Path:
    """One ffmpeg pass: every cue trimmed, faded, corrected to its target level and delayed to its time."""
    ins, parts, labels = [], [], []
    for i, cue in enumerate(CUES):
        f = OUT / f"{cue['id']}.wav"
        if not f.exists():
            raise SystemExit(f"{cue['id']}: run `sfx.py fetch` first")
        f = loop_file(cue) or f
        gain = cue["level"][1] - level_of(cue, f)
        pre = ["-stream_loop", "-1"] if cue.get("loop") else []
        ins += [*pre, "-i", str(f)]
        chain = [f"[{i}:a]atrim=0:{cue['dur']:.3f},asetpts=PTS-STARTPTS",
                 f"afade=t=in:st=0:d={cue['fi']:.2f}" if cue["fi"] else None,
                 f"afade=t=out:st={max(0.0, cue['dur'] - cue['fo']):.3f}:d={cue['fo']:.2f}" if cue["fo"] else None,
                 f"volume={gain:.1f}dB",
                 ramp_filter(cue) if cue.get("ramp") else None,
                 f"adelay={int(cue['t'] * 1000)}:all=1"]
        parts.append(",".join(x for x in chain if x) + f"[s{i}]")
        labels.append(f"[s{i}]")
        print(f"  {cue['id']:14} {gain:+6.1f}dB -> {cue['level'][0]} {cue['level'][1]:.0f}", flush=True)
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[out]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(parts),
                    "-map", "[out]", "-ar", "48000", "-ac", "2", "-t", "319.5", str(BED)], check=True)
    return BED


def show() -> None:
    notes = note()
    for c in CUES:
        n = notes.get(c["id"], {})
        print(f"{c['t']:7.2f} {c['dur']:6.2f}s {c['level'][0]:4} {c['level'][1]:4.0f}  {c['id']:14} "
              f"{(n.get('name') or c.get('query') or c['ref'])[:46]}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "list"
    if what == "list":
        show()
    if what in ("fetch", "all"):
        fetch(set(sys.argv[2:]) or None)
    if what == "trim":
        trim_assets()
    if what in ("place", "all"):
        print("bed", place(), flush=True)
