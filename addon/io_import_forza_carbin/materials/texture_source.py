"""Typed texture source identity and resolution (Blender-independent).

Separates semantic material paths (GAME:\\…) from physical byte sources
(loose file vs zip member). Does not perform semantic TXMP selection or
shader capability decisions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .model import ProvenanceDiagnostic


class TextureSourceKind(Enum):
    LOOSE_FILE = "loose_file"
    ZIP_MEMBER = "zip_member"
    NOT_FOUND = "not_found"


class TextureSourceFailure(Enum):
    """Structured failure codes for texture binding / pink classification."""

    SOURCE_TEXTURE_NOT_FOUND = "SOURCE_TEXTURE_NOT_FOUND"
    SOURCE_TEXTURE_ARCHIVE_NOT_INDEXED = "SOURCE_TEXTURE_ARCHIVE_NOT_INDEXED"
    SOURCE_TEXTURE_MEMBER_NOT_FOUND = "SOURCE_TEXTURE_MEMBER_NOT_FOUND"
    TEXTURE_READ_FAILED = "TEXTURE_READ_FAILED"
    TEXTURE_DECODE_FAILED = "TEXTURE_DECODE_FAILED"
    BLENDER_IMAGE_CREATION_FAILED = "BLENDER_IMAGE_CREATION_FAILED"
    PATH_TRAVERSAL_REJECTED = "PATH_TRAVERSAL_REJECTED"
    AMBIGUOUS_SOURCE = "AMBIGUOUS_SOURCE"


class TexturePathError(ValueError):
    """Invalid or unsafe texture path."""


def canonicalize_game_path(path: str) -> str:
    """Return one canonical game-relative path (backslash, lower-case Media layout).

    Preserves meaning: ``GAME:\\Media\\…`` becomes ``media\\…`` rest with backslashes.
    Rejects path traversal (``..``). Does not invent extensions or basenames.
    """
    if not path or not str(path).strip():
        raise TexturePathError("empty texture path")
    raw = str(path).strip()
    rest = raw
    if len(rest) >= 5 and rest[:5].lower() == "game:":
        rest = rest[5:]
    rest = rest.replace("/", "\\").lstrip("\\")
    # Collapse duplicate separators
    while "\\\\" in rest:
        rest = rest.replace("\\\\", "\\")
    parts = [p for p in rest.split("\\") if p]
    if any(p == ".." for p in parts):
        raise TexturePathError(f"path traversal rejected: {path!r}")
    # Normalise leading Media token case for stable keys
    if parts and parts[0].lower() == "media":
        parts[0] = "media"
    canon = "\\".join(parts)
    return canon.lower()


def diagnostic_game_path(path: str) -> str:
    """Human/diagnostic form preserving original spelling as much as possible."""
    return str(path).strip()


@dataclass(frozen=True)
class SourceAttempt:
    """One location tried during resolution (recorded for reports)."""

    kind: str
    location: str
    hit: bool
    detail: str = ""


@dataclass(frozen=True)
class ResolvedTextureSource:
    """Exact physical identity of texture bytes."""

    kind: TextureSourceKind
    original_path: str
    canonical_game_path: str
    filesystem_path: str | None
    archive_path: str | None
    archive_member: str | None
    exists: bool
    failure: TextureSourceFailure | None = None
    attempts: tuple[SourceAttempt, ...] = ()
    provenance: tuple[ProvenanceDiagnostic, ...] = ()
    case_mismatch: bool = False

    def cache_identity(self, *, non_color: bool, decode_tag: str = "swatchbin") -> str:
        """Stable image-cache key fragment (no bytes)."""
        if self.kind is TextureSourceKind.ZIP_MEMBER:
            src = f"zip:{self.archive_path}|{self.archive_member}"
        elif self.kind is TextureSourceKind.LOOSE_FILE:
            src = f"file:{self.filesystem_path}"
        else:
            src = f"missing:{self.canonical_game_path}"
        return f"{src}|{decode_tag}|nc={int(non_color)}"


def _loose_candidates(media_root: str, canonical: str) -> list[str]:
    """Deterministic loose filesystem candidates for a canonical media\\… path."""
    # canonical is lower-case media\...
    rest = canonical
    if rest.startswith("media\\"):
        stripped = rest[len("media\\") :]
    else:
        stripped = rest
    cands: list[str] = []
    for base in (
        os.path.join(media_root, stripped.replace("\\", os.sep)),
        os.path.join(media_root, rest.replace("\\", os.sep)),
    ):
        if base not in cands:
            cands.append(base)
    return cands


def resolve_texture_source(
    path: str,
    resolver: Any,
    *,
    media_root: str | None = None,
) -> ResolvedTextureSource:
    """Resolve a MatI/TXMP GAME path to a typed source identity.

    Precedence:
      1. Exact loose file under the media root
      2. Exact zip member via ZipAssetStore (shared + cars libraries already indexed)
      3. Failure with structured diagnostics
    """
    from .pipeline_metrics import METRICS

    METRICS.record_call("resolve_texture_source")
    with METRICS.stage("resolve_texture_source"):
        return _resolve_texture_source_impl(
            path, resolver, media_root=media_root
        )


def _resolve_texture_source_impl(
    path: str,
    resolver: Any,
    *,
    media_root: str | None = None,
) -> ResolvedTextureSource:
    original = diagnostic_game_path(path)
    attempts: list[SourceAttempt] = []
    provenance: list[ProvenanceDiagnostic] = []
    try:
        canonical = canonicalize_game_path(original)
    except TexturePathError as exc:
        return ResolvedTextureSource(
            kind=TextureSourceKind.NOT_FOUND,
            original_path=original,
            canonical_game_path="",
            filesystem_path=None,
            archive_path=None,
            archive_member=None,
            exists=False,
            failure=TextureSourceFailure.PATH_TRAVERSAL_REJECTED,
            attempts=(
                SourceAttempt(
                    kind="canonicalize",
                    location=original,
                    hit=False,
                    detail=str(exc),
                ),
            ),
            provenance=(
                ProvenanceDiagnostic(
                    kind="texture_source",
                    detail=str(exc),
                    source="materials.texture_source",
                ),
            ),
        )

    root = media_root
    if root is None and resolver is not None:
        root = getattr(resolver, "root", None)
        from ..parsing.paths import find_media_root

        found = find_media_root(root) if root else None
        root = found or root

    case_mismatch = False
    # 1. Loose file
    if root:
        for cand in _loose_candidates(root, canonical):
            hit = os.path.isfile(cand)
            attempts.append(
                SourceAttempt(kind="loose_file", location=cand, hit=hit)
            )
            if hit:
                # Detect path case differences for diagnostics only.
                if cand.replace("/", "\\").lower() != cand.replace("/", "\\"):
                    case_mismatch = True
                provenance.append(
                    ProvenanceDiagnostic(
                        kind="texture_source",
                        detail=f"loose_file:{cand}",
                        source="materials.texture_source",
                    )
                )
                return ResolvedTextureSource(
                    kind=TextureSourceKind.LOOSE_FILE,
                    original_path=original,
                    canonical_game_path=canonical,
                    filesystem_path=os.path.abspath(cand),
                    archive_path=None,
                    archive_member=None,
                    exists=True,
                    failure=None,
                    attempts=tuple(attempts),
                    provenance=tuple(provenance),
                    case_mismatch=case_mismatch,
                )

    # 2. Zip member (through GamePathResolver / ZipAssetStore)
    zipfs = getattr(resolver, "_zipfs", None) if resolver is not None else None
    game_path = original if original.lower().startswith("game:") else f"GAME:\\{canonical}"
    member_hit = None
    if zipfs is not None and hasattr(zipfs, "lookup_member"):
        member_hit = zipfs.lookup_member(game_path)
        attempts.append(
            SourceAttempt(
                kind="zip_lookup",
                location=game_path,
                hit=member_hit is not None,
                detail=(
                    f"{member_hit[0]}!{member_hit[1]}"
                    if member_hit
                    else "member_not_indexed"
                ),
            )
        )

    filesystem = None
    if resolver is not None:
        try:
            filesystem = resolver.resolve(game_path)
        except Exception as exc:  # noqa: BLE001
            attempts.append(
                SourceAttempt(
                    kind="resolver.resolve",
                    location=game_path,
                    hit=False,
                    detail=str(exc),
                )
            )
            return ResolvedTextureSource(
                kind=TextureSourceKind.NOT_FOUND,
                original_path=original,
                canonical_game_path=canonical,
                filesystem_path=None,
                archive_path=member_hit[0] if member_hit else None,
                archive_member=member_hit[1] if member_hit else None,
                exists=False,
                failure=TextureSourceFailure.TEXTURE_READ_FAILED,
                attempts=tuple(attempts),
                provenance=tuple(provenance),
            )

    if filesystem and os.path.isfile(filesystem):
        attempts.append(
            SourceAttempt(kind="resolved_filesystem", location=filesystem, hit=True)
        )
        if member_hit is not None:
            provenance.append(
                ProvenanceDiagnostic(
                    kind="texture_source",
                    detail=f"zip_member:{member_hit[0]}!{member_hit[1]}",
                    source="materials.texture_source",
                )
            )
            return ResolvedTextureSource(
                kind=TextureSourceKind.ZIP_MEMBER,
                original_path=original,
                canonical_game_path=canonical,
                filesystem_path=os.path.abspath(filesystem),
                archive_path=member_hit[0],
                archive_member=member_hit[1],
                exists=True,
                failure=None,
                attempts=tuple(attempts),
                provenance=tuple(provenance),
            )
        provenance.append(
            ProvenanceDiagnostic(
                kind="texture_source",
                detail=f"loose_via_resolver:{filesystem}",
                source="materials.texture_source",
            )
        )
        return ResolvedTextureSource(
            kind=TextureSourceKind.LOOSE_FILE,
            original_path=original,
            canonical_game_path=canonical,
            filesystem_path=os.path.abspath(filesystem),
            archive_path=None,
            archive_member=None,
            exists=True,
            failure=None,
            attempts=tuple(attempts),
            provenance=tuple(provenance),
        )

    # Failure classification
    if zipfs is None:
        failure = TextureSourceFailure.SOURCE_TEXTURE_ARCHIVE_NOT_INDEXED
    elif member_hit is None:
        # Shared vs cars: if path is under media\_library\textures and zipfs missed
        if "\\_library\\textures\\" in canonical.replace("/", "\\"):
            failure = TextureSourceFailure.SOURCE_TEXTURE_MEMBER_NOT_FOUND
        else:
            failure = TextureSourceFailure.SOURCE_TEXTURE_NOT_FOUND
    else:
        failure = TextureSourceFailure.TEXTURE_READ_FAILED

    attempts.append(
        SourceAttempt(
            kind="failure",
            location=game_path,
            hit=False,
            detail=failure.value,
        )
    )
    return ResolvedTextureSource(
        kind=TextureSourceKind.NOT_FOUND,
        original_path=original,
        canonical_game_path=canonical,
        filesystem_path=None,
        archive_path=member_hit[0] if member_hit else None,
        archive_member=member_hit[1] if member_hit else None,
        exists=False,
        failure=failure,
        attempts=tuple(attempts),
        provenance=tuple(provenance),
    )


@dataclass
class TextureLoadResult:
    """Outcome of reading/decoding after source resolution (no bpy)."""

    source: ResolvedTextureSource
    ok: bool
    failure: TextureSourceFailure | None = None
    detail: str = ""
    fingerprint: str = ""
    extras: dict[str, Any] = field(default_factory=dict)
