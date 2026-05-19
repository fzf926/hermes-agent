"""Hermes-local SQL Excel export file resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.dbops_delivery_config import load_dbops_delivery_config


def resolve_export_file_path(export_uid: str) -> Path | None:
    uid = (export_uid or "").strip()
    if not uid or not uid.isalnum():
        return None
    cfg = load_dbops_delivery_config()
    base = cfg.exports_dir
    if not base:
        return None
    path = base / f"{uid}.xlsx"
    if path.is_file():
        return path
    return None


def export_download_filename(export_uid: str) -> str:
    return f"dbops-query-{export_uid}.xlsx"
