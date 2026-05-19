"""Shared DBOps configuration helpers."""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def load_dbops_yaml_config() -> dict[str, Any]:
    try:
        yaml = importlib.import_module("yaml")
        config_path = get_hermes_home() / "config.yaml"
        if not config_path.exists():
            return {}
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        section = cfg.get("dbops", {})
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load dbops config from config.yaml: %s", exc)
        return {}


def is_dbops_execute_enabled() -> bool:
    env_value = parse_bool(os.getenv("HERMES_DBOPS_EXECUTE_ENABLED"))
    if env_value is not None:
        return env_value
    yaml_value = parse_bool(load_dbops_yaml_config().get("execute_enabled"))
    if yaml_value is not None:
        return yaml_value
    return False
