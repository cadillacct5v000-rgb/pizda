# Проверка по AI Video Flow: 40 AI prompts

Версия исправлений: 0.6.2. Основа: фактический текст AI_Video_Flow_40_AI_prompts.docx, 40 этапов. Учитываются последующие решения пользователя: fal.ai вместо SYNTX; Lumean остаётся озвучкой; Claude и три варианта стиля сохранены; ввод по YouTube-ссылке отменён, исходный сценарий готовит пользователь.

Проверено соответствие кода требованиям, а не только ранее составленной таблице ACCEPTANCE.md. Пять первоначальных регрессионных проверок выявили ошибки на 0.6.1; после исправлений добавлены проверки граничных случаев. Платные API в тестах заменены mock-клиентами. Проверка аккаунта, стоимости конкретной генерации, произношения/просодии и пользовательского антивируса не объявляется пройденной.

## Подтверждённые ошибки и исправления

| Ошибка | Пункты | Исправление и доказательство |
|---|---|---|
| При общем аудио кадры распределялись независимо от реплик; подпись и кадр могли относиться к разным блокам | 20–22, 31 | Сначала длительность блока по тексту, затем его кадры; минимум кадров учитывается в целочисленном распределении. `audit.test.mjs` |
| Длинная реплика обрезалась CSS line-clamp | 31–32 | Полные страницы субтитров с лимитом строк, без скрытия слов; приблизительные границы страниц помечаются fallback. Проверены Unicode и HTML-символы |
| Оставшееся зеркало JSON могло воскресить удалённый проект при пустой SQLite | 11–13 | Однократная legacy-миграция version2; SQLite остаётся источником истины |
| Ошибка удаления файлов могла оставить БД и каталог в разных состояниях | 12–13 | Сначала перенос в закрытый `.deleted`, затем удаление БД; rollback каталога при ошибке SQL. Отложенная очистка повторяется при старте и видна в health |
| Повреждённый XML и разные ведущие нули shot ID диагностировались неверно | 15–16 | XML validation, канонические Sxx.xx, дубликаты/нулевые ID/размеры блоков проверяются до импорта |
| Первые визуальные кадры без речи создавали пустой TTS-блок; полностью немая сцена требовала голос | 15, 19–21 | Вступительные кадры связываются с первой репликой сцены; полностью немые сцены импортируются со skip voiceover |
| Некорректный PATCH с null-элементом давал TypeError/500 | 09, 13 | Валидация формы элемента и UUID; пользовательская ошибка 400 |
| Отмена FFmpeg могла завершить promise раньше закрытия процесса/файлов; игнорирующий SIGTERM процесс мог зависнуть | 21, 28 | Ожидание close, ограниченный grace period, SIGKILL только собственному дочернему процессу; проверки обоих вариантов |
| Новый промпт позволял поставить вторую платную задачу на тот же активный кадр | 25, 29 | Активность защищена по project/stage/item, а не только по хешу промпта. Возвращается 409 до новой оплаты |
| Сбой каталога посторонней модели блокировал подходящую модель | 17, 24 | Частичный каталог с предупреждениями, независимая проверка выбранного endpoint; 401 остаётся ошибкой ключа |
| Строковая стоимость могла складываться как текст; строковое refunded скрывало расход | 18, 30 | Числовая нормализация, строгий boolean возврата, неизвестные единицы/цена не считаются нулём |
| Неизвестный UUID проекта не нормализовал URL; старые ответы state могли мешать новой навигации | 10, 14 | Счётчик актуальности запросов, проверка выбранного проекта и fallback на root |
| Update удалял cache браузера вместе с node_modules и не устанавливал его заново | 33, 39–40 | Browser ensure внутри verify:installed до запуска: срабатывает и при обновлении старым0.6.1 bootstrap |
| Старый/импортированный клип мог не совпасть с исправленным таймлайном | 21, 27, 33 | Локальная нормализация копии для Render/Studio без платного video POST. Исходный артефакт сохраняется |
| Studio могла стартовать после начавшегося shutdown | 09, 33 | Generation/epoch guard, отмена подготовки, проверка abort; отдельный тест раннего close |

Дополнительно устранены буквальные расхождения: три TypeScript-конфига, каталоги references и остальных медиа, pure nextStage, плановые длительности/модели/расходы в карточках, команда продолжения ошибочных задач, сохранение Headers/FormData и проверки frontend API-клиента.

Дополнительно browser-проверка обнаружила выход Tab-фокуса из modal; добавлена явная циклическая обработка фокуса. До исправления проверка падала.

## Сверка всех этапов

«Автоматически» означает указанную проверку с mock API; это не подтверждение успешного платного вызова на пользовательском аккаунте.

| № | Результат сверки | Доказательство / граница |
|---|---|---|
| 01 | Node LTS в пакете, npm, локальные зависимости | Проверяемый Windows Node, engines; проверка consumer Windows/AV отдельно |
| 02 | Существующий Git не пересоздаётся | Чужие изменения старого Python-приложения сохранены; исправления относятся к video-flow. Исходники этого пакета внутри ZIP |
| 03 | Конвейер и ADR соответствуют входу из сценария | PRODUCT_SPEC, ARCHITECTURE, компонентная и state-диаграммы; ссылка отменена по указанию пользователя |
| 04 | React/Express/ESM и три tsconfig | app/node/root configs, lint/types/build, frontend stamp и production SPA fallback |
| 05 | test → lint → build сохранён | npm run check; новый тест до исправления падает. Customer verify:installed проверяет готовый frontend |
| 06 | Ключи backend-only | env vault, URL/числовые границы, key redaction, ограниченный режим без провайдеров |
| 07 | Инвалидация, прогресс и nextStage | domain/core/audit tests; stages не подменяют lifecycle |
| 08 | Responsive/keyboard | Browser smoke с 1280/1920, native dialog focus, labels/reduced motion; субъективная UX-приёмка отдельно |
| 09 | API/ошибки/shutdown | HTTP CSRF/host tests, missing frontend503, закрытие ресурсов и Studio-start race |
| 10 | Typed client / актуальность | Тесты JSON/204/409/requestId/network/Abort/Headers/FormData; browser autosave/navigation |
| 11 | WAL/FULL/STRICT/migrations | SQLite tests, legacy import once, integrity и daily backup |
| 12 | Безопасные относительные пути, manifest, каталоги | Atomic/ENOSPC tests, staged delete/rollback/cleanup, references, /files только projects |
| 13 | CRUD/revisions/lifecycle | HTTP conflicts, подтверждение удаления, null PATCH400, staging SQL rollback |
| 14 | URL/reload/popstate | Browser navigation/reload/unknown-project fallback; счётчик актуальности polling |
| 15 | DOCX по ID | Runs/кириллица/пустые ячейки/202/invalid ZIP/XML/padded IDs/limits |
| 16 | Manual/DOCX create, предупреждения | Без платных запросов; размеры ручных блоков согласованы с редактором. YouTube ввода нет |
| 17 | Статусы подключения, баланс/каталог/time | Две карточки, очистка password state, partial catalog; реальные права ключа требуют аккаунта |
| 18 | Lumean DTO/poll/auth | Contract tests, pagination, wallet/alignment mapping. Реальная озвучка и тарификация не проверены |
| 19 | Block TTS queue1 | Mock create/poll/download, длительность, player, retry и cost normalization; качество голоса отдельно |
| 20 | Project > scene > block audio | Upload decode/limits, skip voiceover, общий/сценовый источник и отсутствие лишнего TTS |
| 21 | Duration / FFmpeg / integer frames | Реальные media fixtures, fallback duration, frame minimums, cancel/kill, локальная нормализация |
| 22 | Lazy Whisper worker/cache/deadline | Отдельный worker, cancellation, native cache path, pure alignment tests. Реальный ASR по языкам требует материала |
| 23 | Storyboard/autosave/approve | Text/prompts/camera/weight + плановые секунды; pure nextStage показывает рекомендацию. Просмотр вкладок разрешён; платные действия и render валидируются сервером |
| 24 | По согласованию fal.ai вместо SYNTX | Auth/endpoint-specific inputs/ID/URLs/upload/cancel contracts. Живой inference не вызывался |
| 25 | Image sequence и защита оплаты | Decode/PNG/atomic/late output, один активный frame по stage; готовые не заменяются автоматически |
| 26 | Grid/list/status/duration/cost/retry | 20 карточек, queued/ready/errors, cost/model metadata, image fallback; browser проверка |
| 27 | Image-to-video | Прямой upload PNG; codec/fps/duration/muted. Старый короткий clip нормализуется локально в public workspace |
| 28 | Video concurrency2 / cancel/retry | Queue tests, native process cancellation и отдельная команда failed/paused. Возврат средств не обещается |
| 29 | FIFO/dedupe/restart | Сохранённые native IDs, paused/uncertain, per-item active guard; неизвестный POST не повторяется автоматически |
| 30 | Прогресс/стоимость/logs | Сумма только конечных числовых значений с известными единицами; неверные типы unknown; не заявляется подтверждение счёта провайдера |
| 31 | SRT/VTT | Страницы без потери слов, Unicode/entities/timecode/монотонность, настройки maxLines/safeArea, estimated page boundaries |
| 32 | Remotion | Sequence/Img/OffthreadVideo muted/Audio, caption pages, integer timeline; real media E2E |
| 33 | Assembly/Studio | FFprobe результата, manifest input hash/version, локальная совместимость старых clips и закрытие дочернего процесса |
| 34 | Health/monitor/logs | Frontend/backend/PID/queues/errors + cleanup status; ограниченный redacted JSONL |
| 35 | Локальная безопасность | Loopback/CSRF/Origin, traversal/ZIP/SSRF/download caps, без shell, negative tests. Не заявляется независимый pentest |
| 36 | Backup/export/restore | 7 дневных SQLite копий, project ZIP/проверка медиа/новый UUID, integrity read-only; полный backup data/config нужен для pending IDs/receipts |
| 37 | Автотесты | Domain/DB/media/provider/HTTP/client + browser/E2E; отдельные fixtures, закрытие ресурсов до temp cleanup |
| 38 | UX/accessibility | Keyboard dialog/navigation, 202-frame dry-run, paginated cards; ручная accessibility/производительность на целевом ПК отдельно |
| 39 | Windows package/install/start | Готовый dist и hash/version, PID/ready/homepage gate, installer lock; отдельный Windows workflow. Чистая потребительская Windows/антивирус — ручная граница |
| 40 | Update/rollback/E2E | Отдельный workflow проверяет upgrade с предыдущего пакета и искусственную ошибку с rollback и сохранением data/config. Итог определяется результатом workflow |

## Команды приёмки

- `npm run check`: unit/integration, oxlint, TypeScript, Vite и frontend stamp.
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium node scripts/check-ui.mjs`: браузер, сценарий/DOCX, отсутствие ввода ссылки, autosave, 3 styles, модельные меню, языки, клавиатура, 1280/1920, неизвестный URL.
- `REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium node scripts/e2e.mjs`: настоящий MP4 30/60 FPS, короткий clip → локальная нормализация, Studio, export/restore, restart и 202-frame dry-run, без paid API.
- Windows workflow пакета: установка, повторный Start/Stop, upgrade 0.6.1 → 0.6.2, сохранение проекта/локального тестового config, искусственно сломанный update → rollback. Логи — артефакт GitHub Actions. Результат конкретного запуска сообщается отдельно от наличия YAML.

Не закрываются автоматически: качество реальных платных изображений/анимации, доступ аккаунтов/моделей и баланс, произношение и native просодия Lumean, точность реального Whisper, приёмка на чистой пользовательской Windows под её антивирусом. Проверенный mock контракт не является доказательством этих свойств.
