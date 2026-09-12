# Evidence locators

- PDF: `pdf_page` with one-based page number.
- HWP/HWPX: `hwp_structure` with section, block, table, row, or cell when available.
- XLSX shallow unit: identifies a sheet only; never use it for numeric claims.
- XLSX deep read: there is no `xlsx_range` locator type. `xlsx-read` returns
  the range itself — `sheet`, `cell_range`, and the indexed vs. current source
  hash — so cite those fields.
- Slack: workspace, conversation, message timestamp, and optional thread root.
- Mail: the `email_message` locator carries `account_id`, `mailbox`,
  `message_id`, and `uid`. There is no MIME-part field; do not cite one.

A locator supports a current claim only while its source revision hash matches.
When stale, report the warning and withhold current-state conclusions. Re-index
only when the user has authorized maintenance; ordinary retrieval does not.
