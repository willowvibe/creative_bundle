"""GPU capability detection and a process-local session lock.

The target machine has a 4 GB laptop GPU, so GPU work must be serialised: ASR
and any local generation (ComfyUI) should not be resident at the same time.
The lock here is process-local (a worker that runs one job at a time); it does
not coordinate across processes.

NVENC runs on dedicated silicon and does not need to take this lock.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

logger = structlog.get_logger(__name__)

_GPU_LOCK = threading.Lock()

# CTranslate2 links against these CUDA runtime libraries. A visible NVIDIA
# device is not enough: without the userspace libraries every CUDA model load
# dies with "libcublas.so.12 is not found or cannot be loaded", long after
# the probe below would have said "yes".
_CUDA_REQUIRED_LIBS = ("cublas", "cudart")


def _cuda_runtime_available() -> bool:
    """Return True when the CUDA runtime libraries CTranslate2 needs load.

    The driver can expose a device (and ``ctranslate2.get_cuda_device_count``
    can report it) while the CUDA userspace libraries are absent — for
    example on a box where only the NVIDIA *driver* is installed and the
    libraries live in the ``nvidia-*-cu12`` wheels. In that state a CUDA
    model load fails, so the libraries are probed directly with ``ctypes``.
    """
    import ctypes
    import ctypes.util
    import sys
    from pathlib import Path

    search_dirs: list[str] = []
    for base in sys.path:
        nvidia_dir = Path(base) / "nvidia"
        if nvidia_dir.is_dir():
            search_dirs.extend(str(p) for p in nvidia_dir.glob("*/lib"))

    for lib in _CUDA_REQUIRED_LIBS:
        path = ctypes.util.find_library(lib)
        if path is None:
            for directory in search_dirs:
                matches = sorted(Path(directory).glob(f"lib{lib}.so*"))
                if matches:
                    path = str(matches[-1])
                    break
        if path is None:
            logger.debug("cuda_runtime_library_missing", library=lib)
            return False
        try:
            ctypes.CDLL(path)
        except OSError as exc:
            logger.debug("cuda_runtime_library_unloadable", library=lib, error=str(exc))
            return False
    return True


def cuda_available() -> bool:
    """Return True when a CUDA device is usable for CTranslate2 inference.

    Reads ``VE_GPU`` first (``auto`` | ``cuda`` | ``cpu``), then probes
    CTranslate2. Any failure degrades to ``False`` so callers fall back to CPU.
    """
    override = os.getenv("VE_GPU", "auto").strip().lower()
    if override == "cpu":
        return False
    try:
        import ctranslate2

        device_count = int(ctranslate2.get_cuda_device_count())
        available = device_count > 0
    except Exception:  # noqa: BLE001 - probing must never raise
        available = False
    if available and not _cuda_runtime_available():
        # A device is visible but the CUDA runtime is not installed; loading
        # a model would raise "libcublas.so.12 is not found". Degrade to CPU
        # so the job still runs.
        logger.warning(
            "cuda_device_visible_but_runtime_missing",
            detail="CUDA userspace libraries are not loadable; using CPU",
        )
        available = False
    if override == "cuda" and not available:
        logger.warning("ve_gpu_cuda_requested_but_unavailable")
    return available


@contextmanager
def gpu_session(name: str = "gpu") -> Iterator[None]:
    """Serialise GPU-heavy work within this process."""
    acquired = _GPU_LOCK.acquire(timeout=float(os.getenv("VE_GPU_LOCK_TIMEOUT", "600")))
    if not acquired:
        raise TimeoutError(f"timed out waiting for GPU session: {name}")
    try:
        logger.debug("gpu_session_enter", name=name)
        yield
    finally:
        _GPU_LOCK.release()
        logger.debug("gpu_session_exit", name=name)
