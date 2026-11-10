import {spawn} from 'node:child_process';
import ffmpegStatic from 'ffmpeg-static';
import ffprobeStatic from 'ffprobe-static';
import {readFile, rm} from 'node:fs/promises';
import {ensure, AppError} from './core.mjs';

export const ffmpeg = process.env.FFMPEG_PATH || ffmpegStatic;
export const ffprobe = process.env.FFPROBE_PATH || ffprobeStatic.path;
export function processFile(binary, args, {signal, timeout = 120000, limit = 4 * 1024 * 1024} = {}) {
  return new Promise((resolve, reject) => {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(new DOMException('Процесс превысил время ожидания', 'TimeoutError')), timeout);
    const combined = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
    const child = spawn(binary, args, {windowsHide: true, shell: false, signal: combined});
    const chunks = []; let size = 0, stderr = '', failed = false;
    const fail = (error) => { if (!failed) { failed = true; clearTimeout(timer); reject(error); } };
    child.stdout.on('data', (chunk) => { size += chunk.length; if (size > limit) { child.kill(); fail(new AppError(422, 'Слишком большой вывод процесса')); } else chunks.push(chunk); });
    child.stderr.on('data', (chunk) => { stderr = (stderr + chunk.toString()).slice(-8000); });
    child.on('error', fail);
    child.on('close', (code) => {
      clearTimeout(timer); if (failed) return;
      if (combined.aborted) fail(combined.reason);
      else if (code !== 0) fail(new AppError(422, `Медиа не удалось обработать (код ${code})`, {reason: stderr.replace(/(?:[A-Za-z]:)?[/\\][^\s]+/g, '[файл]').slice(-800)}));
      else resolve(Buffer.concat(chunks));
    });
  });
}
export async function probe(file, signal) {
  const result = JSON.parse((await processFile(ffprobe, ['-v', 'error', '-show_format', '-show_streams', '-of', 'json', file], {signal, timeout: 30000})).toString());
  const duration = Number(result.format?.duration || result.streams?.find((stream) => stream.duration)?.duration);
  return {...result, duration};
}
export async function validateMedia(file, kind, signal) {
  const result = await probe(file, signal);
  ensure(result.streams?.some((stream) => stream.codec_type === (kind === 'audio' ? 'audio' : 'video')), `Файл не содержит ${kind}`, 422);
  if (kind !== 'image') ensure(Number.isFinite(result.duration) && result.duration > 0 && result.duration <= 24 * 3600, 'Недопустимая длительность медиа', 422);
  return result;
}
export async function normalizeClip(source, target, duration, signal, fps = 30) {
  ensure([30,60].includes(fps), 'Неверный FPS');
  ensure(Number.isFinite(duration) && duration > 0 && duration <= 3600, 'Неверная длительность клипа');
  await processFile(ffmpeg, ['-y', '-i', source, '-an', '-vf', `scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps=${fps},tpad=stop_mode=clone:stop_duration=3600`, '-t', String(duration), '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', target], {signal, timeout: 20 * 60000});
  await validateMedia(target, 'video', signal);
}
export async function pcm(file, temporary, signal) {
  try {
    await processFile(ffmpeg, ['-y', '-i', file, '-ac', '1', '-ar', '16000', '-f', 'f32le', temporary], {signal, timeout: 600000});
    const buffer = await readFile(temporary); ensure(buffer.length <= 16000 * 4 * 3600, 'Для локального Whisper разделите аудио на части до часа', 422);
    return new Float32Array(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
  } finally { await rm(temporary, {force: true}); }
}
