"""Explicit boundary for non-shipping addon diagnostics.

Production behavior is the default. Research-only hooks (pose oracle, verbose
Mojo bake dumps, hot module reload) require:

    FORZA_ADDON_DEV=1

FH6 Mojo Autovista always requires ACL 2.1 — there is no mid-only fallback.
"""
from __future__ import annotations

import os


def development_enabled() -> bool:
    return os.environ.get("FORZA_ADDON_DEV", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
