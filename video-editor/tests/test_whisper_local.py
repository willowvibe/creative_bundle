"""Tests for the faster-whisper local transcription wrapper.

The wrapper loads a real `WhisperModel` and iterates over
`model.transcribe(...)`. These tests mock the model layer so we can
exercise the segment/word flattening and JSON write without a real
model download. The model import itself is also mocked so the test
suite doesn't require `faster_whisper` to be installed.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Stub faster_whisper module if it isn't installed
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_faster_whisper_stub() -> None:
    """Install a stub ``faster_whisper`` module if the real one is missing.

    The wrapper does ``from faster_whisper import WhisperModel`` at
    import time. We don't want the test suite to depend on a real
    faster_whisper install, so inject a stub when it's absent.
    """
    if "faster_whisper" in sys.modules:
        return

    stub = types.ModuleType("faster_whisper")

    class _StubWhisperModel:
        """Placeholder — tests monkeypatch _load_model instead."""

        def __init__(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
            raise RuntimeError(
                "_StubWhisperModel should never be instantiated in tests; "
                "monkeypatch whisper_local._load_model."
            )

    stub.WhisperModel = _StubWhisperModel  # type: ignore[attr-defined]
    sys.modules["faster_whisper"] = stub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_model(
    segments: list[Any],
    language: str = "en",
    language_probability: float = 0.99,
) -> Any:
    """Build a fake WhisperModel whose .transcribe() returns the given segments."""

    class _FakeInfo:
        def __init__(self, lang: str | None, prob: float) -> None:
            self.language = lang
            self.language_probability = prob

    class _FakeModel:
        def transcribe(
            self,
            audio: str,
            language: str | None = None,
            word_timestamps: bool = True,
            vad_filter: bool = True,
        ) -> tuple[Any, _FakeInfo]:
            return iter(segments), _FakeInfo(language, language_probability)

    return _FakeModel()


def _make_segment(
    seg_id: int,
    start: float,
    end: float,
    text: str,
    words: list[Any] | None = None,
) -> Any:
    """Build a fake faster-whisper segment object."""

    class _FakeSegment:
        def __init__(self) -> None:
            self.id = seg_id
            self.start = start
            self.end = end
            self.text = text
            self.words = words

    return _FakeSegment()


def _make_word(word: str, start: float, end: float, probability: float) -> Any:
    """Build a fake faster-whisper word object."""

    class _FakeWord:
        def __init__(self) -> None:
            self.word = word
            self.start = start
            self.end = end
            self.probability = probability

    return _FakeWord()


@pytest.fixture
def whisper_local_module() -> Any:
    """Import the wrapper after the stub is in place.

    Bug #87 regression guard: the module-level model cache persists across
    imports, so clear it before each test to keep device/compute_type tests
    deterministic when they monkeypatch ``WhisperModel``.
    """
    from video_editor_transcribe import whisper_local  # noqa: PLC0415

    whisper_local._MODEL_CACHE.clear()
    return whisper_local


# ---------------------------------------------------------------------------
# _load_model: device / compute_type dispatch
# ---------------------------------------------------------------------------


def test_load_model_uses_cpu_int8_by_default(
    whisper_local_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No device → cpu + int8 (the v1 default)."""
    captured: dict[str, Any] = {}

    class _FakeWhisperModel:
        def __init__(self, model_size: str, device: str, compute_type: str) -> None:
            captured["model_size"] = model_size
            captured["device"] = device
            captured["compute_type"] = compute_type

    monkeypatch.setattr(whisper_local_module, "WhisperModel", _FakeWhisperModel)

    model = whisper_local_module._load_model()

    # Returned object is whatever WhisperModel(...) produced.
    assert model is not None
    assert captured["device"] == "cpu"
    assert captured["compute_type"] == "int8"


def test_load_model_uses_cuda_float16_when_device_cuda(
    whisper_local_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """device='cuda' → float16 compute type."""
    captured: dict[str, Any] = {}

    class _FakeWhisperModel:
        def __init__(self, model_size: str, device: str, compute_type: str) -> None:
            captured["model_size"] = model_size
            captured["device"] = device
            captured["compute_type"] = compute_type

    monkeypatch.setattr(whisper_local_module, "WhisperModel", _FakeWhisperModel)

    whisper_local_module._load_model(model_size="small", device="cuda")

    assert captured["device"] == "cuda"
    assert captured["compute_type"] == "float16"
    assert captured["model_size"] == "small"


def test_load_model_passes_custom_model_size(
    whisper_local_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-default model_size is forwarded to WhisperModel."""
    captured: dict[str, Any] = {}

    class _FakeWhisperModel:
        def __init__(self, model_size: str, device: str, compute_type: str) -> None:
            captured["model_size"] = model_size

    monkeypatch.setattr(whisper_local_module, "WhisperModel", _FakeWhisperModel)

    whisper_local_module._load_model(model_size="large-v3")

    assert captured["model_size"] == "large-v3"


def test_load_model_caches_instances(
    whisper_local_module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #87 regression: repeated calls with the same (model_size, device)
    must return the same instance without instantiating WhisperModel again."""
    calls: list[tuple[str, str]] = []

    class _FakeWhisperModel:
        def __init__(self, model_size: str, device: str, compute_type: str) -> None:
            calls.append((model_size, device))

    monkeypatch.setattr(whisper_local_module, "WhisperModel", _FakeWhisperModel)
    # Clear the module cache so this test starts from a known state.
    whisper_local_module._MODEL_CACHE.clear()

    first = whisper_local_module._load_model(model_size="base")
    second = whisper_local_module._load_model(model_size="base")
    third = whisper_local_module._load_model(model_size="small")

    assert first is second
    assert first is not third
    assert calls == [("base", "cpu"), ("small", "cpu")]


# ---------------------------------------------------------------------------
# transcribe_audio: full pipeline (mocked model)
# ---------------------------------------------------------------------------


def test_transcribe_audio_writes_transcript_json(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transcribe_audio writes a JSON file with the expected schema fields."""
    audio = tmp_path / "in.wav"
    audio.write_bytes(b"FAKE")
    output = tmp_path / "out" / "transcript.json"

    word = _make_word("hello", 0.0, 0.5, 0.95)
    segment = _make_segment(0, 0.0, 1.0, " hello ", [word])
    model = _make_fake_model([segment])

    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: model)

    result = whisper_local_module.transcribe_audio(audio, output)

    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_audio"] == str(audio)
    assert payload["language"] == "en"
    assert payload["language_probability"] == 0.99
    assert payload["word_timestamps"] is True
    assert isinstance(payload["segments"], list)
    assert isinstance(payload["words"], list)
    # The function also returns the in-memory dict. The on-disk file
    # round-trips it, so the two are equal but not the same object.
    assert result == payload


def test_transcribe_audio_flattens_words_into_top_level(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-segment words are flattened into the top-level words list."""
    words_a = [
        _make_word("hello", 0.0, 0.5, 0.9),
        _make_word("world", 0.5, 1.0, 0.85),
    ]
    words_b = [_make_word("second", 1.5, 2.0, 0.7)]
    seg_a = _make_segment(0, 0.0, 1.0, "hello world", words_a)
    seg_b = _make_segment(1, 1.5, 2.0, "second", words_b)
    model = _make_fake_model([seg_a, seg_b])

    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: model)

    output = tmp_path / "transcript.json"
    payload = whisper_local_module.transcribe_audio(tmp_path / "in.wav", output)

    # Each per-segment word also appears in the top-level list.
    assert len(payload["words"]) == 3
    assert [w["word"] for w in payload["words"]] == ["hello", "world", "second"]
    # And it's also in the per-segment list.
    assert len(payload["segments"][0]["words"]) == 2
    assert len(payload["segments"][1]["words"]) == 1


def test_transcribe_audio_strips_whitespace_on_text_and_words(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leading / trailing whitespace on text and words is stripped."""
    word = _make_word("  padded  ", 0.0, 0.5, 0.5)
    segment = _make_segment(0, 0.0, 1.0, "  hello  ", [word])
    model = _make_fake_model([segment])

    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: model)

    output = tmp_path / "transcript.json"
    payload = whisper_local_module.transcribe_audio(tmp_path / "in.wav", output)

    assert payload["segments"][0]["text"] == "hello"
    assert payload["words"][0]["word"] == "padded"


def test_transcribe_audio_handles_segment_without_words(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A segment with no words produces an empty words list and the segment is still recorded."""
    segment = _make_segment(0, 0.0, 1.0, "noise", words=None)
    model = _make_fake_model([segment])

    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: model)

    output = tmp_path / "transcript.json"
    payload = whisper_local_module.transcribe_audio(tmp_path / "in.wav", output)

    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["words"] == []
    assert payload["words"] == []


def test_transcribe_audio_empty_segments(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero segments → empty lists, but the file is still written."""
    model = _make_fake_model([])

    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: model)

    output = tmp_path / "transcript.json"
    payload = whisper_local_module.transcribe_audio(tmp_path / "in.wav", output)

    assert payload["segments"] == []
    assert payload["words"] == []
    assert output.exists()


def test_transcribe_audio_creates_parent_dirs(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The output's parent directory is created if it doesn't exist."""
    model = _make_fake_model([])
    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: model)

    nested_output = tmp_path / "deep" / "nested" / "transcript.json"
    whisper_local_module.transcribe_audio(tmp_path / "in.wav", nested_output)

    assert nested_output.exists()
    assert nested_output.parent.exists()


def test_transcribe_audio_forwards_kwargs_to_model(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transcribe() receives the language and word_timestamps kwargs."""
    captured: dict[str, Any] = {}

    class _FakeModel:
        def transcribe(
            self,
            audio: str,
            language: str | None = None,
            word_timestamps: bool = True,
            vad_filter: bool = True,
        ) -> tuple[Any, Any]:
            captured["audio"] = audio
            captured["language"] = language
            captured["word_timestamps"] = word_timestamps
            captured["vad_filter"] = vad_filter

            class _Info:
                language = "en"
                language_probability = 1.0

            return iter([]), _Info()

    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: _FakeModel())

    whisper_local_module.transcribe_audio(
        tmp_path / "in.wav",
        tmp_path / "out.json",
        model_size="base",
        language="fr",
        word_timestamps=False,
    )

    assert captured["language"] == "fr"
    assert captured["word_timestamps"] is False
    assert captured["vad_filter"] is True  # always on per the wrapper
    assert captured["audio"] == str(tmp_path / "in.wav")


def test_transcribe_audio_forwards_initial_prompt_and_vad_override(
    whisper_local_module: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#268: a caller can prime the decoder and turn the VAD off.

    Silero VAD dropped the final sentence of a synthetic narration ("Find
    yours free at Cosmic Sah-joo dot com.") and the word stream ended 3.9 s
    before the audio did, so everything after the last recognised word
    collapsed onto one timestamp. ``initial_prompt`` makes the tail decodable
    and ``vad_filter=False`` keeps it from being trimmed; both must reach
    ``model.transcribe`` verbatim.
    """
    captured: dict[str, Any] = {}

    class _FakeModel:
        def transcribe(
            self,
            audio: str,
            language: str | None = None,
            word_timestamps: bool = True,
            vad_filter: bool = True,
            initial_prompt: str | None = None,
        ) -> tuple[Any, Any]:
            captured["vad_filter"] = vad_filter
            captured["initial_prompt"] = initial_prompt

            class _Info:
                language = "en"
                language_probability = 1.0

            return iter([]), _Info()

    monkeypatch.setattr(whisper_local_module, "_load_model", lambda *a, **k: _FakeModel())

    whisper_local_module.transcribe_audio(
        tmp_path / "in.wav",
        tmp_path / "out.json",
        initial_prompt="Find yours free at Cosmic Sah-joo dot com.",
        vad_filter=False,
    )

    assert captured["initial_prompt"] == "Find yours free at Cosmic Sah-joo dot com."
    assert captured["vad_filter"] is False
