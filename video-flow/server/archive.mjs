import JSZip from 'jszip';
import {existsSync, readFileSync, rmSync} from 'node:fs';
import {ensure, within, atomic, now} from './core.mjs';
import {createProject, patchProject, importRows, addLog} from './domain.mjs';
import {validateMedia} from './media.mjs';

export function artifacts(project) { return [project.uploads.project, ...Object.values(project.uploads).filter((a) => a !== project.uploads.project), ...project.blocks.map((b) => b.audio), ...project.frames.flatMap((f) => [f.image, f.video]), project.render, project.captions?.srt, project.captions?.vtt].filter(Boolean); }
export async function exportArchive(store, projectId) {
  const project = store.get(projectId), zip = new JSZip(); zip.file('manifest.json', JSON.stringify(project, null, 2));
  const seen = new Set();
  for (const asset of artifacts(project)) if (!seen.has(asset.path)) {
    seen.add(asset.path); const file = within(store.projectsDir, asset.path);
    ensure(existsSync(file), `Отсутствует артефакт: ${asset.path.split('/').at(-1)}`, 409);
    zip.file(asset.path.split('/').slice(1).join('/'), readFileSync(file));
  }
  return zip.generateAsync({type: 'nodebuffer', compression: 'DEFLATE', compressionOptions: {level: 3}});
}
export async function restoreArchive(store, buffer) {
  let zip; try { zip = await JSZip.loadAsync(buffer); } catch { ensure(false, 'Повреждён архив'); }
  const entries = Object.values(zip.files).filter((entry) => !entry.dir);
  ensure(entries.length <= 5000 && entries.reduce((sum, entry) => sum + (entry._data?.uncompressedSize ?? 1e12), 0) <= 512 * 1024 * 1024, 'Архив слишком большой');
  const manifest = zip.file('manifest.json'); ensure(manifest && manifest._data?.uncompressedSize < 16 * 1024 * 1024, 'Нет корректного manifest.json');
  let source; try { source = JSON.parse(await manifest.async('string')); } catch { ensure(false, 'Неверный manifest'); }
  ensure(source && typeof source.id === 'string' && /^[\da-f-]{36}$/i.test(source.id) && Array.isArray(source.frames) && source.frames.length <= 1000 && source.frames.length > 0 && Array.isArray(source.blocks) && Array.isArray(source.scenes), 'Неверная структура проекта');
  for (const group of [source.scenes, source.blocks, source.frames]) ensure(new Set(group.map((item) => item.id)).size === group.length && group.every((item) => /^[\da-f-]{36}$/i.test(item.id)), 'Неверные ID');
  const sceneById = new Map(source.scenes.map((s) => [s.id, s])), blockById = new Map(source.blocks.map((b) => [b.id, b]));
  const project = createProject({title: source.title, language: source.language});
  Object.assign(project, patchProject(project, {options: source.options, models: source.models, captionStyle: source.captionStyle}));
  // Validate the relationships and all editable values through the ordinary domain path.
  const skeleton = importRows({...project, options: {...project.options, voiceoverSkipped: true}}, source.frames.map((frame) => {
    ensure(sceneById.has(frame.sceneId) && blockById.get(frame.blockId)?.sceneId === frame.sceneId, 'Неверные связи элементов');
    const block = blockById.get(frame.blockId);
    return {shotId: frame.shotId, sceneKey: frame.sceneId, narration: block.text, visual: frame.visualDescription, prompt: frame.imagePrompt};
  }));
  ensure(skeleton.frames.length === source.frames.length, 'Не удалось восстановить кадры');
  const restored = {...project, scenes: source.scenes.map((s) => ({id: s.id, title: '', approved: false, voiceoverSkipped: false})), blocks: source.blocks.map((b) => ({id: b.id, sceneId: b.sceneId, text: '', status: 'draft', audio: null})), frames: source.frames.map((f) => ({id: f.id, sceneId: f.sceneId, blockId: f.blockId, shotId: f.shotId, visualDescription: '', imagePrompt: '', motionPrompt: '', camera: 'static', durationWeight: 1, approved: false, image: null, video: null}))};
  let result = patchProject(restored, {scenes: source.scenes.map(({id: sceneId, title, approved, voiceoverSkipped}) => ({id: sceneId, title, approved, voiceoverSkipped})), blocks: source.blocks.map(({id: blockId, text}) => ({id: blockId, text})), frames: source.frames.map(({id: frameId, visualDescription, imagePrompt, motionPrompt, camera, durationWeight, approved}) => ({id: frameId, visualDescription, imagePrompt, motionPrompt, camera, durationWeight, approved}))});
  try {
    async function copy(asset, kind) {
      if (!asset) return null;
      ensure(typeof asset.path === 'string' && asset.path.startsWith(`${source.id}/`), 'Неверный путь артефакта');
      const relative = asset.path.slice(source.id.length + 1);
      ensure(/^(audio|images|clips|renders|subtitles)\/[\w.-]+$/.test(relative), 'Неверный путь артефакта');
      const entry = zip.file(relative); if (!entry) { addLog(result, 'warning', `Отсутствует файл ${relative}`); return null; }
      const target = `${result.id}/${relative}`, file = within(store.projectsDir, target);
      atomic(file, await entry.async('nodebuffer'));
      const metadata = await validateMedia(file, kind);
      return {path: target, duration: kind === 'image' ? null : metadata.duration, provider: 'upload', createdAt: now()};
    }
    for (const block of result.blocks) { block.audio = await copy(blockById.get(block.id).audio, 'audio'); block.status = block.audio ? 'ready' : 'draft'; }
    for (const frame of result.frames) { const original = source.frames.find((f) => f.id === frame.id); frame.image = await copy(original.image, 'image'); frame.video = await copy(original.video, 'video'); }
    for (const [key, value] of Object.entries(source.uploads || {})) { ensure(key === 'project' || sceneById.has(key), 'Неверный ID загруженной озвучки'); const asset = await copy(value, 'audio'); if (asset) result.uploads[key] = asset; }
    result.id = project.id; result.createdAt = now(); result.updatedAt = now(); result.revision = 1;
    addLog(result, 'success', 'Восстановлено из архива. Субтитры и итоговая сборка пересоздаются из проверенных медиа.');
    return store.put(result, null);
  } catch (error) { rmSync(within(store.projectsDir, result.id), {recursive: true, force: true}); throw error; }
}
