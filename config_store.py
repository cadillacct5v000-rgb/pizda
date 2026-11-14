"""Atomic local settings with a last-known-good backup. Never silently erase keys."""
import json
import os
from pathlib import Path
import time
import uuid


def read(path):
    path=Path(path)
    if not path.exists():return {}
    failure=None
    for attempt in range(5):
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value,dict):raise ValueError('settings root must be an object')
            return value
        except PermissionError as error:
            failure=error
            if attempt<4:time.sleep(.1*(attempt+1))
        except (ValueError,OSError) as error:
            failure=error;break
    try:
        backup=json.loads(Path(str(path)+'.bak').read_text(encoding='utf-8'))
        if isinstance(backup,dict):return backup
    except (ValueError,OSError):pass
    raise ValueError('Не удалось прочитать config.json и его резервную копию. Файлы сохранены; восстановите настройки из копии перед продолжением.') from failure


def _atomic(path, data):
    path=Path(path);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        for attempt in range(10):
            try:os.replace(temporary,path);return
            except PermissionError:
                if attempt==9:raise
                time.sleep(.15*(attempt+1))
    finally:
        temporary.unlink(missing_ok=True)


def write(path, value):
    if not isinstance(value,dict):raise ValueError('Некорректные настройки.')
    data=json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')
    path=Path(path);backup=Path(str(path)+'.bak')
    if path.exists() and path.read_bytes()==data and backup.is_file() and backup.read_bytes()==data:return
    if path.exists():
        # Validate before replacing either file; a damaged main file must not poison the backup.
        previous=read(path)
        _atomic(backup,json.dumps(previous,ensure_ascii=False,indent=2).encode('utf-8'))
    _atomic(path,data)
    _atomic(backup,data)
