"""Share a drama stage with remote reviewers on the review board (apps/drama-review, a Cloudflare Worker).

usage: drama_board.py publish <stage> <episode>  -> upload the stage; prints the board link
       drama_board.py pull <stage> <episode>     -> review/feedback_<stage>.md; records the gate approval only
                                                    when the owner approved this exact version on the board
       drama_board.py invite "<name>"            -> a personal review link to send that person
       drama_board.py reviewers

Items carry the same ids and hashes as the drama_review gate, so a comment always points at one line, take, frame
or clip, and a regenerated item shows its old comments as "on an earlier version". Needs DRAMA_REVIEW_URL and
DRAMA_REVIEW_KEY (the owner key) in video-editor/.env.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drama_review import STAGES, _digest, approve, lines, stage_files  # noqa: E402

ENV = Path(__file__).resolve().parents[1] / ".env"
KINDS = {".png": "image", ".jpg": "image", ".mp4": "video", ".wav": "audio", ".mp3": "audio"}
TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}


def _config() -> tuple[str, str]:
    env = dict(os.environ)
    if ENV.exists():
        for raw in ENV.read_text().splitlines():
            key, sep, value = raw.partition("=")
            if sep and not raw.lstrip().startswith("#"):
                env.setdefault(key.strip(), value.strip().strip("'\""))
    url, key = env.get("DRAMA_REVIEW_URL", ""), env.get("DRAMA_REVIEW_KEY", "")
    if not url or not key:
        raise SystemExit("DRAMA_REVIEW_URL and DRAMA_REVIEW_KEY must be set in video-editor/.env")
    return url.rstrip("/"), key


def _call(
    path: str,
    method: str = "GET",
    body: bytes | dict | None = None,
    ctype: str = "application/json",
) -> dict:
    url, key = _config()
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(
        f"{url}{path}",
        data=data,
        method=method,
        headers={"x-review-key": key, "content-type": ctype, "user-agent": "drama-board/1"},
    )
    for attempt in range(4):  # uploads of many files hit the odd dropped TLS connection
        try:
            with urllib.request.urlopen(req, timeout=300) as res:
                return json.loads(res.read() or b"{}")
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def episode_id(episode: Path) -> str:
    """videos/2026-10-30/mantra-rahasyam-ep1 -> 2026-10-30-mantra-rahasyam-ep1"""
    return f"{episode.parent.name}-{episode.name}"


def _title(episode: Path) -> str:
    spec = episode / "shots.json"
    if spec.exists():
        return json.loads(spec.read_text()).get("title", episode.name)
    script = episode / "script.md"
    if script.exists():
        first = script.read_text().splitlines()[0]
        return first.lstrip("# ").strip() or episode.name
    return episode.name


def items(episode: Path, stage: str) -> list[dict]:
    """What reviewers see for a stage: one item per gate entry, with the gate's id and hash."""
    digest = _digest(episode, stage)
    missing = [k for k, v in digest.items() if v == "missing"]
    if missing:
        raise SystemExit(f"cannot publish {stage}, missing: {', '.join(missing)}")
    if stage == "script":
        return [
            {
                "id": "script.md",
                "hash": digest["script.md"],
                "kind": "text",
                "title": "Script",
                "markdown": (episode / "script.md").read_text(),
                "file": None,
            }
        ]
    if stage in ("dialogue", "voices"):
        out = []
        for x in lines(episode):
            f = episode / x["audio"]
            out.append(
                {
                    "id": x["id"],
                    "hash": digest[x["id"]],
                    "kind": "audio" if stage == "voices" else "text",
                    "title": x["id"],
                    "who": x["who"].title(),
                    "te": x["te"],
                    "en": x["en"],
                    "emotion": x["emotion"],
                    "file": f if stage == "voices" else None,
                }
            )
        return out
    # Optional review/captions.json: {"refs/abhinav_a.png": "Abhinav, option A"} shown under each image or clip.
    cap_file = episode / "review" / "captions.json"
    captions = json.loads(cap_file.read_text()) if cap_file.exists() else {}
    out = []
    for f in stage_files(episode, stage):
        rel = str(f.relative_to(episode))
        out.append(
            {
                "id": rel,
                "hash": digest[rel],
                "kind": KINDS.get(f.suffix, "file"),
                "title": rel,
                "en": captions.get(rel, ""),
                "file": f,
            }
        )
    return out


def publish(episode: Path, stage: str, note: str = "") -> str:
    ep = episode_id(episode)
    entries = items(episode, stage)
    cache_file = episode / "review" / ".published.json"
    cache = set(json.loads(cache_file.read_text())) if cache_file.exists() else set()
    for it in entries:
        f = it.pop("file")
        if f is None:
            continue
        key = f"{ep}/{stage}/files/{it['hash'][:16]}{f.suffix}"
        if key not in cache:  # each version is uploaded once
            _call(
                f"/api/admin/put?key={urllib.parse.quote(key)}&type={TYPES.get(f.suffix, 'application/octet-stream')}",
                "PUT",
                f.read_bytes(),
                TYPES.get(f.suffix, "application/octet-stream"),
            )
            cache.add(key)
            cache_file.parent.mkdir(exist_ok=True)
            cache_file.write_text(
                json.dumps(sorted(cache))
            )  # a rerun after a failure skips what already landed
            print(f"  uploaded {it['id']}")
        it["media"] = key
    manifest_hash = hashlib.sha256(
        json.dumps([[i["id"], i["hash"]] for i in entries]).encode()
    ).hexdigest()
    manifest = {
        "episode": ep,
        "title": _title(episode),
        "stage": stage,
        "note": note,
        "items": entries,
        "manifest_hash": manifest_hash,
        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _call(
        f"/api/admin/put?key={urllib.parse.quote(f'{ep}/{stage}/manifest.json')}&type=application/json",
        "PUT",
        json.dumps(manifest, ensure_ascii=False).encode(),
    )
    published = _call("/api/episodes")
    index = [e for e in published if e["id"] != ep]
    old = next((e for e in published if e["id"] == ep), {"stages": []})
    stages = sorted(set(old["stages"]) | {stage}, key=STAGES.index)
    index.insert(0, {"id": ep, "title": manifest["title"], "stages": stages})
    _call(
        "/api/admin/put?key=episodes.json&type=application/json",
        "PUT",
        json.dumps(index, ensure_ascii=False).encode(),
    )
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(sorted(cache)))
    url, _ = _config()
    return f"{url}/#{ep}/{stage}"


def pull(episode: Path, stage: str) -> Path:
    """Write every reviewer's feedback to review/feedback_<stage>.md; apply the owner's board approval to the gate."""
    data = _call(f"/api/stage/{episode_id(episode)}/{stage}")
    manifest, feedback = data["manifest"], data["feedback"]
    rows = [f"# Board feedback: {stage}\n", f"Published {manifest['published_at']}\n"]
    for i, it in enumerate(manifest["items"], 1):
        notes = [f for f in feedback if f["item"] == it["id"]]
        latest = {
            f["name"]: f for f in notes if f["item_hash"] == it["hash"] and f["verdict"] != "note"
        }
        status = (
            "needs change"
            if any(f["verdict"] == "change" for f in latest.values())
            else "good"
            if latest
            else "not reviewed"
        )
        rows.append(f"\n## {i}. {it['id']}{' — ' + it['who'] if it.get('who') else ''}: {status}\n")
        for f in notes:
            old = " (earlier version)" if f["item_hash"] != it["hash"] else ""
            rows.append(f"- **{f['name']}** {f['verdict']}{old}: {f['comment'] or '—'}")
    out = episode / "review" / f"feedback_{stage}.md"
    out.write_text("\n".join(rows) + "\n")
    signed = [s for s in data["signoffs"] if s["manifest_hash"] == manifest["manifest_hash"]]
    current = _digest(episode, stage)
    unchanged = all(current.get(it["id"]) == it["hash"] for it in manifest["items"]) and len(
        current
    ) == len(manifest["items"])
    if signed and unchanged:
        approve(episode, stage)  # the owner approved this exact version on the board
        print(
            f"{stage}: approved on the board by {signed[-1]['name']} at {signed[-1]['created_at']}; gate recorded"
        )
    elif signed:
        print(
            f"{stage}: the board approval is for an older version; publish again for a new approval"
        )
    else:
        print(f"{stage}: not approved on the board yet")
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] in ("publish", "pull") and argv[1] in STAGES:
        episode = Path(argv[2]).resolve()
        if argv[0] == "publish":
            print(publish(episode, argv[1], " ".join(argv[3:])))
        else:
            print(pull(episode, argv[1]))
        return 0
    if len(argv) == 2 and argv[0] == "invite":
        r = _call("/api/admin/reviewers", "POST", {"name": argv[1]})
        url, _ = _config()
        print(f"{r['name']}: {url}/?k={r['token']}")
        return 0
    if argv == ["reviewers"]:
        for r in _call("/api/admin/reviewers"):
            print(f"{r['name']} ({r['role']}) since {r['created_at']}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
