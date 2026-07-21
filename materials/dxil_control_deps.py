"""DXIL CFG + control-dependence analysis for sample-site predicates.

Does **not** invent predicates. Empty recovery stays ``NO_PREDICATE_RECOVERED``
until a grounded CBMP/icmp/branch chain is proven.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


_LABEL = re.compile(r"^;?\s*<label>:(\d+)\b|^(\d+):\s*;\s*preds\s*=")
_LABEL_ALT = re.compile(r"^(\d+):\s*")
_BR_COND = re.compile(
    r"\bbr\s+i1\s+%(\d+)\s*,\s*label\s+%(\d+)\s*,\s*label\s+%(\d+)"
)
_BR_UNCOND = re.compile(r"\bbr\s+label\s+%(\d+)\b")
_ICMP = re.compile(
    r"%(\d+)\s*=\s*icmp\s+(\w+)\s+i32\s+%(\d+)\s*,\s*(.+?)(?:\s*;|$)"
)
_BITCAST = re.compile(r"%(\d+)\s*=\s*bitcast\s+float\s+%(\d+)\s+to\s+i32")
_EXTRACT = re.compile(
    r"%(\d+)\s*=\s*extractvalue\s+%dx\.types\.CBufRet\.\w+\s+%(\d+)\s*,\s*(\d+)"
)
_CBUF = re.compile(
    r"cbufferLoadLegacy\.\w+\(i32\s+\d+,\s*%dx\.types\.Handle\s+%\d+,\s*i32\s+(\d+)\)"
)
_SAMPLE = re.compile(
    r"%(\d+)\s*=\s*call\s+%dx\.types\.ResRet\.\w+\s+@dx\.op\.(sample\w*)\."
)
_ASSIGN = re.compile(r"^\s*%(\d+)\s*=")


@dataclass
class BasicBlock:
    label: int
    start_line: int
    end_line: int
    successors: list[int] = field(default_factory=list)
    branch_cond: int | None = None  # SSA of i1 condition when conditional


@dataclass
class ControlPredicate:
    """One recovered controlling predicate for a sample site."""

    condition_ssa: str
    polarity: str  # "true_branch" | "false_branch"
    icmp_pred: str | None = None
    compared_ssa: str | None = None
    compared_imm: str | None = None
    cb_row: int | None = None
    cb_component: int | None = None
    param_hash: int | None = None
    evidence: tuple[str, ...] = ()


@dataclass
class SiteControlAnalysis:
    instruction_id: str
    block_label: int | None
    control_predicates: list[ControlPredicate] = field(default_factory=list)
    data_dep_cb_hashes: list[int] = field(default_factory=list)
    status: str = "NO_PREDICATE_RECOVERED"
    # PROVEN_UNCONDITIONAL | EXECUTABLE_PREDICATE | UNRESOLVED_PREDICATE |
    # NO_PREDICATE_RECOVERED
    evidence: list[str] = field(default_factory=list)


def parse_cfg(ps_txt: str) -> tuple[dict[int, BasicBlock], dict[str, int]]:
    """Parse dxc LLVM dump into basic blocks + instruction SSA → block label."""
    lines = ps_txt.splitlines()
    # Find function entry — treat first instruction region as label 0 if needed.
    blocks: dict[int, BasicBlock] = {}
    label_starts: list[tuple[int, int]] = []  # (line_idx, label)
    for i, ln in enumerate(lines):
        m = _LABEL.match(ln.strip()) or re.match(
            r"^; <label>:(\d+)\b", ln.strip()
        )
        if m:
            lab = int(m.group(1) or m.group(2))
            label_starts.append((i, lab))
            continue
        # dxc sometimes emits bare "N:" headers
        m2 = re.match(r"^(\d+):\s+; preds =", ln.strip())
        if m2:
            label_starts.append((i, int(m2.group(1))))

    if not label_starts:
        # Single-block function: synthetic label 0 covering all assignments.
        blocks[0] = BasicBlock(label=0, start_line=0, end_line=len(lines) - 1)
        instr_block: dict[str, int] = {}
        for i, ln in enumerate(lines):
            am = _ASSIGN.match(ln)
            if am:
                instr_block[am.group(1)] = 0
        return blocks, instr_block

    # Ensure entry before first label is block -1 or first label's preds region
    if label_starts[0][0] > 0:
        label_starts.insert(0, (0, -1))

    for idx, (start, lab) in enumerate(label_starts):
        end = (
            label_starts[idx + 1][0] - 1
            if idx + 1 < len(label_starts)
            else len(lines) - 1
        )
        blocks[lab] = BasicBlock(label=lab, start_line=start, end_line=end)

    # Successors from terminators
    for lab, bb in blocks.items():
        region = "\n".join(lines[bb.start_line : bb.end_line + 1])
        cm = _BR_COND.search(region)
        if cm:
            bb.branch_cond = int(cm.group(1))
            bb.successors = [int(cm.group(2)), int(cm.group(3))]
            continue
        um = list(_BR_UNCOND.finditer(region))
        if um:
            bb.successors = [int(um[-1].group(1))]

    instr_block = {}
    for lab, bb in blocks.items():
        for i in range(bb.start_line, bb.end_line + 1):
            am = _ASSIGN.match(lines[i])
            if am:
                instr_block[am.group(1)] = lab
            sm = _SAMPLE.search(lines[i])
            if sm:
                instr_block[sm.group(1)] = lab

    return blocks, instr_block


def _reachable(blocks: dict[int, BasicBlock], start: int) -> set[int]:
    seen: set[int] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in blocks:
            continue
        seen.add(cur)
        stack.extend(blocks[cur].successors)
    return seen


def _attribute_cond_to_cb(
    cond_ssa: str,
    defs: dict[str, str],
    cbmp: dict[int, int],
) -> tuple[int | None, int | None, int | None, str | None, str | None, list[str]]:
    """Walk icmp → bitcast/extract → cbufferLoadLegacy → CBMP hash."""
    evidence: list[str] = []
    rhs = defs.get(cond_ssa, "")
    # cond may itself be icmp result
    im = re.search(
        r"icmp\s+(\w+)\s+i32\s+%(\d+)\s*,\s*(.+?)(?:\s*;|$)", rhs
    )
    icmp_pred = None
    compared = None
    imm = None
    if im:
        icmp_pred = im.group(1)
        compared = im.group(2)
        imm = im.group(3).strip()
        evidence.append(f"icmp {icmp_pred} %{compared}, {imm}")
    else:
        # cond SSA might be the icmp destination already looked up
        for rid, rr in defs.items():
            if rid != cond_ssa:
                continue
        # Search defs where this SSA is produced as icmp
        for rid, rr in defs.items():
            if f"%{cond_ssa}" in rr and "icmp" in rr:
                pass
        # Direct: look for `%cond = icmp ...`
        for rid, rr in defs.items():
            if rid == cond_ssa and "icmp" in rr:
                im2 = re.search(
                    r"icmp\s+(\w+)\s+i32\s+%(\d+)\s*,\s*(.+?)(?:\s*;|$)", rr
                )
                if im2:
                    icmp_pred = im2.group(1)
                    compared = im2.group(2)
                    imm = im2.group(3).strip()
                    evidence.append(f"icmp {icmp_pred} %{compared}, {imm}")

    if compared is None:
        return None, None, None, icmp_pred, imm, evidence

    # Follow bitcast float→i32
    src = compared
    src_rhs = defs.get(src, "")
    bm = re.search(r"bitcast float %(\d+) to i32", src_rhs)
    if bm:
        src = bm.group(1)
        evidence.append(f"bitcast from %{src}")
        src_rhs = defs.get(src, "")

    em = re.search(
        r"extractvalue %dx\.types\.CBufRet\.\w+ %(\d+), (\d+)", src_rhs
    )
    if not em:
        evidence.append("no extractvalue/cbuffer attribution")
        return None, None, None, icmp_pred, imm, evidence
    load_id, comp = em.group(1), int(em.group(2))
    load_rhs = defs.get(load_id, "")
    cm = _CBUF.search(load_rhs)
    if not cm:
        evidence.append("no cbufferLoadLegacy")
        return None, None, None, icmp_pred, imm, evidence
    row = int(cm.group(1))
    evidence.append(f"cbufferLoadLegacy row={row} comp={comp}")

    # Resolve CBMP hash from (row, component) via byte offsets
    param_hash = None
    for h, off in cbmp.items():
        if off // 16 == row and (off % 16) // 4 == comp:
            param_hash = int(h)
            evidence.append(f"CBMP hash=0x{param_hash & 0xFFFFFFFF:08X}")
            break
    if param_hash is None:
        evidence.append("CBMP slot not declared — unresolved attribution")
    return row, comp, param_hash, icmp_pred, imm, evidence


def analyze_sample_control(
    ps_txt: str,
    *,
    instruction_id: str,
    cbmp: dict[int, int],
    data_dep_hashes: list[int] | None = None,
) -> SiteControlAnalysis:
    """Control-dependence + data-dep predicates for one sample instruction."""
    from . import shader_bindings as sb

    rid = instruction_id.lstrip("%")
    out = SiteControlAnalysis(instruction_id=instruction_id, block_label=None)
    data_dep_hashes = list(data_dep_hashes or [])
    out.data_dep_cb_hashes = data_dep_hashes

    blocks, instr_block = parse_cfg(ps_txt)
    out.block_label = instr_block.get(rid)
    if out.block_label is None:
        out.status = "NO_PREDICATE_RECOVERED"
        out.evidence.append("sample instruction block not located in CFG")
        return out

    lines = ps_txt.splitlines()
    defs, _ = sb._build_ssa(lines)

    # Control deps: conditional branches where sample is reachable from exactly
    # one successor.
    sample_bb = out.block_label
    for lab, bb in blocks.items():
        if bb.branch_cond is None or len(bb.successors) != 2:
            continue
        t_lab, f_lab = bb.successors[0], bb.successors[1]
        t_reach = _reachable(blocks, t_lab)
        f_reach = _reachable(blocks, f_lab)
        in_t = sample_bb in t_reach
        in_f = sample_bb in f_reach
        if in_t == in_f:
            continue  # both or neither — not a selective control dep
        polarity = "true_branch" if in_t and not in_f else "false_branch"
        row, comp, ph, icmp_pred, imm, ev = _attribute_cond_to_cb(
            str(bb.branch_cond), defs, cbmp
        )
        pred = ControlPredicate(
            condition_ssa=str(bb.branch_cond),
            polarity=polarity,
            icmp_pred=icmp_pred,
            compared_imm=imm,
            cb_row=row,
            cb_component=comp,
            param_hash=ph,
            evidence=tuple(ev),
        )
        out.control_predicates.append(pred)
        out.evidence.append(
            f"control_dep branch_bb={lab} cond=%{bb.branch_cond} "
            f"polarity={polarity} param={ph}"
        )

    if out.control_predicates or data_dep_hashes:
        # If any control pred lacks CBMP hash → unresolved
        if any(p.param_hash is None for p in out.control_predicates):
            out.status = "UNRESOLVED_PREDICATE"
            out.evidence.append(
                "control dependence found but CBMP attribution incomplete"
            )
        else:
            out.status = "EXECUTABLE_PREDICATE"
        return out

    # No control deps and no data-dep gates: only PROVEN_UNCONDITIONAL when CFG
    # was successfully parsed and we searched all conditional branches.
    if len(blocks) <= 1:
        out.status = "NO_PREDICATE_RECOVERED"
        out.evidence.append(
            "single/synthetic block — insufficient CFG to prove unconditional"
        )
        return out

    cond_branches = sum(1 for b in blocks.values() if b.branch_cond is not None)
    if cond_branches == 0:
        out.status = "PROVEN_UNCONDITIONAL"
        out.evidence.append(
            f"CFG blocks={len(blocks)} with zero conditional branches; "
            "sample has no data-dep CB gates"
        )
        return out

    # Conditional branches exist but none selectively control this sample
    # (sample reachable from both sides of every branch, or from neither of
    # the selective sets). That is consistent with unconditional execution
    # within this pass — prove it only when sample is reachable from entry
    # along all paths... We use a conservative rule: if every conditional
    # branch has the sample in BOTH successor reachability sets (or the
    # branch does not reach the sample at all), mark PROVEN_UNCONDITIONAL.
    selective = False
    for lab, bb in blocks.items():
        if bb.branch_cond is None or len(bb.successors) != 2:
            continue
        t_reach = _reachable(blocks, bb.successors[0])
        f_reach = _reachable(blocks, bb.successors[1])
        in_t = sample_bb in t_reach
        in_f = sample_bb in f_reach
        if in_t != in_f:
            selective = True
            break
    if not selective:
        out.status = "PROVEN_UNCONDITIONAL"
        out.evidence.append(
            f"CFG conditional_branches={cond_branches}; sample bb={sample_bb} "
            "not selectively control-dependent on any recovered branch"
        )
    else:
        out.status = "NO_PREDICATE_RECOVERED"
        out.evidence.append(
            "selective control dependence suspected but attribution failed"
        )
    return out


def predicates_as_contract_rows(analysis: SiteControlAnalysis) -> list[dict[str, Any]]:
    """JSON-serialisable branch_predicates for contracts."""
    rows: list[dict[str, Any]] = []
    for p in analysis.control_predicates:
        if p.param_hash is None:
            continue
        true_when = "nonzero"
        if p.icmp_pred == "eq" and p.compared_imm and "0" in (p.compared_imm or ""):
            # icmp eq 0 → true_branch means param==0
            true_when = "zero" if p.polarity == "true_branch" else "nonzero"
        elif p.icmp_pred == "ne":
            true_when = "nonzero" if p.polarity == "true_branch" else "zero"
        rows.append(
            {
                "param_hash": f"0x{p.param_hash & 0xFFFFFFFF:08X}",
                "type": 3,
                "operator": true_when,
                "provenance": "dxil_control_dependence",
                "evidence": list(p.evidence),
            }
        )
    for h in analysis.data_dep_cb_hashes:
        rows.append(
            {
                "param_hash": f"0x{h & 0xFFFFFFFF:08X}",
                "type": 3,
                "operator": "nonzero",
                "provenance": "dxil_data_dependence",
            }
        )
    return rows
