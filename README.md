# Soprano Studio

Web demo with synchronized audio, a vertically scrollable score, measure-range
selection, and answers from the sibling Soprano QA RAG system.

## Setup

### 1. Clone the three repositories

```bash
mkdir soprano-qa-workspace
cd soprano-qa-workspace
git clone https://github.com/minigb/soprano-qa-dataset.git
git clone https://github.com/minigb/soprano-qa-rag-system.git
git clone https://github.com/minigb/soprano-qa-demo.git
```

Keep them as immediate siblings:

```text
soprano-qa-workspace/
├── soprano-qa-dataset/
├── soprano-qa-rag-system/
└── soprano-qa-demo/
```

### 2. Create the one shared environment

Install Conda (Miniconda or Miniforge) and initialize it for your shell first.
Run these commands from `soprano-qa-workspace/`:

```bash
cd soprano-qa-dataset
conda env create -f environment.yml
conda activate soprano-qa

cd ../soprano-qa-rag-system
python -m pip install -r requirements.txt
```

If the environment already exists, run this alternative block from
`soprano-qa-workspace/`:

```bash
cd soprano-qa-dataset
conda env update -n soprano-qa -f environment.yml
conda activate soprano-qa
cd ../soprano-qa-rag-system
python -m pip install -r requirements.txt
```

For NVIDIA CUDA 12.4, replace the `pip install` command in the chosen block
with:

```bash
python -m pip install -r requirements.txt \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```

### 3. Prepare the corpus and optionally the model

From `soprano-qa-rag-system`:

```bash
python scripts/build_corpus.py
python scripts/download_model.py  # Optional; needed for generated answers
```

The explicit corpus build is useful for validating the data before starting
the demo, but it is not required after every annotation or database edit. The
RAG service fingerprints the active
`soprano-qa-dataset/expert_curation/review/*.json` files, the
`soprano-qa-dataset/database/records/*.jsonl` files, and the configured
`soprano-qa-dataset/database/exports/*.jsonl` files. When those inputs change,
the next corpus check rebuilds the local RAG snapshot and reloads it
automatically. There is no annotation or database copy inside the demo
repository.

All expert knowledge units remain retrievable so that uncertain but valuable
human evidence is not discarded. The pipeline attaches a review warning to a
`needs_review` unit, allowing the answer generator and evaluator to treat it
conservatively. All current measure statuses have been reviewed as `specific`,
`whole_piece`, or `unspecified`; original source annotations provide provenance
and fidelity context rather than acting as separate retrieval records.

The model download needs approximately 5 GB. Without it, corpus-backed
extractive answers remain available but local generation is unavailable.

### 4. Run the demo

```bash
cd ../soprano-qa-demo
python server.py --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>.

## Verify

From `soprano-qa-workspace/`:

```bash
cd soprano-qa-demo
conda run -n soprano-qa python -m unittest discover -v

cd ../soprano-qa-rag-system
conda run -n soprano-qa python -m unittest discover -v
```

## Configuration

Paths are resolved from the repositories, not from the launch directory.

| Environment variable | Default |
| --- | --- |
| `SOPRANO_QA_PIPELINE_ROOT` | `../soprano-qa-rag-system` |
| `SOPRANO_QA_DATASET_ROOT` | `../soprano-qa-dataset` |
| `SOPRANO_QA_SCORE_ASSET_ROOT` | `<dataset>/sheet_music` |
| `SOPRANO_QA_RAG_DATASET_ROOT` | `../soprano-qa-dataset` |
| `SOPRANO_QA_MODEL_PATH` | Pipeline setting |

## Project notes

The demo repository contains only the HTTP server, pipeline/dataset adapters,
frontend, and integration tests. Score, audio, geometry, corpus, retrieval, and
model files remain in their owning sibling repositories.

Concatenated score assets are already committed to the dataset. To regenerate
them in the shared environment, run this from `soprano-qa-workspace/`:

```bash
cd soprano-qa-dataset
python sheet_music/build_concatenated_scores.py
```

Corpus-backed responses retain evidence and rights metadata. Questions outside
the corpus can use the model's internal knowledge and are labeled accordingly.
