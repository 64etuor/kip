# ADR-004: XLSX uses shallow indexing and deep reads

Status: Accepted

The global pass indexes sheet names, dimensions, shared strings, inline strings, and structural metadata. Numeric answers and formulas are read from the selected workbook range at query time.
