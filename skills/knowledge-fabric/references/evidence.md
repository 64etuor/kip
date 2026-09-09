# Evidence locators

- PDF: `pdf_page` with one-based page number.
- HWP/HWPX: `hwp_structure` with section, block, table, row, or cell when available.
- XLSX shallow unit: identifies a sheet only; never use it for numeric claims.
- XLSX deep read: `xlsx_range` with exact sheet and A1 range.
- Slack: workspace, conversation, message timestamp, and optional thread root.
- Mail: account, mailbox placement, Message-ID or UID, and MIME part when available.

A locator supports a current claim only while its source revision hash matches.
When stale, report the warning and withhold current-state conclusions. Re-index
only when the user has authorized maintenance; ordinary retrieval does not.
