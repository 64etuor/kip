# LLM capability scaling roadmap

Date: 2026-08-14. Owner decision: constraints sized for today's models must
not become the ceiling as models improve. The trust invariants stay fixed
(untrusted evidence, ACL-before-retrieval, candidates-are-not-facts, exact
evidence citations, no instructions from indexed content). Everything else
is a calibration that must widen through measurement, not decay into a
permanent limit.

## Landed in this change

1. **Capacity caps raised** to match current model context windows, all
   still config-tunable and code-bounded:
   - `search.context_max_chars` 40k -> 120k, `context_item_max_chars`
     8k -> 16k, `AnswerRequest.max_chars` default 12k -> 32k (ceiling
     40k -> 200k)
   - `models.generation.max_claims` 8 -> 16, `max_output_tokens`
     1024 -> 4096
   - `models.relation_mining` `max_units` 50 -> 200, `max_characters`
     120k -> 480k, `max_entity_proposals` 32 -> 128,
     `max_relation_proposals` 64 -> 256
   - `ontology.answer_context` `entity_limit` 8 -> 16, `edge_limit`
     50 -> 150
2. **Judge-proposed golden-set growth** (ADR-045): an LLM judge (external
   agent or the generation model) authors `kip.golden-draft.v1` case
   proposals; a human sample-audits (`kip evaluate draft review`) and an
   explicit promotion (`kip evaluate draft promote`, fail-closed on low
   sample coverage or any sampled rejection) merges cases into a reviewed
   golden dataset. The judge generates; only human-authorized promotion
   creates canonical truth. This unblocks growing the reviewed sets past
   ~19 cases, which in turn unblocks the stale-warning measurement gating
   semantic retrieval activation.

## Next, in priority order

3. **Semantic activation** (no design work left): extend the reviewed
   golden set with stale-source cases via the draft pipeline, measure
   `stale_warning_rate`, and if the gate passes, activate the vector path
   that already wins every quality metric (Recall@10 0.947 vs lexical
   0.789). This is now purely an evidence task. Landed alongside
   (2026-08-15, ADR-035 amendment): the embedding input cap rose
   4k -> 12k chars — the next projection rebuild embeds documents at the
   wider cap in a fresh versioned space, so run it before the activation
   measurement.
4. **Calibrated review tiers — LANDED 2026-08-15 (ADR-047)**: low-risk
   `review: not_required` predicates auto-approve when measured miner
   precision over >= 20 human decisions clears 0.95 and the candidate
   clears 0.8 confidence; marked (`auto-approve-policy/v1`), reported in
   mining payloads, revocable, self-excluding statistics, fail-closed on
   every axis. Architecture rule 10 reworded with owner sign-off.
   Also landed from the same ADR review: clarification choice cap 4 -> 8
   (`CLARIFICATION_CHOICE_CAP`, ADR-032 amendment).
5. **Single-pass mining**: the two-pass loop (mine -> approve entities ->
   re-mine) exists because relations may only reference approved
   entities. A batch-consistency mode could stage entity+relation
   proposals together and release both on one review pass. Its
   prerequisite (review-tier calibration, item 4) is now in place; this
   is the next candidate when mining volume justifies it.
6. **Richer feedback signal**: feedback is deliberately bounded to coded
   outcomes and reason codes for privacy. If the owner wants richer
   learning signal, the consent framework (ADR-040/044 pattern) can gate
   an opt-in free-text feedback channel that is stored as untrusted
   content, never as instructions. Privacy decision, not a technical one.

## Standing rule

When a cap or gate is raised, the change must land as configuration plus
measurement, documented in `CHANGELOG.md` and the affected canonical docs,
never as a silent code-side widening. Capability scaling is a release
decision, not a drift.
