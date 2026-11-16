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
    PROJECT_ROOT.parent / "soprano-qa-rag-system",
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
from soprano_qa.dense import DenseRetrievalUnavailable  # noqa: E402
from soprano_qa.llm import GenerationBackendUnavailable, GenerationFailed  # noqa: E402
from soprano_qa.retrieval import validate_question_measure_contract  # noqa: E402


SETTINGS = SERVICE.SETTINGS
ask = SERVICE.ask
corpus_stats = SERVICE.corpus_stats
model_status = SERVICE.model_status

# A missing retrieval or generation backend raises before corpus work.
BACKEND_UNAVAILABLE_ERRORS = (
    DenseRetrievalUnavailable,
    GenerationBackendUnavailable,
)

# A reachable answer model that returned no usable answer: a remote model's
# error, timeout or incomplete answer.
GENERATION_FAILED_ERRORS = (GenerationFailed,)

__all__ = [
    "BACKEND_UNAVAILABLE_ERRORS",
    "GENERATION_FAILED_ERRORS",
    "PIPELINE_ROOT",
    "SETTINGS",
    "SERVICE",
    "ask",
    "corpus_stats",
    "model_status",
    "validate_question_measure_contract",
]
