"""Phrase-level transcript of a WAV via Sarvam STT (saarika, <=30 s per call).

Sarvam's sync endpoint returns one timestamp per request, so chunk size IS the
timing precision. Two modes:

  phrases  python -I sarvam_stt.py phrases <in.wav> <out.tsv>
           Cut at pause midpoints (-38 dB / 0.25 s), 4-25 s chunks; longer
           stretches with no pause are split evenly. TSV: start, end, text.

  fine     python -I sarvam_stt.py fine <in.wav> <out.txt> A-B [A-B ...]
           Re-transcribe given ranges split at pauses >=0.12 s (pieces >=1 s),
           to place a cut inside a phrase.

Needs SARVAM_API in the environment (video-editor/.env). Billed by audio
length (~Rs 30/hour at the time of writing), so fine mode costs little.
Input is resampled to 16 kHz mono automatically.
"""
import json, os, re, subprocess, sys, tempfile, urllib.request, uuid

URL = "https://api.sarvam.ai/speech-to-text"


def stt(path, lang="en-IN", model="saarika:v2.5"):
    bnd = uuid.uuid4().hex
    body = b""
    for k, v in (("model", model), ("language_code", lang)):
        body += f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{bnd}\r\nContent-Disposition: form-data; name="file"; filename="s.wav"\r\n'
             "Content-Type: audio/wav\r\n\r\n").encode() + open(path, "rb").read() + f"\r\n--{bnd}--\r\n".encode()
    req = urllib.request.Request(URL, data=body, headers={
        "api-subscription-key": os.environ["SARVAM_API"],
        "Content-Type": f"multipart/form-data; boundary={bnd}"})
    return json.load(urllib.request.urlopen(req, timeout=60))["transcript"]


def cut(src, a, b, dst):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                    "-i", src, "-ac", "1", "-ar", "16000", dst], check=True)


def pauses(path, db, dur):
    log = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                          f"silencedetect=n={db}dB:d={dur}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    en = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    return sorted((s + e) / 2 for s, e in zip(st, en))


def length(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                          "format=duration", "-of", "csv=p=0", path]))


def phrases(wav, out):
    dur = length(wav)
    cuts, s = [0.0], 0.0
    for m in pauses(wav, -38, 0.25):
        if 0 < m < dur and m - s >= 4:
            cuts.append(m); s = m
    cuts.append(dur)
    segs = []
    for a, b in zip(cuts, cuts[1:]):
        n = int((b - a) // 20) + 1 if b - a > 25 else 1
        segs += [(a + (b - a) * i / n, a + (b - a) * (i + 1) / n) for i in range(n)]
    with tempfile.TemporaryDirectory() as td, open(out, "w") as f:
        p = os.path.join(td, "p.wav")
        for a, b in segs:
            cut(wav, a, b, p)
            f.write(f"{a:.3f}\t{b:.3f}\t{stt(p)}\n"); f.flush()
    print(f"{len(segs)} segments, max {max(b - a for a, b in segs):.1f} s -> {out}")


def fine(wav, out, ranges):
    with tempfile.TemporaryDirectory() as td, open(out, "w") as f:
        r, p = os.path.join(td, "r.wav"), os.path.join(td, "p.wav")
        for rg in ranges:
            a, b = map(float, rg.split("-"))
            cut(wav, a, b, r)
            cuts = [0.0]
            for m in pauses(r, -38, 0.12):
                if 0.3 < m < b - a - 0.3 and m - cuts[-1] >= 1.0:
                    cuts.append(m)
            cuts.append(b - a)
            f.write(f"## {a}-{b}\n")
            for x, y in zip(cuts, cuts[1:]):
                cut(r, x, y, p)
                f.write(f"  [{a + x:8.2f}-{a + y:8.2f}] {stt(p)}\n"); f.flush()
    print(f"{len(ranges)} ranges -> {out}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "phrases" and len(sys.argv) == 4:
        phrases(sys.argv[2], sys.argv[3])
    elif mode == "fine" and len(sys.argv) >= 5:
        fine(sys.argv[2], sys.argv[3], sys.argv[4:])
    else:
        sys.exit(__doc__)
