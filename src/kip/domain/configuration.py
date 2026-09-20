"""The configured values each use case actually reads, already validated.

A use case that holds a configuration object can reach any key at any time, so
its real inputs are invisible and the layer ends up importing the TOML loader.
These frozen records are the inputs instead: `kip.container` builds them once
from `kip.settings`, raising :class:`kip.errors.ConfigurationError` at start-up
for a value that is out of range, and the use cases receive only what they use.

`kip doctor` is deliberately not here: reporting on arbitrary configuration is
its use case, so it reads through
:class:`kip.ports.configuration.ConfigurationReader`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GenerationSettings:
    """`[models.generation]` as answering uses it."""

    enabled: bool
    fallback_on_error: bool
    max_claims: int
    max_output_tokens: int
    # Extensions of every indexed source, so a question naming a file that is
    # not among allowed evidence fails closed for operator-indexed extensions
    # exactly as it does for the built-in ones.
    document_extensions: frozenset[str]


@dataclass(frozen=True, slots=True)
class SearchSettings:
    """`[search]` as the retrieval use cases and the search engine use it."""

    semantic_enabled: bool
    default_mode: str
    context_item_max_chars: int
    alias_expansion_enabled: bool
    alias_expansion_max_terms: int
    max_hits_per_document: int
    abstain_on_unknown_terms: bool
    hybrid_candidate_limit: int
    rerank_candidate_limit: int
    lexical_rerank_enabled: bool
    # Whether the operator wrote `search.lexical_rerank_enabled` themselves.
    # The shipped default is on, so a config that sets neither the flag nor a
    # reranker adapter leaves lexical ranking unreranked rather than failing a
    # search; an explicit `true` without an adapter stays a loud
    # misconfiguration, because there the operator did ask for it.
    lexical_rerank_explicit: bool
    lexical_rerank_candidate_limit: int
    rrf_rank_constant: int


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    """`[models.embedding]` plus the two `[search]` switches projection obeys.

    `space_name` and `document_instruction` are part of the embedding space
    identity, so they stay unresolved strings: a default that disagreed with
    the shipped profile would build a space no release reviewed.
    """

    space_name: str | None
    document_instruction: str | None
    query_instruction: str
    batch_size: int
    page_size: int
    max_batch_chars: int
    max_document_chars: int
    semantic_enabled: bool
    semantic_auto_activate: bool


@dataclass(frozen=True, slots=True)
class OperationsSettings:
    """What `capabilities`, `migrate` and the default request context need."""

    workspace: str
    migrations_path: Path
    is_memory: bool
    semantic_enabled: bool
    # Configured keys no code reads. Reported once in `capabilities`, because
    # a misspelled key changes nothing at runtime but the operator who wrote
    # it deserves to hear about it.
    unknown_config_keys: tuple[str, ...]
