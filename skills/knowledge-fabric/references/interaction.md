# Interaction consent

Use `interaction` commands only when the requested task needs clarification,
preferences, or feedback. Configuration can disable them, and `capabilities`
does not report whether they are available — find out from the command's own
error, and treat a refusal as configuration, not as a retryable failure.

Clarification answers are not stored preferences. `interaction remember`
requires explicit confirmation (`--confirmed`); `forget` removes a preference.
Preferences shape presentation only and never widen ACL scope or override
evidence rules. Use `interaction feedback` for structured outcomes tied to a
request, and command help for accepted values.
