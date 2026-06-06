from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SourceKind = Literal["url", "file", "feed"]


@dataclass(frozen=True)
class Source:
    id: str
    uri: str
    label: str
    kind: SourceKind
    added_at: str
    host: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        return cls(
            id=str(data["id"]),
            uri=str(data["uri"]),
            label=str(data["label"]),
            kind=data["kind"],
            added_at=str(data["added_at"]),
            host=data.get("host"),
            path=data.get("path"),
        )


@dataclass(frozen=True)
class Citation:
    id: str
    source_id: str
    label: str
    uri: str
    locator: str
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Citation":
        return cls(
            id=str(data["id"]),
            source_id=str(data["source_id"]),
            label=str(data["label"]),
            uri=str(data["uri"]),
            locator=str(data["locator"]),
            excerpt=str(data["excerpt"]),
        )


@dataclass(frozen=True)
class ExtractedPayload:
    source: Source
    markdown: str
    citations: list[Citation]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "markdown": self.markdown,
            "citations": [citation.to_dict() for citation in self.citations],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractedPayload":
        return cls(
            source=Source.from_dict(data["source"]),
            markdown=str(data["markdown"]),
            citations=[Citation.from_dict(item) for item in data.get("citations", [])],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ProfileFinding:
    source_id: str
    label: str
    summary: str
    fields: list[str]
    entities: list[str]
    overlaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileFinding":
        return cls(
            source_id=str(data["source_id"]),
            label=str(data["label"]),
            summary=str(data["summary"]),
            fields=[str(item) for item in data.get("fields", [])],
            entities=[str(item) for item in data.get("entities", [])],
            overlaps=[str(item) for item in data.get("overlaps", [])],
        )


@dataclass(frozen=True)
class HitlQuestion:
    id: str
    run_id: str
    agent: str
    question: str
    options: list[str]
    context: dict[str, Any]
    created_at: str
    status: Literal["pending", "answered"] = "pending"
    answer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HitlQuestion":
        return cls(
            id=str(data["id"]),
            run_id=str(data["run_id"]),
            agent=str(data["agent"]),
            question=str(data["question"]),
            options=[str(item) for item in data.get("options", [])],
            context=dict(data.get("context", {})),
            created_at=str(data["created_at"]),
            status=data.get("status", "pending"),
            answer=data.get("answer"),
        )
