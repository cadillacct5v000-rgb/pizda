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


def analyse(video_path, directory, duration, engine, key, model, progress=None):
    import source_analysis as sa
    directory = Path(directory) / "montage"
    directory.mkdir(exist_ok=True)
    shots = detect_shots(video_path, duration)
    analysed = []
    def validate_description(response):
        for field in ("description", "redesign_prompt", "motion"):
            if not isinstance(response.get(field), str) or not response[field].strip():
                raise sa.SourceAnalysisError(f"Не получено описание плана: {field}.")
    for shot in shots:
        netutil.check_cancel()
        index = shot["index"]
        if progress:
            progress(f"монтаж: план {index + 1}/{len(shots)}, {shot['start']:.1f}–{shot['end']:.1f} с")
        identity = {"version": VERSION, "shot": shot, "engine": engine, "model": model}
        cache = directory / f"shot_{index:05d}.json"
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("identity") == identity and Path(cached["shot"]["reference_frame"]).is_file():
                analysed.append(cached["shot"])
                continue
        except (OSError, ValueError, KeyError, TypeError):
            pass
        margin = min(0.08, shot["duration"] * 0.1)
        times = [shot["start"] + margin + (shot["duration"] - 2 * margin) * f for f in (0.0, 0.5, 1.0)]
        frames = sa.extract_frames(video_path, directory / f"shot_{index:05d}", times)
        sheet = directory / f"shot_{index:05d}.jpg"
        sa._contact_sheet(frames, times, sheet)
        prompt = (
            "These three frames are chronological samples INSIDE ONE detected shot, not separate shots. "
            f"Source time {shot['start']:.3f}–{shot['end']:.3f} seconds. "
            "Describe the visible subjects, composition, palette, materials, light, graphics, and action. "
            "Infer camera/action only when changes between these frames support it; otherwise use static/unknown. "
            "Give an English prompt for NEW footage with a small visual redesign (different details/props, "
            "same broad aesthetic and composition); preserve narrative meaning, no copied logos or text. "
            "Do not invent audible events. Transition at the START cannot be inferred from these inside-shot frames: "
            "it is handled as a measured cut. Return JSON: "
            '{"description":"Russian visual description","redesign_prompt":"English new shot prompt",'
            '"motion":"English camera and subject motion, or static",'
            '"motion_confidence":"low|medium|high"}.')
        response = sa.cached_query(directory, engine, key, model, prompt, sheet, validate=validate_description, progress=progress)
        for field in ("description", "redesign_prompt", "motion"):
            if not isinstance(response.get(field), str) or not response[field].strip():
                raise sa.SourceAnalysisError(f"Не получено описание плана {index + 1}: {field}.")
        item = {**shot, **{k: response[k] for k in ("description", "redesign_prompt", "motion")},
                "motion_confidence": response.get("motion_confidence", "low"),
                "reference_frame": str(frames[1].resolve()),
                "transition_in": {"type": "cut", "duration": 0.0}}
        sa._atomic_json(cache, {"identity": identity, "shot": item})
        analysed.append(item)
    return {"version": VERSION, "duration": duration, "shots": analysed,
            "cut_detection": {"method": "ffmpeg_scene_score", "threshold": SCENE_THRESHOLD,
                              "minimum_shot_seconds": MIN_SHOT},
            "limitations": ["Автодетектор склеек может пропустить плавные переходы или принять вспышку за склейку.",
                            "Движение оценивается по трём кадрам плана; траектория камеры не измеряется.",
                            "Музыка, звуковые акценты и анимация надписей не переносятся."]}


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
