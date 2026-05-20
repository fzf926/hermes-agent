"""DBOps volume-routing configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from tools.dbops_config import load_dbops_yaml_config


@dataclass(frozen=True)
class DBOpsDeliveryConfig:
    excel_threshold_min: int = 21
    excel_threshold_max: int = 5000
    export_threshold: int = 5001
    pagination_page_size: int = 1000
    export_poll_interval_sec: float = 2.0
    export_poll_timeout_sec: float = 180.0
    exports_dir: Path | None = None
    public_base_url: str = ""


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_dbops_delivery_config() -> DBOpsDeliveryConfig:
    cfg = load_dbops_yaml_config()
    home = get_hermes_home()
    exports_raw = str(cfg.get("exports_dir") or os.getenv("HERMES_DBOPS_EXPORTS_DIR") or "").strip()
    if exports_raw:
        exports_dir = Path(exports_raw).expanduser()
    else:
        exports_dir = home / "dbops_exports"

    public = str(
        cfg.get("public_base_url") or os.getenv("HERMES_API_PUBLIC_BASE_URL") or ""
    ).strip().rstrip("/")

    return DBOpsDeliveryConfig(
        excel_threshold_min=_parse_int(
            cfg.get("excel_threshold_min", os.getenv("HERMES_DBOPS_EXCEL_MIN", 21)),
            21,
        ),
        excel_threshold_max=_parse_int(
            cfg.get("excel_threshold_max", os.getenv("HERMES_DBOPS_EXCEL_MAX", 5000)),
            5000,
        ),
        export_threshold=_parse_int(
            cfg.get("export_threshold", os.getenv("HERMES_DBOPS_EXPORT_THRESHOLD", 5001)),
            5001,
        ),
        pagination_page_size=min(
            1000,
            max(1, _parse_int(cfg.get("pagination_page_size", 1000), 1000)),
        ),
        export_poll_interval_sec=float(
            cfg.get("export_poll_interval_sec", 2.0) or 2.0
        ),
        export_poll_timeout_sec=float(
            cfg.get("export_poll_timeout_sec", 180.0) or 180.0
        ),
        exports_dir=exports_dir,
        public_base_url=public,
    )


def build_hermes_download_url(public_base_url: str, export_uid: str) -> str:
    path = f"http://106.53.130.108:8642/api/chat/sql-exports/{export_uid}/download"
    if public_base_url:
        return f"{public_base_url}{path}"
    return path
