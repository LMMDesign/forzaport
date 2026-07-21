"""Exact-SHA ShaderImplementation registry for production IR evaluators.

Replaces duplicated family/SHA dictionaries and dynamic wrapper naming in
nodes_v3. Lookup is by exact shaderbin SHA only — unknown SHA fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .route_model import PRODUCTION_IR_SHADERBIN_SHA256

EvaluateFn = Callable[..., Any]
IdentityFn = Callable[[str | None, str | None], bool]


@dataclass(frozen=True)
class ShaderImplementation:
    shaderbin_sha256: str
    shader_name: str
    evaluate: EvaluateFn
    is_contract_identity: IdentityFn
    # Optional revision / family notes for diagnostics only.
    family_kind: str = "clean_surface"  # clean_surface | car_standard | car_carbonfiber


_REGISTRY: dict[str, ShaderImplementation] = {}
_LOADED = False


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _register_builtins()
    _LOADED = True


def register_shader_implementation(impl: ShaderImplementation) -> None:
    """Register or replace one exact-SHA implementation."""
    _REGISTRY[impl.shaderbin_sha256] = impl


def get_shader_implementation(shaderbin_sha256: str | None) -> ShaderImplementation | None:
    _ensure_loaded()
    if not shaderbin_sha256:
        return None
    return _REGISTRY.get(shaderbin_sha256)


def has_registered_ir_evaluator(shaderbin_sha256: str | None) -> bool:
    """Production gate: exact SHA in registry (and production set)."""
    if not shaderbin_sha256:
        return False
    if shaderbin_sha256 not in PRODUCTION_IR_SHADERBIN_SHA256:
        return False
    return get_shader_implementation(shaderbin_sha256) is not None


def all_registered_shas() -> frozenset[str]:
    _ensure_loaded()
    return frozenset(_REGISTRY)


def _register_builtins() -> None:
    from . import eval_car_carbonfiber as carbon
    from . import eval_car_standard as standard
    from . import eval_clean_surface_ir as clean

    register_shader_implementation(
        ShaderImplementation(
            shaderbin_sha256=standard.CAR_STANDARD_SHADERBIN_SHA256,
            shader_name="car_standard",
            evaluate=standard.evaluate_car_standard,
            is_contract_identity=standard.is_car_standard_contract_identity,
            family_kind="car_standard",
        )
    )
    register_shader_implementation(
        ShaderImplementation(
            shaderbin_sha256=carbon.CAR_CARBONFIBER_SHADERBIN_SHA256,
            shader_name="car_carbonfiber",
            evaluate=carbon.evaluate_car_carbonfiber,
            is_contract_identity=carbon.is_car_carbonfiber_contract_identity,
            family_kind="car_carbonfiber",
        )
    )

    # Clean-surface families — one evaluator, exact SHA + name identity.
    clean_rows = (
        ("car_label", clean.CAR_LABEL_SHADERBIN_SHA256, clean.is_car_label_contract_identity),
        (
            "car_standard_emissive",
            clean.CAR_STANDARD_EMISSIVE_SHADERBIN_SHA256,
            clean.is_car_standard_emissive_contract_identity,
        ),
        (
            "car_standard_fabric",
            clean.CAR_STANDARD_FABRIC_SHADERBIN_SHA256,
            clean.is_car_standard_fabric_contract_identity,
        ),
        (
            "car_automotive_paint",
            clean.CAR_AUTOMOTIVE_PAINT_SHADERBIN_SHA256,
            clean.is_car_automotive_paint_contract_identity,
        ),
        (
            "car_standard_coated",
            clean.CAR_STANDARD_COATED_SHADERBIN_SHA256,
            clean.is_car_standard_coated_contract_identity,
        ),
        (
            "car_glass_detailed",
            clean.CAR_GLASS_DETAILED_SHADERBIN_SHA256,
            clean.is_car_glass_detailed_contract_identity,
        ),
        (
            "car_reflector",
            clean.CAR_REFLECTOR_SHADERBIN_SHA256,
            clean.is_car_reflector_contract_identity,
        ),
        (
            "car_brakerotor",
            clean.CAR_BRAKEROTOR_SHADERBIN_SHA256,
            clean.is_car_brakerotor_contract_identity,
        ),
        (
            "car_livery_transmissive",
            clean.CAR_LIVERY_TRANSMISSIVE_SHADERBIN_SHA256,
            clean.is_car_livery_transmissive_contract_identity,
        ),
        (
            "car_livery",
            clean.CAR_LIVERY_SHADERBIN_SHA256,
            clean.is_car_livery_contract_identity,
        ),
    )
    for name, sha, ident in clean_rows:
        register_shader_implementation(
            ShaderImplementation(
                shaderbin_sha256=sha,
                shader_name=name,
                evaluate=clean.evaluate_clean_surface_ir,
                is_contract_identity=ident,
                family_kind="clean_surface",
            )
        )
