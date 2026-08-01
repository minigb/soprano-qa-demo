# Soprano Studio

Soprano Studio is a local web demo for exploring five soprano works. It pairs
audio with a scrollable score, supports measure-range selection, and sends
questions to the measure-aware Soprano QA RAG and local-LLM pipeline.

This repository contains only the web application. It reads media and score
data from `soprano-qa-dataset` and imports the question-answering service from
`soprano-qa-rag-system` at runtime.

Answers show their cited evidence and source notices. When the corpus has no
answer, any model-only response is labeled as internal knowledge.

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
