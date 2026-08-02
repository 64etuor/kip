# Public government evaluation corpus

This pilot corpus contains six Korean government PDF documents. Every source
page identifies the document as Public Nuri (KOGL) Type 1, which permits reuse
with attribution. The manifest records the source page, exact download URL,
license, attribution, and SHA-256 digest.

The PDFs are not stored in the repository. Fetch and verify them into the
gitignored runtime directory:

```bash
./scripts/fetch_public_corpus.py
./scripts/fetch_public_corpus.py --check
```

Only `https://*.go.kr` URLs are accepted. Downloads must be PDFs, remain below
25 MiB, and match the pinned checksum before becoming visible to ingestion.
The corpus contains public policy material only and intentionally excludes
OneDrive or other private files.

When redistributing output derived from the corpus, preserve each manifest
entry's attribution. Images or third-party material embedded in a government
page can have separate rights; this evaluation uses extracted text only.
