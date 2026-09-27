"""Backward-compatible import for the compact status/QC reporter."""

from .status_reporter import generate_report

__all__ = ["generate_report"]
