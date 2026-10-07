"""Opencode Go engine (opencode.ai/zen/go) — OpenAI-compatible chat completions.

The relay is stricter than a plain OpenAI endpoint:

* every request must carry ``x-opencode-session`` or it answers HTTP 400
  ``MissingSessionID``;
* clients are asked to identify themselves with a real User-Agent instead of a
  generic SDK name (traffic is monitored for abuse);
* not every catalog model speaks chat/completions — Qwen/MiniMax/Claude are
  served over the Anthropic wire (``/messages``), GPT/Grok/Muse-Spark over
  ``/responses``. Those ids are hidden from the model picker by default.

Translation is a batch job, not a conversation, so one stable session id per
run is generated and persisted (it keeps the relay's prompt cache warm).
"""

import time
import uuid

import requests

from mineai.constants import (
    DEFAULT_OPENCODE_GO_MODEL,
    OPENCODE_CHAT_UNSUPPORTED_PREFIXES,
    OPENCODE_GO_API,
    OPENCODE_GO_MODELS_URL,
    OPENCODE_MAX_TOKENS_MULTIPLIER,
    OPENCODE_USER_AGENT,
)
from mineai.engines.llm_common import BatchLlmEngine


def is_chat_compatible(model_id: str) -> bool:
    """False for models Opencode Go serves over /messages or /responses, not chat completions."""
    lowered = (model_id or "").strip().lower()
    return bool(lowered) and not lowered.startswith(OPENCODE_CHAT_UNSUPPORTED_PREFIXES)


def chat_url_from(api_url: str) -> str:
    """Normalize a user-supplied endpoint into a full chat/completions URL."""
    url = (api_url or "").strip().rstrip("/") or OPENCODE_GO_API
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def models_url_from(api_url: str) -> str:
    """Derive the model-catalog URL from whatever endpoint the user configured."""
    url = (api_url or "").strip().rstrip("/") or OPENCODE_GO_API
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            return url[: -len(suffix)] + "/models"
    return url + "/models"


def _base_headers(api_key: str = "", *, session_id: str = "") -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": OPENCODE_USER_AGENT,
    }
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    if session_id.strip():
        headers["x-opencode-session"] = session_id.strip()
    return headers


def fetch_models(api_url: str = OPENCODE_GO_API, api_key: str = "", *, timeout: int = 20) -> list[str]:
    """Live model ids from the Opencode Go catalog (sorted, no metadata)."""
    response = requests.get(
        models_url_from(api_url) or OPENCODE_GO_MODELS_URL,
        headers=_base_headers(api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    ids = [str(entry.get("id", "")).strip() for entry in data.get("data", []) if entry.get("id")]
    return sorted(set(ids))


def probe_connection(
    api_url: str,
    api_key: str,
    model: str,
    *,
    reasoning_effort: str = "",
    session_id: str = "",
    timeout: int = 60,
) -> tuple[bool, str]:
    """One tiny chat call — proves endpoint, key and model in a single shot."""
    payload = {
        "model": (model or DEFAULT_OPENCODE_GO_MODEL).strip(),
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    if reasoning_effort and reasoning_effort != "auto":
        payload["reasoning_effort"] = reasoning_effort
    try:
        response = requests.post(
            chat_url_from(api_url),
            headers=_base_headers(api_key, session_id=session_id or f"mineai-probe-{uuid.uuid4().hex[:8]}"),
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return False, f"Сеть: {exc}"
    if response.ok:
        return True, f"OK — модель {payload['model']} отвечает"
    if response.status_code == 401:
        return False, "401: неверный или отозванный API-ключ"
    return False, f"{response.status_code}: {(response.text or '')[:200]}"


class OpencodeGoEngine(BatchLlmEngine):
    """Batched JSON translation through the Opencode Go relay."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_OPENCODE_GO_MODEL,
        *,
        api_url: str = OPENCODE_GO_API,
        mode: str = "safe",
        context: str = "",
        reasoning_effort: str = "medium",
        session_id: str = "",
        request_delay: float = 1.0,
        max_retries: int = 3,
        user_agent: str = OPENCODE_USER_AGENT,
    ) -> None:
        self.api_url = chat_url_from(api_url)
        self.api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_OPENCODE_GO_MODEL).strip()
        self.reasoning_effort = (reasoning_effort or "").strip().lower()
        self.session_id = (session_id or "").strip() or f"mineai-{uuid.uuid4().hex[:16]}"
        self.request_delay = max(0.0, float(request_delay or 0))
        self.max_retries = max(1, int(max_retries))
        self.user_agent = (user_agent or "").strip() or OPENCODE_USER_AGENT
        super().__init__(
            mode=mode,
            context=context,
            call_api=self._request,
            label="Opencode Go",
        )
        # Reasoning-токены уходят из того же бюджета: иначе «думающая» модель
        # обрежет JSON на середине и пакет придётся дробить.
        self.max_tokens = int(self.max_tokens * OPENCODE_MAX_TOKENS_MULTIPLIER)

    def _headers(self) -> dict[str, str]:
        return _base_headers(self.api_key, session_id=self.session_id) | {"User-Agent": self.user_agent}

    def _payload(self, prompt: str, max_tokens: int) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if self.reasoning_effort and self.reasoning_effort != "auto":
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _request(self, prompt: str, max_tokens: int) -> str | None:
        last_network_error: requests.RequestException | None = None

        for attempt in range(self.max_retries):
            if self.request_delay:
                time.sleep(self.request_delay)

            try:
                response = requests.post(
                    self.api_url,
                    headers=self._headers(),
                    json=self._payload(prompt, max_tokens),
                    timeout=300,
                )
            except requests.RequestException as exc:
                last_network_error = exc
                wait = 2 * (attempt + 1)
                print(f"\n[Opencode Go] Сеть: {exc}. Повтор через {wait} сек ({attempt + 1}/{self.max_retries})...")
                time.sleep(wait)
                continue

            if response.status_code == 429:
                retry_after = 0
                try:
                    retry_after = int(response.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    retry_after = 0
                wait = retry_after or 15 * (attempt + 1)
                print(f"\n[Opencode Go] Лимит 429. Ждём {wait} сек ({attempt + 1}/{self.max_retries})...")
                time.sleep(wait)
                continue

            if response.status_code == 401:
                raise requests.HTTPError(
                    "401: неверный или отозванный API-ключ Opencode Go.", response=response
                )

            if not response.ok:
                detail = (response.text or response.reason or "")[:300]
                if "MissingSessionID" in detail:
                    detail += " (relay не принял x-opencode-session — проверьте session_id)"
                raise requests.HTTPError(f"{response.status_code}: {detail}", response=response)

            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content")

            if content is None:
                finish = choice.get("finish_reason")
                print(
                    f"\n[Opencode Go] Пустой ответ (finish_reason={finish}); "
                    "модель израсходовала бюджет на reasoning — пакет будет раздроблен."
                )
                return None

            return content.strip()

        if last_network_error is not None:
            raise last_network_error
        print("\n[Ошибка] Opencode Go не ответил: лимит 429 не отпустил за все попытки.")
        return None
