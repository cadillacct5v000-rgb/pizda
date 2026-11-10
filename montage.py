"""Measure source cuts, describe temporal shot samples, and transfer a timeline.

Hard cuts are measured with FFmpeg. Camera/action and gradual transitions are
estimated by a vision model from ordered frames, not exact motion tracking.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

import netutil
from media_tools import ffmpeg_bin, run_media

VERSION = 1
SCENE_THRESHOLD = 0.30
MIN_SHOT = 0.16


def detect_shots(video_path, duration):
    netutil.check_cancel()
    result = run_media([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(video_path),
                        "-vf", f"scale=320:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo",
                        "-an", "-f", "null", "-"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Не удалось измерить склейки исходника: " + result.stderr.decode(errors="replace")[-300:])
    cuts = sorted(set(float(t) for t in re.findall(r"pts_time:([\d.]+)", result.stderr.decode(errors="replace"))))
    boundaries = [0.0]
    for cut in cuts:
        if cut - boundaries[-1] >= MIN_SHOT and duration - cut >= MIN_SHOT:
            boundaries.append(cut)
    boundaries.append(duration)
    return [{"index": index, "start": start, "end": end, "duration": end - start}
            for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]))]


def analyse(video_path, directory, duration, engine, key, model, progress=None, batch_size=6):
    import source_analysis as sa
    if type(batch_size) is not int or not 1 <= batch_size <= 6:
        raise ValueError('Размер пакета анализа: от 1 до 6 планов')
    directory = Path(directory) / "montage"
    directory.mkdir(exist_ok=True)
    shots = detect_shots(video_path, duration)
    identities = {shot['index']: {'version': VERSION, 'shot': shot, 'engine': engine, 'model': model} for shot in shots}
    analysed = {}

    def validate_description(response):
        for field in ('description', 'redesign_prompt', 'motion'):
            if not isinstance(response.get(field), str) or not response[field].strip():
                raise sa.SourceAnalysisError(f'Не получено описание плана: {field}.')

    def restore(record):
        try:
            item = record['shot']
            index = item['index']
            if type(index) is int and index not in analysed and record.get('identity') == identities.get(index) and Path(item['reference_frame']).is_file():
                if any(item.get(field) != identities[index]['shot'][field] for field in ('start', 'end', 'duration')):
                    return
                validate_description(item)
                analysed[index] = item
        except (KeyError, TypeError, ValueError, sa.SourceAnalysisError):
            return

    # Preserve individual results produced by earlier versions.
    for shot in shots:
        netutil.check_cancel()
        try:
            restore(json.loads((directory / f"shot_{shot['index']:05d}.json").read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    # A batch checkpoint is committed before individual files, so an interrupted materialization never rebills the batch.
    for checkpoint in directory.glob('batch_*_shots.json'):
        netutil.check_cancel()
        try:
            for record in json.loads(checkpoint.read_text(encoding='utf-8')).get('records', []):
                restore(record)
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    pending = [shot for shot in shots if shot['index'] not in analysed]
    batches = math.ceil(len(pending) / batch_size)
    if progress:
        progress(f'монтаж: найдено {len(shots)} планов; сохранено {len(analysed)}; осталось {batches} пакетов, до {batch_size} планов в запросе')
    for offset in range(0, len(pending), batch_size):
        netutil.check_cancel()
        batch = pending[offset:offset + batch_size]
        indexes = [shot['index'] for shot in batch]
        if progress:
            progress(f"монтаж: пакет {offset // batch_size + 1}/{batches}, планы {', '.join(str(index + 1) for index in indexes)}; по 3 кадра на план")
        frames, timestamps, references, manifest = [], [], {}, []
        for row, shot in enumerate(batch, 1):
            margin = min(0.08, shot['duration'] * 0.1)
            times = [shot['start'] + margin + (shot['duration'] - 2 * margin) * fraction for fraction in (0., .5, 1.)]
            samples = sa.extract_frames(video_path, directory / f"shot_{shot['index']:05d}", times)
            frames.extend(samples)
            timestamps.extend(times)
            references[shot['index']] = str(samples[1].resolve())
            manifest.append({'row': row, 'index': shot['index'], 'start': shot['start'], 'end': shot['end']})
        batch_id = hashlib.sha256(json.dumps([identities[index] for index in indexes], sort_keys=True).encode()).hexdigest()[:24]
        sheet = directory / f'batch_{batch_id}.jpg'
        sa._contact_sheet(frames, timestamps, sheet)
        prompt = (
            'Analyse EVERY row of this contact sheet. Each row contains THREE chronological frames INSIDE ONE shot. '
            'The three columns are early, middle and late in that same shot, not three separate shots. '
            'Rows correspond exactly to the following manifest. Return each original integer index, never assign by position. '
            'For EACH shot describe visible subjects, composition, palette, materials, lighting, graphics and action. '
            'Infer camera/action only when differences between its three frames support that; otherwise static/unknown. '
            'Provide an English prompt for NEW footage with a small redesign: different details/props, same broad aesthetic '
            'and composition. Preserve narrative meaning, no copied logos or text. Do not invent sound or transitions. '
            'Keep each description to 40-80 words, redesign_prompt to 60-100 words and motion to 20-35 words. '
            'Return JSON {"shots":[{"index":0,"description":"Russian description","redesign_prompt":"English prompt",'
            '"motion":"English motion or static","motion_confidence":"low|medium|high"}]}. '
            'Return exactly one complete object per manifest index. Reference manifest JSON:\n' + json.dumps(manifest)
        )

        def validate_batch(response):
            entries = response.get('shots') if isinstance(response, dict) else None
            if not isinstance(entries, list) or len(entries) != len(indexes):
                raise sa.SourceAnalysisError('Модель вернула неполный пакет планов. Готовые пакеты сохранены.')
            seen = set()
            for entry in entries:
                index = entry.get('index') if isinstance(entry, dict) else None
                if type(index) is not int or index not in indexes or index in seen:
                    raise sa.SourceAnalysisError('Модель перепутала ID планов в пакете. Результат не применён.')
                seen.add(index)
                validate_description(entry)

        response = sa.cached_query(directory, engine, key, model, prompt, sheet, validate=validate_batch, progress=progress)
        descriptions = {entry['index']: entry for entry in response['shots']}
        records = []
        for shot in batch:
            description = descriptions[shot['index']]
            item = {**shot, **{field: description[field] for field in ('description', 'redesign_prompt', 'motion')},
                    'motion_confidence': description.get('motion_confidence') if description.get('motion_confidence') in ('low', 'medium', 'high') else 'low',
                    'reference_frame': references[shot['index']], 'transition_in': {'type': 'cut', 'duration': 0.}}
            records.append({'identity': identities[shot['index']], 'shot': item})
        sa._atomic_json(directory / f'batch_{batch_id}_shots.json', {'records': records})
        for record in records:
            item = record['shot']
            sa._atomic_json(directory / f"shot_{item['index']:05d}.json", record)
            analysed[item['index']] = item
    return {'version': VERSION, 'duration': duration, 'shots': [analysed[shot['index']] for shot in shots],
            'analysis': {'method': 'batched_temporal_contact_sheets', 'shots_per_request': batch_size, 'frames_per_shot': 3},
            'cut_detection': {'method': 'ffmpeg_scene_score', 'threshold': SCENE_THRESHOLD, 'minimum_shot_seconds': MIN_SHOT},
            'limitations': ['Автодетектор склеек может пропустить плавные переходы или принять вспышку за склейку.',
                           'Движение оценивается по трём кадрам плана; траектория камеры не измеряется.',
                           'Музыка, звуковые акценты и анимация надписей не переносятся.']}


def map_scenes(profile, words, total_duration, fps=25, max_clip_seconds=10):
    """Preserve source cut positions relative to the new narration duration.

    Long shots are generated in sequential pieces with the same prompt. This
    cannot guarantee seamless action at their internal joins.
    """
    montage = profile["montage"]
    source_duration = float(montage["duration"])
    if not math.isfinite(source_duration) or source_duration <= 0 or total_duration <= 0:
        raise ValueError("Invalid montage duration")
    scenes, previous_end = [], 0.0
    shots = montage["shots"]
    for shot in shots:
        target_end = round(float(shot["end"]) / source_duration * total_duration * fps) / fps
        if shot is shots[-1]:
            target_end = total_duration
        duration = target_end - previous_end
        if duration < 1 / fps:
            continue
        count = max(1, math.ceil(duration / max_clip_seconds))
        for part in range(count):
            start = previous_end + round(duration * part / count * fps) / fps
            end = target_end if part == count - 1 else previous_end + round(duration * (part + 1) / count * fps) / fps
            text = " ".join(w["text"] for w in words if start <= (w["start"] + w["end"]) / 2 < end)
            if not text and words:
                nearest = min(words, key=lambda w: abs(w["start"] - start))
                text = nearest["text"]
            scenes.append({"index": len(scenes), "start": start, "end": end, "duration": end - start, "text": text,
                           "source_shot": shot["index"], "source_visual": shot["description"],
                           "source_redesign": shot["redesign_prompt"], "source_motion": shot["motion"],
                           "source_reference": shot["reference_frame"],
                           "transition_in": shot["transition_in"] if part == 0 else {"type": "cut", "duration": 0.0}})
        previous_end = target_end
    if not scenes:
        raise ValueError("Source montage contains no usable shots")
    return scenes
