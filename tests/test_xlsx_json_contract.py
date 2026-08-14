from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from openpyxl import Workbook

from kip.adapters.storage.local import LocalWorkbookReader
from kip.domain.models import Envelope, EnvelopeMeta, XlsxRangeRead


def test_xlsx_dates_are_json_safe_in_the_public_envelope(tmp_path: Path) -> None:
    # Given date-bearing cells read through the real workbook adapter boundary.
    workbook = Workbook()
    workbook.iso_dates = True
    sheet = workbook.active
    sheet.title = "Evidence"
    sheet.append(
        [
            date(2026, 8, 13),
            datetime(2026, 8, 13, 9, 30),
            time(9, 30),
        ]
    )
    path = tmp_path / "dates.xlsx"
    workbook.save(path)
    cells = LocalWorkbookReader().read(path, "Evidence", "A1:C1")

    result = XlsxRangeRead(
        artifact_id="art_dates",
        source_uri="file:///dates.xlsx",
        sheet="Evidence",
        cell_range="A1:C1",
        cells=cells,
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
    assert '"2026-08-13T09:30:00"' in payload
    assert '"09:30:00"' in payload
