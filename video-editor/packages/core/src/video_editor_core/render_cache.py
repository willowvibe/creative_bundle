"""Segment-level render cache for partial re-rendering.

The cache key is a content hash derived from the source media, segment bounds,
and the operation/parameters applied to that segment. This makes the cache
safe across jobs and robust against parameter changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_HASH_CACHE_MAX = 256


def _file_hash(path: Path) -> str:
    """Return a blake2b hex digest of a file's contents.

    The digest is memoised by ``(path, size, mtime_ns)`` so repeated
    ``segment_cache_key`` calls for the same source do not re-read a
    multi-gigabyte file once per segment.
    """
    stat = path.stat()
    cache_key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _HASH_CACHE.get(cache_key)
    if cached is not None:
        return cached

    hasher = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()

    if len(_HASH_CACHE) >= _HASH_CACHE_MAX:
        _HASH_CACHE.pop(next(iter(_HASH_CACHE)))
    _HASH_CACHE[cache_key] = digest
    return digest


def _stable_json(value: Any) -> bytes:
    """Canonical JSON representation for hashing (sorted keys, no whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def segment_cache_key(
    source_path: Path | str,
    start: float,
    end: float,
    operation: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Build a deterministic cache key for a segment render operation.

    Raises:
        FileNotFoundError: If ``source_path`` does not exist. A missing
            source used to silently hash to the literal string ``"missing"``,
            which meant two different missing files would share the same
            cache key and could return each other's stale renders.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"segment cache source does not exist: {source_path}")
    file_digest = _file_hash(source_path)
    hasher = hashlib.blake2b(digest_size=16)
    hasher.update(file_digest.encode())
    hasher.update(
        _stable_json(
            {
                "operation": operation,
                "start": start,
                "end": end,
                "params": params or {},
            }
        )
    )
    return hasher.hexdigest()


_DEFAULT_MAX_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB


class SegmentCache:
    """Filesystem-backed cache for rendered segments."""

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def _cache_path(self, key: str) -> Path:
        # Two-level directory sharding to avoid huge flat directories.
        return self.cache_dir / key[:2] / key

    def _cache_size_bytes(self) -> int:
        """Return the total byte size of all cached segment files."""
        total = 0
        for path in self.cache_dir.rglob("*"):
            if path.is_file() and not path.name.endswith(".tmp"):
                total += path.stat().st_size
        return total

    def _evict_if_needed(self, required_bytes: int) -> None:
        """Evict oldest entries until the new entry would fit within ``max_bytes``.

        Entries are ordered by mtime; the least-recently-written files are
        removed first. This keeps the cache bounded even under heavy
        concurrent use.
        """
        while True:
            current = self._cache_size_bytes()
            if current + required_bytes <= self.max_bytes:
                return
            files = [
                p for p in self.cache_dir.rglob("*") if p.is_file() and not p.name.endswith(".tmp")
            ]
            if not files:
                return
            files.sort(key=lambda p: p.stat().st_mtime)
            victim = files[0]
            victim_size = victim.stat().st_size
            victim.unlink()
            logger.info("cache_evict", path=str(victim), size=victim_size)

    def get(self, key: str) -> Path | None:
        """Return a cached rendered segment if it exists."""
        path = self._cache_path(key)
        if path.exists():
            logger.info("cache_hit", key=key, path=str(path))
            return path
        logger.info("cache_miss", key=key)
        return None

    def put(self, key: str, rendered_path: Path | str) -> Path:
        """Store a rendered segment in the cache atomically.

        Writes the rendered bytes to a uniquely-named temp file, evicts old
        entries if the cache would exceed ``max_bytes``, fsyncs the file, then
        ``os.replace``s it into place. A reader that calls ``get()`` will see
        either the old entry or the new one — never a half-written MP4 that
        would make FFmpeg error. The unique temp name also prevents two
        concurrent puts to the same key from corrupting each other.
        """
        rendered_path = Path(rendered_path)
        dest = self._cache_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._evict_if_needed(rendered_path.stat().st_size)
        tmp = dest.with_suffix(f"{dest.suffix}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        # Use copyfile rather than move so we don't partially clobber dest
        # if the rename target is on a different device.
        shutil.copyfile(rendered_path, tmp)
        # Flush + fsync so the bytes survive a power loss before the rename.
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, dest)
        logger.info("cache_put", key=key, path=str(dest))
        return dest

    def invalidate(self, key: str | None = None) -> None:
        """Delete a single cached entry or the entire cache directory."""
        if key is None:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info("cache_cleared", cache_dir=str(self.cache_dir))
            return
        path = self._cache_path(key)
        if path.exists():
            path.unlink()
            logger.info("cache_invalidate", key=key)
