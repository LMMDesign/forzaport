"""Exact sample-site identity — never key semantics by texture register alone."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShaderSampleSiteIdentity:
    """Unique identity for one DXIL texture-sample instruction in one PSO."""

    shaderbin_sha256: str
    full_archive_member: str
    pso_sha256: str
    variant: str
    scenario: str
    stage: str
    instruction_id: str
    texture_register: int
    sampler_register: int | None = None
    sample_site_index: int = 0

    def as_key(self) -> str:
        smp = "none" if self.sampler_register is None else str(self.sampler_register)
        member = self.full_archive_member.replace("\\", "/")
        return (
            f"{self.shaderbin_sha256[:16]}|{member}|{self.pso_sha256[:12]}|"
            f"{self.variant or 'root'}|{self.scenario}|{self.stage}|"
            f"{self.instruction_id}|t{self.texture_register}|s{smp}|"
            f"i{self.sample_site_index}"
        )
