"""Local evidence extraction layer for material analysis."""

from .schema import EvidenceValidationError, validate_evidence

__all__ = ["EvidenceValidationError", "validate_evidence"]
