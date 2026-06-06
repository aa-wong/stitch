from __future__ import annotations

from pathlib import Path

import pytest

from stitch.models import Source
from stitch.paths import StitchPaths
from stitch.sources import (
    SourceRegistrationError,
    list_sources,
    load_sources,
    register_source,
    save_sources,
    source_id_for_uri,
)


def test_register_url_normalizes_uri_and_handles_duplicates(tmp_path: Path) -> None:
    paths = StitchPaths(tmp_path)

    source = register_source(
        paths,
        "HTTPS://Example.com:443/pricing/#details",
        added_at="2026-06-06T12:00:00Z",
    )
    duplicate = register_source(
        paths,
        "https://example.com/pricing",
        label="Pricing page",
        added_at="2026-06-06T13:00:00Z",
    )

    assert source.uri == "https://example.com/pricing"
    assert source.id == source_id_for_uri("https://example.com/pricing")
    assert source.label == "example.com pricing"
    assert duplicate.id == source.id
    assert duplicate.label == "Pricing page"
    assert duplicate.added_at == source.added_at
    assert load_sources(paths) == [duplicate]
    assert paths.stitch_dir.is_dir()


def test_register_local_file_accepts_supported_extensions_and_defaults_label(
    tmp_path: Path,
) -> None:
    paths = StitchPaths(tmp_path)
    csv_file = tmp_path / "data" / "Weekly Signals.csv"
    csv_file.parent.mkdir()
    csv_file.write_text("company,signal\nAcme,up\n", encoding="utf-8")

    source = register_source(
        paths,
        Path("data") / "Weekly Signals.csv",
        added_at="2026-06-06T12:00:00Z",
    )
    duplicate = register_source(paths, csv_file.resolve())

    assert source.kind == "file"
    assert source.label == "Weekly Signals"
    assert source.path == str(csv_file.resolve())
    assert source.uri.startswith("file://")
    assert source.id == source_id_for_uri(source.uri)
    assert duplicate == source
    assert list_sources(paths) == [source]


def test_register_feed_like_url_uses_feed_kind(tmp_path: Path) -> None:
    paths = StitchPaths(tmp_path)

    source = register_source(
        paths,
        "https://feeds.example.com/rss.xml?market=ai",
        added_at="2026-06-06T12:00:00Z",
    )

    assert source.kind == "feed"
    assert source.uri == "https://feeds.example.com/rss.xml?market=ai"
    assert source.host == "feeds.example.com"
    assert source.label == "feeds.example.com feed"


def test_save_load_and_list_sources_round_trip(tmp_path: Path) -> None:
    paths = StitchPaths(tmp_path)
    source = Source(
        id="src_example",
        uri="https://example.com",
        label="Example",
        kind="url",
        added_at="2026-06-06T12:00:00Z",
        host="example.com",
    )

    save_sources(paths, [source])

    assert paths.sources_file.exists()
    assert load_sources(paths) == [source]
    assert list_sources(paths) == [source]


def test_register_local_file_rejects_unsupported_extension(tmp_path: Path) -> None:
    paths = StitchPaths(tmp_path)
    source_file = tmp_path / "data.json"
    source_file.write_text("{}", encoding="utf-8")

    with pytest.raises(SourceRegistrationError, match="unsupported local source file type"):
        register_source(paths, source_file)
