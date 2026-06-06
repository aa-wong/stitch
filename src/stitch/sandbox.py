from __future__ import annotations

import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from .models import Source

SandboxBackend = Literal["openshell", "direct"]


@dataclass(frozen=True)
class SandboxPlan:
    source_id: str
    backend: SandboxBackend
    policy: dict[str, Any]
    command: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def source_network_allowlist(source: Source) -> list[str]:
    if source.kind not in {"url", "feed"}:
        return []

    host = _normalize_host(source.host) if source.host else urlparse(source.uri).hostname
    if not host:
        return []
    return [host.lower()]


def build_openshell_policy(
    source: Source,
    *,
    workspace_root: Path | str | None = None,
) -> dict[str, Any]:
    read_paths = []
    if workspace_root is not None:
        read_paths.append(str(Path(workspace_root).resolve()))
    if source.kind == "file":
        read_paths.append(str(_source_file_path(source)))

    return {
        "version": "1",
        "name": f"stitch-extractor-{source.id}",
        "source_id": source.id,
        "network": {
            "default": "deny",
            "allow": [{"host": host} for host in source_network_allowlist(source)],
        },
        "filesystem": {
            "default": "deny",
            "read": read_paths,
            "write": [],
        },
        "env": {
            "inherit": [],
        },
    }


def plan_extraction_sandbox(
    source: Source,
    *,
    workspace_root: Path | str | None = None,
    openshell_bin: str | None = None,
    policy_path: Path | str | None = None,
    source_payload_path: Path | str | None = None,
    python_executable: str | None = None,
    prefer_openshell: bool = True,
) -> SandboxPlan:
    policy = build_openshell_policy(source, workspace_root=workspace_root)
    python = python_executable or sys.executable
    resolved_openshell = openshell_bin if openshell_bin is not None else shutil.which("openshell")
    resolved_policy_path = (
        Path(policy_path)
        if policy_path is not None
        else Path(".stitch") / "policies" / f"{source.id}.openshell.json"
    )
    resolved_source_payload_path = (
        Path(source_payload_path)
        if source_payload_path is not None
        else Path(".stitch") / "sandbox-inputs" / f"{source.id}.json"
    )
    child_command = [
        python,
        "-m",
        "stitch.extraction",
        "--source-json",
        str(resolved_source_payload_path),
        "--no-openshell",
    ]

    if prefer_openshell and resolved_openshell:
        return SandboxPlan(
            source_id=source.id,
            backend="openshell",
            policy=policy,
            command=[
                str(resolved_openshell),
                "sandbox",
                "create",
                "--policy",
                str(resolved_policy_path),
                "--",
                *child_command,
            ],
            reason="openshell binary available",
        )

    return SandboxPlan(
        source_id=source.id,
        backend="direct",
        policy=policy,
        command=child_command,
        reason="openshell unavailable" if prefer_openshell else "openshell disabled",
    )


def _source_file_path(source: Source) -> Path:
    if source.path:
        return Path(source.path).expanduser().resolve()

    parsed = urlparse(source.uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).expanduser().resolve()

    return Path(source.uri).expanduser().resolve()


def _normalize_host(host: str) -> str | None:
    parsed = urlparse(f"//{host}")
    return parsed.hostname or host
