"""Opt-in real inference contract; CI prefetches the pinned public model once."""

import os

import httpx
import pytest
from nautionette_backend.chat_search import ChatSearch
from nautionette_backend.search_model import load_encoder

CACHE = os.environ.get("SEARCH_TEST_MODEL_CACHE", "")
pytestmark = pytest.mark.skipif(not CACHE, reason="set SEARCH_TEST_MODEL_CACHE to a prefetched model cache")


async def test_real_model_offline_semantic_retrieval(backend, monkeypatch):
    def no_network(*_, **__):
        pytest.fail("inference/offline model loading attempted a network request")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    service = ChatSearch(backend.db)
    service.encoder = load_encoder(CACHE, local_files_only=True)
    service.state = "ready"
    budget = backend.db.create_chat(agent_set="default", title="Monthly planning")
    message = backend.db.add_message(
        budget["id"], "user", "Cut our AWS cloud costs by shutting down idle virtual machines."
    )
    dinner = backend.db.create_chat(agent_set="default", title="Dinner")
    backend.db.add_message(dinner["id"], "user", "Bake a chocolate birthday cake with vanilla frosting.")
    while service.index_one():
        pass
    query = "reducing infrastructure expenses"
    assert service.keyword(query) == []
    results = (await service.search(query))["results"]
    assert len(results) == 1
    assert results[0]["chat_id"] == budget["id"]
    assert results[0]["message_id"] == message["id"]
    assert results[0]["match"] == "semantic"
    assert (await service.search("different frosting recipe"))["results"][0]["chat_id"] == dinner["id"]
    assert (await service.search("needle"))["results"] == []
