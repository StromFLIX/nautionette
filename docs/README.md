# Denkzeug documentation

[Back to the project](../README.md) · [Website](https://nautionette.stromflix.com/)

## Start here

- [Self-hosting](self-hosting.md) — prerequisites, Docker setup, model connections,
  HTTPS, updates and backups.
- [Configuration template](../.env.example) — existing environment variables and
  defaults. Do not copy secrets into Git or an issue.
- [Clients and signing](reference.md#desktop-and-ios-builds) — desktop, Android and
  iOS build/install limitations. All clients need an existing instance.

## Use and extend the workspace

| Guide | Covers |
| --- | --- |
| [Agent configuration](agent-configuration.md) | Models, global defaults, saved agents, per-chat overrides and delegation. |
| [MCP servers](mcp-servers.md) | HTTP/stdio tools, hosted OAuth and CLI sign-in. |
| [Native creative apps](creative-apps.md) | ArtCraft tools, versions, updates and sandbox boundaries. |
| [Chat outputs](chat-outputs.md) | Published files, editable sources, immutable revisions and storage quotas. |
| [Miniapps](mini-apps.md) | Publishing, isolated runtimes, capabilities, limits and persistent state. |
| [Workflow authoring](reference.md#workflows-are-python-files) | Python manifests, validation, deployment, flow views and execution statistics. |
| [Saved workflow agents](workflow-agents.md) | Saved profiles, isolated project worktrees, durable retries and cancellation. |
| [GitHub projects](reference.md#github-projects) | App registration, repository access, worktrees, retention and Git attribution. |
| [GitHub issue mentions](github-mentions.md) | Authorized issue-triggered conversations and follow-ups. |
| [Conversation search](conversation-search.md) | Local keyword/semantic indexing, scope and privacy. |
| [Dictation](dictation.md) | Streaming speech, languages, device permissions and privacy. |
| [Native chat sessions](chat-sessions.md) | Durable Pi context, recovery, attachments and migration. |
| [Custom skins](skin-packs.md) | Themes, CSS/layout bundles, import compatibility and recovery mode. |

## Build and operate

- [Architecture](reference.md#architecture) and [repository layout](reference.md#layout).
- [Development and integration tests](reference.md#python-development-and-integration-tests).
- [Frontend design](frontend-design.md) and [canonical branding](../branding/README.md).
- [Telemetry](telemetry.md) — optional OTLP configuration and payload/privacy limits.
- [Project-chat latency investigation](spikes/project-chat-ttft.md) — measured startup
  timings, limitations and the authenticated HTTP/SSE benchmark.
- [Deployment pipelines](github-pipelines.md) — CI, releases, images and deployment checks.
- [Isolated staging](staging.md) — provisioning, production-data snapshots and
  quarantine precautions. This is an operator procedure, not the normal quick start.
- [Dependency audit](dependency-audit-2026-11-10.md) and
  [chat-finalization incident notes](incidents/2026-11-11-abandoned-chat-turns.md).

The [technical reference](reference.md) retains the detailed runtime/API and build
notes formerly in the root README. Prefer the focused guides above for a task.
Research under [`spikes/`](spikes/) and [integration investigations](workiq-oauth-investigation.md)
is not a promise of shipped functionality. The [chat app workspace design](chat-app-workspaces.md)
also contains design/history material; use the current runtime guides for deployment.
