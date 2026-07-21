"""Typed UV expression nodes + recursive MatI-backed evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

from ..model import ProvenanceDiagnostic


@dataclass(frozen=True)
class MeshUVNode:
    index: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImmediateUVNode:
    u: float
    v: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProceduralUVNode:
    kind: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnresolvedUVNode:
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScaleUVNode:
    source: "UvExprNode"
    scale: tuple[float, float] | str  # literal or param name-hash hex
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class OffsetUVNode:
    source: "UvExprNode"
    offset: tuple[float, float]
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class RotateUVNode:
    source: "UvExprNode"
    degrees: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AddUVNode:
    a: "UvExprNode"
    b: "UvExprNode"
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class MultiplyUVNode:
    a: "UvExprNode"
    b: "UvExprNode"
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectUVNode:
    predicate_hash: int
    predicate_type: int  # MatI type (3=bool, 2=float/int scalar, …)
    true_when: str  # "nonzero" | "zero" | "true" | "false" | "eq:<value>"
    true_expr: "UvExprNode"
    false_expr: "UvExprNode"
    missing_policy: str = "reject"  # reject | unresolved
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AtlasOffsetUVNode:
    atlas_uv: "UvExprNode"
    # Operands not yet fully recovered from DXIL → remains unresolved until proven.
    operands_proven: bool = False
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComposeUVNode:
    """Ordered composition; prefer explicit Add/Scale trees when known."""

    ops: tuple["UvExprNode", ...]
    evidence: tuple[str, ...] = ()


UvExprNode = Union[
    MeshUVNode,
    ImmediateUVNode,
    ProceduralUVNode,
    UnresolvedUVNode,
    ScaleUVNode,
    OffsetUVNode,
    RotateUVNode,
    AddUVNode,
    MultiplyUVNode,
    SelectUVNode,
    AtlasOffsetUVNode,
    ComposeUVNode,
]


@dataclass(frozen=True)
class UvEvalResult:
    status: str  # PROVEN | REJECTED | UNRESOLVED
    mesh_texcoord: int | None = None
    node: UvExprNode | None = None
    evidence: tuple[str, ...] = ()
    rejection: str | None = None


def _param(params: dict, h: int):
    return params.get(h) or params.get(h & 0xFFFFFFFF)


def _scalar(p) -> float | bool | None:
    if p is None:
        return None
    t = getattr(p, "type", None)
    v = getattr(p, "value", None)
    if t == 3:
        return bool(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return None


def evaluate_predicate(
    *,
    params: dict,
    param_hash: int,
    param_type: int,
    true_when: str,
) -> tuple[bool | None, str]:
    """Return (branch_is_true|None, evidence). None → missing/unresolved."""
    p = _param(params, param_hash)
    if p is None:
        return None, f"predicate 0x{param_hash & 0xFFFFFFFF:08X} absent"
    raw = _scalar(p)
    if raw is None:
        return None, f"predicate 0x{param_hash & 0xFFFFFFFF:08X} undecodable"
    when = (true_when or "nonzero").lower()
    if when in ("nonzero", "true", "on"):
        if isinstance(raw, bool):
            return raw is True, f"bool={raw}"
        return float(raw) != 0.0, f"value={raw} nonzero→{float(raw) != 0.0}"
    if when in ("zero", "false", "off"):
        if isinstance(raw, bool):
            return raw is False, f"bool={raw}"
        return float(raw) == 0.0, f"value={raw} zero→{float(raw) == 0.0}"
    if when.startswith("eq:"):
        want = when[3:]
        try:
            return float(raw) == float(want), f"value={raw} eq {want}"
        except ValueError:
            return str(raw) == want, f"value={raw!r} eq {want!r}"
    return None, f"unknown true_when={true_when!r}"


def parse_uv_expr_json(obj: Any) -> UvExprNode:
    """Parse contract JSON into typed UV nodes."""
    if obj is None:
        return UnresolvedUVNode("missing uv_expression")
    if isinstance(obj, str):
        if obj.startswith("TEXCOORD") and obj[8:].isdigit():
            return MeshUVNode(index=int(obj[8:]), evidence=(f"direct {obj}",))
        if obj in ("UNRESOLVED_SAMPLE_SITE_CONTRACT",) or obj.startswith("SELECT_AMONG"):
            return UnresolvedUVNode(obj)
        return UnresolvedUVNode(f"unparsed string: {obj}")
    if not isinstance(obj, dict):
        return UnresolvedUVNode(f"bad uv_expression type: {type(obj)}")
    kind = obj.get("kind")
    if kind in (None, "MeshUV") and "index" in obj:
        return MeshUVNode(index=int(obj["index"]), evidence=tuple(obj.get("evidence") or ()))
    if kind == "Select":
        pred = obj.get("predicate") or {}
        ph = pred.get("param_hash") or pred.get("hash")
        if isinstance(ph, str):
            ph = int(ph, 16) if ph.startswith("0x") else int(ph)
        return SelectUVNode(
            predicate_hash=int(ph),
            predicate_type=int(pred.get("type") or pred.get("param_type") or 3),
            true_when=str(pred.get("true_when") or "nonzero"),
            true_expr=parse_uv_expr_json(obj.get("true")),
            false_expr=parse_uv_expr_json(obj.get("false")),
            missing_policy=str(pred.get("missing_policy") or "reject"),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "Scale" or kind == "ScaleUV":
        scale = obj.get("scale")
        if isinstance(scale, list) and len(scale) >= 2:
            scale_v: tuple[float, float] | str = (float(scale[0]), float(scale[1]))
        else:
            scale_v = str(scale or "UNRESOLVED")
        return ScaleUVNode(
            source=parse_uv_expr_json(obj.get("source") or obj.get("input")),
            scale=scale_v,
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "Offset" or kind == "OffsetUV":
        off = obj.get("offset") or [0.0, 0.0]
        return OffsetUVNode(
            source=parse_uv_expr_json(obj.get("source") or obj.get("input")),
            offset=(float(off[0]), float(off[1])),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "Rotate" or kind == "RotateUV":
        return RotateUVNode(
            source=parse_uv_expr_json(obj.get("source") or obj.get("input")),
            degrees=float(obj.get("degrees") or 0.0),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "Multiply" or kind == "MultiplyUV":
        return MultiplyUVNode(
            a=parse_uv_expr_json(obj.get("a") or obj.get("left")),
            b=parse_uv_expr_json(obj.get("b") or obj.get("right")),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "Add" or kind == "AddUV":
        return AddUVNode(
            a=parse_uv_expr_json(obj.get("a") or obj.get("left")),
            b=parse_uv_expr_json(obj.get("b") or obj.get("right")),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "AtlasOffset" or kind == "AtlasOffsetUV":
        return AtlasOffsetUVNode(
            atlas_uv=parse_uv_expr_json(obj.get("atlas_uv") or obj.get("source")),
            operands_proven=bool(obj.get("operands_proven")),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "Compose" or kind == "ComposeUV":
        # Prefer structured tree if present.
        if obj.get("tree"):
            return parse_uv_expr_json(obj["tree"])
        ops = obj.get("ops") or []
        # Legacy string ops → unresolved compose pending structured tree.
        if ops and all(isinstance(x, str) for x in ops):
            return UnresolvedUVNode(
                "Compose string ops without structured tree",
                evidence=tuple(ops),
            )
        return ComposeUVNode(
            ops=tuple(parse_uv_expr_json(x) for x in ops),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "Immediate" or kind == "ImmediateUV":
        return ImmediateUVNode(
            u=float(obj.get("u", 0)),
            v=float(obj.get("v", 0)),
            evidence=tuple(obj.get("evidence") or ()),
        )
    if kind == "UNRESOLVED_SAMPLE_SITE_CONTRACT":
        return UnresolvedUVNode(obj.get("note") or kind)
    return UnresolvedUVNode(f"unknown kind={kind!r}")


def evaluate_uv_expr(
    node: UvExprNode,
    *,
    params: dict,
) -> UvEvalResult:
    """Recursively evaluate typed UV expression against MatI params."""
    if isinstance(node, MeshUVNode):
        return UvEvalResult(
            status="PROVEN",
            mesh_texcoord=node.index,
            node=node,
            evidence=node.evidence + (f"MeshUV({node.index})",),
        )
    if isinstance(node, ImmediateUVNode):
        return UvEvalResult(
            status="PROVEN",
            mesh_texcoord=None,
            node=node,
            evidence=node.evidence + ("ImmediateUV",),
        )
    if isinstance(node, UnresolvedUVNode):
        return UvEvalResult(
            status="UNRESOLVED",
            node=node,
            evidence=node.evidence,
            rejection=node.reason,
        )
    if isinstance(node, ProceduralUVNode):
        return UvEvalResult(
            status="UNRESOLVED",
            node=node,
            rejection=f"ProceduralUV({node.kind}) not lowered",
            evidence=node.evidence,
        )
    if isinstance(node, SelectUVNode):
        branch, ev = evaluate_predicate(
            params=params,
            param_hash=node.predicate_hash,
            param_type=node.predicate_type,
            true_when=node.true_when,
        )
        if branch is None:
            if node.missing_policy == "reject":
                return UvEvalResult(
                    status="REJECTED",
                    node=node,
                    evidence=node.evidence + (ev,),
                    rejection=ev,
                )
            return UvEvalResult(
                status="UNRESOLVED",
                node=node,
                evidence=node.evidence + (ev,),
                rejection=ev,
            )
        chosen = node.true_expr if branch else node.false_expr
        inner = evaluate_uv_expr(chosen, params=params)
        return UvEvalResult(
            status=inner.status,
            mesh_texcoord=inner.mesh_texcoord,
            node=inner.node,
            evidence=node.evidence + (f"Select→{'true' if branch else 'false'}: {ev}",)
            + inner.evidence,
            rejection=inner.rejection,
        )
    if isinstance(node, AtlasOffsetUVNode):
        if not node.operands_proven:
            return UvEvalResult(
                status="UNRESOLVED",
                node=node,
                evidence=node.evidence,
                rejection="AtlasOffsetUV operands not proven from DXIL",
            )
        return evaluate_uv_expr(node.atlas_uv, params=params)
    if isinstance(node, (ScaleUVNode, OffsetUVNode, RotateUVNode)):
        inner = evaluate_uv_expr(node.source, params=params)
        if inner.status != "PROVEN":
            return inner
        # Scale/offset/rotate preserve mesh index when source is MeshUV; mark as
        # composed proven for mesh index purposes.
        return UvEvalResult(
            status="PROVEN",
            mesh_texcoord=inner.mesh_texcoord,
            node=node,
            evidence=inner.evidence + (type(node).__name__,),
        )
    if isinstance(node, AddUVNode):
        a = evaluate_uv_expr(node.a, params=params)
        b = evaluate_uv_expr(node.b, params=params)
        if a.status != "PROVEN" or b.status != "PROVEN":
            return UvEvalResult(
                status="UNRESOLVED",
                node=node,
                evidence=a.evidence + b.evidence,
                rejection=a.rejection or b.rejection or "AddUV operand unresolved",
            )
        return UvEvalResult(
            status="PROVEN",
            mesh_texcoord=None,  # composed — not a single mesh index
            node=node,
            evidence=a.evidence + b.evidence + ("AddUV",),
        )
    if isinstance(node, MultiplyUVNode):
        a = evaluate_uv_expr(node.a, params=params)
        b = evaluate_uv_expr(node.b, params=params)
        if a.status != "PROVEN" or b.status != "PROVEN":
            return UvEvalResult(
                status="UNRESOLVED",
                rejection="MultiplyUV operand unresolved",
                evidence=a.evidence + b.evidence,
            )
        return UvEvalResult(
            status="PROVEN",
            mesh_texcoord=None,
            node=node,
            evidence=a.evidence + b.evidence + ("MultiplyUV",),
        )
    if isinstance(node, ComposeUVNode):
        parts = [evaluate_uv_expr(op, params=params) for op in node.ops]
        if any(p.status != "PROVEN" for p in parts):
            return UvEvalResult(
                status="UNRESOLVED",
                rejection="ComposeUV operand unresolved",
                evidence=tuple(e for p in parts for e in p.evidence),
            )
        return UvEvalResult(
            status="PROVEN",
            mesh_texcoord=None,
            node=node,
            evidence=tuple(e for p in parts for e in p.evidence) + ("ComposeUV",),
        )
    return UvEvalResult(status="UNRESOLVED", rejection=f"unhandled node {type(node)}")


def to_ir_uv(node: UvExprNode, *, evidence: tuple[ProvenanceDiagnostic, ...] = ()):
    """Lower proven MeshUV / SelectUV / ScaleUV / … into forza_ir UVExpression."""
    from ..forza_ir import (
        MeshUV,
        ScaleUV,
        OffsetUV,
        RotateUV,
        SelectUV,
        ConstantScalar,
        Add,
    )

    if isinstance(node, MeshUVNode):
        return MeshUV(index=node.index, evidence=evidence)
    if isinstance(node, SelectUVNode):
        # IR SelectUV uses MaterialExpression condition — caller supplies resolved.
        raise TypeError("SelectUVNode must be evaluated before IR lowering")
    if isinstance(node, ScaleUVNode) and isinstance(node.scale, tuple):
        return ScaleUV(
            source=to_ir_uv(node.source, evidence=evidence),
            scale=node.scale,
            evidence=evidence,
        )
    if isinstance(node, OffsetUVNode):
        return OffsetUV(
            source=to_ir_uv(node.source, evidence=evidence),
            offset=node.offset,
            evidence=evidence,
        )
    if isinstance(node, RotateUVNode):
        return RotateUV(
            source=to_ir_uv(node.source, evidence=evidence),
            degrees=node.degrees,
            evidence=evidence,
        )
    raise TypeError(f"cannot lower {type(node).__name__} to IR yet")
