"""Immutable channel design versions and approved local artwork; no network calls."""
import copy
import hashlib
import json
from pathlib import Path
import re
import uuid

from PIL import Image
from native_projects import ProjectStore, timestamp
from storyboard import digest

FACES = ('reference', 'blank', 'simple', 'detailed')
FONTS = ('Arial', 'Intro Demo Black CAPS')
ROLES = ('reference', 'host', 'logo')
DEFAULT = object()
FACE_RULES = {
    'reference': 'Match the facial-detail level of each supplied character reference. Blank human faces must stay blank; never invent eyes, nose, mouth, beard or realistic facial features where the reference has none.',
    'blank': 'Ordinary human actors have plain featureless faces: no eyes, eyebrows, nose, mouth, beard or realistic facial details. Approved mascots retain their own approved features.',
    'simple': 'Ordinary human faces use minimal flat graphic features, never detailed or photorealistic faces. Keep approved character design.',
    'detailed': 'Keep the approved detailed character faces, identity, proportions and rendering technique unchanged.'}


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Некорректный профиль канала.')
    return value


def validate(value):
    if not isinstance(value, dict):raise ValueError('Некорректный дизайн канала.')
    result = {}
    for key, limit in [('name',120),('description',6000),('rules',4000)]:
        text=value.get(key,'')
        if not isinstance(text,str) or len(text)>limit or (key!='rules' and not text.strip()):
            raise ValueError('Заполните название и описание дизайна канала.')
        result[key]=text.strip()
    for key,allowed,default in [('faces',FACES,'reference'),('font',FONTS,'Arial'),('layout',('source','minimal'),'source'),('motion_policy',('local','generated'),'local')]:
        result[key]=value.get(key,default)
        if result[key] not in allowed:raise ValueError('Неизвестная настройка дизайна: '+key)
    palette=value.get('palette',[])
    if not isinstance(palette,list) or len(palette)>12 or any(not isinstance(x,str) or len(x)>80 for x in palette):
        raise ValueError('Некорректная палитра.')
    result['palette']=[x.strip() for x in palette if x.strip()]
    return result


def fingerprint(profile):
    return digest({**validate(profile),'name':'', 'assets':[
        {k:a[k] for k in ('sha256','role','file')} for a in profile.get('assets',[])]})


def generation_fingerprint(profile):
    value=validate(profile)
    for key in ('name','font','motion_policy'):value.pop(key)
    return digest({**value,'references':[a['sha256'] for a in profile.get('assets',[]) if a['role']=='reference'],
                   'separate_host':any(a['role']=='host' for a in profile.get('assets',[]))})


def selected(root, cfg):
    if '_channel_design' in cfg:return cfg['_channel_design']
    if not cfg.get('channel_design_id'):return None
    store=DesignStore(root);profile=store.get(cfg['channel_design_id'],cfg.get('channel_design_revision'))
    for asset in profile['assets']:store.asset_path(asset)
    return profile


def content_profile(profile, duration=0.):
    """A new story uses an existing design, never source frames or source cut mapping."""
    p=validate(profile)
    return {'content_only':True,'channel_design_ref':{'id':profile['id'],'generation_key':generation_fingerprint(profile)},
            'visual_style':{'description':p['description'],'palette':p['palette'],
                            'character_design':FACE_RULES[p['faces']],
                            'composition':'Minimal explanatory layouts with generous empty space.' if p['layout']=='minimal' else 'Follow the approved channel layout.',
                            'shot_types':['wide','detail','diagram']},
            'narrative_style':'','montage':{'shots':[]},
            'sampling':{'method':'transcript_only','duration':float(duration),'frame_count':0}}


class DesignStore:
    def __init__(self, root):
        self.root=Path(root);self.store=ProjectStore(root)
        with self.store.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS channel_design_heads(
                id TEXT PRIMARY KEY,name TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL) STRICT;
                CREATE TABLE IF NOT EXISTS channel_design_versions(
                id TEXT NOT NULL REFERENCES channel_design_heads(id),revision INTEGER NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)),PRIMARY KEY(id,revision)) STRICT;''')

    def list(self):
        with self.store.connection() as db:
            return [dict(id=r[0],name=r[1],revision=r[2]) for r in db.execute('SELECT id,name,revision FROM channel_design_heads ORDER BY updated_at DESC')]

    def get(self, key, revision=None):
        checked_id(key)
        if revision is not None and (type(revision) is not int or revision<1):raise ValueError('Некорректная версия дизайна.')
        with self.store.connection() as db:
            if revision is None:
                row=db.execute('SELECT revision FROM channel_design_heads WHERE id=?',(key,)).fetchone()
                if not row:raise ValueError('Профиль канала не найден.')
                revision=row[0]
            row=db.execute('SELECT payload FROM channel_design_versions WHERE id=? AND revision=?',(key,revision)).fetchone()
            if not row:raise ValueError('Версия дизайна не найдена.')
        result=json.loads(row[0]);validate(result)
        return result

    def save(self, value, key=None, revision=None, *, assets=None):
        if revision is not None and (type(revision) is not int or revision<1):raise ValueError('Некорректная версия дизайна.')
        result=validate(value);key=checked_id(key) if key else uuid.uuid4().hex
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision FROM channel_design_heads WHERE id=?',(key,)).fetchone()
            if (row and row[0]!=revision) or (not row and revision is not None):
                raise ValueError('Дизайн изменился. Откройте профиль заново перед сохранением.')
            previous={}
            if row:
                previous=json.loads(db.execute('SELECT payload FROM channel_design_versions WHERE id=? AND revision=?',(key,row[0])).fetchone()[0])
            result.update(id=key,revision=(row[0]+1 if row else 1),assets=copy.deepcopy(assets if assets is not None else previous.get('assets',[])))
            for asset in result['assets']:self.asset_path(asset)
            now=timestamp()
            db.execute('INSERT INTO channel_design_heads VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,revision=excluded.revision,updated_at=excluded.updated_at',
                       (key,result['name'],result['revision'],now))
            db.execute('INSERT INTO channel_design_versions VALUES(?,?,?)',(key,result['revision'],json.dumps(result,ensure_ascii=False)))
        return result

    def asset_path(self, asset):
        if (not isinstance(asset,dict) or asset.get('role') not in ROLES or
                not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|webp)',str(asset.get('file','')))):
            raise ValueError('Некорректный файл дизайна.')
        path=self.root/'jobs'/'channel_designs'/'assets'/asset['file']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=asset.get('sha256'):
            raise ValueError('Утверждённый файл дизайна отсутствует или изменён. Восстановите папку jobs/channel_designs.')
        return path

    def import_asset(self, key, revision, path, role='reference'):
        if role not in ROLES:raise ValueError('Неизвестная роль изображения.')
        profile=self.get(key,revision)
        replacing=role!='reference' and any(a['role']==role for a in profile['assets'])
        if len(profile['assets'])>=6 and not replacing:raise ValueError('В профиле допускается до шести изображений.')
        raw=Path(path).read_bytes()
        if not 0<len(raw)<=8*1024*1024:raise ValueError('Изображение должно быть не больше 8 МБ.')
        with Image.open(path) as image:
            if image.width*image.height>25_000_000 or image.format not in ('PNG','JPEG','WEBP'):
                raise ValueError('Используйте PNG, JPG или WebP размером до 25 мегапикселей.')
            extension={'PNG':'png','JPEG':'jpg','WEBP':'webp'}[image.format]
            if role!='reference' and ('A' not in image.getbands() or image.getextrema()[-1][0]==255):
                raise ValueError('Для неизменяемого ведущего/логотипа нужен файл с прозрачным фоном (PNG или WebP).')
            image.load()
        sha=hashlib.sha256(raw).hexdigest();name=sha+'.'+extension
        if any(a['sha256']==sha and a['role']==role for a in profile['assets']):return profile
        directory=self.root/'jobs'/'channel_designs'/'assets';directory.mkdir(parents=True,exist_ok=True)
        target=directory/name
        if not target.exists():
            temporary=directory/(name+'.'+uuid.uuid4().hex+'.tmp');temporary.write_bytes(raw);temporary.replace(target)
        assets=[a for a in profile['assets'] if not (replacing and a['role']==role)]+[{'file':name,'sha256':sha,'role':role,'name':Path(path).name[:160]}]
        return self.save(profile,key,revision,assets=assets)

    def remove_asset(self,key,revision,index):
        profile=self.get(key,revision)
        if type(index) is not int or not 0<=index<len(profile['assets']):raise ValueError('Выберите существующее изображение.')
        assets=[a for n,a in enumerate(profile['assets']) if n!=index]
        return self.save(profile,key,revision,assets=assets)  # Historical versions retain their immutable files.


def guide(profile):
    p=validate(profile)
    return ('APPROVED CHANNEL DESIGN, higher priority than the art direction of a new source video. '
            'Keep this design, palette and character rendering across every shot; the source frame supplies subject and composition only. '
            'Artwork references define the design language, not the story. Never introduce their subjects or objects unless requested in the current scene. '
            + p['description']+' Palette: '+', '.join(p['palette'])+'. '+FACE_RULES[p['faces']]+' '+
            ('Explanatory/diagram shots use a white/clean background, generous negative space and at most one or two main objects. Other shots retain their setting. No decorative diagrams or multi-panel collages. ' if p['layout']=='minimal' else '')+
            p['rules']+' All readable labels/numbers are added by the program; never draw pseudo-text.')


def apply(scenes, profile, root):
    if not profile:return scenes
    store=DesignStore(root);refs=[str(store.asset_path(a)) for a in profile.get('assets',[]) if a['role']=='reference']
    overlays=[{**a,'path':str(store.asset_path(a))} for a in profile.get('assets',[]) if a['role']!='reference']
    signature=generation_fingerprint(profile);instruction=guide(profile);output=[]
    for original in scenes:
        scene=dict(original)
        diagram=(scene.get('shot')=='diagram' or scene.get('shot_size')=='diagram') or bool(re.search(r'diagram|infographic|white background|white canvas|схем|инфограф',str(scene.get('raw_prompt','')),re.I))
        selected=[a for a in overlays if a['role']=='logo' or diagram]
        scene.update(channel_design_signature=signature,channel_design=profile,channel_references=refs,channel_overlays=selected)
        scene['prompt']+=' '+instruction
        if any(a['role']=='host' for a in selected):
            scene['prompt']+=' The approved host will be composited separately in the bottom-left corner. Do not draw a second host; leave that area empty.'
        output.append(scene)
    return output


def decorate_clip(scene, clip, directory, width, height, fps):
    """Stamp immutable PNG/WebP artwork AFTER generated movement, never through a video model."""
    overlays=scene.get('channel_overlays') or []
    if not overlays:return clip
    from media_tools import ffmpeg_bin, run_media
    import netutil
    signature=digest([hashlib.sha256(Path(clip).read_bytes()).hexdigest(),
                      [[a['sha256'],a['role']] for a in overlays],width,height,fps,'fixed-overlays-v1'])
    output=Path(directory)/'channel_layers'/(signature+'.mp4');output.parent.mkdir(parents=True,exist_ok=True)
    if output.is_file():return str(output)
    args=[ffmpeg_bin(),'-y','-loglevel','error','-threads','2','-filter_complex_threads','1','-i',str(clip)];filters=[];previous='0:v'
    for n,asset in enumerate(overlays,1):
        path=Path(asset['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=asset['sha256']:raise ValueError('Утверждённый слой дизайна изменён.')
        with Image.open(path) as img:
            scale=min(width*(.09 if asset['role']=='logo' else .23)/img.width,height*.38/img.height)
            w,h=max(2,round(img.width*scale)),max(2,round(img.height*scale))
        args+=['-loop','1','-i',str(path)]
        x,y=(width-w-round(width*.025),round(height*.025)) if asset['role']=='logo' else (round(width*.04),height-h-round(height*.06))
        filters += [f'[{n}:v]scale={w}:{h},format=rgba[asset{n}]',f'[{previous}][asset{n}]overlay={x}:{y}:shortest=1[layer{n}]']
        previous=f'layer{n}'
    temporary=str(output)+'.tmp.mp4';netutil.check_cancel()
    result=run_media(args+['-filter_complex',';'.join(filters),'-map',f'[{previous}]','-an','-r',str(fps),'-threads','2','-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',temporary])
    if result.returncode:raise RuntimeError('Не удалось наложить утверждённые слои канала.')
    netutil.check_cancel();Path(temporary).replace(output)
    return str(output)
