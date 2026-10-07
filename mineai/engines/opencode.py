"""Opencode Go engine (opencode.ai/zen/go) — OpenAI-compatible chat completions.

Three relay quirks shape this module:

* every request must carry ``x-opencode-session``; without it the relay answers
  HTTP 400 ``MissingSessionID`` and nothing gets translated;
* clients are asked to identify themselves with a real User-Agent, otherwise the
  traffic is treated as abuse;
* part of the catalog (Qwen/MiniMax/Claude on ``/messages``, GPT/Grok/Muse-Spark
  on ``/responses``) does not speak chat completions at all, so those ids are
  hidden from the model picker by default.

Translation is a batch job, not a conversation: one stable session id per install
is generated and stored, which keeps the relay's prompt cache warm between runs.
"""

from __future__ import annotations

import logging
import uuid

import requests

from mineai.constants import (
    DEFAULT_OPENCODE_GO_MODEL,
    OPENCODE_CHAT_UNSUPPORTED_PREFIXES,
    OPENCODE_GO_API,
    OPENCODE_USER_AGENT,
)
from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.engines.llm_common import BatchLlmEngine


logger = logging.getLogger(__name__)

# "Thinking" models spend the same token budget on reasoning as on the answer, so
# a batch that fits in 4k normally would be truncated mid-JSON at effort >= low.
REASONING_TOKEN_HEADROOM = 2
PROBE_MAX_TOKENS = 8


def is_chat_compatible(model_id: str) -> bool:
    """False for catalog ids served over the Anthropic or Responses wire."""
    lowered = (model_id or "").strip().lower()
    return bool(lowered) and not lowered.startswith(OPENCODE_CHAT_UNSUPPORTED_PREFIXES)


def normalize_opencode_api_url(api_url: str) -> str:
    """Normalize any user-supplied endpoint into a chat/completions URL."""
    value = (api_url or "").strip().rstrip("/") or OPENCODE_GO_API
    if value.endswith("/chat/completions"):
        return value
    return f"{value}/chat/completions"


def _models_url(api_url: str) -> str:
    value = (api_url or "").strip().rstrip("/") or OPENCODE_GO_API
    for suffix in ("/chat/completions", "/completions"):
        if value.endswith(suffix):
            return f"{value[: -len(suffix)]}/models"
    return f"{value}/models"


def ensure_session_id(config) -> str:
    """Return the persisted relay session key, creating it on first use."""
    session_id = str(config.get("OPENCODE", "session_id") or "").strip()
    if not session_id:
        session_id = f"mineai-{uuid.uuid4().hex[:16]}"
        config.set("OPENCODE", "session_id", session_id)
    return session_id


def _headers(api_key: str, *, session_id: str = "", user_agent: str = OPENCODE_USER_AGENT) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": (user_agent or "").strip() or OPENCODE_USER_AGENT,
    }
    token = (api_key or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id.strip():
        headers["x-opencode-session"] = session_id.strip()
    return headers


def _catalog_from(payload) -> list[str]:
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return sorted(
        {
            item["id"].strip()
            for item in data
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"].strip()
        }
    )


def list_opencode_models(
    api_url: str,
    *,
    api_key: str = "",
    timeout: int = 15,
    session=None,
) -> list[str]:
    """Return the live Opencode Go catalog (sorted model ids).

    The catalog endpoint is public, so the list alone does not prove the key.
    """
    client = session or requests.Session()
    response = client.get(
        _models_url(api_url),
        headers=_headers(api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    return _catalog_from(response.json())


def probe_opencode(
    api_url: str,
    *,
    api_key: str = "",
    model: str = "",
    reasoning_effort: str = "",
    session_id: str = "",
    timeout: int = 30,
    session=None,
) -> list[str]:
    """Fetch the catalog, then prove key + endpoint + model with a tiny chat call.

    Mirrors ``list_*_models`` of the local providers: it raises on failure so the
    dialog can report the reason, and returns the catalog on success.  The chat
    call only runs when a key is present, because the catalog itself is public.
    """
    client = session or requests.Session()
    catalog = list_opencode_models(api_url, api_key=api_key, timeout=timeout, session=client)

    token = (api_key or "").strip()
    if token:
        _probe_chat(
            client,
            api_url,
            token,
            model,
            reasoning_effort=reasoning_effort,
            session_id=session_id,
            timeout=timeout,
        )
    return catalog


def _probe_chat(
    client,
    api_url: str,
    api_key: str,
    model: str,
    *,
    reasoning_effort: str = "",
    session_id: str = "",
    timeout: int = 30,
) -> None:
    """One minimal completion; raises a readable error when the key/model is wrong."""
    chosen = (model or "").strip() or DEFAULT_OPENCODE_GO_MODEL
    payload = {
        "model": chosen,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
    }
    effort = (reasoning_effort or "").strip().lower()
    if effort and effort != "auto":
        payload["reasoning_effort"] = effort

    response = client.post(
        normalize_opencode_api_url(api_url),
        headers=_headers(
            api_key,
            session_id=session_id or f"mineai-probe-{uuid.uuid4().hex[:8]}",
        ),
        json=payload,
        timeout=timeout,
    )
    if response.status_code == 401:
        raise requests.HTTPError(
            "401 — неверный или отозванный API-ключ Opencode Go",
            response=response,
        )
    response.raise_for_status()


class OpencodeGoEngine(BatchLlmEngine):
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_OPENCODE_GO_MODEL,
        *,
        api_url: str = OPENCODE_GO_API,
        prompt_type: str = "mods",
        mode: str = "safe",
        context: str = "",
        reasoning_effort: str = "medium",
        session_id: str = "",
        retries: int = 3,
        session=None,
        user_agent: str = OPENCODE_USER_AGENT,
    ) -> None:
        self.api_url = normalize_opencode_api_url(api_url)
        self.api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_OPENCODE_GO_MODEL).strip()
        self.reasoning_effort = (reasoning_effort or "").strip().lower()
        self.session_id = (session_id or "").strip() or f"mineai-{uuid.uuid4().hex[:16]}"
        self.user_agent = (user_agent or "").strip() or OPENCODE_USER_AGENT
        self.session = session or requests.Session()
        self._compat: dict[str, bool] = {}
        self._should_continue = None
        self._on_log = None
        super().__init__(
            mode=mode,
            context=context,
            prompt_type=prompt_type,
            call_api=self._request,
            label="Opencode Go",
            retries=retries,
        )

    def translate_batch(self, items, target_lang, callbacks):
        self._should_continue = callbacks.should_run
        self._on_log = callbacks.on_log
        try:
            return super().translate_batch(items, target_lang, callbacks)
        except RequestCancelled:
            return {}
        finally:
            self._should_continue = None
            self._on_log = None

    def _payload(self, prompt: str, max_tokens: int) -> dict:
        budget = max(1, int(max_tokens))
        if self.reasoning_effort and self.reasoning_effort != "none":
            budget *= REASONING_TOKEN_HEADROOM
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 1.0 if self._compat.get("temperature_one") else 0.1,
            "max_tokens": budget,
            "stream": False,
        }
        if not self._compat.get("drop_thinking") and self.reasoning_effort and self.reasoning_effort != "auto":
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    @staticmethod
    def _compat_adaptation(exc: requests.HTTPError) -> str | None:
        """Learn the per-model workaround for a 400 on the standard payload.

        Some catalog models reject the request shape that fits the rest of the
        catalog: ``kimi-k2.7-code`` answers ``invalid thinking: only type=enabled
        is allowed`` for a plain ``reasoning_effort`` and ``invalid temperature:
        only 1 is allowed`` for our low temperature.  Without this the whole
        batch fails and every failing string is retried one by one, which looks
        exactly like "translation is stuck".
        """
        response = exc.response
        if response is None or response.status_code != 400:
            return None
        detail = (response.text or "").casefold()
        if "invalid thinking" in detail:
            return "drop_thinking"
        if "invalid temperature" in detail:
            return "temperature_one"
        return None

    def _request(self, prompt: str, max_tokens: int, on_log=None) -> str | None:
        active_log = on_log or self._on_log
        headers = _headers(
            self.api_key,
            session_id=self.session_id,
            user_agent=self.user_agent,
        )

        def opencode_delay(attempt: int, exc: Exception) -> float:
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                retry_after = exc.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        return max(1.0, float(retry_after))
                    except (TypeError, ValueError):
                        pass
            return 5.0 * attempt

        response = None
        compat_round = 0
        # Enough rounds for every payload adaptation we know how to apply — a model
        # such as kimi-k2.7-code needs two of them (thinking and temperature).
        while compat_round < 3:
            compat_round += 1
            try:
                response = request_with_retry(
                    lambda: self.session.post(
                        self.api_url,
                        headers=headers,
                        json=self._payload(prompt, max_tokens),
                        timeout=300,
                    ),
                    operation="Opencode Go",
                    on_log=active_log,
                    delay_func=opencode_delay,
                    should_continue=self._should_continue,
                )
            except RequestCancelled:
                raise
            except requests.HTTPError as exc:
                adaptation = self._compat_adaptation(exc)
                if adaptation and not self._compat.get(adaptation):
                    self._compat[adaptation] = True
                    if active_log:
                        active_log(
                            f"🛠️ Opencode Go: {self.model} не принял стандартный запрос "
                            f"({adaptation}) — повторяю с поправкой",
                            "yellow",
                        )
                    continue
                status = exc.response.status_code if exc.response is not None else 0
                detail = exc.response.text if exc.response is not None else ""
                if status == 401:
                    message = "неверный или отозванный API-ключ"
                elif status == 400 and "MissingSessionID" in detail:
                    message = "relay не принял x-opencode-session"
                else:
                    message = f"HTTP {status}: {detail[:150]}"
                if active_log:
                    active_log(f"❌ Opencode Go: {message}", "red")
                return None
            except requests.RequestException as exc:
                if active_log:
                    active_log(f"❌ Opencode Go сеть: {exc}", "red")
                return None
            break

        if response is None:
            return None

        try:
            payload = response.json()
            choice = payload["choices"][0]
            finish_reason = choice.get("finish_reason")
            content = (choice.get("message") or {}).get("content")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("Opencode Go invalid JSON: %s", exc)
            if active_log:
                active_log(f"❌ Opencode Go: неверный JSON ответа: {exc}", "red")
            return None

        if content is None:
            # Reasoning models return content=null when the budget ran out on
            # thinking; the batch is retried in smaller pieces by the base class.
            if active_log:
                active_log(
                    "⚠️ Opencode Go: ответ без текста "
                    f"(finish_reason={finish_reason}); пакет будет раздроблен",
                    "yellow",
                )
            return None

        if not isinstance(content, str) or not content.strip():
            if active_log:
                active_log("⚠️ Opencode Go вернул пустой ответ", "yellow")
            return None

        return content.strip()
