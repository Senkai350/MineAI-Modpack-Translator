import uuid

from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.deepl import DeepLEngine
from mineai.engines.google import GoogleEngine
from mineai.constants import DEFAULT_OPENCODE_GO_MODEL, DEFAULT_OPENROUTER_MODEL
from mineai.engines.kobold import KoboldEngine
from mineai.engines.opencode import OpencodeGoEngine
from mineai.engines.openrouter import OpenRouterEngine
from mineai.text_processing import apply_smart_glue, mask_protected_fragments


class TranslationService:
    """Prepares strings, uses cache, delegates to a translation engine."""

    def __init__(
        self,
        engine_name: str,
        cache: TranslationCache,
        config: ConfigManager,
        *,
        google_mode: str = "single",
        ai_mode: str = "safe",
        ai_provider: str = "local",
    ) -> None:
        self.engine_name = engine_name
        self.cache = cache
        self.config = config
        self.google_mode = google_mode
        self.ai_mode = ai_mode
        self.ai_provider = ai_provider

    def _build_engine(self, context: str = "") -> TranslationEngine:
        if self.engine_name == "google":
            return GoogleEngine(
                workers=self.config.getint("GENERAL", "google_workers", 5),
                mode=self.google_mode,
            )
        if self.engine_name == "deepl":
            return DeepLEngine(self.config.get("API", "deepl_key"))
        if self.ai_provider == "openrouter":
            return OpenRouterEngine(
                api_url=self.config.get("OPENROUTER", "api_url"),
                api_key=self.config.get("OPENROUTER", "api_key"),
                model=self.config.get("OPENROUTER", "model") or DEFAULT_OPENROUTER_MODEL,
                mode=self.ai_mode,
                context=context,
                site_url=self.config.get("OPENROUTER", "site_url"),
                app_name=self.config.get("OPENROUTER", "app_name"),
            )
        if self.ai_provider == "opencode":
            return OpencodeGoEngine(
                api_key=self.config.get("OPENCODE", "api_key"),
                model=self.config.get("OPENCODE", "model") or DEFAULT_OPENCODE_GO_MODEL,
                api_url=self.config.get("OPENCODE", "api_url"),
                mode=self.ai_mode,
                context=context,
                reasoning_effort=self.config.get("OPENCODE", "reasoning_effort"),
                session_id=self._opencode_session_id(),
                request_delay=self.config.getfloat("OPENCODE", "request_delay", 1.0),
                user_agent=self.config.get("OPENCODE", "user_agent"),
            )
        return KoboldEngine(mode=self.ai_mode, context=context)

    def _opencode_session_id(self) -> str:
        """Stable per-install session key — keeps the relay's prompt cache warm between runs."""
        session_id = self.config.get("OPENCODE", "session_id").strip()
        if not session_id:
            session_id = f"mineai-{uuid.uuid4().hex[:16]}"
            self.config.set("OPENCODE", "session_id", session_id)
        return session_id

    def translate_dict(
        self,
        strings: dict[str, str],
        target_lang: dict,
        callbacks: EngineCallbacks,
        *,
        context: str = "",
    ) -> dict[str, str]:
        if not strings:
            return {}

        smart_glue = self.config.getboolean("GENERAL", "smart_glue")
        result: dict[str, str] = {}
        pending: dict[str, EngineItem] = {}
        cached_count = 0

        for key, text in strings.items():
            if not callbacks.should_run():
                break
            callbacks.wait_if_paused()
            if smart_glue:
                text = apply_smart_glue(text)

            hit = self.cache.get(target_lang["api"], text)
            if hit is not None:
                result[key] = hit
                cached_count += 1
                continue

            masked, mapping = mask_protected_fragments(text)
            if not masked:
                result[key] = text
                continue
            pending[key] = EngineItem(key=key, original=text, masked=masked, mapping=mapping)

        if cached_count:
            callbacks.on_log(f"   🗃️ Из кэша: {cached_count} строк", "dim")

        if not pending or not callbacks.should_run():
            return result

        engine = self._build_engine(context)
        translated = engine.translate_batch(pending, target_lang, callbacks)

        for key, text in translated.items():
            original = pending[key].original
            result[key] = text
            self.cache.set(target_lang["api"], original, text)
            callbacks.on_log(f" > {original[:40]} -> {text[:40]}", "dim")

        for key, item in pending.items():
            if key not in translated:
                result[key] = item.original

        self.cache.save_if_threshold()
        return result
