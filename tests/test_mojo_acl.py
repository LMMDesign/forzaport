"""Unit tests for FH6 ACL decompress helpers (no Blender).

Most cases mock the native helper so CI/local runs do not need a rebuilt DLL.
Optional integration against a real clip is documented at the bottom.
"""
from __future__ import annotations

import ctypes
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)
_parsing = types.ModuleType("io_import_forza_carbin.parsing")
_parsing.__path__ = [os.path.join(_ROOT, "parsing")]
sys.modules.setdefault("io_import_forza_carbin.parsing", _parsing)

from io_import_forza_carbin.parsing import mojo_acl  # noqa: E402


def _pose(track: int, sample: int):
    # Deterministic unique floats per (sample, track).
    base = float(sample * 100 + track)
    rot = (base + 0.1, base + 0.2, base + 0.3, base + 0.4)
    tr = (base + 0.5, base + 0.6, base + 0.7)
    sc = (1.0, 1.0, 1.0)
    return rot, tr, sc


def _fill_sample(
    buf,
    *,
    num_tracks: int,
    sample_index: int = 0,
    buffer_sample_index: int | None = None,
):
    """Write one logical sample into ``buf``.

    ``sample_index`` chooses pose values. ``buffer_sample_index`` chooses the
    destination slot (defaults to ``sample_index``). Single-sample native
    outputs should pass ``buffer_sample_index=0``.
    """
    slot = sample_index if buffer_sample_index is None else buffer_sample_index
    for ti in range(num_tracks):
        rot, tr, sc = _pose(ti, sample_index)
        base = (slot * num_tracks + ti) * 12
        buf[base + 0], buf[base + 1], buf[base + 2], buf[base + 3] = rot
        buf[base + 4], buf[base + 5], buf[base + 6] = tr
        buf[base + 7] = 0.0
        buf[base + 8], buf[base + 9], buf[base + 10] = sc
        buf[base + 11] = 0.0


class FakeAclLib:
    """ctypes-like stand-in for forza_acl.dll exports."""

    def __init__(self, *, num_tracks: int = 2, num_samples: int = 4, bulk: bool = True):
        self.num_tracks = num_tracks
        self.num_samples = num_samples
        self.bulk = bulk
        self.info_calls = 0
        self.sample_calls = 0
        self.bulk_calls = 0
        self.forza_acl_info = self._info
        self.forza_acl_decompress_sample = self._sample
        if bulk:
            self.forza_acl_decompress_all = self._all

    def _info(self, _buf, _size, out_tracks, out_samples, out_rate, out_dur, out_ver):
        self.info_calls += 1
        out_tracks._obj.value = self.num_tracks
        out_samples._obj.value = self.num_samples
        out_rate._obj.value = 30.0
        out_dur._obj.value = (self.num_samples - 1) / 30.0
        out_ver._obj.value = 0
        return 0

    def _sample(self, _buf, _size, sample_index, out, capacity, out_tracks):
        self.sample_calls += 1
        si = self.num_samples - 1 if int(sample_index) < 0 else int(sample_index)
        if si < 0 or si >= self.num_samples:
            return -5
        needed = self.num_tracks * 12
        if int(capacity) < needed:
            return -4
        out_tracks._obj.value = self.num_tracks
        # Write into the ctypes array as sample 0 of a 1-sample buffer.
        tmp = (ctypes.c_float * needed)()
        _fill_sample(
            tmp,
            num_tracks=self.num_tracks,
            sample_index=si,
            buffer_sample_index=0,
        )
        for i in range(needed):
            out[i] = tmp[i]
        return 0

    def _all(self, _buf, _size, out, capacity, out_tracks, out_samples):
        self.bulk_calls += 1
        needed = self.num_samples * self.num_tracks * 12
        if int(capacity) < needed:
            return -4
        out_tracks._obj.value = self.num_tracks
        out_samples._obj.value = self.num_samples
        for si in range(self.num_samples):
            _fill_sample(out, num_tracks=self.num_tracks, sample_index=si)
        return 0


class DecodeBufferTests(unittest.TestCase):
    def test_decode_pose_buffer_layout(self):
        nt, ns = 2, 3
        values = (ctypes.c_float * (ns * nt * 12))()
        for si in range(ns):
            _fill_sample(values, num_tracks=nt, sample_index=si)
        decoded = mojo_acl._decode_pose_buffer(values, num_tracks=nt, num_samples=ns)
        self.assertEqual(len(decoded), ns)
        self.assertEqual(len(decoded[0]), nt)
        self._assert_pose_close(decoded[1][0], _pose(0, 1))
        self._assert_pose_close(decoded[2][1], _pose(1, 2))

    def _assert_pose_close(self, actual, expected):
        for a_part, e_part in zip(actual, expected):
            for a, e in zip(a_part, e_part):
                self.assertAlmostEqual(a, e, places=4)


class BulkPathTests(unittest.TestCase):
    def setUp(self):
        mojo_acl.decompress_all_samples.cache_clear()
        mojo_acl._dll = None
        mojo_acl._dll_error = None
        mojo_acl._dll_has_bulk = None
        mojo_acl._bulk_native_calls = 0

    def tearDown(self):
        mojo_acl.decompress_all_samples.cache_clear()
        mojo_acl._dll = None
        mojo_acl._dll_error = None
        mojo_acl._dll_has_bulk = None
        mojo_acl._bulk_native_calls = 0

    def _install(self, fake: FakeAclLib):
        mojo_acl._dll = fake
        mojo_acl._dll_has_bulk = fake.bulk
        mojo_acl._dll_error = None

    def test_bulk_matches_legacy_sample_loop(self):
        fake = FakeAclLib(num_tracks=3, num_samples=5, bulk=True)
        self._install(fake)
        transform = b"acl-transform-fixture"
        bulk = mojo_acl.decompress_all_samples(transform)
        # Build legacy expectation from single-sample API (same fake).
        legacy = tuple(
            tuple(mojo_acl.decompress_sample(transform, i)) for i in range(fake.num_samples)
        )
        self.assertEqual(len(bulk), len(legacy))
        self.assertEqual(len(bulk[0]), len(legacy[0]))
        self.assertEqual(bulk, legacy)
        self.assertEqual(fake.bulk_calls, 1)

    def test_first_and_last_sample_equivalence(self):
        fake = FakeAclLib(num_tracks=2, num_samples=8, bulk=True)
        self._install(fake)
        transform = b"acl-endpoints"
        bulk = mojo_acl.decompress_all_samples(transform)
        first = mojo_acl.decompress_sample(transform, 0)
        last = mojo_acl.decompress_sample(transform, -1)
        self.assertEqual(list(bulk[0]), first)
        self.assertEqual(list(bulk[-1]), last)

    def test_cache_skips_second_native_bulk_call(self):
        fake = FakeAclLib(num_tracks=2, num_samples=4, bulk=True)
        self._install(fake)
        transform = b"cached-acl"
        before = mojo_acl._bulk_native_calls
        a = mojo_acl.decompress_all_samples(transform)
        mid_calls = mojo_acl._bulk_native_calls
        # Equal content in a distinct bytes object must hit the same cache key.
        same_content = bytes(bytearray(transform))
        self.assertIsNot(transform, same_content)
        b = mojo_acl.decompress_all_samples(same_content)
        after_calls = mojo_acl._bulk_native_calls
        self.assertIs(a, b)
        self.assertEqual(mid_calls, before + 1)
        self.assertEqual(after_calls, mid_calls)
        self.assertEqual(fake.bulk_calls, 1)

    def test_legacy_dll_fallback_without_bulk_export(self):
        fake = FakeAclLib(num_tracks=2, num_samples=3, bulk=False)
        self._install(fake)
        self.assertFalse(hasattr(fake, "forza_acl_decompress_all"))
        transform = b"legacy-acl"
        # load_acl_dll path with older DLL: bind without bulk.
        with mock.patch.object(mojo_acl, "_dll_candidates", return_value=[]):
            # Directly exercise legacy decompress path.
            result = mojo_acl._decompress_all_samples_legacy(transform)
        self.assertEqual(len(result), 3)
        self.assertEqual(len(result[0]), 2)
        self.assertEqual(fake.sample_calls, 3)
        self.assertEqual(fake.bulk_calls, 0)
        # Public entry uses fallback when bulk flag is false.
        mojo_acl.decompress_all_samples.cache_clear()
        public = mojo_acl.decompress_all_samples(transform)
        self.assertEqual(public, result)

    def test_bind_exports_tolerates_missing_bulk(self):
        lib = SimpleNamespace(
            forza_acl_info=lambda *a: 0,
            forza_acl_decompress_sample=lambda *a: 0,
        )
        has_bulk = mojo_acl._bind_acl_exports(lib)
        self.assertFalse(has_bulk)

    def test_malformed_info_raises(self):
        class BadLib(FakeAclLib):
            def _info(self, *_a, **_k):
                self.info_calls += 1
                return -1

        fake = BadLib(bulk=True)
        self._install(fake)
        with self.assertRaises(RuntimeError) as ctx:
            mojo_acl.decompress_all_samples(b"bad")
        self.assertIn("forza_acl_info failed", str(ctx.exception))

    def test_invalid_dimensions_raise_before_huge_alloc(self):
        with self.assertRaises(RuntimeError) as ctx:
            mojo_acl._validate_acl_dimensions(0, 10)
        self.assertIn("invalid dimensions", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            mojo_acl._validate_acl_dimensions(mojo_acl._MAX_ACL_TRACKS + 1, 2)


class TrackLookupTests(unittest.TestCase):
    def test_duplicate_names_prefer_first_index(self):
        names = ["boneA", "boneB", "boneA", None]
        track_index_by_name: dict[str, int] = {}
        for index, name in enumerate(names):
            if name is not None:
                track_index_by_name.setdefault(name, index)
        self.assertEqual(track_index_by_name["boneA"], 0)
        self.assertEqual(track_index_by_name["boneB"], 1)
        self.assertEqual(names.index("boneA"), track_index_by_name["boneA"])


@unittest.skipUnless(
    os.environ.get("FORZA_ACL_INTEGRATION") == "1",
    "Set FORZA_ACL_INTEGRATION=1 and FORZA_ACL_CLIPD to a local .clipd for live DLL tests.",
)
class OptionalNativeIntegrationTests(unittest.TestCase):
    """Optional: compare bulk vs per-sample on a locally supplied clipd.

    Example:
      set FORZA_ACL_INTEGRATION=1
      set FORZA_ACL_CLIPD=H:\\path\\to\\carclips.clipd
      python -m unittest io_import_forza_carbin.tests.test_mojo_acl
    """

    def test_live_bulk_equals_legacy(self):
        clipd = os.environ.get("FORZA_ACL_CLIPD")
        self.assertTrue(clipd and os.path.isfile(clipd), "FORZA_ACL_CLIPD missing")
        with open(clipd, "rb") as stream:
            data = stream.read()
        clips = mojo_acl.extract_acl_clips(data)
        self.assertTrue(clips, "no ACL clips in clipd")
        transform = max(clips, key=lambda c: len(c.transform)).transform
        mojo_acl.decompress_all_samples.cache_clear()
        bulk = mojo_acl.decompress_all_samples(transform)
        legacy = tuple(
            tuple(mojo_acl.decompress_sample(transform, i)) for i in range(len(bulk))
        )
        self.assertEqual(len(bulk), len(legacy))
        for s_i, (b_sample, l_sample) in enumerate(zip(bulk, legacy)):
            self.assertEqual(len(b_sample), len(l_sample), f"sample {s_i}")
            for t_i, (bp, lp) in enumerate(zip(b_sample, l_sample)):
                for a, b in zip(bp[0] + bp[1] + bp[2], lp[0] + lp[1] + lp[2]):
                    self.assertAlmostEqual(a, b, places=5, msg=f"s{s_i}t{t_i}")


if __name__ == "__main__":
    unittest.main()
