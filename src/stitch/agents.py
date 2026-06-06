from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openai_codex import Codex, Sandbox

from .json_yaml import write_document
from .models import ExtractedPayload, Source
from .paths import StitchPaths
from .tracing import StitchTracer

CodexFactory = Callable[[], Any]

EXTRACTOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["artifact_path", "payload_path"],
    "properties": {
        "artifact_path": {"type": "string"},
        "payload_path": {"type": "string"},
    },
}


@dataclass(frozen=True)
class ExtractorAgent:
    """Codex SDK-backed per-source extractor subagent."""

    source: Source
    paths: StitchPaths
    run_id: str
    model: str
    tracer: StitchTracer
    codex_factory: CodexFactory = Codex

    def run(self) -> ExtractedPayload:
        self._require_openai_auth()
        source_input_path = self._source_input_path()
        artifact_path = self._artifact_path()
        payload_path = self._payload_path()
        source_input_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        write_document(source_input_path, self.source.to_dict())

        prompt = self._prompt(source_input_path, artifact_path, payload_path)
        with self.tracer.span(
            "codex_extractor_agent",
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
            with self.codex_factory() as codex:
                with self.tracer.span(
                    "codex_thread_start",
                    run_id=self.run_id,
                    source_id=self.source.id,
                    agent="extractor",
                    inputs={"model": self.model, "sandbox": Sandbox.workspace_write.value},
                ):
                    thread = codex.thread_start(
                        model=self.model,
                        sandbox=Sandbox.workspace_write,
                        cwd=str(self.paths.root),
                    )
                with self.tracer.span(
                    "codex_thread_run",
                    run_id=self.run_id,
                    source_id=self.source.id,
                    agent="extractor",
                    inputs={"thread_id": getattr(thread, "id", None)},
                ):
                    result = thread.run(
                        prompt,
                        sandbox=Sandbox.workspace_write,
                        output_schema=EXTRACTOR_OUTPUT_SCHEMA,
                    )
                payload = self._load_payload(payload_path, result, thread)
        return payload

    def _require_openai_auth(self) -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required: Stitch extraction uses Codex SDK subagents.")

    def _source_input_path(self) -> Path:
        return self.paths.stitch_dir / "agent-inputs" / self.run_id / f"{self.source.id}.json"

    def _artifact_path(self) -> Path:
        return self.paths.stitch_dir / "extracted" / self.run_id / f"{self.source.id}.md"

    def _payload_path(self) -> Path:
        return self.paths.stitch_dir / "agent-payloads" / self.run_id / f"{self.source.id}.json"

    def _load_payload(self, payload_path: Path, result: Any, thread: Any) -> ExtractedPayload:
        _validate_final_response(result.final_response, payload_path)
        if not payload_path.exists():
            raise RuntimeError(f"Codex extractor did not write payload file: {payload_path}")
        payload = ExtractedPayload.from_dict(json.loads(payload_path.read_text(encoding="utf-8")))
        artifact_path = self._artifact_path()
        if not artifact_path.exists():
            raise RuntimeError(f"Codex extractor did not write markdown artifact: {artifact_path}")
        metadata = {
            **payload.metadata,
            "extractor": "codex-sdk",
            "codex_thread_id": getattr(thread, "id", None),
            "codex_turn_id": getattr(result, "id", None),
            "codex_status": str(getattr(result, "status", "")),
            "artifact_path": str(artifact_path),
            "payload_path": str(payload_path),
        }
        return ExtractedPayload(
            source=payload.source,
            markdown=payload.markdown,
            citations=payload.citations,
            metadata=metadata,
        )

    def _prompt(self, source_input_path: Path, artifact_path: Path, payload_path: Path) -> str:
        return f"""You are a Stitch extractor subagent. Extract exactly one source/document.

Read the source descriptor JSON at:
{source_input_path}

You must inspect only that source. If it is a local file, read that file. If it is a URL/feed,
fetch only the assigned URI/host from the descriptor.

Write two files:

1. Markdown artifact at:
{artifact_path}

The markdown artifact must contain these sections:
- # Extracted Document: <source label>
- ## Source Metadata
- ## Synthetic Description
- ## Normalized Markdown Payload
- ## Citation Segments

The synthetic description must be prose describing what the document is, its structure,
and the useful signals it appears to contain. The normalized markdown payload must be the
canonical extracted text/table/feed representation downstream agents should use.

2. JSON payload at:
{payload_path}

The JSON payload must match this shape:
{{
  "source": <the exact source descriptor object>,
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
    "extractor": "codex-sdk",
    "artifact_kind": "synthetic-document-markdown"
  }}
}}

Return final JSON only:
{{"artifact_path": "{artifact_path}", "payload_path": "{payload_path}"}}
"""


def _validate_final_response(final_response: str | None, expected_payload_path: Path) -> None:
    if final_response is None:
        raise RuntimeError("Codex extractor returned no final response.")
    try:
        data = json.loads(final_response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Codex extractor final response was not JSON: {final_response}") from exc
    if data.get("payload_path") != str(expected_payload_path):
        raise RuntimeError("Codex extractor final response did not reference the expected payload path.")
