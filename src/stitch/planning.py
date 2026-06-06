from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hitl import HitlManager
from .json_yaml import write_document
from .models import ExtractedPayload, HitlQuestion, ProfileFinding

CURRENCY_PATTERN = re.compile(r"\b(USD|EUR|GBP|CAD|AUD|\$|€|£)\b")


@dataclass(frozen=True)
class PipelinePlan:
    name: str
    pipeline_md: str
    pipeline_yaml: dict[str, Any]
    questions: list[HitlQuestion]

    @property
    def blocked(self) -> bool:
        return bool(self.questions)


def create_pipeline_plan(
    *,
    run_id: str,
    goal: str,
    payloads: list[ExtractedPayload],
    findings: list[ProfileFinding],
    hitl: HitlManager,
    name: str | None = None,
) -> PipelinePlan:
    pipeline_name = name or _slug(goal)
    answers = hitl.answers_by_context_key(run_id)
    questions = _detect_questions(run_id, payloads, answers)
    pipeline_yaml = {
        "name": pipeline_name,
        "goal": goal,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "sources": [payload.source.to_dict() for payload in payloads],
        "steps": [
            {"id": "extract", "agent": "extractor", "action": "collect source payloads"},
            {"id": "profile", "agent": "profiler", "action": "infer schemas, entities, and overlaps"},
            {"id": "plan", "agent": "strategist", "action": "record joins, canonicalization, and open questions"},
            {"id": "build", "agent": "builder", "action": "materialize cited markdown report"},
        ],
        "findings": [finding.to_dict() for finding in findings],
        "human_answers": hitl.as_pipeline_records(run_id),
        "status": "blocked" if questions else "ready",
    }
    pipeline_md = _render_pipeline_markdown(goal, findings, pipeline_yaml, questions)
    return PipelinePlan(name=pipeline_name, pipeline_md=pipeline_md, pipeline_yaml=pipeline_yaml, questions=questions)


def write_pipeline(plan: PipelinePlan, pipeline_dir: Path) -> None:
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    (pipeline_dir / "pipeline.md").write_text(plan.pipeline_md, encoding="utf-8")
    write_document(pipeline_dir / "pipeline.yaml", plan.pipeline_yaml)


def _detect_questions(
    run_id: str,
    payloads: list[ExtractedPayload],
    answers: dict[str, str],
) -> list[HitlQuestion]:
    if "canonical_currency" in answers:
        return []
    currencies = sorted({_normalize_currency(match.group(1)) for payload in payloads for match in CURRENCY_PATTERN.finditer(payload.markdown)})
    if len(currencies) <= 1:
        return []
    question_id = "q_" + hashlib.sha1(f"{run_id}:canonical_currency".encode("utf-8")).hexdigest()[:10]
    return [
        HitlQuestion(
            id=question_id,
            run_id=run_id,
            agent="strategist",
            question="Multiple currencies appear across sources. Which currency should the report use as canonical?",
            options=currencies,
            context={"key": "canonical_currency", "currencies": currencies},
            created_at=datetime.now(UTC).isoformat(),
        )
    ]


def _render_pipeline_markdown(
    goal: str,
    findings: list[ProfileFinding],
    pipeline_yaml: dict[str, Any],
    questions: list[HitlQuestion],
) -> str:
    lines = [
        f"# Pipeline: {pipeline_yaml['name']}",
        "",
        f"Goal: {goal}",
        "",
        "## Strategy",
        "",
        "1. Extract each source independently inside its per-source sandbox policy.",
        "2. Profile fields, entities, and overlaps into the shared blackboard.",
        "3. Normalize shared fields and entities before report generation.",
        "4. Render a markdown signals report with citations back to source payloads.",
        "",
        "## Source Findings",
        "",
    ]
    for finding in findings:
        fields = ", ".join(finding.fields) if finding.fields else "none inferred"
        overlaps = ", ".join(finding.overlaps) if finding.overlaps else "none yet"
        lines.extend(
            [
                f"### {finding.label}",
                "",
                f"- Summary: {finding.summary}",
                f"- Fields: {fields}",
                f"- Overlaps: {overlaps}",
                "",
            ]
        )
    if pipeline_yaml["human_answers"]:
        lines.extend(["## Recorded Human Answers", ""])
        for record in pipeline_yaml["human_answers"]:
            lines.append(f"- {record['question']} Answer: {record['answer']}")
        lines.append("")
    if questions:
        lines.extend(["## Open Questions", ""])
        for question in questions:
            lines.append(f"- `{question.id}`: {question.question} Options: {', '.join(question.options)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        slug = "pipeline"
    return slug[:64]


def _normalize_currency(value: str) -> str:
    return {"$": "USD", "€": "EUR", "£": "GBP"}.get(value, value)
