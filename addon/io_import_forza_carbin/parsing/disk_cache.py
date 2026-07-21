"""Shared on-disk cache helpers for zip extracts and DDS staging."""

from __future__ import annotations

import os
import shutil
import tempfile

# Soft cap for zipfs extracts (LRU trim after new writes).
ZIPFS_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def zipfs_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "forza_import", "zipfs")


def dds_cache_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "forza_import_dds")


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def dir_size_bytes(root: str) -> int:
    if not root or not os.path.isdir(root):
        return 0
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def clear_dir(root: str) -> int:
    """Delete everything under root. Returns bytes freed (approximate)."""
    if not root or not os.path.isdir(root):
        return 0
    freed = dir_size_bytes(root)
    try:
        shutil.rmtree(root, ignore_errors=True)
    except OSError:
        pass
    return freed


def trim_dir(root: str, max_bytes: int, protect: set[str] | None = None) -> int:
    """Delete oldest files (by mtime) until total size <= max_bytes. Returns bytes freed.

    Paths in ``protect`` are never deleted (e.g. the file just extracted).
    """
    if max_bytes < 0 or not root or not os.path.isdir(root):
        return 0
    protect_abs = {os.path.abspath(p) for p in (protect or ())}
    files: list[tuple[float, int, str]] = []
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, path))
                total += st.st_size
    except OSError:
        return 0
    if total <= max_bytes:
        return 0
    files.sort(key=lambda t: t[0])  # oldest first
    freed = 0
    for _mtime, size, path in files:
        if total <= max_bytes:
            break
        if os.path.abspath(path) in protect_abs:
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        freed += size
    # Drop empty directories left behind (keep the cache root itself)
    root_abs = os.path.abspath(root)
    try:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            if os.path.abspath(dirpath) == root_abs:
                continue
            if not dirnames and not filenames:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
    except OSError:
        pass
    return freed


def cache_summary() -> tuple[str, int, str, int]:
    """Return (zipfs_path, zipfs_bytes, dds_path, dds_bytes)."""
    z = zipfs_cache_dir()
    d = dds_cache_dir()
    return z, dir_size_bytes(z), d, dir_size_bytes(d)


def clear_all_caches() -> int:
    """Clear zipfs + DDS caches. Returns total bytes freed."""
    return clear_dir(zipfs_cache_dir()) + clear_dir(dds_cache_dir())


def touch_file(path: str) -> None:
    """Bump mtime so LRU trim prefers recently used extracts."""
    try:
        os.utime(path, None)
    except OSError:
        pass
