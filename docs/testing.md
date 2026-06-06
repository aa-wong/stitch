# Testing Stitch End-to-End

## 1. Local Environment

```bash
uv sync --extra dev
cp .env.example .env
```

Edit `.env` with real credentials:

```bash
OPENAI_API_KEY=sk-...
STITCH_OPENAI_MODEL=gpt-5.4
STITCH_REDIS_URL=redis://localhost:6379/0
STITCH_WEAVE_DISABLED=false
STITCH_WEAVE_PROJECT=<your-weave-project>
WANDB_API_KEY=<your-wandb-key>
```

Stitch loads `.env` automatically. Existing shell environment variables take precedence over `.env` values.

## 2. Required Services

- **OpenAI/Codex SDK auth:** `OPENAI_API_KEY` must be set. Extraction uses one Codex SDK thread per source/document.
- **Redis:** run Redis locally and set `STITCH_REDIS_URL=redis://localhost:6379/0`.
- **W&B Weave:** run `wandb login` or set `WANDB_API_KEY`, set `STITCH_WEAVE_PROJECT`, and keep `STITCH_WEAVE_DISABLED=false`.
- **uv environment:** use `uv sync --extra dev`; do not use pip for this project.

## 3. Unit Tests

Unit tests fake the Codex SDK client where needed, so they do not spend API calls:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider
```

## 4. Whole-System Codex Agent Test

Create a clean demo workspace outside the repo:

```bash
STITCH_BIN="$PWD/.venv/bin/stitch"
mkdir -p /private/tmp/stitch-demo
cd /private/tmp/stitch-demo
```

Create heterogeneous local sources:

```bash
cat > prices.csv <<'CSV'
company,plan,price
Alpha,Starter,$10 USD
Beta,Starter,8 EUR
CSV

cat > notes.txt <<'TXT'
Alpha launched a starter tier for $10 USD per user.
Beta lists a comparable starter tier at 8 EUR per user.
TXT
```

Run Stitch:

```bash
"$STITCH_BIN" init
"$STITCH_BIN" add prices.csv --label "Pricing CSV"
"$STITCH_BIN" add notes.txt --label "Market Notes"
"$STITCH_BIN" run --goal "weekly competitive pricing signals"
```

Expected behavior:

- The main orchestrator starts one Codex SDK extractor thread per source.
- Each extractor writes `.stitch/extracted/<run_id>/<source_id>.md`.
- Each extractor writes `.stitch/agent-payloads/<run_id>/<source_id>.json`.
- The strategist should ask a CLI HITL question because the sources intentionally mix USD and EUR. Answer with the canonical currency, for example `USD`.
- The run produces `pipeline.md`, `pipeline.yaml`, and `report.md`.

## 5. Verify Artifacts

```bash
"$STITCH_BIN" pipeline ls
"$STITCH_BIN" report --path
find .stitch/extracted -name '*.md' -print
sed -n '1,80p' .stitch/extracted/*/*.md
find .stitch/agent-payloads -name '*.json' -print
```

Expected outputs:

- `pipelines/<name>/pipeline.md`
- `pipelines/<name>/pipeline.yaml`
- `pipelines/<name>/runs/<run_id>/report.md`
- `.stitch/extracted/<run_id>/<source_id>.md`
- `.stitch/agent-payloads/<run_id>/<source_id>.json`

Each extracted document artifact must include:

- `## Synthetic Description`
- `## Normalized Markdown Payload`
- `## Citation Segments`

## 6. Verify Weave

Open the configured Weave project and find the run by `run_id`. Expected spans include:

- `run`
- `extract_fanout`
- one outer `extract_source` span per source
- one `codex_extractor_agent` span per source
- one `codex_thread_start` span per source
- one `codex_thread_run` span per source
- `profile`
- `plan`
- `hitl_questions` when the currency ambiguity is triggered
- `plan_after_hitl`
- `build_report`

## 7. Expected Failure Cases

- If `OPENAI_API_KEY` is missing, `stitch run` must fail before extraction.
- If a Codex extractor does not write its markdown artifact or payload JSON, `stitch run` must fail.
- If Redis or Weave are misconfigured, fix the service setup before considering the proper end-to-end test valid.
