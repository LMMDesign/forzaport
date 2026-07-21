"""Convert evaluated sample-site UV AST (UvExprNode) → ForzaMaterialIR UVExpression.

Authoritative path for contracted SHAs: do not rebuild UV from
ResolvedTextureSlot.tiling / rotation_degrees.
"""

from __future__ import annotations

from .forza_ir import (
    ConstantScalar,
    MeshUV,
    OffsetUV,
    RotateUV,
    ScaleUV,
    SelectUV,
    UVExpression,
)
from .model import ProvenanceDiagnostic as PD
from .uv.uv_expr import (
    AddUVNode,
    ComposeUVNode,
    MeshUVNode,
    MultiplyUVNode,
    OffsetUVNode,
    RotateUVNode,
    ScaleUVNode,
    SelectUVNode,
    UnresolvedUVNode,
    UvExprNode,
    evaluate_predicate,
)


def _pd(*details: str, source: str = "materials.uv_ir_bridge") -> tuple[PD, ...]:
    return tuple(PD(kind="uv", detail=d, source=source) for d in details if d)


def _ev_from(node: UvExprNode) -> tuple[PD, ...]:
    raw = getattr(node, "evidence", ()) or ()
    return tuple(
        PD(kind="uv", detail=str(x), source="materials.uv_ir_bridge") for x in raw if x
    )


def _resolve_scale(scale, params: dict) -> tuple[float, float] | None:
    if isinstance(scale, (tuple, list)) and len(scale) >= 2:
        return float(scale[0]), float(scale[1])
    if isinstance(scale, str):
        # Param name-hash hex → MatI float2
        try:
            h = int(scale, 16) if scale.startswith("0x") or len(scale) == 8 else int(scale)
        except ValueError:
            return None
        p = params.get(h) or params.get(h & 0xFFFFFFFF)
        if p is None:
            return None
        raw = getattr(p, "raw", None) or getattr(p, "samp", None) or b""
        if isinstance(raw, (bytes, bytearray)) and len(raw) >= 8:
            import struct

            return struct.unpack_from("<ff", raw, 0)
        # float/vec attributes
        u = getattr(p, "x", None)
        v = getattr(p, "y", None)
        if u is not None and v is not None:
            return float(u), float(v)
    return None


def uv_expr_to_forza_ir(
    node: UvExprNode | None,
    *,
    params: dict,
) -> tuple[UVExpression | None, str | None]:
    """Carry typed sample-site UV into Forza IR. Returns (expr, reject_reason)."""
    if node is None:
        return None, "missing uv_node on evaluated sample site"
    if isinstance(node, UnresolvedUVNode):
        return None, f"unresolved UV: {node.reason}"
    if isinstance(node, MeshUVNode):
        return MeshUV(index=int(node.index), evidence=_ev_from(node)), None
    if isinstance(node, ScaleUVNode):
        src, err = uv_expr_to_forza_ir(node.source, params=params)
        if err:
            return None, err
        scale = _resolve_scale(node.scale, params)
        if scale is None:
            return None, f"unresolved ScaleUV scale={node.scale!r}"
        return (
            ScaleUV(source=src, scale=scale, evidence=_ev_from(node)),
            None,
        )
    if isinstance(node, OffsetUVNode):
        src, err = uv_expr_to_forza_ir(node.source, params=params)
        if err:
            return None, err
        return (
            OffsetUV(
                source=src,
                offset=(float(node.offset[0]), float(node.offset[1])),
                evidence=_ev_from(node),
            ),
            None,
        )
    if isinstance(node, RotateUVNode):
        src, err = uv_expr_to_forza_ir(node.source, params=params)
        if err:
            return None, err
        return (
            RotateUV(
                source=src,
                degrees=float(node.degrees),
                evidence=_ev_from(node),
            ),
            None,
        )
    if isinstance(node, SelectUVNode):
        ok, detail = evaluate_predicate(
            params=params,
            param_hash=node.predicate_hash,
            param_type=node.predicate_type,
            true_when=node.true_when,
        )
        if ok is None:
            return None, f"SelectUV predicate unresolved: {detail}"
        true_ir, err_t = uv_expr_to_forza_ir(node.true_expr, params=params)
        false_ir, err_f = uv_expr_to_forza_ir(node.false_expr, params=params)
        if err_t or err_f:
            return None, err_t or err_f
        cond = ConstantScalar(
            value=1.0 if ok else 0.0,
            evidence=_pd(detail or f"SelectUV predicate={ok}"),
        )
        return (
            SelectUV(
                condition=cond,
                a=true_ir,
                b=false_ir,
                evidence=_ev_from(node) + _pd(detail),
            ),
            None,
        )
    if isinstance(node, (AddUVNode, MultiplyUVNode, ComposeUVNode)):
        # IR UV DAG has no Add/Multiply yet — fail closed rather than invent.
        return None, f"UV node kind {type(node).__name__} not representable in Forza IR"
    return None, f"unsupported UV node {type(node).__name__}"
