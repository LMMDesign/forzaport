"""Immutable per-material evaluation context — built once at resolve boundary.

Game-file instance evaluation happens exactly once. Shared static analysis
lives on ``ShaderStaticAnalysis``. Capability/slot structures are derived
lazily on MaterialResolution — never stored on this context.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

from .context_cache import (
    MaterialContextCache,
    build_context_cache_key,
    default_context_cache,
)
from .model import ProvenanceDiagnostic
from .pipeline_metrics import METRICS
from .resolved_texture_resource import (
    ResolvedTextureResource,
    resolve_texture_resources,
)
from .sample_site_eval import EvaluatedMaterialSampleSites
from .shader_static_analysis import (
    ShaderStaticAnalysis,
    get_or_create_static_analysis,
)
from .texture_source_memo import TextureResourceIdentity, TextureSourceMemo


# Evidence marker: capability slots were not built on the IR route.
IR_ROUTE_SLOTS_DEFERRED = "IR_ROUTE_SLOTS_DEFERRED"


@dataclass(frozen=True)
class MaterialSourceIdentity:
    instance_key: str
    shader_name: str
    source_mati_path: str | None = None


@dataclass(frozen=True)
class ShaderEvalIdentity:
    shader_name: str
    shaderbin_sha256: str
    permutation: str = "CarLightScenario"
    archive_path: str | None = None
    pso_sha256: str = ""


@dataclass(frozen=True)
class MaterialEvaluationContext:
    """Immutable instance evaluation bag (no resolution/capability back-refs)."""

    source: MaterialSourceIdentity
    shader: ShaderEvalIdentity
    serialized_schema: Mapping[str, Any] | None
    effective_parameters: Mapping[Any, Any]
    texture_resources: Mapping[int, ResolvedTextureResource]
    static_analysis: ShaderStaticAnalysis | None
    # Legacy carrier (ShaderBindings); prefer static_analysis + evaluated_sites.
    static_pass_analysis: Any
    evaluated_sites: EvaluatedMaterialSampleSites | None
    variant_result: Any
    alpha_semantics: Any | None
    diagnostics: tuple[ProvenanceDiagnostic, ...]
    bindings: Any = None
    txmp: Mapping[Any, Any] = field(default_factory=dict)
    spmp: Mapping[Any, Any] = field(default_factory=dict)
    cbmp: Mapping[Any, Any] = field(default_factory=dict)
    media_root: str = ""
    game_key: str = "fh6"
    # Per-context texture resolve memo (mutable helper; not part of semantic state).
    _texture_memo: TextureSourceMemo | None = field(default=None, repr=False, compare=False)

    def with_alpha_semantics(self, alpha_semantics: Any) -> MaterialEvaluationContext:
        return replace(self, alpha_semantics=alpha_semantics)

    def with_diagnostics(
        self, extra: tuple[ProvenanceDiagnostic, ...]
    ) -> MaterialEvaluationContext:
        if not extra:
            return self
        return replace(self, diagnostics=self.diagnostics + tuple(extra))

    def resolve_texture(
        self,
        path: str,
        resolver: Any,
        *,
        texture_register: int | None = None,
        sampler_register: int | None = None,
        txmp_name_hash: int | None = None,
        override_identity: str = "",
    ):
        memo = self._texture_memo
        if memo is None:
            from .texture_source import resolve_texture_source

            return resolve_texture_source(path, resolver, media_root=self.media_root or None)
        ident = TextureResourceIdentity(
            shaderbin_sha256=self.shader.shaderbin_sha256,
            pass_name=self.shader.permutation,
            texture_register=texture_register,
            sampler_register=sampler_register,
            txmp_path=path or "",
            txmp_name_hash=txmp_name_hash,
            override_identity=override_identity,
        )
        return memo.resolve(ident, resolver, media_root=self.media_root or None)

    def shallow_size_bytes(self) -> int:
        import sys

        return sys.getsizeof(self)


def _freeze_mapping(m: Mapping | dict | None) -> Mapping:
    if m is None:
        return MappingProxyType({})
    if isinstance(m, MappingProxyType):
        return m
    return MappingProxyType(dict(m))


def create_material_evaluation_context(
    *,
    instance_key: str,
    material,
    media_root: str,
    game_key: str = "fh6",
    bindings=None,
    context_cache: MaterialContextCache | None = None,
) -> MaterialEvaluationContext:
    """Build game-file evaluation exactly once for one material instance."""
    from .shader_bindings import extract_bindings

    cache = context_cache if context_cache is not None else default_context_cache()

    with METRICS.stage("create_material_evaluation_context"):
        shader_name = getattr(material, "shader_name", None) or ""
        params = getattr(material, "parameters", None) or {}
        cbmp = getattr(material, "cbmp", None) or {}
        txmp = getattr(material, "txmp", None) or {}
        spmp = getattr(material, "spmp", None) or {}

        if bindings is None:
            bindings = extract_bindings(
                media_root=media_root,
                shader_name=shader_name,
                params=params,
                cbmp=cbmp,
                game_key=game_key,
                material_instance_key=instance_key,
            )

        hashes = getattr(bindings, "source_hashes", None) or {}
        sha = str(hashes.get("shaderbin_sha256") or "")
        permutation = str(hashes.get("primary_pass") or "CarLightScenario")
        pso_sha = str(hashes.get("pso_sha256") or "")
        archive = f"{media_root}/cars/_library/shaders/{shader_name}.zip".replace(
            "\\", "/"
        )

        cache_key = build_context_cache_key(
            instance_key=instance_key,
            material=material,
            media_root=media_root,
            game_key=game_key,
            shaderbin_sha256=sha,
            pass_name=permutation,
            pso_sha256=pso_sha,
            archive_path=archive,
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        evaluated = getattr(bindings, "evaluated_sites", None)
        variant = getattr(evaluated, "variant", None) if evaluated is not None else None
        schema = (
            getattr(evaluated, "serialized_schema", None) if evaluated is not None else None
        )
        if schema is None:
            schema = getattr(bindings, "serialized_schema", None)

        with METRICS.stage("resource_resolution"):
            try:
                from .name_hashes import require_name

                def _lookup(h: int) -> str:
                    return require_name(h, context=f"{shader_name} TXMP")

                resources = resolve_texture_resources(
                    params=params,
                    txmp=txmp,
                    name_lookup=_lookup,
                    source_mati=getattr(bindings, "source_mati_path", None),
                )
            except Exception:
                resources = {}

        static = get_or_create_static_analysis(
            shaderbin_sha256=sha,
            pass_name=permutation,
            pso_sha256=pso_sha,
            archive_path=archive,
            platform=game_key,
            static_bindings_template=bindings,
            evidence=(f"shared_static sha={sha[:16]}",),
        )

        diagnostics: tuple[ProvenanceDiagnostic, ...] = (
            ProvenanceDiagnostic(
                kind="context",
                detail=(
                    f"MaterialEvaluationContext created sha={sha[:16]}… "
                    f"auth={getattr(bindings, 'authoritative_model', None)}"
                ),
                source="materials.evaluation_context",
            ),
        )

        memo = TextureSourceMemo()
        ctx = MaterialEvaluationContext(
            source=MaterialSourceIdentity(
                instance_key=instance_key,
                shader_name=shader_name,
                source_mati_path=getattr(bindings, "source_mati_path", None),
            ),
            shader=ShaderEvalIdentity(
                shader_name=shader_name,
                shaderbin_sha256=sha,
                permutation=permutation,
                archive_path=archive,
                pso_sha256=pso_sha,
            ),
            serialized_schema=_freeze_mapping(schema) if schema else None,
            effective_parameters=_freeze_mapping(params),
            texture_resources=_freeze_mapping(resources),
            static_analysis=static,
            static_pass_analysis=bindings,
            evaluated_sites=evaluated,
            variant_result=variant,
            alpha_semantics=None,
            diagnostics=diagnostics,
            bindings=bindings,
            txmp=_freeze_mapping(txmp),
            spmp=_freeze_mapping(spmp),
            cbmp=_freeze_mapping(cbmp),
            media_root=media_root,
            game_key=game_key,
            _texture_memo=memo,
        )
        METRICS.note_context_size(ctx.shallow_size_bytes())
        cache.put(cache_key, ctx)
        return ctx


def capability_is_ir_deferred(cap) -> bool:
    for e in getattr(cap, "evidence", None) or ():
        if IR_ROUTE_SLOTS_DEFERRED in (getattr(e, "detail", "") or ""):
            return True
    return False
