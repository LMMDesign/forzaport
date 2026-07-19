"""Clean material translator entry point."""

from __future__ import annotations

from ..pipeline_v3 import CleanMaterialBuilder, MaterialTranslateError


def translator_for(
    game_key: str, media_root: str | None = None
) -> CleanMaterialBuilder:
    """Return the clean translator; unsupported games fail explicitly."""
    key = (game_key or "").lower()
    if key != "fh6":
        raise MaterialTranslateError(
            f"clean material pipeline currently supports FH6 only, got {game_key!r}"
        )
    return CleanMaterialBuilder(media_root=media_root, game_key=key)
