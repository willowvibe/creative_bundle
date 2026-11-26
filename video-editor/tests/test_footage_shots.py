"""``shots:`` on a footage scene: several stock clips/photos in one scene."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_core.models.asset import BatchResult
from video_editor_core.scene_plan_composition import compile_scene_plan_project
from video_editor_pipelines.explainer_footage import FootageVerdict, resolve_with_footage
from video_editor_pipelines.explainer_pipeline import explainer_job_requests, footage_check
from video_editor_pipelines.explainer_plan import (
    attach_footage_shots,
    build_explainer_plan,
    footage_fallbacks,
    initial_requests,
)
from video_editor_pipelines.explainer_script import ScriptError, parse_explainer_script

HEADER = """---
title: Tiffin
aspect_ratio: "16:9"
narration_mode: per_scene
---

"""

SHOTS_SCENE = """## Scene 1 — Counter
visual: footage
image: a busy south indian tiffin centre documentary photo
shots:
  - {query: idli steaming restaurant india}
  - {photo: south indian tiffin centre counter}
  - {query: dosa tawa cooking, image: a dosa on a hot tawa documentary photo}
narration: Idli, dosa and a crowd at the counter.

## Scene 2 — Total
visual: typographic
layout: big_stat
headline: ₹12
narration: Twelve rupees a plate.
"""

SINGLE_SCENE = SHOTS_SCENE.replace(
    "shots:\n  - {query: idli steaming restaurant india}\n"
    "  - {photo: south indian tiffin centre counter}\n"
    "  - {query: dosa tawa cooking, image: a dosa on a hot tawa documentary photo}\n",
    "query: idli steaming restaurant india\n",
)


def _script(body: str = SHOTS_SCENE):
    return parse_explainer_script(HEADER + body)


def _requests(body: str = SHOTS_SCENE):
    script = _script(body)
    plan, _ = build_explainer_plan(script, "job")
    return plan, explainer_job_requests(plan, script, "job", music_provider="")


# --- parse + validation ------------------------------------------------------


def test_shots_parse_and_normalise() -> None:
    shots = _script().scenes[0].props["shots"]

    assert [(s["query"], s["photo"], s["image"]) for s in shots] == [
        ("idli steaming restaurant india", "", ""),
        ("", "south indian tiffin centre counter", ""),
        ("dosa tawa cooking", "", "a dosa on a hot tawa documentary photo"),
    ]


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("shots: []\n", "non-empty list"),
        ("shots: idli\n", "non-empty list"),
        ("shots:\n  - just a string\n", "shot 1 must be a map"),
        ("shots:\n  - {image: only a fallback}\n", "shot 1 must have exactly one"),
        ("shots:\n  - {query: a, photo: b}\n", "shot 1 must have exactly one"),
        ("shots:\n  - {query: a}\n  - {qeury: typo}\n", "shot 2 must have exactly one"),
        ("shots:\n  - {query: a, clip: b}\n", "unknown key"),
        ("query: x\nshots:\n  - {query: a}\n", "either `query` or `shots`"),
    ],
)
def test_bad_shots_are_rejected_when_the_script_is_read(replacement: str, message: str) -> None:
    start = SHOTS_SCENE.index("shots:")
    end = SHOTS_SCENE.index("narration: Idli")
    body = SHOTS_SCENE[:start] + replacement + SHOTS_SCENE[end:]
    with pytest.raises(ScriptError, match=message):
        _script(body)


def test_a_shot_without_any_fallback_is_rejected() -> None:
    body = SHOTS_SCENE.replace("image: a busy south indian tiffin centre documentary photo\n", "")
    with pytest.raises(ScriptError, match="shot 1 has no `image` fallback"):
        _script(body)


def test_a_scene_level_image_is_every_shots_fallback() -> None:
    _, requests = _requests()
    fallbacks = {r.asset_id: r.prompt for r in requests if r.asset_id.endswith("_fallback_image")}

    assert fallbacks == {
        "footage_scene_1_0_fallback_image": "a busy south indian tiffin centre documentary photo",
        "footage_scene_1_1_fallback_image": "a busy south indian tiffin centre documentary photo",
        "footage_scene_1_2_fallback_image": "a dosa on a hot tawa documentary photo",
    }


# --- requests ----------------------------------------------------------------


def test_each_shot_gets_its_own_request_video_or_photo() -> None:
    _, requests = _requests()
    stock = [r for r in requests if r.provider == "stock"]

    assert [(r.asset_id, r.asset_type, r.prompt, r.output_ext) for r in stock] == [
        ("footage_scene_1_0", AssetType.VIDEO, "idli steaming restaurant india", "mp4"),
        ("footage_scene_1_1", AssetType.IMAGE, "south indian tiffin centre counter", "jpg"),
        ("footage_scene_1_2", AssetType.VIDEO, "dosa tawa cooking", "mp4"),
    ]
    assert all(r.scene_id == "scene_1" for r in stock)
    # A clip is asked to cover its slot (a third of the scene, never under 1 s).
    assert stock[0].params["duration_seconds"] >= 1.0
    assert "duration_seconds" not in stock[1].params


def test_the_cost_gate_counts_every_shot() -> None:
    _, requests = _requests()

    assert sum(r.provider == "stock" for r in requests) == 3
    assert sum(r.asset_id.endswith("_fallback_image") for r in requests) == 3


def test_narration_requests_do_not_change_with_shots() -> None:
    _, with_shots = _requests()
    _, single = _requests(SINGLE_SCENE)

    def narration(requests: list[AssetRequest]) -> list[tuple[str, str]]:
        return [(r.asset_id, r.prompt) for r in requests if r.asset_type is AssetType.TTS]

    assert narration(with_shots) == narration(single)
    assert narration(with_shots)[0][0] == "narration_scene_1"


def test_query_alone_is_unchanged() -> None:
    plan, requests = _requests(SINGLE_SCENE)
    scene = plan["scenes"][0]

    assert scene["props"]["stock_query"] == "idli steaming restaurant india"
    assert "shots" not in scene["props"]
    assert [r.asset_id for r in requests if r.provider == "stock"] == ["scene_1_footage"]
    assert [r.asset_id for r in requests if r.asset_id.endswith("_fallback_image")] == [
        "scene_1_fallback_image"
    ]


def test_only_the_shots_that_lost_their_media_ask_for_a_fallback() -> None:
    _, requests = _requests()

    chosen = footage_fallbacks(
        requests,
        scenes_without_clip=set(),
        shots_without_media={"footage_scene_1_2_fallback_image"},
    )

    assert [r.asset_id for r in chosen] == ["footage_scene_1_2_fallback_image"]
    assert not any(r.asset_id.endswith("_fallback_image") for r in initial_requests(requests))


# --- the footage pass --------------------------------------------------------


class FakeManager:
    def __init__(self, serve, calls, tmp: Path) -> None:  # type: ignore[no-untyped-def]
        self.serve, self.calls, self.tmp = serve, calls, tmp

    async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
        self.calls.append([r.asset_id for r in requests])
        batch = BatchResult()
        for request in requests:
            provider = self.serve(request)
            if provider is None:
                batch.failures.append((request.asset_id, "no usable results"))
                continue
            path = self.tmp / f"{request.asset_id}.{request.output_ext}"
            path.write_bytes(b"x")
            batch.results.append(
                AssetResult(
                    asset_id=request.asset_id,
                    asset_type=request.asset_type,
                    scene_id=request.scene_id,
                    local_path=str(path),
                    metadata={"provider": provider},
                )
            )
        return batch


async def _accept(clip: Path, query: str) -> FootageVerdict:
    return FootageVerdict(ok=True, reason="shows it")


@pytest.mark.asyncio
async def test_the_pass_reports_each_shot_and_falls_back_per_shot(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    _, requests = _requests()

    def serve(request: AssetRequest) -> str | None:
        if request.asset_id == "footage_scene_1_1":
            return None  # the photo search came back empty
        if request.asset_id.startswith("footage_"):
            return "pexels"
        return request.provider or "auto"

    results, report = await resolve_with_footage(
        lambda: FakeManager(serve, calls, tmp_path),
        requests,
        judge=_accept,
        stock_chain=["pexels", "pixabay"],
    )

    assert [(r["shot"], r["kind"], r["source"]) for r in report] == [
        (0, "video", "stock"),
        (1, "photo", "ai_fallback"),
        (2, "video", "stock"),
    ]
    assert calls[-1] == ["footage_scene_1_1_fallback_image"]
    ids = {r.asset_id for r in results}
    assert {"footage_scene_1_0", "footage_scene_1_2", "footage_scene_1_1_fallback_image"} <= ids
    assert footage_check(report).ok  # a photo is never "unjudged"


@pytest.mark.asyncio
async def test_a_rejected_shot_clip_retries_another_library(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    _, requests = _requests()

    async def judge(clip: Path, query: str) -> FootageVerdict:
        return FootageVerdict(ok="footage_scene_1_0." not in clip.name, reason="checked")

    results, report = await resolve_with_footage(
        lambda: FakeManager(
            lambda r: "pexels" if r.provider == "stock" else "auto", calls, tmp_path
        ),
        requests,
        judge=judge,
        stock_chain=["pexels", "pixabay"],
    )

    assert ["footage_scene_1_0_pixabay"] in calls
    assert report[0]["rejected"] == [{"provider": "pexels", "reason": "checked"}]
    assert "footage_scene_1_0_pixabay" in {r.asset_id for r in results}


def test_attach_points_each_shot_at_its_file() -> None:
    plan, _ = _requests()

    def result(asset_id: str, kind: AssetType, ext: str) -> AssetResult:
        return AssetResult(
            asset_id=asset_id,
            asset_type=kind,
            scene_id="scene_1",
            local_path=f"/m/{asset_id}.{ext}",
        )

    attach_footage_shots(
        plan,
        [
            result("footage_scene_1_0_pixabay", AssetType.VIDEO, "mp4"),
            result("footage_scene_1_1", AssetType.IMAGE, "jpg"),
            result("footage_scene_1_2_fallback_image", AssetType.IMAGE, "png"),
        ],
    )
    shots = plan["scenes"][0]["props"]["shots"]

    assert [(s["kind"], s["source"], Path(s["path"]).name) for s in shots] == [
        ("video", "stock", "footage_scene_1_0_pixabay.mp4"),
        ("image", "stock", "footage_scene_1_1.jpg"),
        ("image", "ai_fallback", "footage_scene_1_2_fallback_image.png"),
    ]
    with pytest.raises(RuntimeError, match="no media"):
        attach_footage_shots(plan, [])


# --- composition -------------------------------------------------------------


def _compiled(tmp_path: Path, duration: float = 9.0) -> tuple[str, dict]:
    plan, _ = _requests()
    for name in ("a.mp4", "b.jpg", "c.png"):
        (tmp_path / name).write_bytes(b"x")
    plan["design"] = ""
    plan["scenes"][0]["duration_seconds"] = duration
    plan["scenes"][0]["props"]["tally"] = {"label": "cash", "value": -30}
    for shot, (name, kind) in zip(
        plan["scenes"][0]["props"]["shots"],
        [("a.mp4", "video"), ("b.jpg", "image"), ("c.png", "image")],
        strict=True,
    ):
        shot.update(path=str(tmp_path / name), kind=kind, source="stock")
    plan["scenes"] = plan["scenes"][:1]
    index = compile_scene_plan_project(plan, tmp_path / "project")
    return index.read_text(encoding="utf-8"), plan


def _tag(html: str, element_id: str) -> str:
    match = re.search(rf'<(?:video|img)[^>]*id="{element_id}"[^>]*>', html)
    assert match, f"{element_id} missing"
    return match.group(0)


def test_shots_are_placed_in_order_with_an_even_split(tmp_path: Path) -> None:
    html, _ = _compiled(tmp_path)

    first, second, third = (_tag(html, f"scene_0_shot{n}") for n in range(3))
    assert first.startswith("<video") and " muted" in first and " loop" in first
    assert 'data-start="0.000"' in first and 'data-duration="3.000"' in first
    assert second.startswith("<img") and 'data-start="3.000"' in second
    assert 'data-duration="3.000"' in second
    assert third.startswith("<img") and 'data-start="6.000"' in third
    assert 'data-duration="3.000"' in third
    assert html.index("scene_0_shot0") < html.index("scene_0_shot1") < html.index("scene_0_shot2")
    assert "object-fit:cover" in first
    assert "scene_0_video" not in html and "scene_0_img" not in html


def test_photos_get_a_ken_burns_and_clips_do_not(tmp_path: Path) -> None:
    html, _ = _compiled(tmp_path)

    assert "scale(1.08)" in html and "scale(1) translate(0%, 0%)" in html
    assert "animation:" in _tag(html, "scene_0_shot1")
    assert "animation:" not in _tag(html, "scene_0_shot0")


def test_scene_overlays_still_span_the_whole_scene(tmp_path: Path) -> None:
    html, _ = _compiled(tmp_path)

    chip = re.search(r'<[^>]*id="scene_0_tally[^"]*"[^>]*>', html)
    assert (
        chip and 'data-start="0.000"' in chip.group(0) and 'data-duration="9.000"' in chip.group(0)
    )


def test_an_odd_duration_still_covers_the_scene_exactly(tmp_path: Path) -> None:
    html, _ = _compiled(tmp_path, duration=10.0)

    total = sum(
        float(re.search(r'data-duration="([\d.]+)"', _tag(html, f"scene_0_shot{n}")).group(1))
        for n in range(3)
    )
    assert abs(total - 10.0) < 2e-3  # three-decimal printing


def test_the_single_query_path_still_places_one_clip(tmp_path: Path) -> None:
    script = _script(SINGLE_SCENE)
    plan, _ = build_explainer_plan(script, "job")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    plan["design"] = ""
    plan["scenes"] = plan["scenes"][:1]
    plan["scenes"][0]["duration_seconds"] = 6.0
    plan["scenes"][0]["assets"] = [
        {
            "asset_id": "scene_1_footage",
            "asset_type": "video",
            "scene_id": "scene_1",
            "local_path": str(clip),
        }
    ]

    html = compile_scene_plan_project(plan, tmp_path / "p").read_text(encoding="utf-8")

    assert 'id="scene_0_video"' in html and "shot0" not in html


class IdManager(FakeManager):
    """Serves stock results carrying an attribution id: ``ids(request)`` picks it."""

    def __init__(self, ids, calls, tmp: Path, params_seen) -> None:  # type: ignore[no-untyped-def]
        super().__init__(lambda r: None, calls, tmp)
        self.ids, self.params_seen = ids, params_seen

    async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
        self.calls.append([r.asset_id for r in requests])
        batch = BatchResult()
        for request in requests:
            self.params_seen[request.asset_id] = dict(request.params)
            media_id = self.ids(request)
            if media_id is None:
                batch.failures.append((request.asset_id, "no usable results"))
                continue
            path = self.tmp / f"{request.asset_id}.{request.output_ext}"
            path.write_bytes(b"x")
            provider = "pexels" if request.provider in ("stock", "pexels") else request.provider
            key = "photo_id" if request.asset_type == AssetType.IMAGE else "video_id"
            batch.results.append(
                AssetResult(
                    asset_id=request.asset_id,
                    asset_type=request.asset_type,
                    scene_id=request.scene_id,
                    local_path=str(path),
                    metadata={"provider": provider, "attribution": {key: media_id}},
                )
            )
        return batch


@pytest.mark.asyncio
async def test_a_clip_already_used_is_skipped_for_the_next_result(tmp_path: Path) -> None:
    """Two shots whose top hit is the same clip: the second moves down the list."""
    calls: list[list[str]] = []
    seen: dict[str, dict] = {}
    _, requests = _requests()

    def ids(request: AssetRequest) -> str | None:
        if "fallback" in request.asset_id or request.asset_type == AssetType.IMAGE:
            return f"img-{request.asset_id}"
        excluded = set(request.params.get("exclude_ids") or ())
        # Every query's top hit is clip 111; the next result is 222.
        return "222" if "111" in excluded else "111"

    results, report = await resolve_with_footage(
        lambda: IdManager(ids, calls, tmp_path, seen),
        requests,
        judge=_accept,
        stock_chain=["pexels"],  # one library: the repeat must move down its list
    )

    videos = [r for r in report if r["kind"] == "video"]
    assert [v["source"] for v in videos] == ["stock", "stock"]
    assert videos[1]["rejected"][0]["reason"] == "already used in this video"
    retry_id = next(a for a in seen if a.startswith("footage_scene_1_2_"))
    assert "111" in seen[retry_id]["exclude_ids"]
    chosen = {
        r.metadata["attribution"].get("video_id")
        for r in results
        if r.asset_id.startswith(("footage_scene_1_0", "footage_scene_1_2"))
    }
    assert chosen == {"111", "222"}


def test_providers_skip_excluded_ids() -> None:
    from video_editor_asset_ingestion.providers.visual.pexels import PexelsProvider

    provider = PexelsProvider.__new__(PexelsProvider)
    provider.provider_options = {}
    file = {"file_type": "video/mp4", "link": "u", "width": 1920, "height": 1080}
    data = {
        "videos": [
            {"id": 1, "duration": 10, "video_files": [file], "user": {}},
            {"id": 2, "duration": 10, "video_files": [{**file, "link": "v"}], "user": {}},
        ]
    }
    picked = provider._select_result(data, AssetType.VIDEO, exclude_ids=frozenset({"1"}))
    assert picked is not None and picked[1]["video_id"] == 2
    assert provider._select_result(data, AssetType.VIDEO, exclude_ids=frozenset({"1", "2"})) is None


# --- the AI label reaches shot fallbacks ---------------------------------------


@pytest.mark.asyncio
async def test_shot_fallback_stills_carry_the_ai_label(tmp_path: Path) -> None:
    """The tiffin shot fallbacks came from ``poyo_ai`` and shipped with no label."""
    from PIL import Image
    from video_editor_pipelines.ai_label import label_ai_stills

    plan, requests = _requests()
    calls: list[list[str]] = []

    class StillManager(FakeManager):
        async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
            batch = await super().resolve_partial(requests)
            for result in batch.results:
                if result.asset_type is AssetType.IMAGE:
                    Image.new("RGB", (1376, 768), (40, 90, 140)).save(result.local_path, "PNG")
            return batch

    def serve(request: AssetRequest) -> str | None:
        # No stock for shots 0 and 1; every AI still comes from poyo_ai.
        if request.asset_id.endswith("_fallback_image"):
            return "poyo_ai"
        if request.asset_id.startswith(("footage_scene_1_0", "footage_scene_1_1")):
            return None
        return "pexels"

    results, _ = await resolve_with_footage(
        lambda: StillManager(serve, calls, tmp_path),
        requests,
        judge=_accept,
        stock_chain=["pexels"],
    )
    labelled = label_ai_stills(results, tmp_path / "labelled", text="AI చిత్రం")
    attach_footage_shots(plan, labelled)

    shots = plan["scenes"][0]["props"]["shots"]
    assert [s["source"] for s in shots] == ["ai_fallback", "ai_fallback", "stock"]
    for shot in shots[:2]:
        assert Path(shot["path"]).parent == tmp_path / "labelled"
    # the labelled pill sits over the old sparkle corner; the corner of the raw still is untouched
    pill = Image.open(shots[0]["path"]).getpixel((1376 - 97, 768 - 97))
    assert pill != (40, 90, 140)
    assert Path(shots[2]["path"]).parent == tmp_path  # stock clip: not labelled


# --- file: shots (your own image or clip) --------------------------------------


FILE_SCENE = """## Scene 1 — Counter
visual: footage
shots:
  - {file: flow/scene1_shot0.webp, ai: true}
  - {file: clips/counter.mp4}
  - {query: dosa tawa cooking, image: a dosa on a hot tawa documentary photo}
narration: Idli, dosa and a crowd at the counter.
"""


def _folder(tmp_path: Path) -> Path:
    (tmp_path / "flow").mkdir()
    (tmp_path / "clips").mkdir()
    (tmp_path / "flow" / "scene1_shot0.webp").write_bytes(b"w")
    (tmp_path / "clips" / "counter.mp4").write_bytes(b"v")
    return tmp_path


def _file_script(tmp_path: Path, body: str = FILE_SCENE):
    return parse_explainer_script(HEADER + body, base_dir=_folder(tmp_path))


def _file_requests(tmp_path: Path):
    script = _file_script(tmp_path)
    plan, _ = build_explainer_plan(script, "job")
    return plan, explainer_job_requests(plan, script, "job", music_provider="")


def test_file_shots_parse_relative_to_the_script(tmp_path: Path) -> None:
    shots = _file_script(tmp_path).scenes[0].props["shots"]

    assert shots[0]["file"] == str((tmp_path / "flow" / "scene1_shot0.webp").resolve())
    assert (shots[0]["ai"], shots[1]["ai"], shots[2]["ai"]) == (True, False, False)
    assert shots[1]["file"].endswith("clips/counter.mp4")


def test_a_file_shot_needs_no_image_fallback(tmp_path: Path) -> None:
    body = FILE_SCENE.replace(", image: a dosa on a hot tawa documentary photo", "")
    # shot 3 is a bare query with no scene-level image: still rejected; the file shots are not.
    with pytest.raises(ScriptError, match="shot 3 has no `image` fallback"):
        _file_script(tmp_path, body)


def test_file_shot_validation(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "real.png").write_bytes(b"p")
    (folder / "link.png").symlink_to(folder / "real.png")
    (folder / "notes.txt").write_text("x")

    def attempt(shot: str) -> None:
        body = f"## S\nvisual: footage\nshots:\n  - {shot}\nnarration: Hi.\n"
        parse_explainer_script(HEADER + body, base_dir=folder)

    for shot, message in [
        ("{file: nope.png}", "does not exist"),
        ("{file: flow}", "not a regular file"),
        ("{file: link.png}", "symlink"),
        ("{file: notes.txt}", "unsupported extension"),
        ("{query: a, ai: true}", "only allowed with `file`"),
        ("{photo: a, ai: true}", "only allowed with `file`"),
        ("{file: real.png, ai: maybe}", "true or false"),
        ("{file: real.png, query: a}", "exactly one of"),
        ("{file: real.png, photo: a}", "exactly one of"),
        ("{file: real.png, image: a prompt}", "no AI fallback"),
    ]:
        with pytest.raises(ScriptError, match=message):
            attempt(shot)
    attempt(f"{{file: {folder / 'real.png'}}}")  # absolute paths are fine


def test_file_shots_become_local_media_requests(tmp_path: Path) -> None:
    plan, requests = _file_requests(tmp_path)
    local = [r for r in requests if r.provider == "local_media"]

    assert [(r.asset_id, r.asset_type, r.output_ext) for r in local] == [
        ("footage_scene_1_0", AssetType.IMAGE, "webp"),
        ("footage_scene_1_1", AssetType.VIDEO, "mp4"),
    ]
    assert local[0].prompt == str((tmp_path / "flow" / "scene1_shot0.webp").resolve())
    assert local[0].params["ai"] is True and local[1].params["ai"] is False
    # Only the stock shot has a fallback still.
    assert [r.asset_id for r in requests if r.asset_id.endswith("_fallback_image")] == [
        "footage_scene_1_2_fallback_image"
    ]
    assert plan["scenes"][0]["props"]["shots"][0]["fallback_image"] == ""


def test_file_shots_cost_nothing(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.pricing import PricingRegistry
    from video_editor_pipelines.explainer_pipeline import price_requests

    _, requests = _file_requests(tmp_path)
    items = price_requests(
        [r for r in requests if r.provider == "local_media"], PricingRegistry.load()
    )

    assert sum(item["total_cost"] for item in items) == 0
    registry = PricingRegistry.load()
    assert registry.unit_cost("local_media", "default", "image") == 0.0


@pytest.mark.asyncio
async def test_the_pass_never_judges_retries_or_replaces_file_shots(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    judged: list[str] = []
    _, requests = _file_requests(tmp_path)

    def serve(request: AssetRequest) -> str | None:
        return "local_media" if request.provider == "local_media" else "pexels"

    async def judge(clip: Path, query: str) -> FootageVerdict:
        judged.append(query)
        return FootageVerdict(ok=True, reason="ok")

    results, report = await resolve_with_footage(
        lambda: FakeManager(serve, calls, tmp_path),
        requests,
        judge=judge,
        stock_chain=["pexels", "pixabay"],
    )

    assert judged == ["dosa tawa cooking"]
    rows = {row["shot"]: row for row in report}
    assert (rows[0]["source"], rows[0]["kind"], rows[0]["ai"]) == ("file", "image", True)
    assert (rows[1]["source"], rows[1]["kind"], rows[1]["ai"]) == ("file", "video", False)
    assert "ai_label" not in rows[1]
    assert rows[2]["source"] == "stock"
    ids = {r.asset_id for r in results}
    assert {"footage_scene_1_0", "footage_scene_1_1", "footage_scene_1_2"} <= ids
    assert not any(a.endswith("_pexels") or a.endswith("_pixabay") for c in calls for a in c)
    assert footage_check(report).ok
    assert "2 own file" in footage_check(report).detail


@pytest.mark.asyncio
async def test_a_missing_file_is_a_hard_error(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    _, requests = _file_requests(tmp_path)

    def serve(request: AssetRequest) -> str | None:
        return None if request.provider == "local_media" else "pexels"

    with pytest.raises(RuntimeError, match="footage_scene_1_0"):
        await resolve_with_footage(
            lambda: FakeManager(serve, calls, tmp_path),
            requests,
            judge=_accept,
            stock_chain=["pexels"],
        )


@pytest.mark.asyncio
async def test_a_file_shot_is_not_deduped_against_stock_ids(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.visual.local_media import LocalMediaProvider

    _, requests = _file_requests(tmp_path)
    request = next(r for r in requests if r.provider == "local_media")
    result = await LocalMediaProvider().generate(request, tmp_path / "out" / "x.webp")

    assert result.metadata["ai"] is True
    assert "attribution" not in result.metadata
    assert (tmp_path / "out" / "x.webp").read_bytes() == b"w"


def test_attach_points_file_shots_at_their_copies(tmp_path: Path) -> None:
    plan, requests = _file_requests(tmp_path)

    def result(asset_id: str, kind: AssetType, ext: str) -> AssetResult:
        return AssetResult(
            asset_id=asset_id, asset_type=kind, scene_id="scene_1", local_path=f"/m/{asset_id}.{ext}"
        )

    attach_footage_shots(
        plan,
        [
            result("footage_scene_1_0", AssetType.IMAGE, "webp"),
            result("footage_scene_1_1", AssetType.VIDEO, "mp4"),
            result("footage_scene_1_2", AssetType.VIDEO, "mp4"),
        ],
    )
    shots = plan["scenes"][0]["props"]["shots"]

    assert [(s["kind"], s["source"]) for s in shots] == [
        ("image", "file"),
        ("video", "file"),
        ("video", "stock"),
    ]
    with pytest.raises(RuntimeError, match="was not served"):
        attach_footage_shots(plan, [result("footage_scene_1_2", AssetType.VIDEO, "mp4")])


def test_composition_places_file_shots_in_order_and_keeps_webp(tmp_path: Path) -> None:
    plan, _ = _file_requests(tmp_path)
    for name in ("a.webp", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(b"x")
    plan["design"] = ""
    plan["scenes"][0]["duration_seconds"] = 9.0
    plan["scenes"] = plan["scenes"][:1]
    for shot, (name, kind, source) in zip(
        plan["scenes"][0]["props"]["shots"],
        [("a.webp", "image", "file"), ("b.mp4", "video", "file"), ("c.mp4", "video", "stock")],
        strict=True,
    ):
        shot.update(path=str(tmp_path / name), kind=kind, source=source)

    html = compile_scene_plan_project(plan, tmp_path / "project").read_text(encoding="utf-8")

    first, second = _tag(html, "scene_0_shot0"), _tag(html, "scene_0_shot1")
    assert first.startswith("<img") and ".webp" in first and 'data-start="0.000"' in first
    assert second.startswith("<video") and " muted" in second and 'data-start="3.000"' in second
    assert "animation:" in first and "animation:" not in second
    assert list((tmp_path / "project" / "assets").glob("*.webp"))
