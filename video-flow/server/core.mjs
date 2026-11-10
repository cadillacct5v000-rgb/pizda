import {randomUUID, createHash} from 'node:crypto';
import {mkdirSync, writeFileSync, renameSync, rmSync, readFileSync, existsSync} from 'node:fs';
import path from 'node:path';

export class AppError extends Error {
  constructor(status, message, details) { super(message); this.status = status; this.details = details; }
}
export const ensure = (condition, message, status = 400) => { if (!condition) throw new AppError(status, message); };
export const hash = (value) => createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex');
export const id = () => randomUUID();
export const now = () => new Date().toISOString();
export function within(root, relative) {
  ensure(typeof relative === 'string' && !relative.includes('\0') && !relative.includes('\\') && !path.isAbsolute(relative), 'Недопустимый путь');
  const resolved = path.resolve(root, relative);
  ensure(resolved.startsWith(path.resolve(root) + path.sep), 'Путь выходит за пределы хранилища');
  return resolved;
}
export function atomic(file, data) {
  mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.${id()}.tmp`;
  try { writeFileSync(temporary, data, {mode: 0o600}); renameSync(temporary, file); }
  finally { rmSync(temporary, {force: true}); }
}
export function text(value, name, max = 20000, empty = false) {
  ensure(typeof value === 'string' && value.length <= max && (empty || value.trim()), `Некорректное поле: ${name}`);
  return value.trim();
}
export function number(value, name, minimum, maximum) {
  ensure(typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum, `Некорректное поле: ${name}`);
  return value;
}
export function decode64(value, maximum = 20 * 1024 * 1024) {
  ensure(typeof value === 'string' && value.length <= Math.ceil(maximum / 3) * 4 && /^[A-Za-z0-9+/]*={0,2}$/.test(value), 'Неверный или слишком большой base64');
  const buffer = Buffer.from(value, 'base64');
  ensure(buffer.length > 0 && buffer.length <= maximum && buffer.toString('base64') === value, 'Неверный base64');
  return buffer;
}
export function redact(value, secrets = []) {
  let result = String(value).replace(/(bearer\s+)[^\s"']+/gi, '$1[скрыто]').replace(/((?:api[_-]?key|token|authorization)["'\s:=]+)[^\s,"'}]+/gi, '$1[скрыто]');
  for (const secret of secrets) if (secret) result = result.split(secret).join('[скрыто]');
  return result.slice(0, 2000);
}
export function readJSON(file, fallback) { return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : fallback; }
