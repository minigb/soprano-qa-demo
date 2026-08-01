"""Read-only access to the demo's score assets and source dataset checkout.

Everything here is derived from the on-disk JSON at request time (small
in-process caches only), so alignment changes and rebuilt score geometry
become usable without a code change or server restart beyond the cache TTL
below.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _external_root(env_name: str, default: Path) -> Path:
    configured = Path(os.environ.get(env_name, str(default))).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


DATASET_ROOT = _external_root(
    "SOPRANO_QA_DATASET_ROOT",
    PROJECT_ROOT.parent / "soprano-qa-dataset",
)
SCORE_ASSET_ROOT = _external_root(
    "SOPRANO_QA_SCORE_ASSET_ROOT",
    DATASET_ROOT / "sheet_music",
)

# Cache entries expire quickly so in-progress annotation work (new alignment
# or geometry files landing on disk) shows up without restarting the server.
_CACHE_TTL_SECONDS = 5.0

PIECE_IDS = [
    "die-forelle",
    "una-voce-poco-fa",
    "la-capinera",
    "in-flowery-clouds",
    "nella-fantasia",
]

# Escape hatch for a piece whose alignment flags its score-to-audio mapping
# as "unavailable" (edition mismatch) but should be treated as identity
# anyway. Empty by default -- die-forelle needed this while its scanned
# edition didn't match the aligned recording, but its alignment has since
# been re-annotated against the matching edition and now reports "identity"
# mapping on its own, so no override is needed for it any more.
FORCE_SYNC_PIECE_IDS = frozenset(
    p.strip()
    for p in os.environ.get("SOPRANO_QA_FORCE_SYNC", "").split(",")
    if p.strip()
)


def alignment_path(piece_id: str) -> Path:
    return DATASET_ROOT / "alignment" / "result" / f"{piece_id}.json"


def geometry_path(piece_id: str) -> Path:
    return SCORE_ASSET_ROOT / "result" / f"{piece_id}-concatenated.json"


def audio_path(piece_id: str) -> Path:
    return DATASET_ROOT / "audio" / f"{piece_id}.mp3"


def image_path(piece_id: str, page_id: str) -> Path:
    return SCORE_ASSET_ROOT / "image" / piece_id / f"{page_id}.png"


_json_cache: dict[Path, tuple[float, Optional[dict]]] = {}


def _load_json(path: Path) -> Optional[dict]:
    cached = _json_cache.get(path)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    data = None
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    _json_cache[path] = (now, data)
    return data


def load_alignment(piece_id: str) -> Optional[dict]:
    return _load_json(alignment_path(piece_id))


def load_geometry(piece_id: str) -> Optional[dict]:
    return _load_json(geometry_path(piece_id))


def geometry_measure_numbers(geometry: dict) -> set[int]:
    nums: set[int] = set()
    for page in geometry.get("pages", []):
        for row in page.get("rows", []):
            for m in row.get("measures", []):
                nums.add(m["measure_number"])
    return nums


def piece_status(piece_id: str) -> dict:
    """Data-driven capability flags for one piece.

    ``sync_available`` gates audio<->score highlighting/seeking, which needs
    a *complete* timestamp annotation whose measure numbers are fully
    represented in the score geometry (the mapping is explicitly flagged
    ``unavailable`` when the scanned edition doesn't match the performed
    audio's measure/repeat structure, e.g. die-forelle).

    ``measures_known`` gates measure-range selection for RAG questions: the
    score geometry alone is enough to know valid measure numbers, even if
    audio timing isn't ready yet, since expert annotations reference printed
    measure numbers rather than audio timestamps.
    """
    alignment = load_alignment(piece_id)
    geometry = load_geometry(piece_id)

    title = piece_id.replace("-", " ").title()
    composer = None
    duration_seconds = None
    sync_available = False
    reason = None

    if alignment is None:
        reason = "Audio-score alignment has not been created for this piece yet."
    else:
        title = alignment["piece"]["title"]
        composer = alignment["piece"].get("composer")
        duration_seconds = alignment["assets"]["audio"]["duration_seconds"]
        complete = bool(alignment.get("complete"))
        mapping = (
            alignment.get("assets", {})
            .get("sheet_music", {})
            .get("measure_mapping", {"mode": "identity"})
        )
        mapping_mode = mapping.get("mode", "identity")

        if not complete:
            reason = (
                "Audio-score measure timestamps have not been fully annotated "
                "for this piece yet."
            )
        elif mapping_mode == "unavailable" and piece_id not in FORCE_SYNC_PIECE_IDS:
            reason = mapping.get("reason") or (
                "Score-image measure numbering does not match the performed "
                "audio yet."
            )
        elif geometry is None:
            reason = "Sheet-music measure geometry has not been captured for this piece yet."
        else:
            aligned_numbers = {m["measure_number"] for m in alignment.get("measures", [])}
            geo_numbers = geometry_measure_numbers(geometry)
            missing = aligned_numbers - geo_numbers
            if missing:
                reason = (
                    f"{len(missing)} performed measure(s) are not yet mapped "
                    "to score geometry."
                )
            else:
                sync_available = True

    max_measure_number = None
    page_count = 0
    if geometry is not None:
        page_count = geometry.get("page_count", len(geometry.get("pages", [])))
        geo_numbers = geometry_measure_numbers(geometry)
        if geo_numbers:
            max_measure_number = max(geo_numbers)

    return {
        "id": piece_id,
        "title": title,
        "composer": composer,
        "duration_seconds": duration_seconds,
        "page_count": page_count,
        "max_measure_number": max_measure_number,
        "sync_available": sync_available,
        "measures_known": geometry is not None,
        "pending_reason": reason,
    }


def list_pieces() -> list[dict]:
    return [piece_status(pid) for pid in PIECE_IDS]


def alignment_payload(piece_id: str) -> dict:
    alignment = load_alignment(piece_id)
    status = piece_status(piece_id)
    if alignment is None:
        return {"sync_available": False, "duration_seconds": None, "measures": []}
    measures = [
        {
            "measure_number": m["measure_number"],
            "occurrence_index": m["occurrence_index"],
            "repeat_pass": m.get("repeat_pass"),
            "start_seconds": m.get("start_seconds"),
            "end_seconds": m.get("end_seconds"),
        }
        for m in alignment.get("measures", [])
        if m.get("start_seconds") is not None and m.get("end_seconds") is not None
    ]
    return {
        "sync_available": status["sync_available"],
        "duration_seconds": alignment["assets"]["audio"]["duration_seconds"],
        "measures": measures,
    }


def score_payload(piece_id: str) -> Optional[dict]:
    geometry = load_geometry(piece_id)
    if geometry is None:
        return None
    pages = []
    for page in geometry.get("pages", []):
        rows = []
        for row in page.get("rows", []):
            measures = [
                {
                    "measure_number": m["measure_number"],
                    "bbox_normalized": m["bbox_normalized"],
                }
                for m in row.get("measures", [])
            ]
            rows.append({"measures": measures})
        pages.append(
            {
                "page_id": page["page_id"],
                "image_url": f"/media/score/{piece_id}/{page['page_id']}.png",
                "image_width": page["image_width"],
                "image_height": page["image_height"],
                "rows": rows,
            }
        )
    return {"page_count": len(pages), "pages": pages}
