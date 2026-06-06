from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stitch.agents import ExtractorAgent
from stitch.models import Citation, ExtractedPayload, Source
from stitch.paths import StitchPaths
from stitch.tracing import StitchTracer


class FakeThread:
    def __init__(self, source: Source) -> None:
        self.id = f"thread-{source.id}"
        self.source = source
        self.runs: list[dict[str, object]] = []

    def run(self, prompt, *, sandbox, output_schema):
        artifact_path, payload_path = _paths_from_prompt(prompt)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = "# Extracted\n\nA Codex extracted signal."
        artifact_path.write_text(
            "\n".join(
                [
                    f"# Extracted Document: {self.source.label}",
                    "",
                    "## Source Metadata",
                    "",
                    f"- Source ID: `{self.source.id}`",
                    "",
                    "## Synthetic Description",
                    "",
                    "This document was extracted by a Codex subagent.",
                    "",
                    "## Normalized Markdown Payload",
                    "",
                    markdown,
                    "",
                    "## Citation Segments",
                    "",
                    f"- `{self.source.id}:1` line 1: A Codex extracted signal.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        payload = ExtractedPayload(
            source=self.source,
            markdown=markdown,
            citations=[
                Citation(
                    id=f"{self.source.id}:1",
                    source_id=self.source.id,
                    label=self.source.label,
                    uri=self.source.uri,
                    locator="line 1",
                    excerpt="A Codex extracted signal.",
                )
            ],
            metadata={"extractor": "codex-sdk", "artifact_kind": "synthetic-document-markdown"},
        )
        payload_path.write_text(json.dumps(payload.to_dict()), encoding="utf-8")
        self.runs.append({"prompt": prompt, "sandbox": sandbox, "output_schema": output_schema})
        return SimpleNamespace(
            id=f"turn-{self.source.id}",
            status="completed",
            final_response=json.dumps(
                {"artifact_path": str(artifact_path), "payload_path": str(payload_path)}
            ),
        )


class FakeCodex:
    created_threads: list[FakeThread] = []
    source: Source | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def thread_start(self, *, model, sandbox, cwd):
        assert model == "gpt-5.4"
        assert cwd
        assert self.source is not None
        thread = FakeThread(self.source)
        self.created_threads.append(thread)
        return thread


def _source() -> Source:
    return Source(
        id="src_demo",
        uri="file:///demo.txt",
        label="Demo",
        kind="file",
        added_at="2026-06-06T00:00:00Z",
        path="/demo.txt",
    )


def _paths_from_prompt(prompt: str):
    artifact_marker = "1. Markdown artifact at:\n"
    payload_marker = "2. JSON payload at:\n"
    artifact_path = prompt.split(artifact_marker, 1)[1].split("\n", 1)[0].strip()
    payload_path = prompt.split(payload_marker, 1)[1].split("\n", 1)[0].strip()
    return Path(artifact_path), Path(payload_path)


def test_codex_extractor_agent_writes_artifact_and_payload(tmp_path, monkeypatch):
    source = _source()
    FakeCodex.created_threads = []
    FakeCodex.source = source
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    agent = ExtractorAgent(
        source=source,
        paths=StitchPaths(tmp_path),
        run_id="run1",
        model="gpt-5.4",
        tracer=StitchTracer(enabled=False),
        codex_factory=FakeCodex,
    )

    payload = agent.run()

    assert payload.source == source
    assert payload.metadata["extractor"] == "codex-sdk"
    assert payload.metadata["codex_thread_id"] == "thread-src_demo"
    artifact_path = tmp_path / ".stitch" / "extracted" / "run1" / "src_demo.md"
    assert artifact_path.exists()
    assert "## Synthetic Description" in artifact_path.read_text(encoding="utf-8")
    assert len(FakeCodex.created_threads) == 1
    assert FakeCodex.created_threads[0].runs[0]["output_schema"]["required"] == [
        "artifact_path",
        "payload_path",
    ]


def test_codex_extractor_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent = ExtractorAgent(
        source=_source(),
        paths=StitchPaths(tmp_path),
        run_id="run1",
        model="gpt-5.4",
        tracer=StitchTracer(enabled=False),
        codex_factory=FakeCodex,
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is required"):
        agent.run()
