"""Featherless client wrapper.

Featherless exposes an OpenAI-compatible API, so the OpenAI SDK works unchanged
once `base_url` points at Featherless. This module adds the three things the
pipeline actually needs on top of that:

  - a model router (small model for extraction, strong model for synthesis)
  - retry with backoff for cold models (503) and transient 5xx / rate limits
  - a concurrency semaphore matching the Premium 4-slot budget
  - lenient JSON parsing, since not every open model honours response_format
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

BASE_URL = "https://api.featherless.ai/v1"

# Status codes worth retrying: cold model, gateway hiccups, rate limit.
_RETRYABLE = {429, 500, 502, 503, 504}
# Featherless cold-start can take 30-60s. Default request timeout high enough
# that the SDK does not give up before the model is ready.
_DEFAULT_REQUEST_TIMEOUT = 120.0


class FeatherlessError(RuntimeError):
    """Raised when a call fails after exhausting retries, or fails hard."""


class FeatherlessClient:
    """Thin async wrapper around the OpenAI SDK pointed at Featherless."""

    def __init__(
        self,
        api_key: str | None = None,
        concurrency: int | None = None,
        extract_model: str | None = None,
        synthesis_model: str | None = None,
    ) -> None:
        key = api_key or os.getenv("FEATHERLESS_API_KEY")
        if not key:
            raise FeatherlessError(
                "FEATHERLESS_API_KEY is not set. Copy .env.example to .env "
                "and fill it in."
            )
        self._client = AsyncOpenAI(
            base_url=BASE_URL, api_key=key, timeout=_DEFAULT_REQUEST_TIMEOUT
        )

        slots = concurrency or int(os.getenv("FEATHERLESS_CONCURRENCY", "4"))
        # One semaphore models the Premium slot budget. The extraction fan-out
        # acquires it per call; synthesis runs after the fan-out drains, so a
        # large synthesis model never contends with extraction here.
        self._sem = asyncio.Semaphore(slots)

        self.extract_model = (
            extract_model
            or os.getenv("EXTRACT_MODEL", "mistralai/Mistral-Nemo-Instruct-2407")
        )
        self.synthesis_model = (
            synthesis_model
            or os.getenv("SYNTHESIS_MODEL", "deepseek-ai/DeepSeek-V3.2")
        )

    async def chat(
        self,
        messages: list[dict],
        model: str,
        *,
        max_retries: int = 5,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """One chat completion. Returns the message content string.

        Retries cold-model and transient failures with exponential backoff.
        Featherless cold-start can take 30-60s; default 5 retries gives a
        total backoff budget of 2+4+8+16+32 = 62s before raising.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem:
                    resp = await self._client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                return resp.choices[0].message.content or ""
            except APIStatusError as exc:
                status = getattr(exc, "status_code", None)
                if status == 403:
                    raise FeatherlessError(
                        f"Model '{model}' is gated (403). Open its page on "
                        "featherless.ai, click Unlock Model, and accept the "
                        "licence terms."
                    ) from exc
                if status in _RETRYABLE and attempt <= max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise FeatherlessError(
                    f"Featherless call to '{model}' failed: {status} {exc}"
                ) from exc
            except (APIConnectionError, APITimeoutError) as exc:
                if attempt <= max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise FeatherlessError(
                    f"Could not reach Featherless for '{model}': {exc}"
                ) from exc
            except httpx.TimeoutException as exc:
                # The SDK normally wraps this in APITimeoutError, but a raw
                # transport-level timeout can slip through during cold-start.
                if attempt <= max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise FeatherlessError(
                    f"Transport timeout reaching Featherless for '{model}': {exc}"
                ) from exc

    async def chat_json(
        self,
        messages: list[dict],
        model: str,
        *,
        max_retries: int = 3,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> dict:
        """Like `chat`, but parses the response as JSON.

        Open models are inconsistent about returning clean JSON, so this strips
        code fences and falls back to grabbing the outermost `{...}` block.
        """
        raw = await self.chat(
            messages,
            model,
            max_retries=max_retries,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _parse_json(raw)


def _parse_json(raw: str) -> dict:
    """Best-effort JSON extraction from a model response."""
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost brace-delimited span.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise FeatherlessError(
        f"Model did not return parseable JSON. Got: {raw[:200]!r}"
    )
