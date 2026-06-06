from __future__ import annotations

import re

from .models import ExtractedPayload
from .paths import StitchPaths


def write_extracted_document_artifacts(
    paths: StitchPaths,
    run_id: str,
    payloads: list[ExtractedPayload],
) -> list[ExtractedPayload]:
    artifact_dir = paths.stitch_dir / "extracted" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    enriched: list[ExtractedPayload] = []
    for payload in payloads:
        artifact_path = artifact_dir / f"{payload.source.id}.md"
        artifact = render_extracted_document_artifact(payload)
        artifact_path.write_text(artifact, encoding="utf-8")
        enriched.append(
            ExtractedPayload(
                source=payload.source,
                markdown=payload.markdown,
                citations=payload.citations,
                metadata={
                    **payload.metadata,
                    "artifact_path": str(artifact_path),
                    "artifact_kind": "synthetic-document-markdown",
                },
            )
        )
    return enriched


def render_extracted_document_artifact(payload: ExtractedPayload) -> str:
    source = payload.source
    lines = [
        f"# Extracted Document: {source.label}",
        "",
        "## Source Metadata",
        "",
        f"- Source ID: `{source.id}`",
        f"- Kind: `{source.kind}`",
        f"- URI: {source.uri}",
        f"- Extractor: `{payload.metadata.get('extractor', 'unknown')}`",
        f"- Format: `{payload.metadata.get('format', 'unknown')}`",
        "",
        "## Synthetic Description",
        "",
        synthesize_document_description(payload),
        "",
        "## Normalized Markdown Payload",
        "",
        payload.markdown or "_No textual content was extracted._",
        "",
        "## Citation Segments",
        "",
    ]
    for citation in payload.citations:
        excerpt = citation.excerpt or "_No excerpt text._"
        lines.append(f"- `{citation.id}` {citation.locator}: {excerpt}")
    return "\n".join(lines).rstrip() + "\n"


def synthesize_document_description(payload: ExtractedPayload) -> str:
    source = payload.source
    format_name = str(payload.metadata.get("format") or source.kind)
    non_empty_lines = [line.strip() for line in payload.markdown.splitlines() if line.strip()]
    citation_count = len(payload.citations)
    shape = _describe_shape(payload.markdown, format_name)
    focus = _describe_focus(non_empty_lines)
    return (
        f"This source is a {format_name} document labeled \"{source.label}\". "
        f"The extractor converted it into a markdown payload with {len(non_empty_lines)} "
        f"non-empty lines and {citation_count} citeable segment{'s' if citation_count != 1 else ''}. "
        f"{shape} {focus} Downstream profiler, strategist, and builder agents should treat "
        "the normalized markdown payload below as the canonical extracted representation."
    )


def _describe_shape(markdown: str, format_name: str) -> str:
    if format_name == "csv" or _looks_like_markdown_table(markdown):
        fields = _table_fields(markdown)
        if fields:
            return "It appears to be structured tabular data with fields: " + ", ".join(fields) + "."
        return "It appears to be structured tabular data."
    if format_name == "feed":
        item_count = sum(1 for line in markdown.splitlines() if line.startswith("## "))
        return f"It appears to be a feed containing {item_count or 'one or more'} extracted items."
    headings = [line.strip("# ").strip() for line in markdown.splitlines() if line.startswith("#")]
    if headings:
        return "It contains markdown-style sections including: " + ", ".join(headings[:4]) + "."
    return "It appears to be prose or semi-structured text."


def _describe_focus(non_empty_lines: list[str]) -> str:
    candidate = next((line for line in non_empty_lines if not set(line) <= {"|", "-", " "}), "")
    candidate = re.sub(r"\s+", " ", candidate).strip(" |-")
    if not candidate:
        return "No dominant textual focus could be inferred from the extracted content."
    if len(candidate) > 160:
        candidate = candidate[:157].rstrip() + "..."
    return f"A representative extracted signal is: \"{candidate}\"."


def _looks_like_markdown_table(markdown: str) -> bool:
    return any(line.strip().startswith("|") and line.strip().endswith("|") for line in markdown.splitlines())


def _table_fields(markdown: str) -> list[str]:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            fields = [field.strip() for field in stripped.strip("|").split("|")]
            if len(fields) > 1 and not all(set(field) <= {"-"} for field in fields):
                return [field for field in fields if field]
    return []
