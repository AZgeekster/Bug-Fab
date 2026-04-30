# Bug-Fab Integration Guides

This directory holds **per-stack walkthroughs** — the step-by-step "drop Bug-Fab into a real production app of stack X" recipes. They sit between the language-level sketches in [`../ADAPTERS.md`](../ADAPTERS.md) and the wire-protocol contract in [`../PROTOCOL.md`](../PROTOCOL.md).

| Layer | What it tells you |
|-------|-------------------|
| [`../PROTOCOL.md`](../PROTOCOL.md) + [`../protocol-schema.json`](../protocol-schema.json) | The 8 endpoints, JSON shapes, error envelope. The contract every adapter satisfies. |
| [`../ADAPTERS.md`](../ADAPTERS.md) | Code-level sketches per language / framework. Useful when writing a new adapter. |
| `integrations/<stack>.md` (this directory) | A complete, copy-paste-ready walkthrough for one specific stack — schema, routes, deployment, conformance. Useful when integrating Bug-Fab into a real app. |

## Available guides

| Guide | Stack | Companion AI doc |
|-------|-------|------------------|
| [`fastify-nextjs-postgres.md`](./fastify-nextjs-postgres.md) | Fastify ≥ 5 + Next.js ≥ 14 (App Router) + PostgreSQL + PM2 | [`fastify-nextjs-postgres.AGENTS.md`](./fastify-nextjs-postgres.AGENTS.md) |

## Adding a guide

A new integration guide is a substantial deliverable (~2-3 days of focused work + a real consumer integration to validate against). The process:

1. **Confirm a real consumer is integrating against the stack.** Speculative guides for stacks no one is using become stale fast. The Fastify+Next.js+Postgres+PM2 guide exists because TKR (Bug-Fab's first Node consumer) is integrating against that stack.
2. **Build the integration first; document second.** The guide should reflect the path the consumer actually walked, not the path you imagined they'd walk.
3. **Pair with an AGENTS.md companion.** Each guide has a sibling `<stack>.AGENTS.md` for AI coding assistants that need step-by-step file-by-file instructions. Humans use the main guide; AIs use the AGENTS.md.
4. **Add to [`../ADAPTERS_REGISTRY.md`](../ADAPTERS_REGISTRY.md).** The 12-field registry entry tracks the new guide alongside any maintained packages and sketches for the same stack.
5. **Open a PR.** Bug-Fab maintainers review against the corresponding entries in `ADAPTERS_REGISTRY.md` and the wire protocol.

## Why per-stack guides instead of a generator

The wire protocol is deliberately stack-agnostic, but the "drop it into your app" experience is stack-specific:

- **Fastify** plugin registration is `app.register(plugin, { prefix })`.
- **Express** middleware is `app.use('/api', router)`.
- **Django** is a reusable app installed via `INSTALLED_APPS` + `urls.py`.
- **Next.js** Route Handlers live in `app/api/.../route.ts` files.

A single generic guide can't capture these conventions without becoming a wishy-washy "you'll need to mount the routes somehow" document. Per-stack guides are the cost of giving consumers a real answer.

This means most stacks won't have a dedicated guide. That's fine — they have [`../ADAPTERS.md`](../ADAPTERS.md) sketches and [`../PROTOCOL.md`](../PROTOCOL.md) for guidance, plus the option of contributing a guide once they've integrated.
