from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .json_yaml import read_document
from .paths import StitchPaths


@dataclass(frozen=True)
class StoredPipeline:
    name: str
    directory: Path
    yaml: dict[str, Any]
    markdown: str


def list_pipelines(paths: StitchPaths) -> list[str]:
    if not paths.pipelines_dir.exists():
        return []
    return sorted(path.name for path in paths.pipelines_dir.iterdir() if path.is_dir())


def load_pipeline(paths: StitchPaths, name: str) -> StoredPipeline:
    directory = paths.pipeline_dir(name)
    if not directory.exists():
        raise FileNotFoundError(f"Unknown pipeline: {name}")
    yaml_path = directory / "pipeline.yaml"
    markdown_path = directory / "pipeline.md"
    return StoredPipeline(
        name=name,
        directory=directory,
        yaml=read_document(yaml_path, {}),
        markdown=markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else "",
    )


def latest_report_path(paths: StitchPaths) -> Path | None:
    candidates = sorted(paths.pipelines_dir.glob("*/latest-report.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None
