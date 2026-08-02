# Evidence locators

- PDF: `pdf_page` with one-based page number.
- HWP/HWPX: `hwp_structure` with section, block, table, row, or cell when available.
- XLSX shallow unit: identifies a sheet only; never use it for numeric claims.
- XLSX deep read: `xlsx_range` with exact sheet and A1 range.
- Slack: workspace, conversation, message timestamp, and optional thread root.
- Mail: account, mailbox placement, Message-ID or UID, and MIME part when available.

A locator is valid only while its source revision hash matches. When a source is stale, report the warning and request or run an explicit re-index before relying on it.
