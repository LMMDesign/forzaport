"""Production route states for sample-site completeness."""

from __future__ import annotations

# Authoritative runtime representations (per exact SHA).
FULL_SAMPLE_SITE_IR = "FULL_SAMPLE_SITE_IR"
PARTIAL_SAMPLE_SITE_WITH_BINDING_SEMANTICS = (
    "PARTIAL_SAMPLE_SITE_WITH_BINDING_SEMANTICS"
)
PRIMARY_TEXTUREBINDING_AUTHORITATIVE = "PRIMARY_TEXTUREBINDING_AUTHORITATIVE"
UNSUPPORTED_FAIL_CLOSED = "UNSUPPORTED_FAIL_CLOSED"

# extract_bindings.authoritative_model values
AUTH_FULL_SAMPLE_SITE_IR = "FULL_SAMPLE_SITE_IR"
AUTH_EVALUATED_SAMPLE_SITES = "EVALUATED_SAMPLE_SITES"  # legacy alias during cutover
AUTH_PRIMARY_PASS_TEXTURE_BINDINGS = "PRIMARY_PASS_TEXTURE_BINDINGS"

# Branch-status taxonomy (Blender-relevant sites).
PROVEN_UNCONDITIONAL = "PROVEN_UNCONDITIONAL"
EXECUTABLE_PREDICATE = "EXECUTABLE_PREDICATE"
COMPILE_TIME_VARIANT = "COMPILE_TIME_VARIANT"
PASS_SCOPED = "PASS_SCOPED"
NOT_RELEVANT_TO_BLENDER = "NOT_RELEVANT_TO_BLENDER"
NO_PREDICATE_RECOVERED = "NO_PREDICATE_RECOVERED"
UNRESOLVED_PREDICATE = "UNRESOLVED_PREDICATE"
INACTIVE_OR_OPTIMISED = "INACTIVE_OR_OPTIMISED"

# Exact SHAs with a ForzaMaterialIR production evaluator.
PRODUCTION_IR_SHADERBIN_SHA256: frozenset[str] = frozenset(
    {
        "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16",  # car_standard
        "35bccc9b43710c374b94c8800436dce8a44c607ee778f65764f31f0bc56cc515",  # car_label
        "f18954b13a8d117a6e442f153c2138cec6f31154d80430d0b86c458725a597b3",  # car_carbonfiber
        "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178",  # emissive
        "af463726a228752c328abd847868a90bf69110463594a69851ebee1ce9034523",  # fabric
        "ce460364d8151e819f056552d274353ba2657aff2ff718ed1239db02b7ffebb3",  # paint
        "373050795197539169f78b29a08424add9f313e99c8eab0a33a6658a40987c88",  # coated
        "3f988df89a12b4a008777463a56eee840a5c3488c6af3ad53f69c2f4cb861d09",  # glass
        "47f92e42f2d1991ae07a36364216402e53801bc6be9efa765ee49fe64a51d0e9",  # reflector
        "384692abfe3daace9b29f2580c60c23a171192e8c5e9fd6b3be10989b255f106",  # brakerotor
        "831b4866240da29fa4bf6706b13ceab4f4259e2cb4f32eb7b10da687f7284f53",  # livery_transmissive
        "f1617a600d251bc8acb78abf939ce6b1b223ea23afee8f4fb592094c135051bb",  # livery
    }
)

PRODUCTION_IR_SHADER_NAMES: frozenset[str] = frozenset(
    {
        "car_standard",
        "car_label",
        "car_carbonfiber",
        "car_standard_emissive",
        "car_standard_fabric",
        "car_automotive_paint",
        "car_standard_coated",
        "car_glass_detailed",
        "car_reflector",
        "car_brakerotor",
        "car_livery_transmissive",
        "car_livery",
    }
)


def has_ir_evaluator(shaderbin_sha256: str | None, shader_name: str | None = None) -> bool:
    """Production gate: exact shaderbin SHA only (no shader-name-only approval).

    ``shader_name`` is retained for call-site compatibility / diagnostics and
    is intentionally ignored for the approval decision.
    """
    del shader_name  # name-only approval removed from production
    if shaderbin_sha256 and shaderbin_sha256 in PRODUCTION_IR_SHADERBIN_SHA256:
        return True
    return False
