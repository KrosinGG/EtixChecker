import asyncio
import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk
import pandas as pd
import tkinter as tk
from tkinter import messagebox

import app_core


DATA_DIR = Path("data")
SHOWS_CSV = DATA_DIR / "shows.csv"
PROFILES_PATH = DATA_DIR / "gui_profiles.json"
RERUN_SHOWS_CSV = DATA_DIR / "shows_rerun.csv"

FIELD_META: Dict[str, Dict[str, Any]] = {
    "tabs_count": {
        "label": "Вкладки (TABS_COUNT)",
        "desc": "Сколько вкладок/контекстов запускается одновременно.",
        "min": 15,
        "type": "int",
    },
    "slowmo_ms": {
        "label": "SLOWMO_MS",
        "desc": "Задержка Playwright между действиями (мс).",
        "min": 80,
        "type": "int",
    },
    "nav_timeout": {
        "label": "NAV_TIMEOUT",
        "desc": "Таймаут загрузки страницы (мс).",
        "min": 20000,
        "type": "int",
    },
    "click_timeout": {
        "label": "CLICK_TIMEOUT",
        "desc": "Таймаут клика по кнопкам (мс).",
        "min": 8500,
        "type": "int",
    },
    "batch_nav_delay_ms": {
        "label": "BATCH_NAV_DELAY_MS",
        "desc": "Пауза между партиями навигаций, диапазон (мс). Формат: 260,910.",
        "min": (260, 910),
        "type": "range",
    },
    "add_sequential_delay_ms": {
        "label": "ADD_SEQUENTIAL_DELAY_MS",
        "desc": "Пауза между последовательными добавлениями, диапазон (мс). Формат: 170,620.",
        "min": (170, 620),
        "type": "range",
    },
    "delay_before_clear_carts_s": {
        "label": "DELAY_BEFORE_CLEAR_CARTS_S",
        "desc": "Пауза перед очисткой корзин (сек).",
        "min": 1.5,
        "type": "float",
    },
}

DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "Обычная": {
        "tabs_count": 24,
        "slowmo_ms": 80,
        "nav_timeout": 45000,
        "click_timeout": 20000,
        "batch_nav_delay_ms": "260,910",
        "add_sequential_delay_ms": "500,850",
        "delay_before_clear_carts_s": 5,
        "headless": False,
        "randomize_proxies": True,
    },
    "Быстрая": {
        "tabs_count": 24,
        "slowmo_ms": 80,
        "nav_timeout": 20000,
        "click_timeout": 8500,
        "batch_nav_delay_ms": "260,910",
        "add_sequential_delay_ms": "170,620",
        "delay_before_clear_carts_s": 1.5,
        "headless": True,
        "randomize_proxies": True,
    },
}


def _format_number(value: float) -> str:
    return f"{value:g}"


def _min_text(value: Any) -> str:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{int(value[0])},{int(value[1])}"
    if isinstance(value, float):
        return _format_number(value)
    return str(value)


def load_profiles() -> Dict[str, Dict[str, Any]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PROFILES_PATH.exists():
        try:
            raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw:
                return raw
        except Exception:
            pass
    PROFILES_PATH.write_text(
        json.dumps(DEFAULT_PROFILES, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json.loads(json.dumps(DEFAULT_PROFILES))


def save_profiles(profiles: Dict[str, Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def calc_total_from_csv(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path).fillna("")
        if df.empty:
            return 0
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if "url" not in df.columns:
            return 0
        urls = df["url"].astype(str).str.strip()
        return int(urls.str.match(r"^https?://", na=False).sum())
    except Exception:
        return 0


def open_path(path: Path) -> None:
    if not path.exists():
        return
    try:
        os.startfile(str(path))
    except Exception:
        pass


class HoverTooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None) -> None:
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        try:
            self.tip.attributes("-topmost", True)
        except Exception:
            pass
        label = tk.Label(
            self.tip,
            text=self.text,
            justify="left",
            background="#2b2b2b",
            foreground="#f5f5f5",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
        )
        label.pack()
        self.tip.geometry(f"+{x}+{y}")

    def hide(self, _event=None) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None


class EtixApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("ETIX CHECKER")
        self.geometry("980x720")

        self.profiles = load_profiles()
        default_profile = "Обычная"
        if default_profile not in self.profiles and self.profiles:
            default_profile = next(iter(self.profiles))
        self.profile_var = ctk.StringVar(value=default_profile)
        self.manual_var = ctk.BooleanVar(value=False)
        self.same_proxies_var = ctk.BooleanVar(value=False)
        self.same_proxies_available = False
        self.running = False
        self.queue: queue.Queue = queue.Queue()
        self.rows: List[dict] = []
        self.manual_check_rows: List[dict] = []
        self.original_shows_df: Optional[pd.DataFrame] = None
        self.tooltips: List[HoverTooltip] = []

        self._build_ui()

    def _build_ui(self) -> None:
        header = ctk.CTkLabel(
            self, text="ETIX CHECKER", font=ctk.CTkFont(size=22, weight="bold")
        )
        header.pack(pady=(12, 4))

        top_frame = ctk.CTkFrame(self)
        top_frame.pack(fill="x", padx=12, pady=8)

        profile_label = ctk.CTkLabel(top_frame, text="Профиль")
        profile_label.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        profile_menu = ctk.CTkOptionMenu(
            top_frame,
            values=list(self.profiles.keys()),
            variable=self.profile_var,
            command=lambda _v: self.load_profile_to_form(),
        )
        profile_menu.grid(row=0, column=1, padx=6, pady=6, sticky="w")

        self.manual_check = ctk.CTkCheckBox(
            top_frame,
            text="Полуавтоматический режим (капча на 15 вкладках)",
            variable=self.manual_var,
        )
        self.manual_check.grid(row=0, column=2, padx=6, pady=6, sticky="w")

        self.start_button = ctk.CTkButton(top_frame, text="Запуск", command=self.on_start)
        self.start_button.grid(row=0, column=3, padx=6, pady=6, sticky="e")

        self.recheck_button = ctk.CTkButton(
            top_frame, text="Перепроверить", command=self.on_recheck
        )
        self.recheck_button.grid(row=0, column=4, padx=6, pady=6, sticky="e")
        self.recheck_button.grid_remove()

        self.toggle_settings_button = ctk.CTkButton(
            top_frame, text="Показать настройки", command=self.toggle_settings
        )
        self.toggle_settings_button.grid(row=0, column=5, padx=6, pady=6, sticky="e")

        self.same_proxies_check = ctk.CTkCheckBox(
            top_frame, text="Использовать те же прокси", variable=self.same_proxies_var
        )
        self.same_proxies_check.grid(row=1, column=1, padx=6, pady=6, sticky="w")
        self.same_proxies_check.grid_remove()

        self.headless_var = ctk.BooleanVar()
        self.randomize_var = ctk.BooleanVar()

        self.settings_frame = ctk.CTkFrame(self)
        self.settings_frame.pack(fill="x", padx=12, pady=8)
        self.settings_frame.grid_columnconfigure(0, weight=1)
        self.settings_frame.grid_columnconfigure(1, weight=1)

        self.fields: Dict[str, ctk.CTkEntry] = {}
        self._add_field(self.settings_frame, "tabs_count", 0, 0)
        self._add_field(self.settings_frame, "slowmo_ms", 0, 1)
        self._add_field(self.settings_frame, "nav_timeout", 1, 0)
        self._add_field(self.settings_frame, "click_timeout", 1, 1)
        self._add_field(self.settings_frame, "batch_nav_delay_ms", 2, 0)
        self._add_field(self.settings_frame, "add_sequential_delay_ms", 2, 1)
        self._add_field(self.settings_frame, "delay_before_clear_carts_s", 3, 0)

        self.headless_check = ctk.CTkCheckBox(
            self.settings_frame, text="Headless", variable=self.headless_var
        )
        self.headless_check.grid(row=4, column=0, padx=6, pady=6, sticky="w")

        self.randomize_check = ctk.CTkCheckBox(
            self.settings_frame, text="Randomize Proxies", variable=self.randomize_var
        )
        self.randomize_check.grid(row=4, column=1, padx=6, pady=6, sticky="w")

        save_button = ctk.CTkButton(
            self.settings_frame, text="Сохранить профиль", command=self.on_save_profile
        )
        save_button.grid(row=5, column=1, padx=6, pady=8, sticky="e")

        self.settings_visible = True
        self.toggle_settings()

        self.status_frame = ctk.CTkFrame(self)
        self.status_frame.pack(fill="x", padx=12, pady=8)

        self.status_label = ctk.CTkLabel(self.status_frame, text="Готов к запуску")
        self.status_label.pack(side="left", padx=6)

        self.progress = ctk.CTkProgressBar(self.status_frame)
        self.progress.set(0)
        self.progress.pack(side="right", fill="x", expand=True, padx=6)

        actions = ctk.CTkFrame(self)
        actions.pack(fill="x", padx=12, pady=8)
        ctk.CTkButton(actions, text="Открыть report.csv", command=self.open_report).pack(
            side="left", padx=6
        )
        ctk.CTkButton(actions, text="Открыть shows.csv", command=self.open_shows).pack(
            side="left", padx=6
        )
        ctk.CTkButton(actions, text="Открыть logs", command=self.open_logs).pack(
            side="left", padx=6
        )

        self.results_frame = ctk.CTkScrollableFrame(self, height=360)
        self.results_frame.pack(fill="both", expand=True, padx=12, pady=8)

        self.load_profile_to_form()

    def toggle_settings(self) -> None:
        if getattr(self, "settings_visible", False):
            self.settings_frame.pack_forget()
            self.settings_visible = False
            self.toggle_settings_button.configure(text="Показать настройки")
        else:
            try:
                self.settings_frame.pack(fill="x", padx=12, pady=8, before=self.status_frame)
            except Exception:
                self.settings_frame.pack(fill="x", padx=12, pady=8)
            self.settings_visible = True
            self.toggle_settings_button.configure(text="Скрыть настройки")

    def _add_field(self, parent, key: str, row: int, col: int) -> None:
        meta = FIELD_META[key]
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=col, padx=6, pady=6, sticky="w")

        label = ctk.CTkLabel(frame, text=meta["label"])
        label.grid(row=0, column=0, padx=(0, 6), sticky="w")

        entry = ctk.CTkEntry(frame, width=140)
        entry.grid(row=0, column=1, padx=(0, 6), sticky="w")
        self.fields[key] = entry

        info = ctk.CTkLabel(
            frame,
            text="?",
            width=18,
            height=18,
            fg_color="#3a3a3a",
            text_color="#e5e5e5",
            corner_radius=9,
        )
        info.grid(row=0, column=2, sticky="w")

        tooltip_text = f"{meta['desc']}\nМинимум: {_min_text(meta['min'])}"
        self.tooltips.append(HoverTooltip(info, tooltip_text))

    @staticmethod
    def _parse_int(value: object, default: int) -> Tuple[int, bool]:
        try:
            return int(float(str(value).strip())), True
        except Exception:
            return default, False

    @staticmethod
    def _parse_float(value: object, default: float) -> Tuple[float, bool]:
        try:
            return float(str(value).strip()), True
        except Exception:
            return default, False

    @staticmethod
    def _parse_range(value: object, default: Tuple[int, int]) -> Tuple[Tuple[int, int], bool]:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                lo = int(float(value[0]))
                hi = int(float(value[1]))
                return (lo, hi), True
            except Exception:
                return default, False
        if isinstance(value, str) and "," in value:
            parts = [p.strip() for p in value.split(",") if p.strip()]
            if len(parts) == 2:
                try:
                    lo = int(float(parts[0]))
                    hi = int(float(parts[1]))
                    return (lo, hi), True
                except Exception:
                    return default, False
        return default, False

    def _normalize_profile(self, profile: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        corrected: Dict[str, Any] = {}
        warnings: List[str] = []

        for key, meta in FIELD_META.items():
            raw = profile.get(key, "")
            min_val = meta["min"]
            label = meta["label"]
            field_type = meta["type"]

            if field_type == "int":
                val, ok = self._parse_int(raw, int(min_val))
                fixed = False
                if not ok:
                    fixed = True
                if val < min_val:
                    fixed = True
                    val = int(min_val)
                if fixed:
                    warnings.append(f"{label}: значение ниже минимума, установлено {min_val}")
                corrected[key] = str(val)
            elif field_type == "float":
                val, ok = self._parse_float(raw, float(min_val))
                fixed = False
                if not ok:
                    fixed = True
                if val < min_val:
                    fixed = True
                    val = float(min_val)
                if fixed:
                    warnings.append(f"{label}: значение ниже минимума, установлено {min_val}")
                corrected[key] = _format_number(val)
            elif field_type == "range":
                default = (int(min_val[0]), int(min_val[1]))
                (lo, hi), ok = self._parse_range(raw, default)
                fixed = False
                if not ok:
                    fixed = True
                if lo < min_val[0]:
                    lo = int(min_val[0])
                    fixed = True
                if hi < min_val[1]:
                    hi = int(min_val[1])
                    fixed = True
                if hi < lo:
                    hi = lo
                    fixed = True
                if fixed:
                    warnings.append(
                        f"{label}: значение ниже минимума, установлено {lo},{hi}"
                    )
                corrected[key] = f"{lo},{hi}"
            else:
                corrected[key] = raw

        corrected["headless"] = bool(profile.get("headless", False))
        corrected["randomize_proxies"] = bool(profile.get("randomize_proxies", True))
        return corrected, warnings

    def _apply_profile_to_form(self, profile: Dict[str, Any]) -> None:
        for key, entry in self.fields.items():
            val = profile.get(key, "")
            entry.delete(0, "end")
            entry.insert(0, str(val))
        self.headless_var.set(bool(profile.get("headless", False)))
        self.randomize_var.set(bool(profile.get("randomize_proxies", True)))

    def load_profile_to_form(self) -> None:
        profile = self.profiles.get(self.profile_var.get(), {})
        normalized, _warnings = self._normalize_profile(profile)
        self.profiles[self.profile_var.get()] = normalized
        save_profiles(self.profiles)
        self._apply_profile_to_form(normalized)

    def get_profile_from_form(self) -> Dict[str, Any]:
        profile: Dict[str, Any] = {}
        for key, entry in self.fields.items():
            profile[key] = entry.get().strip()
        profile["headless"] = self.headless_var.get()
        profile["randomize_proxies"] = self.randomize_var.get()
        return profile

    def _warn_if_needed(self, warnings: List[str]) -> None:
        if warnings:
            messagebox.showwarning("Исправлены значения", "\n".join(warnings))

    def on_save_profile(self) -> None:
        name = self.profile_var.get()
        raw = self.get_profile_from_form()
        normalized, warnings = self._normalize_profile(raw)
        self.profiles[name] = normalized
        save_profiles(self.profiles)
        self._apply_profile_to_form(normalized)
        self._warn_if_needed(warnings)
        self.status_label.configure(text=f"Профиль '{name}' сохранен")

    def on_start(self) -> None:
        if self.running:
            return
        if not self.same_proxies_available:
            self.same_proxies_var.set(False)
        self.recheck_button.grid_remove()
        self.rows = []
        self.manual_check_rows = []
        self._clear_results()
        self._start_run(rerun_urls=None)

    def on_recheck(self) -> None:
        if self.running:
            return
        if not self.manual_check_rows:
            return
        urls = [r.get("url") for r in self.manual_check_rows if r.get("url")]
        if not urls:
            return
        self.recheck_button.grid_remove()
        self.rows = []
        self.manual_check_rows = []
        self._clear_results()
        self._start_run(rerun_urls=urls)

    def _start_run(self, rerun_urls: Optional[List[str]]) -> None:
        raw = self.get_profile_from_form()
        profile, warnings = self._normalize_profile(raw)
        self.profiles[self.profile_var.get()] = profile
        save_profiles(self.profiles)
        self._apply_profile_to_form(profile)
        self._warn_if_needed(warnings)
        app_core.apply_runtime_profile(profile)

        manual_mode = self.manual_var.get()
        use_same_proxies = self.same_proxies_var.get() if self.same_proxies_available else False
        if manual_mode and self.headless_var.get():
            messagebox.showinfo(
                "Полуавтоматический режим",
                "В полуавтоматическом режиме HEADLESS=False на этот запуск.",
            )

        shows_path = SHOWS_CSV
        if rerun_urls:
            shows_path = self._build_rerun_csv(rerun_urls)

        total = calc_total_from_csv(shows_path)
        self.status_label.configure(text=f"Проверено 0/{total}")
        self.progress.set(0)

        self.running = True
        self.start_button.configure(state="disabled")

        worker = threading.Thread(
            target=self._run_async,
            args=(manual_mode, use_same_proxies, shows_path),
            daemon=True,
        )
        worker.start()
        self.after(100, self._poll_queue)

    def _run_async(self, manual_mode: bool, use_same_proxies: bool, shows_path: Path) -> None:
        async def on_show_done(row: dict, done_idx: int, total_from_core: int) -> None:
            self.queue.put(("row", row, done_idx, total_from_core))

        async def runner():
            try:
                rows = await app_core.check_shows(
                    on_show_done=on_show_done,
                    resume_mode="fresh",
                    run_root="runs",
                    manual_captcha_first_show=manual_mode,
                    use_same_proxies=use_same_proxies,
                    shows_path=shows_path,
                )
                self.queue.put(("done", rows))
            except Exception as exc:
                self.queue.put(("error", str(exc)))

        asyncio.run(runner())

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "row":
                    _tag, row, done_idx, total = item
                    self.rows.append(row)
                    self._append_result(row)
                    if total:
                        self.status_label.configure(text=f"Проверено {done_idx}/{total}")
                        self.progress.set(min(1.0, done_idx / total))
                elif item[0] == "done":
                    _tag, rows = item
                    self.running = False
                    self.start_button.configure(state="normal")
                    self._finalize_run(rows)
                elif item[0] == "error":
                    _tag, msg = item
                    self.running = False
                    self.start_button.configure(state="normal")
                    messagebox.showerror("Ошибка", msg)
        except queue.Empty:
            pass

        if self.running:
            self.after(100, self._poll_queue)

    def _finalize_run(self, rows: List[dict]) -> None:
        manual_rows = [r for r in rows if self._is_manual_check(r)]
        self.manual_check_rows = manual_rows
        if manual_rows:
            self.recheck_button.grid()
        if not self.same_proxies_available:
            self.same_proxies_available = True
            self.same_proxies_check.grid()
        self.status_label.configure(text="Готово")

    def _is_manual_check(self, row: dict) -> bool:
        status = (row.get("status") or "").lower()
        notes = str(row.get("notes") or "")
        if "manual_ended" in notes.lower() or "manual_sold_out" in notes.lower():
            return False
        if app_core.MANUAL_INSUFFICIENT_MARKER in notes:
            return False
        if "sold out" in status or "ended" in status or status.startswith("ok"):
            return False
        if ("недостаточно" in status) or ("insufficient" in status):
            return False
        return True

    def _append_result(self, row: dict) -> None:
        name = (row.get("name") or row.get("url") or "").strip()
        status_raw = (row.get("status") or "")
        status = status_raw.lower()
        notes = str(row.get("notes") or "")

        try:
            est = int(row.get("success_carts", 0)) * int(row.get("per_order_limit", 0))
        except Exception:
            est = 0

        if "manual_ended" in notes.lower():
            status_text = "Ended"
            color = "#b36bff"
        elif "manual_sold_out" in notes.lower():
            status_text = "SOLD OUT"
            color = "#ff5c5c"
        elif app_core.MANUAL_INSUFFICIENT_MARKER in notes:
            suffix = f" ({est})" if est else ""
            status_text = f"Билетов недостаточно{suffix}"
            color = "#ff5c5c"
        elif "sold out" in status:
            status_text = "SOLD OUT"
            color = "#ff5c5c"
        elif "ended" in status:
            status_text = "Ended"
            color = "#b36bff"
        elif status.startswith("ok"):
            status_text = "OK"
            color = "#5cff8a"
        elif ("недостаточно" in status) or ("insufficient" in status):
            suffix = f" ({est})" if est else ""
            status_text = f"Билетов недостаточно{suffix}"
            color = "#ff5c5c"
        else:
            status_text = "Проверь вручную"
            color = "#ffd45c"

        text = f"{name} - {status_text}"
        ctk.CTkLabel(
            self.results_frame, text=text, text_color=color, anchor="w"
        ).pack(fill="x", padx=6, pady=2)

    def _clear_results(self) -> None:
        for child in self.results_frame.winfo_children():
            child.destroy()

    def _build_rerun_csv(self, urls: List[str]) -> Path:
        try:
            df = pd.read_csv(SHOWS_CSV).fillna("")
            self.original_shows_df = df.copy()
        except Exception:
            df = pd.DataFrame()
        urls_set = {u.strip() for u in urls if u}
        if not df.empty and "url" in df.columns:
            df = df[df["url"].astype(str).str.strip().isin(urls_set)]
        if df.empty:
            df = pd.DataFrame({"url": list(urls_set)})
        RERUN_SHOWS_CSV.write_text("", encoding="utf-8")
        df.to_csv(RERUN_SHOWS_CSV, index=False, encoding="utf-8")
        return RERUN_SHOWS_CSV

    def open_report(self) -> None:
        open_path(Path("report.csv"))

    def open_shows(self) -> None:
        open_path(SHOWS_CSV)

    def open_logs(self) -> None:
        open_path(Path("logs"))


if __name__ == "__main__":
    app = EtixApp()
    app.mainloop()
