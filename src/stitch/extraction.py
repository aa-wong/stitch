from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlparse

from .models import Citation, ExtractedPayload, Source
from .sandbox import plan_extraction_sandbox

_MAX_EXCERPT_CHARS = 280


@dataclass(frozen=True)
class FetchedDocument:
    text: str
    content_type: str | None = None
    final_url: str | None = None


FetchResultLike = FetchedDocument | str | bytes | tuple[Any, ...] | dict[str, Any]
UrlFetcher = Callable[[str], FetchResultLike]


class _HTMLMarkdownParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = unescape(data).strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return _normalize_markdown(" ".join(self.parts))


def fetch_url(uri: str, *, timeout: float = 20.0) -> FetchedDocument:
    request = urllib.request.Request(
        uri,
        headers={"User-Agent": "stitch-extractor/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        content_type = response.headers.get("content-type")
        charset = response.headers.get_content_charset() or "utf-8"
        return FetchedDocument(
            text=raw.decode(charset, errors="replace"),
            content_type=content_type,
            final_url=response.geturl(),
        )


def extract_source(
    source: Source,
    *,
    fetcher: UrlFetcher | None = None,
    workspace_root: Path | str | None = None,
    prefer_openshell: bool = True,
) -> ExtractedPayload:
    workspace_path = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
    policy_path = workspace_path / ".stitch" / "policies" / f"{source.id}.openshell.json"
    source_payload_path = workspace_path / ".stitch" / "sandbox-inputs" / f"{source.id}.json"
    sandbox_plan = plan_extraction_sandbox(
        source,
        workspace_root=workspace_path,
        policy_path=policy_path,
        source_payload_path=source_payload_path,
        prefer_openshell=prefer_openshell,
    )

    if sandbox_plan.backend == "openshell" and os.getenv("STITCH_EXTRACTOR_CHILD") != "1":
        return _extract_in_openshell(source, sandbox_plan, policy_path, source_payload_path, workspace_path)

    payload = _extract_direct(source, fetcher)
    metadata = {
        **payload.metadata,
        "sandbox": sandbox_plan.to_dict(),
    }
    return ExtractedPayload(
        source=payload.source,
        markdown=payload.markdown,
        citations=payload.citations,
        metadata=metadata,
    )


def _extract_direct(source: Source, fetcher: UrlFetcher | None = None) -> ExtractedPayload:
    if source.kind == "file":
        return _extract_file(source)
    if source.kind == "url":
        return _extract_url(source, fetcher or fetch_url)
    if source.kind == "feed":
        return _extract_feed(source, fetcher or fetch_url)
    raise ValueError(f"Unsupported source kind: {source.kind!r}")


def extract_sources_parallel(
    sources: Iterable[Source],
    *,
    fetcher: UrlFetcher | None = None,
    workspace_root: Path | str | None = None,
    prefer_openshell: bool = True,
) -> list[ExtractedPayload]:
    source_list = list(sources)
    if not source_list:
        return []

    results: list[ExtractedPayload | None] = [None] * len(source_list)
    with ThreadPoolExecutor(max_workers=len(source_list), thread_name_prefix="stitch-extract") as pool:
        future_to_index = {
            pool.submit(
                extract_source,
                source,
                fetcher=fetcher,
                workspace_root=workspace_root,
                prefer_openshell=prefer_openshell,
            ): index
            for index, source in enumerate(source_list)
        }
        for future in as_completed(future_to_index):
            results[future_to_index[future]] = future.result()

    return [result for result in results if result is not None]


def _extract_in_openshell(
    source: Source,
    sandbox_plan: Any,
    policy_path: Path,
    source_payload_path: Path,
    workspace_root: Path,
) -> ExtractedPayload:
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    source_payload_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(sandbox_plan.policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    source_payload_path.write_text(json.dumps(source.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env["STITCH_EXTRACTOR_CHILD"] = "1"
    completed = subprocess.run(
        sandbox_plan.command,
        cwd=workspace_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"OpenShell extractor failed for {source.id}: {detail}")

    payload = ExtractedPayload.from_dict(json.loads(completed.stdout))
    metadata = {
        **payload.metadata,
        "sandbox": sandbox_plan.to_dict(),
    }
    return ExtractedPayload(
        source=payload.source,
        markdown=payload.markdown,
        citations=payload.citations,
        metadata=metadata,
    )


def _extract_file(source: Source) -> ExtractedPayload:
    path = _source_file_path(source)
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    if suffix == ".csv":
        markdown, citation_segments = _csv_to_markdown(raw)
        format_name = "csv"
    else:
        markdown = _normalize_markdown(raw)
        citation_segments = _line_segments(markdown)
        format_name = "markdown" if suffix in {".md", ".markdown"} else "text"

    return ExtractedPayload(
        source=source,
        markdown=markdown,
        citations=_make_citations(source, citation_segments, fallback_locator=path.name),
        metadata={
            "extractor": "local-file",
            "format": format_name,
            "path": str(path),
        },
    )


def _extract_url(source: Source, fetcher: UrlFetcher) -> ExtractedPayload:
    fetched = _coerce_fetched_document(fetcher(source.uri))
    markdown, citation_segments, format_name = _document_to_markdown(
        source,
        fetched.text,
        content_type=fetched.content_type,
    )
    return ExtractedPayload(
        source=source,
        markdown=markdown,
        citations=_make_citations(source, citation_segments, fallback_locator="document"),
        metadata={
            "extractor": "url",
            "format": format_name,
            "content_type": fetched.content_type,
            "final_url": fetched.final_url or source.uri,
        },
    )


def _extract_feed(source: Source, fetcher: UrlFetcher) -> ExtractedPayload:
    fetched = _coerce_fetched_document(fetcher(source.uri))
    try:
        markdown, citation_segments = _feed_to_markdown(fetched.text)
        if citation_segments or markdown:
            format_name = "feed"
        else:
            markdown, citation_segments, format_name = _document_to_markdown(
                source,
                fetched.text,
                content_type=fetched.content_type,
            )
    except ET.ParseError:
        markdown, citation_segments, format_name = _document_to_markdown(
            source,
            fetched.text,
            content_type=fetched.content_type,
        )

    return ExtractedPayload(
        source=source,
        markdown=markdown,
        citations=_make_citations(source, citation_segments, fallback_locator="feed"),
        metadata={
            "extractor": "feed",
            "format": format_name,
            "content_type": fetched.content_type,
            "final_url": fetched.final_url or source.uri,
        },
    )


def _document_to_markdown(
    source: Source,
    text: str,
    *,
    content_type: str | None,
) -> tuple[str, list[tuple[str, str]], str]:
    lower_content_type = (content_type or "").lower()
    path_suffix = Path(urlparse(source.uri).path).suffix.lower()

    if "csv" in lower_content_type or path_suffix == ".csv":
        markdown, citation_segments = _csv_to_markdown(text)
        return markdown, citation_segments, "csv"

    if "html" in lower_content_type or _looks_like_html(text):
        markdown = _html_to_markdown(text)
        return markdown, _line_segments(markdown), "html"

    markdown = _normalize_markdown(text)
    return markdown, _line_segments(markdown), "text"


def _source_file_path(source: Source) -> Path:
    if source.path:
        return Path(source.path).expanduser().resolve()

    parsed = urlparse(source.uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).expanduser().resolve()

    return Path(source.uri).expanduser().resolve()


def _coerce_fetched_document(value: FetchResultLike) -> FetchedDocument:
    if isinstance(value, FetchedDocument):
        return value
    if isinstance(value, bytes):
        return FetchedDocument(text=value.decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return FetchedDocument(text=value)
    if isinstance(value, dict):
        return FetchedDocument(
            text=str(value.get("text", "")),
            content_type=value.get("content_type"),
            final_url=value.get("final_url"),
        )
    if isinstance(value, tuple):
        if len(value) == 2:
            return FetchedDocument(text=str(value[0]), content_type=str(value[1]))
        if len(value) >= 3:
            return FetchedDocument(
                text=str(value[0]),
                content_type=str(value[1]) if value[1] is not None else None,
                final_url=str(value[2]) if value[2] is not None else None,
            )
    raise TypeError(f"Fetcher returned unsupported value: {type(value)!r}")


def _csv_to_markdown(text: str) -> tuple[str, list[tuple[str, str]]]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return "", []

    width = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    header = normalized_rows[0]
    body = normalized_rows[1:]
    table_lines = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    table_lines.extend(
        "| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |"
        for row in body
    )

    citation_segments = [
        (f"row {index + 2}", ", ".join(cell for cell in row if cell))
        for index, row in enumerate(body)
        if any(cell.strip() for cell in row)
    ]
    if not citation_segments and any(cell.strip() for cell in header):
        citation_segments.append(("header", ", ".join(cell for cell in header if cell)))

    return "\n".join(table_lines).strip(), citation_segments


def _feed_to_markdown(text: str) -> tuple[str, list[tuple[str, str]]]:
    root = ET.fromstring(text)
    entries = _feed_entries(root)
    lines: list[str] = []
    citation_segments: list[tuple[str, str]] = []

    for index, entry in enumerate(entries, start=1):
        title = entry.get("title") or f"Feed item {index}"
        link = entry.get("link")
        summary = _html_to_markdown(entry.get("summary") or "")
        lines.append(f"## {title}")
        if link:
            lines.append(f"[Source]({link})")
        if summary:
            lines.append(summary)
        citation_segments.append((f"item {index}", " ".join(part for part in [title, summary] if part)))

    return _normalize_markdown("\n\n".join(lines)), citation_segments


def _feed_entries(root: ET.Element) -> list[dict[str, str | None]]:
    root_tag = _local_name(root.tag)
    if root_tag == "rss":
        channel = root.find("channel")
        if channel is None:
            return []
        return [
            {
                "title": _child_text(item, "title"),
                "link": _child_text(item, "link"),
                "summary": _child_text(item, "description"),
            }
            for item in channel.findall("item")
        ]

    if root_tag == "feed":
        entries = []
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
            entries.append(
                {
                    "title": _child_text(entry, "{http://www.w3.org/2005/Atom}title"),
                    "link": _atom_link(entry),
                    "summary": (
                        _child_text(entry, "{http://www.w3.org/2005/Atom}summary")
                        or _child_text(entry, "{http://www.w3.org/2005/Atom}content")
                    ),
                }
            )
        return entries

    return []


def _child_text(parent: ET.Element, tag: str) -> str | None:
    child = parent.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _atom_link(entry: ET.Element) -> str | None:
    for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
        href = link.attrib.get("href")
        if href:
            return href
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _html_to_markdown(text: str) -> str:
    parser = _HTMLMarkdownParser()
    parser.feed(text)
    return parser.text()


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*(html|body|main|article|section|p|div|h1|h2)\b", text, re.I))


def _normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _line_segments(markdown: str) -> list[tuple[str, str]]:
    return [
        (f"line {index}", line)
        for index, line in enumerate(markdown.splitlines(), start=1)
        if line.strip()
    ]


def _make_citations(
    source: Source,
    segments: list[tuple[str, str]],
    *,
    fallback_locator: str,
) -> list[Citation]:
    effective_segments = segments or [(fallback_locator, "")]
    return [
        Citation(
            id=f"{source.id}:{index}",
            source_id=source.id,
            label=source.label,
            uri=source.uri,
            locator=locator,
            excerpt=_excerpt(excerpt),
        )
        for index, (locator, excerpt) in enumerate(effective_segments, start=1)
    ]


def _excerpt(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= _MAX_EXCERPT_CHARS:
        return compact
    return compact[: _MAX_EXCERPT_CHARS - 1].rstrip() + "..."


def _escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stitch.extraction")
    parser.add_argument("--source-json", required=True)
    parser.add_argument("--no-openshell", action="store_true")
    args = parser.parse_args(argv)

    source = Source.from_dict(json.loads(Path(args.source_json).read_text(encoding="utf-8")))
    payload = extract_source(source, prefer_openshell=not args.no_openshell)
    sys.stdout.write(json.dumps(payload.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
