"""Instance evaluator stub — Milestone A only (raises NotImplementedError).

Phase 7 will combine shader contract + MatI → ForzaMaterialIR.
"""

from __future__ import annotations

from .forza_ir import ForzaMaterialIR


class ShaderInstanceEvaluator:
    """Pure-Python evaluator. Not production-wired in Milestone A."""

    def evaluate(self, *, material, media_root: str, resolver=None) -> ForzaMaterialIR:
        raise NotImplementedError(
            "ShaderInstanceEvaluator is scaffolding until Milestone B contracts exist"
        )
