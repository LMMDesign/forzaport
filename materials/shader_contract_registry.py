"""Contract registry keyed by shader identity (cache; no per-import DXIL)."""

from __future__ import annotations

import json
import os
from functools import lru_cache

from .forza_ir import ContractStatus, ShaderIdentity
from .shader_contract import ShaderFamilyContract


def contracts_dir() -> str:
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(here, "contracts")


@lru_cache(maxsize=128)
def load_contract(shaderbin_sha256: str) -> dict | None:
    path = os.path.join(contracts_dir(), f"{shaderbin_sha256}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_contract_files() -> tuple[str, ...]:
    root = contracts_dir()
    if not os.path.isdir(root):
        return ()
    return tuple(
        sorted(n for n in os.listdir(root) if n.endswith(".json"))
    )


def contract_status_for(shaderbin_sha256: str) -> ContractStatus:
    data = load_contract(shaderbin_sha256)
    if data is None:
        return ContractStatus.PROPOSED
    raw = (data.get("status") or "proposed").lower()
    # B1+ contracts may use descriptive status strings; map known proven aliases.
    if raw in (
        "proven",
        "corpus_proven_production_observed_branches",
        "corpus-proven",
    ):
        return ContractStatus.PROVEN
    try:
        return ContractStatus(raw)
    except ValueError:
        return ContractStatus.PROPOSED
