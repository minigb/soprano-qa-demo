"""Thin adapter to the sibling Soprano QA pipeline repository."""
from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _external_root(env_name: str, default: Path) -> Path:
    configured = Path(os.environ.get(env_name, str(default))).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


PIPELINE_ROOT = _external_root(
    "SOPRANO_QA_PIPELINE_ROOT",
    PROJECT_ROOT.parent / "soprano-qa",
)
if not (PIPELINE_ROOT / "soprano_qa" / "__init__.py").is_file():
    raise RuntimeError(
        "Soprano QA pipeline repository not found at "
        f"{PIPELINE_ROOT}. Clone it beside this demo or set "
        "SOPRANO_QA_PIPELINE_ROOT."
    )

pipeline_path = str(PIPELINE_ROOT)
if pipeline_path not in sys.path:
    sys.path.insert(0, pipeline_path)

from soprano_qa import service as SERVICE  # noqa: E402


SETTINGS = SERVICE.SETTINGS
ask = SERVICE.ask
corpus_stats = SERVICE.corpus_stats
model_status = SERVICE.model_status

__all__ = [
    "PIPELINE_ROOT",
    "SETTINGS",
    "SERVICE",
    "ask",
    "corpus_stats",
    "model_status",
]
