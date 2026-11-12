# Проверка адаптированной версии

Среда проверки: Linux x86_64, Python 3.12.14, FFmpeg 7.1.5 с libx264/libass.
Целевая среда пользователя: Mac M4. Результаты Linux не заменяют проверку macOS.

## Выполнено

Последний полный запуск: **104 теста и 13 подтестов прошли**, пропусков и ошибок нет.
Команда: `.venv/bin/python -m pytest -q --junitxml=test-results/linux.xml`.

- Автоматические тесты Python, включая реальные кодирование и декодирование видео:
  кадры и Ken Burns, переходы, видеофрагменты, звук, H.264/AAC, один сценарий 1920×1080,
  SRT и вшитые субтитры с проверкой пикселей, копии скорости, отмена процессов.
- Проверки настроек, удаления/маскировки ключей, очереди, валидации входа,
  завершения и отмены фоновых задач.
- Проверки ошибок/повторов HTTP, временных обрывов, 429/5xx и атомарной загрузки файлов.
- YouTube: разные форматы ссылок, отказ первой дорожки и переход к следующей,
  короткие субтитры, запасная загрузка, явный выбор браузера,
  незавершённые аудиофайлы, сохранение текста перед рерайтом и повторное использование.
- fast-gen: восстановление после имитации `ConnectionResetError 10054`,
  отсутствие повторов с другой моделью при ошибке авторизации 401.
- JavaScript: синтаксис, соответствие 33 методов интерфейса Python,
  5 исполняемых проверок восстановления опроса/кнопки «Стоп».
- `pip check`, зависимости/ресурсы и фильтры FFmpeg, запуск `app.py --help`
  без графического движка, синтаксис shell-скриптов и PyInstaller specs.

Сетевые ответы в автоматических тестах подставлены. Ключи, баланс и доступность
внешних сервисов этими тестами не подтверждаются.

## Исправленные проблемы

- Windows-only загрузка GUI и системные операции заменены платформенными ветками;
  данные на Mac вынесены за пределы `.app`, поиск FFmpeg учитывает Homebrew/Finder.
- Удаление всех ключей больше не восстанавливает старый одиночный ключ;
  Royal TTS проверяет правильное поле, очередь проверяет наличие ключа рерайта.
- Прерывание подготовки/рерайта правильно отображается как отмена;
  FFmpeg завершается при отмене, ошибки галереи не останавливают опрос прогресса.
- Повторы NonStop/YouGen видят исходные HTTP-ошибки;
  частичные загрузки не публикуются как готовые файлы.
- Исправлены специальные символы в путях субтитров, проверка длительности
  кэшированных клипов и разделение кэша разных аудиофайлов с одинаковым именем.
- Для YouTube добавлены перебор дорожек, запасной способ получения субтитров,
  локальная настройка входа, ограниченные сетевые таймауты и компоненты yt-dlp EJS.

## Осталось проверить

- Нативное окно, диалоги, Finder/clipboard/Keychain и сборку `.app` на macOS arm64.
- Живое получение текста `OhnfppMraXY` и рерайт через fast-gen:
  облачный прокси отклонил оба соединения; ключ пользователя не предоставлялся.
- Реальные вызовы всех провайдеров изображений, видео, STT, TTS, проверки ключей,
  референсов и автоматического отбора изображений.
- Подпись и notarization приложения; самостоятельный установщик с FFmpeg.

Это проверенная на Linux рабочая адаптация исходников, а не подтверждение
полной работоспособности всех функций на Mac или всех внешних сервисов.

## Обновление 1.8.0

Добавлены Windows-сборка с комплектными FFmpeg/ffprobe/Deno и EJS, отключение
устаревшего обновления, диагностика защищённых/занятых browser cookies, yt-dlp 2026.10.26.
Полный режим проверяется тестами передачи всех частей исходного текста, сохранения
порядка/возобновления и передачи визуального профиля в сценические промпты.
Реальный FFmpeg извлекает кадры из тестового видео, ответы модели анализа подставляются.
Поддержка изображений fast-gen проверяется по chat.multimodal, при её отсутствии
полный режим явно завершается ошибкой. Точность смыслового переноса моделью этими
тестами не гарантируется; итоговый текст редактируется пользователем.
Смена голоса или исходного визуального профиля создаёт отдельный кэш таймингов.
Сборки на GitHub запускаются после публикации; результаты ниже нужно сверять с Actions.

## Восстановление полного рерайта — 30 октября 2026

Полный запуск на Linux: 141 тест и 13 подтестов прошли, 4 проверки Windows PowerShell
пропущены на Linux. Дополнительно проверена отмена последней попытки, обрезанной
по лимиту токенов. Проверки интерфейса также прошли.

Добавлены ограниченные повторные запросы и деление только неудачной части,
сохранение готовых подчастей и возобновление после обрыва. Проверяются порядок
всех фрагментов и последнего слова, отказ от кратких пересказов, отмена,
отсутствие повторов при отказе в авторизации или ограничении сервиса.
Незавершённые ответы Gemini/OpenAI/Claude/fast-gen отклоняются независимо от длины;
блоки Gemini с `thought: true` исключаются из сценария. Ответы API в этих проверках
подставлены: смысловую полноту реального рерайта нужно проверить на экране редактирования.

Предыдущий коммит `c7ff2bd` успешно прошёл сборку и тесты на Windows x64 и
macOS arm64 в GitHub Actions. Эти сборки не включают данное обновление рерайта;
его результаты следует проверять по последнему запуску Actions.

## Объединённая версия 1.8.1-dev — 2026-11-09

После объединения исправлений ElevenLabs, fast-gen, отмены, кэша и экспериментального переноса ритма склеек с текущим main: **173 теста и 13 подтестов прошли**, 4 теста Windows PowerShell пропущены на Linux. Команда: `python -m pytest -q tests --junitxml=test-results/linux.xml`. Синтаксис JavaScript, 34 метода Python-моста и 5 проверок восстановления UI прошли через `node tests/check_ui.mjs`.

Добавленные проверки включают прямую озвучку ElevenLabs с реальным MP3, возобновление фрагментов, детектор склеек и сборку MP4 H.264/AAC. Сетевые ответы подставлены: доступ конкретного ключа, баланс и причина пользовательского HTTP 400 требуют живого ответа сервиса. Все существующие ресурсы и исправления полного рерайта сохранены. Статус нативной Windows-сборки следует проверять по Actions соответствующего коммита.

## 1.9.0-dev — раскадровка и предпросмотр

На Linux: **200 тестов и 13 подтестов прошли**, 4 Windows PowerShell проверки пропущены по платформе. Новые проверки: семантическое предложение с перестановкой исходных планов, явное отсутствие референса, возобновление пачек после отмены, проверка редактируемых полей/длительностей и целостности сохранённых раскадровок, привязка к распознанным словам, отказ от несвязанного распознавания, реальное изменение темпа аудио, вырезание интервала и субтитров, генерация только выбранных планов, сборка разреженных индексов, переиспользование предпросмотра в полной сборке и независимая перегенерация одного плана. Сетевые границы генерации и модель сопоставления подставлены; FFmpeg, аудио и итоговые MP4 настоящие.

`node tests/check_ui.mjs`: синтаксис, 39 методов моста и 5 проверок восстановления прошли.

Дополнительная проверка интерфейса — `python scripts/check_storyboard_ui.py` после установки Playwright (`pip install playwright`, `python -m playwright install chromium`). Она использует настоящий Python API приложения для построения/сохранения/загрузки раскадровки, а модель и запуск платной генерации подставлены. В Chromium проверены: выбор референса, редактирование текста и времени, сохранение нового варианта, отказ от отрицательного начала, запуск предпросмотра и полного ролика, возврат к редактору и инвалидирование после правки общего сценария. Скриншот сохраняется в `test-results/storyboard-editor.png`. Данные проверки изолированы во временной папке.

Оценка точности семантической модели и качество живой генерации этими тестами не подтверждаются. Нативную сборку Windows следует сверять с Actions нового коммита.

## Video Flow / 60 FPS — 2026-11-10

Linux: 213 Python-тестов и 13 подтестов прошли, четыре Windows PowerShell проверки пропущены. Проверено настоящее перекодирование картинки сначала в 30, затем в 60 FPS с FFprobe и подсчётом кадров.

Video Flow: 17 node:test проверок, oxlint, TypeScript и production build проходят. Browser smoke: создание проекта, переход до debounce, сохранение, reload и четыре карточки ключей. E2E: настоящие MP4 из трёх сцен в 30/60 FPS, проверка кодеков, archive/restore, restart без повторного create и dry-run 202 кадра. Платные API подставлены. Customer ZIP содержит проверенный SHA-256 Node 24.22.2, исходники и lockfile; без ключей, пользовательских медиа и node_modules. Windows/macOS сборки и установка нового модуля проверяются Actions этого коммита.

## Единое настольное приложение 1.9.1

В основной Python/pywebview интерфейс перенесены проекты SQLite, импорт DOCX и SYNTX. 220 тестов +13 подтестов проходят на Linux, 4 Windows-only проверки пропущены. Браузерная проверка расширена: импорт DOCX → раскадровка → сохранение в SQLite → повторное открытие. В сборку Windows добавлен self-test готового EXE, который проверяет наличие и работу этих модулей без внешних API.

## 1.9.2 — FastGen image access

Исправлена проверка: отсутствие chat.multimodal в ответе usage не равно запрету. Только явное JSON false блокирует отправку кадров, true разрешает; отсутствие проверяется настоящим запросом анализа. Добавлена кнопка бесплатной GET-диагностики доступа без создания чата/генерации. Regression-тесты покрывают true/false/отсутствующее поле и отсутствие ключей в ответе диагностики.

## 1.9.3 — возобновление анализа и HTTP 503

Проверены ограниченные повторы HTTP 503, Retry-After, запрет раннего повтора при длинном Retry-After, отсутствие автоматического повторного POST при потерянном ответе, кэширование только валидных ответов, инвалидирование при смене картинки и восстановление полного анализа 17 кадров после сбоя второй пачки без повтора первой. Общий набор 233 теста +13 подтестов прошёл; дополнительный интеграционный тест возобновления также прошёл. Сообщение о 17 кадрах явно указывает обзор и следующий разбор склеек в montage-режиме.

## 1.9.5 — квоты Gemini и пакетный анализ планов

До шести планов в одном запросе: по три кадра каждого плана, ID проверяются строго, перестановка ответов допустима. Индивидуальные результаты прошлых версий сохраняются. Пакет записывается атомарно перед отдельными файлами для восстановления после прерывания. Добавлен общий разбор Google QuotaFailure/RetryInfo в анализе кадров и GeminiClient для текстов/QA. Суточная квота и квота 0 не повторяются автоматически, минутные ограничения учитывают Retry-After и retryDelay; ключи удаляются из сообщений. Проверки включают 13 планов/39 кадров за 3 запроса и отсутствие повторного API-вызова после восстановления.

## 1.9.5: Elysium gateway and connection diagnostics

Elysium is a separate provider using its documented OpenAI-compatible
`https://elysiumai.garden/v1/chat/completions` endpoint and Bearer authentication.
Text scenes, rewriting, storyboard creation, vision QA and source analysis route
through it without replacing direct OpenAI/Google/Anthropic credentials.
Model discovery uses GET /models and supports Google/Anthropic pagination;
listing models is independent of a small billable generation check.
Gateway billing 429 failures stop immediately. Provider errors identify server,
model and status, redact secrets, and explain key rejection versus billing.
The UI shares a provider key across duplicate fields, preserves server model IDs,
and shows custom API addresses even for vision-only provider use.
Mocked contract tests and browser E2E cover these paths. A live paid Elysium
request has not been performed; account/model/vision entitlement is not verified.
Reference: https://elysiumai.garden/docs and /docs/api/chat-completions.

## 1.9.6: source JSON recovery

Source GPT requests use JSON-object mode (including GPT through Elysium).
Shared parsing accepts fenced objects, BOM, trailing commas and literal line
breaks inside strings without filling missing data. Multiple objects, non-finite
numbers, arrays and incomplete objects are rejected. Text response blocks are
normalized; explicit refusals, truncation and empty responses are diagnosed
separately and are not sent for syntax repair.
The original completed model reply is checkpointed before parsing/validation,
without the request, images or API key. A syntax failure permits one text-only
format repair per preparation attempt, validated with the original schema.
Successful raw/repair replies resume after interruption without re-sending the
source frames. Wrong-schema replies are marked ineligible for resume so a user
retry can regenerate only that failed part. Existing validated checkpoints and
query identity remain compatible with 1.9.5.
Automated tests cover malformed replies, no image re-send, repair outage resume,
crash recovery, strict validation and no fabricated completion. The reported user
reply from 1.9.5 was discarded by that version; its exact defect is unknown.

## 1.9.7: streamed source analysis and transport diagnostics

OpenAI-compatible source analysis (OpenAI and Elysium) requests streamed chat
completions per Elysium's documented SSE API. The reader handles UTF-8, comments,
reasoning-only events, text blocks, DONE, stop/length/refusal and an explicit
finish marker without waiting for the proxy connection to close. It does not
accept balanced-but-unfinished JSON as a completed stream. Partial content is
checkpointed as incomplete and never sent for JSON repair or silently retried.
Idle read timeout is 300 seconds for streaming versus 180 for legacy JSON;
stream processing has a 900-second bound and an 8-MiB event limit. Progress
reports heartbeat/response activity without displaying model reasoning/text.
Connection errors identify host/model, elapsed attempt time and exception type,
redacting credentials. Existing validated reply/cache identities stay compatible.
Tests include real localhost HTTP heartbeats over a shorter idle timeout, stream
interruption with no second POST, cached completion and timeout redaction. No live
paid Elysium call was made. The user's repeated transport exception is not enough
to identify its exact upstream cause; streaming cannot guarantee server uptime.

## 1.9.8: storyboard last-batch waits

Storyboard Fast-gen clients now report quota waits and allow at most two minute
waits before stopping with a resume message. Other video-generation clients keep
their prior configured wait policy. Storyboards via Elysium/OpenAI opt into SSE
using chat_stream, accept explicit completion, report transport progress with the
current batch prefix, and make one POST per batch attempt. Ambiguous network
failures/invalid JSON are not automatically replayed by this storyboard client.
Committed storyboard batch signatures/cache locations remain unchanged.
Create clears stale restore errors before awaiting its preparation call; a missing
saved board is explained separately from a malformed identifier. Progress marks
committed batch counts and final save. The screenshot's exact live cause is not
verified, but the previous Fast-gen 70-minute invisible quota-wait path and
OpenAI-compatible four network-retry path have both been addressed for storyboards.
Tests reproduce 197 passages with failure at 193–197 and prove the first 192 are
not queried again; test quota cap/status, stream completion/no retry, missing-board
messages, stale-error reset, and browser storyboard/project roundtrip.

Fast-gen storyboard requests also disable the generic four-attempt network/JSON
retry loop: connect/read timeout is 15/180 seconds, transport/parse ambiguity is
terminal for that batch attempt, and explicit HTTP failures get at most two
attempts. Request waiting is visible. Other pipeline LLM clients retain defaults.

## 1.9.9: Fast-gen storyboard JSON and cross-provider draft recovery

Fast-gen storyboard requests now use its documented SSE stream field (see stored
ChatCompletionRequest schema), not an undocumented response_format parameter.
model_reply.py captures transport/model receipts before parsing, without the API
key. API HTML/empty/unexpected envelopes, truncated/refused replies and malformed
model JSON are distinct errors. Completed model JSON may receive one text-only
syntax repair per preparation attempt, validated against the complete original
batch and source IDs. Receipts survive a failed repair so original reference
prompts need not be sent again. HTTP HTML and incomplete responses are not sent
for model repair. Compatibility parsing handles SSE bodies with an incorrect JSON
MIME without assuming the first chunk is a complete response.
Validated drafts from other provider/model directories can be reused only with
identical script/profile identity, prompt signature and valid exact integer IDs.
No provider is switched automatically. This enables an explicit user switch to
Elysium to finish the last five passages while keeping the first 192.
Tests include the 197-passage repair and provider-switch cases, wrong-ID rejection,
API/model distinction, partial/refused handling, recovery after repair timeout,
secret redaction, native browser/project roundtrip and prior cache regressions.
No live paid request was made; the user's discarded 1.9.8 reply cannot establish
whether its JSONDecodeError was in the transport envelope or assistant content.

## 1.9.10: explicit media mode and automatic source style

Removed storyboard/source-montage forced video overrides in review, preview,
forecast and rendering. Requested image/mixed/video mode is retained; existing
explicit video_first selection still requests intro clips. Strict missing-video
validation applies only to the selected animated scenes, never silently replacing
failed requested clips. Source cuts keep motion-design transitions disabled.
Source-style validation accepts a saved source profile without manual custom
style text; the UI sends the profile ID independently of the rewrite checkbox.
Saved boards expose their visual style (not profile paths) for read-only review.
Source prompts/references remain in effect and no preset style needs to be chosen.
Tests render real FFmpeg image-only preview/full MP4 with video access disabled,
verify no video requests and actual source prompt/reference input; image assets
are reused when switching to video. Mixed/video still fail when required clips
are unavailable. API and forecast tests reproduce custom-style/profile and image
mode settings; browser E2E verifies readonly source style and images mode in both
preview/full launch with rewrite unchecked. No paid live provider calls.


## Studio 2.0 verification

Offline fixture coverage: source-style identity preserved, creative metadata/plan validation and resumable batches, full chunked script including its ending, publication schema/cache, durable export without keys, typed SQLite Studio settings, explicit video-denial preflight. Four ASS styles are actually burned by FFmpeg and decoded: pixels change, audio stream survives, user ASS markup is neutralized. Native bundled self-test checks Studio assets and ASS writing. Browser E2E exercises Studio settings, measured report, async packaging/script/board workers and project roundtrip. No live paid API generations, account capability or video-quality claims are inferred from these tests.


## 2.0.1 Studio recovery

325 local Python tests passed, 4 Windows-only skipped, 13 subtests passed. Six regressions cover correction of only bad plan IDs, preservation of valid prompt/motion/reference fields, partial batch checkpoints, retry of only missing plans, rejection of unknown refs, exact 2.0 cache signatures, locally normalized nullable overlay/enums, and recovery of 2.0 raw schema-invalid answers without resending source descriptions. Browser tests exercise the exact «Создать улучшенную постановку» action with malformed metadata and verify the old board cannot be silently rendered; explicit saved-board selection restores controls. A separate browser flow starts with an empty script and rewrite off, pastes a YouTube URL and receives a prepared enhanced storyboard through fixture workers. User-uploaded two 30-second previews were compared: identical decoded audio; cropped upper-80% visual SSIM 0.999089, confirming no meaningful scene change in those samples. No paid live generation was run.


## Bounded source trial (2.0.2)

Tests cover section-range downloading and cache isolation by source/time, rejection of oversized source responses, analysis/transcription only on a bounded AV file, prohibition of full-video/full-transcript fallbacks, invalid range and denied video preflight before paid work, and propagation of all selected production options through the short worker. Real FFmpeg integration renders a short storyboard with audio, ASS captions, 25/60 FPS and retimed exact duration. An oversized model script is rejected before narration. Browser E2E exercises invalid-start rejection, a single sample API request, no full-source rewrite worker, output navigation and loading the marked short storyboard. Provider calls use fixtures; live YouTube section availability and paid model quality are not inferred from mocks.

Local combined suite for 2.0.2: 343 passed, 4 Windows-only skipped, 13 subtests passed. JavaScript bridge: 53 methods and 6 recovery checks. New sample test cases: 18 passed. Browser E2E and native-import/asset self-test passed.

## Gemini Video через Elysium, 2.0.3

- Полный Linux-набор: 358 passed, 4 Windows-only skipped, 13 subtests passed.
- 15 новых проверок Elysium: точный ID/режим/первый кадр, отсутствие повторной отправки при таймауте, восстановление ID, отказ до оплаты без ссылки, раздельный провайдер, настоящий MP4 и FFprobe.
- Browser E2E: выбор Gemini Video, сохранение разрешения, сохранение FastGen для картинок; прежние сценарии редактора и 30-секундной пробы также пройдены.
- Живой публичный медиакаталог Elysium прочитан; платная генерация не выполнялась.
- Нативная Windows-сборка 2.0.3 пока не запускалась: автоматическая проверка отклонила публикацию исходников для CI, запрошено явное разрешение пользователя.
