from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agents import CodexFactory, ExtractorAgent
from .bus import create_bus
from .config import init_project, load_config
from .hitl import HitlManager
from .json_yaml import write_document
from .models import ExtractedPayload, Source
from .paths import StitchPaths
from .pipeline_store import load_pipeline
from .planning import PipelinePlan, _slug, create_pipeline_plan, write_pipeline
from .profiling import profile_payloads
from .reporting import render_report, write_report
from .sources import list_sources
from .tracing import StitchTracer, init_tracer


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    pipeline_name: str
    status: str
    pipeline_dir: Path
    report_path: Path | None
    questions: list[str]
    trace_url: str | None


def run(
    *,
    goal: str,
    paths: StitchPaths | None = None,
    name: str | None = None,
    prompt: bool = True,
    review: bool = False,
    codex_factory: CodexFactory | None = None,
    input_fn=input,
    output_fn=print,
) -> RunSummary:
    paths = paths or StitchPaths.discover()
    init_project(paths)
    config = load_config(paths)
    bus = create_bus(paths, config.redis_url)
    hitl = HitlManager(bus, input_fn=input_fn, output_fn=output_fn)
    run_id = _new_run_id()
    tracer = init_tracer(project=config.weave_project, run_id=run_id, agent="orchestrator")
    sources = list_sources(paths)
    if not sources:
        raise RuntimeError("No sources registered. Add sources with `stitch add <url|path|feed>` first.")

    pipeline_name = name or _default_pipeline_name(goal)
    pipeline_dir = paths.pipeline_dir(pipeline_name)
    with tracer.span("run", run_id=run_id, agent="orchestrator", inputs={"goal": goal, "sources": len(sources)}):
        payloads = _extract_all(
            sources,
            paths=paths,
            run_id=run_id,
            tracer=tracer,
            model=config.openai_model,
            codex_factory=codex_factory,
        )
        _write_payloads(paths, run_id, payloads)
        with tracer.span("profile", run_id=run_id, agent="profiler", inputs={"payloads": len(payloads)}):
            findings = profile_payloads(payloads, bus, run_id)
        plan = _plan_until_ready(
            run_id=run_id,
            goal=goal,
            name=pipeline_name,
            payloads=payloads,
            findings=findings,
            hitl=hitl,
            pipeline_dir=pipeline_dir,
            prompt=prompt,
            tracer=tracer,
        )
        if plan.blocked:
            tracer.flush()
            return RunSummary(
                run_id=run_id,
                pipeline_name=plan.name,
                status="blocked",
                pipeline_dir=pipeline_dir,
                report_path=None,
                questions=[question.id for question in plan.questions],
                trace_url=tracer.trace_url(),
            )
        if review and prompt and not _approve_plan(input_fn, output_fn, pipeline_dir):
            tracer.flush()
            return RunSummary(
                run_id=run_id,
                pipeline_name=plan.name,
                status="review-blocked",
                pipeline_dir=pipeline_dir,
                report_path=None,
                questions=[],
                trace_url=tracer.trace_url(),
            )
        with tracer.span("build_report", run_id=run_id, agent="builder", inputs={"pipeline": plan.name}):
            report = render_report(goal=goal, payloads=payloads, findings=findings, plan=plan)
            report_path = write_report(report, pipeline_dir, run_id)
    tracer.flush()
    return RunSummary(
        run_id=run_id,
        pipeline_name=plan.name,
        status="complete",
        pipeline_dir=pipeline_dir,
        report_path=report_path,
        questions=[],
        trace_url=tracer.trace_url(),
    )


def run_saved_pipeline(
    *,
    name: str,
    paths: StitchPaths | None = None,
    codex_factory: CodexFactory | None = None,
) -> RunSummary:
    paths = paths or StitchPaths.discover()
    config = load_config(paths)
    stored = load_pipeline(paths, name)
    run_id = _new_run_id()
    tracer = init_tracer(project=config.weave_project, run_id=run_id, agent="orchestrator")
    bus = create_bus(paths, config.redis_url)
    goal = str(stored.yaml.get("goal") or name)
    sources = [Source.from_dict(item) for item in stored.yaml.get("sources", [])]
    if not sources:
        raise RuntimeError(f"Pipeline {name!r} does not contain saved sources.")

    with tracer.span("pipeline_rerun", run_id=run_id, agent="orchestrator", inputs={"pipeline": name}):
        payloads = _extract_all(
            sources,
            paths=paths,
            run_id=run_id,
            tracer=tracer,
            model=config.openai_model,
            codex_factory=codex_factory,
        )
        _write_payloads(paths, run_id, payloads)
        findings = profile_payloads(payloads, bus, run_id)
        plan = PipelinePlan(name=name, pipeline_md=stored.markdown, pipeline_yaml=stored.yaml, questions=[])
        report = render_report(goal=goal, payloads=payloads, findings=findings, plan=plan)
        report_path = write_report(report, stored.directory, run_id)
    tracer.flush()
    return RunSummary(
        run_id=run_id,
        pipeline_name=name,
        status="complete",
        pipeline_dir=stored.directory,
        report_path=report_path,
        questions=[],
        trace_url=tracer.trace_url(),
    )


def _extract_all(
    sources: list[Source],
    *,
    paths: StitchPaths,
    run_id: str,
    tracer: StitchTracer,
    model: str,
    codex_factory: CodexFactory | None,
) -> list[ExtractedPayload]:
    results: list[ExtractedPayload | None] = [None] * len(sources)
    with tracer.span("extract_fanout", agent="orchestrator", inputs={"sources": len(sources)}):
        with ThreadPoolExecutor(max_workers=len(sources), thread_name_prefix="stitch-agent") as pool:
            future_to_index = {
                pool.submit(
                    _extract_one,
                    source,
                    paths,
                    run_id,
                    tracer,
                    model,
                    codex_factory,
                ): index
                for index, source in enumerate(sources)
            }
            for future in as_completed(future_to_index):
                results[future_to_index[future]] = future.result()
    return [result for result in results if result is not None]


def _extract_one(
    source: Source,
    paths: StitchPaths,
    run_id: str,
    tracer: StitchTracer,
    model: str,
    codex_factory: CodexFactory | None,
) -> ExtractedPayload:
    with tracer.span(
        "extract_source",
        source_id=source.id,
        agent="extractor",
        inputs={"uri": source.uri, "kind": source.kind, "backend": "codex-sdk", "model": model},
    ):
        agent_args: dict[str, Any] = {
            "source": source,
            "paths": paths,
            "run_id": run_id,
            "model": model,
            "tracer": tracer,
        }
        if codex_factory is not None:
            agent_args["codex_factory"] = codex_factory
        agent = ExtractorAgent(**agent_args)
        return agent.run()


def _plan_until_ready(
    *,
    run_id: str,
    goal: str,
    name: str,
    payloads: list[ExtractedPayload],
    findings: list[Any],
    hitl: HitlManager,
    pipeline_dir: Path,
    prompt: bool,
    tracer: StitchTracer,
) -> PipelinePlan:
    with tracer.span("plan", run_id=run_id, agent="strategist", inputs={"goal": goal}):
        plan = create_pipeline_plan(
            run_id=run_id,
            goal=goal,
            name=name,
            payloads=payloads,
            findings=findings,
            hitl=hitl,
        )
        write_pipeline(plan, pipeline_dir)
    if not plan.questions:
        return plan

    with tracer.span("hitl_questions", run_id=run_id, agent="strategist", inputs={"questions": len(plan.questions)}):
        for question in plan.questions:
            hitl.publish(question)
        if not prompt:
            return plan
        hitl.prompt_pending(run_id)

    with tracer.span("plan_after_hitl", run_id=run_id, agent="strategist", inputs={"goal": goal}):
        resumed = create_pipeline_plan(
            run_id=run_id,
            goal=goal,
            name=name,
            payloads=payloads,
            findings=findings,
            hitl=hitl,
        )
        write_pipeline(resumed, pipeline_dir)
    return resumed


def _write_payloads(paths: StitchPaths, run_id: str, payloads: list[ExtractedPayload]) -> None:
    write_document(paths.payloads_dir / f"{run_id}.yaml", {"payloads": [payload.to_dict() for payload in payloads]})


def _approve_plan(input_fn: Any, output_fn: Any, pipeline_dir: Path) -> bool:
    output_fn(f"Plan written to {pipeline_dir / 'pipeline.md'}")
    answer = input_fn("Approve plan and build report? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _default_pipeline_name(goal: str) -> str:
    return _slug(goal)
