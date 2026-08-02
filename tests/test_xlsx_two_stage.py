from __future__ import annotations

from openpyxl import Workbook

from kip.domain.models import SearchRequest


def test_xlsx_shallow_search_and_deep_range(test_container):
    path = test_container.settings.project_root / "source" / "A과제_정산.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "정산"
    sheet.append(["구분", "항목", "금액"])
    sheet.append(["장비비", "카메라", 890000])
    sheet.append(["인건비", "참여율", "=C2*2"])
    workbook.save(path)

    context = test_container.service.request_context()
    summary = test_container.service.sync_filesystem(context, "fixture")
    assert summary.inserted == 1

    hits = test_container.service.search(context, SearchRequest(query="장비비 카메라", limit=10))
    assert hits
    unit = test_container.repository.get_content_unit(context, hits[0].unit_id)
    assert unit.unit_type == "xlsx_sheet_shallow"
    assert "장비비" in unit.body
    assert "890000" not in unit.body
    assert unit.metadata["deep_read_required_for_numbers"] is True

    deep = test_container.service.read_xlsx(
        context,
        hits[0].artifact_id,
        sheet="정산",
        cell_range="A1:C3",
    )
    assert deep.cells[1][2]["value"] == 890000
    assert deep.cells[2][2]["value"] == "=C2*2"
    assert deep.source_changed_since_index is False
