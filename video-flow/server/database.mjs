import Database from 'better-sqlite3';
import {mkdirSync, readdirSync, rmSync, existsSync, readFileSync} from 'node:fs';
import path from 'node:path';
import {AppError, atomic, within, now, ensure} from './core.mjs';

export function openStore(root) {
  mkdirSync(root, {recursive: true});
  const projectsDir = path.join(root, 'projects'); mkdirSync(projectsDir, {recursive: true});
  const db = new Database(path.join(root, 'database.sqlite'));
  db.pragma('journal_mode = WAL'); db.pragma('synchronous = FULL'); db.pragma('foreign_keys = ON'); db.pragma('busy_timeout = 5000');
  const schema = db.transaction(() => {
    db.exec(`CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT;
      CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, title TEXT NOT NULL, lifecycle TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload))) STRICT;
      CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload))) STRICT;
      CREATE TABLE IF NOT EXISTS operations(key TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, status TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload))) STRICT;`);
    db.prepare('INSERT OR IGNORE INTO schema_migrations VALUES(1, ?)').run(now());
  }); schema();
  const integrity = () => { const value = db.pragma('integrity_check', {simple: true}); ensure(value === 'ok', `SQLite: ${value}`, 500); return value; };
  integrity();
  const list = () => db.prepare('SELECT payload FROM projects ORDER BY updated_at DESC').all().map((r) => JSON.parse(r.payload));
  const get = (projectId) => { const row = db.prepare('SELECT payload FROM projects WHERE id=?').get(projectId); if (!row) throw new AppError(404, 'Проект не найден'); return JSON.parse(row.payload); };
  const mirror = (project) => atomic(within(projectsDir, `${project.id}/manifest.json`), JSON.stringify(project, null, 2));
  const write = db.transaction((project, expected) => {
    const current = db.prepare('SELECT revision FROM projects WHERE id=?').get(project.id);
    if (current && current.revision !== expected) throw new AppError(409, 'Проект изменился. Обновите данные перед сохранением.', {revision: current.revision});
    if (!current && expected !== null) throw new AppError(404, 'Проект удалён');
    const saved = {...project, revision: current ? current.revision + 1 : 1, updatedAt: now()};
    db.prepare(`INSERT INTO projects VALUES (@id,@title,@lifecycle,@updatedAt,@revision,@payload) ON CONFLICT(id) DO UPDATE SET title=excluded.title,lifecycle=excluded.lifecycle,updated_at=excluded.updated_at,revision=excluded.revision,payload=excluded.payload`).run({...saved, payload: JSON.stringify(saved)});
    return saved;
  });
  let mirrorError = null;
  const put = (project, expected = project.revision) => {
    const saved = write(project, expected);
    // SQLite is authoritative. A disk-full mirror failure never reports a rolled-back DB write.
    try { mirror(saved); mirrorError = null; } catch (error) { mirrorError = error.code || 'manifest_write_failed'; }
    return saved;
  };
  // Migrate old manifests only into an empty database. Never replace newer SQLite state.
  if (!list().length) for (const entry of readdirSync(projectsDir, {withFileTypes: true})) {
    const file = path.join(projectsDir, entry.name, 'manifest.json');
    if (entry.isDirectory() && existsSync(file)) {
      const project = JSON.parse(readFileSync(file, 'utf8'));
      ensure(project.id === entry.name && Array.isArray(project.frames) && Number.isInteger(project.revision), 'Повреждён legacy manifest', 500);
      put(project, null);
    }
  }
  for (const project of list()) { try { mirror(project); } catch (error) { mirrorError = error.code; } }
  const operations = {
    get: (key) => { const row = db.prepare('SELECT payload FROM operations WHERE key=?').get(key); return row ? JSON.parse(row.payload) : null; },
    list: () => db.prepare('SELECT payload FROM operations').all().map((r) => JSON.parse(r.payload)),
    put: (op) => { db.prepare('INSERT INTO operations VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,payload=excluded.payload').run(op.key, op.projectId, op.status, now(), JSON.stringify(op)); return op; },
  };
  async function backup() {
    integrity(); const directory = path.join(root, 'backups'); mkdirSync(directory, {recursive: true});
    const file = path.join(directory, `${now().slice(0, 10)}.sqlite`);
    if (!existsSync(file)) { const temporary = `${file}.tmp`; await db.backup(temporary); const check = new Database(temporary, {readonly: true}); try { ensure(check.pragma('integrity_check', {simple: true}) === 'ok', 'Повреждена резервная копия', 500); } finally { check.close(); } const {renameSync} = await import('node:fs'); renameSync(temporary, file); }
    readdirSync(directory).filter((name) => /^\d{4}-\d{2}-\d{2}\.sqlite$/.test(name)).sort().reverse().slice(7).forEach((name) => rmSync(path.join(directory, name)));
    return file;
  }
  return {db, root, projectsDir, list, get, put, operations, integrity, backup, mirrorStatus: () => mirrorError,
    mutate(projectId, callback) { const current = get(projectId); return put(callback(current), current.revision); },
    delete(projectId) { get(projectId); db.prepare('DELETE FROM projects WHERE id=?').run(projectId); rmSync(within(projectsDir, projectId), {recursive: true, force: true}); },
    close() { db.close(); },
  };
}
