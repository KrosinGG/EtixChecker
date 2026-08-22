"""Modern Graphical User Interface for Etix Checker using CustomTkinter."""

from __future__ import annotations

import asyncio
import os
import queue
import subprocess
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
from src.domain.models import CheckResult
from src.etix.checker import EtixCheckEngine
from src.storage.checkpoint import RunContext
from src.utils.logger import LOGGER

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class EtixGuiApp(ctk.CTk):
    """Main CustomTkinter application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Etix Checker 2026 — AdsPower Edition")
        self.geometry("980x720")
        self.minsize(860, 600)

        self.event_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self.client = AdsPowerClient(base_url=CONFIG.adspower_api_url)
        self.profile_manager = AdsPowerProfileManager(client=self.client)
        self.backup_service = ProfileBackupService()

        self._build_ui()
        self._check_adspower_status_async()
        self._poll_queue()

    def _build_ui(self) -> None:
        # Top Banner / Status Card
        self.status_card = ctk.CTkFrame(self, corner_radius=10)
        self.status_card.pack(fill="x", padx=16, pady=(16, 8))

        self.lbl_title = ctk.CTkLabel(
            self.status_card,
            text="🎟 Etix Checker — AdsPower CDP Edition",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.lbl_title.pack(anchor="w", padx=16, pady=(12, 4))

        self.lbl_adspower = ctk.CTkLabel(
            self.status_card,
            text="AdsPower: Проверка подключения...",
            font=ctk.CTkFont(size=13),
            text_color="#f39c12",
        )
        self.lbl_adspower.pack(anchor="w", padx=16, pady=(0, 12))

        # Controls & Action Buttons
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=16, pady=8)

        self.btn_start = ctk.CTkButton(
            self.btn_frame,
            text="▶ Запустить проверку",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2ecc71",
            hover_color="#27ae60",
            height=38,
            command=self._on_start_clicked,
        )
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_backup = ctk.CTkButton(
            self.btn_frame,
            text="💾 Сделать бэкап профилей",
            height=38,
            command=self._on_backup_clicked,
        )
        self.btn_backup.pack(side="left", padx=8)

        self.btn_shows = ctk.CTkButton(
            self.btn_frame,
            text="📄 shows.csv",
            height=38,
            command=lambda: self._open_file(CONFIG.shows_csv),
        )
        self.btn_shows.pack(side="left", padx=8)

        self.btn_report = ctk.CTkButton(
            self.btn_frame,
            text="📊 report.csv",
            height=38,
            command=lambda: self._open_file(Path("report.csv")),
        )
        self.btn_report.pack(side="left", padx=8)

        self.btn_logs = ctk.CTkButton(
            self.btn_frame,
            text="📁 Логи",
            height=38,
            command=lambda: self._open_folder(CONFIG.logs_dir),
        )
        self.btn_logs.pack(side="left", padx=8)

        # Progress / Results Frame
        self.results_frame = ctk.CTkFrame(self, corner_radius=10)
        self.results_frame.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        self.lbl_progress = ctk.CTkLabel(
            self.results_frame,
            text="Готов к запуску",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.lbl_progress.pack(anchor="w", padx=16, pady=(12, 6))

        self.txt_log = ctk.CTkTextbox(
            self.results_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="none",
        )
        self.txt_log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

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
                            f"AdsPower: Подключено | Группа: '{CONFIG.adspower_group_name}' | Активных: {active} | Резерв: {reserve}",
                        )
                    )
                else:
                    self.event_queue.put(
                        (
                            "adspower_status",
                            False,
                            f"AdsPower: Офлайн (Не удалось подключиться к {self.client.base_url}). Убедитесь что AdsPower запущен!",
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
        self.btn_start.configure(state="disabled", text="⏳ Проверка выполняется...")
        self._log("=========================================")
        self._log("🚀 Запуск процесса проверки Etix...")

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
                    ok, text = msg[1], msg[2]
                    color = "#2ecc71" if ok else "#e74c3c"
                    self.lbl_adspower.configure(text=text, text_color=color)

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
                    self.lbl_progress.configure(text=f"Прогресс: {current}/{total} проверено")
                    self._log(f"[{res.status.value}] {res.name} — Резерв: {res.reserved}/{res.target} ({res.details})")

                elif kind == "check_completed":
                    self.is_running = False
                    self.btn_start.configure(state="normal", text="▶ Запустить проверку")
                    self.lbl_progress.configure(text="✅ Проверка завершена! Результаты в report.csv")
                    self._log("🎉 Проверка успешно завершена. Отчет сохранен в report.csv.")
                    messagebox.showinfo("Готово", "Проверка завершена! Результаты сохранены в report.csv.")

                elif kind == "check_failed":
                    self.is_running = False
                    self.btn_start.configure(state="normal", text="▶ Запустить проверку")
                    self.lbl_progress.configure(text="❌ Ошибка при проверке")
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

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)


if __name__ == "__main__":
    app = EtixGuiApp()
    app.mainloop()
