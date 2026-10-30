# Conversation search

On desktop, **Ctrl/Command + K** or **Expand chat search** opens the global palette.
A non-empty query searches every saved chat, independent of sidebar agent/activity
filters and the sidebar's 200-chat list. Settings keep their existing local search.
An empty query still shows recent chats. The compact mobile sidebar filter is
unchanged; it remains a local title/latest-preview filter.

## What is searched

- Chat titles and complete saved **user/assistant message content**.
- FTS5 provides case/diacritic-insensitive, word-prefix keyword search. All query
  words must occur in a matching title or message. User input is literal text, not
  an FTS query language. Titles receive extra lexical weight.
- Local CPU embeddings provide semantic matches even without overlapping words.
  The initial model is the English-focused `BAAI/bge-small-en-v1.5`, with 384
  dimensions and pinned ONNX weights. Other languages still have keyword search;
  multilingual semantic quality is not guaranteed by this model.
- Semantic KNN searches the entire sqlite-vec index, not just lexical candidates.
  A cosine-distance cutoff avoids filling the list with unrelated neighbors.
- Reciprocal rank fusion combines **chat-level** keyword and semantic ranks. A
  long conversation does not get multiple votes for having more matching chunks.
  One result per chat includes the best keyword excerpt (or semantic passage if
  there is no keyword hit). Semantic-only excerpts are marked **Related**.
- Selecting a message match opens the chat at that message, outlines/focuses it,
  and preserves a draft already being written there. Title hits open the chat.

System messages, job snapshots, hidden prompts, message metadata, tool arguments
and results, attachment bytes, and generated files are **not** indexed. Text
explicitly included in a visible user/assistant message is searchable. The
endpoint uses the same instance authentication as chat access; it does not
introduce a public search surface. Neither inference nor search sends message
text to a provider.

## Storage and lifecycle

The existing backend SQLite file owns `search_documents`, the external-content
`search_fts` FTS5 table, `search_chunks`, the `search_vectors` vec0 table, and a
small version marker. SQL triggers cover all chat/message writers and cascade
deletes. Title/content changes invalidate old vectors immediately. A revision
check before committing inference prevents concurrent edits/deletes from
resurrecting stale text.

Existing titles and messages are backfilled once at backend startup. Lexical
indexing is transactional with writes. A background task embeds overlapping
~1,000-character passages in small batches on one CPU thread. Per-document
revision/progress and retry times are durable across restarts. Failed documents
retry after 60 seconds without blocking later documents. Changing the index
version invalidates/rebuilds embeddings without removing keyword search.

The encoder loads and warms outside the request/event loop. Queries have a
bounded semantic budget, a small query-vector cache, and keyword fallback while
the model is loading, busy, disabled, or unavailable. The UI shows indexing/loading
state. If the search API itself fails, the palette explicitly reports that only
local title/latest-preview matches are being shown. Requests are debounced and
cancelled when a query, scope, or dialog changes; stale responses are ignored.

Embeddings and indexed text are part of the backend database backup. Deleting a
message/chat removes its indexed text and vector rows in the same transaction.
As with the rest of SQLite, physical reclamation of deleted pages is separate
from logical deletion. Administrative connections that modify the schema or
indexed sources must load the packaged `sqlite_vec` extension first; the backend
loads it before all migrations because SQLite validates vec0 triggers on ALTER. Indexes add disk usage; no external vector service or
PostgreSQL change is required.

## Model provisioning

`services/backend/Dockerfile` downloads the public, MIT-licensed ONNX model at
build time through `search_model.load_encoder`. Its Hugging Face revision is
pinned in `search_index.py` and is part of the index version. The built backend
runs offline inference from that cache; it needs no provider credential and does
not download model weights at startup. CI tests both deterministic index behavior
and actual semantic retrieval with offline-only model loading. `.python-version`
keeps workspace/CI on Python 3.13, matching the runtime images; ONNX Runtime does
not yet publish Python 3.15 wheels.

Backend environment variables (set on the backend container):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SEARCH_SEMANTIC_ENABLED` | `true` | Set `false` for keyword-only operation; FTS5 continues working. |
| `SEARCH_MODEL_CACHE` | `/opt/nautionette-search-model` in Docker; `$DATA_DIR/search-model` otherwise | Pinned model cache location. |
| `SEARCH_MODEL_LOCAL_ONLY` | `true` in Docker; `false` otherwise | Refuse model downloads at runtime. Local development may download the public model once. |

When replacing weights, preprocessing, dimensions or chunking, update
`INDEX_VERSION` and keep the vec0 schema dimensions in sync. Different dimensions
or weights must never share an index. A different model may also need a different
relevance cutoff; the current cutoff has a real-model retrieval/noise regression
test.

## API

`GET /api/search/chats?q=...&limit=30` (authenticated)

- `q`: 1–200 characters; whitespace-only queries return no results.
- `limit`: 1–50 chats, default 30.
- Response: `results` with `chat_id`, `title`, nullable `message_id`, plain-text
  `excerpt`, `match` (`keyword`, `semantic`, `both`) and fusion `score`.
- `semantic`: `state` (`loading`, `ready`, `indexing`, `busy`, `unavailable`,
  `disabled`) and pending document count. Some history may not yet be embedded
  while `indexing`; keyword search still covers all backfilled text.

The diagnostic `/api/chat-inspection/chats` endpoint remains separate, with its
existing literal substring filters and tool/session summaries.

## Tests

```sh
uv run pytest tests/backend/test_chat_search.py
SEARCH_TEST_MODEL_CACHE=/tmp/search-model uv run python -c \
  "from nautionette_backend.search_model import load_encoder; load_encoder('/tmp/search-model')"
SEARCH_TEST_MODEL_CACHE=/tmp/search-model uv run pytest tests/backend/test_chat_search_model.py
cd services/frontend
npm test
npx playwright test tests/command-palette.spec.js tests/hybrid-search.spec.js
```
