from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Citation, ExtractedPayload, ProfileFinding
from .planning import PipelinePlan


@dataclass(frozen=True)
class ReportResult:
    markdown: str
    path: Path


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
