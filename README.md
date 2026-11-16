# Soprano Lab

Soprano Lab is a local web demo for exploring five soprano works. It pairs
audio with a scrollable score, supports measure-range selection, and sends
questions to the measure-aware Soprano QA RAG pipeline. Its answer model is
the local Qwen3-14B unless the pipeline's `llm.provider` setting or the
`SOPRANO_QA_LLM_PROVIDER` environment variable selects another one.

This repository contains only the web application. It reads media and score
data from `soprano-qa-dataset` and imports the question-answering service from
`soprano-qa-rag-system` at runtime.

Answers show their cited evidence and source notices. When the corpus has no
matching evidence, the answer is labeled as a general music explanation.

Questions may be asked in English or Korean. `POST /api/ask` accepts an
optional `language` (`"ko"` or `"en"`); when it is omitted, the server infers
it from the question (Hangul present → `"ko"`, otherwise `"en"`). The value is
passed to the pipeline only when its `ask()` accepts a `language` parameter,
and the response reports `language`, `requested_language`, and
`language_supported`. For selected-range questions, pipelines that return
`score_context` have the selected bars' model-transcribed lyrics shown beside
the answer; web and lesson-video coaching evidence is labeled as not
expert-curated.

## Requirements

Keep these repositories as immediate siblings with their default names:

```text
soprano-qa-workspace/
├── soprano-qa-dataset/
├── soprano-qa-rag-system/
└── soprano-qa-demo/
```

Clone them if needed:

```bash
mkdir soprano-qa-workspace
cd soprano-qa-workspace
git clone https://github.com/minigb/soprano-qa-dataset.git
git clone https://github.com/minigb/soprano-qa-rag-system.git
git clone https://github.com/minigb/soprano-qa-demo.git
```

Create the shared Conda environment from the dataset repository, then install
the RAG dependencies:

```bash
cd soprano-qa-dataset
conda env create -f environment.yml
conda activate soprano-qa

cd ../soprano-qa-rag-system
python -m pip install -r requirements.txt
python scripts/build_corpus.py
```

If the environment already exists, replace `conda env create` with:

```bash
conda env update -n soprano-qa -f environment.yml
```

Generated answers require the optional local model:

```bash
python scripts/download_model.py
```

Without the model, the demo remains usable and returns corpus-backed
extractive answers. See the
[RAG system README](https://github.com/minigb/soprano-qa-rag-system) for
platform-specific model installation and pipeline usage.

## Run

From `soprano-qa-rag-system`, launch the server with the shared environment
explicitly so that local LLM support is available:

```bash
cd ../soprano-qa-demo
conda run --no-capture-output -n soprano-qa \
  python server.py --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765/> and keep the server terminal running.

Restart the server after changing demo or RAG Python code, settings, or model
configuration. Dataset evidence changes are detected and reloaded
automatically.

## Serving a web page's live API

A static page on another origin, such as the
[soprano-lab web demo](https://minigb.github.io/soprano-lab/), can ask this
server for answers. Its CORS origins, per-visitor and daily question limits,
proxy handling and startup warm-up are set with `SOPRANO_LAB_*` environment
variables, listed at the top of `server.py` and all off by default.
`GET /api/health` wakes a scaled-to-zero host. When the answer model fails,
for example a remote model's error or timeout, the server answers 503 with a
short retry message. soprano-lab deploys this server to Cloud Run with
`SOPRANO_QA_LLM_PROVIDER=vertex_gemini`.

With `SOPRANO_LAB_RECORDS=1` the server keeps records for the page's saved
questions and feedback, and `/api/health` reports `"records": true`. A question
sent with `"save": true` is printed to stdout with its answer as one JSON line
(`"event": "soprano_lab_question"`, with a `record_id` that the response also
carries). `POST /api/feedback` takes a rating (`"up"` or `"down"`) and an
optional comment on one answer, named by that `record_id` or, for a
precomputed answer, by its `answer_file`; it is printed the same way
(`"event": "soprano_lab_feedback"`) and limited per visitor. No client address
or other identifier is recorded. Off by default; the server then answers
`/api/feedback` with 404.

## Test

From `soprano-qa-demo`:

```bash
conda run --no-capture-output -n soprano-qa \
  python -m unittest discover -v
```

## Configuration

The sibling layout works without environment variables. Custom layouts can
override these paths:

| Variable | Purpose |
| --- | --- |
| `SOPRANO_QA_PIPELINE_ROOT` | RAG system repository |
| `SOPRANO_QA_DATASET_ROOT` | Dataset used for demo media and score assets |
| `SOPRANO_QA_RAG_DATASET_ROOT` | Dataset used to build the RAG corpus |
| `SOPRANO_QA_MODEL_PATH` | Local GGUF model checkpoint |

When overriding the dataset location, point both dataset variables at the same
checkout. Additional pipeline settings belong to `soprano-qa-rag-system`;
dataset and score tooling belong to
[soprano-qa-dataset](https://github.com/minigb/soprano-qa-dataset).
