# GitHub issue mentions

An explicitly authorized GitHub user can mention the connected App bot in a **new
issue comment** to create a visible Nautionette chat and run the normal default
agent. Further authorized mentions on that issue continue the same chat, including
while it is answering (the ordinary durable chat queue handles follow-ups).
Unmentioned comments are not forwarded. Editing a comment does not trigger work.
Pull-request comments, issue descriptions, and bot-authored comments do not trigger
work in this version.

## Setup

1. Connect a GitHub App under **Settings > Projects**, using the public HTTPS
   instance URL, and add the desired repositories. Their download must be ready.
2. New Apps request **Issues: read and write** and subscribe to **Issue comment**
   automatically. For an existing App, open its GitHub App settings, enable that
   permission and webhook event, save, and approve the permission change on the
   installation. This does not happen automatically when upgrading Nautionette.
3. In each connection's **Authorized GitHub user IDs** field, enter comma-separated
   numeric GitHub user IDs and save. An empty list disables all mention triggers.
   Find a user's immutable numeric `id` using GitHub's `GET /users/{username}` API;
   usernames, repository collaborators, issue authors, and GitHub organization
   membership alone do **not** authorize anyone.
4. Keep **Reply on GitHub with the chat link** enabled for acknowledgements, or
   disable it if publishing the instance URL on an issue would be inappropriate.
   Chat links still require the existing Nautionette authentication; they are not
   share links or login tokens. Chats have the same instance-wide visibility as
   chats created in the UI, not per-GitHub-user visibility.

Example (replace the name with the actual slug shown in Projects):

```text
@nautionette-abc123 Fix this issue, add regression tests, and open a PR.
```

Follow up on the same issue:

```text
@nautionette-abc123 Also cover the timeout case in the tests.
```

Type the mention manually if GitHub does not offer the App in autocomplete.
Both `@slug` and `@slug[bot]` are accepted; autocomplete or a rendered profile link
is not needed. GitHub delivers **issue_comment** events for all new comments on
installed repositories, not only recognized mentions.

If nothing arrives, open the **connected App's** GitHub settings (not a different
new App), check **Permissions & events > Issues: read and write > Issue comment**,
and approve the installation permission update. Under **Advanced > Recent
Deliveries**, check whether the comment generated a delivery and its response.
The webhook URL must point to this instance's
`/api/projects/github-app/webhook`. Redeliver a failed delivery after fixing setup,
or post a new comment; editing an old comment does not trigger work. Upgrading
Nautionette does not update existing GitHub App subscriptions.

The App acknowledges each accepted request with a link to the same chat, then
submits it through the regular chat pipeline. The agent receives the current issue
title/body and the triggering comment, not every intervening comment. The selected
repository is the only attached writable project. The agent inherits the instance's
default agent/model/tools/extensions. Code requests are prompted to use a task
branch and PR, not push to the default branch. This is an agent instruction, not a
GitHub branch protection rule: protect your default branch separately.

## Security and operational behavior

- This allowlist grants **agent execution**, including the configured tools and
  repository write access. Add only people trusted to operate this Nautionette
  instance. There is no automatic mapping to individual Nautionette admin accounts.
- Webhook signatures and installation IDs are verified before accepting anything.
  The commenter and sender must be the same human user. Repository access is
  checked against an added, ready project on that exact connection.
- Authorization is checked again when processing an accepted request. Revoking a
  user or removing/suspending the installation prevents pending requests from
  starting; it does not cancel an already-submitted agent turn.
- Issue text is labeled untrusted reference material, not authorization. This is
  not a complete defense against prompt injection. The agent has the same trust
  and permission boundaries as a normal chat; do not attach overpowered tools to
  an agent that will read untrusted issue content.
- The signed webhook stores an inbox entry and returns promptly. A single backend
  consumer processes it, including after restart. Immutable comment IDs deduplicate
  deliveries permanently, even when GitHub changes the delivery ID. Keep one backend
  worker process, as in the provided deployment; the consumer is not a distributed
  multi-worker job scheduler.
- Failures retry with bounded backoff (up to five minutes). Requests on the same
  issue retain their order; a failing issue does not block other issues. With
  acknowledgements enabled, a GitHub reply failure delays agent submission rather
  than silently starting without a link. Fix the App permission/network issue, or
  disable acknowledgements for that connection to allow submission on retry.
- A repository-scoped, Issues-only installation token posts acknowledgements;
  workspace Git tokens are unchanged. Replies include a hidden comment-ID marker
  so a retry can reconcile a successful POST whose response was lost. As with any
  external API, strict exactly-once posting across network failures is not
  guaranteed; chat/turn identities remain deduplicated.
- Completion is viewed in Nautionette. Agent results and transcripts are not
  automatically published back to GitHub.
- Incoming payloads are cleared once submitted or ignored; comment IDs remain as
  deduplication records. `github.mention_submitted` and `github.mention_failed`
  appear in system events. Pending entries, retry times and sanitized failure
  messages are stored in SQLite's `github_mentions` table. Projects shows pending
  and retrying request counts per connection; use Refresh connections to update.
- Deleting a chat removes its issue mapping. A new mention can create a new chat
  for that issue; deleting a chat is not a way to revoke mention access.
- After submission, execution/restart/stop behavior is exactly that of normal
  chats, including paused queues and recovery notices. The durable webhook inbox
  does not make arbitrary agent tool calls safe to repeat after a restart.

## API

Instance-authenticated configuration:

```http
PUT /api/projects/github-app/{connection_id}/mentions
Authorization: Bearer <instance token>
Content-Type: application/json

{"user_ids": [123456], "acknowledge": true}
```

IDs must be positive integers (at most 100). `GET /api/projects/github-app` returns
only the allowlist, acknowledgement preference, inbox counts, and the existing safe connection
status fields; App credentials and webhook secrets are never returned.
