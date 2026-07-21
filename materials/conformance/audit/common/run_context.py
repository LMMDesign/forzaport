"""Immutable generated-run helper for local material-conformance reports."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace_paths import REPORTS_INDEX, REPORTS_RUNS, REPO_ROOT


def _git_info() -> tuple[str | None, bool]:
    """Best-effort git commit for the add-on product tree (not parent workspace)."""
    addon_git = REPO_ROOT / "addon" / "io_import_forza_carbin"
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(addon_git), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        commit = None
    dirty = False
    try:
        st = subprocess.check_output(
            ["git", "-C", str(addon_git), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        dirty = bool(st.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        dirty = False
    return commit, dirty


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class ConformanceRun:
    """Create a unique immutable run directory under reports/material-conformance/runs/."""

    def __init__(
        self,
        milestone: str,
        *,
        shader_name: str | None = None,
        shaderbin_sha256: str | None = None,
        tool: str | None = None,
        command: str | None = None,
        corpus: str | None = None,
        contract_version: str | None = None,
        historical_snapshot: bool = False,
    ) -> None:
        self.milestone = milestone
        self.shader_name = shader_name
        self.shaderbin_sha256 = shaderbin_sha256
        self.tool = tool
        self.command = command
        self.corpus = corpus
        self.contract_version = contract_version
        self.historical_snapshot = historical_snapshot
        self.run_dir: Path | None = None
        self.run_id: str | None = None
        self._warnings: list[str] = []
        self._unresolved: list[str] = []
        self._tests: list[dict[str, Any]] = []
        self._generated: list[str] = []
        self._inputs: list[str] = []
        self._finalized = False

    def __enter__(self) -> "ConformanceRun":
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        commit, dirty = _git_info()
        short = (commit or "nogit")[:7]
        base = f"{stamp}_{self.milestone}_{short}"
        run_dir = REPORTS_RUNS / base
        n = 0
        while run_dir.exists():
            n += 1
            run_dir = REPORTS_RUNS / f"{base}_{n}"
        run_dir.mkdir(parents=False)
        for sub in ("audits", "diffs", "data", "screenshots", "logs"):
            (run_dir / sub).mkdir()
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self._git_commit = commit
        self._git_dirty = dirty
        self._created_at = datetime.now(timezone.utc).isoformat()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._finalized:
            self.finalize(ok=exc_type is None)
        return False

    def path(self, relative: str) -> Path:
        assert self.run_dir is not None
        return self.run_dir / relative

    def write_json(self, relative: str, payload: Any) -> Path:
        p = self.path(relative)
        _write_json(p, payload)
        self._generated.append(relative.replace("\\", "/"))
        return p

    def write_text(self, relative: str, text: str) -> Path:
        p = self.path(relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self._generated.append(relative.replace("\\", "/"))
        return p

    def record_input(self, path: str) -> None:
        self._inputs.append(path)

    def record_warning(self, msg: str) -> None:
        self._warnings.append(msg)

    def record_unresolved(self, msg: str) -> None:
        self._unresolved.append(msg)

    def record_test_result(self, name: str, passed: bool, detail: str | None = None) -> None:
        self._tests.append({"name": name, "passed": passed, "detail": detail})

    def finalize(
        self,
        *,
        ok: bool = True,
        run_execution_ok: bool | None = None,
        completion_gate_passed: bool | None = None,
        unresolved_variants: list[str] | None = None,
        unresolved_families: list[str] | None = None,
        ambiguous_sample_sites: list[str] | None = None,
        incomplete_contracts: list[str] | None = None,
    ) -> Path:
        assert self.run_dir is not None and self.run_id is not None
        if self._finalized:
            return self.run_dir / "manifest.json"
        manifest_path = self.run_dir / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(f"refusing to overwrite {manifest_path}")
        exec_ok = ok if run_execution_ok is None else run_execution_ok
        gate = False if completion_gate_passed is None else completion_gate_passed
        # Truthfulness: never claim overall ok while the completion gate fails.
        overall_ok = bool(exec_ok) and bool(gate)
        manifest = {
            "schema_version": 2,
            "run_id": self.run_id,
            "created_at": self._created_at,
            "historical_snapshot": self.historical_snapshot,
            "migrated_at": None,
            "original_created_at": None,
            "original_created_at_source": None,
            "git_commit": self._git_commit,
            "git_dirty": self._git_dirty,
            "command": self.command,
            "tool": self.tool,
            "milestone": self.milestone,
            "shader_name": self.shader_name,
            "shaderbin_sha256": self.shaderbin_sha256,
            "contract_version": self.contract_version,
            "corpus": self.corpus,
            "input_files": list(self._inputs),
            "generated_files": list(self._generated),
            "warnings": list(self._warnings),
            "unresolved_branches": list(self._unresolved),
            "unresolved_variants": list(unresolved_variants or []),
            "unresolved_families": list(unresolved_families or []),
            "ambiguous_sample_sites": list(ambiguous_sample_sites or []),
            "incomplete_contracts": list(incomplete_contracts or []),
            "test_results": list(self._tests),
            "run_execution_ok": bool(exec_ok),
            "completion_gate_passed": bool(gate),
            "ok": overall_ok,
        }
        _write_json(manifest_path, manifest)
        self._update_index(manifest)
        self._finalized = True
        return manifest_path

    def _update_index(self, manifest: dict[str, Any]) -> None:
        REPORTS_RUNS.mkdir(parents=True, exist_ok=True)
        if REPORTS_INDEX.exists():
            index = json.loads(REPORTS_INDEX.read_text(encoding="utf-8"))
        else:
            index = {"schema_version": 1, "runs": []}
        index.setdefault("runs", [])
        # never rewrite prior run entries' payloads; append only
        index["runs"] = [r for r in index["runs"] if r.get("run_id") != manifest["run_id"]]
        index["runs"].append(
            {
                "run_id": manifest["run_id"],
                "path": f"runs/{manifest['run_id']}",
                "milestone": manifest.get("milestone"),
                "created_at": manifest.get("created_at"),
                "historical_snapshot": manifest.get("historical_snapshot", False),
                "ok": manifest.get("ok"),
            }
        )
        index["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(REPORTS_INDEX, index)
