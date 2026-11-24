# Model content-filter diagnostics v1

Date: 2026-11-20

This extends the model transport failure contract in
`docs/en/model-api-compatibility.md` (Stream and research failure semantics)
and `docs/zh/developer-docs/agent-backend.md` (模型请求的线上诊断).

## Requirements

- CF-001: An empty streaming turn whose provider finish reason is exactly
  `content_filter` fails with the `content_filter` diagnostic classification and
  an explicit provider content-filter message. HTTP 200 does not make it a
  successful completion. Preserve request IDs and framing evidence.
- CF-002: Report one diagnostic record per request. Do not retry a filtered
  completion, strip its context, change models or change protocols automatically.
  Ordinary empty and reasoning-only turns retain their existing classifications.
- CF-003: The desktop failure card recognizes the explicit finish-reason marker
  in both new and previously persisted empty-turn diagnostics. Chinese and English
  copy explains that the provider filtered the response and directs the user to
  inspect the technical details or contact the provider. Keep raw evidence in
  the existing expandable details. Do not infer filtering from a generic error.

## Validation

Model transport tests cover an HTTP 200 filtered stream, no retries despite a
nonzero retry budget, one classified diagnostic, and the existing empty/reasoning
regressions. Desktop tests cover both languages, historical diagnostics and a
generic error that only mentions the word `content_filter`.

A synthetic basic check of the user's configured endpoint passed on 2026-11-20.
This does not certify a complete task with its history, tools and system prompt.
No local code change can guarantee that a provider will accept a filtered task.
