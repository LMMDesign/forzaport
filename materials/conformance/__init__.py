"""Material conformance tooling (Milestone A+). Does not alter production resolve."""

from .corpus import (
    FAILURE_CLASSES,
    DEFAULT_CORPUS,
    CorpusCar,
    scan_corpus,
    write_family_catalog_markdown,
)

__all__ = (
    "FAILURE_CLASSES",
    "DEFAULT_CORPUS",
    "CorpusCar",
    "scan_corpus",
    "write_family_catalog_markdown",
)
