"""Tests for alert deduplication against a fake Redis client."""
from datetime import timedelta

import pytest

from src.services.ingestion.deduplicator import AlertDeduplicator


class FakeRedis:
    """Minimal async fake standing in for redis.asyncio.Redis."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def exists(self, key):
        return key in self.store

    async def expire(self, key, ttl):
        return key in self.store


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(AlertDeduplicator, "_redis_client", fake)
    yield fake
    AlertDeduplicator._redis_client = None


async def test_new_fingerprint_is_not_a_duplicate(fake_redis):
    is_duplicate, existing_id = await AlertDeduplicator.check_duplicate("fp-1")

    assert is_duplicate is False
    assert existing_id is None


async def test_registered_fingerprint_is_detected_as_duplicate(fake_redis):
    await AlertDeduplicator.register_fingerprint("fp-1", "incident-123")

    is_duplicate, existing_id = await AlertDeduplicator.check_duplicate("fp-1")

    assert is_duplicate is True
    assert existing_id == "incident-123"


async def test_removed_fingerprint_is_no_longer_a_duplicate(fake_redis):
    await AlertDeduplicator.register_fingerprint("fp-1", "incident-123")
    await AlertDeduplicator.remove_fingerprint("fp-1")

    is_duplicate, _ = await AlertDeduplicator.check_duplicate("fp-1")

    assert is_duplicate is False


async def test_register_fingerprint_uses_default_ttl(fake_redis, monkeypatch):
    captured = {}

    async def fake_set(key, value, ex=None):
        captured["ex"] = ex

    monkeypatch.setattr(fake_redis, "set", fake_set)

    await AlertDeduplicator.register_fingerprint("fp-1", "incident-123")

    assert captured["ex"] == int(timedelta(hours=4).total_seconds())
