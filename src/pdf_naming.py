"""Human-readable, Windows-safe PDF filename planning."""

from __future__ import annotations

import re
from pathlib import Path


WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
MAX_SAFE_WINDOWS_PATH = 240


def sanitize_pdf_filename(title: str) -> str:
    """Preserve a title unless Windows requires a small, readable substitution."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(title))
    value = re.sub(r"\s+", " ", value).strip().rstrip(" .")
    if not value:
        value = "Untitled"
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value


def _safe_id(paper_id: str) -> str:
    return sanitize_pdf_filename(paper_id).replace(" ", "_")


def _with_id_suffix(stem: str, paper_id: str, destination_dir: Path) -> str:
    suffix = f"__{_safe_id(paper_id)}"
    available = MAX_SAFE_WINDOWS_PATH - len(str(destination_dir.resolve())) - 1 - len(suffix) - len(".pdf")
    if available < 1:
        raise ValueError(f"Destination path is too long even with a shortened filename: {destination_dir}")
    shortened = stem[:available].rstrip(" .-") or "Untitled"
    return f"{shortened}{suffix}.pdf"


def plan_title_pdf_filename(
    title: str,
    paper_id: str,
    destination_dir: Path,
    used_filenames: set[str],
) -> tuple[str, bool]:
    """Return a title-first filename and collision flag without touching the filesystem."""
    stem = sanitize_pdf_filename(title)
    filename = f"{stem}.pdf"
    collision = filename.casefold() in used_filenames
    path_too_long = len(str((destination_dir / filename).resolve())) > MAX_SAFE_WINDOWS_PATH
    if collision or path_too_long:
        filename = _with_id_suffix(stem, paper_id, destination_dir)
    if filename.casefold() in used_filenames:
        raise ValueError(f"Unable to produce a unique PDF filename for Paper_ID {paper_id}")
    used_filenames.add(filename.casefold())
    return filename, collision
