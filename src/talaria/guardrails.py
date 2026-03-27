"""Lean operational guardrails for Talaria self-hosting."""

from __future__ import annotations

import os
from pathlib import Path


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def enforce_runner_target_separation(
    runner_dir: Path,
    target_paths: list[Path],
    bypass_env: str = "TALARIA_BYPASS_ALLOWED",
) -> None:
    """Fail fast if runner and any target repo path are the same.

    This protects dual-clone dogfooding setups where a stable orchestrator checkout
    must not mutate itself. Use TALARIA_BYPASS_ALLOWED=true for explicit emergency
    bypasses only.
    """
    if _is_truthy(os.getenv(bypass_env)):
        print(
            "[guardrail] WARNING: TALARIA_BYPASS_ALLOWED=true — "
            "runner/target separation guard bypassed."
        )
        return

    runner = runner_dir.expanduser().resolve()
    normalized_targets = [p.expanduser().resolve() for p in target_paths if str(p).strip()]

    for target in normalized_targets:
        if runner == target:
            raise RuntimeError(
                "Talaria self-hosting guardrail violation: runner directory and target repo path are identical "
                f"({runner}). Run orchestrator from stable clone and target a different dev clone, "
                "or set TALARIA_BYPASS_ALLOWED=true for emergency-only bypass."
            )
