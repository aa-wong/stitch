from __future__ import annotations

import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .json_yaml import read_document, write_document
from .paths import StitchPaths


@dataclass(frozen=True)
class StitchConfig:
    redis_url: str | None = None
    weave_project: str | None = "stitch"
    slack_webhook_url: str | None = None
    openai_model: str = "gpt-5.4"
    openai_api_key: str | None = None
    agent_timeout_seconds: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "redis_url": self.redis_url,
            "weave_project": self.weave_project,
            "slack_webhook_url": self.slack_webhook_url,
            "openai_model": self.openai_model,
            "agent_timeout_seconds": self.agent_timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StitchConfig":
        return cls(
            redis_url=data.get("redis_url") or os.getenv("STITCH_REDIS_URL") or os.getenv("REDIS_URL"),
            weave_project=data.get("weave_project") or os.getenv("STITCH_WEAVE_PROJECT") or "stitch",
            slack_webhook_url=data.get("slack_webhook_url") or os.getenv("STITCH_SLACK_WEBHOOK_URL"),
            openai_model=data.get("openai_model") or os.getenv("STITCH_OPENAI_MODEL") or "gpt-5.4",
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            agent_timeout_seconds=_int_env(
                data.get("agent_timeout_seconds") or os.getenv("STITCH_AGENT_TIMEOUT_SECONDS"),
                default=300,
            ),
        )


def init_project(paths: StitchPaths, redis_url: str | None = None, weave_project: str = "stitch") -> StitchConfig:
    load_dotenv(paths)
    paths.stitch_dir.mkdir(parents=True, exist_ok=True)
    paths.payloads_dir.mkdir(parents=True, exist_ok=True)
    paths.pipelines_dir.mkdir(parents=True, exist_ok=True)
    config = StitchConfig(
        redis_url=redis_url or os.getenv("STITCH_REDIS_URL") or os.getenv("REDIS_URL"),
        weave_project=weave_project,
        slack_webhook_url=os.getenv("STITCH_SLACK_WEBHOOK_URL"),
        openai_model=os.getenv("STITCH_OPENAI_MODEL", "gpt-5.4"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        agent_timeout_seconds=_int_env(os.getenv("STITCH_AGENT_TIMEOUT_SECONDS"), default=300),
    )
    if not paths.config_file.exists():
        write_document(paths.config_file, config.to_dict())
    if not paths.sources_file.exists():
        write_document(paths.sources_file, {"sources": []})
    if not paths.questions_file.exists():
        write_document(paths.questions_file, {"questions": []})
    return load_config(paths)


def load_config(paths: StitchPaths) -> StitchConfig:
    load_dotenv(paths)
    data = read_document(paths.config_file, {})
    return StitchConfig.from_dict(data)


def load_dotenv(paths: StitchPaths, environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    env_file = paths.root / ".env"
    values = parse_env_file(env_file)
    target = os.environ if environ is None else environ
    for key, value in values.items():
        target.setdefault(key, value)
    return values


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _clean_env_value(value.strip())
    return values


def _clean_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _int_env(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected integer environment value, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"Expected positive integer environment value, got {value!r}")
    return parsed
