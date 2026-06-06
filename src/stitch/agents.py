from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from agents import Agent, RunConfig, RunContextWrapper, Runner, function_tool, trace
from pydantic import BaseModel

from .json_yaml import write_document
from .models import ExtractedPayload, Source
from .paths import StitchPaths
from .tracing import StitchTracer

AgentsRunner = Any
ProgressFn = Callable[[str], None]

_SOURCE_READ_LIMIT_BYTES = 2_000_000
_HTTP_TIMEOUT_SECONDS = 30
_REQUIRED_ARTIFACT_SECTIONS = (
    "# Extracted Document:",
    "## Source Metadata",
    "## Synthetic Description",
    "## Normalized Markdown Payload",
    "## Citation Segments",
)


class ExtractorFinalOutput(BaseModel):
    artifact_path: str
    payload_path: str


@dataclass(frozen=True)
class ExtractorRunContext:
    source: Source
    source_input_path: Path
    artifact_path: Path
    payload_path: Path


@function_tool
def read_assigned_source(wrapper: RunContextWrapper[ExtractorRunContext]) -> str:
    """Read the exact source assigned to this extractor agent."""
    context = wrapper.context
    source = context.source
    descriptor = json.dumps(source.to_dict(), indent=2, sort_keys=True)
    if source.kind == "file":
        body = _read_file_source(source)
    else:
        body = _read_url_source(source)
    return "\n".join(
        [
            "SOURCE DESCRIPTOR",
            descriptor,
            "",
            "SOURCE CONTENT",
            body,
        ]
    )


@function_tool
def write_markdown_artifact(
    wrapper: RunContextWrapper[ExtractorRunContext],
    content: str,
) -> str:
    """Write the required markdown artifact for this extractor agent."""
    context = wrapper.context
    missing = [section for section in _REQUIRED_ARTIFACT_SECTIONS if section not in content]
    if missing:
        return "ERROR: markdown artifact is missing required sections: " + ", ".join(missing)
    context.artifact_path.parent.mkdir(parents=True, exist_ok=True)
    context.artifact_path.write_text(_with_trailing_newline(content), encoding="utf-8")
    return f"OK: wrote markdown artifact to {context.artifact_path}"


@function_tool
def write_payload_json(
    wrapper: RunContextWrapper[ExtractorRunContext],
    payload_json: str,
) -> str:
    """Validate and write the required JSON payload companion for this extractor agent."""
    context = wrapper.context
    try:
        raw_payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        return f"ERROR: payload_json is not valid JSON: {exc}"

    try:
        payload = ExtractedPayload.from_dict(raw_payload)
    except (KeyError, TypeError, ValueError) as exc:
        return f"ERROR: payload_json does not match the ExtractedPayload shape: {exc}"

    if payload.source != context.source:
        return "ERROR: payload source must exactly match the assigned source descriptor."
    if not payload.markdown.strip():
        return "ERROR: payload markdown must not be empty."
    if not payload.citations:
        return "ERROR: payload must contain at least one citation segment."

    bad_citations = [
        citation.id
        for citation in payload.citations
        if citation.source_id != context.source.id
        or citation.label != context.source.label
        or citation.uri != context.source.uri
        or not citation.locator.strip()
        or not citation.excerpt.strip()
    ]
    if bad_citations:
        return "ERROR: all citations must reference the assigned source with locators and excerpts."

    metadata = {
        **payload.metadata,
        "extractor": "openai-agents-sdk",
        "artifact_kind": "synthetic-document-markdown",
    }
    normalized = ExtractedPayload(
        source=payload.source,
        markdown=payload.markdown,
        citations=payload.citations,
        metadata=metadata,
    )
    write_document(context.payload_path, normalized.to_dict())
    return f"OK: wrote JSON payload to {context.payload_path}"


@dataclass(frozen=True)
class ExtractorAgent:
    """OpenAI Agents SDK-backed per-source extractor subagent."""

    source: Source
    paths: StitchPaths
    run_id: str
    model: str
    tracer: StitchTracer
    timeout_seconds: int = 300
    progress_fn: ProgressFn | None = None
    runner: AgentsRunner = Runner

    def run(self) -> ExtractedPayload:
        self._require_openai_auth()
        source_input_path = self._source_input_path()
        artifact_path = self._artifact_path()
        payload_path = self._payload_path()
        source_input_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        write_document(source_input_path, self.source.to_dict())

        context = ExtractorRunContext(
            source=self.source,
            source_input_path=source_input_path,
            artifact_path=artifact_path,
            payload_path=payload_path,
        )
        sdk_agent = self._build_agent()
        prompt = self._prompt(source_input_path, artifact_path, payload_path)

        self._progress(f"extractor {self.source.id}: starting OpenAI Agents SDK run")
        with self.tracer.span(
            "agents_extractor_agent",
            run_id=self.run_id,
            source_id=self.source.id,
            agent="extractor",
            inputs={
                "source_uri": self.source.uri,
                "source_kind": self.source.kind,
                "model": self.model,
                "artifact_path": str(artifact_path),
                "payload_path": str(payload_path),
            },
        ):
            with self.tracer.span(
                "agents_runner_run",
                run_id=self.run_id,
                source_id=self.source.id,
                agent="extractor",
                inputs={"model": self.model},
            ):
                self._progress(f"extractor {self.source.id}: running extraction agent")
                result = self._run_agent_with_timeout(sdk_agent, prompt, context)
                self._progress(f"extractor {self.source.id}: extraction agent completed")
            payload = self._load_payload(payload_path, result)
            self._progress(f"extractor {self.source.id}: artifact written to {artifact_path}")
        return payload

    def _build_agent(self) -> Agent[ExtractorRunContext]:
        return Agent[ExtractorRunContext](
            name=f"Stitch extractor {self.source.id}",
            instructions=(
                "You are a Stitch extractor subagent. Extract exactly one assigned source. "
                "You must call read_assigned_source before writing outputs. You must call "
                "write_markdown_artifact and write_payload_json before returning final output. "
                "Do not use any source other than the assigned source returned by the tool."
            ),
            model=self.model,
            tools=[read_assigned_source, write_markdown_artifact, write_payload_json],
            output_type=ExtractorFinalOutput,
        )

    def _run_agent_with_timeout(
        self,
        sdk_agent: Agent[ExtractorRunContext],
        prompt: str,
        context: ExtractorRunContext,
    ) -> Any:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"agents-{self.source.id}")
        future = executor.submit(self._run_agent_sync, sdk_agent, prompt, context)
        try:
            return future.result(timeout=self.timeout_seconds)
        except TimeoutError as exc:
            future.cancel()
            raise RuntimeError(
                f"OpenAI Agents extractor timed out after {self.timeout_seconds}s for source {self.source.id}. "
                "Check OPENAI_API_KEY, model access, network connectivity, and Weave/OpenAI trace dashboards."
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _run_agent_sync(
        self,
        sdk_agent: Agent[ExtractorRunContext],
        prompt: str,
        context: ExtractorRunContext,
    ) -> Any:
        return asyncio.run(self._run_agent_async(sdk_agent, prompt, context))

    async def _run_agent_async(
        self,
        sdk_agent: Agent[ExtractorRunContext],
        prompt: str,
        context: ExtractorRunContext,
    ) -> Any:
        with trace(
            "stitch_extraction",
            group_id=self.run_id,
            metadata={"run_id": self.run_id, "source_id": self.source.id},
        ):
            return await self.runner.run(
                sdk_agent,
                prompt,
                context=context,
                max_turns=8,
                run_config=RunConfig(
                    workflow_name="stitch_extraction",
                    group_id=self.run_id,
                    trace_metadata={"run_id": self.run_id, "source_id": self.source.id},
                ),
            )

    def _progress(self, message: str) -> None:
        if self.progress_fn is not None:
            self.progress_fn(message)

    def _require_openai_auth(self) -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required: Stitch extraction uses OpenAI Agents SDK subagents.")

    def _source_input_path(self) -> Path:
        return self.paths.stitch_dir / "agent-inputs" / self.run_id / f"{self.source.id}.json"

    def _artifact_path(self) -> Path:
        return self.paths.stitch_dir / "extracted" / self.run_id / f"{self.source.id}.md"

    def _payload_path(self) -> Path:
        return self.paths.stitch_dir / "agent-payloads" / self.run_id / f"{self.source.id}.json"

    def _load_payload(self, payload_path: Path, result: Any) -> ExtractedPayload:
        final_output = self._final_output(result)
        if final_output.payload_path != str(payload_path):
            raise RuntimeError("OpenAI Agents extractor final output did not reference the expected payload path.")
        artifact_path = self._artifact_path()
        if final_output.artifact_path != str(artifact_path):
            raise RuntimeError("OpenAI Agents extractor final output did not reference the expected artifact path.")
        if not payload_path.exists():
            raise RuntimeError(f"OpenAI Agents extractor did not write payload file: {payload_path}")
        if not artifact_path.exists():
            raise RuntimeError(f"OpenAI Agents extractor did not write markdown artifact: {artifact_path}")

        payload = ExtractedPayload.from_dict(json.loads(payload_path.read_text(encoding="utf-8")))
        metadata = {
            **payload.metadata,
            "extractor": "openai-agents-sdk",
            "agents_last_response_id": getattr(result, "last_response_id", None),
            "artifact_path": str(artifact_path),
            "payload_path": str(payload_path),
        }
        return ExtractedPayload(
            source=payload.source,
            markdown=payload.markdown,
            citations=payload.citations,
            metadata=metadata,
        )

    def _final_output(self, result: Any) -> ExtractorFinalOutput:
        if hasattr(result, "final_output_as"):
            return result.final_output_as(ExtractorFinalOutput, raise_if_incorrect_type=True)
        final_output = getattr(result, "final_output", None)
        if isinstance(final_output, ExtractorFinalOutput):
            return final_output
        if isinstance(final_output, dict):
            return ExtractorFinalOutput.model_validate(final_output)
        raise RuntimeError("OpenAI Agents extractor returned no structured final output.")

    def _prompt(self, source_input_path: Path, artifact_path: Path, payload_path: Path) -> str:
        return f"""Extract exactly one Stitch source/document.

Source descriptor JSON path, for audit only:
{source_input_path}

Required sequence:
1. Call read_assigned_source and inspect the returned source descriptor and content.
2. Call write_markdown_artifact with the markdown artifact content.
3. Call write_payload_json with the JSON payload companion content.
4. Return final structured output with artifact_path and payload_path.

The markdown artifact must be written to:
{artifact_path}

It must contain these sections:
- # Extracted Document: <source label>
- ## Source Metadata
- ## Synthetic Description
- ## Normalized Markdown Payload
- ## Citation Segments

The synthetic description must be prose describing what the document is, its structure,
and the useful signals it appears to contain. The normalized markdown payload must be the
canonical extracted text/table/feed representation downstream agents should use.

The JSON payload must be written to:
{payload_path}

The JSON payload must match this shape:
{{
  "source": <the exact source descriptor object returned by read_assigned_source>,
  "markdown": "<the normalized markdown payload>",
  "citations": [
    {{
      "id": "<source_id>:1",
      "source_id": "<source_id>",
      "label": "<source label>",
      "uri": "<source uri>",
      "locator": "line 1 / row 2 / item 1 / section name",
      "excerpt": "<short source excerpt>"
    }}
  ],
  "metadata": {{
    "extractor": "openai-agents-sdk",
    "artifact_kind": "synthetic-document-markdown"
  }}
}}

Return only final structured output with:
artifact_path = "{artifact_path}"
payload_path = "{payload_path}"
"""


def _read_file_source(source: Source) -> str:
    path = Path(source.path or _file_uri_to_path(source.uri))
    if not path.is_file():
        raise FileNotFoundError(f"assigned source file does not exist: {path}")
    raw = path.read_bytes()
    if len(raw) > _SOURCE_READ_LIMIT_BYTES:
        raw = raw[:_SOURCE_READ_LIMIT_BYTES]
    text = raw.decode("utf-8", errors="replace")
    return _number_lines(text)


def _read_url_source(source: Source) -> str:
    request = urllib.request.Request(
        source.uri,
        headers={"User-Agent": "stitch/0.1 extractor"},
    )
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        raw = response.read(_SOURCE_READ_LIMIT_BYTES + 1)
        content_type = response.headers.get("content-type", "")
        final_url = response.geturl()
    if len(raw) > _SOURCE_READ_LIMIT_BYTES:
        raw = raw[:_SOURCE_READ_LIMIT_BYTES]
    text = raw.decode("utf-8", errors="replace")
    return "\n".join(
        [
            f"Fetched URL: {final_url}",
            f"Content-Type: {content_type}",
            "",
            _number_lines(text),
        ]
    )


def _file_uri_to_path(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme != "file":
        return uri
    return unquote(parsed.path)


def _number_lines(text: str) -> str:
    return "\n".join(f"line {index}: {line}" for index, line in enumerate(text.splitlines(), start=1))


def _with_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"
