from __future__ import annotations

from datetime import UTC, date, datetime, time

from kip.domain.models import Envelope, EnvelopeMeta, XlsxRangeRead


def test_xlsx_dates_are_json_safe_in_the_public_envelope() -> None:
    # Given workbook scalars returned by openpyxl for date-bearing cells.
    result = XlsxRangeRead(
        artifact_id="art_dates",
        source_uri="file:///dates.xlsx",
        sheet="Evidence",
        cell_range="A1:C1",
        cells=[
            [
                {"coordinate": "A1", "value": date(2026, 8, 13)},
                {
                    "coordinate": "B1",
                    "value": datetime(2026, 8, 13, 9, 30, tzinfo=UTC),
                },
                {"coordinate": "C1", "value": time(9, 30)},
            ]
        ],
        indexed_source_sha256="a" * 64,
        current_source_sha256="a" * 64,
        source_changed_since_index=False,
    )
    envelope = Envelope(
        ok=True,
        data=result.model_dump(mode="json"),
        meta=EnvelopeMeta(request_id="req_dates", workspace="default"),
    )

    # When the CLI/REST-compatible envelope is serialized.
    payload = envelope.model_dump_json()

    # Then no Python date object escapes the versioned JSON boundary.
    assert '"2026-08-13"' in payload
    assert '"2026-08-13T09:30:00Z"' in payload
    assert '"09:30:00"' in payload
