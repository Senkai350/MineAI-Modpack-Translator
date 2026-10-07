import queue
import threading

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog

from mineai.config import ConfigManager
from mineai.constants import (
    DEFAULT_OPENCODE_GO_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    OPENCODE_GO_API,
    OPENCODE_REASONING_EFFORTS,
)
from mineai.engines.opencode import fetch_models, is_chat_compatible, probe_connection


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: ConfigManager, on_saved) -> None:
        super().__init__(parent)
        self.config = config
        self.on_saved = on_saved
        self.title("⚙ Настройки MineAI")
        self.geometry("540x680")
        self.resizable(False, False)
        self.grab_set()

        tabs = ctk.CTkTabview(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        tab_ai = tabs.add("Локальный ИИ")
        tab_or = tabs.add("OpenRouter")
        tab_oc = tabs.add("Opencode Go")
        tab_gen = tabs.add("Общие и API")

        ctk.CTkLabel(tab_ai, text="Исполняемый файл KoboldCPP (.exe):", font=("", 12, "bold")).pack(
            anchor="w", pady=(10, 0), padx=10
        )
        self.ent_ai_exe = ctk.CTkEntry(tab_ai, width=360)
        self.ent_ai_exe.insert(0, config.get("AI", "exe_path"))
        self.ent_ai_exe.pack(fill="x", padx=10, pady=5)
        ctk.CTkButton(
            tab_ai, text="Обзор", width=80, command=lambda: self._browse(self.ent_ai_exe, [("Executables", "*.exe")])
        ).pack(anchor="e", padx=10)

        ctk.CTkLabel(tab_ai, text="Модель (.gguf):", font=("", 12, "bold")).pack(anchor="w", pady=(10, 0), padx=10)
        self.ent_ai_mod = ctk.CTkEntry(tab_ai, width=360)
        self.ent_ai_mod.insert(0, config.get("AI", "model_path"))
        self.ent_ai_mod.pack(fill="x", padx=10, pady=5)
        ctk.CTkButton(
            tab_ai, text="Обзор", width=80, command=lambda: self._browse(self.ent_ai_mod, [("GGUF Models", "*.gguf")])
        ).pack(anchor="e", padx=10)

        gpu_val = config.getint("AI", "gpu_layers", 99)
        self.lbl_gpu = ctk.CTkLabel(tab_ai, text=f"Слои GPU: {gpu_val}", font=("", 12, "bold"))
        self.lbl_gpu.pack(anchor="w", pady=(10, 0), padx=10)
        self.slider_gpu = ctk.CTkSlider(
            tab_ai,
            from_=0,
            to=99,
            number_of_steps=99,
            command=lambda v: self.lbl_gpu.configure(text=f"Слои GPU: {int(v)}"),
        )
        self.slider_gpu.set(gpu_val)
        self.slider_gpu.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            tab_or,
            text="Ключ: openrouter.ai/keys",
            font=("", 11),
            text_color="gray",
        ).pack(anchor="w", padx=10, pady=(10, 0))
        
        ctk.CTkLabel(tab_or, text="API URL (для Ollama, vLLM и др.):", font=("", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
        self.ent_or_url = ctk.CTkEntry(tab_or)
        self.ent_or_url.insert(0, config.get("OPENROUTER", "api_url"))
        self.ent_or_url.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="API ключ OpenRouter:", font=("", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 0))
        self.ent_or_key = ctk.CTkEntry(tab_or, show="*")
        self.ent_or_key.insert(0, config.get("OPENROUTER", "api_key"))
        self.ent_or_key.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="ID модели (напр. qwen/qwen-2.5-72b-instruct):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.ent_or_model = ctk.CTkEntry(tab_or)
        self.ent_or_model.insert(0, config.get("OPENROUTER", "model") or DEFAULT_OPENROUTER_MODEL)
        self.ent_or_model.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="Site URL (необязательно, для статистики):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.ent_or_site = ctk.CTkEntry(tab_or)
        self.ent_or_site.insert(0, config.get("OPENROUTER", "site_url"))
        self.ent_or_site.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_or, text="Название приложения (X-Title):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.ent_or_app = ctk.CTkEntry(tab_or)
        self.ent_or_app.insert(0, config.get("OPENROUTER", "app_name"))
        self.ent_or_app.pack(fill="x", padx=10, pady=5)

        # ---------------- Opencode Go ----------------
        ctk.CTkLabel(
            tab_oc,
            text="Ключ: opencode.ai/auth (подписка Go / Go Plus)",
            font=("", 11),
            text_color="gray",
        ).pack(anchor="w", padx=10, pady=(10, 0))

        ctk.CTkLabel(tab_oc, text="API URL (по умолчанию zen/go, можно свой эндпойнт):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.ent_oc_url = ctk.CTkEntry(tab_oc)
        self.ent_oc_url.insert(0, config.get("OPENCODE", "api_url") or OPENCODE_GO_API)
        self.ent_oc_url.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_oc, text="API ключ Opencode Go:", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(5, 0)
        )
        self.ent_oc_key = ctk.CTkEntry(tab_oc, show="*")
        self.ent_oc_key.insert(0, config.get("OPENCODE", "api_key"))
        self.ent_oc_key.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_oc, text="Модель (список тянется из API):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(5, 0)
        )
        row_oc_model = ctk.CTkFrame(tab_oc, fg_color="transparent")
        row_oc_model.pack(fill="x", padx=10, pady=5)
        self._all_oc_models: list[str] = []
        saved_oc_model = config.get("OPENCODE", "model") or DEFAULT_OPENCODE_GO_MODEL
        self.cmb_oc_model = ctk.CTkComboBox(row_oc_model, values=[saved_oc_model])
        self.cmb_oc_model.set(saved_oc_model)
        self.cmb_oc_model.pack(side="left", fill="x", expand=True)
        self.btn_oc_refresh = ctk.CTkButton(
            row_oc_model, text="🔄 Обновить список", width=140, command=self._refresh_models
        )
        self.btn_oc_refresh.pack(side="left", padx=(6, 0))

        self.var_oc_all = ctk.BooleanVar(value=config.getboolean("OPENCODE", "show_all_models"))
        ctk.CTkCheckBox(
            tab_oc,
            text="Показать все модели (часть идёт не по chat-протоколу и не подойдёт для перевода)",
            variable=self.var_oc_all,
            command=self._apply_model_filter,
            font=("", 11),
        ).pack(anchor="w", padx=10)

        ctk.CTkLabel(tab_oc, text="Уровень мышления (reasoning_effort):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.var_oc_effort = ctk.StringVar(
            value=config.get("OPENCODE", "reasoning_effort") or "medium"
        )
        ctk.CTkOptionMenu(
            tab_oc, variable=self.var_oc_effort, values=list(OPENCODE_REASONING_EFFORTS)
        ).pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_oc, text="Session ID (стабильный, для кэша промпта):", font=("", 12, "bold")).pack(
            anchor="w", padx=10, pady=(5, 0)
        )
        row_oc_sess = ctk.CTkFrame(tab_oc, fg_color="transparent")
        row_oc_sess.pack(fill="x", padx=10, pady=5)
        self.ent_oc_session = ctk.CTkEntry(row_oc_sess)
        self.ent_oc_session.insert(0, config.get("OPENCODE", "session_id"))
        self.ent_oc_session.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row_oc_sess, text="↻", width=40, command=self._new_session_id).pack(
            side="left", padx=(6, 0)
        )

        ctk.CTkButton(tab_oc, text="🔌 Проверить подключение", command=self._check_connection).pack(
            anchor="w", padx=10, pady=(8, 0)
        )
        self.lbl_oc_status = ctk.CTkLabel(
            tab_oc, text="", font=("", 11), text_color="gray", wraplength=470, justify="left"
        )
        self.lbl_oc_status.pack(anchor="w", padx=10, pady=(4, 0))

        self.var_smart = ctk.BooleanVar(value=config.getboolean("GENERAL", "smart_glue"))
        ctk.CTkSwitch(tab_gen, text="✨ Умный склейщик предложений", variable=self.var_smart).pack(
            anchor="w", padx=10, pady=15
        )

        workers = config.getint("GENERAL", "google_workers", 5)
        ctk.CTkLabel(tab_gen, text="Потоки Google Translate:", font=("", 12, "bold")).pack(anchor="w", padx=10)
        self.slider_thr = ctk.CTkSlider(tab_gen, from_=1, to=10, number_of_steps=9)
        self.slider_thr.set(workers)
        self.slider_thr.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(tab_gen, text="API ключ DeepL:", font=("", 12, "bold")).pack(anchor="w", pady=(10, 0), padx=10)
        self.ent_deepl = ctk.CTkEntry(tab_gen, show="*")
        self.ent_deepl.insert(0, config.get("API", "deepl_key"))
        self.ent_deepl.pack(fill="x", padx=10, pady=5)

        self._oc_queue: queue.Queue = queue.Queue()
        self.after(300, self._refresh_models)
        self.after(400, self._drain_oc_queue)

        ctk.CTkButton(
            self,
            text="💾 Сохранить настройки",
            fg_color="#28a745",
            hover_color="#218838",
            command=self._save,
        ).pack(fill="x", padx=20, pady=10)

    def _browse(self, entry: ctk.CTkEntry, filetypes) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def _save(self) -> None:
        self.config.set("AI", "exe_path", self.ent_ai_exe.get())
        self.config.set("AI", "model_path", self.ent_ai_mod.get())
        self.config.set("AI", "gpu_layers", int(self.slider_gpu.get()))
        self.config.set("OPENROUTER", "api_key", self.ent_or_key.get())
        self.config.set("OPENROUTER", "api_url", self.ent_or_url.get().strip())
        self.config.set("OPENROUTER", "model", self.ent_or_model.get().strip())
        self.config.set("OPENROUTER", "site_url", self.ent_or_site.get().strip())
        self.config.set("OPENROUTER", "app_name", self.ent_or_app.get().strip())
        self.config.set("GENERAL", "smart_glue", self.var_smart.get())
        self.config.set("GENERAL", "google_workers", int(self.slider_thr.get()))
        self.config.set("API", "deepl_key", self.ent_deepl.get())
        self.config.set("OPENCODE", "api_url", self.ent_oc_url.get().strip() or OPENCODE_GO_API)
        self.config.set("OPENCODE", "api_key", self.ent_oc_key.get().strip())
        self.config.set("OPENCODE", "model", self.cmb_oc_model.get().strip() or DEFAULT_OPENCODE_GO_MODEL)
        self.config.set("OPENCODE", "reasoning_effort", self.var_oc_effort.get())
        self.config.set("OPENCODE", "session_id", self.ent_oc_session.get().strip())
        self.config.set("OPENCODE", "show_all_models", self.var_oc_all.get())
        self.on_saved()
        self.destroy()

    # ---------------- Opencode Go helpers ----------------

    def _apply_model_filter(self) -> None:
        """Re-filter the already fetched catalog without hitting the network again."""
        values = list(self._all_oc_models)
        if values and not self.var_oc_all.get():
            values = [m for m in values if is_chat_compatible(m)]
        current = self.cmb_oc_model.get().strip()
        if not values:
            values = [current or DEFAULT_OPENCODE_GO_MODEL]
        if current and current not in values:
            values.insert(0, current)
        self._visible_oc_models = values
        self.cmb_oc_model.configure(values=values)
        self.cmb_oc_model.set(current)

    def _refresh_models(self) -> None:
        self.btn_oc_refresh.configure(state="disabled", text="⏳ Загрузка...")
        self.lbl_oc_status.configure(text="Запрашиваю актуальный список моделей...", text_color="gray")
        api_url = self.ent_oc_url.get().strip() or OPENCODE_GO_API
        api_key = self.ent_oc_key.get().strip()

        def worker() -> None:
            try:
                self._oc_queue.put(("models", fetch_models(api_url, api_key), None))
            except Exception as exc:  # сеть/HTTP/JSON — показываем причину в UI
                self._oc_queue.put(("models", [], exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_models_loaded(self, models: list, error) -> None:
        self.btn_oc_refresh.configure(state="normal", text="🔄 Обновить список")
        if error is not None:
            self.lbl_oc_status.configure(text=f"❌ Не удалось получить модели: {error}", text_color="#e74c3c")
            return
        self._all_oc_models = models
        self._apply_model_filter()
        shown = len(self._visible_oc_models)
        self.lbl_oc_status.configure(
            text=f"✅ Доступно моделей: {shown} из {len(models)} (галочка выше показывает все)",
            text_color="#2ecc71",
        )

    def _new_session_id(self) -> None:
        import uuid

        self.ent_oc_session.delete(0, "end")
        self.ent_oc_session.insert(0, f"mineai-{uuid.uuid4().hex[:16]}")

    def _check_connection(self) -> None:
        self.lbl_oc_status.configure(text="Проверяю подключение...", text_color="gray")
        api_url = self.ent_oc_url.get().strip() or OPENCODE_GO_API
        api_key = self.ent_oc_key.get().strip()
        model = self.cmb_oc_model.get().strip() or DEFAULT_OPENCODE_GO_MODEL
        effort = self.var_oc_effort.get()
        session_id = self.ent_oc_session.get().strip()

        def worker() -> None:
            ok, message = probe_connection(
                api_url, api_key, model, reasoning_effort=effort, session_id=session_id
            )
            self._oc_queue.put(("probe", ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_oc_queue(self) -> None:
        """Main-thread poller — Tk widgets are only ever touched from the main thread."""
        try:
            while True:
                kind, *payload = self._oc_queue.get_nowait()
                if kind == "models":
                    self._on_models_loaded(payload[0], payload[1])
                elif kind == "probe":
                    self._on_probe_done(payload[0], payload[1])
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if self.winfo_exists():
            self.after(150, self._drain_oc_queue)

    def _on_probe_done(self, ok: bool, message: str) -> None:
        self.lbl_oc_status.configure(
            text=("✅ " if ok else "❌ ") + message,
            text_color="#2ecc71" if ok else "#e74c3c",
        )
