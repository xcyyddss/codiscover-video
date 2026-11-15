"""Bounded DeepSeek JSON client. Credentials and response bodies never enter call records."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """A safe-to-display provider failure, without response text or credentials."""

    def __init__(self, message: str, *, code: str = "llm_error", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class UsageScope:
    """A task-local budget: each HTTP attempt consumes one unit, including retries."""

    trace_id: str
    max_calls: int = 24
    records: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0


_scope: ContextVar[UsageScope | None] = ContextVar("codiscover_video_llm_scope", default=None)


@contextmanager
def usage_scope(trace_id: str, max_calls: int = 24) -> Iterator[UsageScope]:
    scope = UsageScope(trace_id=trace_id, max_calls=max_calls)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


class DeepSeekClient:
    """Chat Completions with schema validation, one repair, and finite transient retries.

    Thinking is disabled for bounded extraction tasks; no reasoning content is persisted.
    Model-generated tool calls are never executed by this client.
    """

    def __init__(self, settings: Any, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.records: list[dict[str, Any]] = []
        if settings.model != "deepseek-flash":
            raise ValueError("CoDiscover Video 的模型固定为 deepseek-flash。")

    def _record(self, record: dict[str, Any]) -> None:
        scope = _scope.get()
        if scope:
            record = {**record, "trace_id": scope.trace_id}
            scope.records.append(record)
        self.records.append(record)
        # Operational metadata is bounded even when the local service runs for weeks.
        if len(self.records) > 1000:
            del self.records[:-1000]

    async def _request(self, messages: list[dict[str, Any]], task: str) -> str:
        key_value = self.settings.api_key
        key = key_value.get_secret_value() if hasattr(key_value, "get_secret_value") else str(key_value or "")
        if not key:
            raise LLMError("未配置 DEEPSEEK_API_KEY。", code="authentication")
        body = {
            "model": "deepseek-flash",
            "messages": messages,
            "thinking": {"type": "disabled"},
            "max_tokens": self.settings.llm_max_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        endpoint = self.settings.api_base.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout, transport=self.transport, follow_redirects=False
        ) as client:
            for attempt in range(3):
                scope = _scope.get()
                if scope:
                    if scope.calls >= scope.max_calls:
                        raise LLMError("本轮模型调用预算已用完。", code="budget_exhausted")
                    scope.calls += 1
                started = monotonic()
                record: dict[str, Any] = {"task": task, "attempt": attempt + 1, "model": "deepseek-flash"}
                try:
                    response = await client.post(
                        endpoint, headers={"Authorization": "Bearer " + key}, json=body
                    )
                except httpx.RequestError as exc:
                    record.update(
                        status="timeout" if isinstance(exc, httpx.TimeoutException) else "network_error"
                    )
                    record["elapsed_ms"] = round((monotonic() - started) * 1000)
                    self._record(record)
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise LLMError("模型服务连接失败或超时；本轮已停止重试。", code="connection") from None
                record.update(
                    http_status=response.status_code, elapsed_ms=round((monotonic() - started) * 1000)
                )
                if response.status_code != 200:
                    record["status"] = "http_error"
                    self._record(record)
                    retryable = response.status_code in {408, 429, 500, 502, 503, 504}
                    if retryable and attempt < 2:
                        retry_after = response.headers.get("retry-after", "")
                        delay = min(float(retry_after), 4.0) if retry_after.isdigit() else 0.5 * (2**attempt)
                        await asyncio.sleep(delay)
                        continue
                    explanations = {
                        401: ("authentication", "DeepSeek API Key 无效或已失效（401）。"),
                        402: ("account", "DeepSeek 账户暂时无法继续请求（402）。"),
                        429: ("rate_limit", "DeepSeek 请求限流；有限重试后仍未恢复（429）。"),
                    }
                    code, message = explanations.get(
                        response.status_code,
                        (
                            "http_error",
                            f"DeepSeek 请求失败（HTTP {response.status_code}）；未保存服务端响应正文。",
                        ),
                    )
                    raise LLMError(message, code=code, status=response.status_code)
                try:
                    payload = response.json()
                    choice = payload["choices"][0]
                    reason = choice.get("finish_reason")
                    if reason not in (
                        "stop",
                        "length",
                        "tool_calls",
                        "content_filter",
                        "insufficient_system_resource",
                        "aborted",
                    ):
                        reason = "unknown"
                    content = choice["message"].get("content")
                    record.update(
                        status="success" if reason == "stop" else "incomplete",
                        response_id=str(payload.get("id", "")).replace(key, "[redacted]")[:160],
                        finish_reason=reason,
                    )
                    usage = payload.get("usage") or {}
                    record["usage"] = {
                        k: v
                        for k, v in usage.items()
                        if k
                        in {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens"}
                        and isinstance(v, int)
                        and not isinstance(v, bool)
                        and v >= 0
                    }
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    record["status"] = "invalid_response"
                    self._record(record)
                    raise LLMError("模型服务返回了无法识别的响应结构。", code="invalid_response") from None
                self._record(record)
                if reason != "stop":
                    raise LLMError(
                        "模型未完整结束输出；本轮结果未采用。",
                        code="incomplete",
                    )
                if not isinstance(content, str) or not content.strip():
                    raise LLMError("模型返回了空的最终回答。", code="invalid_response")
                return content
        raise LLMError("模型请求未完成。")  # Defensive; all loop exits above return or raise.

    async def generate(
        self,
        schema: type[T],
        messages: list[dict[str, Any]],
        *,
        task: str,
        validator: Callable[[T], T] | None = None,
    ) -> T:
        """Validate JSON; allow one correction for syntax/schema/business constraints.

        Never include raw validation exceptions in the repair prompt: they can echo
        arbitrary external data. The original evidence remains available in messages.
        """
        working = list(messages)
        for repair in range(2):
            raw = await self._request(working, task + (".repair" if repair else ""))
            try:
                result = schema.model_validate_json(raw)
                return validator(result) if validator else result
            except (ValidationError, ValueError):
                if repair:
                    raise LLMError(
                        "模型两次返回的 JSON 未通过结构或证据校验，本轮未采用。", code="validation"
                    ) from None
                working.extend(
                    [
                        {"role": "assistant", "content": raw[:24000]},
                        {
                            "role": "user",
                            "content": (
                                "上个回答未通过 JSON 结构或证据约束。请重新核对给定资料，仅返回符合要求的 JSON。"
                                "不要新增来源、不输出 Markdown；引用必须来自提供的原文，引用 ID 必须在候选列表内。"
                            ),
                        },
                    ]
                )
        raise LLMError("模型结构校验失败。", code="validation")
