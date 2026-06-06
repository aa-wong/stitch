from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from stitch.agents import ExtractorAgent, ExtractorFinalOutput
from stitch.models import Citation, ExtractedPayload, Source
from stitch.paths import StitchPaths
from stitch.tracing import StitchTracer


class FakeAgentsRunner:
    runs: list[dict[str, object]] = []

    @classmethod
    async def run(cls, starting_agent, input, *, context, max_turns, run_config):
        markdown = "# Extracted\n\nAn Agents SDK extracted signal."
        artifact = "\n".join(
            [
                f"# Extracted Document: {context.source.label}",
                "",
                "## Source Metadata",
                "",
                f"- Source ID: `{context.source.id}`",
                "",
                "## Synthetic Description",
                "",
                "This document was extracted by an OpenAI Agents SDK subagent.",
                "",
                "## Normalized Markdown Payload",
                "",
                markdown,
                "",
                "## Citation Segments",
                "",
                f"- `{context.source.id}:1` line 1: An Agents SDK extracted signal.",
                "",
            ]
        )
        context.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        context.artifact_path.write_text(artifact, encoding="utf-8")

        payload = ExtractedPayload(
            source=context.source,
            markdown=markdown,
            citations=[
                Citation(
                    id=f"{context.source.id}:1",
                    source_id=context.source.id,
                    label=context.source.label,
                    uri=context.source.uri,
                    locator="line 1",
                    excerpt="An Agents SDK extracted signal.",
                )
            ],
            metadata={"extractor": "openai-agents-sdk", "artifact_kind": "synthetic-document-markdown"},
        )
        context.payload_path.parent.mkdir(parents=True, exist_ok=True)
        context.payload_path.write_text(json.dumps(payload.to_dict()), encoding="utf-8")
        cls.runs.append(
            {
                "agent_name": starting_agent.name,
                "input": input,
                "context": context,
                "max_turns": max_turns,
                "run_config": run_config,
                "tool_names": [tool.name for tool in starting_agent.tools],
                "output_type": starting_agent.output_type,
            }
        )
        return SimpleNamespace(
            final_output=ExtractorFinalOutput(
                artifact_path=str(context.artifact_path),
                payload_path=str(context.payload_path),
            ),
            last_response_id=f"resp-{context.source.id}",
        )


def _source() -> Source:
    return Source(
        id="src_demo",
        uri="file:///demo.txt",
        label="Demo",
        kind="file",
        added_at="2026-06-06T00:00:00Z",
        path="/demo.txt",
    )


def test_agents_extractor_agent_writes_artifact_and_payload(tmp_path, monkeypatch):
    source = _source()
    FakeAgentsRunner.runs = []
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    agent = ExtractorAgent(
        source=source,
        paths=StitchPaths(tmp_path),
        run_id="run1",
        model="gpt-5.4",
        tracer=StitchTracer(enabled=False),
        progress_fn=lambda message: None,
        runner=FakeAgentsRunner,
    )

    payload = agent.run()

    assert payload.source == source
    assert payload.metadata["extractor"] == "openai-agents-sdk"
    assert payload.metadata["agents_last_response_id"] == "resp-src_demo"
    artifact_path = tmp_path / ".stitch" / "extracted" / "run1" / "src_demo.md"
    assert artifact_path.exists()
    assert "## Synthetic Description" in artifact_path.read_text(encoding="utf-8")
    assert len(FakeAgentsRunner.runs) == 1
    run = FakeAgentsRunner.runs[0]
    assert run["max_turns"] == 8
    assert run["output_type"] is ExtractorFinalOutput
    assert run["tool_names"] == [
        "read_assigned_source",
        "write_markdown_artifact",
        "write_payload_json",
    ]


def test_agents_extractor_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent = ExtractorAgent(
        source=_source(),
        paths=StitchPaths(tmp_path),
        run_id="run1",
        model="gpt-5.4",
        tracer=StitchTracer(enabled=False),
        runner=FakeAgentsRunner,
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is required"):
        agent.run()
