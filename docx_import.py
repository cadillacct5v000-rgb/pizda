"""OOXML tables imported into the desktop storyboard, preserving shot IDs."""
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET
import storyboard

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def read_rows(path, prompts=False):
    path = Path(path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('DOCX больше 20 МБ')
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 5000:
                raise ValueError('Слишком много файлов в DOCX')
            info = archive.getinfo('word/document.xml')
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError('XML документа слишком большой')
            xml = archive.read(info)
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ValueError('Небезопасный XML')
        root = ET.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError('Повреждён DOCX или отсутствует таблица') from error
    rows, warnings, seen = [], [], set()
    for index, row in enumerate(root.iter(W + 'tr'), 1):
        cells = ['\n'.join(''.join((node.text or '') if node.tag == W + 't' else ' ' if node.tag in (W + 'br', W + 'tab') else '' for node in p.iter()).strip() for p in cell.findall(W + 'p')).strip() for cell in row.findall(W + 'tc')]
        match = re.search(r'\bS(\d{1,4})\.(\d{1,4})\b', cells[0] if cells else '', re.I)
        if not match:
            if any(cells):
                warnings.append(f'Строка {index}: нет shot ID, пропущена')
            continue
        shot_id = f'S{int(match[1]):02d}.{int(match[2]):02d}'
        if shot_id in seen:
            raise ValueError(f'Строка {index}: повтор ID {shot_id}')
        seen.add(shot_id)
        rows.append({'shot_id': shot_id, 'narration': '' if prompts else cells[1] if len(cells) > 1 else '',
                     'prompt': '\n'.join(cells[1:]) if prompts else cells[2] if len(cells) > 2 else ''})
    if not rows or len(rows) > 1000:
        raise ValueError('Нужна таблица с ID S01.01, текстом во втором столбце и визуалом в третьем; не более 1000 кадров')
    return rows, warnings


def import_board(script_file, prompts_file=None):
    source, warnings = read_rows(script_file)
    if prompts_file:
        extra, notes = read_rows(prompts_file, True)
        mapping = {row['shot_id']: row['prompt'] for row in extra}
        warnings.extend(notes)
        for row in source:
            if row['shot_id'] in mapping:
                row['prompt'] = mapping.pop(row['shot_id'])
            else:
                warnings.append(f"Нет отдельного промпта для {row['shot_id']}")
        warnings.extend(f'Неизвестный shot ID: {shot_id}' for shot_id in mapping)
    rows = []
    for row in source:
        # Empty narration cannot be speech-aligned independently. Keep its visual instructions on the preceding plan.
        if not row['narration'].strip():
            if not rows:
                raise ValueError(f"Первый кадр {row['shot_id']} не содержит текста озвучки")
            rows[-1]['prompt'] += '\n' + row['shot_id'] + ': ' + row['prompt']
            warnings.append(f"{row['shot_id']}: пустая озвучка, визуальные указания добавлены к предыдущему плану")
            continue
        if not row['prompt'].strip():
            raise ValueError(f"Заполните визуальное описание для {row['shot_id']}")
        rows.append({'id': f'plan-{len(rows):05d}', 'narration': row['narration'].strip(), 'source_shot': None,
                     'confidence': 'manual', 'reason': 'DOCX ' + row['shot_id'], 'prompt': row['prompt'].strip(),
                     'motion': 'static camera', 'duration': None, 'take': 0})
    profile = {'visual_style': {'description': 'User-authored DOCX storyboard'}, 'montage': {'shots': []}}
    return storyboard.seal({'version': storyboard.VERSION, 'script': '\n\n'.join(row['narration'] for row in rows), 'profile': profile, 'rows': rows}), warnings
