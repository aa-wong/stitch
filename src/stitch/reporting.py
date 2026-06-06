from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Citation, ExtractedPayload, ProfileFinding
from .planning import PipelinePlan


@dataclass(frozen=True)
class ReportResult:
    markdown: str
    path: Path


@dataclass(frozen=True)
class GoalSignal:
    label: str
    text: str
    citation: Citation | None


def render_report(
    *,
    goal: str,
    payloads: list[ExtractedPayload],
    findings: list[ProfileFinding],
    plan: PipelinePlan,
) -> str:
    citation_map = _first_citation_by_source(payloads)
    lines = [
        f"# Signals Report: {plan.name}",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        f"Goal: {goal}",
        "",
        "## Key Signals",
        "",
    ]
    goal_signals = _goal_relevant_signals(goal, payloads, citation_map)
    if goal_signals:
        for signal in goal_signals:
            marker = f"[^{signal.citation.id}]" if signal.citation else ""
            lines.append(f"- {signal.label}: {signal.text}{marker}")
    else:
        for finding in findings:
            citation = citation_map.get(finding.source_id)
            marker = f"[^{citation.id}]" if citation else ""
            lines.append(f"- {finding.label}: {finding.summary}{marker}")
    lines.extend(["", "## Source Notes", ""])
    for finding in findings:
        fields = ", ".join(finding.fields) if finding.fields else "none inferred"
        entities = ", ".join(finding.entities[:6]) if finding.entities else "none inferred"
        citation = citation_map.get(finding.source_id)
        marker = f"[^{citation.id}]" if citation else ""
        lines.extend(
            [
                f"### {finding.label}",
                "",
                f"- Inferred fields: {fields}",
                f"- Notable entities: {entities}{marker}",
                "",
            ]
        )
    lines.extend(["## Citations", ""])
    for payload in payloads:
        for citation in payload.citations:
            excerpt = " ".join(citation.excerpt.split())
            if len(excerpt) > 180:
                excerpt = excerpt[:177] + "..."
            lines.append(f"[^{citation.id}]: {citation.label}, {citation.locator}, {citation.uri}. \"{excerpt}\"")
    return "\n".join(lines).rstrip() + "\n"


def write_report(markdown: str, pipeline_dir: Path, run_id: str) -> Path:
    report_dir = pipeline_dir / "runs" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "report.md"
    path.write_text(markdown, encoding="utf-8")
    latest = pipeline_dir / "latest-report.md"
    latest.write_text(markdown, encoding="utf-8")
    return path


def _first_citation_by_source(payloads: list[ExtractedPayload]) -> dict[str, Citation]:
    citations: dict[str, Citation] = {}
    for payload in payloads:
        if payload.citations:
            citations[payload.source.id] = payload.citations[0]
    return citations


def _goal_relevant_signals(
    goal: str,
    payloads: list[ExtractedPayload],
    fallback_citations: dict[str, Citation],
) -> list[GoalSignal]:
    terms = _goal_terms(goal)
    if not terms:
        return []

    candidates: list[tuple[int, int, GoalSignal]] = []
    for payload in payloads:
        for line_number, raw_line in enumerate(payload.markdown.splitlines(), start=1):
            line = _clean_line(raw_line)
            if len(line) < 30:
                continue
            score = _term_score(line, terms)
            if score == 0:
                continue
            citation = _best_citation(payload, terms) or fallback_citations.get(payload.source.id)
            candidates.append(
                (
                    score,
                    -line_number,
                    GoalSignal(
                        label=payload.source.label,
                        text=_truncate(line, 360),
                        citation=citation,
                    ),
                )
            )

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    signals: list[GoalSignal] = []
    seen: set[tuple[str, str]] = set()
    for _, _, signal in candidates:
        key = (signal.label, signal.text.lower())
        if key in seen:
            continue
        signals.append(signal)
        seen.add(key)
        if len(signals) >= 5:
            break
    return signals


def _goal_terms(goal: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "for",
        "from",
        "how",
        "is",
        "of",
        "s",
        "the",
        "to",
        "what",
        "what's",
        "whats",
        "why",
    }
    terms = set()
    for token in re.findall(r"[A-Za-z0-9]+", goal.lower()):
        if token in stopwords:
            continue
        if len(token) >= 3 or token in {"ai", "ii", "v2", "v3"}:
            terms.add(token)
    return terms


def _term_score(text: str, terms: set[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if re.search(rf"\b{re.escape(term)}\b", lowered))


def _best_citation(payload: ExtractedPayload, terms: set[str]) -> Citation | None:
    scored = [
        (_term_score(citation.excerpt, terms), citation)
        for citation in payload.citations
    ]
    scored = [item for item in scored if item[0] > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _clean_line(line: str) -> str:
    return " ".join(line.strip(" #|-").split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
