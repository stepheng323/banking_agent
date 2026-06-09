"""HTTP timing hooks for LLM provider calls."""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Any

import httpx

LLMHttpRecord = dict[str, Any]

_LLM_HTTP_RECORDS: ContextVar[list[LLMHttpRecord] | None] = ContextVar("llm_http_records", default=None)


def start_llm_http_recording() -> Token[list[LLMHttpRecord] | None]:
    return _LLM_HTTP_RECORDS.set([])


def stop_llm_http_recording(token: Token[list[LLMHttpRecord] | None]) -> tuple[LLMHttpRecord, ...]:
    records = tuple(_finalize_record(record) for record in (_LLM_HTTP_RECORDS.get() or ()))
    _LLM_HTTP_RECORDS.reset(token)
    return records


async def record_llm_http_request(request: httpx.Request) -> None:
    records = _LLM_HTTP_RECORDS.get()
    if records is None:
        return
    start = time.perf_counter()
    record: LLMHttpRecord = {
        "_start": start,
        "client_http_method": request.method,
        "client_http_host": request.url.host,
        "client_http_path": request.url.path,
    }
    request.extensions["llm_http_record"] = record
    records.append(record)


async def record_llm_http_response(response: httpx.Response) -> None:
    record = response.request.extensions.get("llm_http_record")
    if not isinstance(record, dict):
        return
    start = record.get("_start")
    if isinstance(start, float):
        record["client_http_response_headers_ms"] = round((time.perf_counter() - start) * 1000, 2)
    request_id = _request_id_from_headers(response.headers)
    if request_id:
        record["provider_request_id"] = request_id
    record["client_http_status_code"] = response.status_code


def summarize_llm_http_records(records: tuple[LLMHttpRecord, ...]) -> dict[str, Any]:
    if not records:
        return {}
    response_header_latencies = [
        float(record["client_http_response_headers_ms"])
        for record in records
        if isinstance(record.get("client_http_response_headers_ms"), int | float)
    ]
    total_latencies = [
        float(record["client_http_total_ms"])
        for record in records
        if isinstance(record.get("client_http_total_ms"), int | float)
    ]
    fields: dict[str, Any] = {
        "client_http_request_count": len(records),
    }
    last = records[-1]
    for key in ("client_http_method", "client_http_host", "client_http_path", "client_http_status_code"):
        if last.get(key) is not None:
            fields[key] = last[key]
    if response_header_latencies:
        fields["client_http_response_headers_ms"] = round(max(response_header_latencies), 2)
    if total_latencies:
        fields["client_http_total_ms"] = round(max(total_latencies), 2)
    request_id = next(
        (record.get("provider_request_id") for record in reversed(records) if record.get("provider_request_id")),
        None,
    )
    if isinstance(request_id, str):
        fields["provider_request_id"] = request_id
    return fields


def llm_http_event_hooks() -> dict[str, list[Any]]:
    return {
        "request": [record_llm_http_request],
        "response": [record_llm_http_response],
    }


def build_llm_http_async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        event_hooks=llm_http_event_hooks(),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )


def _finalize_record(record: Mapping[str, Any]) -> LLMHttpRecord:
    finalized = dict(record)
    start = finalized.pop("_start", None)
    if isinstance(start, float):
        finalized["client_http_total_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return {key: value for key, value in finalized.items() if value is not None}


def _request_id_from_headers(headers: httpx.Headers) -> str | None:
    for key in ("x-request-id", "openai-request-id", "x-openai-request-id", "request-id"):
        value = headers.get(key)
        if value:
            return value
    return None
