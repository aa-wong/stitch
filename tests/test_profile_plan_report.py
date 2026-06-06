from __future__ import annotations

from datetime import UTC, datetime

from stitch.bus import LocalBus
from stitch.hitl import HitlManager
from stitch.models import Citation, ExtractedPayload, Source
from stitch.paths import StitchPaths
from stitch.planning import create_pipeline_plan
from stitch.profiling import profile_payloads
from stitch.reporting import render_report


def _payload(source_id: str, label: str, markdown: str) -> ExtractedPayload:
    source = Source(
        id=source_id,
        uri=f"file:///{source_id}.txt",
        label=label,
        kind="file",
        added_at=datetime.now(UTC).isoformat(),
        path=f"/{source_id}.txt",
    )
    return ExtractedPayload(
        source=source,
        markdown=markdown,
        citations=[Citation(f"c_{source_id}_1", source_id, label, source.uri, "line 1", markdown.splitlines()[0])],
    )


def test_profile_writes_findings_to_blackboard(tmp_path):
    paths = StitchPaths(tmp_path)
    bus = LocalBus(paths)
    payloads = [_payload("a", "A", "| company | price |\n| A | $10 USD |")]

    findings = profile_payloads(payloads, bus, "run1")

    blackboard = bus.read_blackboard("run1")
    assert findings[0].fields == ["company", "price"]
    assert "profile:a" in blackboard
    assert blackboard["profiles"][0]["source_id"] == "a"


def test_planner_blocks_on_currency_ambiguity_then_records_answer(tmp_path):
    paths = StitchPaths(tmp_path)
    bus = LocalBus(paths)
    hitl = HitlManager(bus, input_fn=lambda _: "USD", output_fn=lambda _: None)
    payloads = [
        _payload("a", "A", "Plan costs $10 USD per seat."),
        _payload("b", "B", "Plan costs 8 EUR per seat."),
    ]
    findings = profile_payloads(payloads, bus, "run2")

    plan = create_pipeline_plan(run_id="run2", goal="pricing", payloads=payloads, findings=findings, hitl=hitl)
    assert plan.blocked

    hitl.publish(plan.questions[0])
    hitl.prompt_pending("run2")
    resumed = create_pipeline_plan(run_id="run2", goal="pricing", payloads=payloads, findings=findings, hitl=hitl)
    assert not resumed.blocked
    assert resumed.pipeline_yaml["human_answers"][0]["answer"] == "USD"


def test_report_contains_per_source_citations(tmp_path):
    paths = StitchPaths(tmp_path)
    bus = LocalBus(paths)
    payloads = [_payload("a", "A", "A has a $10 USD starter plan.")]
    findings = profile_payloads(payloads, bus, "run3")
    plan = create_pipeline_plan(
        run_id="run3",
        goal="pricing",
        payloads=payloads,
        findings=findings,
        hitl=HitlManager(bus),
    )

    report = render_report(goal="pricing", payloads=payloads, findings=findings, plan=plan)

    assert "[^c_a_1]" in report
    assert "## Citations" in report
