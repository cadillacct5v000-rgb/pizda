import {ensure, id, now, text, number} from './core.mjs';

export const stages = ['draft', 'voiceover', 'storyboard', 'images', 'animation', 'subtitles', 'assembly'];
export const frameCountLimit = 1000;
export function createProject(input = {}) {
  const stamp = now();
  return {
    id: id(), title: text(input.title || 'Новый проект', 'Название', 200), language: text(input.language || 'ru', 'Язык', 40),
    createdAt: stamp, updatedAt: stamp, revision: 1, lifecycle: 'active', favorite: false, stage: 'draft',
    options: {animationEnabled: false, subtitlesEnabled: true, voiceoverSkipped: false, silentFrameDuration: 4, fps: 30},
    models: {voiceProvider: 'elevenlabs', voiceId: '', voiceModel: 'eleven_multilingual_v2', templateId: '', imageProvider: 'fastgen', imageModel: 'flower_image_generate', videoModel: 'flow_video_lite_from_keyframes', syntxImageModel: 'gpt-image-2', syntxVideoModel: 'wan_26_i2v', syntxVideoService: 'wan_video'},
    captionStyle: {fontSize: 48, position: 'bottom', maxLines: 2, safeArea: 100},
    scenes: [], blocks: [], frames: [], uploads: {}, captions: null, render: null, logs: [], cost: [],
  };
}
export function addLog(project, level, message, itemId) {
  project.logs = [...project.logs.slice(-199), {at: now(), level, message, ...(itemId ? {itemId} : {})}];
}
export function scriptRows(input, framesPerScene = 1) {
  const paragraphs = text(input, 'Сценарий', 200000).split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean);
  ensure(paragraphs.length <= frameCountLimit, `Не более ${frameCountLimit} блоков`);
  number(framesPerScene, 'Кадров на сцену', 1, 20);
  ensure(Number.isInteger(framesPerScene) && paragraphs.length * framesPerScene <= frameCountLimit, 'Слишком много кадров');
  return paragraphs.flatMap((paragraph, index) => Array.from({length: framesPerScene}, (_, shot) => ({
    shotId: `S${String(index + 1).padStart(2, '0')}.${String(shot + 1).padStart(2, '0')}`, sceneKey: String(index + 1),
    narration: shot === 0 ? paragraph : '', visual: '', prompt: '',
  })));
}
export function importRows(project, rows) {
  ensure(Array.isArray(rows) && rows.length > 0 && rows.length <= frameCountLimit, 'Нет кадров или превышен лимит');
  const seen = new Set(), scenes = new Map(), blocks = [], frames = [];
  for (const row of rows) {
    ensure(!seen.has(row.shotId), `Повтор shot ID ${row.shotId}`); seen.add(row.shotId);
    if (!scenes.has(row.sceneKey)) scenes.set(row.sceneKey, {id: id(), title: `Сцена ${row.sceneKey}`, approved: false, voiceoverSkipped: false});
    const sceneId = scenes.get(row.sceneKey).id;
    let block = blocks.at(-1);
    if (row.narration || !block || block.sceneId !== sceneId) {
      block = {id: id(), sceneId, text: row.narration || '', status: 'draft', audio: null}; blocks.push(block);
    }
    frames.push({id: id(), sceneId, blockId: block.id, shotId: row.shotId, visualDescription: row.visual || '', imagePrompt: row.prompt || row.visual || '', motionPrompt: '', camera: 'static', durationWeight: 1, approved: false, image: null, video: null});
  }
  ensure(blocks.some((b) => b.text) || project.options.voiceoverSkipped, 'Сценарий пуст');
  return {...project, scenes: [...scenes.values()], blocks, frames, uploads: {}, captions: null, render: null, stage: 'draft'};
}
export function patchProject(project, patch) {
  const result = structuredClone(project);
  ensure(patch && typeof patch === 'object' && !Array.isArray(patch), 'Ожидается объект изменений');
  const allowed = new Set(['revision', 'title', 'language', 'lifecycle', 'favorite', 'stage', 'options', 'models', 'captionStyle', 'frames', 'blocks', 'scenes']);
  for (const key of Object.keys(patch)) ensure(allowed.has(key), `Нельзя изменить ${key}`);
  for (const key of ['title', 'language']) if (key in patch) result[key] = text(patch[key], key, key === 'title' ? 200 : 40);
  if ('lifecycle' in patch) { ensure(['active', 'completed', 'archived'].includes(patch.lifecycle), 'Неверный lifecycle'); result.lifecycle = patch.lifecycle; }
  if ('stage' in patch) { ensure(stages.includes(patch.stage), 'Неверный этап'); result.stage = patch.stage; }
  if ('favorite' in patch) { ensure(typeof patch.favorite === 'boolean', 'favorite должен быть boolean'); result.favorite = patch.favorite; }
  if (patch.options) for (const [key, value] of Object.entries(patch.options)) {
    ensure(key in result.options, `Неизвестная опция ${key}`);
    if (key === 'silentFrameDuration') number(value, key, 1, 60); else if (key === 'fps') ensure([30,60].includes(value), 'Выберите 30 или 60 FPS'); else ensure(typeof value === 'boolean', `${key} должен быть boolean`);
    if (result.options[key] !== value) { result.render = null; result.captions = null; if (key !== 'subtitlesEnabled' && key !== 'animationEnabled') result.frames.forEach((f) => { f.video = null; }); }
    result.options[key] = value;
  }
  if (patch.models) for (const [key, value] of Object.entries(patch.models)) {
    ensure(key in result.models, `Неизвестная модель ${key}`); text(value, key, 200, true);
    if (key === 'voiceProvider') ensure(['elevenlabs','fastgen','lumean'].includes(value), 'Неизвестный провайдер голоса');
    if (key === 'imageProvider') ensure(['fastgen','syntx'].includes(value), 'Неизвестный провайдер визуала');
    if (result.models[key] !== value) {
      if (['imageProvider', 'imageModel', 'syntxImageModel'].includes(key)) result.frames.forEach((f) => { f.image = null; f.video = null; });
      else if (['videoModel', 'syntxVideoModel', 'syntxVideoService'].includes(key)) result.frames.forEach((f) => { f.video = null; });
      else { result.blocks.forEach((b) => { b.audio = null; b.status = 'draft'; }); result.captions = null; }
      result.render = null;
    }
    result.models[key] = value;
  }
  if (patch.captionStyle) for (const [key, value] of Object.entries(patch.captionStyle)) {
    if (key === 'fontSize') number(value, key, 18, 100);
    else if (key === 'safeArea') number(value, key, 24, 300);
    else if (key === 'maxLines') { number(value, key, 1, 4); ensure(Number.isInteger(value), 'Целое число строк'); }
    else if (key === 'position') ensure(['top', 'center', 'bottom'].includes(value), 'Неверная позиция');
    else ensure(false, 'Неизвестное поле субтитров');
    result.captionStyle[key] = value; result.render = null;
  }
  const editable = {frames: ['visualDescription', 'imagePrompt', 'motionPrompt', 'camera', 'durationWeight', 'approved'], blocks: ['text'], scenes: ['title', 'approved', 'voiceoverSkipped']};
  for (const group of Object.keys(editable)) if (patch[group]) {
    ensure(Array.isArray(patch[group]) && patch[group].length <= frameCountLimit, 'Неверные элементы');
    for (const change of patch[group]) {
      const item = result[group].find((candidate) => candidate.id === change.id);
      ensure(item, `Неизвестный ID ${change.id}`);
      for (const [key, value] of Object.entries(change)) {
        if (key === 'id') continue;
        ensure(editable[group].includes(key), `Нельзя изменить ${key}`);
        if (['approved', 'voiceoverSkipped'].includes(key)) ensure(typeof value === 'boolean', 'Ожидается boolean');
        else if (key === 'durationWeight') number(value, key, 0.05, 100);
        else text(value, key, key === 'text' ? 20000 : 10000, true);
        if (item[key] === value) continue;
        item[key] = value; result.render = null;
        if (group === 'frames' && ['imagePrompt', 'visualDescription'].includes(key)) { item.image = null; item.video = null; item.approved = false; }
        if (group === 'frames' && ['motionPrompt', 'camera', 'durationWeight'].includes(key)) item.video = null;
        if (group === 'blocks') { item.audio = null; item.status = 'draft'; result.captions = null; if (result.uploads.project) delete result.uploads.project.timings; if (result.uploads[item.sceneId]) delete result.uploads[item.sceneId].timings; result.frames.filter((f) => f.blockId === item.id).forEach((f) => { f.video = null; }); }
        if (key === 'voiceoverSkipped') { result.captions = null; result.frames.filter((f) => f.sceneId === item.id).forEach((f) => { f.video = null; }); }
      }
    }
  }
  if (!result.options.animationEnabled && result.stage === 'animation') result.stage = 'images';
  return result;
}
export function progress(project) {
  if (project.render) return 100;
  if (!project.frames.length) return 0;
  const voice = project.options.voiceoverSkipped || project.uploads.project ? 1 : project.blocks.filter((b) => b.audio || project.uploads[b.sceneId]).length / Math.max(1, project.blocks.length);
  const approved = project.frames.filter((f) => f.approved).length / project.frames.length;
  const images = project.frames.filter((f) => f.image).length / project.frames.length;
  const videos = project.options.animationEnabled ? project.frames.filter((f) => f.video).length / project.frames.length : images;
  return Math.round(10 + voice * 20 + approved * 10 + images * 25 + videos * 25 + (project.captions || !project.options.subtitlesEnabled ? 5 : 0));
}
