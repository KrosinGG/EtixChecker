# ETIX CHECKER — Полный контекст проекта для CODEX

## 1) Назначение проекта
`Etix Checker` — автоматизированный проверщик доступности билетов на страницах Etix-подобных сайтов через множественные прокси и вкладки Playwright.

Основная цель:
- быстро оценить фактическую доступность билетов по каждому шоу;
- обойти нестабильность отдельных прокси/вкладок за счет ротации;
- фиксировать результат в `report.csv`;
- поддерживать продолжение после падения через checkpoint.

---

## 2) Точки входа и запуск

### CLI
- Файл: `cli.py`
- Запуск через `run.bat`
- Возможности:
  - меню запуска;
  - выбор авто/полуавто режима;
  - resume/fresh при наличии checkpoint;
  - вывод статусов по шоу.

### GUI (CustomTkinter)
- Файл: `gui_app.py`
- Запуск через `run_gui.bat` или скрыто через `run_gui.vbs`
- Возможности:
  - профили `Обычная` / `Быстрая`;
  - редактируемые runtime-параметры;
  - запуск проверки;
  - кнопки открытия `report.csv`, `shows.csv`, `logs`.

### Скрипты установки/запуска
- `run.bat`, `run_gui.bat`:
  - создают `venv` (если нет);
  - устанавливают зависимости из `requirements.txt`;
  - устанавливают `playwright chromium`;
  - запускают CLI/GUI.
- `run_gui.vbs` запускает GUI скрыто (без видимого `cmd` окна).

---

## 3) Ключевая структура кода

- `app_core.py` — основная бизнес-логика проверки шоу:
  - загрузка/валидация `shows.csv`;
  - загрузка прокси из `data/Chezile.csv`;
  - создание браузерных контекстов;
  - детект SOLD OUT / ENDED / CAPTCHA / inventory errors;
  - набор корзин и ретраи;
  - полуавтоматический режим на первом шоу;
  - checkpoint/resume;
  - формирование `report.csv`.

- `config.py` — dataclass-конфиг + env overrides.
- `app_logging.py` — инициализация логгера.
- `features/proxy_state.py` — persistent storage state по прокси + last_good_proxies.
- `features/warmup.py` — загрузка `warmup_sites.txt`, выбор URL, warm-up обход URL.

---

## 4) Основная логика `check_shows()` (app_core)

Последовательность:
1. Проверка входных файлов и параметров.
2. Подготовка прокси-пула:
   - исключение bad proxy;
   - опционально выбор “те же прокси” из `last_good_proxies.json`;
   - добор пула под `TABS_COUNT`.
3. Создание `context + page` на вкладку.
4. Для каждого шоу:
   - открытие URL во всех вкладках (батчами через `HumanScheduler`);
   - ранний детект SOLD OUT / ENDED;
   - попытки выбрать количество + добавить в корзину;
   - пост-ретраи для CAPTCHA (авторежим);
   - расчет статуса (`OK`, `SOLD OUT`, `ENDED`, недостаточно, частично);
   - очистка корзин.
5. Сохранение отчета:
   - `report.csv` пишется без колонки `url` (удаляется перед записью).
6. Сохранение состояния прокси:
   - storage state на диск;
   - last good proxies.

---

## 5) Полуавтоматический режим (реализовано)

Ключевое:
- работает только на первом шоу;
- используется максимум `MANUAL_TAB_LIMIT = 15` вкладок;
- на полуавто принудительно `HEADLESS=False` на прогон;
- при встрече CAPTCHA:
  - фокус нужной вкладки (`bring_to_front`);
  - ожидание ручного прохождения до 60 секунд (`wait_for_manual_cart`);
  - без консольного `input()` и без блокирующего Enter-подтверждения;
  - поддержка новой “Begin CAPTCHA” кнопки (`is_begin_captcha` + `click_begin_captcha`).
- после первого шоу переход в обычный авто-режим.

---

## 6) Важные файлы данных

- `data/shows.csv`
  - колонки: `name,url,target_total,max_per_order,ticket_index`
- `data/Chezile.csv`
  - прокси.
- `data/bad_proxies.txt`, `data/good_proxies.txt`
  - накопительные списки качества прокси.
- `data/last_good_proxies.json`
  - порядок “последних хороших” прокси для повторного запуска.
- `data/proxy_state/*.json`
  - persistent storage_state по каждому proxy-id hash.
- `runs/<run_id>/checkpoint.json`
  - состояние resume/fresh.

---

## 7) Что уже внедрено из серии доработок

Ниже факт по текущему коду:

1. Полуавто режим на первом шоу и лимит 15 вкладок — **внедрено**.
2. Ожидание ручной CAPTCHA без ввода в консоль (60 сек) — **внедрено**.
3. Обработка “Begin” CAPTCHA страницы — **внедрено**.
4. Исправление ошибки `manual_active ... not associated with a value` — **внедрено**.
5. Приоритет статусов ENDED/SOLD OUT над “Билетов недостаточно” — **внедрено** (через notes/manual markers и порядок отображения).
6. `report.csv` без `url` — **внедрено**.
7. GUI: профили “Обычная/Быстрая” — **внедрено**.
8. GUI: свернуть/развернуть настройки — **внедрено**.
9. GUI: кнопка `Shows` (открытие `data/shows.csv`) — **внедрено**.
10. GUI: “Использовать те же прокси” появляется после первой проверки — **внедрено**.
11. GUI: подсказки и автоисправление значений ниже минимума — **внедрено**.
12. `run_gui.vbs` (без cmd окна) — **внедрено**.
13. Модуль `features/proxy_state.py` + подключение в `app_core` — **внедрено**.
14. Базовый warm-up модуль `features/warmup.py` + вызов из `check_shows` — **частично внедрено**.

---

## 8) Что НЕ доведено до конца (после краша/прерванной сессии)

### Критично (ломает/блокирует)
1. В GUI есть кнопка `Open warmup_sites.txt`, но метода `open_warmup_sites()` нет.
   - Файл: `gui_app.py`
   - Эффект: потенциальный runtime crash на создании UI/обработке.

### Важно (функция добавлена частично)
2. GUI не прокидывает в `app_core.check_shows()`:
   - `warmup_enabled`
   - `warmup_urls_count`
   (поля есть в форме, но не передаются в вызов).

3. GUI не подключил callback `on_event`:
   - нет постановки warm-up событий в queue;
   - `_poll_queue` не обрабатывает тип `"event"`;
   - нет отображения строк вида `Proxy N - score`.

4. В `features/warmup.py` нет реализации проверки score через `https://antcpt.com/score_detector/`.
   - нет чтения `Your score is: X`;
   - нет логики порога `>= 0.7`.

5. В `app_core.check_shows()` нет блокировки старта основной проверки при низком score после warm-up.
   - сейчас warm-up выполняется как best-effort, но не gatekeeper.

### Запланировано, но отсутствует
6. Нет отдельной функции “Прогрев аккаунтов”:
   - скрытый режим;
   - батчи по 3 браузера;
   - в каждом контексте 3–7 сайтов с прокруткой 3–7 секунд;
   - сохранение persistent state после прогрева.

### Низкий приоритет/техдолг
7. `app_core.py` остается монолитным (очень большой файл); новая логика частично вынесена, но не полностью.
8. Нет формального smoke-набора автотестов для свежих фич.

---

## 9) Детальный план “что доделать” (конкретные шаги)

### Шаг 1 — стабилизация GUI warm-up
- `gui_app.py`:
  - добавить `open_warmup_sites()` (открывать `data/warmup_sites.txt`);
  - в `_start_run()` читать warm-up параметры из формы;
  - в `_run_async()` передавать warm-up args и `on_event`;
  - добавить обработку `("event", payload)` в `_poll_queue`;
  - добавить рендер warm-up статуса в results frame.

### Шаг 2 — score detector и gate
- `features/warmup.py`:
  - функция чтения score с `antcpt score detector`;
  - возврат structured результата warm-up per proxy.
- `app_core.py`:
  - после warm-up собрать scores;
  - при score < 0.7:
    - либо retry с заменой прокси;
    - либо аварийно завершать запуск (если политика strict).
  - отправлять события в GUI: `Proxy N - score X`.

### Шаг 3 — режим “Прогрев аккаунтов”
- Новый модуль в `features/`:
  - отдельный async workflow warm-up-only;
  - батчи по 3 контекста;
  - 3–7 URL + scroll 3–7 сек;
  - сохранение proxy persistent state.
- `gui_app.py`:
  - добавить кнопку `Прогрев аккаунтов`;
  - отдельный worker thread + статус в UI.

### Шаг 4 — валидация
- Прогнать:
  - обычный запуск GUI;
  - полуавто first-show;
  - “те же прокси” повторный запуск;
  - warm-up enabled;
  - генерация `report.csv` без `url`.

---

## 10) Риски и замечания

1. `git` в текущем окружении не доступен в PATH (невозможно показать git-статус через shell).
2. `py_compile` частично блокируется правами на `__pycache__` в текущем окружении.
3. В репозитории хранится много runtime-артефактов (`venv`, `ms-playwright`, `__pycache__`) — для передачи в CODEX лучше отправлять только исходники и `data/*` шаблоны.

---

## 11) Рекомендуемый минимальный набор файлов для передачи в CODEX

- `app_core.py`
- `gui_app.py`
- `cli.py`
- `config.py`
- `app_logging.py`
- `features/proxy_state.py`
- `features/warmup.py`
- `run.bat`, `run_gui.bat`, `run_gui.vbs`
- `requirements.txt`
- `data/shows.csv` (пример)
- `data/gui_profiles.json`
- `Etix Checker — README for Codex.txt`
- `CODEX_PROJECT_FULL_CONTEXT.md` (этот файл)

