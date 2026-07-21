"""Shader-family contract stubs (Milestone A).

Contracts are development artefacts keyed by ShaderIdentity. Runtime import
must not disassemble DXIL per instance — see shader_contract_registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .forza_ir import ContractStatus, ShaderIdentity
from .model import ProvenanceDiagnostic


@dataclass(frozen=True)
class TextureBindingContract:
    name_hash: int
    name: str
    treg: int
    activation_rule: str  # human-readable until expression DAG proven
    channels: tuple[str, ...]
    uv_rule: str
    influences: tuple[str, ...]  # base_color | normal | ...
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class SwitchContract:
    name_hash: int
    name: str
    cbuffer_field: str | None
    default_when_absent: Any
    branches: tuple[str, ...]
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ShaderFamilyContract:
    identity: ShaderIdentity
    status: ContractStatus
    texture_bindings: tuple[TextureBindingContract, ...]
    switches: tuple[SwitchContract, ...]
    output_notes: tuple[str, ...] = ()
    evidence: tuple[ProvenanceDiagnostic, ...] = ()
