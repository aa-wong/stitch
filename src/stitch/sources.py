from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import SplitResult, quote, unquote, urlsplit, urlunsplit

from .json_yaml import read_document, write_document
from .models import Source, SourceKind
from .paths import StitchPaths

SUPPORTED_FILE_EXTENSIONS = {".csv", ".md", ".txt"}
_FEED_PATH_MARKERS = {"atom", "feed", "feeds", "rss"}


class SourceRegistrationError(ValueError):
    """Raised when a source URI cannot be registered."""


def load_sources(paths: StitchPaths) -> list[Source]:
    data = read_document(paths.sources_file, default={"sources": []})
    raw_sources = data.get("sources", []) if isinstance(data, dict) else data
    if not isinstance(raw_sources, list):
        raise SourceRegistrationError("sources.yaml must contain a list of sources")
    return [Source.from_dict(item) for item in raw_sources]


def save_sources(paths: StitchPaths, sources: Iterable[Source]) -> None:
    write_document(
        paths.sources_file,
        {"sources": [source.to_dict() for source in sources]},
    )


def list_sources(paths: StitchPaths) -> list[Source]:
    return load_sources(paths)


def register_source(
    paths: StitchPaths,
    uri: str | Path,
    label: str | None = None,
    *,
    added_at: str | None = None,
) -> Source:
    source = _build_source(paths, uri, label, added_at=added_at)
    sources = load_sources(paths)

    for index, existing in enumerate(sources):
        if _same_source(existing, source):
            if label is None or existing.label == source.label:
                return existing
            updated = replace(existing, label=source.label)
            sources[index] = updated
            save_sources(paths, sources)
            return updated

    sources.append(source)
    save_sources(paths, sources)
    return source


def source_id_for_uri(uri: str) -> str:
    digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
    return f"src_{digest}"


def _build_source(
    paths: StitchPaths,
    raw_uri: str | Path,
    label: str | None,
    *,
    added_at: str | None,
) -> Source:
    kind, uri, host, path = _classify_uri(paths, raw_uri)
    source_label = _clean_label(label) or _default_label(kind, uri, host, path)
    return Source(
        id=source_id_for_uri(uri),
        uri=uri,
        label=source_label,
        kind=kind,
        added_at=added_at or _utc_timestamp(),
        host=host,
        path=path,
    )


def _classify_uri(
    paths: StitchPaths,
    raw_uri: str | Path,
) -> tuple[SourceKind, str, str | None, str | None]:
    value = str(raw_uri).strip()
    if not value:
        raise SourceRegistrationError("source URI cannot be empty")

    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        uri, host = _normalize_http_url(parsed)
        kind: SourceKind = "feed" if _is_feed_like_url(uri, host) else "url"
        return kind, uri, host, None

    if parsed.scheme == "file":
        file_path = _file_url_to_path(parsed)
    elif parsed.scheme:
        raise SourceRegistrationError(f"unsupported source scheme: {parsed.scheme}")
    else:
        file_path = Path(value)

    resolved = _resolve_local_file(paths, file_path)
    uri = _path_to_file_uri(resolved)
    return "file", uri, None, str(resolved)


def _normalize_http_url(parsed: SplitResult) -> tuple[str, str]:
    if not parsed.netloc:
        raise SourceRegistrationError("URL sources must include a host")

    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not host:
        raise SourceRegistrationError("URL sources must include a host")

    port = parsed.port
    netloc = host
    if parsed.username or parsed.password:
        raise SourceRegistrationError("URL sources cannot include credentials")
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    if port and not default_port:
        netloc = f"{host}:{port}"

    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, "")), host


def _file_url_to_path(parsed: SplitResult) -> Path:
    if parsed.netloc and parsed.netloc not in {"localhost", "127.0.0.1"}:
        raise SourceRegistrationError("file URL sources must be local")
    return Path(unquote(parsed.path))


def _resolve_local_file(paths: StitchPaths, file_path: Path) -> Path:
    candidate = file_path.expanduser()
    if not candidate.is_absolute():
        candidate = paths.root / candidate
    resolved = candidate.resolve()

    if not resolved.is_file():
        raise SourceRegistrationError(f"local source file does not exist: {resolved}")
    if resolved.suffix.lower() not in SUPPORTED_FILE_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_FILE_EXTENSIONS))
        raise SourceRegistrationError(
            "unsupported local source file type: "
            f"{resolved.suffix or '<none>'}; expected {supported}"
        )
    return resolved


def _path_to_file_uri(path: Path) -> str:
    return "file://" + quote(str(path), safe="/:")


def _is_feed_like_url(uri: str, host: str) -> bool:
    parsed = urlsplit(uri)
    path_parts = [part.lower() for part in parsed.path.split("/") if part]
    suffix = Path(parsed.path).suffix.lower()
    if host.startswith("feeds.") or "feedburner" in host:
        return True
    if suffix in {".atom", ".rss", ".xml"}:
        return True
    return any(part in _FEED_PATH_MARKERS for part in path_parts)


def _clean_label(label: str | None) -> str | None:
    if label is None:
        return None
    cleaned = label.strip()
    return cleaned or None


def _default_label(kind: SourceKind, uri: str, host: str | None, path: str | None) -> str:
    if kind == "file":
        if path is None:
            raise SourceRegistrationError("file source is missing a local path")
        return Path(path).stem

    parsed = urlsplit(uri)
    source_host = host or parsed.hostname or uri
    last_path = next((part for part in reversed(parsed.path.split("/")) if part), "")
    path_label = Path(unquote(last_path)).stem.lower()
    if kind == "feed":
        return f"{source_host} feed"
    if path_label and path_label not in _FEED_PATH_MARKERS:
        return f"{source_host} {path_label}"
    return source_host


def _same_source(left: Source, right: Source) -> bool:
    return left.uri == right.uri or left.id == right.id


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
