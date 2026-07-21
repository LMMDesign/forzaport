"""UVChoice contracts keyed by exact shaderbin SHA (not global policy).

``UVChoice_OnCh1_OffCh2`` was proven on car_standard CarLightScenario DXIL.
It must not be applied to another SHA without independent evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import ProvenanceDiagnostic

# Proven on car_standard exact production SHA only.
CAR_STANDARD_SHADERBIN_SHA256 = (
    "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16"
)
# Independent DXIL proof on car_standard_emissive SimpleCarLightScenario
# (same NameHash / polarity; separate SHA entry — not cross-family reuse).
CAR_STANDARD_EMISSIVE_SHADERBIN_SHA256 = (
    "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178"
)
# Independent DXIL proof on car_standard_fabric CarLightScenario
# (CB row 22.0 UVChoice_OnCh1_OffCh2; same polarity; separate SHA entry).
CAR_STANDARD_FABRIC_SHADERBIN_SHA256 = (
    "af463726a228752c328abd847868a90bf69110463594a69851ebee1ce9034523"
)
# Independent DXIL proof on car_label CarLightScenario
# (CB row 9.0 UVChoice_OnCh1_OffCh2; same polarity; separate SHA entry).
CAR_LABEL_SHADERBIN_SHA256 = (
    "35bccc9b43710c374b94c8800436dce8a44c607ee778f65764f31f0bc56cc515"
)
# Independent DXIL proof on car_standard_coated CarLightScenario
# (CB row 28.0 UVChoice_OnCh1_OffCh2; same polarity; separate SHA entry).
CAR_STANDARD_COATED_SHADERBIN_SHA256 = (
    "373050795197539169f78b29a08424add9f313e99c8eab0a33a6658a40987c88"
)
UV_CHOICE_ON_CH1_OFF_CH2 = 0x402B8ED0
UV_CHOICE_TRUE_TEXCOORD = 0
UV_CHOICE_FALSE_TEXCOORD = 1


@dataclass(frozen=True)
class UvChoiceContract:
    shaderbin_sha256: str
    param_hash: int
    param_name: str
    true_texcoord: int
    false_texcoord: int
    applies_to_txmp: tuple[str, ...]
    evidence_pass: str
    evidence: str


UV_CHOICE_BY_SHA: dict[str, UvChoiceContract] = {
    CAR_STANDARD_SHADERBIN_SHA256: UvChoiceContract(
        shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        param_hash=UV_CHOICE_ON_CH1_OFF_CH2,
        param_name="UVChoice_OnCh1_OffCh2",
        true_texcoord=UV_CHOICE_TRUE_TEXCOORD,
        false_texcoord=UV_CHOICE_FALSE_TEXCOORD,
        applies_to_txmp=(
            "BaseColorAlpha",
            "BaseColorAlpha_1",
            "Alpha",
            "Normal",
            "RoughMetalAO",
        ),
        evidence_pass="CarLightScenario",
        evidence=(
            "DXIL car_standard CarLightScenario: CB load -> icmp eq 0 -> "
            "phi loadInput sigId 1 (false) vs sigId 0 (true); "
            "feeds t16/t17/t20/t26 sample coords. Exact SHA only."
        ),
    ),
    CAR_STANDARD_EMISSIVE_SHADERBIN_SHA256: UvChoiceContract(
        shaderbin_sha256=CAR_STANDARD_EMISSIVE_SHADERBIN_SHA256,
        param_hash=UV_CHOICE_ON_CH1_OFF_CH2,
        param_name="UVChoice_OnCh1_OffCh2",
        true_texcoord=UV_CHOICE_TRUE_TEXCOORD,
        false_texcoord=UV_CHOICE_FALSE_TEXCOORD,
        applies_to_txmp=("Alpha", "BaseColorAlpha", "Normal", "RoughMetalAO"),
        evidence_pass="SimpleCarLightScenario",
        evidence=(
            "Independent DXIL on car_standard_emissive SimpleCarLightScenario: "
            "c13.0 UVChoice (CBMP 0x402B8ED0) icmp eq 0 -> phi TEXCOORD1 "
            "(false) vs TEXCOORD0 (true); feeds Alpha t16 sample coords %526/%527."
        ),
    ),
    CAR_STANDARD_FABRIC_SHADERBIN_SHA256: UvChoiceContract(
        shaderbin_sha256=CAR_STANDARD_FABRIC_SHADERBIN_SHA256,
        param_hash=UV_CHOICE_ON_CH1_OFF_CH2,
        param_name="UVChoice_OnCh1_OffCh2",
        true_texcoord=UV_CHOICE_TRUE_TEXCOORD,
        false_texcoord=UV_CHOICE_FALSE_TEXCOORD,
        applies_to_txmp=(
            "BaseColorAlpha",
            "Alpha",
            "Normal",
            "RoughMetalAO",
        ),
        evidence_pass="CarLightScenario",
        evidence=(
            "Independent DXIL on car_standard_fabric CarLightScenario: "
            "CB row 22.0 (declared UVChoice_OnCh1_OffCh2) icmp eq 0 -> "
            "phi loadInput sigId 1 (%99/%100, false) vs sigId 0 (%101/%102, true); "
            "feeds BaseColorAlpha/Normal/RoughMetalAO/Alpha multi-UV samples. "
            "Exact SHA af463726… only — not reused from car_standard."
        ),
    ),
    CAR_LABEL_SHADERBIN_SHA256: UvChoiceContract(
        shaderbin_sha256=CAR_LABEL_SHADERBIN_SHA256,
        param_hash=UV_CHOICE_ON_CH1_OFF_CH2,
        param_name="UVChoice_OnCh1_OffCh2",
        true_texcoord=UV_CHOICE_TRUE_TEXCOORD,
        false_texcoord=UV_CHOICE_FALSE_TEXCOORD,
        applies_to_txmp=("BaseColorAlpha", "Alpha", "Normal", "RoughMetalAO"),
        evidence_pass="CarLightScenario",
        evidence=(
            "Independent DXIL on car_label CarLightScenario: "
            "CB row 9.0 UVChoice_OnCh1_OffCh2 icmp eq 0 -> "
            "phi loadInput sigId 1 (%70/%71, false) vs sigId 0 (%72/%73, true). "
            "Exact SHA 35bccc9b… only."
        ),
    ),
    CAR_STANDARD_COATED_SHADERBIN_SHA256: UvChoiceContract(
        shaderbin_sha256=CAR_STANDARD_COATED_SHADERBIN_SHA256,
        param_hash=UV_CHOICE_ON_CH1_OFF_CH2,
        param_name="UVChoice_OnCh1_OffCh2",
        true_texcoord=UV_CHOICE_TRUE_TEXCOORD,
        false_texcoord=UV_CHOICE_FALSE_TEXCOORD,
        applies_to_txmp=("BaseColorAlpha", "Alpha", "Normal", "RoughMetalAO"),
        evidence_pass="CarLightScenario",
        evidence=(
            "Independent DXIL on car_standard_coated CarLightScenario: "
            "CB row 28.0 UVChoice_OnCh1_OffCh2 icmp eq 0 -> "
            "phi loadInput sigId 1 (%102/%103, false) vs sigId 0 (%104/%105, true). "
            "Exact SHA 37305079… only."
        ),
    ),
}


def resolve_uv_choice_texcoord(
    params: dict,
    *,
    shaderbin_sha256: str | None = None,
) -> tuple[int, ProvenanceDiagnostic] | None:
    """Apply UVChoice only when the exact SHA has a proven contract.

    Without ``shaderbin_sha256``, or for an unproven SHA, returns None
    (fail closed — never invent TEXCOORD from another family's DXIL).
    """
    if not shaderbin_sha256:
        return None
    contract = UV_CHOICE_BY_SHA.get(shaderbin_sha256)
    if contract is None:
        return None
    p = params.get(contract.param_hash)
    if p is None:
        p = params.get(contract.param_hash & 0xFFFFFFFF)
    if p is None or getattr(p, "type", None) != 3:
        return None
    texcoord = (
        contract.true_texcoord if bool(p.value) else contract.false_texcoord
    )
    return (
        texcoord,
        ProvenanceDiagnostic(
            kind="UVChoice_OnCh1_OffCh2",
            detail=(
                f"MatI bool={bool(p.value)} -> TEXCOORD{texcoord} "
                f"(sha={shaderbin_sha256[:12]}… pass={contract.evidence_pass}; "
                f"{contract.evidence})"
            ),
            source="materials.uv.uv_choice_contracts",
        ),
    )
