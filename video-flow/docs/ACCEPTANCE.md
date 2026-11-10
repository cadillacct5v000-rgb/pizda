# Приёмка 40 рекомендаций

Этот файл отделяет реализацию от доказанной приёмки. Документ не объявляет все 40 этапов полностью закрытыми. Код рабочего первого прохода находится в одном модуле рядом с прежним KadroGen; прежний Python pipeline и его тесты сохраняются.

| Этапы PDF | Реализация / проверка | Остаток |
|---|---|---|
| 01–05: среда, Git, архитектура, стек, quality gate | Node24, ES modules, React19/Vite8/TS, Express5; lockfile; тесты/lint/build | Три отдельных tsconfig не нужны текущей общей сборке; один строгий config |
| 06–10: секреты, модель, UI, API | Backend vault, typed client, CSRF, дизайн/stepper, project/frame types | Каталог провайдеров в UI упрощён |
| 11–14: БД/файлы/CRUD/URL | SQLite WAL STRICT, ревизии, manifests, архив/lifecycle, History API | Первый проход legacy migration ожидает совместимые manifest |
| 15–17: DOCX, черновик, подключения | Парсер OOXML по ID, 202 строки в тесте, ручной текст, проверка ключа | Полная пагинация каталогов и выбор Lumean template из списка ещё не сделаны |
| 18–20: Lumean, TTS, загрузка | Адаптер по public OpenAPI, поблочная очередь, project/scene/block audio | Живые тарифы/ошибки ключа без ключа не проверены; Lumean service alignment пока не импортируется |
| 21–23: медиа, Whisper, storyboard | FFprobe, integer allocation, lazy CPU Whisper, confidence, editor/autosave/approval | Нет мгновенной остановки Whisper inference; визуальная редактура границ аудио отсутствует |
| 24–29: SYNTX, images/video, queues | Опубликованные SPA contracts, dedicated chat, persisted ID, mock tests, queues1/1/2, cancel | Реальные SYNTX image/video на аккаунте требуют проверки; исходные provider settings поддержаны для выбранных моделей |
| 30–33: прогресс/стоимость/subtitles/render | Детерминированный progress, журнал, неизвестная стоимость, SRT/VTT, Remotion, verified MP4 | Studio пока открывает базовую композицию; нет полноценного редактора титров/музыки |
| 34–36: диагностика/защита/backup | Health/logs/monitor, origin/CSRF, pinned HTTPS, path/ZIP checks, 7 backups, media ZIP/restore | Full backup медиа — отдельный ZIP/копия data, не дневная SQLite-копия |
| 37–38: тесты/UX | node:test, browser create/navigation/autosave/reload, 20 карточек на странице | Ручная accessibility/производительность 202 карточек на слабом ПК ещё не проверены |
| 39–40: Windows/update/E2E | Пакет с проверенным Node, Install/Start/Stop/Check/Monitor/Update, backup/rollback, 3-scene render +202 dry-run | Чистая Windows VM и реальный rollback/антивирус требуют ручной приёмки; текущий статус CI в Actions |

Проверки локальной среды: `npm run check`, `node scripts/check-ui.mjs`, `node scripts/e2e.mjs`. В E2E внешние API заменены, медиа и FFmpeg/Remotion настоящие. Фактические артефакты сохраняются в test-results и публикуются workflow. Проверка 60 FPS проверяет поток FFprobe, а не только метаданные проекта.

Проверки рисков: revision lost-update — SQLite test; duplicate billing — generation/restart/uncertain tests; stale late output — input-change test; ZIP/traversal/SSRF — negative tests; real media duration — synthetic FFmpeg test; frame rounding — 249 разных размеров таймлайна; DOCX ordering/Cyrillic/202 — parser test; browser race — переход до debounce и reload.
