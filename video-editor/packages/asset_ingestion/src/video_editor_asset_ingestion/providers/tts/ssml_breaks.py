"""SSML break-tag handling for TTS providers.

Kokoro and OpenAI do not natively support ``<break>`` tags. This module
extracts break durations, splits the text into chunks, and concatenates the
synthesized chunks with silence pads so the final audio honors the requested
pauses. ElevenLabs supports ``<break>`` inline, so the ElevenLabs provider
passes the tags through (optionally wrapping the text in ``<speak>``).
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import run_ffmpeg

if TYPE_CHECKING:
    from video_editor_asset_ingestion.models import AssetRequest, AssetResult
    from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

_BREAK_RE = re.compile(r"<break\s+time=[\"']?([^\"'\s>]+)[\"']?\s*/?>")


@dataclass
class TextChunk:
    """A piece of text followed by an optional pause."""

    text: str
    pause_after_ms: int = 0


def _parse_break_time(value: str) -> int:
    """Parse ``500ms``, ``1s``, ``1.5s`` into milliseconds."""
    value = value.strip().lower()
    if value.endswith("ms"):
        return int(value[:-2])
    if value.endswith("s"):
        return int(float(value[:-1]) * 1000)
    return int(value)


def strip_unsupported_ssml(text: str) -> str:
    """Remove remaining XML-like tags from ``text``.

    This is applied to each text chunk after ``<break>`` tags have been
    extracted, so any stray ``<speak>``, ``<emphasis>``, etc. are
    stripped before being sent to a provider that does not understand SSML.
    """
    return re.sub(r"<[^>]+>", "", text)


def extract_breaks(text: str) -> list[TextChunk]:
    """Split ``text`` at ``<break>`` tags and record pause durations.

    Returns a list of :class:`TextChunk` objects. Each chunk contains the text
    preceding a break and the pause duration that should follow it. The final
    chunk carries a zero-length pause unless the input ends with a break (in
    which case a trailing empty chunk is omitted).
    """
    chunks: list[TextChunk] = []
    cursor = 0
    current_text = ""

    for match in _BREAK_RE.finditer(text):
        before = text[cursor : match.start()]
        current_text += before
        pause_ms = _parse_break_time(match.group(1))
        cleaned = strip_unsupported_ssml(current_text)
        if cleaned.strip():
            chunks.append(TextChunk(cleaned, pause_ms))
        elif pause_ms > 0:
            # Text was only whitespace/tags but we still need the pause.
            # Append to the previous chunk if possible, otherwise create an
            # empty chunk that will be consumed as a leading pause.
            if chunks:
                chunks[-1].pause_after_ms += pause_ms
            else:
                chunks.append(TextChunk("", pause_ms))
        current_text = ""
        cursor = match.end()

    remaining = text[cursor:]
    if remaining:
        current_text += remaining
    if current_text.strip():
        chunks.append(TextChunk(strip_unsupported_ssml(current_text), 0))
    elif not chunks:
        # Degenerate case: input contained only breaks or was empty.
        chunks.append(TextChunk("", 0))

    return chunks


def has_break_tags(text: str) -> bool:
    """Return True when ``text`` contains at least one ``<break>`` tag."""
    return bool(_BREAK_RE.search(text))


def wrap_for_elevenlabs(text: str) -> str:
    """Wrap ``text`` in ``<speak>`` if it contains SSML but is not already wrapped."""
    stripped = text.strip()
    if stripped.startswith("<speak") and stripped.endswith("</speak>"):
        return stripped
    if has_break_tags(stripped) or "<" in stripped and ">" in stripped:
        return f"<speak>{stripped}</speak>"
    return stripped


def _concat_audio_with_pauses(
    segments: list[tuple[Path, int]],
    output_path: Path,
    sample_rate: int = 44100,
) -> None:
    """Concatenate audio files with silence pads between them.

    Each tuple is ``(audio_path, pause_after_ms)``. The pause after the last
    segment is ignored.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not segments:
        raise ValueError("No audio segments to concatenate")

    inputs: list[str] = []
    filter_parts: list[str] = []
    labels: list[str] = []
    input_idx = 0

    for i, (audio_path, pause_ms) in enumerate(segments):
        inputs.extend(["-i", str(audio_path)])
        # Normalize each audio input to a common sample rate and format so the
        # concat filter can chain them without format errors.
        out_label = f"seg{i}"
        filter_parts.append(
            f"[{input_idx}:a]aresample={sample_rate},aformat=sample_fmts=fltp[{out_label}]"
        )
        labels.append(f"[{out_label}]")
        input_idx += 1

        if pause_ms > 0 and i < len(segments) - 1:
            pause_sec = pause_ms / 1000.0
            inputs.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    str(pause_sec),
                    "-i",
                    f"anullsrc=r={sample_rate}:cl=mono",
                ]
            )
            silence_label = f"sil{i}"
            filter_parts.append(f"[{input_idx}:a]aformat=sample_fmts=fltp[{silence_label}]")
            labels.append(f"[{silence_label}]")
            input_idx += 1

    concat_filter = f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]"
    filter_parts.append(concat_filter)

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[out]",
        str(output_path),
    ]
    run_ffmpeg(cmd)


async def synthesize_with_breaks(
    provider: GenerativeProvider,
    request: AssetRequest,
    output_path: Path,
) -> AssetResult:
    """Synthesize ``request.prompt`` using ``provider``, honoring ``<break>`` tags.

    The ``provider`` must already be the concrete TTS implementation (Kokoro,
    OpenAI, Sarvam, etc.). This helper is *not* used by ElevenLabs because it
    supports inline ``<break>`` tags natively.
    """
    from video_editor_asset_ingestion.models import AssetResult, AssetType

    chunks = extract_breaks(request.prompt)

    # Fast path: a single chunk with no pause means we can delegate directly.
    if len(chunks) == 1 and chunks[0].pause_after_ms == 0:
        result = await provider.generate(
            request.model_copy(update={"prompt": chunks[0].text}), output_path
        )
        return result

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        segment_paths: list[tuple[Path, int]] = []
        total_duration_ms = 0

        for i, chunk in enumerate(chunks):
            if not chunk.text.strip():
                # No speech for this chunk — just a pause. Record it against
                # the previous segment if one exists, otherwise it becomes a
                # leading pause (rare, but possible with leading breaks).
                if segment_paths:
                    segment_paths[-1] = (
                        segment_paths[-1][0],
                        segment_paths[-1][1] + chunk.pause_after_ms,
                    )
                continue

            chunk_path = tmp / f"chunk_{i}.wav"
            chunk_request = request.model_copy(update={"prompt": chunk.text})
            chunk_result = await provider.generate(chunk_request, chunk_path)
            segment_paths.append((chunk_path, chunk.pause_after_ms))
            total_duration_ms += chunk_result.duration_ms

        # Add all pauses to the total duration.
        total_duration_ms += sum(pause for _, pause in segment_paths)

        if not segment_paths:
            raise RuntimeError("SSML break processing produced no audio segments")

        _concat_audio_with_pauses(segment_paths, output_path)
        info = probe_media(output_path)
        probed_duration_ms = info.duration_ms() if info.duration is not None else total_duration_ms

    logger.info(
        "ssml_breaks_merged",
        job_id=request.job_id,
        asset_id=request.asset_id,
        chunks=len(segment_paths),
        total_duration_ms=probed_duration_ms,
    )

    return AssetResult(
        asset_id=request.asset_id,
        asset_type=AssetType.TTS,
        local_path=str(output_path),
        scene_id=request.scene_id,
        duration_ms=probed_duration_ms,
        metadata={
            "provider": provider.provider_name,
            "ssml_chunks": len(chunks),
            **request.params,
        },
    )
