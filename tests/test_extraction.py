from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from stitch.extraction import FetchedDocument, extract_source, extract_sources_parallel
from stitch.models import Citation, ExtractedPayload
from stitch.models import Source
from stitch.sandbox import build_openshell_policy, plan_extraction_sandbox


def _source(source_id: str, uri: str, kind: str, **kwargs: str) -> Source:
    return Source(
        id=source_id,
        uri=uri,
        label=kwargs.pop("label", source_id),
        kind=kind,  # type: ignore[arg-type]
        added_at="2026-06-06T00:00:00Z",
        host=kwargs.pop("host", None),
        path=kwargs.pop("path", None),
    )


def test_extracts_local_markdown_text_and_csv(tmp_path) -> None:
    md_path = tmp_path / "note.md"
    txt_path = tmp_path / "note.txt"
    csv_path = tmp_path / "prices.csv"
    md_path.write_text("# Roadmap\n\nLaunch signal", encoding="utf-8")
    txt_path.write_text("plain signal\nsecond line", encoding="utf-8")
    csv_path.write_text("company,price\nAlpha,10\nBeta,12\n", encoding="utf-8")

    payloads = extract_sources_parallel(
        [
            _source("md", str(md_path), "file", path=str(md_path), label="Markdown"),
            _source("txt", str(txt_path), "file", path=str(txt_path), label="Text"),
            _source("csv", str(csv_path), "file", path=str(csv_path), label="CSV"),
        ],
        prefer_openshell=False,
    )

    by_id = {payload.source.id: payload for payload in payloads}
    assert "Launch signal" in by_id["md"].markdown
    assert "plain signal" in by_id["txt"].markdown
    assert "| company | price |" in by_id["csv"].markdown
    assert "| Alpha | 10 |" in by_id["csv"].markdown
    assert by_id["csv"].citations[0].locator == "row 2"
    assert all(citation.source_id == payload.source.id for payload in payloads for citation in payload.citations)
    assert by_id["md"].metadata["sandbox"]["backend"] == "direct"


def test_extracts_url_and_feed_with_monkeypatched_fetcher() -> None:
    responses = {
        "https://example.com/page": FetchedDocument(
            "<html><body><h1>Pricing</h1><script>ignore()</script><p>Alpha costs $10.</p></body></html>",
            content_type="text/html",
            final_url="https://example.com/page",
        ),
        "https://example.com/feed.xml": FetchedDocument(
            """
            <rss>
              <channel>
                <item>
                  <title>Launch</title>
                  <link>https://example.com/launch</link>
                  <description><![CDATA[<p>New product tier.</p>]]></description>
                </item>
              </channel>
            </rss>
            """,
            content_type="application/rss+xml",
        ),
    }

    def fake_fetch(uri: str) -> FetchedDocument:
        return responses[uri]

    url_payload = extract_source(
        _source("url", "https://example.com/page", "url", host="example.com"),
        fetcher=fake_fetch,
        prefer_openshell=False,
    )
    feed_payload = extract_source(
        _source("feed", "https://example.com/feed.xml", "feed", host="example.com"),
        fetcher=fake_fetch,
        prefer_openshell=False,
    )

    assert "Pricing" in url_payload.markdown
    assert "Alpha costs $10." in url_payload.markdown
    assert "ignore" not in url_payload.markdown
    assert url_payload.citations[0].uri == "https://example.com/page"
    assert "## Launch" in feed_payload.markdown
    assert "New product tier." in feed_payload.markdown
    assert feed_payload.citations[0].locator == "item 1"


def test_parallel_extraction_uses_one_worker_per_source() -> None:
    barrier = threading.Barrier(2, timeout=2)
    seen_threads: set[str] = set()

    def fake_fetch(uri: str) -> str:
        seen_threads.add(threading.current_thread().name)
        barrier.wait()
        return f"payload for {uri}"

    payloads = extract_sources_parallel(
        [
            _source("a", "https://a.example/doc.txt", "url", host="a.example"),
            _source("b", "https://b.example/doc.txt", "url", host="b.example"),
        ],
        fetcher=fake_fetch,
        prefer_openshell=False,
    )

    assert [payload.source.id for payload in payloads] == ["a", "b"]
    assert len(seen_threads) == 2


def test_sandbox_policy_allowlists_only_source_host_and_falls_back_to_direct(monkeypatch) -> None:
    source = _source("web", "https://Example.com/path", "url")
    monkeypatch.setattr("stitch.sandbox.shutil.which", lambda _: None)

    policy = build_openshell_policy(source)
    plan = plan_extraction_sandbox(source)

    assert policy["network"]["default"] == "deny"
    assert policy["network"]["allow"] == [{"host": "example.com"}]
    assert plan.backend == "direct"
    assert plan.policy["network"]["allow"] == [{"host": "example.com"}]
    assert plan.command[:2] != ["openshell", "sandbox"]


def test_extract_source_executes_through_openshell_when_available(tmp_path, monkeypatch) -> None:
    source_file = tmp_path / "source.txt"
    source_file.write_text("sandboxed signal", encoding="utf-8")
    source = _source("local", str(source_file), "file", path=str(source_file))
    child_payload = ExtractedPayload(
        source=source,
        markdown="sandboxed signal",
        citations=[
            Citation(
                id="local:1",
                source_id="local",
                label="local",
                uri=source.uri,
                locator="line 1",
                excerpt="sandboxed signal",
            )
        ],
        metadata={"extractor": "child"},
    )

    monkeypatch.setattr("stitch.sandbox.shutil.which", lambda _: "/usr/local/bin/openshell")

    def fake_run(command, cwd, env, text, stdout, stderr, timeout, check):
        policy_path = tmp_path / ".stitch" / "policies" / "local.openshell.json"
        source_payload_path = tmp_path / ".stitch" / "sandbox-inputs" / "local.json"
        assert command[:5] == [
            "/usr/local/bin/openshell",
            "sandbox",
            "create",
            "--policy",
            str(policy_path),
        ]
        assert command[-3:] == ["--source-json", str(source_payload_path), "--no-openshell"]
        assert json.loads(policy_path.read_text(encoding="utf-8"))["filesystem"]["default"] == "deny"
        assert json.loads(source_payload_path.read_text(encoding="utf-8"))["id"] == "local"
        assert env["STITCH_EXTRACTOR_CHILD"] == "1"
        return SimpleNamespace(returncode=0, stdout=json.dumps(child_payload.to_dict()), stderr="")

    monkeypatch.setattr("stitch.extraction.subprocess.run", fake_run)

    payload = extract_source(source, workspace_root=tmp_path, prefer_openshell=True)

    assert payload.markdown == "sandboxed signal"
    assert payload.metadata["extractor"] == "child"
    assert payload.metadata["sandbox"]["backend"] == "openshell"


def test_extract_source_fails_when_openshell_has_no_gateway(tmp_path, monkeypatch) -> None:
    source_file = tmp_path / "source.txt"
    source_file.write_text("must fail when OpenShell is unavailable", encoding="utf-8")
    source = _source("local", str(source_file), "file", path=str(source_file))

    monkeypatch.setattr("stitch.sandbox.shutil.which", lambda _: "/usr/local/bin/openshell")
    monkeypatch.setattr(
        "stitch.extraction.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error: No active gateway.",
        ),
    )

    with pytest.raises(RuntimeError, match="OpenShell extractor failed"):
        extract_source(source, workspace_root=tmp_path, prefer_openshell=True)
