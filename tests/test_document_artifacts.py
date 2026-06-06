from __future__ import annotations

from stitch.document_artifacts import (
    render_extracted_document_artifact,
    write_extracted_document_artifacts,
)
from stitch.models import Citation, ExtractedPayload, Source
from stitch.paths import StitchPaths


def _payload() -> ExtractedPayload:
    source = Source(
        id="src_prices",
        uri="file:///prices.csv",
        label="Pricing",
        kind="file",
        added_at="2026-06-06T00:00:00Z",
        path="/prices.csv",
    )
    return ExtractedPayload(
        source=source,
        markdown="| company | price |\n| --- | --- |\n| Alpha | $10 USD |",
        citations=[
            Citation(
                id="src_prices:1",
                source_id="src_prices",
                label="Pricing",
                uri=source.uri,
                locator="row 2",
                excerpt="Alpha, $10 USD",
            )
        ],
        metadata={"extractor": "local-file", "format": "csv"},
    )


def test_rendered_extracted_document_contains_synthetic_description_and_payload() -> None:
    artifact = render_extracted_document_artifact(_payload())

    assert "# Extracted Document: Pricing" in artifact
    assert "## Synthetic Description" in artifact
    assert "structured tabular data with fields: company, price" in artifact
    assert "## Normalized Markdown Payload" in artifact
    assert "| Alpha | $10 USD |" in artifact
    assert "`src_prices:1` row 2: Alpha, $10 USD" in artifact


def test_write_extracted_document_artifacts_adds_artifact_metadata(tmp_path) -> None:
    paths = StitchPaths(tmp_path)

    [payload] = write_extracted_document_artifacts(paths, "run1", [_payload()])

    artifact_path = tmp_path / ".stitch" / "extracted" / "run1" / "src_prices.md"
    assert artifact_path.exists()
    assert payload.metadata["artifact_path"] == str(artifact_path)
    assert payload.metadata["artifact_kind"] == "synthetic-document-markdown"
