# ADR-004: XLSX uses shallow indexing and deep reads

Status: Accepted

The global pass indexes sheet names, dimensions, shared strings, inline strings, and structural metadata. Numeric answers and formulas are read from the selected workbook range at query time.

Deep reads preserve the exact requested rectangle and expose a strict JSON cell
contract: source formula and cached value are separate; date/time/duration
values retain Excel serial and number-format context; merged, hidden, and
filtered layout state stays explicit. Requests are validated against worksheet
bounds and a 100,000-cell dense-response cap. The adapter remains the read-only
openpyxl implementation; a whole-workbook chunking parser is not introduced
into this exact-evidence path.
