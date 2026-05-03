# Schemas

JSON Schema draft 2020-12. Each schema is referenced from at least one RFC.

## Layout

- `messages/` — A2A envelope and message-shape schemas.
- `credentials/` — VC payload and freshness-proof schemas.
- `events/` — (placeholder) audit / log events.

## Naming

`<concept>.schema.json` (e.g. `subject-credential.schema.json`).

## `$id`

`https://sh4dow.org/schemas/v<MAJOR>/<path>.schema.json`.
