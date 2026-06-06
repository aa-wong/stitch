from __future__ import annotations

import csv
import io
import re
from collections import Counter

from .bus import CoordinationBus
from .models import ExtractedPayload, ProfileFinding

CAPITALIZED_PHRASE = re.compile(r"\b[A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,3}\b")


def profile_payloads(payloads: list[ExtractedPayload], bus: CoordinationBus, run_id: str) -> list[ProfileFinding]:
    findings = [_profile_payload(payload) for payload in payloads]
    _mark_overlaps(findings)
    for finding in findings:
        bus.write_blackboard(run_id, f"profile:{finding.source_id}", finding.to_dict())
    bus.write_blackboard(run_id, "profiles", [finding.to_dict() for finding in findings])
    return findings


def _profile_payload(payload: ExtractedPayload) -> ProfileFinding:
    fields = _infer_fields(payload.markdown)
    entities = _infer_entities(payload.markdown, payload.source.label)
    summary = _first_signal_sentence(payload.markdown)
    return ProfileFinding(
        source_id=payload.source.id,
        label=payload.source.label,
        summary=summary,
        fields=fields,
        entities=entities,
    )


def _infer_fields(markdown: str) -> list[str]:
    table_fields = _fields_from_markdown_table(markdown)
    if table_fields:
        return table_fields
    csv_fields = _fields_from_csv_like_text(markdown)
    if csv_fields:
        return csv_fields
    headings = [line.strip("# ").strip() for line in markdown.splitlines() if line.startswith("#")]
    return _dedupe([heading.lower().replace(" ", "_") for heading in headings[:8]])


def _fields_from_markdown_table(markdown: str) -> list[str]:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip().lower().replace(" ", "_") for cell in stripped.strip("|").split("|")]
            if len(cells) > 1 and not all(set(cell) <= {"-"} for cell in cells):
                return [cell for cell in cells if cell]
    return []


def _fields_from_csv_like_text(markdown: str) -> list[str]:
    lines = [line for line in markdown.splitlines() if line.strip()]
    if not lines:
        return []
    try:
        row = next(csv.reader(io.StringIO(lines[0])))
    except csv.Error:
        return []
    if len(row) <= 1:
        return []
    return [cell.strip().lower().replace(" ", "_") for cell in row if cell.strip()]


def _infer_entities(markdown: str, fallback: str) -> list[str]:
    matches = [match.group(0).strip() for match in CAPITALIZED_PHRASE.finditer(markdown)]
    ignored = {"The", "This", "And", "Source", "Report", "Markdown"}
    counts = Counter(match for match in matches if match not in ignored and len(match) > 2)
    entities = [entity for entity, _ in counts.most_common(12)]
    return entities or [fallback]


def _first_signal_sentence(markdown: str) -> str:
    for raw_line in markdown.splitlines():
        line = raw_line.strip(" #|-")
        if len(line) >= 40:
            return line[:240]
    compact = " ".join(markdown.split())
    return compact[:240] if compact else "No textual content extracted."


def _mark_overlaps(findings: list[ProfileFinding]) -> None:
    field_counts = Counter(field for finding in findings for field in finding.fields)
    entity_counts = Counter(entity.lower() for finding in findings for entity in finding.entities)
    for finding in findings:
        overlaps = [
            f"field:{field}" for field in finding.fields if field_counts[field] > 1
        ] + [
            f"entity:{entity}" for entity in finding.entities if entity_counts[entity.lower()] > 1
        ]
        finding.overlaps.extend(_dedupe(overlaps))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result
