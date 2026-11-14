"""A6: ve doctor reports external tool readiness."""

from __future__ import annotations

import json

from typer.testing import CliRunner


def test_doctor_reports_tools_and_json(monkeypatch) -> None:
    from video_editor_cli.commands import doctor
    from video_editor_cli.main import app

    monkeypatch.setattr(
        doctor,
        "collect_tools",
        lambda: [
            {"name": "ffmpeg", "path": "/ffmpeg", "version": "8.1", "ok": True, "detail": "ok"},
            {
                "name": "hyperframes",
                "path": None,
                "version": None,
                "ok": False,
                "detail": "missing",
            },
        ],
    )
    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output)[0]["name"] == "ffmpeg"
