"""Repository-root-relative paths for local material-conformance workspace."""
from __future__ import annotations

from pathlib import Path

# tools/material_conformance/common/workspace_paths.py → repo root is parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]

ADDON_ROOT = REPO_ROOT / "addon" / "io_import_forza_carbin"
ADDON_PACKAGE_PARENT = REPO_ROOT / "addon"

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
    """Insert add-on parent on sys.path so `io_import_forza_carbin` imports resolve."""
    import sys

    p = str(ADDON_PACKAGE_PARENT)
    if p not in sys.path:
        sys.path.insert(0, p)
    return ADDON_PACKAGE_PARENT
