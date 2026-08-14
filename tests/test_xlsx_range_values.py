from __future__ import annotations

import json
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from typer.testing import CliRunner

from kip.adapters.parsers.xlsx import read_xlsx_range
from kip.adapters.storage.local import LocalWorkbookReader
from kip.api import create_app
from kip.cli import app
from kip.domain.models import SearchRequest
from kip.errors import ValidationError


def _rewrite_sheet_xml(path: Path, replacements: dict[bytes, bytes]) -> None:
    rewritten = path.with_suffix(".rewritten.xlsx")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(
        rewritten,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as target:
        for member in source.infolist():
            payload = source.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                for old, new in replacements.items():
                    assert old in payload
                    payload = payload.replace(old, new, 1)
            target.writestr(member, payload)
    rewritten.replace(path)


def test_local_reader_normalizes_every_supported_openpyxl_scalar(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.iso_dates = True
    sheet = workbook.active
    sheet.title = "Values"
    sheet.append(
        [
            date(2026, 8, 13),
            datetime(2026, 8, 13, 9, 30, 15),
            time(9, 30, 15),
            timedelta(days=1, hours=2, minutes=3, seconds=4, milliseconds=500),
            True,
            "#DIV/0!",
        ]
    )
    sheet["A1"].number_format = "yyyy-mm-dd"
    sheet["B1"].number_format = "yyyy-mm-dd hh:mm:ss"
    sheet["C1"].number_format = "hh:mm:ss"
    sheet["D1"].number_format = "[h]:mm:ss.000"
    sheet["F1"].data_type = "e"
    path = tmp_path / "values.xlsx"
    workbook.save(path)

    cells = LocalWorkbookReader().read(path, "Values", "A1:F1")[0]

    assert [cell["value"] for cell in cells] == [
        "2026-08-13",
        "2026-08-13T09:30:15",
        "09:30:15",
        "P1DT2H3M4.5S",
        True,
        "#DIV/0!",
    ]
    assert [cell["value_type"] for cell in cells] == [
        "date",
        "datetime",
        "time",
        "duration",
        "boolean",
        "error",
    ]
    assert cells[0]["excel_serial"] == pytest.approx(46247.0)
    assert cells[3]["excel_serial"] == pytest.approx(1.08546875)
    json.dumps(cells, allow_nan=False)


def test_formula_objects_and_date_cache_are_readable_json(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Formulas"
    sheet["A1"] = "=DATE(2026,8,13)"
    sheet["A1"].number_format = "yyyy-mm-dd"
    sheet["B1"] = ArrayFormula("B1:B2", "=ROW(B1:B2)*2")
    sheet["C1"] = DataTableFormula(
        "C1:D2",
        ca=True,
        dt2D=True,
        dtr=True,
        r1="E1",
        r2="E2",
    )
    path = tmp_path / "formulas.xlsx"
    workbook.save(path)
    _rewrite_sheet_xml(
        path,
        {b"<f>DATE(2026,8,13)</f><v></v>": b"<f>DATE(2026,8,13)</f><v>46247</v>"},
    )

    cells = LocalWorkbookReader().read(path, "Formulas", "A1:C1")[0]

    assert cells[0]["value"] == "=DATE(2026,8,13)"
    assert cells[0]["cached_value"] == "2026-08-13T00:00:00"
    assert cells[0]["cached_excel_serial"] == pytest.approx(46247.0)
    assert cells[0]["formula_kind"] == "normal"
    assert cells[1]["value"] == "=ROW(B1:B2)*2"
    assert cells[1]["formula"] == "=ROW(B1:B2)*2"
    assert cells[1]["formula_kind"] == "array"
    assert cells[1]["formula_ref"] == "B1:B2"
    assert cells[2]["value"] is None
    assert cells[2]["formula_kind"] == "data_table"
    assert cells[2]["formula_ref"] == "C1:D2"
    assert cells[2]["formula_attributes"]["dt2D"] == "1"
    assert "openpyxl" not in json.dumps(cells)


def test_range_read_returns_the_exact_requested_rectangle(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sparse"
    sheet["B2"] = "used"
    path = tmp_path / "sparse.xlsx"
    workbook.save(path)

    single = read_xlsx_range(path, "Sparse", "B2")
    empty = read_xlsx_range(path, "Sparse", "C3:D5")

    assert [[cell["coordinate"] for cell in row] for row in single["cells"]] == [["B2"]]
    assert single["cells"][0][0]["value"] == "used"
    assert [[cell["coordinate"] for cell in row] for row in empty["cells"]] == [
        ["C3", "D3"],
        ["C4", "D4"],
        ["C5", "D5"],
    ]
    assert all(cell["value"] is None for row in empty["cells"] for cell in row)


@pytest.mark.parametrize(
    "cell_range",
    [
        "C2:A1",
        "XFE1:XFE1",
        "A1048577:A1048577",
        "A1:B50001",
    ],
)
def test_range_read_rejects_reversed_out_of_bounds_or_oversized_ranges(
    tmp_path: Path,
    cell_range: str,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Bounds"
    path = tmp_path / "bounds.xlsx"
    workbook.save(path)

    with pytest.raises(ValidationError, match="range"):
        read_xlsx_range(path, "Bounds", cell_range)


def test_range_read_marks_merged_and_filtered_cells(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Layout"
    sheet["A1"] = "merged"
    sheet.merge_cells("A1:B2")
    sheet.append(["header", "value"])
    sheet.append(["visible", 1])
    sheet.append(["filtered", 2])
    sheet.auto_filter.ref = "A3:B5"
    sheet.row_dimensions[2].hidden = True
    sheet.row_dimensions[5].hidden = True
    path = tmp_path / "layout.xlsx"
    workbook.save(path)

    cells = read_xlsx_range(path, "Layout", "A1:B5")["cells"]

    assert cells[0][0]["merged"] is True
    assert cells[0][0]["merge_master"] == "A1"
    assert cells[0][0]["merge_range"] == "A1:B2"
    assert cells[1][1]["merged"] is True
    assert cells[1][1]["merge_master"] == "A1"
    assert cells[1][0]["row_hidden"] is True
    assert cells[1][0]["row_filtered"] is False
    assert cells[4][0]["row_hidden"] is True
    assert cells[4][0]["row_filtered"] is True


def test_non_finite_numeric_cell_does_not_turn_into_json_null(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Malformed"
    sheet["A1"] = 1
    path = tmp_path / "non-finite.xlsx"
    workbook.save(path)
    _rewrite_sheet_xml(path, {b"<v>1</v>": b"<v>1E9999</v>"})

    cell = LocalWorkbookReader().read(path, "Malformed", "A1")[0][0]

    assert cell["value"] == "Infinity"
    assert cell["value_type"] == "non_finite_number"
    assert cell["cached_value"] == "Infinity"
    json.dumps(cell, allow_nan=False)


def test_rest_xlsx_range_returns_date_cells_in_the_json_envelope(test_container) -> None:
    path = test_container.settings.project_root / "source" / "schedule.xlsx"
    workbook = Workbook()
    workbook.iso_dates = True
    sheet = workbook.active
    sheet.title = "Schedule"
    sheet.append(["milestone", date(2026, 8, 13)])
    workbook.save(path)
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    hit = test_container.application.retrieval.search(
        context,
        SearchRequest(query="milestone"),
    )[0]
    client = TestClient(create_app(test_container))

    response = client.get(
        f"/v1/xlsx/{hit.artifact_id}/range",
        headers={"X-KIP-API-Key": "test-key"},
        params={"sheet": "Schedule", "cell_range": "A1:B1"},
    )

    assert response.status_code == 200
    cell = response.json()["data"]["cells"][0][1]
    assert cell["value"] == "2026-08-13"
    assert cell["cached_value"] == "2026-08-13"
    assert cell["excel_serial"] == pytest.approx(46247.0)


def test_cli_xlsx_range_returns_date_cells_in_the_versioned_envelope(
    monkeypatch,
    test_container,
) -> None:
    path = test_container.settings.project_root / "source" / "schedule-cli.xlsx"
    workbook = Workbook()
    workbook.iso_dates = True
    sheet = workbook.active
    sheet.title = "Schedule"
    sheet.append(["deadline", date(2026, 8, 13)])
    workbook.save(path)
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    hit = test_container.application.retrieval.search(
        context,
        SearchRequest(query="deadline"),
    )[0]
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: test_container,
    )

    result = CliRunner().invoke(
        app,
        ["xlsx-read", hit.artifact_id, "--sheet", "Schedule", "--range", "A1:B1"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["data"]["cells"][0][1]["value"] == "2026-08-13"
