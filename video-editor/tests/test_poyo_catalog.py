"""The PoYo catalog check compares configured models with the live catalog."""

from __future__ import annotations

import json

from typer.testing import CliRunner
from video_editor_cli.commands import poyo_catalog

CATALOG = [
    {
        "model_id": "nano-banana-2-lite",
        "category": "image",
        "status": "stable",
        "billing": {"credits": 5},
    },
    {
        "model_id": "old-model",
        "category": "image",
        "status": "deprecated",
        "billing": {"credits": 1},
    },
]


def test_compare_catalog_marks_missing_retired_and_ok() -> None:
    result = poyo_catalog.compare_catalog(
        CATALOG,
        {
            "nano-banana-2-lite": "config assets.poyo_ai.default_model",
            "old-model": "config assets.poyo_ai.video_model",
            "gone-model": "config assets.poyo_ai.default_model",
        },
    )

    rows = {row["model"]: row for row in result["rows"]}
    assert rows["nano-banana-2-lite"]["status"] == "ok"
    assert rows["nano-banana-2-lite"]["credits"] == 5
    assert rows["old-model"]["status"] == "retired"
    assert rows["gone-model"]["status"] == "missing"
    assert result["ok"] is False


def test_poyo_catalog_json_and_check_exit(monkeypatch) -> None:
    from video_editor_cli.main import app

    monkeypatch.setattr(poyo_catalog, "fetch_catalog", lambda **kwargs: CATALOG)
    monkeypatch.setattr(
        poyo_catalog,
        "_configured_models",
        lambda _path: {"nano-banana-2-lite": "config"},
    )

    result = CliRunner().invoke(app, ["poyo-catalog", "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["checked"] == 1
    assert body["rows"][0]["status"] == "ok"

    monkeypatch.setattr(
        poyo_catalog,
        "_configured_models",
        lambda _path: {"gone-model": "config assets.poyo_ai.default_model"},
    )
    checked = CliRunner().invoke(app, ["poyo-catalog", "--check"])

    assert checked.exit_code == 1
    assert "MISSING" in checked.output
