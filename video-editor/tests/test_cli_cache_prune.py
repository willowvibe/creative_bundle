"""Tests for ve cache-prune command."""

import os
import time

from video_editor_cli.commands.cache_prune import prune_cache


class TestPruneCache:
    def test_empty_cache_dir(self, tmp_path):
        """Pruning an empty/nonexistent dir returns zeros."""
        result = prune_cache(cache_dir=tmp_path / "nonexistent")
        assert result == {"removed": 0, "kept": 0, "freed_bytes": 0}

    def test_keeps_recent_files(self, tmp_path):
        """Files newer than max_age_days are kept."""
        cache = tmp_path / "cache"
        cache.mkdir()
        recent = cache / "recent.json"
        recent.write_text("data")
        # File just created should be kept
        result = prune_cache(cache_dir=cache, max_age_days=30)
        assert result["kept"] >= 1
        assert result["removed"] == 0
        assert recent.exists()

    def test_removes_old_files(self, tmp_path):
        """Files older than max_age_days are removed."""
        cache = tmp_path / "cache"
        cache.mkdir()
        old = cache / "old.json"
        old.write_text("data")
        # Set mtime to 60 days ago
        old_mtime = time.time() - (60 * 86400)
        os.utime(old, (old_mtime, old_mtime))
        result = prune_cache(cache_dir=cache, max_age_days=30)
        assert result["removed"] >= 1
        assert not old.exists()

    def test_dry_run_does_not_delete(self, tmp_path):
        """Dry run reports but does not remove files."""
        cache = tmp_path / "cache"
        cache.mkdir()
        old = cache / "old.json"
        old.write_text("data")
        old_mtime = time.time() - (60 * 86400)
        os.utime(old, (old_mtime, old_mtime))
        result = prune_cache(cache_dir=cache, max_age_days=30, dry_run=True)
        assert result["removed"] >= 1
        assert old.exists()  # Not actually deleted
