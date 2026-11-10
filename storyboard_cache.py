"""Reuse completed per-plan generations across preview/full runs and edits."""
import hashlib
import math
from pathlib import Path
import shutil

from PIL import Image

from storyboard import digest
from media_tools import ffmpeg_bin, run_media


def image_key(scene):
    reference = scene.get('source_reference')
    fingerprint = None
    if reference and Path(reference).is_file():
        fingerprint = hashlib.sha256(Path(reference).read_bytes()).hexdigest()
    return digest([scene.get('plan_id'), scene.get('prompt'), scene.get('characters'), fingerprint, scene.get('plan_take', 0)])


def video_key(scene, image):
    return digest([image_key(scene), hashlib.sha256(Path(image).read_bytes()).hexdigest(), scene.get('motion'),
                   max(3, min(10, math.ceil(scene['duration'])))])


def valid(path, kind):
    if not path.is_file() or not path.stat().st_size:
        return False
    if kind == 'image':
        try:
            with Image.open(path) as image:
                image.verify()
            return True
        except (OSError, ValueError):
            return False
    result = run_media([ffmpeg_bin('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', str(path)])
    return result.returncode == 0 and result.stdout.strip() == b'video'


def restore(scenes, job_dir, cache_dir):
    job_dir, cache_dir = Path(job_dir), Path(cache_dir)
    for scene in scenes:
        image = job_dir / 'images' / f"{scene['index']:04d}.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        cached_image = cache_dir / ('i_' + image_key(scene) + '.png')
        if not image.exists() and valid(cached_image, 'image'):
            shutil.copyfile(cached_image, image)
        if not image.exists():
            continue
        video = job_dir / 'videos' / f"{scene['index']:04d}.mp4"
        cached_video = cache_dir / ('v_' + video_key(scene, image) + '.mp4')
        video.parent.mkdir(parents=True, exist_ok=True)
        if not video.exists() and valid(cached_video, 'video'):
            shutil.copyfile(cached_video, video)


def publish(scenes, job_dir, cache_dir):
    job_dir, cache_dir = Path(job_dir), Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for scene in scenes:
        image = job_dir / 'images' / f"{scene['index']:04d}.png"
        if not valid(image, 'image'):
            continue
        for kind, source, destination in [
            ('image', image, cache_dir / ('i_' + image_key(scene) + '.png')),
            ('video', job_dir / 'videos' / f"{scene['index']:04d}.mp4", cache_dir / ('v_' + video_key(scene, image) + '.mp4'))]:
            if (destination.exists() and valid(destination, kind)) or not valid(source, kind):
                continue
            temporary = destination.with_suffix('.part')
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
