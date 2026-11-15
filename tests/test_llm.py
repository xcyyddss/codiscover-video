import json

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from codiscover_video.config import Settings
from codiscover_video.llm import DeepSeekClient, LLMError, usage_scope


class Result(BaseModel):
    value: str


def response(content='{"value":"ok"}', *, reason="stop", status=200):
    return httpx.Response(
        status,
        json={
            "id": "response-fixture",
            "choices": [{"finish_reason": reason, "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        },
    )


def settings():
    return Settings(llm_mode="deepseek", api_key=SecretStr("test-secret-not-for-logs"))


@pytest.mark.asyncio
async def test_json_usage_budget_and_request_contract():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 4096
        return response()

    client = DeepSeekClient(settings(), transport=httpx.MockTransport(handler))
    with usage_scope("job-1", max_calls=1) as scope:
        result = await client.generate(Result, [], task="test")
        assert result.value == "ok"
        with pytest.raises(LLMError, match="预算") as error:
            await client.generate(Result, [], task="test")
        assert error.value.code == "budget_exhausted"
        assert scope.calls == 1
        assert scope.records[0]["trace_id"] == "job-1"
        assert scope.records[0]["usage"]["total_tokens"] == 14
    assert "test-secret" not in json.dumps(client.records)


@pytest.mark.asyncio
async def test_auth_failure_never_retries_or_discloses_body():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="test-secret-not-for-logs")

    client = DeepSeekClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as error:
        await client.generate(Result, [], task="test")
    assert error.value.code == "authentication"
    assert len(calls) == 1
    assert "test-secret" not in str(error.value)
    assert "test-secret" not in json.dumps(client.records)


@pytest.mark.asyncio
async def test_transient_failure_retry_is_bounded(monkeypatch):
    calls = []
    sleeps = []

    async def no_sleep(delay):
        sleeps.append(delay)

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "1000"})

    monkeypatch.setattr("codiscover_video.llm.asyncio.sleep", no_sleep)
    client = DeepSeekClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as error:
        await client.generate(Result, [], task="test")
    assert error.value.code == "rate_limit"
    assert len(calls) == 3
    assert sleeps == [4.0, 4.0]


@pytest.mark.asyncio
async def test_one_schema_repair_then_success():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return response("not json" if len(calls) == 1 else '{"value":"repaired"}')

    client = DeepSeekClient(settings(), transport=httpx.MockTransport(handler))
    result = await client.generate(Result, [{"role": "user", "content": "JSON"}], task="test")
    assert result.value == "repaired"
    assert len(calls) == 2
    assert len(calls[1]["messages"]) == 3


@pytest.mark.asyncio
async def test_schema_repair_stops_after_two_invalid_responses():
    client = DeepSeekClient(settings(), transport=httpx.MockTransport(lambda request: response("{}")))
    with pytest.raises(LLMError) as error:
        await client.generate(Result, [], task="test")
    assert error.value.code == "validation"
    assert len(client.records) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["length", "tool_calls", "content_filter", "aborted"])
async def test_incomplete_or_tool_response_is_not_accepted(reason):
    client = DeepSeekClient(
        settings(), transport=httpx.MockTransport(lambda request: response(reason=reason))
    )
    with pytest.raises(LLMError) as error:
        await client.generate(Result, [], task="test")
    assert error.value.code == "incomplete"
    assert len(client.records) == 1


@pytest.mark.asyncio
async def test_scope_isolation_between_concurrent_tasks():
    import asyncio

    client = DeepSeekClient(settings(), transport=httpx.MockTransport(lambda request: response()))

    async def task(name):
        with usage_scope(name) as scope:
            await client.generate(Result, [], task="test")
            await asyncio.sleep(0)
            return scope

    first, second = await asyncio.gather(task("first"), task("second"))
    assert [r["trace_id"] for r in first.records] == ["first"]
    assert [r["trace_id"] for r in second.records] == ["second"]
