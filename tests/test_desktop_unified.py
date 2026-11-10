import json
from pathlib import Path
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest

from native_projects import ProjectStore
from docx_import import import_board, read_rows
from syntx_api import Syntx, SyntxError


def docx(path, rows):
    def cell(value):
        return '<w:tc><w:p><w:r><w:t>' + value + '</w:t></w:r></w:p></w:tc>'
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>' + ''.join('<w:tr>' + ''.join(cell(value) for value in row) + '</w:tr>' for row in rows) + '</w:tbl></w:body></w:document>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return path


def test_project_revision_backup_restore_and_no_keys(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.save('Ролик YouTube', {'script': 'Текст', 'providers': ['syntx'], 'render_fps': 60,
                                         'elevenlabs_api_key': 'must-not-persist', 'rewrite': {'url': 'https://youtu.be/123', 'api_key': 'must-not-persist'}})
    saved = store.save(project['title'], {'script': 'Новый текст'}, project['id'], project['revision'])
    assert saved['revision'] == 2
    with pytest.raises(ValueError, match='изменился'):
        store.save('Устарело', {}, project['id'], 1)
    assert ProjectStore(tmp_path).get(project['id'])['options']['script'] == 'Новый текст'
    assert 'must-not-persist' not in json.dumps(project)
    assert store.list()[0]['id'] == project['id']
    backup = next((tmp_path / 'project_backups').glob('*.sqlite'))
    db = sqlite3.connect(backup)
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    db.close()
    store.delete(project['id'])
    with pytest.raises(ValueError, match='не найден'):
        store.get(project['id'])


def test_docx_maps_shot_ids_not_positions_and_editable_board(tmp_path):
    script = docx(tmp_path / 'script.docx', [['ID', 'Текст', 'Визуал'], ['S01.01', 'Первый текст.', 'Лес'], ['S01.02', 'Второй текст.', 'Море']])
    prompts = docx(tmp_path / 'prompts.docx', [['S01.02', 'Sea'], ['S01.01', 'Forest']])
    board, warnings = import_board(script, prompts)
    assert [row['prompt'] for row in board['rows']] == ['Forest', 'Sea']
    assert board['script'] == 'Первый текст.\n\nВторой текст.'
    import storyboard
    edited = storyboard.edit(board, board['rows'])
    assert edited['id'] == board['id']
    assert warnings  # Header row was explicitly skipped.


def test_docx_empty_narration_is_diagnosed_not_silently_lost(tmp_path):
    board, warnings = import_board(docx(tmp_path / 'x.docx', [['S01.01', 'Речь', 'Forest'], ['S01.02', '', 'Lake']]))
    assert len(board['rows']) == 1
    assert 'Lake' in board['rows'][0]['prompt']
    assert any('пустая озвучка' in message for message in warnings)
    with pytest.raises(ValueError, match='повтор'):
        read_rows(docx(tmp_path / 'duplicates.docx', [['S01.01', 'a'], ['S01.01', 'b']]))


def test_docx_202_shots_and_unsafe_xml(tmp_path):
    rows, _ = read_rows(docx(tmp_path / 'large.docx', [[f'S01.{index:03}', f'Текст {index}', 'Промпт'] for index in range(1, 203)]))
    assert len(rows) == 202
    path = tmp_path / 'unsafe.docx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<!DOCTYPE x [<!ENTITY x "bad">]><x/>')
    with pytest.raises(ValueError, match='Небезопасный'):
        read_rows(path)


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class Session:
    def __init__(self, uncertain=False):
        self.headers = {}
        self.calls = []
        self.uncertain = uncertain

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith('/chats'):
            return Response({'id': 42, 'uuid': 'uuid-42'})
        if 'design/generate' in url or 'video/generate' in url:
            if self.uncertain:
                raise TimeoutError('network lost')
            return Response({'id': 73})
        if 'upload-files' in url:
            return Response({'files': [{'url': 'https://cdn.example.org/source.png'}]})
        return Response({'messages': [{'author_id': -1, 'message_object': [{'object_type': 'image', 'object_url': 'https://cdn.example.org/result.png', 'completed': True}]}]})


def test_syntx_persists_chat_then_paid_post_and_resume_never_recreates(tmp_path, monkeypatch):
    import syntx_api
    store, session = ProjectStore(tmp_path), Session()
    monkeypatch.setattr(syntx_api.requests, 'get', lambda *args, **kwargs: object())
    monkeypatch.setattr(syntx_api.netutil, 'save_response', lambda response, target: Path(target).write_bytes(b'fixture'))
    target = str(tmp_path / 'image.png')
    client = Syntx('secret-key', store=store, session=session, poll=0)
    assert client.generate('Forest', target).startswith('syntx:')
    wire = next(kwargs['json'] for method, url, kwargs in session.calls if 'design/generate' in url)
    assert wire['chat_uuid'] == 'uuid-42'
    assert wire['settings']['model_type'] == 'gpt-image-2'
    assert wire['settings']['aspect_ratio'] == '16:9'
    fresh = Session()
    Syntx('secret-key', store=ProjectStore(tmp_path), session=fresh, poll=0).generate('Forest', target)
    assert all(method == 'GET' for method, _, _ in fresh.calls)
    with store.connection() as db:
        assert 'secret-key' not in ''.join(row[0] for row in db.execute('SELECT payload FROM operations'))


def test_syntx_ambiguous_post_blocks_retry(tmp_path):
    session = Session(uncertain=True)
    client = Syntx('key', store=ProjectStore(tmp_path), session=session, poll=0)
    target = str(tmp_path / 'image.png')
    with pytest.raises(TimeoutError):
        client.generate('Forest', target)
    with pytest.raises(SyntxError, match='повтор заблокирован'):
        client.generate('Forest', target)
    assert len([url for method, url, _ in session.calls if 'design/generate' in url]) == 1


def test_desktop_api_saves_projects_and_imports_docx_without_network(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app, 'data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(app, 'load_cfg', lambda: {})
    monkeypatch.setattr(app, 'save_cfg', lambda config: None)
    file = docx(tmp_path / 'source.docx', [['S01.01', 'Текст речи.', 'Cinematic forest']])
    monkeypatch.setattr(app, 'WINDOW', SimpleNamespace(create_file_dialog=lambda *args, **kwargs: [str(file)]))
    monkeypatch.setattr(app, 'webview', SimpleNamespace(OPEN_DIALOG='open'))
    monkeypatch.setitem(app.STATE, 'running', False)
    monkeypatch.setitem(app.STATE, 'preparing', False)
    monkeypatch.setitem(app.STATE, 'queue_active', False)
    api = app.Api()
    imported = api.import_docx()
    assert imported['ok'], imported
    assert imported['board']['sources'] == []
    assert imported['board']['script'] == 'Текст речи.'
    saved = api.save_project('Фильм', {'script': 'Текст речи.', 'storyboard_id': imported['board']['id'], 'providers': ['syntx'], 'render_fps': 60})
    assert saved['ok']
    loaded = api.load_project(saved['project']['id'])
    assert loaded['ok']
    assert loaded['board']['rows'][0]['prompt'] == 'Cinematic forest'
    assert loaded['project']['options']['render_fps'] == 60
