# Soprano Studio

Web demo for five soprano pieces, with synchronized audio, a vertically
scrollable score, measure highlighting and range selection, and
measure-aware RAG plus local-LLM answers.

This repository contains only the web application. It consumes the pipeline
and dataset from sibling repositories instead of copying their code or data.

## Expected checkout layout

```text
workspace/
├── soprano-qa/                          RAG + LLM pipeline and local model
├── soprano-qa-demo/                     this repository
├── soprano-qa-dataset/                  score, audio, alignment, geometry
└── soprano-qa-dataset-database-collect/ RAG corpus source checkout
```

Defaults are resolved from the location of this repository, not from the
shell's current working directory:

- Pipeline: `../soprano-qa`
- Score/audio dataset: `../soprano-qa-dataset`
- Concatenated score assets:
  `../soprano-qa-dataset/sheet_music`
- Model and corpus settings: owned by `../soprano-qa`

## Run

Install the pipeline dependencies and download its model once:

```bash
cd ../soprano-qa
conda run -n soprano-qa python -m pip install -r requirements.txt \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
conda run -n soprano-qa python scripts/download_model.py
```

Then start the demo:

```bash
cd ../soprano-qa-demo
conda run -n soprano-qa python server.py --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>.

The server can be launched from another working directory because all default
paths are repository-relative:

```bash
conda run -n soprano-qa python /path/to/soprano-qa-demo/server.py \
  --host 127.0.0.1 --port 8765
```

The Qwen checkpoint is read from
`../soprano-qa/models/Qwen3-8B-Q4_K_M.gguf`. If the model or
`llama-cpp-python` is unavailable, corpus hits still use an extractive
fallback and the UI labels model-unavailable responses explicitly.

## Repository overrides

All overrides accept absolute paths. Relative demo-owned override values are
resolved against the demo repository; the RAG dataset and model overrides are
resolved against the pipeline repository. Neither depends on the launch
directory.

| Environment variable | Purpose | Default |
| --- | --- | --- |
| `SOPRANO_QA_PIPELINE_ROOT` | RAG + LLM repository | `../soprano-qa` |
| `SOPRANO_QA_DATASET_ROOT` | Score, audio, and alignment repository | `../soprano-qa-dataset` |
| `SOPRANO_QA_SCORE_ASSET_ROOT` | Concatenated images and geometry | `<dataset>/sheet_music` |
| `SOPRANO_QA_RAG_DATASET_ROOT` | Pipeline corpus source override | Pipeline setting |
| `SOPRANO_QA_MODEL_PATH` | Local GGUF checkpoint override | Pipeline setting |

The media dataset and RAG dataset variables are deliberately separate, so a
demo media override cannot silently replace the corpus source.

## Concatenated score assets

The dataset repository owns both the original pages and these additional
derived files:

```text
../soprano-qa-dataset/sheet_music/image/<piece>/Concatenated.png
../soprano-qa-dataset/sheet_music/result/<piece>-concatenated.json
```

Regenerate them without changing the original pages or geometry:

```bash
conda run -n music-sheet-vlm python \
  ../soprano-qa-dataset/sheet_music/build_concatenated_scores.py
```

The builder uses the annotated highest and lowest musical-content positions to
remove doubled page margins, preserves a representative inter-row gap, and
transforms every row and measure bounding box into concatenated-image
coordinates.

## QA behavior

The web adapter imports the public service facade from
`../soprano-qa/soprano_qa/service.py`.

- Corpus-backed answers retain exact evidence IDs, measure routing, and source
  and rights notices.
- The Korean paraphrase
  `피아노 반주에서 두 번째 박의 악센트는 무엇을 나타내는가?` retrieves
  the Die Forelle second-beat accent explanation for measures 2–5.
- If the corpus does not answer the core question, the local model receives a
  separate internal-knowledge prompt. The UI clearly identifies that answer
  as ungrounded and displays no corpus evidence for it.

## Validation

Run the demo integration and HTTP tests:

```bash
python3 -m unittest discover -v
```

Run the pipeline's own tests separately:

```bash
python3 -m unittest discover -v -s ../soprano-qa/tests
```

## Layout

```text
server.py           stdlib HTTP server and JSON/media routes
backend/dataset.py  sibling dataset access and capability computation
backend/qa.py       thin adapter to the sibling pipeline service
static/             HTML, JavaScript, and CSS frontend
tests/              cross-repository integration and HTTP contract tests
```
