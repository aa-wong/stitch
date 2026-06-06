# Stitch — PR/FAQ

> Working-backwards document · WeaveHacks 4 (Multi-Agent Orchestration) · June 6–7, 2026 · Draft v1

---

## Press Release

**FOR IMMEDIATE RELEASE — San Francisco, CA, June 7, 2026**

### Stitch turns scattered data sources into reusable ETL pipelines — built by a sandboxed agent swarm that asks *you* when it's unsure

**A local-first CLI that wrangles a swarm of agents to extract, profile, and join messy data sources into repeatable, inspectable pipelines — no ETL code written by hand, no silent hallucinated joins.**

Today, every team that needs insight from scattered sources — competitor pages, RSS feeds, CSVs, internal docs — faces the same choice: a data engineer spends days writing one-off ETL glue, or an analyst copies things into a spreadsheet and hopes. The glue code rots the moment a source changes shape. The spreadsheet was wrong the moment it was made.

Stitch replaces hand-written ETL with a **negotiated pipeline**. You point the CLI at your sources. Stitch spins up a swarm of sandboxed extractor agents — one per source, each locked inside an NVIDIA OpenShell sandbox that can *only* reach the source it was assigned. Profiler agents find the schemas, entities, and overlaps in what came back. A strategist agent proposes an ETL plan — how to join, dedupe, normalize, and aggregate — as a **human-readable artifact you review before anything runs**. Then builder agents execute it, materializing a markdown signals report with citations back to every source.

The pipeline itself is the product. It's saved, versioned, and re-runnable: `stitch pipeline run market-intel` regenerates the report next week against fresh data, no agents re-deliberating from scratch.

And when the swarm hits something it can't resolve — an auth wall, an ambiguous schema, two sources disagreeing about whether revenue is in EUR or USD — it doesn't guess and it doesn't die. It **pings you**: a question lands in your terminal, your desktop notifications, or your Slack. You answer; the swarm continues. Walk away from your machine; the swarm calls you back.

Every agent decision is traced end-to-end in W&B Weave, and the entire swarm is coordinated through Redis — Streams for task dispatch, a shared blackboard for cross-agent findings, pub/sub for the moment an agent needs a human.

"We stopped thinking of ETL as code someone writes and started thinking of it as an agreement you reach with a swarm," said Aaron Wong, creator of Stitch. "The swarm does the tedious 95%. You answer the three questions that actually needed a human."

Stitch runs entirely on your machine. Your data never leaves it unless you say so.

**Getting started:**

```bash
brew install stitch
stitch init
stitch add https://competitor-a.com/pricing
stitch add https://competitor-b.com/pricing
stitch add ./internal-sales.csv
stitch run --goal "weekly competitive pricing signals"
```

---

## External FAQ

**Q: Who is Stitch for?**
Three users, one tool:
1. **Data engineers** who are tired of writing one-off extraction glue — Stitch gives them an inspectable, re-runnable pipeline definition instead of another brittle script.
2. **Analysts and founders** who need signals from scattered sources *today* — Stitch gives them a cited markdown brief without waiting on engineering.
3. **AI/agent developers** who need clean, structured corpora for downstream LLM work — Stitch's markdown-native output drops straight into a RAG or agent pipeline.

**Q: How is this different from a scraper + a script?**
A scraper gets you raw data; the hard part is the *strategy* — what joins to what, what's canonical, what's noise. Stitch's swarm discovers that strategy, writes it down as a reviewable plan, and turns it into a pipeline you can re-run. The strategy is the artifact, not the scrape.

**Q: What does "sandboxed" actually mean here?**
Each extractor agent runs inside an NVIDIA OpenShell sandbox with a deny-by-default policy: network access is allowlisted to *only* the source that agent was assigned, the filesystem is scoped, and API keys never touch disk. An agent assigned `competitor-a.com` physically cannot phone anywhere else — and you can watch the policy engine block it if it tries.

**Q: What happens when an agent gets stuck?**
It escalates instead of guessing. Blocked agents publish a structured question ("Source B reports revenue in EUR, Source C in USD — which is canonical?") to a Redis stream. You get it in your terminal, as a desktop notification, or in Slack. Your answer is recorded into the pipeline definition, so the same question is never asked twice.

**Q: Does my data leave my machine?**
The orchestrator, Redis, sandboxes, and all extracted data are local. Model calls go to the LLM provider (the agents run on the OpenAI Codex harness), and traces go to W&B Weave. An optional `--remote` mode runs extractors in CoreWeave Sandboxes instead of locally — but that's your call, per run.

**Q: What output formats are supported?**
Markdown in v1 — both the signals report and the pipeline definition are human-readable markdown (with a machine-readable YAML companion). Parquet/SQL/warehouse targets are on the roadmap; markdown-first means humans and LLMs can both read everything Stitch produces.

**Q: Can I edit a pipeline the swarm created?**
Yes — pipelines are plain markdown + YAML in your repo. Edit the plan, and the swarm executes your edited version. Stitch is a collaborator, not a black box.

---

## Internal FAQ

**Q: Why does this fit WeaveHacks 4?**
The theme is multi-agent orchestration — "orchestrating pipelines and wrangling swarms" is *literally the product description*. Weave usage (required to win) is structural, not bolted on: every extractor/profiler/strategist/builder span is traced, and the demo opens the Weave UI to show the swarm deliberating. Redis (sponsor prize) is the coordination spine, not a cache afterthought: Streams for dispatch, hash-based blackboard for shared findings, pub/sub for HITL escalation, plus payload caching with TTL.

**Q: Why local-first?**
Differentiation and trust. Every agent-data product in this space is a SaaS that wants your data uploaded. "Your data never leaves your machine, and the agents are physically caged" is a one-sentence trust story. It also makes the demo robust — no deploy, no cloud flakiness on stage.

**Q: Why markdown as the v1 data format?**
Three reasons: (1) demo legibility — judges can *read* the output; (2) it's the lingua franca of LLM pipelines, so persona #3 gets value immediately; (3) it forces the swarm to produce explanations, not just tables. It's a positioning choice, not a limitation: "insight reports humans actually read."

**Q: Why OpenShell over plain Docker or CoreWeave Sandboxes?**
OpenShell natively wraps the Codex harness, is local, and gives policy-enforced deny-by-default isolation out of the box — "sandboxed" becomes demoable instead of adjective-ware. CoreWeave Sandboxes is the judge-pleasing stretch (`--remote` flag): same orchestrator, swap the execution backend, and Weave traces auto-correlate per-sandbox. Plain Docker is the documented fallback if OpenShell costs more than ~2 hours on day 1.

**Q: What's the wow moment in the demo?**
Three beats:
1. **The swarm fan-out** — `stitch run`, then the Weave trace view showing parallel extractors, each in its own sandbox.
2. **The ping** — presenter walks away mid-run; a desktop notification fires: the swarm has a question. Presenter answers from Slack; the swarm resumes.
3. **The re-run** — `stitch pipeline run` regenerates the report in a fraction of the time, proving the pipeline (not the report) is the artifact.

**Q: What are the riskiest assumptions?**
1. OpenShell setup cost is unknown — timebox to 2 hours, fall back to Docker.
2. The strategist agent producing a *good* plan from heterogeneous sources is the hardest LLM problem in the build — mitigate with a curated demo source set rehearsed in advance.
3. HITL across three channels is integration surface — CLI prompt is the guaranteed tier; desktop and Slack are progressive enhancements.

**Q: What is explicitly out of scope for the hackathon?**
Scheduling/cron, auth-walled sources beyond cookies, non-markdown output targets, multi-user/team features, Windows support, and any persistence beyond Redis + files on disk.

**Q: Alternate names considered?**
Stitch (chosen — stitching sources together; thematic cousin of Weave), Loom, Mosaic, Trellis, Swarmline, Confluence (taken). "Stitch" wins on verb-ability: *"just stitch it."*

---

*Related: [PRD](./prd.md)*
