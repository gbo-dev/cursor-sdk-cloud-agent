"""Shared Cursor SDK model selection for pipeline scripts."""

from __future__ import annotations

import os
import sys

from cursor_sdk import Cursor, ModelParameterValue, ModelSelection

DEFAULT_MODEL_ID = "composer-2.5"


def model_id() -> str:
    return os.getenv("CURSOR_MODEL", DEFAULT_MODEL_ID)


def _parse_fast_param() -> bool | None:
    """None = SDK default (fast=true). Set CURSOR_MODEL_FAST=false for standard tier."""
    raw = os.getenv("CURSOR_MODEL_FAST", "").strip().lower()
    if not raw:
        return None
    if raw in ("1", "true", "yes", "fast"):
        return True
    if raw in ("0", "false", "no", "standard"):
        return False
    print(f"Invalid CURSOR_MODEL_FAST={raw!r}", file=sys.stderr)
    sys.exit(1)


def build_model_selection(api_key: str) -> str | ModelSelection:
    mid = model_id()
    fast = _parse_fast_param()
    if fast is None and mid != DEFAULT_MODEL_ID:
        return mid
    if fast is None:
        return mid  # composer-2.5 shorthand → server default (fast)

    if mid != DEFAULT_MODEL_ID:
        return ModelSelection(
            id=mid,
            params=[ModelParameterValue(id="fast", value="true" if fast else "false")],
        )

    try:
        composer = next(
            m for m in Cursor.models.list(api_key=api_key) if m.id == DEFAULT_MODEL_ID
        )
        for variant in composer.variants:
            param = next((p for p in variant.params if p.id == "fast"), None)
            if param is None:
                continue
            want = "true" if fast else "false"
            if param.value == want:
                return ModelSelection(id=DEFAULT_MODEL_ID, params=list(variant.params))
    except Exception:
        pass

    return ModelSelection(
        id=DEFAULT_MODEL_ID,
        params=[ModelParameterValue(id="fast", value="true" if fast else "false")],
    )


def format_model(model) -> str:
    if model is None:
        return "(none)"
    if isinstance(model, str):
        return model
    if isinstance(model, dict):
        mid = model.get("id", "?")
        params = model.get("params", [])
    else:
        mid = getattr(model, "id", "?")
        params = getattr(model, "params", ()) or ()
    parts = [f"id={mid!r}"]
    for p in params:
        if isinstance(p, dict):
            pid, val = p.get("id"), p.get("value")
        else:
            pid, val = getattr(p, "id", None), getattr(p, "value", None)
        parts.append(f"{pid}={val!r}")
    return " ".join(parts)
