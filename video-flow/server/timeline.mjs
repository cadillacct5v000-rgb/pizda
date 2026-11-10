import {ensure, AppError} from './core.mjs';

export const FPS = 30;
export const secondsToFrames = (seconds, fps = FPS) => Math.max(1, Math.round(seconds * fps));
export function allocateFrames(total, weights) {
  ensure(Number.isInteger(total) && total >= weights.length && weights.length > 0, 'Недостаточная длительность для числа кадров', 409);
  ensure(weights.every((weight) => Number.isFinite(weight) && weight > 0), 'Неверные веса кадров');
  const available = total - weights.length, sum = weights.reduce((a, b) => a + b, 0);
  const quotas = weights.map((weight) => weight / sum * available), result = quotas.map((value) => 1 + Math.floor(value));
  let remaining = total - result.reduce((a, b) => a + b, 0);
  for (const entry of quotas.map((value, index) => ({index, fraction: value % 1})).sort((a, b) => b.fraction - a.fraction || a.index - b.index)) {
    if (!remaining) break; result[entry.index]++; remaining--;
  }
  return result;
}
export function buildTimeline(project, requireVisuals = true) {
  const fps = project.options.fps || FPS; ensure([30,60].includes(fps), 'Неверный FPS');
  const visuals = [], audio = [], cues = [], missing = [];
  const allBlocks = project.blocks;
  let cursor = 0, timingFallback = false;
  function group(frames, blocks, source, silent) {
    if (!frames.length) return;
    if (silent) {
      for (const frame of frames) place(frame, secondsToFrames(project.options.silentFrameDuration, fps));
      return;
    }
    if (!source) { missing.push(...blocks.map((b) => `Озвучка: ${b.id}`)); return; }
    const total = secondsToFrames(source.duration, fps), origin = cursor;
    audio.push({from: origin, durationInFrames: total, path: source.path});
    const textWeights = blocks.map((b) => Math.max(1, b.text.trim().split(/\s+/).length));
    const boundaries = source.timings;
    const aligned = boundaries?.length === blocks.length && boundaries.every((t, index) => t.confidence >= 0.6 && t.blockId === blocks[index].id && t.end > t.start && t.start >= (index ? boundaries[index - 1].end : 0) && t.end <= source.duration + 0.01);
    if (aligned) {
      // Audio is never stretched. Cuts follow measured narration boundaries; pauses remain visual.
      let previous = 0;
      blocks.forEach((block, index) => {
        const related = frames.filter((f) => f.blockId === block.id), end = index === blocks.length - 1 ? total : Math.round(boundaries[index].end * fps), target = end - previous;
        ensure(related.length && target >= related.length, 'Недостаточно времени для кадров блока; проверьте alignment', 409);
        const durations = allocateFrames(target, related.map((f) => f.durationWeight)); related.forEach((frame, position) => place(frame, durations[position])); previous = end;
      });
      boundaries.forEach((t, index) => cues.push({start: origin / fps + t.start, end: origin / fps + t.end, text: blocks[index].text, confidence: t.confidence, source: 'alignment'}));
    } else {
      const durations = allocateFrames(total, frames.map((f) => f.durationWeight)); frames.forEach((frame, index) => place(frame, durations[index]));
      timingFallback = true;
      const parts = allocateFrames(total, textWeights); let offset = origin;
      parts.forEach((part, index) => { cues.push({start: offset / fps, end: (offset + part) / fps, text: blocks[index].text, confidence: 0, source: 'estimated'}); offset += part; });
    }
  }
  function place(frame, durationInFrames) {
    const asset = project.options.animationEnabled ? frame.video : frame.image;
    if (requireVisuals && !asset) missing.push(`${project.options.animationEnabled ? 'Видео' : 'Изображение'}: ${frame.shotId}`);
    visuals.push({id: frame.id, shotId: frame.shotId, from: cursor, durationInFrames, kind: project.options.animationEnabled ? 'video' : 'image', path: asset?.path || null, camera: frame.camera}); cursor += durationInFrames;
  }
  if (project.options.voiceoverSkipped) group(project.frames, allBlocks, null, true);
  else if (project.uploads.project) group(project.frames, allBlocks, project.uploads.project, false);
  else for (const scene of project.scenes) {
    const frames = project.frames.filter((f) => f.sceneId === scene.id), blocks = allBlocks.filter((b) => b.sceneId === scene.id);
    if (scene.voiceoverSkipped) group(frames, blocks, null, true);
    else if (project.uploads[scene.id]) group(frames, blocks, project.uploads[scene.id], false);
    else for (const block of blocks) group(frames.filter((f) => f.blockId === block.id), [block], block.audio, false);
  }
  if (missing.length) throw new AppError(409, 'Проект пока не готов к сборке', {missing});
  ensure(cursor > 0, 'В проекте нет кадров', 409);
  return {fps, width: 1920, height: 1080, durationInFrames: cursor, visuals, audio, cues: project.options.subtitlesEnabled ? cues : [], timingFallback, captionStyle: project.captionStyle};
}
export function timecode(seconds, vtt = false) {
  const milliseconds = Math.max(0, Math.round(seconds * 1000)), hour = Math.floor(milliseconds / 3600000), minute = Math.floor(milliseconds / 60000) % 60, second = Math.floor(milliseconds / 1000) % 60;
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}${vtt ? '.' : ','}${String(milliseconds % 1000).padStart(3, '0')}`;
}
export function subtitles(cues, vtt = false) {
  let end = 0;
  return (vtt ? 'WEBVTT\n\n' : '') + cues.filter((cue) => cue.text.trim()).map((cue, index) => {
    const start = Math.max(end, cue.start); end = Math.max(start + 0.001, cue.end);
    const cleaned = cue.text.replace(/[<>]/g, '').replace(/-->/g, '→').replace(/\s+/g, ' ').trim();
    const lines = cleaned.match(/.{1,44}(?:\s|$)|\S{1,44}/g) || [cleaned];
    return `${index + 1}\n${timecode(start, vtt)} --> ${timecode(end, vtt)}\n${lines.map((line) => line.trim()).join('\n')}\n`;
  }).join('\n');
}
