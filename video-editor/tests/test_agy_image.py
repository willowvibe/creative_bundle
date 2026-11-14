"""AGYImageProvider tests. Every test uses a fake `agy` script; no quota is used."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
from pathlib import Path

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.visual.agy_image import (
    DEFAULT_TIMEOUT_SECONDS,
    AGYImageError,
    AGYImageProvider,
)
from video_editor_ffmpeg.run import FFmpegError

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

FAKE_SOURCE = """\
import json, pathlib, re, sys, time
here = pathlib.Path(__file__).parent
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("1.4.8")
    sys.exit(0)
if "models" in argv:
    print(json.dumps({
        "status": "SUCCESS",
        "response": "",
        "command": {"name": "models", "data": {"models": [{"id": "gemini-3.8-flash-low"}]}},
    }))
    sys.exit(0)
(here / "argv.json").write_text(json.dumps(argv))
with (here / "argv.log").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")
prompt = sys.argv[sys.argv.index("-p") + 1]
mode = (here / "mode.txt").read_text().strip()
(here / "cwd.json").write_text(json.dumps({
    "cwd": str(pathlib.Path.cwd()),
    "entries": sorted(p.name for p in pathlib.Path.cwd().iterdir()),
}))
match = re.search(r"Save the image to (.+?) and reply", prompt)
if mode == "sleep":
    time.sleep(10)
if mode == "exit1":
    sys.exit(3)
def png(width, height):
    import struct, zlib
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\\x00" + b"\\xff\\x00\\x00" * width for _ in range(height))
    return (b"\\x89PNG\\r\\n\\x1a\\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
target = pathlib.Path(match.group(1)) if match else pathlib.Path("missing.png")
model = argv[argv.index("--model") + 1] if "--model" in argv else "fake-image"
w = h = 0
if mode not in {"nofile", "quota"} and match:
    if mode == "garbage":
        target.write_bytes(b"<html>nope</html>")
        w, h = 16, 16
    elif mode == "square":
        # An agent that ignored the aspect line.
        target.write_bytes(png(16, 16))
        w, h = 16, 16
    else:
        # Draw at the asked aspect, as a well-behaved agent would.
        aspect = re.search(r"Aspect: (\\d+):(\\d+)", prompt)
        w, h = (int(aspect.group(1)), int(aspect.group(2))) if aspect else (1, 1)
        target.write_bytes(png(w * 4, h * 4))
        w, h = w * 4, h * 4
if mode == "quota":
    print(json.dumps({
        "status": "SUCCESS",
        "response": "429 RESOURCE_EXHAUSTED: image quota exhausted",
        "structured_output": {
            "path": str(target),
            "width": w,
            "height": h,
            "image_model": model,
        },
    }))
elif mode == "schema":
    print(json.dumps({"status": "SUCCESS", "response": "no structured reply"}))
else:
    print(json.dumps({
        "status": "SUCCESS",
        "response": "",
        "structured_output": {
            "path": str(target),
            "width": w,
            "height": h,
            "image_model": model,
        },
    }))
"""


def make_fake_agy(tmp_path: Path, mode: str = "ok") -> Path:
    """Write an executable stand-in for `agy` and return its path."""
    (tmp_path / "fixture.png").write_bytes(PNG_1X1)
    (tmp_path / "mode.txt").write_text(mode)
    script = tmp_path / "agy"
    script.write_text(f"#!{sys.executable}\n{FAKE_SOURCE}")
    script.chmod(0o755)
    return script


def make_request(**overrides: object) -> AssetRequest:
    payload = {
        "asset_id": "img-1",
        "job_id": "job-1",
        "scene_id": "scene-1",
        "asset_type": AssetType.IMAGE,
        "prompt": "a red circle on white",
        "output_ext": "png",
    }
    payload.update(overrides)
    return AssetRequest(**payload)


def make_provider(tmp_path: Path, mode: str = "ok", **options: object) -> AGYImageProvider:
    fake = make_fake_agy(tmp_path, mode)
    profile = tmp_path / "profile"
    auth = profile / ".gemini"
    auth.mkdir(parents=True, exist_ok=True)
    (auth / "oauth_creds.json").write_text("{}")
    # Existing tests exercise the one-shot JSON path; session mode has its own
    # fake and tests below.
    provider_options = {
        "agy_bin": str(fake),
        "session": False,
        "profile_dir": str(profile),
    }
    provider_options.update(options)
    return AGYImageProvider(ProviderConfig(provider_options=provider_options))


@pytest.mark.asyncio
async def test_generate_writes_image(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)
    output = tmp_path / "out.png"

    result = await provider.generate(make_request(), output)

    assert output.exists()
    assert output.stat().st_size > 0
    assert result.asset_type == AssetType.IMAGE
    assert result.metadata["provider"] == "agy_image"
    assert result.metadata["width"] > 0 and result.metadata["height"] > 0


@pytest.mark.asyncio
async def test_relative_output_path_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child resolves the prompt path against its cwd; ours must be absolute."""
    provider = make_provider(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    # The parent's cwd differs from the child's cwd: a relative path embedded
    # in the prompt is resolved twice, once by each process.
    monkeypatch.chdir(tmp_path)

    result = await provider.generate(make_request(), Path("work/out.png"))

    expected = (work / "out.png").resolve()
    assert Path(result.local_path).is_absolute()
    assert Path(result.local_path) == expected
    assert expected.exists()
    assert expected.stat().st_size > 0
    # The child runs in an empty workspace, not in the repo/cache directory:
    # it has no project to index before drawing.
    cwd_info = json.loads((tmp_path / "cwd.json").read_text())
    assert Path(cwd_info["cwd"]) != work
    # The empty workspace carries only our schema file; no project to index.
    assert set(cwd_info["entries"]) <= {"agy-image-schema.json"}
    argv = json.loads((tmp_path / "argv.json").read_text())
    prompt = argv[argv.index("-p") + 1]
    match = re.search(r"Save the image to (.+?) and reply", prompt)
    assert match is not None
    prompt_target = Path(match.group(1))
    assert prompt_target.is_absolute()
    assert prompt_target.parent != work
    assert prompt_target.name == "image.png"
    assert not prompt_target.exists()
    assert not list(work.glob(".*.agy.png"))


@pytest.mark.asyncio
async def test_output_lands_by_same_directory_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#280: the workspace is on another filesystem; the final step must be a rename."""
    import os

    from video_editor_asset_ingestion.providers.visual import agy_image

    provider = make_provider(tmp_path)
    output = tmp_path / "work" / "out.png"
    renames: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def spy(src: str | Path, dst: str | Path) -> None:
        renames.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(agy_image.os, "replace", spy)

    await provider.generate(make_request(), output)

    assert len(renames) == 1
    src, dst = renames[0]
    assert dst == output.resolve()
    assert src.parent == output.resolve().parent
    assert src.name.startswith(".out.")
    assert output.read_bytes().startswith(b"\x89PNG")
    assert not list(output.parent.glob(".*.agy.png"))


@pytest.mark.asyncio
async def test_failed_rename_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_asset_ingestion.providers.visual import agy_image

    provider = make_provider(tmp_path)
    output = tmp_path / "work" / "out.png"

    def boom(src: str | Path, dst: str | Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(agy_image.os, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        await provider.generate(make_request(), output)

    assert not output.exists()
    assert list(output.parent.iterdir()) == []


@pytest.mark.asyncio
async def test_temp_paths_are_unique_per_run(tmp_path: Path) -> None:
    """Concurrent runs on the same key must not share a temp name."""
    provider = make_provider(tmp_path)
    output = tmp_path / "out.png"

    await provider.generate(make_request(), output)
    await provider.generate(make_request(), output)

    lines = (tmp_path / "argv.log").read_text().splitlines()
    prompts = [json.loads(line)[json.loads(line).index("-p") + 1] for line in lines]
    targets = [
        re.search(r"Save the image to (.+?) and reply", prompt).group(1)  # type: ignore[union-attr]
        for prompt in prompts
    ]
    assert len(targets) == 2
    assert len(set(targets)) == 2
    assert all(Path(target).name == "image.png" for target in targets)
    assert all(Path(target).parent != tmp_path for target in targets)


@pytest.mark.asyncio
async def test_generate_uses_sandbox_and_aspect_line(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)
    request = make_request(params={"width": 1080, "height": 1920})

    await provider.generate(request, tmp_path / "out.png")

    argv = json.loads((tmp_path / "argv.json").read_text())
    assert "--sandbox" in argv
    assert any("Aspect: 9:16 (1080x1920)" in arg for arg in argv)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "line"),
    [
        ({"aspect_ratio": "9:16"}, "Aspect: 9:16."),
        ({"model_kwargs": {"size": "4:5"}}, "Aspect: 4:5."),
        # No framing on the request: agy draws 16:9 like every other provider.
        ({}, "Aspect: 16:9."),
    ],
)
async def test_every_spelling_of_the_aspect_reaches_the_prompt(
    tmp_path: Path, params: dict[str, object], line: str
) -> None:
    """#291: only width/height used to reach agy; every other caller got a square."""
    provider = make_provider(tmp_path)

    result = await provider.generate(make_request(params=params), tmp_path / "out.png")

    argv = json.loads((tmp_path / "argv.json").read_text())
    prompt = argv[argv.index("-p") + 1]
    assert line in prompt
    ratio = line.removeprefix("Aspect: ").rstrip(".").split(":")
    assert result.metadata["width"] * int(ratio[1]) == result.metadata["height"] * int(ratio[0])


@pytest.mark.asyncio
async def test_an_image_at_the_wrong_aspect_is_rejected(tmp_path: Path) -> None:
    """#291: a square still for a 9:16 short falls through to the next provider."""
    provider = make_provider(tmp_path, mode="square")
    output = tmp_path / "out.png"

    with pytest.raises(AGYImageError, match="aspect"):
        await provider.generate(make_request(params={"aspect_ratio": "9:16"}), output)

    assert not output.exists()


@pytest.mark.asyncio
async def test_missing_file_raises(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="nofile")

    with pytest.raises(AGYImageError, match="wrote no image"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_invalid_bytes_raise(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="garbage")

    with pytest.raises(AGYImageError, match="not a valid image"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_nonzero_exit_raises(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="exit1")

    with pytest.raises(AGYImageError, match="exited 3"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_probe_failure_raises_agy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = make_provider(tmp_path)

    def boom(_path: object) -> object:
        raise FFmpegError("ffprobe exploded", ["ffprobe"])

    monkeypatch.setattr("video_editor_asset_ingestion.providers.visual.agy_image.probe_media", boom)

    with pytest.raises(AGYImageError, match="unreadable image"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_timeout_kills_process(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="sleep")
    provider.timeout = 0.5

    with pytest.raises(AGYImageError, match="timed out"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_cancellation_inside_the_timeout_reap_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel that lands while reaping must not become a timeout error.

    The timeout branch kills and then reaps. If a cancellation arrives during
    that reap it is the newer signal and must cancel the task; swallowing it
    would report a plain ``AGYImageError`` for a job the operator cancelled.
    """
    provider = make_provider(tmp_path, mode="sleep")
    provider.timeout = 0.5
    real_reap = AGYImageProvider._reap

    async def reap_then_cancel(proc: asyncio.subprocess.Process, timeout: float = 5.0) -> None:
        await real_reap(proc, timeout)
        raise asyncio.CancelledError

    monkeypatch.setattr(provider, "_reap", reap_then_cancel)

    with pytest.raises(asyncio.CancelledError):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_cancellation_kills_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled task must not orphan the agent process in its own session."""
    provider = make_provider(tmp_path, mode="sleep")
    captured: dict[str, asyncio.subprocess.Process] = {}
    real_spawn = asyncio.create_subprocess_exec

    async def _spy(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        proc = await real_spawn(*args, **kwargs)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spy)

    task = asyncio.create_task(provider.generate(make_request(), tmp_path / "out.png"))
    for _ in range(200):
        if "proc" in captured:
            break
        await asyncio.sleep(0.01)
    assert "proc" in captured, "fake agy was never spawned"
    proc = captured["proc"]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.returncode is not None, "cancellation left the agy child running"


@pytest.mark.asyncio
async def test_non_png_extension_fails_before_running(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)

    with pytest.raises(AGYImageError, match="PNG only"):
        await provider.generate(make_request(output_ext="jpg"), tmp_path / "out.jpg")

    assert not (tmp_path / "argv.json").exists()


@pytest.mark.asyncio
async def test_missing_binary_raises(tmp_path: Path) -> None:
    provider = AGYImageProvider(
        ProviderConfig(provider_options={"agy_bin": str(tmp_path / "does-not-exist")})
    )

    with pytest.raises(AGYImageError, match="not found"):
        await provider.generate(make_request(), tmp_path / "out.png")


def test_timeout_is_not_retryable() -> None:
    from video_editor_asset_ingestion.manager import _is_retryable

    assert _is_retryable(AGYImageError("boom")) is False


def test_timeout_uses_default_only_when_unset() -> None:
    # ProviderConfig.timeout defaults to 120.0; a bare config must still get
    # the AGY-specific 300 s budget, while an explicit value must win.
    assert AGYImageProvider(ProviderConfig()).timeout == DEFAULT_TIMEOUT_SECONDS
    assert AGYImageProvider(ProviderConfig(timeout=45)).timeout == 45


@pytest.mark.asyncio
async def test_stdin_is_devnull(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = make_provider(tmp_path)
    seen: dict[str, object] = {}
    real_spawn = asyncio.create_subprocess_exec

    async def _spy(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spy)

    await provider.generate(make_request(), tmp_path / "out.png")

    assert seen["stdin"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_model_option_is_passed_and_recorded(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, model="gemini-2.5-flash")

    result = await provider.generate(make_request(), tmp_path / "out.png")

    argv = json.loads((tmp_path / "argv.json").read_text())
    assert "--model" in argv
    assert "gemini-2.5-flash" in argv
    assert result.metadata["model"] == "gemini-2.5-flash"


@pytest.mark.parametrize("value", [False, 0, "false", "FALSE", "0", " false "])
def test_falsey_sandbox_options_are_ignored_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, value: object
) -> None:
    """``--sandbox`` is unconditional; a false option must not pass silently."""
    import logging

    caplog.set_level(logging.WARNING)

    provider = make_provider(tmp_path, sandbox=value)

    argv = provider._argv(make_request(), tmp_path / "out.png", "agy")
    assert "--sandbox" in argv
    assert any("agy_image_sandbox_forced" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("value", [True, 1, "true", "yes", None])
def test_non_false_sandbox_options_do_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, value: object
) -> None:
    import logging

    caplog.set_level(logging.WARNING)

    make_provider(tmp_path, sandbox=value)

    assert not any("agy_image_sandbox_forced" in record.getMessage() for record in caplog.records)


SESSION_FAKE = """\
import json, pathlib, re, sys
here = pathlib.Path(__file__).parent
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("1.4.8")
    sys.exit(0)
if "models" in argv:
    print(json.dumps({"status": "SUCCESS", "command": {"data": {"models": [{"id": "gemini-3.8-flash-low"}]}}}))
    sys.exit(0)
with (here / "session.log").open("a", encoding="utf-8") as fh:
    fh.write("start\\n")
def png(width, height):
    import struct, zlib
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\\x00" + b"\\xff\\x00\\x00" * width for _ in range(height))
    return (b"\\x89PNG\\r\\n\\x1a\\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
print(json.dumps({"event": "init", "init": {"model": "gemini-3.8-flash-low"}}), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    text = message["message"]["content"][0]["text"]
    match = re.search(r"Save the image to (.+?) and reply", text)
    if not match:
        continue
    target = pathlib.Path(match.group(1))
    aspect = re.search(r"Aspect: (\\d+):(\\d+)", text)
    w, h = (int(aspect.group(1)), int(aspect.group(2))) if aspect else (1, 1)
    target.write_bytes(png(w * 4, h * 4))
    print(json.dumps({
        "event": "result",
        "result": {
            "status": "SUCCESS",
            "response": "",
            "structured_output": {
                "path": str(target),
                "width": w * 4,
                "height": h * 4,
                "image_model": "session-image-model",
            },
        },
    }), flush=True)
"""


def make_session_agy(tmp_path: Path) -> Path:
    script = tmp_path / "agy"
    script.write_text(f"#!{sys.executable}\n{SESSION_FAKE}")
    script.chmod(0o755)
    return script


@pytest.mark.asyncio
async def test_structured_call_passes_the_schema_and_low_effort(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)
    argv = provider._argv(make_request(), tmp_path / "image.png", "agy")

    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert "--json-schema" in argv
    assert "--disable-slash-commands" in argv
    assert argv[argv.index("--effort") + 1] == "low"
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-low"
    assert argv[argv.index("--print-timeout") + 1].endswith("s")
    schema = json.loads(Path(argv[argv.index("--json-schema") + 1]).read_text())
    assert set(schema["required"]) == {"path", "width", "height", "image_model"}


@pytest.mark.asyncio
async def test_structured_reply_records_provenance(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)

    result = await provider.generate(make_request(), tmp_path / "out.png")

    assert result.metadata["agy_version"] == "1.4.8"
    assert result.metadata["driving_model"] == "gemini-3.8-flash-low"
    assert result.metadata["image_model"] == "gemini-3.8-flash-low"
    assert result.metadata["quota_units"] == 1
    assert result.metadata["cost"] == 0.0


@pytest.mark.asyncio
async def test_a_missing_structured_reply_raises_the_agent_reason(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="schema")

    with pytest.raises(AGYImageError, match="no structured output"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_unsupported_agy_version_fails_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = make_provider(tmp_path)

    async def fake_capture(argv: list[str], *, cwd: Path, timeout: float) -> tuple[str, str]:
        if "--version" in argv:
            return "1.3.0\n", ""
        return (
            json.dumps({"status": "SUCCESS", "command": {"data": {"models": [{"id": "x"}]}}}),
            "",
        )

    monkeypatch.setattr(provider, "_capture", fake_capture)

    with pytest.raises(AGYImageError, match="supported range"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_a_quota_error_hidden_behind_a_fabricated_reply_is_classified(
    tmp_path: Path,
) -> None:
    provider = make_provider(tmp_path, mode="quota")

    with pytest.raises(AGYImageError) as exc:
        await provider.generate(make_request(), tmp_path / "out.png")

    assert exc.value.kind == "quota"


@pytest.mark.asyncio
async def test_an_auth_failure_is_classified_and_trips_the_breaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = make_provider(tmp_path)

    async def fake_capture(argv: list[str], *, cwd: Path, timeout: float) -> tuple[str, str]:
        if "--version" in argv:
            return "1.4.8\n", ""
        return json.dumps({"status": "ERROR", "response": "not authenticated"}), ""

    monkeypatch.setattr(provider, "_capture", fake_capture)

    with pytest.raises(AGYImageError) as exc:
        await provider.preflight()
    assert exc.value.kind == "auth"


@pytest.mark.asyncio
async def test_session_reuses_one_stream_process_for_a_batch(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    auth = profile / ".gemini"
    auth.mkdir(parents=True)
    (auth / "oauth_creds.json").write_text("{}")
    provider = AGYImageProvider(
        ProviderConfig(
            provider_options={
                "agy_bin": str(make_session_agy(tmp_path)),
                "session": True,
                "profile_dir": str(profile),
            }
        )
    )

    first = await provider.generate(make_request(asset_id="a"), tmp_path / "a.png")
    second = await provider.generate(make_request(asset_id="b"), tmp_path / "b.png")

    assert first.metadata["image_model"] == "session-image-model"
    assert second.metadata["image_model"] == "session-image-model"
    assert (tmp_path / "session.log").read_text().count("start") == 1
    await provider.aclose()


@pytest.mark.asyncio
async def test_a_dead_session_falls_back_to_a_one_shot_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = make_provider(tmp_path)
    provider.use_session = True
    calls: list[str] = []

    async def dead_session(*args: object, **kwargs: object) -> tuple[dict[str, object], int, int]:
        calls.append("session")
        raise AGYImageError("stream closed")

    async def one_shot(*args: object, **kwargs: object) -> tuple[dict[str, object], int, int]:
        calls.append("one-shot")
        return (
            {"path": "x", "width": 16, "height": 9, "image_model": "fallback"},
            16,
            9,
        )

    monkeypatch.setattr(provider, "_generate_session", dead_session)
    monkeypatch.setattr(provider, "_generate_once", one_shot)

    result = await provider.generate(make_request(), tmp_path / "out.png")

    assert calls == ["session", "one-shot"]
    assert result.metadata["image_model"] == "fallback"
