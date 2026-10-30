"""Real FTS5/sqlite-vec with deterministic inference; these tests need no model."""

import asyncio
import math
import sqlite3
import time

import pytest
from nautionette_backend import chat_search
from nautionette_backend.chat_search import ChatSearch
from nautionette_backend.config import settings
from nautionette_backend.db import _SCHEMA, Database
from nautionette_backend.search_index import DIMENSIONS, INDEX_VERSION, chunks, install


class Encoder:
    def __init__(self):
        self.before_embed = None
        self.fail = False

    def vector(self, text):
        if self.fail:
            raise RuntimeError("no inference")
        # Different words, same concept; no lexical overlap required.
        cost = any(word in text.lower() for word in ("cloud", "expenses", "bill", "infrastructure"))
        vector = [1.0, 0.05] if cost else [0.05, 1.0]
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] + [0.0] * (DIMENSIONS - 2)

    def embed(self, texts, **_):
        if self.before_embed:
            self.before_embed()
        return [self.vector(text) for text in texts]

    def query_embed(self, query):
        yield self.vector(query)


@pytest.fixture
def search(backend, monkeypatch):
    monkeypatch.setattr(settings, "search_semantic_enabled", True)
    from functools import partial

    monkeypatch.setattr(backend.db, "create_chat", partial(backend.db.create_chat, agent_set="default"))
    service = ChatSearch(backend.db)
    service.encoder = Encoder()
    service.state = "ready"
    monkeypatch.setattr(chat_search, "search", service)
    from nautionette_backend.routers import search as router

    monkeypatch.setattr(router, "search", service)
    return service


def index_all(search):
    for _ in range(100):
        if not search.index_one():
            return
    pytest.fail("indexer did not drain")


def test_full_history_keywords_and_snippets(client, backend, search):
    chat = backend.db.create_chat(title="Unrelated title")
    message = backend.db.add_message(chat["id"], "user", "ordinary text " * 1000 + "the café NeedleMarker")
    backend.db.add_message(chat["id"], "assistant", "latest message with no match")
    backend.db.add_message(chat["id"], "system", "PRIVATE_SYSTEM_PROMPT")
    response = client.get("/api/search/chats", params={"q": "cafe needle"})
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["chat_id"] == chat["id"]
    assert result["message_id"] == message["id"]
    assert "NeedleMarker" in result["excerpt"]
    assert len(result["excerpt"]) <= 500
    assert result["match"] == "keyword"
    # Metadata/prompts/tools are not indexed.
    assert search.keyword("PRIVATE_SYSTEM_PROMPT") == []
    backend.db.add_message(chat["id"], "assistant", "visible", {"steps": [{"text": "SECRET_TOOL_TEXT"}]})
    assert search.keyword("SECRET_TOOL_TEXT") == []


def test_literal_query_prefix_and_title_weighting(backend, search):
    body = backend.db.create_chat(title="Other")
    title = backend.db.create_chat(title="Power planning")
    backend.db.add_message(body["id"], "user", "power planning")
    for _ in range(10):
        backend.db.add_message(body["id"], "assistant", "power")
    assert [row["chat_id"] for row in search.keyword("pow")] == [title["id"], body["id"]]
    for query in ['" OR * - : ()', "!!!", "a' OR 1=1 --", "power:NEAR(test)", "_", "你好"]:
        assert isinstance(search.keyword(query), list)


async def test_semantic_only_hits_and_hybrid_chat_level_fusion(backend, search):
    costs = backend.db.create_chat(title="Budget discussion")
    message = backend.db.add_message(costs["id"], "user", "We should cut our cloud costs")
    other = backend.db.create_chat(title="Birthday cake")
    backend.db.add_message(other["id"], "assistant", "Chocolate cake recipe")
    index_all(search)
    assert search.keyword("lower infrastructure bill") == []
    results = await search.search("lower infrastructure bill")
    assert [row["chat_id"] for row in results["results"]] == [costs["id"]]
    assert results["results"][0]["message_id"] == message["id"]
    assert results["results"][0]["match"] == "semantic"
    assert results["semantic"] == {"state": "ready", "pending": 0}
    # Both rankings contribute once per chat, regardless of chunk count.
    results = await search.search("cloud")
    assert len(results["results"]) == 1
    assert results["results"][0]["match"] == "both"
    assert results["results"][0]["score"] == pytest.approx(2 / 61)


def test_update_role_change_and_cascade_delete_invalidate_vectors(backend, search):
    chat = backend.db.create_chat(title="OldTitle")
    message = backend.db.add_message(chat["id"], "user", "cloud costs")
    index_all(search)
    backend.db.update_chat(chat["id"], {"title": "NewTitle"})
    assert search.keyword("OldTitle") == []
    assert search.keyword("NewTitle")
    backend.db.execute("UPDATE messages SET content = ? WHERE id = ?", ("cake", message["id"]))
    assert search.keyword("cloud") == []
    assert search.semantic("infrastructure") == []
    index_all(search)
    backend.db.execute("UPDATE messages SET role = 'system' WHERE id = ?", (message["id"],))
    assert search.keyword("cake") == []
    backend.db.execute("UPDATE messages SET role = 'assistant' WHERE id = ?", (message["id"],))
    assert search.keyword("cake")
    index_all(search)
    backend.db.delete_chat(chat["id"])
    assert backend.db.one("SELECT COUNT(*) AS n FROM search_documents")["n"] == 0
    assert backend.db.one("SELECT COUNT(*) AS n FROM search_chunks")["n"] == 0
    assert backend.db.one("SELECT COUNT(*) AS n FROM search_vectors")["n"] == 0
    assert search.keyword("NewTitle") == []


@pytest.mark.parametrize("delete", [False, True])
def test_edit_delete_during_inference_cannot_resurrect_stale_data(backend, search, delete):
    chat = backend.db.create_chat(title="Budget")
    index_all(search)
    message = backend.db.add_message(chat["id"], "user", "cloud costs")

    def mutate():
        if delete:
            backend.db.delete_chat(chat["id"])
        else:
            backend.db.execute("UPDATE messages SET content = 'cake' WHERE id = ?", (message["id"],))

    search.encoder.before_embed = mutate
    assert search.index_one()
    assert search.semantic("infrastructure") == []
    search.encoder.before_embed = None
    index_all(search)
    assert search.keyword("cloud") == []


def test_failed_document_retries_without_blocking_others(backend, search):
    backend.db.create_chat(title="Budget")
    search.encoder.fail = True
    assert search.index_one()
    assert not search.index_one()  # Backoff survives worker/restart.
    doc = backend.db.one("SELECT * FROM search_documents")
    assert doc["indexed_revision"] == 0 and doc["retry_after"] > time.time()
    search.encoder.fail = False
    backend.db.create_chat(title="Later")
    assert search.index_one()
    backend.db.execute("UPDATE search_documents SET retry_after = 0")
    index_all(search)
    assert backend.db.one("SELECT COUNT(*) AS n FROM search_chunks")["n"] == 2


async def test_keyword_fallback_loading_busy_disabled_and_failure(backend, search, monkeypatch):
    backend.db.create_chat(title="Needle")
    index_all(search)
    search.encoder.fail = True
    response = await search.search("Needle")
    assert response["results"] and response["semantic"]["state"] == "unavailable"
    search._encoder_lock.acquire()
    try:
        response = await search.search("Needle")
        assert response["results"] and response["semantic"]["state"] == "busy"
    finally:
        search._encoder_lock.release()
    search.encoder = None
    search.state = "loading"
    assert (await search.search("Needle"))["semantic"]["state"] == "loading"
    monkeypatch.setattr(settings, "search_semantic_enabled", False)
    search.state = "disabled"
    assert (await search.search("Needle"))["semantic"]["state"] == "disabled"


async def test_query_timeout_does_not_hide_keywords(backend, search, monkeypatch):
    backend.db.create_chat(title="Needle")

    def slow(_):
        time.sleep(0.9)
        return []

    monkeypatch.setattr(search, "semantic", slow)
    response = await search.search("Needle")
    assert response["results"] and response["semantic"]["state"] == "busy"


def test_auth_bounds_and_limit(client, anonymous, backend, search):
    assert anonymous.get("/api/search/chats?q=test").status_code == 401
    for params in [{}, {"q": ""}, {"q": "x" * 201}, {"q": "x", "limit": 0}, {"q": "x", "limit": 51}]:
        assert client.get("/api/search/chats", params=params).status_code == 422
    assert client.get("/api/search/chats?q=%20%20").json()["results"] == []
    for i in range(3):
        backend.db.create_chat(title=f"Needle {i}")
    assert len(client.get("/api/search/chats?q=Needle&limit=2").json()["results"]) == 2


def test_legacy_backfill_restart_and_model_version_rebuild(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO chats(id,title,created_at,updated_at) VALUES ('old','Legacy title',1,1)")
    conn.execute(
        "INSERT INTO messages(id,chat_id,role,content,created_at) "
        "VALUES ('msg','old','user','ancient phrase',1)"
    )
    conn.commit()
    conn.close()
    database = Database(path)
    service = ChatSearch(database)
    service.encoder = Encoder()
    assert service.keyword("ancient")[0]["message_id"] == "msg"
    index_all(service)
    ids = database.query("SELECT id, indexed_revision FROM search_documents")
    database._conn.close()
    database = Database(path)
    service = ChatSearch(database)
    service.encoder = Encoder()
    assert database.query("SELECT id, indexed_revision FROM search_documents") == ids
    with database.transaction() as conn:
        install(conn)
    assert database.query("SELECT id, indexed_revision FROM search_documents") == ids
    assert database.one("SELECT COUNT(*) AS n FROM search_vectors")["n"] == 2
    database.execute("UPDATE search_index_version SET version = 'previous-model'")
    with database.transaction() as conn:
        install(conn)
    assert database.one("SELECT version FROM search_index_version")["version"] == INDEX_VERSION
    assert database.one("SELECT COUNT(*) AS n FROM search_vectors")["n"] == 0
    assert service.keyword("ancient")
    index_all(service)
    assert database.one("SELECT COUNT(*) AS n FROM search_vectors")["n"] == 2


def test_chunks_preserve_long_text_with_overlap():
    assert list(chunks("  ")) == []
    text = "x" * 3501
    passages = list(chunks(text))
    assert all(0 < len(part) <= 1000 for part in passages)
    assert sum(map(len, passages)) - 150 * (len(passages) - 1) == len(text)
    assert list(chunks("tiny message")) == ["tiny message"]


async def test_worker_load_failure_is_retried_without_blocking_event_loop(search, monkeypatch):
    search.encoder = None
    failures = []

    def fail():
        failures.append(True)
        raise RuntimeError("model missing")

    monkeypatch.setattr(search, "load", fail)
    task = asyncio.create_task(search.run())
    for _ in range(100):
        if failures and search.state == "unavailable":
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert failures and search.state == "unavailable"
