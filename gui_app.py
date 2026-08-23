"""Modern Graphical User Interface for Etix Checker using CustomTkinter with Slate-Indigo 3D Card theme."""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import customtkinter as ctk
import pandas as pd
from tkinter import messagebox

from src.adspower.backup_service import ProfileBackupService
from src.adspower.client import AdsPowerClient
from src.adspower.profile_manager import AdsPowerProfileManager
from src.config.settings import AppConfig, CONFIG
from src.domain.enums import ShowStatus
from src.domain.models import CheckResult, Show
from src.etix.checker import EtixCheckEngine
from src.utils.logger import LOGGER

# Configure appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Design Tokens (Slate-Navy / Indigo Gradient Palette)
FONT_FAMILY = "Segoe UI"
COLOR_BG = "#0b0f19"              # Main window background (Deep Space)
COLOR_CARD = "#121829"            # Primary card surface
COLOR_CARD_BORDER = "#1e293b"     # Card border outline
COLOR_CARD_INNER = "#182038"      # Inner nested item surface
COLOR_CARD_INNER_HOVER = "#202c4c"
COLOR_TEXT_PRIMARY = "#f8fafc"    # Bright text
COLOR_TEXT_MUTED = "#94a3b8"      # Muted subtext
COLOR_TEXT_ACCENT = "#818cf8"     # Indigo text

# Button Styling (Indigo Gradient Inspired)
COLOR_BTN_PRIMARY = "#4338ca"     # Deep Indigo
COLOR_BTN_PRIMARY_HOVER = "#4f46e5"
COLOR_BTN_SEC = "#1e293b"         # Dark slate
COLOR_BTN_SEC_HOVER = "#2a374f"
COLOR_BTN_SEC_BORDER = "#334155"

# Status Badges Colors (bg, text, border)
STATUS_COLORS = {
    ShowStatus.OK: {"bg": "#064e3b", "text": "#34d399", "border": "#059669"},
    ShowStatus.PARTIAL: {"bg": "#78350f", "text": "#fbbf24", "border": "#d97706"},
    ShowStatus.SOLD_OUT: {"bg": "#7f1d1d", "text": "#f87171", "border": "#dc2626"},
    ShowStatus.ENDED: {"bg": "#374151", "text": "#9ca3af", "border": "#4b5563"},
    ShowStatus.BLOCKED: {"bg": "#581c87", "text": "#c084fc", "border": "#9333ea"},
    ShowStatus.FAILED: {"bg": "#881337", "text": "#fb7185", "border": "#e11d48"},
    ShowStatus.IN_FLIGHT: {"bg": "#1e3a8a", "text": "#60a5fa", "border": "#2563eb"},
    ShowStatus.PENDING: {"bg": "#1e293b", "text": "#94a3b8", "border": "#334155"},
}


class ShowCardWidget(ctk.CTkFrame):
    """Modern card item representing a single monitored event."""

    def __init__(self, master, show: Show, **kwargs):
        super().__init__(
            master,
            fg_color=COLOR_CARD_INNER,
            border_color="#263352",
            border_width=1,
            corner_radius=10,
            **kwargs,
        )
        self.show = show
        self._build_card()

    def _build_card(self):
        # Left section: Icon + Event info
        self.left_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.left_frame.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        title_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        title_frame.pack(fill="x", anchor="w")

        self.lbl_icon = ctk.CTkLabel(
            title_frame,
            text="🎟️",
            font=ctk.CTkFont(size=14),
        )
        self.lbl_icon.pack(side="left", padx=(0, 6))

        self.lbl_name = ctk.CTkLabel(
            title_frame,
            text=self.show.name,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
            anchor="w",
        )
        self.lbl_name.pack(side="left", fill="x")

        # Subtitle details (Target & ticket index)
        sub_info = f"Цель: {self.show.target_total} шт. • Лимит: {self.show.max_per_order}/заказ"
        if self.show.ticket_index:
            sub_info += f" • Тип билета #{self.show.ticket_index}"

        self.lbl_sub = ctk.CTkLabel(
            self.left_frame,
            text=sub_info,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.lbl_sub.pack(fill="x", anchor="w", pady=(2, 0))

        # Center section: Progress Bar & Reserved Count
        self.center_frame = ctk.CTkFrame(self, fg_color="transparent", width=180)
        self.center_frame.pack(side="left", padx=12, pady=10)

        self.lbl_count = ctk.CTkLabel(
            self.center_frame,
            text=f"0 / {self.show.target_total}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLOR_TEXT_MUTED,
        )
        self.lbl_count.pack(anchor="center")

        self.progress_bar = ctk.CTkProgressBar(
            self.center_frame,
            width=160,
            height=6,
            corner_radius=3,
            fg_color="#0f172a",
            progress_color="#6366f1",
        )
        self.progress_bar.set(0.0)
        self.progress_bar.pack(anchor="center", pady=(4, 0))

        # Right section: Status Badge
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent", width=140)
        self.right_frame.pack(side="right", padx=14, pady=10)

        self.lbl_status = ctk.CTkLabel(
            self.right_frame,
            text="PENDING",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color="#94a3b8",
            fg_color="#1e293b",
            corner_radius=6,
            width=100,
            height=26,
        )
        self.lbl_status.pack(anchor="e")

    def update_result(self, res: CheckResult):
        """Update card state with check outcome."""
        color_info = STATUS_COLORS.get(res.status, STATUS_COLORS[ShowStatus.PENDING])
        self.lbl_status.configure(
            text=res.status.value,
            text_color=color_info["text"],
            fg_color=color_info["bg"],
        )

        ratio = 0.0
        if res.target > 0:
            ratio = min(1.0, max(0.0, res.reserved / res.target))

        self.lbl_count.configure(
            text=f"{res.reserved} / {res.target}",
            text_color=COLOR_TEXT_PRIMARY if res.reserved > 0 else COLOR_TEXT_MUTED,
        )

        # Progress bar color logic
        if res.status == ShowStatus.OK:
            self.progress_bar.configure(progress_color="#10b981")
            self.progress_bar.set(1.0)
        elif res.status == ShowStatus.PARTIAL:
            self.progress_bar.configure(progress_color="#f59e0b")
            self.progress_bar.set(ratio)
        elif res.status in (ShowStatus.SOLD_OUT, ShowStatus.ENDED, ShowStatus.BLOCKED, ShowStatus.FAILED):
            self.progress_bar.configure(progress_color="#ef4444")
            self.progress_bar.set(ratio)
        else:
            self.progress_bar.set(ratio)

        if res.details:
            self.lbl_sub.configure(text=f"{res.details}")


class EtixGuiApp(ctk.CTk):
    """Modern Dark Card Dashboard for Etix Checker."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Etix Checker 2026 — AdsPower Edition")
        self.geometry("1020x760")
        self.minsize(900, 640)
        self.configure(fg_color=COLOR_BG)

        icon_file = Path("icons/etix_robot_round.ico")
        if icon_file.exists():
            try:
                self.iconbitmap(str(icon_file))
            except Exception:
                pass

        self.event_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self.client = AdsPowerClient(base_url=CONFIG.adspower_api_url)
        self.profile_manager = AdsPowerProfileManager(client=self.client)
        self.backup_service = ProfileBackupService()
        self.show_cards: Dict[str, ShowCardWidget] = {}

        self._build_ui()
        self._load_shows_preview()
        self._check_adspower_status_async()
        self._poll_queue()

    def _build_ui(self) -> None:
        # 1. Top Header Card (Inspired by 3D Card Design)
        self.header_card = ctk.CTkFrame(
            self,
            fg_color=COLOR_CARD,
            border_color=COLOR_CARD_BORDER,
            border_width=1,
            corner_radius=14,
        )
        self.header_card.pack(fill="x", padx=20, pady=(16, 10))

        header_inner = ctk.CTkFrame(self.header_card, fg_color="transparent")
        header_inner.pack(fill="x", padx=18, pady=14)

        # Title & Subtitle with Icon
        title_left = ctk.CTkFrame(header_inner, fg_color="transparent")
        title_left.pack(side="left", fill="both", expand=True)

        title_row = ctk.CTkFrame(title_left, fg_color="transparent")
        title_row.pack(anchor="w")

        self.badge_tag = ctk.CTkLabel(
            title_row,
            text="✨ CDP 2026",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
            text_color="#818cf8",
            fg_color="#1e1b4b",
            corner_radius=6,
            padx=6,
            pady=2,
        )
        self.badge_tag.pack(side="left", padx=(0, 8))

        self.lbl_title = ctk.CTkLabel(
            title_row,
            text="Etix Checker — AdsPower CDP Edition",
            font=ctk.CTkFont(family=FONT_FAMILY, size=17, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
        )
        self.lbl_title.pack(side="left")

        # AdsPower Status indicator with glowing pill
        self.adspower_pill = ctk.CTkFrame(
            header_inner,
            fg_color="#182038",
            border_color="#263352",
            border_width=1,
            corner_radius=8,
        )
        self.adspower_pill.pack(side="right", padx=(10, 0))

        self.lbl_adspower_dot = ctk.CTkLabel(
            self.adspower_pill,
            text="●",
            font=ctk.CTkFont(size=14),
            text_color="#f59e0b",
        )
        self.lbl_adspower_dot.pack(side="left", padx=(10, 4), pady=6)

        self.lbl_adspower = ctk.CTkLabel(
            self.adspower_pill,
            text="AdsPower: Проверка...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLOR_TEXT_MUTED,
        )
        self.lbl_adspower.pack(side="left", padx=(0, 10), pady=6)

        # 2. Metric Stat Badges Bar
        self.stats_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_bar.pack(fill="x", padx=20, pady=(0, 10))

        self.stat_shows = self._create_metric_chip(self.stats_bar, "📋 Событий", "0")
        self.stat_shows.pack(side="left", padx=(0, 8))

        self.stat_reserved = self._create_metric_chip(self.stats_bar, "📦 Набрано билетов", "0 / 0")
        self.stat_reserved.pack(side="left", padx=8)

        self.stat_workers = self._create_metric_chip(self.stats_bar, "⚡ Активных воркеров", "—")
        self.stat_workers.pack(side="left", padx=8)

        self.stat_status = self._create_metric_chip(self.stats_bar, "🚀 Статус", "Готов к запуску")
        self.stat_status.pack(side="left", padx=8)

        # 3. Action Toolbar (Styled Buttons)
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=20, pady=(0, 12))

        # Primary Start Button (Indigo gradient style)
        self.btn_start = ctk.CTkButton(
            self.btn_frame,
            text="▶  Запустить проверку",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLOR_BTN_PRIMARY,
            hover_color=COLOR_BTN_PRIMARY_HOVER,
            text_color="#ffffff",
            height=38,
            corner_radius=8,
            command=self._on_start_clicked,
        )
        self.btn_start.pack(side="left", padx=(0, 8))

        # Secondary Action Buttons
        self.btn_backup = self._create_action_btn("💾  Бэкап профилей", self._on_backup_clicked)
        self.btn_backup.pack(side="left", padx=6)

        self.btn_shows = self._create_action_btn("📄  shows.csv", lambda: self._open_file(CONFIG.shows_csv))
        self.btn_shows.pack(side="left", padx=6)

        self.btn_report = self._create_action_btn("📊  report.csv", lambda: self._open_file(Path("report.csv")))
        self.btn_report.pack(side="left", padx=6)

        self.btn_logs = self._create_action_btn("📁  Логи", lambda: self._open_folder(CONFIG.logs_dir))
        self.btn_logs.pack(side="left", padx=6)

        # 4. Main Content Card with Tabs (Dashboard Cards vs Live Logs)
        self.main_card = ctk.CTkFrame(
            self,
            fg_color=COLOR_CARD,
            border_color=COLOR_CARD_BORDER,
            border_width=1,
            corner_radius=14,
        )
        self.main_card.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        # Tabview
        self.tabview = ctk.CTkTabview(
            self.main_card,
            fg_color="transparent",
            segmented_button_fg_color="#0f172a",
            segmented_button_selected_color=COLOR_BTN_PRIMARY,
            segmented_button_selected_hover_color=COLOR_BTN_PRIMARY_HOVER,
            segmented_button_unselected_color="#182038",
            segmented_button_unselected_hover_color="#202c4c",
            text_color=COLOR_TEXT_PRIMARY,
            corner_radius=10,
        )
        self.tabview.pack(fill="both", expand=True, padx=12, pady=8)

        self.tab_dashboard = self.tabview.add("📊  Панель мониторинга")
        self.tab_logs = self.tabview.add("📜  Журнал событий (Лог)")

        # Tab 1: Scrollable Show Cards
        self.scroll_shows = ctk.CTkScrollableFrame(
            self.tab_dashboard,
            fg_color="transparent",
        )
        self.scroll_shows.pack(fill="both", expand=True, padx=4, pady=4)

        # Tab 2: Terminal Logs View
        self.txt_log = ctk.CTkTextbox(
            self.tab_logs,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0b0f19",
            text_color="#e2e8f0",
            border_color="#1e293b",
            border_width=1,
            corner_radius=8,
            wrap="none",
        )
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)

    def _create_metric_chip(self, parent, label: str, value: str) -> ctk.CTkFrame:
        """Create a sleek metric badge chip."""
        chip = ctk.CTkFrame(
            parent,
            fg_color=COLOR_CARD,
            border_color=COLOR_CARD_BORDER,
            border_width=1,
            corner_radius=8,
        )
        lbl_k = ctk.CTkLabel(
            chip,
            text=label,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLOR_TEXT_MUTED,
        )
        lbl_k.pack(anchor="w", padx=10, pady=(6, 0))

        lbl_v = ctk.CTkLabel(
            chip,
            text=value,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
        )
        lbl_v.pack(anchor="w", padx=10, pady=(0, 6))
        chip.lbl_val = lbl_v
        return chip

    def _create_action_btn(self, text: str, command) -> ctk.CTkButton:
        """Create a styled secondary action button."""
        return ctk.CTkButton(
            self.btn_frame,
            text=text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLOR_BTN_SEC,
            hover_color=COLOR_BTN_SEC_HOVER,
            border_color=COLOR_BTN_SEC_BORDER,
            border_width=1,
            text_color=COLOR_TEXT_PRIMARY,
            height=38,
            corner_radius=8,
            command=command,
        )

    def _load_shows_preview(self) -> None:
        """Load shows from shows.csv and generate cards."""
        for child in self.scroll_shows.winfo_children():
            child.destroy()
        self.show_cards.clear()

        engine = EtixCheckEngine(config=CONFIG)
        shows = engine.load_shows(CONFIG.shows_csv)
        total_target = sum(s.target_total for s in shows)

        self.stat_shows.lbl_val.configure(text=f"{len(shows)}")
        self.stat_reserved.lbl_val.configure(text=f"0 / {total_target}")

        if not shows:
            empty_lbl = ctk.CTkLabel(
                self.scroll_shows,
                text="Файл shows.csv пуст или не содержит событий. Нажмите 'shows.csv' для добавления ссылок.",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLOR_TEXT_MUTED,
            )
            empty_lbl.pack(pady=40)
            return

        for s in shows:
            card = ShowCardWidget(self.scroll_shows, show=s)
            card.pack(fill="x", pady=5)
            self.show_cards[s.show_id] = card

    def _log(self, text: str) -> None:
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")

    def _check_adspower_status_async(self) -> None:
        def worker():
            async def run():
                alive = await self.client.check_status()
                if alive:
                    profiles = await self.profile_manager.load_and_organize_profiles(
                        group_name=CONFIG.adspower_group_name,
                        active_count=CONFIG.active_profiles_count,
                    )
                    active = len(self.profile_manager.get_active_profiles())
                    reserve = len(self.profile_manager.get_reserve_profiles())
                    self.event_queue.put(
                        (
                            "adspower_status",
                            True,
                            f"AdsPower: Активно ({active} активн. / {reserve} резерв)",
                            active,
                        )
                    )
                else:
                    self.event_queue.put(
                        (
                            "adspower_status",
                            False,
                            "AdsPower: Офлайн (порт 50325)",
                            0,
                        )
                    )

            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()

    def _on_backup_clicked(self) -> None:
        def worker():
            async def run():
                try:
                    profiles = await self.client.get_profiles_by_group(group_name=CONFIG.adspower_group_name)
                    if profiles:
                        backup_file = self.backup_service.backup_profiles(CONFIG.adspower_group_name, profiles)
                        self.event_queue.put(("backup_done", True, str(backup_file)))
                    else:
                        self.event_queue.put(("backup_done", False, "Профили не найдены"))
                except Exception as exc:
                    self.event_queue.put(("backup_done", False, str(exc)))

            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()

    def _on_start_clicked(self) -> None:
        if self.is_running:
            return

        self.is_running = True
        self.btn_start.configure(state="disabled", text="⏳  Проверка выполняется...")
        self.stat_status.lbl_val.configure(text="Выполняется...", text_color="#f59e0b")
        self._log("=========================================")
        self._log("🚀 Запуск процесса проверки Etix (AdsPower CDP)...")

        # Reload cards to reset pending state
        self._load_shows_preview()

        def worker():
            async def run():
                engine = EtixCheckEngine(
                    config=CONFIG,
                    client=self.client,
                    profile_manager=self.profile_manager,
                )

                def on_done(res: CheckResult, current: int, total: int):
                    self.event_queue.put(("show_done", res, current, total))

                try:
                    results = await engine.run(
                        shows_csv=CONFIG.shows_csv,
                        resume=True,
                        on_show_done=on_done,
                    )
                    self.event_queue.put(("check_completed", results))
                except Exception as exc:
                    self.event_queue.put(("check_failed", str(exc)))

            asyncio.run(run())

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self.event_queue.get_nowait()
                kind = msg[0]

                if kind == "adspower_status":
                    ok, text, active_count = msg[1], msg[2], msg[3]
                    dot_color = "#10b981" if ok else "#ef4444"
                    txt_color = "#f8fafc" if ok else "#f87171"
                    self.lbl_adspower_dot.configure(text_color=dot_color)
                    self.lbl_adspower.configure(text=text, text_color=txt_color)
                    self.stat_workers.lbl_val.configure(text=f"{active_count} профилей" if ok else "Офлайн")

                elif kind == "backup_done":
                    ok, path_or_err = msg[1], msg[2]
                    if ok:
                        messagebox.showinfo("Бэкап профилей", f"Резервная копия успешно создана:\n{path_or_err}")
                        self._log(f"💾 Создан бэкап метаданных: {path_or_err}")
                    else:
                        messagebox.showerror("Ошибка бэкапа", f"Не удалось создать бэкап: {path_or_err}")

                elif kind == "show_done":
                    res: CheckResult = msg[1]
                    current, total = msg[2], msg[3]
                    self.stat_status.lbl_val.configure(text=f"Прогресс: {current}/{total}")
                    if res.show_id in self.show_cards:
                        self.show_cards[res.show_id].update_result(res)
                    self._log(f"[{res.status.value}] {res.name} — Резерв: {res.reserved}/{res.target} ({res.details})")

                elif kind == "check_completed":
                    results: List[CheckResult] = msg[1]
                    self.is_running = False
                    self.btn_start.configure(state="normal", text="▶  Запустить проверку")
                    self.stat_status.lbl_val.configure(text="✅ Завершено", text_color="#10b981")
                    total_res = sum(r.reserved for r in results)
                    total_tgt = sum(r.target for r in results)
                    self.stat_reserved.lbl_val.configure(text=f"{total_res} / {total_tgt}")
                    self._log("🎉 Проверка успешно завершена. Отчет сохранен в report.csv.")
                    messagebox.showinfo("Готово", "Проверка завершена! Результаты сохранены в report.csv.")

                elif kind == "check_failed":
                    self.is_running = False
                    self.btn_start.configure(state="normal", text="▶  Запустить проверку")
                    self.stat_status.lbl_val.configure(text="❌ Ошибка", text_color="#ef4444")
                    err = msg[1]
                    self._log(f"❌ Ошибка: {err}")
                    messagebox.showerror("Ошибка проверки", err)

        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _open_file(self, path: Path) -> None:
        if not path.exists():
            messagebox.showwarning("Файл не найден", f"Файл {path} еще не создан.")
            return
        os.startfile(path)
        self._load_shows_preview()

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)


if __name__ == "__main__":
    app = EtixGuiApp()
    app.mainloop()
