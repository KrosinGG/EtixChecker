
import asyncio
import sys
from pathlib import Path
import os
import subprocess
import importlib
import datetime
import traceback

import pandas as pd
from colorama import init as colorama_init, Fore, Style


def ensure_yaml_available() -> bool:
    try:
        importlib.import_module("yaml")
        return True
    except Exception:
        pass

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "PyYAML>=6.0"],
            check=True,
        )
        importlib.import_module("yaml")
        return True
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(
            "WARNING: Failed to auto-install PyYAML. "
            "Some features may not work correctly.\n"
        )
        sys.stderr.write(tb + "\n")
        return False


ensure_yaml_available()
import app_core

_ANCHOR_SAVED = False

colorama_init()

def _save_anchor_once():
    global _ANCHOR_SAVED
    if not _ANCHOR_SAVED:
        sys.stdout.write("\033[s")  
        sys.stdout.flush()
        _ANCHOR_SAVED = True

def _wipe_from_anchor():
    sys.stdout.write("\033[u\033[J")  
    sys.stdout.flush()

def bring_console_to_front():
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass

def ensure_rich_available() -> bool:
    try:
        importlib.import_module("rich")
        return True
    except Exception:
        pass
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "rich>=13"],
                        check=True)
        importlib.import_module("rich")
        return True
    except Exception:
        return False

def calc_total_from_csv() -> int:
    if not SHOWS_CSV.exists():
        return 0
    try:
        df = pd.read_csv(SHOWS_CSV).fillna("")
        if df.empty:
            return 0
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if "url" not in df.columns:
            return 0
        urls = df["url"].astype(str).str.strip()
        return int(urls.str.match(r"^https?://", na=False).sum())
    except Exception:
        return 0

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def print_banner():
    bar = "=" * 49
    print(Fore.CYAN + Style.BRIGHT + bar + Style.RESET_ALL)
    print(Fore.CYAN + Style.BRIGHT + "            E T I X   C H E C K E R   " + Style.RESET_ALL)
    print(Fore.CYAN + Style.BRIGHT + bar + Style.RESET_ALL)
    print()

DATA_DIR = Path("data")
SHOWS_CSV = DATA_DIR / "shows.csv"

WELCOME = (
    "Привет! Это мини-приложение для проверки наличия\n"
    "стоячих билетов (GA/ADVANCED и пр.) на Etix."
)

def shows_csv_has_rows() -> bool:
    if not SHOWS_CSV.exists():
        return False
    try:
        df = pd.read_csv(SHOWS_CSV).fillna("")
        return not df.empty
    except Exception:
        return False

def ask_menu():
    print()
    print("1. Запустить проверку")
    print("2. Выход")
    return input("> ").strip()

def ask_run_mode() -> bool:
    print("\nВыберите режим:")
    print("1. Автоматический")
    print("2. Полуавтоматический (ручная капча для первого шоу)")
    choice = input("> ").strip()
    return choice == "2"

def print_result_line(row: dict):
    name = (row.get("name") or row.get("url", "")).strip()
    status = (row.get("status") or "").lower()
    notes = str(row.get("notes") or "")

    try:
        est = int(row.get("success_carts", 0)) * int(row.get("per_order_limit", 0))
    except Exception:
        est = 0

    if "MANUAL_ENDED" in notes:
        bring_console_to_front()
        print(f"- {name}  {Fore.MAGENTA}Ended{Style.RESET_ALL}")
        return
    if "MANUAL_SOLD_OUT" in notes:
        bring_console_to_front()
        print(f"- {name}  {Fore.RED}SOLD OUT{Style.RESET_ALL}")
        return
    if app_core.MANUAL_INSUFFICIENT_MARKER in notes:
        bring_console_to_front()
        suffix = f" ({est})" if est else ""
        print(f"- {name}  {Fore.RED}Билетов недостаточно{suffix}{Style.RESET_ALL}")
        return

    if "sold out" in status:
        print(f"- {name}  {Fore.RED}SOLD OUT{Style.RESET_ALL}")
    elif "ended" in status:
        print(f"- {name}  {Fore.MAGENTA}Ended{Style.RESET_ALL}")
    elif "ok" in status:
        print(f"- {name}  {Fore.GREEN}ОК{Style.RESET_ALL}")
    elif ("красная" in status) or ("insufficient" in status) or ("не хватает" in status):
        suffix = f" ({est})" if est else ""
        print(f"- {name}  {Fore.RED}Билетов недостаточно{suffix}{Style.RESET_ALL}")
    elif "error" in status:
        print(f"- {name}  {Fore.RED}Ошибка — проверь вручную{Style.RESET_ALL}")
    else:
        print(f"- {name}  {Fore.YELLOW}Проверь вручную{Style.RESET_ALL}")

def make_rich_result_text(row, Text):
    name = (row.get("name") or row.get("url", "")).strip()
    status_l = (row.get("status") or "").lower()
    notes = str(row.get("notes") or "")
    try:
        est = int(row.get("success_carts", 0)) * int(row.get("per_order_limit", 0))
    except Exception:
        est = 0

    line = Text(f"- {name}  ")
    if "MANUAL_ENDED" in notes:
        line.append("Ended", style="magenta")
        return line
    if "MANUAL_SOLD_OUT" in notes:
        line.append("SOLD OUT", style="red")
        return line
    if app_core.MANUAL_INSUFFICIENT_MARKER in notes:
        suffix = f" ({est})" if est else ""
        line.append(f"Билетов недостаточно{suffix}", style="red")
        return line
    if "sold out" in status_l:
        line.append("SOLD OUT", style="red")
    elif "ended" in status_l:
        line.append("Ended", style="magenta")
    elif "ok" in status_l:
        line.append("ОК", style="green")
    elif ("красная" in status_l) or ("insufficient" in status_l) or ("не хватает" in status_l):
        suffix = f" ({est})" if est else ""
        line.append(f"Билетов недостаточно{suffix}", style="red")
    elif "error" in status_l:
        line.append("Ошибка — проверь вручную", style="red")
    else:
        line.append("Проверь вручную", style="yellow")
    return line

def clear_console():
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

def _format_run_moment(run_id: str) -> str:
    try:
        dt = datetime.datetime.strptime(run_id, "%Y-%m-%dT%H-%M-%S")
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return run_id

async def main():
    clear_screen()
    print_banner()
    print(WELCOME)
    _save_anchor_once()

    if not shows_csv_has_rows():
        msg = "Ваш файл shows.csv пуст. Для начала добавьте нужные шоу для проверки."
        print(Fore.RED + msg + Style.RESET_ALL)
        print("\nНажмите Enter, чтобы выйти...")
        input()
        return

    while True:
        choice = ask_menu()
        if choice == "2":
            return
        elif choice == "1":
            probe = app_core.checkpoint_probe()
            resume_mode = "fresh"
            done0 = 0
            total0 = calc_total_from_csv()

            if probe:
                print("\nОбнаружен незавершённый запуск:")
                print(f"— момент: {_format_run_moment(probe['run_id'])}")
                print(f"— прогресс: {probe['done_count']}/{probe['shows_total']}\n")
                print("1. Продолжить незавершённую проверку")
                print("2. Запустить проверку заново")
                print("3. Назад")
                sub = input("> ").strip()
                if sub == "1":
                    curr_fp, _curr_total = app_core.current_csv_fingerprint()
                    if not curr_fp:
                        print("\nWARNING: Unable to read shows.csv fingerprint; resuming is unsafe.")
                        print("Starting a fresh run instead.")
                        app_core.drop_last_active_run()
                        resume_mode = "fresh"
                        done0, total0 = 0, calc_total_from_csv()
                    elif curr_fp != probe.get("shows_fingerprint", ""):
                        print("\n⚠️ Список shows.csv был изменён. Возобновление небезопасно.")
                        print("1. Запустить проверку заново (рекомендуется)")
                        print("2. Назад")
                        sub2 = input("> ").strip()
                        if sub2 == "1":
                            app_core.drop_last_active_run()
                            resume_mode = "fresh"
                            done0, total0 = 0, calc_total_from_csv()
                        else:
                            continue
                    else:
                        resume_mode = "resume"
                        done0 = int(probe["done_count"])
                        total0 = int(probe["shows_total"])
                elif sub == "2":
                    app_core.drop_last_active_run()
                    resume_mode = "fresh"
                    done0, total0 = 0, calc_total_from_csv()
                else:
                    continue

            manual_captcha_first_show = ask_run_mode()
            if manual_captcha_first_show:
                print(
                    "\nПолуавтоматический режим: "
                    "на первом шоу потребуется ручное решение CAPTCHA. "
                    "Браузер будет открыт (HEADLESS=False) на время прогона."
                )

            _wipe_from_anchor()
            print("\nЗапуск проверки...\n")
            use_rich = False if manual_captcha_first_show else ensure_rich_available()
            if use_rich:
                from rich.live import Live
                from rich.console import Group
                from rich.text import Text

                done = done0
                total = total0
                lines = []  

                def render():
                    header = Text(f"Проверено {done}/{total}", style="bold")
                    return Group(header, *lines)

                try:
                    async def on_show_done(row: dict, done_idx: int, total_from_core: int) -> None:
                        nonlocal done, total
                        done = done_idx
                        if total_from_core:
                            total = total_from_core
                        lines.append(make_rich_result_text(row, Text))
                        live.update(render())

                    with Live(render(), refresh_per_second=8, transient=False) as live:
                        rows = await app_core.check_shows(
                            on_show_done=on_show_done,
                            resume_mode=resume_mode,
                            run_root="runs",
                            manual_captcha_first_show=manual_captcha_first_show,
                        )
                except Exception as e:
                    print(Fore.RED + f"Ошибка: {e}" + Style.RESET_ALL)
                    print("\nНажмите Enter, чтобы вернуться в меню...")
                    input()
                    continue

                print("\nГотово. Отчёт сохранён в report.csv")
                print("2. Выход")
                input("> ")
                return
            else:
                try:
                    async def on_show_done(row: dict, done_idx: int, total_from_core: int) -> None:
                        print_result_line(row)

                    rows = await app_core.check_shows(
                        resume_mode=resume_mode,
                        run_root="runs",
                        manual_captcha_first_show=manual_captcha_first_show,
                        on_show_done=on_show_done,
                    )
                except Exception as e:
                    print(Fore.RED + f"Ошибка: {e}" + Style.RESET_ALL)
                    print("\nНажмите Enter, чтобы вернуться в меню...")
                    input()
                    continue

                print("\nГотово. Отчёт сохранён в report.csv")
                print("2. Выход")
                input("> ")
                return

        else:
            print("Введите 1 или 2.")

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())  
    asyncio.run(main())
