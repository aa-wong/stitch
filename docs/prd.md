# Stitch — PRD

> WeaveHacks 4 (Multi-Agent Orchestration) · June 6–7, 2026 · Draft v1
> Companion to the [PR/FAQ](./pr-faq.md)

## 1. Summary

Stitch is a local-first CLI that orchestrates a swarm of sandboxed agents to turn heterogeneous data sources (URLs, files, feeds) into **reusable, inspectable ETL pipelines** that materialize markdown signals reports. Agents extract, profile, strategize, and build; when blocked, they escalate structured questions to the user instead of guessing. Redis is the coordination spine; NVIDIA OpenShell provides per-agent isolation; W&B Weave traces every decision; extraction agents run through the OpenAI Agents SDK.

## 2. Goals & non-goals

### Goals (hackathon)
- G1: `stitch run` takes ≥3 heterogeneous sources to a cited markdown signals report with zero hand-written ETL code.
- G2: The run produces a saved, re-runnable pipeline definition (`stitch pipeline run <name>` regenerates the report measurably faster than the first run).
- G3: Every extractor agent executes inside an OpenShell sandbox with a per-source network allowlist (deny-by-default), demonstrable live.
- G4: Blocked agents escalate structured questions via CLI prompt (tier 1), macOS desktop notification (tier 2), and Slack (tier 3); answers unblock the swarm and are recorded into the pipeline.
- G5: Full swarm observability in W&B Weave — judges can open the project and follow a run end-to-end (hard judging requirement).
- G6: Redis used structurally: Streams (dispatch), blackboard (shared findings), pub/sub (HITL + lifecycle events), cache (extracted payloads) — eligible for the Redis sponsor prize.

### Non-goals (hackathon)
- Scheduling/cron, multi-user features, Windows support.
- Auth-walled sources beyond cookie passthrough.
- Output targets other than markdown (+ YAML pipeline companion).
- Durable storage beyond Redis and files on disk.
- Agent self-improvement / memory across projects.

## 3. Personas

| Persona | Pain | Stitch's answer |
|---|---|---|
| **Analyst / founder (primary)** | Needs cross-source signals today, can't wait on engineering | Cited markdown brief from a single CLI command |
| **Data engineer** | One-off extraction glue that rots when sources change | Reviewable, editable, re-runnable pipeline definition |
| **AI/agent developer** | Needs clean corpora for RAG/agent pipelines | Markdown-native structured output, ready for LLM consumption |

## 4. User stories

1. As a data engineer, I add sources and run Stitch; I review the proposed ETL plan before execution and edit it as plain markdown/YAML.
2. As an analyst, I run `stitch run --goal "..."` and get a signals report with per-claim citations back to sources.
3. As any user, when the swarm is blocked, I get a notification with a specific question; answering it (from terminal or Slack) resumes the run.
4. As any user, I re-run a saved pipeline and get a fresh report without the swarm re-deliberating strategy.
5. As a judge, I open the Weave project and follow any run: fan-out, per-agent spans, the human escalation, and the final materialization.

## 5. System architecture

```
┌─────────────────────────────── stitch CLI (orchestrator) ───────────────────────────────┐
│  init · add · run · questions · answer · pipeline {ls,show,run} · report                 │
└───────────────┬──────────────────────────────────────────────────────────────────────────┘
                │
        ┌───────▼────────┐         events / pings (pub/sub)        ┌──────────────────┐
        │     Redis      │◄────────────────────────────────────────│  Notifier        │
        │  Streams: tasks│                                          │  CLI prompt      │
        │  Hash: blackboard                                         │  osascript notif │
        │  Pub/Sub: HITL │                                          │  Slack webhook   │
        │  Cache: payloads                                          └──────────────────┘
        └───┬───────┬────┘
            │       │ dispatch (consumer groups)
   ┌────────▼──┐ ┌──▼────────┐ ┌───────────┐ ┌──────────┐
   │ Extractor │ │ Profiler  │ │ Strategist│ │ Builder  │   ← OpenAI Agents SDK-backed agents
   │ (×N, one  │ │ (schemas, │ │ (ETL plan │ │ (execute │
   │ per source│ │ entities, │ │ artifact) │ │ plan →   │
   │ OpenShell │ │ overlaps) │ │           │ │ report)  │
   │ sandbox)  │ └───────────┘ └───────────┘ └──────────┘
   └───────────┘
            └────────────── all agent spans traced in W&B Weave ──────────────┘
```

### Components

- **Orchestrator (CLI, single process):** parses commands, seeds Redis task streams, supervises agent lifecycle, owns the run state machine: `EXTRACT → PROFILE → PLAN → [HUMAN REVIEW] → BUILD → MATERIALIZE`.
- **Redis (coordination spine):**
  - `stream:tasks:{extract,profile,plan,build}` — dispatch with consumer groups; agent crash → message reclaimed.
  - `blackboard:{run_id}` — hash of cross-agent findings (schemas, entities, candidate join keys).
  - `pubsub:hitl` + `stream:questions` — escalations (stream = durable inbox, pub/sub = wake the notifier).
  - `cache:payload:{source_hash}` — extracted payloads with TTL; re-runs skip unchanged sources.
- **Extractor agents (×N):** during `EXTRACT`, the orchestrator launches one OpenAI Agents SDK extractor agent per source/document. Each extractor is a real model-backed subagent with narrow tools for reading exactly its assigned source and writing exactly its assigned outputs, not an in-process parser fallback. Each extractor emits a durable per-document markdown artifact under `.stitch/extracted/<run_id>/<source_id>.md` containing source metadata, a synthetic text description of the document, the normalized markdown payload, and citation segments. It also emits `.stitch/agent-payloads/<run_id>/<source_id>.json` so the orchestrator can validate the subagent output before profiling. The normalized markdown payload + provenance metadata are written to the coordination/data plane for downstream profiler, strategist, and builder agents. Every Agents SDK run is wrapped in Weave spans.
  - *Sandbox layering (defense in depth):* OpenShell (outer) owns the per-source domain allowlist, credential env-injection, and FS lock. The extractor agent is given only source-scoped tools, and those tools enforce exact artifact/payload paths under the workspace. Do **not** add a direct parser fallback; extraction must fail if the OpenAI agent cannot produce the required artifact and payload.
- **Profiler agents:** read payloads, write schema/entity/overlap findings to the blackboard.
- **Strategist agent:** reads the full blackboard, produces `pipeline.md` (human-readable plan: joins, canonicalization, dedupe, aggregations, open questions) + `pipeline.yaml` (machine-executable). Unresolvable ambiguities become HITL questions.
- **Builder agents:** execute the plan steps, materialize `report.md` with per-claim citations.
- **CocoIndex (data plane / corpus index):** a CocoIndex flow watches the extracted-payload workspace; chunks, embeds, and indexes markdown into LanceDB (embedded, file-based — keeps local-first, no extra server). Profiler/strategist agents query the corpus semantically via a search tool instead of loading full payloads into context; `stitch query "<question>"` exposes the same index to the user. CocoIndex's delta engine (memoization by input + code hash) powers incremental re-runs — only changed sources re-process/re-embed — and its byte-level lineage backs per-claim citations (F7). **Boundary:** Redis remains the control plane (dispatch/blackboard/HITL); CocoIndex owns the data plane. Stretch/roadmap: `pipeline.yaml` compiles to a CocoIndex flow — "CocoIndex executes a defined dataflow; Stitch's swarm authors it."
- **Notifier:** subscribes to `pubsub:hitl`; fans out to CLI prompt (always), macOS notification via `osascript` (tier 2), Slack incoming webhook (tier 3). Answers flow back via `stitch answer <id>` or Slack thread reply (stretch).
- **Weave:** `weave.init()` at orchestrator start; every agent invocation, tool call, and HITL exchange is a span. Run ID links terminal output to the trace URL.

### HITL question lifecycle

```
agent blocked → XADD stream:questions {id, agent, run, question, options, context}
             → PUBLISH pubsub:hitl → notifier fans out (CLI/desktop/Slack)
user answers → stitch answer <id> "<text>" → XADD stream:answers → agent resumes
             → answer recorded into pipeline.yaml (never asked twice)
```

Pending questions survive restarts (stream, not pub/sub). `stitch questions` lists the inbox.

## 6. CLI surface (v1)

```bash
stitch init                          # scaffold .stitch/, check Redis/OpenShell/Weave creds
stitch add <url|path|feed> [--label] # register a source
stitch run --goal "<intent>"         # full swarm run; --review pauses at plan for approval
stitch questions                     # pending HITL questions
stitch answer <id> "<answer>"        # unblock an agent
stitch pipeline ls|show|run <name>   # manage / re-run saved pipelines
stitch report [--open]               # show latest report
stitch query "<question>"            # semantic search over the indexed corpus (CocoIndex/LanceDB)
stitch run --remote                  # STRETCH: extractors in CoreWeave Sandboxes
```

## 7. Artifacts on disk

```
.stitch/
  config.yaml               # redis url, weave project, slack webhook, channels
  sources.yaml              # registered sources + labels
  extracted/<run>/<source>.md # per-document extractor artifact: synthetic description + normalized payload + citations
pipelines/<name>/
  pipeline.md               # human-readable strategy (the reviewable artifact)
  pipeline.yaml             # machine-executable steps + recorded HITL answers
  runs/<ts>/report.md       # materialized signals report, per-claim citations
```

## 8. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| F1 | Register URL, local file (md/csv/txt) | P0 |
| F2 | Orchestrator launches one OpenAI Agents SDK extractor subagent per source/document in parallel; extraction fails if OpenAI auth is missing | P0 |
| F3 | Profiling findings written to shared blackboard | P0 |
| F4 | Strategist emits pipeline.md + pipeline.yaml before build | P0 |
| F5 | HITL: structured question → CLI prompt → answer → resume | P0 |
| F6 | Weave tracing across all agents, linkable per run | P0 |
| F7 | Markdown report with per-claim source citations | P0 |
| F7a | Each extractor writes a per-document markdown artifact containing source metadata, synthetic text description, normalized payload, and citation segments | P0 |
| F7b | Each Agents SDK extractor writes a validated JSON payload companion consumed by profiler/strategist/builder stages | P0 |
| F8 | Saved pipeline re-run, skipping deliberation + unchanged-source extraction (cache) | P1 |
| F9 | Desktop notification tier for HITL | P1 |
| F10 | Slack webhook tier for HITL | P1 |
| F11 | `--review` gate: human approves plan before build | P1 |
| F12 | `--remote`: CoreWeave Sandboxes execution backend | P2 (stretch) |
| F13 | Slack thread-reply answers | P2 (stretch) |
| F14 | CocoIndex corpus indexing (chunk/embed → LanceDB); agents query via search tool; `stitch query` | P1 |
| F15 | Incremental re-runs via CocoIndex delta engine (only changed sources reprocess) | P1 (upgrades F8) |
| F16 | `pipeline.yaml` compiles to a CocoIndex flow | P2 (stretch) |

## 9. Judging alignment

| Judging lever | How Stitch hits it |
|---|---|
| Theme: multi-agent orchestration | Swarm fan-out, role specialization, blackboard coordination, supervised state machine |
| Weave required to win | Structural tracing; demo opens the Weave UI; HITL exchanges visible as spans |
| Redis sponsor prize | Streams + consumer groups, blackboard, pub/sub, TTL cache — four distinct structural uses |
| Sponsor goodwill (W&B/CoreWeave) | `--remote` CoreWeave Sandboxes stretch; per-sandbox Weave correlation |
| Demo impact | Live swarm fan-out → walk-away desktop ping → fast pipeline re-run |

## 10. Milestones (2-day plan)

| Slot | Deliverable | Cut line |
|---|---|---|
| Day 1 AM | CLI skeleton, Redis up, **Weave wired first** (hard requirement), single extractor in OpenShell. **Timebox OpenShell to 2h → Docker fallback.** | — |
| Day 1 PM | Parallel fan-out via Streams, profiler agents, blackboard | — |
| Day 1 eve | Strategist → pipeline.md/yaml; HITL tier 1 (CLI prompt) end-to-end | F11 review gate |
| Day 2 AM | Builder → report.md with citations; pipeline save/re-run + cache; desktop notifications | F8 cache skip |
| Day 2 midday | Slack tier, demo rehearsal with curated source set | F12/F13 stretch |
| Day 2 PM | Demo polish, Weave project cleanup for judges, submission | — |

**Demo source set (curate + rehearse in advance):** 2–3 competitor pricing pages + 1 local CSV → "weekly competitive pricing signals" brief. Seed one deliberate ambiguity (currency mismatch) to guarantee the HITL beat fires on stage.

## 11. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| OpenShell setup eats day 1 | Med | 2h timebox → plain Docker fallback; isolation story degrades but survives |
| Strategist plan quality on messy sources | High | Curated demo sources; constrain plan schema; `--review` gate makes imperfection a feature ("you approve the plan") |
| HITL race conditions / lost answers | Med | Streams (durable) not bare pub/sub; idempotent answer handling |
| OpenAI model rate limits mid-demo | Low | Payload cache + saved pipeline = fast re-run path as demo plan B |
| 3 notification channels = integration sprawl | Med | Strict tiering: CLI guaranteed, desktop P1, Slack P1-if-time |
| CocoIndex dependency cost (embedding model download, new framework) on a 2-day build | Med | P1 not P0 — the Redis payload cache (F8) remains the fallback re-run path; if CocoIndex slips, demo still works without `stitch query` |
| Live scraping flakes on stage | Med | Cache pre-warmed payloads; demo can run fully offline from cache |

## 12. Success metrics (hackathon-scoped)

- End-to-end run on 4+ sources completes in < 5 min with ≥1 HITL exchange.
- Pipeline re-run ≥ 3× faster than first run (cache + skipped deliberation).
- 100% of agent invocations visible as Weave spans.
- Zero extractor egress outside its allowlist (show the policy denial in demo).
- Judges can navigate the Weave project unassisted.

## 13. Open questions

1. Plan schema for `pipeline.yaml` — fixed step vocabulary (fetch/normalize/join/dedupe/aggregate/render) vs. free-form agent-authored steps? *Lean fixed: easier to execute reliably and to diff.*
2. Does the strategist run once per pipeline or re-validate on each re-run (source drift detection)? *V1: once; drift detection is roadmap.*
3. Slack answers via webhook are one-way — is `stitch answer` from terminal acceptable for the demo, with thread-reply as stretch? *Assume yes.*
4. ~~Can the orchestrator supervise one extractor per document?~~ **Resolved:** yes — Stitch owns fan-out and launches one OpenAI Agents SDK-backed extractor per source. Each extractor receives source-scoped tools and must write its own markdown artifact plus JSON payload companion. Supervision lives in the orchestrator + Redis (consumer groups reclaim dead workers' tasks). Stretch (with B-tier demo value): an outer sandbox gateway can place each extractor in a stronger per-source runtime with the same tool contract.

---

*Related: [PR/FAQ](./pr-faq.md)*
