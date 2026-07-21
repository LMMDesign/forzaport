"""Repository-root-relative paths for local material-conformance workspace.

This copy lives under ``materials/conformance/audit/common/`` inside the add-on.
"""
from __future__ import annotations

from pathlib import Path

# …/addon/io_import_forza_carbin/materials/conformance/audit/common/workspace_paths.py
_ADDON_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = _ADDON_ROOT.parents[1]  # …/Forza Import
ADDON_ROOT = _ADDON_ROOT
ADDON_PACKAGE_PARENT = _ADDON_ROOT.parent

DOCS_MATERIAL = REPO_ROOT / "docs" / "material-conformance"
STATUS_MD = DOCS_MATERIAL / "STATUS.md"
ROADMAP_MD = DOCS_MATERIAL / "ROADMAP.md"
CONTRACT_DOCS = DOCS_MATERIAL / "contracts"
DECISIONS = DOCS_MATERIAL / "decisions"

REPORTS_MATERIAL = REPO_ROOT / "reports" / "material-conformance"
REPORTS_INDEX = REPORTS_MATERIAL / "index.json"
REPORTS_RUNS = REPORTS_MATERIAL / "runs"

RUNTIME_CONTRACTS = ADDON_ROOT / "materials" / "contracts"

SCRATCH = REPO_ROOT / "scratch"
ARCHIVE_RE = REPO_ROOT / "archive" / "reverse-engineering"
WORKSPACE_BACKUPS = REPO_ROOT / "_workspace_backups"
FH6_RIP_LEGACY = ARCHIVE_RE / "fh6-rip-legacy"


def ensure_addon_on_sys_path() -> Path:
    import sys

    p = str(ADDON_PACKAGE_PARENT)
    if p not in sys.path:
        sys.path.insert(0, p)
    return ADDON_PACKAGE_PARENT
