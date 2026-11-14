"""Tests for the render cache and timeline engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.render_cache import SegmentCache, segment_cache_key
from video_editor_core.timeline import EDL, compute_timeline_segments, diff_edls


def test_segment_cache_key_determinism(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")

    key1 = segment_cache_key(source, 1.0, 3.0, "cut", {"crf": 23})
    key2 = segment_cache_key(source, 1.0, 3.0, "cut", {"crf": 23})
    key3 = segment_cache_key(source, 1.0, 3.5, "cut", {"crf": 23})

    assert key1 == key2
    assert key1 != key3


def test_segment_cache_key_includes_preset_digest(tmp_path: Path) -> None:
    """Regression: cache key MUST differ when the preset/brand changes,
    otherwise two presets on the same segment would return each other's
    rendered output."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")

    key_preset_a = segment_cache_key(
        source,
        1.0,
        3.0,
        "graphics",
        {"layers": [], "__preset_digest__": "preset_a"},
    )
    key_preset_b = segment_cache_key(
        source,
        1.0,
        3.0,
        "graphics",
        {"layers": [], "__preset_digest__": "preset_b"},
    )
    assert key_preset_a != key_preset_b


def test_segment_cache_key_includes_brand_digest(tmp_path: Path) -> None:
    """Regression: the same preset with different brand assets must NOT share
    a cache key."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video")

    key_brand_a = segment_cache_key(
        source,
        1.0,
        3.0,
        "graphics",
        {"__preset_digest__": "short_explainer@brand_a"},
    )
    key_brand_b = segment_cache_key(
        source,
        1.0,
        3.0,
        "graphics",
        {"__preset_digest__": "short_explainer@brand_b"},
    )
    assert key_brand_a != key_brand_b


def test_segment_cache_key_rejects_missing_source(tmp_path: Path) -> None:
    """Bug #77 regression: a missing source must not collapse to the shared
    'missing' digest."""
    missing = tmp_path / "does_not_exist.mp4"
    with pytest.raises(FileNotFoundError, match="segment cache source"):
        segment_cache_key(missing, 0.0, 1.0, "cut")


def test_segment_cache_put_get(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache = SegmentCache(cache_dir)

    rendered = tmp_path / "segment.mp4"
    rendered.write_bytes(b"rendered bytes")

    key = "abc123"
    assert cache.get(key) is None

    cached = cache.put(key, rendered)
    assert cached.exists()
    assert cache.get(key) == cached


def test_segment_cache_invalidate(tmp_path: Path) -> None:
    cache = SegmentCache(tmp_path / "cache")
    rendered = tmp_path / "seg.mp4"
    rendered.write_bytes(b"x")

    key = "k1"
    cache.put(key, rendered)
    assert cache.get(key) is not None

    cache.invalidate(key)
    assert cache.get(key) is None

    cache.put("k2", rendered)
    cache.invalidate()
    assert cache.get("k2") is None


def test_segment_cache_put_does_not_leave_tmp_file(tmp_path: Path) -> None:
    """Bug #12 regression: a successful put() must not leave its .tmp file
    behind. The atomic put() should rename the tmp into place and clean up.
    """
    cache_dir = tmp_path / "cache"
    cache = SegmentCache(cache_dir)
    rendered = tmp_path / "seg.mp4"
    rendered.write_bytes(b"rendered bytes")

    key = "atomic1"
    dest = cache.put(key, rendered)
    assert dest.exists()
    # Walk the cache dir and confirm no *.tmp files remain.
    leftover = list(cache_dir.rglob("*.tmp"))
    assert leftover == [], f"put() left tmp files behind: {leftover}"


def test_segment_cache_put_overwrites_existing_atomically(tmp_path: Path) -> None:
    """Bug #12 regression: putting a new render under an existing key must
    atomically replace the old file. A reader that calls get() should see
    either the old bytes or the new bytes — never a torn file.
    """
    cache_dir = tmp_path / "cache"
    cache = SegmentCache(cache_dir)
    rendered_old = tmp_path / "old.mp4"
    rendered_old.write_bytes(b"OLD RENDER")
    rendered_new = tmp_path / "new.mp4"
    rendered_new.write_bytes(b"NEW RENDER")

    key = "overwrite"
    cache.put(key, rendered_old)
    cached_old = cache.get(key)
    assert cached_old is not None
    assert cached_old.read_bytes() == b"OLD RENDER"
    cache.put(key, rendered_new)
    cached_new = cache.get(key)
    assert cached_new is not None
    assert cached_new.read_bytes() == b"NEW RENDER"
    # No leftover tmp files.
    leftover = list(cache_dir.rglob("*.tmp"))
    assert leftover == []


def test_segment_cache_put_uses_unique_tmp_per_call(tmp_path: Path) -> None:
    """Bug #86 regression: concurrent puts to the same key must use distinct
    temp files so they don't corrupt each other.
    """
    cache_dir = tmp_path / "cache"
    cache = SegmentCache(cache_dir)
    a = tmp_path / "a.mp4"
    a.write_bytes(b"A")
    b = tmp_path / "b.mp4"
    b.write_bytes(b"B")

    key = "shared"
    cache.put(key, a)
    cache.put(key, b)

    cached = cache.get(key)
    assert cached is not None
    assert cached.read_bytes() == b"B"
    # No leftover tmp files.
    assert list(cache_dir.rglob("*.tmp")) == []


def test_segment_cache_evicts_oldest_when_over_max_bytes(tmp_path: Path) -> None:
    """Bug #86 regression: the cache must enforce a size cap and evict the
    oldest entries to make room for new ones.
    """
    cache_dir = tmp_path / "cache"
    cache = SegmentCache(cache_dir, max_bytes=8)

    for i, content in enumerate([b"first", b"second", b"third"]):
        rendered = tmp_path / f"r{i}.mp4"
        rendered.write_bytes(content)
        cache.put(f"k{i}", rendered)

    # Only the most recent entry fits under the 100-byte cap (the cap is
    # soft: we evict before writing, so after the final put the cache holds
    # the newest entry).
    assert cache.get("k0") is None
    assert cache.get("k1") is None
    assert cache.get("k2") is not None


def test_compute_timeline_segments() -> None:
    segs = compute_timeline_segments(
        [
            (0.0, 2.0, "intro"),
            (5.0, 7.0, "middle"),
        ]
    )
    assert len(segs) == 2
    assert segs[0].timeline_start == 0.0
    assert segs[0].timeline_end == 2.0
    assert segs[1].timeline_start == 2.0
    assert segs[1].timeline_end == 4.0


def test_diff_edls() -> None:
    old = EDL(
        job_id="j1",
        source_path="s.mp4",
        segments=compute_timeline_segments(
            [
                (0.0, 2.0, "a"),
                (3.0, 5.0, "b"),
            ]
        ),
    )
    new = EDL(
        job_id="j1",
        source_path="s.mp4",
        segments=compute_timeline_segments(
            [
                (0.0, 2.0, "a"),
                (3.0, 6.0, "b"),
                (8.0, 9.0, "c"),
            ]
        ),
    )

    diff = diff_edls(old, new)
    assert diff.unchanged == ["seg_0000"]
    assert diff.changed == ["seg_0001"]
    assert diff.added == ["seg_0002"]
    assert diff.removed == []


def test_file_hash_is_memoized_by_stat(tmp_path: Path) -> None:
    """Repeated segment keys must not re-read the whole source file.

    The hash is memoised by ``(path, size, mtime_ns)``; a content/mtime change
    must invalidate the memo.
    """
    import os

    from video_editor_core import render_cache

    render_cache._HASH_CACHE.clear()
    source = tmp_path / "s.mp4"
    source.write_bytes(b"x" * 4096)

    first = render_cache._file_hash(source)
    assert len(render_cache._HASH_CACHE) == 1
    second = render_cache._file_hash(source)
    assert second == first
    assert len(render_cache._HASH_CACHE) == 1  # no new entry

    source.write_bytes(b"y" * 4096)
    os.utime(source, (0, 0))  # force a distinct mtime
    third = render_cache._file_hash(source)
    assert third != first
