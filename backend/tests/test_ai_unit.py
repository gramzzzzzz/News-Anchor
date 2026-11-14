import httpx
import pytest

from app.ai import validate as V
from app.ai.providers import pick_model
from app.ai.router import extract_json



def test_helpers():
    assert extract_json('Sure! ```json\n{"a": 1}\n``` done') == {"a": 1}
    assert extract_json('{"a": {"b": 2}}') == {"a": {"b": 2}}
    for bad in ("no json here", "[1, 2]"):
        with pytest.raises(ValueError):
            extract_json(bad)
    assert pick_model(["whisper-large-v3", "llama-3.3-70b-versatile", "llama-guard-4-12b", "text-embedding-3"]) == "llama-3.3-70b-versatile"
    assert pick_model(["paid/x", "google/gemma-4-31b-it:free", "foo/bar:free"], free_only=True) == "google/gemma-4-31b-it:free"
    assert pick_model(["whisper", "tts-1"]) is None


def test_validators_directly():
    obj, rep = V.validate_story({"summary": [{"text": "It rose 12%.", "cite": ["S1"]}], "established": [{"text": "x", "cite": []}], "confidence": "certain"}, {"S1": "It rose 12 percent"})
    assert obj["confidence"] == "low" and obj["established"] == [] and rep["dropped_uncited"] == 1 and rep["numbers_not_found"] == 0
    with pytest.raises(ValueError):
        V.validate_story({"summary": [{"text": "no cites", "cite": []}]}, {"S1": "t"})
    with pytest.raises(ValueError):
        V.validate_article({"summary": ""}, ["p"])
    assert V.numbers_in("Rs 4,200 crore and 3 people, 12.5% in 2026") == {"4200", "3", "12.5"}
    assert V.numbers_in("nine bidders") == {"9"}


def test_classification_of_provider_errors():
    from app.ai.providers import classify
    h = lambda **k: httpx.Headers(k)
    e = classify(429, '{"error":{"message":"Daily token quota exceeded. Retry after 55 seconds."}}', h(**{"retry-after": "55"}))
    assert (e.kind, e.retry_after) == ("rate", 55.0)                                   # told when to come back: short wait, not a day
    assert classify(429, "Rate limit reached: requests per day (RPD)", h()).kind == "quota"
    assert classify(429, "slow down", h()).kind == "rate"
    assert classify(402, '{"error":{"message":"Free limit reached: 1 reply every 45 seconds per IP. Retry after 29s"}}', h()).retry_after == 29.0
    assert classify(401, "", h()).kind == "auth" and classify(403, "", h()).kind == "auth"
    assert classify(404, "model not found", h()).kind == "model" and classify(400, "The model `x` does not exist", h()).kind == "model"
    assert classify(503, "", h()).kind == "server" and classify(400, "bad request", h()).kind == "bad_output"

