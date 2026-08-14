# Golden case drafts

A draft (`kip.golden-draft.v1`) is a batch of evaluation cases an LLM judge
*proposes* against a fixed `corpus_fingerprint`, with a `judge_confidence` and
`rationale` recorded per case. A draft is an immutable proposal — it carries
no status field and is never evaluated or shipped on its own.

A human reviewer samples the draft and records approve/reject decisions in a
`kip.golden-draft-review.v1` file bound to the exact draft content by a
sha256 fingerprint (`kip evaluate draft review`). Promotion
(`kip evaluate draft promote`) is fail-closed: it requires a minimum sampled
coverage rate, refuses the whole batch if any sampled case was rejected, and
refuses on case-ID collisions with the target dataset. Only promotion appends
cases into a real `kip.golden-dataset.v1` file — the judge is the generator,
never the canonicalization gate. A draft may never set canonical-authority
fields (`lifecycle`, `version`, `reviewer`, `source_revision`) itself;
promotion assigns them explicitly via `--lifecycle` (default `reviewed`),
`--dataset-version` (required unless the target dataset already has a
non-draft version), and `--source-revision` (defaults to the draft's
`corpus_fingerprint`).
