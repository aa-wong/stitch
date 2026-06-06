from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StitchPaths:
    root: Path

    @property
    def stitch_dir(self) -> Path:
        return self.root / ".stitch"

    @property
    def config_file(self) -> Path:
        return self.stitch_dir / "config.yaml"

    @property
    def sources_file(self) -> Path:
        return self.stitch_dir / "sources.yaml"

    @property
    def payloads_dir(self) -> Path:
        return self.stitch_dir / "payloads"

    @property
    def questions_file(self) -> Path:
        return self.stitch_dir / "questions.yaml"

    @property
    def pipelines_dir(self) -> Path:
        return self.root / "pipelines"

    def pipeline_dir(self, name: str) -> Path:
        return self.pipelines_dir / name

    @classmethod
    def discover(cls, start: Path | None = None) -> "StitchPaths":
        return cls(root=(start or Path.cwd()).resolve())
