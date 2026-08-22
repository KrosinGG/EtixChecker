1) Что такое Etix Checker и что он делает
Etix Checker — консольный инструмент на Python + Playwright (async) для параллельной проверки шоу (URL’ы) через множество вкладок/контекстов с прокси и попыткой выбрать количество билетов и добавить в корзину. Результат по каждому шоу фиксируется в report.csv (одна строка = одно шоу) и показывается в консоли (с Rich Live, если доступно).

Архитектурно:
cli.py — меню/UX и live-прогресс.
app_core.py — вся “машина”: чтение входных CSV, прокси-пулы, Playwright, логика вкладок, резюма/чекпоинт, формирование строк отчёта, сохранение report.csv.
requirements.txt — зависимости проекта. 
requirements

2) Файлы и папки проекта (как мыслить о репозитории)
Главные файлы
app_core.py — ядро (Playwright pipeline + бизнес-логика + crash-safe resume).
Ключевая точка входа: async def check_shows(...): 
app_core
cli.py — консольное меню “Запустить проверку”, детект незавершённого запуска, выбор “resume/fresh”, вывод прогресса, и красивый вывод результатов (Rich/Colorama).
requirements.txt — зависимости: playwright, pandas, rich, colorama, textual, PyYAML. 
requirements

Папка data/ (входные данные и списки прокси)
По твоему дереву:
data/shows.csv — список шоу для проверки (минимум столбец url; остальные опциональные).
data/Chezile.csv — список прокси.
data/bad_proxies (текстовый файл) — чёрный список прокси.
data/good_proxies (текстовый файл) — “хорошие” прокси, которые можно приоритизировать.
data/human.yml — конфиг/настройки “human-like” поведения (используется ядром при необходимости; файл присутствует в проекте).

Папки “выходов”
runs/<run_id>/ — папка прогона (crash-safe resume): хранит params.json и checkpoint.json (пока прогон не завершён).
logs/ — логи, включая ротацию прокси (proxies_rotation.log).
screens/ — скриншоты (например, при SOLD OUT делается скрин). 

3) cli.py: как устроен запуск и UX
Режимы
cli.py делает 3 важные вещи:
Проверяет, что shows.csv не пуст — иначе сообщает пользователю и выходит. 
Детектит незавершённый прогон через app_core.checkpoint_probe() и предлагает:
продолжить (resume)
начать заново (fresh, предварительно удалив последний активный run)
назад 

Также CLI сравнивает “отпечаток” текущего shows.csv с отпечатком из чекпоинта, и если список шоу менялся — предупреждает, что resume небезопасен, и рекомендует fresh.
Запускает app_core.check_shows():
Если Rich доступен — включает rich.live.Live и обновляет прогресс “Проверено done/total”, добавляя по строке результата на каждое шоу через callback on_show_done(...).
Если Rich недоступен — печатает результаты обычными строками.

4) app_core.py: ядро, резюмирование, очереди и стабильность
4.1. Crash-safe resume (runs + checkpoint)
В app_core.py есть класс RunContext, который управляет “состоянием прогона” в runs/<run_id>/checkpoint.json и пишет туда атомарно.
Состояние прогона включает:
run_id, version, shows_total, shows_fingerprint
очереди: done, inflight, pending
counters.done_count
Логика очередей:
перед началом обработки шоу: mark_inflight(show_id) — переносит из pending в inflight 
после завершения шоу: commit_done(show_id, row) — удаляет из inflight, добавляет в done (с сохранением row) и обновляет done_count
по завершению всего прогона: complete_run() удаляет checkpoint.json (то есть “активного незавершённого” прогона больше нет).

Важно для Codex (инварианты):
checkpoint.json — единственный критерий “есть незавершённый прогон”.
Resume разрешён только если fingerprint shows.csv совпадает (иначе ошибка/предупреждение).
При resume уже готовые строки (done) подхватываются в summary_rows, чтобы итоговый report.csv включал их в начале. 


4.2. Чтение входных CSV
Прокси:
Загружает все прокси из data/Chezile.csv
Убирает те, что в bad_proxies (чёрный список)
Если пул пуст — падает с понятной ошибкой. 

Шоу:
Читает data/shows.csv, приводит названия колонок к lower-case
Требует колонку url
Формирует список shows_list только для валидных URL (http/https)
Для каждого шоу хранит:
url
name (если пусто — используется сам url)
target_total (сколько билетов “хотим увидеть”, влияет на статус OK/частично)
max_per_order (если задан — фиксирует лимит на заказ)
ticket_index (индекс/выбор конкретной опции билета, аккуратно парсится)
show_id = стабильный ID, построенный из name+url

5) Playwright модель: вкладки, прокси, ротация
5.1. Контексты/страницы
check_shows() создаёт браузер:
pw.chromium.launch(headless=HEADLESS, slow_mo=SLOWMO_MS) 
Затем собирает pool прокси размером TABS_COUNT и на каждый прокси создаёт:
BrowserContext с параметрами прокси
Page внутри контекста
выставляет default timeout NAV_TIMEOUT 

5.2. Приоритизация good_proxies
Есть “хорошие прокси” (good_proxies), из которых ядро умеет делать стартовую выборку (часть слотов занять good, часть — остальными), а потом дозаполнить до TABS_COUNT. 
5.3. Ротация прокси и “плохие причины”
Внутри прогона есть механизм замены “плохой вкладки”:
replace_bad_tab(tab_i, bad_reason, mark_as_bad=True, avoid_captcha=True) делает:
освобождает старый прокси из in_use
если mark_as_bad=True — добавляет прокси в bad и сохраняет причину (bad_proxies)
если mark_as_bad=False — не банит, но добавляет ID в captcha_blocked_ids (то есть “избегать при выборе, если avoid_captcha=True”)
закрывает старый context
выбирает новый прокси через next_good_proxy(...)
создаёт новый context/page
пишет запись в logs/proxies_rotation.log

Механика выбора следующего прокси поддерживает:
исключение bad_ids
исключение in_use (чтобы один прокси не использовался сразу в двух вкладках)
опциональное избегание captcha_blocked_ids
режим RANDOMIZE_PROXIES (выбор случайного кандидата)

6) Логика обработки одного шоу (главный цикл)
Главный цикл: for show in todo:
Перед обработкой шоу обязательно: await run_ctx.mark_inflight(show["show_id"]). 
Дальше (концептуально, как Codex должен это понимать):
Для всех вкладок открывается страница шоу.
На каждой вкладке выполняется попытка “выбрать количество билетов и добавить”.
Собираются результаты по вкладкам:
attempts — сколько раз пытались “add”
successes — сколько вкладок реально набили корзину
add_results — список (tab_idx, ok, message)
captcha_tabs — вкладки где детектнули CAPTCHA
notes — короткие заметки (потом режутся до 300 символов)

Есть ветки раннего выхода:
если детект “SOLD OUT” или “ENDED” — формируется строка и делается continue.
При SOLD OUT дополнительно пытается сделать скриншот в screens/. 
Есть “допроход” для капчи: если цель target_total не достигнута и есть captcha_tabs, запускаются ретраи run_captcha_retries(...).

6.1. CAPTCHA retries (важно для Codex)
run_captcha_retries(problematic_tabs, target_total, limit, ticket_index, show_url):
Для каждой проблемной вкладки делает до 5 попыток:
сначала пробует на том же прокси (чистит cookies контекста и заново открывает show)
если капча сохраняется/неуспех — переключается на стратегию “менять прокси” через replace_bad_tab(..., mark_as_bad=False, avoid_captcha=True) (то есть прокси не банится как “плохой”, а помечается “captcha-blocked”)
Если ретрай успешен — увеличивает successes и сохраняет прокси как “good”, если он не в captcha_blocked_ids. 

7) Как формируется итог по шоу (status + поля отчёта)
По итогам шоу формируется _row и добавляется в summary_rows, затем фиксируется в чекпоинте commit_done(), потом вызывается on_show_done(...) для live UI.
7.1. Ключевые вычисления
est = successes * limit — “оценка доступного количества” (минимальная, поэтому в отчёте поле называется estimated_available_>=). 
needed_carts_threshold вычисляется функцией compute_needed_carts(limit, target_total, tabs_used):
если задан target_total — сколько корзин нужно минимум, чтобы “набрать” target_total (ceil(target_total/limit)), но не больше tabs_used
если target_total не задан — минимум 1 корзина (тоже ограничено tabs_used)

7.2. Статусы (как сейчас)
Основная развилка:
Если target_total задан и est >= target_total → OK
Иначе если successes >= needed_carts_threshold → OK
Иначе если “инвентарь исчерпан” → КРАСНАЯ ЛИНИЯ (билетов не хватает)
Иначе → ЧАСТИЧНО (не все корзины набиты; без явных алертов) 

Отдельно ранние статусы:
SOLD OUT
ENDED 

7.3. Структура строки отчёта (НЕ ЛОМАТЬ)
_row содержит поля (в точности такими ключами), и именно из них строится report.csv:
name
url
target_total
site_per_order_limit
per_order_limit
tabs_used
attempts
success_carts
estimated_available_>=
needed_carts
status
ok_all (True если successes == tabs_used)
inv_errors (кол-во inventory-ошибок, если не было раннего inventory abort)
other_fails (прочие ошибки)
notes (обрезается до 300 символов)

8) Финал прогона: очистка корзины, CSV, закрытие
После каждого шоу ядро:
ждёт DELAY_BEFORE_CLEAR_CARTS_S
параллельно вызывает clear_cart(pg) для всех страниц (чтобы следующий show начинать “чисто”)

После всех шоу:
pd.DataFrame(summary_rows).to_csv("report.csv", index=False, encoding="utf-8-sig")
await run_ctx.complete_run() (удаляет checkpoint)
await browser.close()
возвращает summary_rows

9) Что именно нужно сказать CODEX (чтобы он не “переизобрёл” проект)
Ниже — готовые “правила разработки” для Codex. Они максимально важны.

9.1. Жёсткие инварианты (не ломать)
Формат report.csv: те же колонки/ключи _row, тот же смысл вычислений (особенно estimated_available_>=, needed_carts, status). 

Crash-safe resume:
runs/<run_id>/checkpoint.json — источник истины
pending/inflight/done логика должна сохраняться
при success завершении прогона checkpoint удаляется
Resume запрещён при изменённом shows.csv (fingerprint mismatch).
Live прогресс в cli.py должен продолжать работать через callback on_show_done(row, done_idx, total) и Rich Live. 


Ротация прокси:
плохие прокси уходят в bad (если реально “bad”)
капча-прокси не банятся, а помечаются captcha_blocked (и избегаются при avoid_captcha=True)

9.2. Где расширять (без риска)
Самые безопасные точки улучшений:
улучшение эвристик выбора прокси (next_good_proxy, критерии good/bad/captcha) — не меняя внешние файлы и формат логов.
улучшение run_captcha_retries (лимиты, паузы, условия выхода) — не меняя контракт “обновляет successes/attempts/add_results/notes”. 
добавление новых логов/метрик (в logs/) — если не ломает скорость и не мешает resume.
улучшение “детекта” SOLD OUT / ENDED / inventory сообщений — но итоговые статусы должны остаться совместимыми с текущим CLI-раскрасом. 


10) Готовый блок контекста для CODEX (можешь вставить как есть)
Скопируй и вставь в Codex как “Project Context / System Spec”:
Проект: Etix Checker (Python async + Playwright).
Entrypoint: cli.py (меню) → вызывает app_core.check_shows(...).
Ядро: app_core.py:
читает data/shows.csv (обязателен url, опционально name,target_total,max_per_order,ticket_index)
читает data/Chezile.csv прокси, учитывает data/bad_proxies и data/good_proxies
создаёт TABS_COUNT контекстов/страниц Playwright (1 прокси = 1 контекст/вкладка)
для каждого шоу открывает страницу во всех вкладках и пытается выбрать количество и добавить в корзину
считает success_carts, attempts, формирует estimated_available_>= = success_carts*per_order_limit
статусы: OK / SOLD OUT / ENDED / КРАСНАЯ ЛИНИЯ (билетов не хватает) / ЧАСТИЧНО
пишет report.csv через pandas с кодировкой utf-8-sig

Crash-safe resume:
каждый прогон в runs/<run_id>/
params.json и checkpoint.json (очереди pending/inflight/done)
при успешном завершении checkpoint удаляется
resume разрешён только если fingerprint текущего shows.csv совпадает с checkpoint

Важно: НЕ ломать структуру _row (колонки report.csv) и контракт callback on_show_done(row, done_idx, total) для Rich Live UI.