import path from 'node:path';
import {mkdtemp, mkdir, copyFile, rm, rename} from 'node:fs/promises';
import {existsSync, statSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition, makeCancelSignal} from '@remotion/renderer';
import {appDir} from './config.mjs';
import {within, ensure, atomic, hash, now} from './core.mjs';
import {buildTimeline} from './timeline.mjs';
import {probe} from './media.mjs';

export function renderService(store, {browserExecutable} = {}) {
  let studio;
  async function render(project, signal, onProgress = () => {}) {
    const timeline = buildTimeline(project), workspace = await mkdtemp(path.join(store.root, 'render-'));
    const fingerprint = hash(timeline), publicDir = path.join(workspace, 'public');
    const final = within(store.projectsDir, `${project.id}/renders/${fingerprint.slice(0, 16)}.mp4`), temporary = `${final}.tmp.mp4`;
    try {
      await mkdir(publicDir);
      const mapping = new Map();
      for (const entry of [...timeline.visuals, ...timeline.audio]) {
        const source = within(store.projectsDir, entry.path); ensure(existsSync(source), 'Отсутствует файл проекта', 409);
        if (!mapping.has(entry.path)) {
          const filename = `${hash(entry.path)}${path.extname(entry.path)}`; mapping.set(entry.path, filename);
          await copyFile(source, path.join(publicDir, filename));
        }
        entry.path = mapping.get(entry.path);
      }
      signal.throwIfAborted();
      const serveUrl = await bundle({entryPoint: path.join(appDir, 'remotion/index.tsx'), publicDir, enableCaching: true});
      const inputProps = {timeline};
      const composition = await selectComposition({serveUrl, id: 'FinalVideo', inputProps, browserExecutable});
      const cancel = makeCancelSignal(), abort = () => cancel.cancel(); signal.addEventListener('abort', abort, {once: true});
      try {
        signal.throwIfAborted(); await mkdir(path.dirname(final), {recursive: true});
        await renderMedia({composition, serveUrl, codec: 'h264', audioCodec: 'aac', enforceAudioTrack: true, outputLocation: temporary, inputProps, pixelFormat: 'yuv420p', browserExecutable, cancelSignal: cancel.cancelSignal, concurrency: 2, onProgress: ({progress}) => onProgress(progress)});
      } finally { signal.removeEventListener('abort', abort); }
      signal.throwIfAborted();
      const info = await probe(temporary, signal), video = info.streams.find((stream) => stream.codec_type === 'video'), audio = info.streams.find((stream) => stream.codec_type === 'audio');
      ensure(video?.codec_name === 'h264' && video.width === 1920 && video.height === 1080 && video.r_frame_rate === `${timeline.fps}/1` && audio?.codec_name === 'aac' && Math.abs(info.duration - timeline.durationInFrames / timeline.fps) <= 0.12 && statSync(temporary).size > 1000, 'Результат рендера не прошёл проверку', 422);
      await rename(temporary, final);
      const manifest = {version: '0.1.0', projectId: project.id, revision: project.revision, createdAt: now(), inputHash: fingerprint, timeline: buildTimeline(project), duration: info.duration, codecs: ['h264','aac']};
      atomic(`${final}.json`, JSON.stringify(manifest, null, 2));
      return {path: path.relative(store.projectsDir, final).split(path.sep).join('/'), duration: info.duration, inputHash: fingerprint, createdAt: now(), manifestPath: path.relative(store.projectsDir, `${final}.json`).split(path.sep).join('/')};
    } finally { await rm(workspace, {recursive: true, force: true}); await rm(temporary, {force: true}); }
  }
  return {render,
    openStudio() { if (!studio) { studio = spawn(process.execPath, [path.join(appDir, 'node_modules/@remotion/cli/remotion-cli.js'), 'studio', 'remotion/index.tsx', '--port=8788', '--host=127.0.0.1'], {cwd: appDir, windowsHide: true, stdio: 'ignore'}); studio.on('exit', () => { studio = null; }); studio.on('error', () => { studio = null; }); } return 'http://127.0.0.1:8788'; },
    close() { studio?.kill(); },
  };
}
