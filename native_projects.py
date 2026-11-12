"""Local SQLite projects and durable provider checkpoints for the desktop app."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

OPTION_FIELDS = {'audio', 'source', 'script', 'tts', 'voice', 'providers', 'mode', 'use_refs', 'subtitles',
                 'motion', 'hd', 'qa', 'speed', 'video_first', 'style', 'custom_style', 'render_fps', 'rewrite', 'storyboard_id', 'studio'}
REWRITE_FIELDS = {'on', 'montage', 'analyze_video', 'source', 'url', 'text', 'engine', 'words', 'language'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def safe_options(options):
    if not isinstance(options, dict):
        raise ValueError('Некорректные настройки проекта')
    result = {key: value for key, value in options.items() if key in OPTION_FIELDS}
    if 'rewrite' in result:
        if not isinstance(result['rewrite'], dict):
            raise ValueError('Некорректные настройки рерайта')
        result['rewrite'] = {key: value for key, value in result['rewrite'].items() if key in REWRITE_FIELDS}
    if 'studio' in result:
        if not isinstance(result['studio'],dict):raise ValueError('Некорректные настройки студии')
        import studio
        result['studio']=studio.settings(result['studio'])
    if len(json.dumps(result, ensure_ascii=False)) > 2_000_000:
        raise ValueError('Проект слишком большой')
    return result


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'projects.sqlite'
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload))) STRICT;
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,payload TEXT NOT NULL CHECK(json_valid(payload))) STRICT;
            ''')
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(1,?)', (timestamp(),))
        self.backup()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=15000')
            with db:
                yield db
        finally:
            db.close()

    def backup(self):
        directory = self.root / 'project_backups'
        directory.mkdir(exist_ok=True)
        target = directory / (timestamp()[:10] + '.sqlite')
        if not target.exists():
            with self.connection() as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Повреждена база проектов SQLite')
                temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                copy = sqlite3.connect(temporary)
                try:
                    db.backup(copy)
                    if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('Ошибка резервной копии проектов')
                finally:
                    copy.close()
                temporary.replace(target)
        for old in sorted(directory.glob('*.sqlite'), reverse=True)[7:]:
            old.unlink()

    def list(self):
        with self.connection() as db:
            return [dict(id=row[0], title=row[1], updated_at=row[2], revision=row[3]) for row in db.execute('SELECT id,title,updated_at,revision FROM projects ORDER BY updated_at DESC')]

    def get(self, project_id):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise ValueError('Проект не найден')
        return json.loads(row[0])

    def save(self, title, options, project_id=None, revision=None):
        title = str(title).strip()
        if not title or len(title) > 200:
            raise ValueError('Название должно быть от 1 до 200 символов')
        options = safe_options(options)
        project_id = project_id or uuid.uuid4().hex
        if len(project_id) != 32 or any(char not in '0123456789abcdef' for char in project_id):
            raise ValueError('Неверный ID проекта')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM projects WHERE id=?', (project_id,)).fetchone()
            if row and row[0] != revision:
                raise ValueError('Проект изменился. Откройте его заново перед сохранением.')
            if row is None and revision is not None:
                raise ValueError('Проект удалён')
            payload = {'id': project_id, 'title': title, 'revision': row[0] + 1 if row else 1, 'updated_at': timestamp(), 'options': options}
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,updated_at=excluded.updated_at,revision=excluded.revision,payload=excluded.payload',
                       (project_id, title, payload['updated_at'], payload['revision'], json.dumps(payload, ensure_ascii=False)))
        return payload

    def operation(self, key, value=None):
        with self.connection() as db:
            if value is not None:
                db.execute('INSERT INTO operations VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (key, json.dumps(value)))
                return value
            row = db.execute('SELECT payload FROM operations WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def delete(self, project_id):
        with self.connection() as db:
            db.execute('DELETE FROM projects WHERE id=?', (project_id,))
