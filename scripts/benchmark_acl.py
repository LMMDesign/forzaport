#!/usr/bin/env python3
"""Compare legacy per-sample ACL decompress vs native bulk (development only).

Does not run during normal car import.

Usage:
  python scripts/benchmark_acl.py PATH\\to\\carclips.clipd
  python scripts/benchmark_acl.py PATH\\to\\carclips.clipd --repeat 20
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("io_import_forza_carbin", _pkg)
_parsing = types.ModuleType("io_import_forza_carbin.parsing")
_parsing.__path__ = [str(ROOT / "parsing")]
sys.modules.setdefault("io_import_forza_carbin.parsing", _parsing)

from io_import_forza_carbin.parsing import mojo_acl  # noqa: E402


def _pick_transform(clipd: Path) -> bytes:
    clips = mojo_acl.extract_acl_clips(clipd.read_bytes())
    if not clips:
        raise SystemExit(f"no ACLAnimationData in {clipd}")
    return max(clips, key=lambda c: len(c.transform) * max(c.num_samples, 1)).transform


def _time_legacy(transform: bytes, repeats: int) -> tuple[float, int, int]:
    lib = mojo_acl.load_acl_dll()
    _buf, nt, ns = mojo_acl._acl_info(lib, transform)
    t0 = time.perf_counter()
    for _ in range(repeats):
        mojo_acl._decompress_all_samples_legacy(transform)
    elapsed = time.perf_counter() - t0
    return elapsed / repeats, nt, ns


def _time_bulk(transform: bytes, repeats: int) -> tuple[float, int, int]:
    if not mojo_acl.acl_bulk_supported():
        raise SystemExit(
            "Loaded forza_acl.dll has no forza_acl_decompress_all — rebuild tools/acl first."
        )
    lib = mojo_acl.load_acl_dll()
    _buf, nt, ns = mojo_acl._acl_info(lib, transform)
    t0 = time.perf_counter()
    for _ in range(repeats):
        # Bypass lru_cache so each call measures native work.
        mojo_acl._decompress_all_samples_bulk(transform)
    elapsed = time.perf_counter() - t0
    return elapsed / repeats, nt, ns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clipd", type=Path, help="Path to a FH6 .clipd with ACL buffers")
    parser.add_argument("--repeat", type=int, default=12, help="Timed iterations per path")
    args = parser.parse_args(argv)
    if not args.clipd.is_file():
        raise SystemExit(f"not a file: {args.clipd}")

    transform = _pick_transform(args.clipd)
    legacy_s, nt, ns = _time_legacy(transform, args.repeat)
    bulk_s, nt2, ns2 = _time_bulk(transform, args.repeat)
    assert (nt, ns) == (nt2, ns2)

    # Equivalence smoke (one pass each).
    mojo_acl.decompress_all_samples.cache_clear()
    bulk = mojo_acl._decompress_all_samples_bulk(transform)
    legacy = mojo_acl._decompress_all_samples_legacy(transform)
    if bulk != legacy:
        # Allow float32 noise if any native path differs slightly.
        max_err = 0.0
        for bs, ls in zip(bulk, legacy):
            for bp, lp in zip(bs, ls):
                for a, b in zip(bp[0] + bp[1] + bp[2], lp[0] + lp[1] + lp[2]):
                    max_err = max(max_err, abs(a - b))
        if max_err > 1e-5:
            raise SystemExit(f"bulk/legacy mismatch max_err={max_err}")

    ratio = (legacy_s / bulk_s) if bulk_s > 0 else float("inf")
    print(f"clipd:   {args.clipd}")
    print(f"tracks:  {nt}")
    print(f"samples: {ns}")
    print(f"legacy:  {legacy_s * 1000:.3f} ms/call  ({ns} native sample calls)")
    print(f"bulk:    {bulk_s * 1000:.3f} ms/call  (1 native call)")
    print(f"speed-up:{ratio:.2f}x")
    print(f"bulk_api:{mojo_acl.acl_bulk_supported()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
