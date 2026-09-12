import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CACHE_DIR, VideoOptions, get_api_key, load_settings, save_api_key, save_settings
from app.llm.manual_provider import ManualNarrationGenerator
from app.models import Slide
from app.pipeline import load_script, parse_and_cache, project_dir_for, render_video, save_script
from app.tts import PROVIDER_LABELS, list_voices
from app.video.themes import THEME_LABELS

APP_TITLE = "Ders Stüdyosu — MD/PPTX/PDF → Sesli/Görüntülü Ders"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x860")
        self.minsize(1050, 700)

        self.settings = load_settings()
        self.raw_sections = []
        self.slides: list[Slide] = []
        self.pdir: Path | None = None
        self.log_queue = queue.Queue()
        self._script_busy = False
        self._script_insert_cursor: int | None = None

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_source = ttk.Frame(nb)
        self.tab_script = ttk.Frame(nb)
        self.tab_video = ttk.Frame(nb)
        nb.add(self.tab_source, text="1. Kaynak")
        nb.add(self.tab_script, text="2. Anlatım Metni")
        nb.add(self.tab_video, text="3. Ses ve Video")

        self._build_source_tab()
        self._build_script_tab()
        self._build_video_tab()

        self.status = tk.StringVar(value="Hazır.")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=8, pady=(0, 6))

        self.after(150, self._poll_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- TAB 1: Kaynak ----------
    def _build_source_tab(self):
        f = self.tab_source
        top = ttk.Frame(f)
        top.pack(fill="x", padx=10, pady=10)

        self.source_var = tk.StringVar(value=self.settings.get("last_source", ""))
        ttk.Label(top, text="Dosya (.md / .pptx / .pdf):").pack(side="left")
        ttk.Entry(top, textvariable=self.source_var, width=70).pack(side="left", padx=6)
        ttk.Button(top, text="Gözat", command=self._browse_source).pack(side="left")
        ttk.Button(top, text="İçeriği Ayır", command=self._split_source).pack(side="left", padx=10)

        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, padx=10, pady=6)
        ttk.Label(
            mid,
            text="Bölümler — bir satıra tıklayarak seçimi açıp kapatabilirsin:",
        ).pack(anchor="w")
        list_frame = ttk.Frame(mid)
        list_frame.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, orient="vertical")
        self.section_tree = ttk.Treeview(
            list_frame,
            columns=("selected", "title", "chars"),
            show="headings",
            selectmode="extended",
            yscrollcommand=sb.set,
        )
        self.section_tree.heading("selected", text="Seçili")
        self.section_tree.heading("title", text="Bölüm")
        self.section_tree.heading("chars", text="Karakter")
        self.section_tree.column("selected", width=70, minwidth=70, anchor="center", stretch=False)
        self.section_tree.column("title", width=850, anchor="w")
        self.section_tree.column("chars", width=100, anchor="e", stretch=False)
        sb.config(command=self.section_tree.yview)
        self.section_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.section_tree.bind("<Button-1>", self._toggle_section_row)
        self.section_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._refresh_section_selection_marks()
        )

        btns = ttk.Frame(f)
        btns.pack(fill="x", padx=10, pady=6)
        ttk.Button(btns, text="Tümünü Seç", command=self._select_all_sections).pack(side="left")
        ttk.Button(btns, text="Seçimi Temizle", command=self._clear_section_selection).pack(side="left", padx=6)
        self.section_selected_var = tk.StringVar(value="Henüz içerik ayrıştırılmadı.")
        ttk.Label(btns, textvariable=self.section_selected_var).pack(side="left", padx=16)

    def _browse_source(self):
        path = filedialog.askopenfilename(filetypes=[("Desteklenen dosyalar", "*.md *.pptx *.pdf"), ("Tüm dosyalar", "*.*")])
        if path:
            self.source_var.set(path)

    def _split_source(self):
        path = self.source_var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showerror(APP_TITLE, "Geçerli bir dosya seç.")
            return
        try:
            self.pdir, self.raw_sections = parse_and_cache(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Ayrıştırma hatası:\n{e}")
            return
        self.settings["last_source"] = path
        save_settings(self.settings)

        self.section_tree.delete(*self.section_tree.get_children())
        for i, s in enumerate(self.raw_sections):
            n = len(s.text)
            self.section_tree.insert(
                "", "end", iid=str(i),
                values=("✓", f"[{'#' * s.level}] {s.title}", n),
            )
        self._select_all_sections()
        self.status.set(f"{len(self.raw_sections)} bölüm bulundu: {path}")

    def _toggle_section_row(self, event):
        row = self.section_tree.identify_row(event.y)
        if not row:
            return
        if row in self.section_tree.selection():
            self.section_tree.selection_remove(row)
        else:
            self.section_tree.selection_add(row)
        self.section_tree.focus(row)
        self._refresh_section_selection_marks()
        return "break"

    def _select_all_sections(self):
        rows = self.section_tree.get_children()
        if rows:
            self.section_tree.selection_set(*rows)
        self._refresh_section_selection_marks()

    def _clear_section_selection(self):
        self.section_tree.selection_remove(*self.section_tree.selection())
        self._refresh_section_selection_marks()

    def _refresh_section_selection_marks(self):
        selected = set(self.section_tree.selection())
        rows = self.section_tree.get_children()
        for row in rows:
            values = list(self.section_tree.item(row, "values"))
            if values:
                values[0] = "✓" if row in selected else ""
                self.section_tree.item(row, values=values)
        self.section_selected_var.set(f"{len(selected)} / {len(rows)} bölüm seçili")

    # ---------- TAB 2: Script ----------
    def _build_script_tab(self):
        f = self.tab_script
        top = ttk.LabelFrame(f, text="Anlatım metni nasıl üretilsin?")
        top.pack(fill="x", padx=10, pady=10)

        self.llm_provider_var = tk.StringVar(value=self.settings.get("llm_provider", "gemini"))
        ttk.Radiobutton(top, text="Gemini API", variable=self.llm_provider_var, value="gemini").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(top, text="OpenAI uyumlu API", variable=self.llm_provider_var, value="openai").grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(top, text="Agent CLI", variable=self.llm_provider_var, value="agent").grid(row=0, column=2, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(top, text="Manuel JSON", variable=self.llm_provider_var, value="manual").grid(row=0, column=3, sticky="w", padx=6, pady=4)

        ttk.Label(top, text="Gemini API Key:").grid(row=1, column=0, sticky="e", padx=6)
        self.gemini_key_var = tk.StringVar(value=get_api_key("GEMINI_API_KEY") or "")
        ttk.Entry(top, textvariable=self.gemini_key_var, width=45, show="*").grid(row=1, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Anahtarı Kaydet", command=self._save_gemini_key).grid(row=1, column=2, padx=6)
        ttk.Label(top, text="(ücretsiz key: aistudio.google.com/apikey)").grid(row=1, column=3, sticky="w")

        from app.llm.gemini_provider import DEFAULT_MODEL, KNOWN_MODELS
        ttk.Label(top, text="Gemini modeli:").grid(row=2, column=0, sticky="e", padx=6)
        self.gemini_model_var = tk.StringVar(value=self.settings.get("gemini_model", DEFAULT_MODEL))
        ttk.Combobox(top, textvariable=self.gemini_model_var, values=KNOWN_MODELS, width=30).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(top, text="(liste sabit değil, kutuya elle de model adı yazabilirsin)").grid(row=2, column=2, columnspan=2, sticky="w")

        from app.llm.openai_compatible_provider import DEFAULT_ENDPOINT, DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
        ttk.Label(top, text="OpenAI endpoint:").grid(row=3, column=0, sticky="e", padx=6, pady=4)
        self.openai_endpoint_var = tk.StringVar(value=self.settings.get("openai_endpoint", DEFAULT_ENDPOINT))
        ttk.Entry(top, textvariable=self.openai_endpoint_var, width=55).grid(row=3, column=1, columnspan=2, sticky="we", padx=6)
        ttk.Label(top, text="(base URL veya tam /chat/completions URL)").grid(row=3, column=3, sticky="w")

        ttk.Label(top, text="OpenAI API Key:").grid(row=4, column=0, sticky="e", padx=6, pady=4)
        self.openai_key_var = tk.StringVar(value=get_api_key("OPENAI_API_KEY") or "")
        ttk.Entry(top, textvariable=self.openai_key_var, width=45, show="*").grid(row=4, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Anahtarı Kaydet", command=self._save_openai_key).grid(row=4, column=2, padx=6)
        ttk.Label(top, text="(yerel anahtarsız sunucularda boş kalabilir)").grid(row=4, column=3, sticky="w")

        ttk.Label(top, text="OpenAI model adı:").grid(row=5, column=0, sticky="e", padx=6, pady=4)
        self.openai_model_var = tk.StringVar(value=self.settings.get("openai_model", OPENAI_DEFAULT_MODEL))
        ttk.Entry(top, textvariable=self.openai_model_var, width=32).grid(row=5, column=1, sticky="w", padx=6)
        ttk.Label(top, text="(örn. gpt-5.2 veya sunucundaki model kimliği)").grid(row=5, column=2, columnspan=2, sticky="w")

        from app.llm.agent_cli_provider import DEFAULT_COMMAND as AGENT_DEFAULT_CMD
        ttk.Label(top, text="Agent komutu:").grid(row=6, column=0, sticky="e", padx=6, pady=4)
        self.agent_command_var = tk.StringVar(value=self.settings.get("agent_command", AGENT_DEFAULT_CMD))
        ttk.Entry(top, textvariable=self.agent_command_var, width=55).grid(row=6, column=1, columnspan=2, sticky="we", padx=6)
        ttk.Label(top, text="(claude, gemini vb.; prompt stdin'den verilir)").grid(row=6, column=3, sticky="w")

        agent_opts = ttk.Frame(top)
        agent_opts.grid(row=7, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        self.agent_reuse_session_var = tk.BooleanVar(
            value=self.settings.get("agent_reuse_session", True)
        )
        ttk.Checkbutton(
            agent_opts,
            text="Parçaları aynı Claude oturumunda sürdür (önerilen)",
            variable=self.agent_reuse_session_var,
        ).pack(side="left")
        ttk.Label(agent_opts, text="Parça başına en çok bölüm:").pack(side="left", padx=(20, 4))
        self.agent_max_sections_var = tk.IntVar(value=self.settings.get("agent_max_sections", 4))
        ttk.Spinbox(
            agent_opts, from_=1, to=20, width=5,
            textvariable=self.agent_max_sections_var,
        ).pack(side="left")
        ttk.Label(agent_opts, text="Timeout (sn):").pack(side="left", padx=(20, 4))
        self.agent_timeout_var = tk.IntVar(value=self.settings.get("agent_timeout_sec", 900))
        ttk.Spinbox(
            agent_opts, from_=60, to=3600, increment=60, width=7,
            textvariable=self.agent_timeout_var,
        ).pack(side="left")

        ttk.Label(top, text="Üslup notu (opsiyonel):").grid(row=8, column=0, sticky="e", padx=6, pady=4)
        self.style_note_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.style_note_var, width=60).grid(row=8, column=1, columnspan=2, sticky="we", padx=6)

        self.single_request_var = tk.BooleanVar(value=self.settings.get("single_request", False))
        ttk.Checkbutton(top, text="Tek istekte gönder (seçili tüm bölümleri bölmeden tek API çağrısıyla işle)",
                         variable=self.single_request_var).grid(row=9, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))
        top.columnconfigure(1, weight=1)

        actions = ttk.Frame(f)
        actions.pack(fill="x", padx=10, pady=4)
        self.script_generate_button = ttk.Button(
            actions,
            text="Script Üret (Seçili Bölümlerden)",
            command=self._generate_script_threaded,
        )
        self.script_generate_button.pack(side="left")
        ttk.Button(actions, text="Prompt'u Panoya Kopyala", command=self._copy_prompt).pack(side="left", padx=6)
        ttk.Button(actions, text="Manuel JSON Dosyası Yükle", command=self._load_manual_json).pack(side="left", padx=6)
        ttk.Button(actions, text="Script'i Diskten Yükle", command=self._load_script_from_disk).pack(side="left", padx=6)
        ttk.Button(actions, text="Script'i Kaydet", command=self._save_script_to_disk).pack(side="left", padx=6)

        generation_opts = ttk.Frame(f)
        generation_opts.pack(fill="x", padx=10, pady=(2, 4))
        ttk.Label(generation_opts, text="Yeni üretilen slaytlar:").pack(side="left")
        self.script_insert_mode_var = tk.StringVar(value="Sona ekle")
        ttk.Combobox(
            generation_opts,
            textvariable=self.script_insert_mode_var,
            state="readonly",
            width=30,
            values=["Sona ekle", "Seçili slayttan sonra ekle"],
        ).pack(side="left", padx=6)
        self.script_progress_label_var = tk.StringVar(value="Hazır")
        ttk.Label(generation_opts, textvariable=self.script_progress_label_var).pack(side="right")

        self.script_progress = ttk.Progressbar(f, mode="determinate", maximum=1)
        self.script_progress.pack(fill="x", padx=10, pady=(0, 4))

        body = ttk.PanedWindow(f, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=6)

        left = ttk.Frame(body)
        self.slide_tree = ttk.Treeview(left, columns=("title",), show="headings", selectmode="browse")
        self.slide_tree.heading("title", text="Slaytlar")
        self.slide_tree.pack(fill="both", expand=True)
        self.slide_tree.bind("<<TreeviewSelect>>", self._on_slide_select)
        body.add(left, weight=1)

        right = ttk.Frame(body)
        ttk.Label(right, text="Başlık:").grid(row=0, column=0, sticky="w")
        self.edit_title = tk.Entry(right, width=70)
        self.edit_title.grid(row=1, column=0, sticky="we", pady=(0, 6))

        ttk.Label(right, text="Seviye:").grid(row=2, column=0, sticky="w")
        self.edit_level = ttk.Combobox(right, values=["topic", "chapter"], state="readonly", width=15)
        self.edit_level.grid(row=3, column=0, sticky="w", pady=(0, 6))

        ttk.Label(right, text="Maddeler (her satır bir madde):").grid(row=4, column=0, sticky="w")
        self.edit_bullets = tk.Text(right, height=6, width=70)
        self.edit_bullets.grid(row=5, column=0, sticky="we", pady=(0, 6))

        ttk.Label(right, text="Kod (opsiyonel):").grid(row=6, column=0, sticky="w")
        self.edit_code = tk.Text(right, height=6, width=70, font=("Consolas", 10))
        self.edit_code.grid(row=7, column=0, sticky="we", pady=(0, 6))

        ttk.Label(right, text="Anlatım metni (sesli okunacak):").grid(row=8, column=0, sticky="w")
        self.edit_narration = tk.Text(right, height=10, width=70, wrap="word")
        self.edit_narration.grid(row=9, column=0, sticky="we", pady=(0, 6))

        rowbtns = ttk.Frame(right)
        rowbtns.grid(row=10, column=0, sticky="w")
        ttk.Button(rowbtns, text="Bu Slaytı Kaydet", command=self._save_slide_edit).pack(side="left")
        ttk.Button(rowbtns, text="Slaytı Sil", command=self._delete_slide).pack(side="left", padx=6)
        ttk.Button(rowbtns, text="Yeni Slayt Ekle", command=self._add_slide).pack(side="left", padx=6)
        ttk.Button(rowbtns, text="↑ Yukarı", command=lambda: self._move_slide(-1)).pack(side="left", padx=(12, 3))
        ttk.Button(rowbtns, text="↓ Aşağı", command=lambda: self._move_slide(1)).pack(side="left", padx=3)
        body.add(right, weight=2)

        self._current_slide_idx = None

    def _save_gemini_key(self):
        save_api_key("GEMINI_API_KEY", self.gemini_key_var.get().strip())
        messagebox.showinfo(APP_TITLE, "Kaydedildi (.env dosyasına yazıldı).")

    def _save_openai_key(self):
        save_api_key("OPENAI_API_KEY", self.openai_key_var.get().strip())
        messagebox.showinfo(APP_TITLE, "Kaydedildi (.env dosyasına yazıldı).")

    def _selected_sections(self):
        idxs = sorted(int(i) for i in self.section_tree.selection())
        return [self.raw_sections[i] for i in idxs]

    def _copy_prompt(self):
        if not self.raw_sections:
            messagebox.showwarning(APP_TITLE, "Önce Kaynak sekmesinden bir dosya ayır.")
            return
        sections = self._selected_sections()
        if not sections:
            messagebox.showwarning(APP_TITLE, "Prompt için en az bir bölüm seç.")
            return
        prompt = ManualNarrationGenerator.build_prompt_for(sections, self.style_note_var.get())
        self.clipboard_clear()
        self.clipboard_append(prompt)
        messagebox.showinfo(APP_TITLE, "Prompt panoya kopyalandı. İstediğin bir sohbet arayüzüne (Gemini, ChatGPT, Claude) yapıştırıp\nçıkan JSON'u 'Manuel JSON Dosyası Yükle' ile geri al.")

    def _load_manual_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("Tüm dosyalar", "*.*")])
        if not path:
            return
        try:
            gen = ManualNarrationGenerator(json_path=path)
            new_slides = gen.generate(self._selected_sections(), self.style_note_var.get())
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"JSON okunamadı:\n{e}")
            return
        self.slides.extend(new_slides)
        self._refresh_slide_tree()
        self.status.set(f"{len(new_slides)} slayt eklendi (manuel JSON). Toplam: {len(self.slides)}")

    def _generate_script_threaded(self):
        if self._script_busy:
            messagebox.showinfo(APP_TITLE, "Bir script üretimi zaten devam ediyor.")
            return
        if not self.raw_sections:
            messagebox.showwarning(APP_TITLE, "Önce Kaynak sekmesinden bir dosya ayır.")
            return
        if self.llm_provider_var.get() == "manual":
            messagebox.showinfo(APP_TITLE, "Manuel modda: 'Prompt'u Panoya Kopyala' ile prompt'u al, bir LLM'e yapıştır, sonucu 'Manuel JSON Dosyası Yükle' ile geri yükle.")
            return
        sections = self._selected_sections()
        if not sections:
            messagebox.showwarning(APP_TITLE, "Script üretmek için en az bir bölüm seç.")
            return
        try:
            agent_max_sections = max(1, int(self.agent_max_sections_var.get()))
            agent_timeout = max(60, int(self.agent_timeout_var.get()))
        except (ValueError, tk.TclError):
            messagebox.showerror(APP_TITLE, "Agent bölüm sayısı ve timeout geçerli bir sayı olmalı.")
            return

        if self.script_insert_mode_var.get() == "Seçili slayttan sonra ekle":
            if self._current_slide_idx is None:
                messagebox.showwarning(APP_TITLE, "Önce yeni slaytların arkasına ekleneceği slaytı seç.")
                return
            self._script_insert_cursor = self._current_slide_idx + 1
        else:
            self._script_insert_cursor = len(self.slides)

        provider = self.llm_provider_var.get()
        self.settings["llm_provider"] = provider
        self.settings["gemini_model"] = self.gemini_model_var.get().strip()
        self.settings["openai_endpoint"] = self.openai_endpoint_var.get().strip()
        self.settings["openai_model"] = self.openai_model_var.get().strip()
        self.settings["single_request"] = self.single_request_var.get()
        self.settings["agent_command"] = self.agent_command_var.get().strip()
        self.settings["agent_reuse_session"] = self.agent_reuse_session_var.get()
        self.settings["agent_max_sections"] = agent_max_sections
        self.settings["agent_timeout_sec"] = agent_timeout
        save_settings(self.settings)
        style = self.style_note_var.get()
        single_request = self.single_request_var.get()
        existing_slides = list(self.slides)
        self._script_busy = True
        self.script_generate_button.state(["disabled"])
        self.script_progress["value"] = 0
        self.script_progress["maximum"] = 1
        self.script_progress_label_var.set("Hazırlanıyor…")
        if provider == "agent":
            threading.Thread(
                target=self._generate_script_worker_agent,
                args=(
                    sections, style, single_request, existing_slides,
                    self.agent_command_var.get().strip(),
                    self.agent_reuse_session_var.get(),
                    agent_max_sections, agent_timeout,
                ),
                daemon=True,
            ).start()
        elif provider == "openai":
            endpoint = self.openai_endpoint_var.get().strip()
            model = self.openai_model_var.get().strip()
            api_key = self.openai_key_var.get().strip() or None
            threading.Thread(
                target=self._generate_script_worker_openai,
                args=(sections, style, single_request, existing_slides, endpoint, model, api_key),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._generate_script_worker,
                args=(sections, style, single_request, existing_slides),
                daemon=True,
            ).start()

    def _generate_script_worker(self, sections, style, single_request, existing_slides):
        try:
            from app.llm.gemini_provider import GeminiNarrationGenerator, GeminiRateLimitError
            model = self.gemini_model_var.get().strip()
            gen = GeminiNarrationGenerator(model=model, api_key=self.gemini_key_var.get().strip() or None)

            def cb(i, total, title):
                mode = "tek istek" if single_request else f"parça {i}/{total}"
                self.log_queue.put(("status", f"Gemini ({model}) ile üretiliyor: {mode} — {title}"))
                self.log_queue.put(("script_progress", (i, total, title)))

            def chunk_cb(i, total, slides):
                self.log_queue.put(("script_chunk", (i, total, slides)))

            new_slides = gen.generate_chunked(
                sections, style, progress_cb=cb, single_request=single_request,
                chunk_result_cb=chunk_cb,
                initial_context_slides=existing_slides,
            )
            self.log_queue.put(("done_script", len(new_slides)))
        except GeminiRateLimitError as e:
            self.log_queue.put(("script_error", str(e)))
        except Exception as e:
            self.log_queue.put(("script_error", str(e)))

    def _generate_script_worker_agent(self, sections, style, single_request, existing_slides, command,
                                      reuse_session, max_sections, timeout):
        try:
            from app.llm.agent_cli_provider import AgentCliNarrationGenerator
            gen = AgentCliNarrationGenerator(
                command=command,
                timeout=timeout,
                reuse_session=reuse_session,
            )

            def cb(i, total, title):
                mode = "tek istek" if single_request else f"parça {i}/{total}"
                self.log_queue.put(("status", f"Agent ({command}) ile üretiliyor: {mode} — {title}"))
                self.log_queue.put(("script_progress", (i, total, title)))

            def chunk_cb(i, total, slides):
                self.log_queue.put(("script_chunk", (i, total, slides)))

            new_slides = gen.generate_chunked(
                sections,
                style,
                progress_cb=cb,
                single_request=single_request,
                request_interval_sec=0.5,
                max_sections_per_chunk=max_sections,
                chunk_result_cb=chunk_cb,
                initial_context_slides=existing_slides,
            )
            if gen.session_id:
                self.log_queue.put(("status", f"Claude oturumu: {gen.session_id}"))
            self.log_queue.put(("status", f"Agent toplam maliyet: ${gen.total_cost_usd:.4f}"))
            self.log_queue.put(("done_script", len(new_slides)))
        except Exception as e:
            self.log_queue.put(("script_error", str(e)))

    def _generate_script_worker_openai(self, sections, style, single_request, existing_slides,
                                       endpoint, model, api_key):
        try:
            from app.llm.openai_compatible_provider import OpenAICompatibleNarrationGenerator

            gen = OpenAICompatibleNarrationGenerator(
                endpoint=endpoint,
                model=model,
                api_key=api_key,
            )

            def cb(i, total, title):
                mode = "tek istek" if single_request else f"parça {i}/{total}"
                self.log_queue.put(("status", f"OpenAI uyumlu API ({model}) ile üretiliyor: {mode} — {title}"))
                self.log_queue.put(("script_progress", (i, total, title)))

            def chunk_cb(i, total, slides):
                self.log_queue.put(("script_chunk", (i, total, slides)))

            new_slides = gen.generate_chunked(
                sections,
                style,
                progress_cb=cb,
                single_request=single_request,
                chunk_result_cb=chunk_cb,
                initial_context_slides=existing_slides,
            )
            self.log_queue.put(("done_script", len(new_slides)))
        except Exception as e:
            self.log_queue.put(("script_error", str(e)))

    def _insert_script_chunk(self, new_slides):
        if not new_slides:
            return
        cursor = self._script_insert_cursor
        if cursor is None:
            cursor = len(self.slides)
        cursor = max(0, min(cursor, len(self.slides)))
        self.slides[cursor:cursor] = new_slides
        self._script_insert_cursor = cursor + len(new_slides)
        self._refresh_slide_tree(select_idx=self._script_insert_cursor - 1)
        self._persist_script_if_possible()

    def _persist_script_if_possible(self):
        if self.pdir:
            save_script(self.pdir, self.slides)

    def _finish_script_job(self):
        self._script_busy = False
        self.script_generate_button.state(["!disabled"])

    def _refresh_slide_tree(self, select_idx=None):
        self.slide_tree.delete(*self.slide_tree.get_children())
        for i, s in enumerate(self.slides):
            tag = "chapter" if s.level == "chapter" else "topic"
            self.slide_tree.insert("", "end", iid=str(i), values=(f"{i+1}. {s.title}",), tags=(tag,))
        self.slide_tree.tag_configure("chapter", foreground="#1f6fd6")
        if select_idx is not None and 0 <= select_idx < len(self.slides):
            iid = str(select_idx)
            self.slide_tree.selection_set(iid)
            self.slide_tree.focus(iid)
            self.slide_tree.see(iid)

    def _on_slide_select(self, _evt=None):
        sel = self.slide_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self._current_slide_idx = idx
        s = self.slides[idx]
        self.edit_title.delete(0, tk.END); self.edit_title.insert(0, s.title)
        self.edit_level.set(s.level)
        self.edit_bullets.delete("1.0", tk.END); self.edit_bullets.insert("1.0", "\n".join(s.bullets))
        self.edit_code.delete("1.0", tk.END); self.edit_code.insert("1.0", s.code or "")
        self.edit_narration.delete("1.0", tk.END); self.edit_narration.insert("1.0", s.narration)

    def _save_slide_edit(self):
        if self._current_slide_idx is None:
            messagebox.showwarning(APP_TITLE, "Önce soldan bir slayt seç.")
            return
        idx = self._current_slide_idx
        bullets = [b.strip() for b in self.edit_bullets.get("1.0", tk.END).splitlines() if b.strip()]
        code = self.edit_code.get("1.0", tk.END).strip() or None
        self.slides[idx] = Slide(
            title=self.edit_title.get().strip(),
            bullets=bullets,
            code=code,
            narration=self.edit_narration.get("1.0", tk.END).strip(),
            level=self.edit_level.get() or "topic",
        )
        self._refresh_slide_tree(select_idx=idx)
        self._persist_script_if_possible()
        self.status.set(f"Slayt {idx+1} kaydedildi.")

    def _delete_slide(self):
        if self._current_slide_idx is None:
            return
        deleted_idx = self._current_slide_idx
        del self.slides[deleted_idx]
        next_idx = min(deleted_idx, len(self.slides) - 1)
        self._current_slide_idx = next_idx if next_idx >= 0 else None
        self._refresh_slide_tree(select_idx=self._current_slide_idx)
        self._persist_script_if_possible()
        self.status.set(f"Slayt silindi. Toplam: {len(self.slides)}")

    def _add_slide(self):
        insert_at = self._current_slide_idx + 1 if self._current_slide_idx is not None else len(self.slides)
        self.slides.insert(
            insert_at,
            Slide(title="Yeni Slayt", bullets=[], code=None, narration="", level="topic"),
        )
        self._current_slide_idx = insert_at
        self._refresh_slide_tree(select_idx=insert_at)
        self._persist_script_if_possible()
        self.status.set(f"Yeni slayt {insert_at + 1}. sıraya eklendi.")

    def _move_slide(self, direction: int):
        if self._current_slide_idx is None:
            messagebox.showwarning(APP_TITLE, "Önce taşınacak slaytı seç.")
            return
        old_idx = self._current_slide_idx
        new_idx = old_idx + direction
        if not 0 <= new_idx < len(self.slides):
            return
        self.slides[old_idx], self.slides[new_idx] = self.slides[new_idx], self.slides[old_idx]
        self._current_slide_idx = new_idx
        self._refresh_slide_tree(select_idx=new_idx)
        self._persist_script_if_possible()
        self.status.set(f"Slayt {new_idx + 1}. sıraya taşındı.")

    def _load_script_from_disk(self):
        if not self.pdir:
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if not path:
                return
            import json
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.slides = [Slide.from_dict(d) for d in data]
        else:
            try:
                self.slides = load_script(self.pdir)
            except FileNotFoundError:
                messagebox.showwarning(APP_TITLE, "Bu proje için kaydedilmiş script bulunamadı.")
                return
        self._refresh_slide_tree()

    def _save_script_to_disk(self):
        if not self.pdir:
            messagebox.showwarning(APP_TITLE, "Önce Kaynak sekmesinden bir dosya ayır (proje klasörü oluşturulsun).")
            return
        save_script(self.pdir, self.slides)
        messagebox.showinfo(APP_TITLE, f"Kaydedildi: {self.pdir / 'script.json'}")

    # ---------- TAB 3: Video ----------
    def _build_video_tab(self):
        f = self.tab_video
        top = ttk.LabelFrame(f, text="Ses (TTS) Ayarları")
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Sağlayıcı:").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.tts_provider_var = tk.StringVar(value=self.settings.get("tts_provider", "edge"))
        provider_combo = ttk.Combobox(top, textvariable=self.tts_provider_var, state="readonly", width=55,
                                       values=[f"{k} — {v}" for k, v in PROVIDER_LABELS.items()])
        provider_combo.grid(row=0, column=1, sticky="w", padx=6)
        provider_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_voices())
        self._set_combo_by_key(provider_combo, self.tts_provider_var.get())

        ttk.Label(top, text="Ses:").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.voice_var = tk.StringVar(value=self.settings.get("tts_voice", "tr-TR-AhmetNeural"))
        self.voice_combo = ttk.Combobox(top, textvariable=self.voice_var, state="readonly", width=40)
        self.voice_combo.grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(top, text="Konuşma hızı:").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.rate_var = tk.StringVar(value=self.settings.get("tts_rate", "+0%"))
        ttk.Combobox(top, textvariable=self.rate_var, state="readonly", width=15,
                     values=["-20%", "-10%", "+0%", "+10%", "+20%"]).grid(row=2, column=1, sticky="w", padx=6)

        ttk.Label(top, text="ElevenLabs API Key (opsiyonel):").grid(row=3, column=0, sticky="e", padx=6, pady=4)
        self.eleven_key_var = tk.StringVar(value=get_api_key("ELEVENLABS_API_KEY") or "")
        ttk.Entry(top, textvariable=self.eleven_key_var, width=45, show="*").grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Kaydet", command=lambda: (save_api_key("ELEVENLABS_API_KEY", self.eleven_key_var.get().strip()), messagebox.showinfo(APP_TITLE, "Kaydedildi."))).grid(row=3, column=2, padx=6)

        opts = ttk.LabelFrame(f, text="Video Ayarları")
        opts.pack(fill="x", padx=10, pady=10)

        theme_row = ttk.Frame(opts)
        theme_row.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(theme_row, text="Görsel tema:").pack(side="left")
        self.theme_preset_var = tk.StringVar(value=self.settings.get("theme_preset", "auto"))
        self.theme_combo = ttk.Combobox(
            theme_row,
            textvariable=self.theme_preset_var,
            state="readonly",
            width=42,
            values=[f"{key} — {label}" for key, label in THEME_LABELS.items()],
        )
        self.theme_combo.pack(side="left", padx=8)
        self._set_combo_by_key(self.theme_combo, self.theme_preset_var.get())
        ttk.Button(theme_row, text="Temayı Önizle", command=self._preview_theme).pack(side="left", padx=4)
        ttk.Label(
            theme_row,
            text="Otomatik: bölüm, kod ve normal slaytlar için uygun görünümü kendi seçer.",
        ).pack(side="left", padx=8)

        effects_row = ttk.Frame(opts)
        effects_row.pack(fill="x", padx=10, pady=(2, 8))
        self.subtitles_var = tk.BooleanVar(value=self.settings.get("subtitles", True))
        self.fade_var = tk.BooleanVar(value=self.settings.get("fade_transitions", True))
        self.kenburns_var = tk.BooleanVar(value=self.settings.get("ken_burns", False))
        ttk.Checkbutton(effects_row, text="Altyazı göster", variable=self.subtitles_var).pack(side="left")
        ttk.Checkbutton(effects_row, text="Geçiş efekti (fade)", variable=self.fade_var).pack(side="left", padx=18)
        ttk.Checkbutton(effects_row, text="Hafif yakınlaştırma (Ken Burns)", variable=self.kenburns_var).pack(side="left")

        actions = ttk.Frame(f)
        actions.pack(fill="x", padx=10, pady=6)
        ttk.Button(actions, text="Videoyu Oluştur", command=self._render_threaded).pack(side="left")
        ttk.Button(actions, text="Çıktı Klasörünü Aç", command=self._open_output_folder).pack(side="left", padx=6)
        ttk.Button(actions, text="Videoyu Oynat", command=self._play_video).pack(side="left", padx=6)

        self.progress = ttk.Progressbar(f, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(4, 0))

        ttk.Label(f, text="Günlük:").pack(anchor="w", padx=10, pady=(8, 0))
        self.log_text = tk.Text(f, height=14)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._refresh_voices()

    def _set_combo_by_key(self, combo, key):
        for v in combo["values"]:
            if v.startswith(key + " —"):
                combo.set(v)
                return

    def _current_provider_key(self) -> str:
        raw = self.tts_provider_var.get()
        return raw.split(" —")[0].strip()

    def _current_theme_key(self) -> str:
        raw = self.theme_preset_var.get()
        return raw.split(" —")[0].strip()

    def _preview_theme(self):
        from app.video.slide_renderer import render_slide

        if self.slides:
            idx = self._current_slide_idx if self._current_slide_idx is not None else 0
            slide = self.slides[idx]
            index, total = idx + 1, len(self.slides)
        else:
            slide = Slide(
                title="Modern Ders Videosu Tasarımı",
                bullets=[
                    "Okunabilir tipografi ve güçlü görsel hiyerarşi",
                    "İçeriğe göre otomatik arka plan seçimi",
                    "Hazır temalarla tutarlı ve profesyonel görünüm",
                ],
                narration="Tema önizleme slaytı.",
                level="topic",
            )
            index, total = 1, 6

        preview_path = CACHE_DIR / "theme_preview.png"
        try:
            render_slide(
                slide, index, total, "TEMA ÖNİZLEMESİ", preview_path,
                theme_preset=self._current_theme_key(),
            )
            import os
            os.startfile(preview_path)
            self._log(f"Tema önizlemesi: {preview_path}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Tema önizlemesi oluşturulamadı:\n{e}")

    def _refresh_voices(self):
        key = self._current_provider_key()
        try:
            voices = list_voices(key)
        except Exception as e:
            voices = []
            self._log(f"[uyarı] {key} sağlayıcısı sesleri listeleyemedi: {e}")
        self.voice_combo["values"] = [v["id"] for v in voices]
        labels = {v["id"]: v["label"] for v in voices}
        if voices:
            self.voice_var.set(voices[0]["id"])
        self._voice_labels = labels

    def _render_threaded(self):
        if not self.slides:
            messagebox.showwarning(APP_TITLE, "Önce bir script üret / yükle (2. sekme).")
            return
        if not self.pdir:
            self.pdir = project_dir_for(self.source_var.get() or "proje")

        self.settings.update({
            "tts_provider": self._current_provider_key(),
            "tts_voice": self.voice_var.get(),
            "tts_rate": self.rate_var.get(),
            "subtitles": self.subtitles_var.get(),
            "fade_transitions": self.fade_var.get(),
            "ken_burns": self.kenburns_var.get(),
            "theme_preset": self._current_theme_key(),
        })
        save_settings(self.settings)
        save_script(self.pdir, self.slides)

        opts = VideoOptions(
            subtitles=self.subtitles_var.get(),
            fade_transitions=self.fade_var.get(),
            ken_burns=self.kenburns_var.get(),
            theme_preset=self._current_theme_key(),
        )
        provider_key = self._current_provider_key()
        voice = self.voice_var.get()
        rate = self.rate_var.get()
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.slides)
        threading.Thread(target=self._render_worker, args=(provider_key, voice, rate, opts), daemon=True).start()

    def _render_worker(self, provider_key, voice, rate, opts):
        try:
            def cb(i, total, title):
                self.log_queue.put(("progress", (i, total, title)))

            video, audio = render_video(self.pdir, self.slides, provider_key, voice, rate, opts, progress_cb=cb)
            self.log_queue.put(("done_video", (str(video), str(audio))))
        except Exception as e:
            self.log_queue.put(("error", str(e)))

    def _open_output_folder(self):
        if self.pdir:
            import os
            os.startfile(self.pdir)

    def _play_video(self):
        if self.pdir:
            import os
            p = self.pdir / "ders.mp4"
            if p.exists():
                os.startfile(p)
            else:
                messagebox.showwarning(APP_TITLE, "Henüz video üretilmedi.")

    def _log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def _poll_log(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                    self._log(payload)
                elif kind == "script_progress":
                    i, total, title = payload
                    self.script_progress["maximum"] = total
                    self.script_progress["value"] = max(i - 1, 0)
                    self.script_progress_label_var.set(f"Parça {i}/{total} hazırlanıyor — {title}")
                elif kind == "script_chunk":
                    i, total, slides = payload
                    self._insert_script_chunk(slides)
                    self.script_progress["maximum"] = total
                    self.script_progress["value"] = i
                    self.script_progress_label_var.set(
                        f"Parça {i}/{total} tamamlandı — {len(slides)} slayt"
                    )
                elif kind == "progress":
                    i, total, title = payload
                    self.progress["maximum"] = total
                    self.progress["value"] = i
                    self.status.set(f"[{i}/{total}] {title}")
                    self._log(f"[{i}/{total}] {title}")
                elif kind == "done_script":
                    self._finish_script_job()
                    self.script_progress["value"] = self.script_progress["maximum"]
                    self.script_progress_label_var.set(f"Tamamlandı — {payload} yeni slayt")
                    self.status.set(f"Script üretildi: {payload} yeni slayt.")
                    self._log(f"Script üretildi: {payload} yeni slayt.")
                elif kind == "script_error":
                    self._finish_script_job()
                    self.script_progress_label_var.set("Hata — tamamlanan parçalar korundu")
                    self._log(f"[HATA] {payload}")
                    messagebox.showerror(APP_TITLE, payload)
                elif kind == "done_video":
                    video, audio = payload
                    self.status.set(f"Bitti: {video}")
                    self._log(f"VIDEO: {video}\nAUDIO: {audio}")
                    messagebox.showinfo(APP_TITLE, f"Video hazır:\n{video}")
                elif kind == "error":
                    self._log(f"[HATA] {payload}")
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.after(150, self._poll_log)

    def _on_close(self):
        save_settings(self.settings)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
