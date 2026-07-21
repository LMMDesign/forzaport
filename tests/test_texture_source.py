"""Tests for typed texture source resolution and zip indexing."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import time
import unittest
import zipfile

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "addon", "io_import_forza_carbin"))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.texture_source import (  # noqa: E402
    TextureSourceFailure,
    TextureSourceKind,
    TexturePathError,
    ResolvedTextureSource,
    canonicalize_game_path,
    resolve_texture_source,
)
from io_import_forza_carbin.parsing.paths import GamePathResolver  # noqa: E402
from io_import_forza_carbin.parsing.zipfs import ZipAssetStore  # noqa: E402

SHARED_SWATCH = (
    "GAME:\\Media\\_library\\textures\\swatches\\defaultshader_diff_abc.swatchbin"
)
CARS_SWATCH = "GAME:\\Media\\cars\\_library\\textures\\pack\\car_badge.swatchbin"
LOOSE_SWATCH = "GAME:\\Media\\_library\\textures\\swatches\\loose_diff.swatchbin"
MISSING_SWATCH = (
    "GAME:\\Media\\_library\\textures\\swatches\\does_not_exist.swatchbin"
)


class CanonicalPathTests(unittest.TestCase):
    def test_game_prefix_and_separators(self):
        a = canonicalize_game_path(
            "GAME:\\Media\\_library\\textures\\swatches\\a.swatchbin"
        )
        b = canonicalize_game_path(
            "GAME:/Media/_library/textures/swatches/a.swatchbin"
        )
        c = canonicalize_game_path(
            "GAME:\\\\Media\\\\_library\\\\textures\\\\swatches\\\\a.swatchbin"
        )
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertTrue(a.startswith("media\\_library\\textures\\"))

    def test_reject_traversal(self):
        with self.assertRaises(TexturePathError):
            canonicalize_game_path(
                "GAME:\\Media\\_library\\textures\\..\\secret.swatchbin"
            )


class ZipAndLooseResolutionTests(unittest.TestCase):
    def _media_tree(self, tmp: str):
        shared = os.path.join(tmp, "_library")
        cars_lib = os.path.join(tmp, "cars", "_library")
        os.makedirs(shared)
        os.makedirs(cars_lib)
        shared_zip = os.path.join(shared, "Textures.zip")
        with zipfile.ZipFile(shared_zip, "w") as zf:
            zf.writestr(
                "swatches/defaultshader_diff_abc.swatchbin",
                b"SHARED",
            )
        cars_zip = os.path.join(cars_lib, "Textures.zip")
        with zipfile.ZipFile(cars_zip, "w") as zf:
            zf.writestr("pack/car_badge.swatchbin", b"CARS")
        loose_dir = os.path.join(tmp, "_library", "textures", "swatches")
        os.makedirs(loose_dir, exist_ok=True)
        loose = os.path.join(loose_dir, "loose_diff.swatchbin")
        with open(loose, "wb") as f:
            f.write(b"LOOSE")
        return tmp, shared_zip, cars_zip, loose

    def test_shared_zip_resolves_media_library_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, shared_zip, _cars, _loose = self._media_tree(tmp)
            store = ZipAssetStore(media, cache_dir=os.path.join(tmp, "cache"))
            try:
                store.build()
                hit = store.lookup_member(SHARED_SWATCH)
                self.assertIsNotNone(hit)
                self.assertEqual(os.path.normcase(hit[0]), os.path.normcase(shared_zip))
                out = store.resolve_to_cache(SHARED_SWATCH)
                self.assertTrue(os.path.isfile(out))
                with open(out, "rb") as f:
                    self.assertEqual(f.read(), b"SHARED")
                stats_before = store.cache_stats()["lookups"]
                store.resolve_to_cache(SHARED_SWATCH)
                self.assertGreater(store.cache_stats()["lookups"], stats_before)
                self.assertGreaterEqual(store.cache_stats()["cache_hits"], 1)
            finally:
                store.close()

    def test_cars_zip_does_not_poison_media_library_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, shared_zip, cars_zip, _ = self._media_tree(tmp)
            store = ZipAssetStore(media, cache_dir=os.path.join(tmp, "cache"))
            try:
                store.build()
                hit = store.lookup_member(SHARED_SWATCH)
                self.assertIsNotNone(hit)
                self.assertEqual(os.path.normcase(hit[0]), os.path.normcase(shared_zip))
                hit2 = store.lookup_member(CARS_SWATCH)
                self.assertIsNotNone(hit2)
                self.assertEqual(os.path.normcase(hit2[0]), os.path.normcase(cars_zip))
            finally:
                store.close()

    def test_index_built_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, *_ = self._media_tree(tmp)
            store = ZipAssetStore(media, cache_dir=os.path.join(tmp, "cache"))
            try:
                t0 = time.perf_counter()
                store.build()
                first = time.perf_counter() - t0
                members = store.cache_stats()["members_indexed"]
                t1 = time.perf_counter()
                store.build()
                second = time.perf_counter() - t1
                self.assertEqual(store.cache_stats()["members_indexed"], members)
                self.assertLess(second, first + 0.05)
            finally:
                store.close()

    def test_loose_file_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, *_ = self._media_tree(tmp)
            resolver = GamePathResolver(media)
            try:
                src = resolve_texture_source(LOOSE_SWATCH, resolver, media_root=media)
                self.assertTrue(src.exists)
                self.assertEqual(src.kind, TextureSourceKind.LOOSE_FILE)
                with open(src.filesystem_path, "rb") as f:
                    self.assertEqual(f.read(), b"LOOSE")
            finally:
                if resolver._zipfs is not None:
                    resolver._zipfs.close()

    def test_missing_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, *_ = self._media_tree(tmp)
            resolver = GamePathResolver(media)
            try:
                src = resolve_texture_source(MISSING_SWATCH, resolver, media_root=media)
                self.assertFalse(src.exists)
                self.assertEqual(src.kind, TextureSourceKind.NOT_FOUND)
                self.assertIn(
                    src.failure,
                    (
                        TextureSourceFailure.SOURCE_TEXTURE_MEMBER_NOT_FOUND,
                        TextureSourceFailure.SOURCE_TEXTURE_NOT_FOUND,
                    ),
                )
            finally:
                if resolver._zipfs is not None:
                    resolver._zipfs.close()

    def test_cache_identity_differs_by_member(self):
        s1 = ResolvedTextureSource(
            kind=TextureSourceKind.ZIP_MEMBER,
            original_path="a",
            canonical_game_path="media\\_library\\textures\\a.swatchbin",
            filesystem_path="/tmp/a",
            archive_path="/z/Textures.zip",
            archive_member="swatches/a.swatchbin",
            exists=True,
        )
        s2 = ResolvedTextureSource(
            kind=TextureSourceKind.ZIP_MEMBER,
            original_path="b",
            canonical_game_path="media\\_library\\textures\\b.swatchbin",
            filesystem_path="/tmp/b",
            archive_path="/z/Textures.zip",
            archive_member="other/a.swatchbin",
            exists=True,
        )
        self.assertNotEqual(
            s1.cache_identity(non_color=True),
            s2.cache_identity(non_color=True),
        )
        self.assertNotEqual(
            s1.cache_identity(non_color=True),
            s1.cache_identity(non_color=False),
        )


if __name__ == "__main__":
    unittest.main()
