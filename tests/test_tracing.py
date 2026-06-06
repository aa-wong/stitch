from __future__ import annotations

from types import SimpleNamespace

import pytest

from stitch.tracing import (
    StitchTracer,
    configured_project,
    get_trace_url,
    trace_span,
    tracing_enabled,
)


class FakeAttributesContext:
    def __init__(self, weave: "FakeWeave", attributes: dict[str, object]) -> None:
        self.weave = weave
        self.attributes = attributes

    def __enter__(self) -> None:
        self.weave.entered_attributes.append(self.attributes)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.weave.exited_attributes.append((exc_type, exc))


class FakeClient:
    def __init__(self) -> None:
        self.ui_url = "https://wandb.ai/team/project/weave"
        self.created_calls: list[dict[str, object]] = []
        self.finished_calls: list[dict[str, object]] = []
        self.flushed = False

    def create_call(
        self,
        *,
        op: str,
        inputs: dict[str, object],
        attributes: dict[str, object],
        display_name: str,
    ) -> object:
        call = SimpleNamespace(
            op=op,
            inputs=inputs,
            attributes=attributes,
            display_name=display_name,
            ui_url=f"https://wandb.ai/team/project/weave/calls/{len(self.created_calls) + 1}",
        )
        self.created_calls.append(
            {
                "op": op,
                "inputs": inputs,
                "attributes": attributes,
                "display_name": display_name,
                "call": call,
            }
        )
        return call

    def finish_call(
        self,
        call: object,
        output: object | None = None,
        exception: BaseException | None = None,
    ) -> None:
        self.finished_calls.append(
            {"call": call, "output": output, "exception": exception}
        )

    def flush(self) -> None:
        self.flushed = True


class FakeWeave:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.init_calls: list[dict[str, object]] = []
        self.entered_attributes: list[dict[str, object]] = []
        self.exited_attributes: list[tuple[object, object]] = []

    def init(self, project: str, **kwargs: object) -> FakeClient:
        self.init_calls.append({"project": project, **kwargs})
        return self.client

    def attributes(self, attributes: dict[str, object]) -> FakeAttributesContext:
        return FakeAttributesContext(self, attributes)


class BrokenUrlShape:
    @property
    def ui_url(self) -> str:
        raise RuntimeError("not available")

    def get_url(self) -> str:
        raise RuntimeError("not available")


def test_configured_project_uses_first_stitch_specific_env_var() -> None:
    env = {
        "WANDB_PROJECT": "wandb-project",
        "WEAVE_PROJECT": "weave-project",
        "STITCH_WEAVE_PROJECT": "stitch-project",
    }

    assert configured_project(env) == "stitch-project"


def test_tracing_enabled_respects_disable_flags() -> None:
    assert tracing_enabled(env={}) is True
    assert tracing_enabled(env={"STITCH_WEAVE_ENABLED": "false"}) is False
    assert tracing_enabled(env={"STITCH_WEAVE_DISABLED": "true"}) is False
    assert tracing_enabled(enabled=True, env={"STITCH_WEAVE_DISABLED": "true"}) is True


class BrokenWeave:
    def init(self, project: str, **kwargs: object) -> object:
        raise RuntimeError("auth failed")


def test_weave_initialization_failure_is_noop_even_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("stitch.tracing.weave", BrokenWeave())

    tracer = StitchTracer(project="team/project", enabled=True, run_id="run-1")

    assert tracer.initialize() is False
    assert tracer.active is False
    assert tracer.initialization_error is not None

    with tracer.span("extract", source_id="source-1", agent="extractor") as span:
        assert span.call is None
        assert span.attributes == {
            "app": "stitch",
            "run_id": "run-1",
            "source_id": "source-1",
            "agent": "extractor",
        }
        assert span.trace_url is None

    assert tracer.trace_url() is None


def test_unconfigured_tracer_is_noop_without_initializing_weave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_weave = FakeWeave()
    monkeypatch.setattr("stitch.tracing.weave", fake_weave)

    tracer = StitchTracer(enabled=True)

    assert tracer.initialize() is False
    assert fake_weave.init_calls == []

    with tracer.span("profile", run_id="run-1") as span:
        assert span.call is None
        assert span.inputs == {"run_id": "run-1"}


def test_successful_weave_span_carries_run_source_agent_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_weave = FakeWeave()
    monkeypatch.setattr("stitch.tracing.weave", fake_weave)
    tracer = StitchTracer(
        project="team/project",
        enabled=True,
        run_id="run-1",
        metadata={"environment": "test"},
    )

    with tracer.span(
        "extract_source",
        source_id="source-1",
        agent="extractor",
        metadata={"sandbox": "local"},
        inputs={"uri": "https://example.com"},
        output={"ok": True},
    ) as span:
        assert tracer.active is True
        assert span.call is not None
        assert span.trace_url == "https://wandb.ai/team/project/weave/calls/1"

    assert fake_weave.init_calls == [
        {
            "project": "team/project",
            "global_attributes": {
                "app": "stitch",
                "environment": "test",
                "run_id": "run-1",
            },
        }
    ]
    assert fake_weave.entered_attributes == [
        {
            "app": "stitch",
            "environment": "test",
            "sandbox": "local",
            "run_id": "run-1",
            "source_id": "source-1",
            "agent": "extractor",
        }
    ]

    created = fake_weave.client.created_calls[0]
    assert created["op"] == "extract_source"
    assert created["display_name"] == "extract_source"
    assert created["inputs"] == {
        "uri": "https://example.com",
        "run_id": "run-1",
        "source_id": "source-1",
        "agent": "extractor",
    }
    assert created["attributes"] == fake_weave.entered_attributes[0]
    assert fake_weave.client.finished_calls == [
        {"call": created["call"], "output": {"ok": True}, "exception": None}
    ]
    assert tracer.trace_url() == "https://wandb.ai/team/project/weave/calls/1"


def test_span_finishes_with_exception_without_swallowing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_weave = FakeWeave()
    monkeypatch.setattr("stitch.tracing.weave", fake_weave)
    tracer = StitchTracer(project="team/project", enabled=True)

    with pytest.raises(RuntimeError, match="boom"):
        with tracer.span("build", run_id="run-1"):
            raise RuntimeError("boom")

    created = fake_weave.client.created_calls[0]
    assert len(fake_weave.client.finished_calls) == 1
    finished = fake_weave.client.finished_calls[0]
    assert finished["call"] is created["call"]
    assert finished["output"] is None
    assert isinstance(finished["exception"], RuntimeError)


def test_trace_span_helper_accepts_existing_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_weave = FakeWeave()
    monkeypatch.setattr("stitch.tracing.weave", fake_weave)
    tracer = StitchTracer(project="team/project", enabled=True)

    with trace_span("plan", tracer=tracer, run_id="run-1") as span:
        assert span.call is not None

    assert fake_weave.client.created_calls[0]["attributes"]["run_id"] == "run-1"


def test_get_trace_url_probes_common_weave_and_wandb_shapes() -> None:
    assert get_trace_url(SimpleNamespace(ui_url="https://example.com/weave")) == (
        "https://example.com/weave"
    )
    assert get_trace_url(SimpleNamespace(get_url=lambda: "https://example.com/run")) == (
        "https://example.com/run"
    )
    assert get_trace_url(SimpleNamespace(url="not-a-url")) is None
    assert get_trace_url(BrokenUrlShape()) is None
    assert get_trace_url(None, "https://example.com/direct") == (
        "https://example.com/direct"
    )
