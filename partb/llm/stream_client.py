"""Stream tokens from the unified load balancer (/generate), with LiteLLM
as fallback if the LB itself is unreachable."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from partb.logger import time_it, async_time_it, logger

import time

from partb.config import (
    LITELLM_API_KEY,
    LITELLM_BASE_URL,
    LITELLM_MODEL,
    OLLAMA_LB_URL,
)


@time_it
def _prompt_from_messages(messages: list[dict[str, str]]) -> str:
    """Ollama /api/generate expects a single prompt string."""
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"System:\n{content}")
        elif role == "user":
            parts.append(f"User:\n{content}")
        else:
            parts.append(f"Assistant:\n{content}")
    return "\n\n".join(parts)


@async_time_it
async def stream_llm(
    messages: list[dict[str, str]],
    mode: str,
    cfg: dict[str, Any],
) -> AsyncIterator[dict]:
    timeout = cfg.get("llm_timeout_s", 600.0)
    prompt = _prompt_from_messages(messages)

    # Try the unified LB first
    try:
        async for ev in _stream_via_lb(prompt, mode, timeout):
            yield ev
        return
    except Exception as e:
        logger.warning("[LB] Failed, falling back to LiteLLM: %s", e)

    # Fallback to LiteLLM
    try:
        async for ev in _stream_litellm(messages, timeout):
            yield ev
        return
    except Exception as e:
        logger.warning("[LITELLM] All fallbacks exhausted, last error: %s", e)
        yield {"type": "error", "message": f"All LLM backends failed: {e}"}


@async_time_it
async def _stream_via_lb(
    prompt: str,
    mode: str,
    timeout: float,
) -> AsyncIterator[dict]:
    """Single call to the unified LB's /generate. The LB owns allocation,
    queueing, and (for Pahal) preemption — this client just sends the
    request and reads the resulting NDJSON stream. No separate allocate
    call, no direct GPU connection, no /release_server call: the LB frees
    the slot itself the moment this stream ends or errors out."""
    url = f"{OLLAMA_LB_URL.rstrip('/')}/generate"
    t0 = time.perf_counter()
    t_first_token = None
    token_count = 0
    char_count = 0

    body = {
        "project": "krag",
        "mode": mode,                    # "fast" | "balanced" | "deep"
        "prompt": prompt,
        "options": {"stream": True},
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 65)) as client:
        # +65s headroom over llm_timeout_s: the LB may hold this connection
        # open for up to MAX_WAIT_SEC (60s) in its wait queue before a slot
        # is even assigned, on top of actual generation time.
        try:
            async with client.stream("POST", url, json=body) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    err_text = err.decode(errors="replace")[:500]
                    logger.error("[LB] HTTP error | status=%s | body=%s", resp.status_code, err_text)
                    yield {"type": "error", "message": f"LB HTTP {resp.status_code}: {err_text}"}
                    return

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Control messages injected by the LB itself (not raw Ollama output)
                    if data.get("type") == "overridden":
                        logger.info("[LB] Request overridden by higher-priority project, retrying: %s", data.get("message"))
                        yield {"type": "overridden", "message": data.get("message", "Overridden — retrying.")}
                        continue
                    if data.get("type") == "error":
                        logger.error("[LB] Error from LB: %s", data.get("message"))
                        yield {"type": "error", "message": data.get("message", "Unknown LB error")}
                        return

                    # Otherwise it's a raw Ollama /api/generate line
                    token = data.get("response") or ""
                    if token:
                        token_count += 1
                        char_count += len(token)
                        if token_count == 1:
                            t_first_token = time.perf_counter()
                            logger.info("[LB] First token | cold_startup_time=%.2fs", t_first_token - t0)
                        yield {"type": "token", "content": token}
                    if data.get("done"):
                        break

                t_end = time.perf_counter()
                if t_first_token:
                    logger.info(
                        "[LB] Stream complete | tokens=%s | chars=%s | cold_startup_time=%.2fs | response_time=%.2fs",
                        token_count, char_count, t_first_token - t0, t_end - t_first_token,
                    )
                else:
                    logger.info("[LB] Stream complete | tokens=%s | chars=%s | elapsed=%.2fs", token_count, char_count, t_end - t0)

        except httpx.TimeoutException:
            logger.error("[LB] Timed out after %.2fs", time.perf_counter() - t0)
            yield {"type": "error", "message": "LB request timed out."}
        except Exception as e:
            logger.exception("[LB] Stream error")
            yield {"type": "error", "message": f"LB error: {e}"}


@async_time_it
async def _stream_litellm(
    messages: list[dict[str, str]],
    timeout: float,
) -> AsyncIterator[dict]:
    url = f"{LITELLM_BASE_URL}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"

    body = {
        "model": LITELLM_MODEL,
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        try:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    err = await resp.aread()
                    yield {
                        "type": "error",
                        "message": f"LLM HTTP {resp.status_code}: {err.decode(errors='replace')[:500]}",
                    }
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].lstrip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        yield {"type": "token", "content": content}
        except httpx.TimeoutException:
            yield {"type": "error", "message": f"LLM timeout after {timeout}s"}
        except Exception as e:
            yield {"type": "error", "message": f"LLM stream error: {e}"}

