import dotenv from 'dotenv';
import path from 'node:path';
import {readFileSync, existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {atomic, ensure} from './core.mjs';

export const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export function configuration(environment = process.env, directory = appDir) {
  const regular = path.join(directory, '.env'), local = path.join(environment.VIDEO_FLOW_CONFIG_DIR || directory, '.env.local');
  const values = {...(existsSync(regular) ? dotenv.parse(readFileSync(regular)) : {}), ...(existsSync(local) ? dotenv.parse(readFileSync(local)) : {}), ...environment};
  const bounded = (key, fallback, min, max) => { const value = Number(values[key] ?? fallback); ensure(Number.isInteger(value) && value >= min && value <= max, `Проверьте настройку ${key}`); return value; };
  const providers = {};
  for (const [name, fallback] of Object.entries({lumean: 'https://api.lumean.app/api/public', syntx: 'https://api.syntx.ai', fastgen: 'https://api.fast-gen.ai', elevenlabs: 'https://api.elevenlabs.io'})) {
    const base = values[`${name.toUpperCase()}_BASE_URL`] || fallback;
    ensure(new URL(base).protocol === 'https:', `URL ${name} должен использовать HTTPS`);
    providers[name] = {base: base.replace(/\/+$/, ''), key: values[`${name.toUpperCase()}_API_KEY`] || ''};
  }
  return {
    directory, root: path.resolve(directory, values.DATA_DIR || '.runtime'), port: bounded('PORT', 8787, 1024, 65535), host: '127.0.0.1', providers,
    poll: bounded('POLL_INTERVAL_MS', 5000, 500, 60000), httpTimeout: bounded('HTTP_TIMEOUT_MS', 60000, 1000, 300000), jobTimeout: bounded('JOB_TIMEOUT_MS', 3600000, 1000, 86400000), whisperModel: values.WHISPER_MODEL || 'Xenova/whisper-tiny',
    concurrency: {voice: bounded('VOICE_CONCURRENCY', 1, 1, 1), image: bounded('IMAGE_CONCURRENCY', 1, 1, 2), video: bounded('VIDEO_CONCURRENCY', 2, 1, 2)},
    saveKey(name, key) {
      ensure(name in providers && typeof key === 'string' && key.length < 4096 && !/[\r\n\0]/.test(key), 'Некорректный ключ');
      const settings = existsSync(local) ? dotenv.parse(readFileSync(local)) : {};
      settings[`${name.toUpperCase()}_API_KEY`] = key;
      atomic(local, Object.entries(settings).map(([field, value]) => `${field}=${JSON.stringify(value)}\n`).join(''));
      providers[name].key = key;
    },
  };
}
