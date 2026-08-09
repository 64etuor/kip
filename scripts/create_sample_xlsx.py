#!/usr/bin/env python3
from pathlib import Path

try:
    from openpyxl import Workbook
except ImportError as exc:
    raise SystemExit(
        "Install the extractors extra before generating the sample workbook"
    ) from exc

root = Path(__file__).resolve().parents[1]
out = root / "sample-data" / "A과제_정산.xlsx"
if out.exists():
    raise SystemExit(0)
wb = Workbook()
ws = wb.active
ws.title = "정산"
ws.append(["구분", "항목", "금액", "제출기한"])
ws.append(["인건비", "참여연구원", 1500000, "2026-08-15"])
ws.append(["장비비", "카메라", 890000, "2026-08-15"])
ws2 = wb.create_sheet("코드")
ws2.append(["과제번호", "A-2026-001"])
wb.save(out)
