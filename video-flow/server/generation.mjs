import {readFileSync, renameSync, rmSync} from 'node:fs';
import {hash, ensure, AppError, atomic, within, now, redact} from './core.mjs';
import {createGenerationQueue} from './queue.mjs';
import {buildTimeline} from './timeline.mjs';
import {validateMedia, normalizeClip} from './media.mjs';
import {poll} from './providers.mjs';
import {addLog} from './domain.mjs';

export function generationService(store, clients, config) {
  const queues = Object.fromEntries(Object.entries(config.concurrency).map(([name, limit]) => [name, createGenerationQueue({name, limit})]));
  for (const operation of store.operations.list()) {
    if (['creating', 'running', 'queued'].includes(operation.status)) store.operations.put({...operation, status: operation.status === 'creating' && !operation.externalId ? 'uncertain' : 'paused'});
  }
  function specification(project, stage, itemId) {
    ensure(['voice', 'image', 'video'].includes(stage), 'Неверный тип генерации');
    const item = (stage === 'voice' ? project.blocks : project.frames).find((candidate) => candidate.id === itemId);
    ensure(item, 'Элемент не найден', 404);
    const provider = stage === 'voice' ? project.models.voiceProvider : project.models.imageProvider;
    ensure(['fastgen', 'elevenlabs', 'lumean', 'syntx'].includes(provider), 'Неизвестный провайдер');
    let payload, duration;
    if (stage === 'voice') {
      ensure(item.text.trim(), 'Пустой текст озвучки', 409);
      if (provider === 'elevenlabs') { ensure(project.models.voiceId, 'Укажите Voice ID', 409); payload = {text: item.text, voiceId: project.models.voiceId, model: project.models.voiceModel}; }
      else if (provider === 'lumean') { ensure(project.models.templateId, 'Выберите шаблон Lumean', 409); payload = {template_id: project.models.templateId, input_text: item.text}; }
      else if (provider === 'fastgen') payload = {operation: project.models.voiceModel, prompt: item.text, voice: project.models.voiceId};
      else throw new AppError(409, 'Для озвучки выберите ElevenLabs, Lumean или FastGen');
    } else {
      ensure(item.approved, `Сначала утвердите кадр ${item.shotId}`, 409);
      ensure(item.imagePrompt.trim(), `Пустой промпт ${item.shotId}`, 409);
      if (stage === 'image') payload = provider === 'syntx' ? {stage, model: project.models.syntxImageModel, prompt: item.imagePrompt} : {operation: project.models.imageModel, prompt: item.imagePrompt, aspect_ratio: '16:9'};
      else {
        ensure(project.options.animationEnabled && item.image, 'Включите анимацию и создайте изображение', 409);
        const visual = buildTimeline(project, false).visuals.find((f) => f.id === itemId);
        duration = visual.durationInFrames / (project.options.fps || 30);
        const image = readFileSync(within(store.projectsDir, item.image.path)).toString('base64'), prompt = `${item.motionPrompt || 'Subtle natural motion'}. Camera: ${item.camera}`;
        payload = provider === 'syntx' ? {stage, model: project.models.syntxVideoModel, service: project.models.syntxVideoService, prompt, image, duration} : {operation: project.models.videoModel, prompt, inputs: [`data:image/png;base64,${image}`], aspect_ratio: '16:9', keyframes: true, duration_seconds: Math.max(3, Math.min(8, Math.ceil(duration)))};
      }
    }
    return {item, provider, payload, duration, fps: project.options.fps || 30, fingerprint: hash({stage, provider, payload})};
  }
  function enqueue(projectId, stage, itemId, {newTake = false, acknowledgeUncertain = false} = {}) {
    const project = store.get(projectId), spec = specification(project, stage, itemId);
    ensure(config.providers[spec.provider]?.key, `Подключите ${spec.provider} в настройках`, 409);
    const key = `${projectId}:${stage}:${itemId}:${spec.fingerprint}`;
    let operation = store.operations.get(key);
    ensure(!newTake || !queues[stage].stats().keys.includes(key), 'Генерация уже выполняется', 409);
    if (newTake) {
      ensure(operation?.status !== 'uncertain' || acknowledgeUncertain, 'Предыдущий запрос мог быть оплачен. Проверьте кабинет и подтвердите новый платный запуск.', 409);
      operation = null;
    }
    if (operation?.status === 'uncertain') throw new AppError(409, 'Сервис мог принять запрос, но ответ потерян. Автоповтор отключён во избежание повторной оплаты. Проверьте кабинет.', {operationKey: key, uncertain: true});
    if (operation?.status === 'ready') {
      const asset = stage === 'voice' ? spec.item.audio : spec.item[stage === 'image' ? 'image' : 'video'];
      if (asset) return {key, status: 'ready'};
    }
    if (!operation) operation = {key, projectId, stage, itemId, provider: spec.provider, fingerprint: spec.fingerprint, status: 'queued', attempt: now(), externalId: null, createdAt: now()};
    store.operations.put(operation);
    queues[stage].enqueue(key, async (signal) => {
      const op = {...operation};
      const client = clients[op.provider]; let temporary;
      try {
        signal.throwIfAborted();
        ensure(specification(store.get(projectId), stage, itemId).fingerprint === spec.fingerprint, 'Входные данные изменились. Запустите заново.', 409);
        let bytes, status;
        if (!op.externalId) {
          op.status = 'creating'; store.operations.put(op);
          if (op.provider === 'elevenlabs') bytes = await client.speech(spec.payload, signal);
          else {
            const accepted = await client.create(spec.payload, signal, (externalId) => { op.externalId = externalId; store.operations.put(op); });
            ensure(accepted.id, 'Провайдер не вернул внешний ID', 502);
            op.externalId = String(accepted.id); op.status = 'running'; store.operations.put(op);
          }
        }
        if (!bytes) {
          status = await poll(client, op.externalId, config, signal);
          bytes = await client.result(status, stage, signal);
        }
        signal.throwIfAborted();
        const extension = stage === 'voice' ? 'mp3' : stage === 'image' ? 'png' : 'mp4';
        const folder = stage === 'voice' ? 'audio' : stage === 'image' ? 'images' : 'clips';
        const variant = stage === 'video' ? `-${spec.fps}fps-${hash(String(spec.duration)).slice(0,8)}` : '';
        const relative = `${projectId}/${folder}/${itemId}-${hash(op.attempt).slice(0, 8)}-${spec.fingerprint.slice(0, 12)}${variant}.${extension}`;
        const file = within(store.projectsDir, relative); temporary = `${file}.raw.${extension}`;
        atomic(temporary, bytes);
        const probe = await validateMedia(temporary, stage === 'voice' ? 'audio' : stage, signal);
        if (stage === 'video') { await normalizeClip(temporary, `${file}.tmp.mp4`, spec.duration, signal, spec.fps); rmSync(temporary, {force: true}); temporary = `${file}.tmp.mp4`; }
        signal.throwIfAborted(); renameSync(temporary, file); temporary = null;
        const asset = {path: relative, duration: stage === 'image' ? null : stage === 'video' ? spec.duration : probe.duration, sourceDuration: Number.isFinite(probe.duration) ? probe.duration : null, provider: op.provider, externalId: op.externalId, inputHash: spec.fingerprint, createdAt: now()};
        store.mutate(projectId, (latest) => {
          ensure(specification(latest, stage, itemId).fingerprint === spec.fingerprint, 'Данные изменились; результат сохранён в файлах, но не применён', 409);
          if (stage === 'video') ensure((latest.options.fps || 30) === spec.fps && specification(latest,stage,itemId).duration === spec.duration, 'Настройки монтажа изменились; результат сохранён, но не применён', 409);
          const target = (stage === 'voice' ? latest.blocks : latest.frames).find((item) => item.id === itemId);
          target[stage === 'voice' ? 'audio' : stage] = asset;
          if (stage === 'voice') { target.status = 'ready'; latest.captions = null; latest.frames.filter((f) => f.blockId === itemId).forEach((f) => { f.video = null; }); }
          if (stage === 'image') target.video = null;
          latest.render = null;
          const chargeId = `${op.key}:${op.attempt}`;
          if (!latest.cost.some((entry) => entry.id === chargeId)) latest.cost.push({id: chargeId, stage, provider: op.provider, unit: status?.usage?.credits !== undefined ? 'credits' : null, value: status?.usage?.credits ?? null, refunded: status?.usage?.refunded ?? null});
          addLog(latest, 'success', `${stage}: готово`, itemId); return latest;
        });
        op.status = 'ready'; op.asset = asset; op.error = null; store.operations.put(op);
      } catch (error) {
        // A sent POST with no accepted ID is ambiguous, including cancellation and process/network failure.
        const uncertain = op.status === 'creating' && !op.externalId;
        op.status = uncertain ? 'uncertain' : signal.aborted ? 'cancelled' : 'error';
        op.error = uncertain ? 'Ответ на создание потерян; проверьте кабинет провайдера' : redact(error.message, Object.values(config.providers).map((p) => p.key));
        store.operations.put(op);
        if (signal.aborted && op.externalId && client.cancel) { try { await client.cancel(op.externalId); } catch { op.cancelUnconfirmed = true; store.operations.put(op); } }
        try { store.mutate(projectId, (latest) => { addLog(latest, signal.aborted ? 'warning' : 'error', op.error, itemId); return latest; }); } catch (loggingError) { if (loggingError.status !== 404) throw loggingError; }
        throw error;
      } finally { if (temporary) rmSync(temporary, {force: true}); }
    });
    return {key, status: 'queued'};
  }
  return {enqueue, queues, stats: () => Object.values(queues).map((q) => q.stats()),
    cancel(projectId, itemId) { let count = 0; for (const queue of Object.values(queues)) for (const key of queue.stats().keys) if (key.startsWith(`${projectId}:`) && (!itemId || key.split(':')[2] === itemId)) { if (queue.cancel(key)) { count++; const op = store.operations.get(key); if (op?.status === 'queued') store.operations.put({...op, status: 'cancelled'}); } } return count; },
    close: () => Promise.all(Object.values(queues).map((q) => q.close())),
  };
}
