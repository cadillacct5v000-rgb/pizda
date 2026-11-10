import express from 'express';
import path from 'node:path';
import {existsSync, readFileSync, appendFileSync, mkdirSync, statSync, renameSync} from 'node:fs';
import {randomUUID, timingSafeEqual} from 'node:crypto';
import {openStore} from './database.mjs';
import {createProject, patchProject, importRows, scriptRows, progress, addLog} from './domain.mjs';
import {AppError, ensure, within, atomic, decode64, now, hash, redact} from './core.mjs';
import {parseDocx, mergePrompts} from './docx.mjs';
import {providerClients} from './providers.mjs';
import {generationService} from './generation.mjs';
import {createGenerationQueue} from './queue.mjs';
import {validateMedia} from './media.mjs';
import {buildTimeline, subtitles} from './timeline.mjs';
import {transcriber} from './alignment.mjs';
import {renderService} from './render.mjs';
import {exportArchive, restoreArchive, artifacts} from './archive.mjs';
import {appDir} from './config.mjs';

export const VERSION = '0.1.0';
export function createApp(config, dependencies = {}) {
  const store = dependencies.store || openStore(config.root), clients = dependencies.clients || providerClients(config);
  const generation = generationService(store, clients, config), local = createGenerationQueue({name: 'local', limit: 1});
  const renderer = dependencies.renderer || renderService(store, dependencies), transcribe = dependencies.transcribe || transcriber(config);
  const app = express(), csrf = randomUUID(), started = Date.now(), integrations = {}, errors = [];
  const logDirectory = path.join(config.root, 'logs'); mkdirSync(logDirectory, {recursive: true});
  const logFile = path.join(logDirectory, 'backend.jsonl');
  function log(level, event, fields = {}) {
    const record = {at: now(), level, event, ...fields};
    if (level === 'error') { errors.push(record); if (errors.length > 20) errors.shift(); }
    try {
      if (existsSync(logFile) && statSync(logFile).size > 4 * 1024 * 1024) { for (let i = 2; i >= 1; i--) if (existsSync(`${logFile}.${i}`)) renameSync(`${logFile}.${i}`, `${logFile}.${i + 1}`); renameSync(logFile, `${logFile}.1`); }
      appendFileSync(logFile, JSON.stringify(record) + '\n', {mode: 0o600});
    } catch (error) { process.stderr.write(`Backend log unavailable: ${error.code}\n`); }
  }
  const safeError = (error) => redact(error.status ? error.message : 'Внутренняя ошибка сервера', Object.values(config.providers).map((p) => p.key));
  app.disable('x-powered-by');
  app.use((req, res, next) => {
    req.requestId = randomUUID(); res.setHeader('X-Request-ID', req.requestId); res.setHeader('X-Content-Type-Options', 'nosniff'); res.setHeader('Referrer-Policy', 'no-referrer'); res.setHeader('Cross-Origin-Resource-Policy', 'same-origin'); res.setHeader('X-Frame-Options', 'DENY');
    const begin = Date.now(); res.on('finish', () => { if (res.statusCode >= 400 || Date.now() - begin > 1500) log(res.statusCode >= 500 ? 'error' : 'warning', 'http', {requestId: req.requestId, method: req.method, status: res.statusCode, milliseconds: Date.now() - begin}); });
    try {
      const host = new URL(`http://${req.headers.host}`).hostname;
      ensure(['127.0.0.1', 'localhost', '[::1]'].includes(host) && ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress), 'Доступ разрешён только с этого компьютера', 403);
      if (req.headers.origin) { const origin = new URL(req.headers.origin); ensure(['http:', 'https:'].includes(origin.protocol) && ['127.0.0.1','localhost','[::1]'].includes(origin.hostname) && (origin.host === req.headers.host || origin.port === '5173'), 'Запрос с постороннего сайта отклонён', 403); }
      if (!['GET','HEAD','OPTIONS'].includes(req.method)) {
        const supplied = req.headers['x-csrf-token']; ensure(typeof supplied === 'string' && supplied.length === csrf.length && timingSafeEqual(Buffer.from(supplied), Buffer.from(csrf)), 'Обновите страницу: отсутствует локальный защитный токен', 403);
      }
      next();
    } catch (error) { next(error.status ? error : new AppError(403, 'Некорректный адрес запроса')); }
  });
  app.use(express.json({limit: '72mb'})); // 50 MiB media + base64 overhead, DOCX separately capped at 20 MiB.
  const state = () => ({version: VERSION, csrf, projects: store.list().map((p) => ({id: p.id, title: p.title, language: p.language, lifecycle: p.lifecycle, favorite: p.favorite, updatedAt: p.updatedAt, revision: p.revision, frames: p.frames.length, progress: progress(p)})), integrations: Object.fromEntries(Object.entries(config.providers).map(([name, value]) => [name, integrations[name] || {status: value.key ? 'unverified' : 'not_configured'}])), queues: [...generation.stats(), local.stats()]});
  app.get('/api/state', (_req, res) => res.json(state()));
  app.get('/api/health', (_req, res) => res.json({version: VERSION, node: process.version, pid: process.pid, uptime: (Date.now() - started) / 1000, database: 'open', manifestWarning: store.mirrorStatus(), projects: store.list().length, queues: [...generation.stats(), local.stats()], recentErrors: errors}));
  app.get('/api/diagnostics', (_req, res) => res.json({integrity: store.integrity(), errors, log: existsSync(logFile) ? readFileSync(logFile, 'utf8').slice(-32000).split('\n').filter(Boolean).slice(-100) : []}));
  app.get('/api/projects/:id', (req, res) => res.json(store.get(req.params.id)));
  app.post('/api/projects', async (req, res) => {
    let project = createProject(req.body), warnings = [];
    if (req.body.docx) {
      let result = await parseDocx(decode64(req.body.docx));
      if (req.body.promptsDocx) result = mergePrompts(result, await parseDocx(decode64(req.body.promptsDocx), true));
      project = importRows(project, result.rows); warnings = result.warnings;
    } else if (req.body.script) project = importRows(project, scriptRows(req.body.script, req.body.framesPerScene || 1));
    res.status(201).json({project: store.put(project, null), warnings});
  });
  app.patch('/api/projects/:id', (req, res) => {
    const project = store.get(req.params.id); ensure(req.body.revision === project.revision, 'Проект изменился. Обновите данные.', 409);
    res.json(store.put(patchProject(project, req.body), req.body.revision));
  });
  app.delete('/api/projects/:id', (req, res) => {
    ensure(req.body.confirm === req.params.id, 'Подтвердите удаление проекта');
    ensure(![...generation.stats(),local.stats()].some((queue) => queue.keys.some((key) => key.startsWith(`${req.params.id}:`))), 'Сначала остановите операции проекта', 409);
    store.delete(req.params.id); res.status(204).end();
  });
  app.get('/api/projects/:id/export', async (req, res) => {
    if (req.query.media === '1') res.type('zip').attachment('video-project.zip').send(await exportArchive(store, req.params.id));
    else res.attachment('manifest.json').json(store.get(req.params.id));
  });
  app.post('/api/restore', async (req, res) => { res.status(201).json(await restoreArchive(store, decode64(req.body.data, 50 * 1024 * 1024))); });
  app.post('/api/integrations/:provider', async (req, res) => {
    const name = req.params.provider; ensure(name in config.providers, 'Провайдер не найден', 404);
    if ('key' in req.body) config.saveKey(name, req.body.key);
    integrations[name] = {status: 'checking', checkedAt: now()};
    try {
      const details = await clients[name].check();
      // Only known catalog/balance DTO fields; never return raw authenticated profile data.
      integrations[name] = {status: 'connected', checkedAt: now(), details};
    } catch (error) { integrations[name] = {status: !config.providers[name].key ? 'not_configured' : error.status === 401 ? 'rejected' : 'unavailable', checkedAt: now(), error: safeError(error)}; }
    res.json(integrations[name]);
  });
  app.post('/api/projects/:id/generate', (req, res) => {
    ensure(typeof req.body.itemId === 'string', 'Нужен ID элемента');
    res.status(202).json(generation.enqueue(req.params.id, req.body.stage, req.body.itemId, {newTake: req.body.newTake === true, acknowledgeUncertain: req.body.acknowledgeUncertain === true}));
  });
  app.get('/api/projects/:id/jobs', (req, res) => { store.get(req.params.id); res.json(store.operations.list().filter((op) => op.projectId === req.params.id).map(({key,stage,itemId,status,externalId,error,cancelUnconfirmed}) => ({key,stage,itemId,status,externalId,error,cancelUnconfirmed}))); });
  app.post('/api/projects/:id/cancel', (req, res) => {
    store.get(req.params.id); const count = generation.cancel(req.params.id, req.body.itemId);
    for (const key of local.stats().keys) if (key.startsWith(`${req.params.id}:`)) local.cancel(key);
    res.json({cancelled: count});
  });
  app.post('/api/projects/:id/audio', async (req, res) => {
    const project = store.get(req.params.id), scope = req.body.scope || 'project';
    ensure(scope === 'project' || project.scenes.some((s) => s.id === scope) || project.blocks.some((b) => b.id === scope), 'Неверная область аудио');
    if (req.body.remove) {
      res.json(store.mutate(project.id, (latest) => { const block = latest.blocks.find((b) => b.id === scope); if (block) { block.audio = null; block.status = 'draft'; } else delete latest.uploads[scope]; latest.captions = null; latest.render = null; latest.frames.forEach((f) => { f.video = null; }); return latest; })); return;
    }
    const data = decode64(req.body.data, 50 * 1024 * 1024), relative = `${project.id}/audio/upload-${hash(data.toString('base64')).slice(0,24)}.media`, file = within(store.projectsDir, relative);
    atomic(file, data); const metadata = await validateMedia(file, 'audio');
    res.json(store.mutate(project.id, (latest) => {
      const asset = {path: relative, duration: metadata.duration, provider: 'upload', createdAt: now()};
      const block = latest.blocks.find((b) => b.id === scope);
      if (block) { block.audio = asset; block.status = 'ready'; } else latest.uploads[scope] = asset;
      latest.captions = null; latest.render = null; latest.frames.forEach((frame) => { frame.video = null; }); return latest;
    }));
  });
  app.post('/api/projects/:id/alignment', (req, res) => {
    const project = store.get(req.params.id), scope = req.body.scope || 'project';
    const block = project.blocks.find((b) => b.id === scope), source = block?.audio || project.uploads[scope]; ensure(source, 'Нет аудио для выравнивания', 409);
    const blocks = scope === 'project' ? project.blocks : block ? [block] : project.blocks.filter((b) => b.sceneId === scope), inputHash = hash({source, blocks});
    const key = `${project.id}:alignment:${scope}`;
    local.enqueue(key, async (signal) => {
      try {
        const result = await transcribe(within(store.projectsDir, source.path), blocks, source.duration, signal);
        store.mutate(project.id, (latest) => {
          const target = latest.blocks.find((b) => b.id === scope)?.audio || latest.uploads[scope];
          const currentBlocks = scope === 'project' ? latest.blocks : block ? latest.blocks.filter((b) => b.id === scope) : latest.blocks.filter((b) => b.sceneId === scope);
          ensure(hash({source: target, blocks: currentBlocks}) === inputHash, 'Аудио или текст изменились', 409);
          target.timings = result.timings; target.alignmentModel = result.model; latest.captions = null; latest.render = null;
          addLog(latest, 'success', 'Выравнивание готово. Проверьте confidence.', scope); return latest;
        });
      } catch (error) { log('error', 'alignment', {message: safeError(error)}); store.mutate(project.id, (latest) => { addLog(latest, 'error', safeError(error)); return latest; }); throw error; }
    }); res.status(202).json({key});
  });
  app.post('/api/projects/:id/subtitles', (req, res) => {
    const project = store.get(req.params.id); ensure(project.options.subtitlesEnabled, 'Субтитры выключены', 409);
    const timeline = buildTimeline(project, false), base = `${project.id}/subtitles/${hash(timeline.cues).slice(0,16)}`;
    atomic(within(store.projectsDir, `${base}.srt`), subtitles(timeline.cues)); atomic(within(store.projectsDir, `${base}.vtt`), subtitles(timeline.cues, true));
    res.json(store.mutate(project.id, (latest) => { latest.captions = {srt: {path: `${base}.srt`}, vtt: {path: `${base}.vtt`}, estimated: timeline.timingFallback}; return latest; }));
  });
  app.post('/api/projects/:id/render', (req, res) => {
    const project = store.get(req.params.id), timeline = buildTimeline(project), inputHash = hash(timeline), key = `${project.id}:render`;
    local.enqueue(key, async (signal) => {
      try {
        const render = await renderer.render(project, signal);
        store.mutate(project.id, (latest) => { ensure(hash(buildTimeline(latest)) === inputHash, 'Проект изменился во время сборки; файл сохранён, но не назначен актуальным', 409); latest.render = render; latest.stage = 'assembly'; addLog(latest, 'success', 'MP4 собран и проверен'); return latest; });
      } catch (error) { log('error', 'render', {message: safeError(error)}); store.mutate(project.id, (latest) => { addLog(latest, signal.aborted ? 'warning' : 'error', safeError(error)); return latest; }); throw error; }
    }); res.status(202).json({key});
  });
  app.post('/api/studio', (_req, res) => res.json({url: renderer.openStudio()}));
  app.post('/api/shutdown', (_req, res) => { ensure(dependencies.requestShutdown, 'Сервер запущен внутри тестового процесса', 409); res.json({stopping: true}); setImmediate(dependencies.requestShutdown); });
  app.get('/api/integrity', (_req, res) => res.json({database: store.integrity(), missing: store.list().flatMap((p) => artifacts(p).filter((a) => !existsSync(within(store.projectsDir, a.path))).map((a) => ({projectId: p.id, path: a.path})))}));
  app.use('/api', (_req, _res, next) => next(new AppError(404, 'API endpoint не найден')));
  app.use('/files', express.static(store.projectsDir, {index: false, dotfiles: 'deny', setHeaders: (res) => res.setHeader('Content-Security-Policy', "default-src 'none'; sandbox") }));
  const dist = path.join(appDir, 'dist');
  if (existsSync(dist)) { app.use(express.static(dist)); app.get('/{*splat}', (_req, res) => res.sendFile(path.join(dist, 'index.html'))); }
  app.use((error, req, res, _next) => {
    const status = error.status && error.status >= 400 && error.status <= 599 ? error.status : 500;
    log(status >= 500 ? 'error' : 'warning', 'request_error', {requestId: req.requestId, status, message: safeError(error)});
    res.status(status).json({message: safeError(error), status, requestId: req.requestId, ...(error instanceof AppError && error.details && !error.details.cause ? {details: error.details} : {})});
  });
  log('info', 'start', {version: VERSION});
  const backup = () => store.backup().catch((error) => log('error', 'backup', {message: safeError(error)}));
  const initialBackup = backup(), timer = setInterval(backup, 86400000); timer.unref();
  return {app, store, generation, local, csrf, async close() { clearInterval(timer); await generation.close(); await local.close(); renderer.close(); await initialBackup; await backup(); store.close(); log('info', 'shutdown'); }};
}
