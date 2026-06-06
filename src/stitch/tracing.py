from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Iterator, Mapping

import weave


PROJECT_ENV_VARS = ("STITCH_WEAVE_PROJECT", "WEAVE_PROJECT", "WANDB_PROJECT")
FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


@dataclass(slots=True)
class TraceSpan:
    name: str
    attributes: dict[str, Any]
    inputs: dict[str, Any] = field(default_factory=dict)
    call: Any | None = None
    tracer: "StitchTracer | None" = None

    @property
    def trace_url(self) -> str | None:
        if self.call is None:
            return None
        if self.tracer is None:
            return get_trace_url(self.call)
        return self.tracer.trace_url(self.call)


class StitchTracer:
    """Optional W&B Weave tracer for Stitch runs.

    Weave is imported at module load time. Initialization still happens only
    when tracing is enabled; missing configuration, auth failure, or runtime
    tracing failure degrades to a no-op so orchestration code can keep running.
    """

    def __init__(
        self,
        *,
        project: str | None = None,
        enabled: bool | None = None,
        run_id: str | None = None,
        source_id: str | None = None,
        agent: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.project = project or configured_project()
        self.enabled = tracing_enabled(enabled)
        self.run_id = run_id
        self.source_id = source_id
        self.agent = agent
        self.metadata = dict(metadata or {})
        self.initialization_error: Exception | None = None
        self.span_error: Exception | None = None
        self._initialized = False
        self._client: Any | None = None
        self._weave: Any | None = None
        self._last_call: Any | None = None

    @property
    def active(self) -> bool:
        return self.enabled and self._client is not None

    @property
    def client(self) -> Any | None:
        return self._client

    def initialize(self) -> bool:
        if self._initialized:
            return self.active

        self._initialized = True
        if not self.enabled or not self.project:
            return False

        try:
            global_attributes = self._attributes()
            try:
                self._client = weave.init(
                    self.project,
                    global_attributes=global_attributes,
                )
            except TypeError:
                self._client = weave.init(self.project)
            self._weave = weave
        except Exception as exc:  # pragma: no cover - exact SDK/auth errors vary.
            self.initialization_error = exc
            self._client = None
            self._weave = None

        return self.active

    @contextmanager
    def span(
        self,
        name: str,
        *,
        run_id: str | None = None,
        source_id: str | None = None,
        agent: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        output: Any | None = None,
    ) -> Iterator[TraceSpan]:
        attributes = self._attributes(
            run_id=run_id,
            source_id=source_id,
            agent=agent,
            metadata=metadata,
        )
        call_inputs = self._inputs(
            run_id=attributes.get("run_id"),
            source_id=attributes.get("source_id"),
            agent=attributes.get("agent"),
            inputs=inputs,
        )
        span = TraceSpan(
            name=name,
            attributes=attributes,
            inputs=call_inputs,
            tracer=self,
        )

        if not self.initialize():
            yield span
            return

        call: Any | None = None
        exc_info: tuple[
            type[BaseException],
            BaseException,
            TracebackType,
        ] | None = None
        try:
            attributes_context = self._attributes_context(attributes)
            with attributes_context:
                call = self._create_call(name, call_inputs, attributes)
                span.call = call
                self._last_call = call
                yield span
        except BaseException as exc:
            exc_info = (type(exc), exc, exc.__traceback__)
            if call is not None:
                self._finish_call(call, exception=exc)
            raise
        finally:
            if call is not None and exc_info is None:
                self._finish_call(call, output=output)

    def trace_url(self, target: Any | None = None) -> str | None:
        return get_trace_url(target, self._last_call, self._client)

    def flush(self) -> None:
        if self._client is None:
            return
        flush = getattr(self._client, "flush", None)
        if not callable(flush):
            return
        try:
            flush()
        except Exception as exc:
            self.span_error = exc

    def _attributes(
        self,
        *,
        run_id: str | None = None,
        source_id: str | None = None,
        agent: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {"app": "stitch"}
        attributes.update(self.metadata)
        if metadata:
            attributes.update(metadata)

        core_attributes = {
            "run_id": run_id or self.run_id,
            "source_id": source_id or self.source_id,
            "agent": agent or self.agent,
        }
        attributes.update({
            key: value for key, value in core_attributes.items() if value is not None
        })
        return attributes

    def _inputs(
        self,
        *,
        run_id: Any | None,
        source_id: Any | None,
        agent: Any | None,
        inputs: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        call_inputs = dict(inputs or {})
        for key, value in (
            ("run_id", run_id),
            ("source_id", source_id),
            ("agent", agent),
        ):
            if value is not None and key not in call_inputs:
                call_inputs[key] = value
        return call_inputs

    def _attributes_context(self, attributes: Mapping[str, Any]) -> Any:
        attributes_fn = getattr(self._weave, "attributes", None)
        if not callable(attributes_fn):
            return nullcontext()
        try:
            return attributes_fn(dict(attributes))
        except Exception as exc:
            self.span_error = exc
            return nullcontext()

    def _create_call(
        self,
        name: str,
        inputs: Mapping[str, Any],
        attributes: Mapping[str, Any],
    ) -> Any | None:
        if self._client is None:
            return None
        create_call = getattr(self._client, "create_call", None)
        if not callable(create_call):
            return None
        try:
            return create_call(
                op=name,
                inputs=dict(inputs),
                attributes=dict(attributes),
                display_name=name,
            )
        except TypeError:
            try:
                return create_call(op=name, inputs=dict(inputs))
            except Exception as exc:
                self.span_error = exc
                return None
        except Exception as exc:
            self.span_error = exc
            return None

    def _finish_call(
        self,
        call: Any,
        *,
        output: Any | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if self._client is None:
            return
        finish_call = getattr(self._client, "finish_call", None)
        if not callable(finish_call):
            return
        try:
            finish_call(call, output=output, exception=exception)
        except TypeError:
            try:
                finish_call(call, output=output)
            except Exception as exc:
                self.span_error = exc
        except Exception as exc:
            self.span_error = exc


def configured_project(env: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if env is None else env
    for name in PROJECT_ENV_VARS:
        value = source.get(name)
        if value:
            return value
    return None


def tracing_enabled(
    enabled: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    if enabled is not None:
        return enabled

    source = os.environ if env is None else env
    disabled = source.get("STITCH_WEAVE_DISABLED") or source.get(
        "STITCH_TRACING_DISABLED"
    )
    if disabled is not None:
        return disabled.strip().lower() not in TRUE_VALUES

    configured = source.get("STITCH_WEAVE_ENABLED") or source.get(
        "STITCH_TRACING_ENABLED"
    )
    if configured is not None:
        return configured.strip().lower() not in FALSE_VALUES

    return True


def init_tracer(
    *,
    project: str | None = None,
    enabled: bool | None = None,
    run_id: str | None = None,
    source_id: str | None = None,
    agent: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> StitchTracer:
    tracer = StitchTracer(
        project=project,
        enabled=enabled,
        run_id=run_id,
        source_id=source_id,
        agent=agent,
        metadata=metadata,
    )
    tracer.initialize()
    return tracer


@contextmanager
def trace_span(
    name: str,
    *,
    tracer: StitchTracer | None = None,
    project: str | None = None,
    enabled: bool | None = None,
    run_id: str | None = None,
    source_id: str | None = None,
    agent: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    output: Any | None = None,
) -> Iterator[TraceSpan]:
    active_tracer = tracer or StitchTracer(
        project=project,
        enabled=enabled,
        run_id=run_id,
        source_id=source_id,
        agent=agent,
    )
    with active_tracer.span(
        name,
        run_id=run_id,
        source_id=source_id,
        agent=agent,
        metadata=metadata,
        inputs=inputs,
        output=output,
    ) as span:
        yield span


def get_trace_url(*targets: Any) -> str | None:
    for target in targets:
        url = _extract_trace_url(target)
        if url is not None:
            return url
    return None


def _extract_trace_url(target: Any) -> str | None:
    if target is None:
        return None
    if isinstance(target, str):
        return target if target.startswith(("http://", "https://")) else None

    for attr in ("ui_url", "trace_url", "url", "run_url"):
        try:
            value = getattr(target, attr)
        except Exception:
            continue
        url = _resolve_url_value(value)
        if url is not None:
            return url

    for method in ("get_url", "get_trace_url"):
        try:
            candidate = getattr(target, method, None)
        except Exception:
            continue
        if callable(candidate):
            url = _resolve_url_value(candidate)
            if url is not None:
                return url

    return None


def _resolve_url_value(value: Any) -> str | None:
    try:
        if callable(value):
            value = value()
    except Exception:
        return None
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None
