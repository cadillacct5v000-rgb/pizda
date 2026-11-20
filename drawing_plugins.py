"""Local drawing adapters. Model output is bounded geometry, never executable code."""
import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageColor, ImageDraw
import netutil
import storyboard as sb

VERSION=1
STYLES={
    'sketchbook':{'name':'Цветной карандаш','engine':'Cloveric · Sketchbook','description':'Warm paper, textured coloured pencil fills and delicate hand-drawn contours.'},
    'lineart':{'name':'Линия и акцент','engine':'Cloveric · LineArt','description':'Clean paper, fine continuous contours and restrained coloured accents.'},
    'sketchling':{'name':'Рисованная заливка','engine':'Sketchling','description':'Hand-drawn flat illustration with controlled rough outlines and solid colour fills.'},
}


def _number(value,lo=0.,hi=1.):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:
        raise ValueError(f'Координаты рисовки должны быть числами от {lo:g} до {hi:g}.')
    return float(value)


def _text(value,limit=120):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('Некорректное название объекта рисовки.')
    return value.strip()


def _shapes(values):
    if not isinstance(values,list) or not 1<=len(values)<=60:raise ValueError('Нужно 1–60 контуров на объект.')
    result=[]
    for row in values:
        if not isinstance(row,dict):raise ValueError('Контур должен быть объектом JSON.')
        points=row.get('points')
        if not isinstance(points,list) or not 2<=len(points)<=100:raise ValueError('В контуре нужно 2–100 точек.')
        path=[]
        for p in points:
            if not isinstance(p,list) or len(p)!=2:raise ValueError('Точка должна содержать x и y.')
            path.append([_number(p[0]),_number(p[1])])
        fill=row.get('fill');stroke=row.get('stroke',0)
        if fill is not None and (type(fill) is not int or not 0<=fill<=7):raise ValueError('Заливка — номер цвета 0–7 или null.')
        if type(stroke) is not int or not 0<=stroke<=7:raise ValueError('Контур — номер цвета 0–7.')
        closed=row.get('closed',True);smooth=row.get('smooth',False)
        if type(closed) is not bool or type(smooth) is not bool:raise ValueError('closed/smooth должны быть логическими значениями.')
        result.append({'points':path,'closed':closed,'smooth':smooth,'fill':fill,'stroke':stroke,'width':_number(row.get('width',.004),.001,.02)})
    return result


def settings(value=None):
    if value is None:return None
    if not isinstance(value,dict) or value.get('style') not in STYLES:raise ValueError('Выберите установленный плагин рисовки.')
    seed=value.get('seed',24)
    if type(seed) is not int or not 0<=seed<2**31:raise ValueError('Seed рисовки должен быть целым числом от 0 до 2147483647.')
    motion=value.get('motion','still')
    if motion not in ('still','draw'):raise ValueError('Неизвестный эффект рисовки.')
    enabled=value.get('enabled',True)
    if type(enabled) is not bool:raise ValueError('Некорректное включение местной рисовки.')
    symbols=value.get('symbols',[])
    if not isinstance(symbols,list) or len(symbols)>12:raise ValueError('Можно закрепить до 12 рисованных объектов.')
    result=[];seen=set();aliases_seen=set();total=0
    for symbol in symbols:
        if not isinstance(symbol,dict) or not re.fullmatch(r's_[0-9a-f]{16}',str(symbol.get('id',''))):raise ValueError('Некорректный ID рисованного объекта.')
        if symbol['id'] in seen:raise ValueError('Объект рисовки уже закреплён.')
        seen.add(symbol['id']);aliases=symbol.get('aliases',[])
        if not isinstance(aliases,list) or not 1<=len(aliases)<=20:raise ValueError('Укажите 1–20 точных имён объекта, включая названия на нужных языках.')
        shapes=_shapes(symbol.get('shapes'));total+=len(shapes)
        aliases=[_text(a) for a in aliases]
        if any(a.casefold() in aliases_seen for a in aliases):raise ValueError('Обозначение уже закреплено за другим рисованным объектом.')
        aliases_seen.update(a.casefold() for a in aliases)
        result.append({'id':symbol['id'],'name':_text(symbol.get('name')),'aliases':aliases,'shapes':shapes,'aspect':_number(symbol.get('aspect',1),.01,100)})
    if total>240:raise ValueError('Слишком много контуров в библиотеке канала (максимум 240).')
    return {'enabled':enabled,'style':value['style'],'seed':seed,'motion':motion,'symbols':result}


def active(profile):
    return bool(profile and profile.get('drawing') and profile['drawing'].get('enabled',True))


def validate_scene(value,drawing=None):
    drawing=settings(drawing) if drawing else None
    symbols={s['id']:s for s in (drawing or {}).get('symbols',[])}
    if not isinstance(value,dict) or not isinstance(value.get('objects'),list) or not 1<=len(value['objects'])<=12:
        raise ValueError('Сцена рисовки должна содержать 1–12 объектов.')
    result=[];total=0
    for row in value['objects']:
        if not isinstance(row,dict):raise ValueError('Рисованный объект должен быть JSON-объектом.')
        obj={k:_number(row.get(k),0,1) for k in ('x','y','w','h')}
        if obj['w']<.01 or obj['h']<.01 or obj['x']+obj['w']>1.001 or obj['y']+obj['h']>1.001:raise ValueError('Объект рисовки выходит за границы кадра.')
        if row.get('symbol'):
            if row['symbol'] not in symbols:raise ValueError('Сцена ссылается на незакреплённый рисованный объект.')
            obj.update(symbol=row['symbol'],name=symbols[row['symbol']]['name'])
            obj['h']=obj['w']/symbols[row['symbol']]['aspect']
            if obj['y']+obj['h']>1:
                obj['h']=1-obj['y'];obj['w']=obj['h']*symbols[row['symbol']]['aspect']
            if min(obj['w'],obj['h'])<.01:raise ValueError('Закреплённый объект не помещается в кадр без искажения пропорций.')
        else:obj.update(name=_text(row.get('name')),shapes=_shapes(row.get('shapes')))
        total+=len(symbols[obj['symbol']]['shapes'] if obj.get('symbol') else obj['shapes'])
        result.append(obj)
    if total>240:raise ValueError('Слишком сложная сцена: больше 240 контуров.')
    return {'objects':result}


def example():
    """Own neutral test scene; never presented as an analysis of a user's source."""
    return validate_scene({'objects':[{'name':'Кит','x':.12,'y':.2,'w':.72,'h':.58,'shapes':[
        {'points':[[.03,.39],[.16,.18],[.42,.13],[.69,.29],[.83,.42],[.94,.22],[.99,.29],[.94,.52],[.99,.7],[.9,.74],[.82,.59],[.56,.78],[.26,.77],[.08,.62]],'closed':True,'smooth':True,'fill':1},
        {'points':[[.12,.6],[.31,.67],[.59,.68],[.73,.57],[.56,.78],[.26,.77]],'closed':True,'smooth':True,'fill':2},
        {'points':[[.45,.57],[.42,.87],[.56,.76],[.63,.61]],'closed':True,'smooth':True,'fill':1},
        {'points':[[.15,.39],[.165,.38],[.18,.39],[.165,.41]],'closed':True,'fill':0},
        {'points':[[.06,.51],[.16,.56],[.24,.54]],'closed':False,'smooth':True},
    ]}]})


def matched_symbols(drawing,brief):
    return [s for s in drawing['symbols'] if any(re.search(r'(?<!\w)'+re.escape(a.casefold())+r'(?!\w)',brief.casefold()) for a in s['aliases'])]


def plan(client,brief,design,directory,progress=None):
    """One cached text call per scene; all brushes reuse that geometry."""
    import studio
    import model_reply
    brief=_text(brief,16000);drawing=settings(design.get('drawing'))
    if not drawing:raise ValueError('Сначала выберите плагин рисовки.')
    required=matched_symbols(drawing,brief)
    registry=[{k:s[k] for k in ('id','name','aliases')} for s in drawing['symbols']]
    prompt=('Design a simple ORIGINAL explanatory illustration, not a copy of a source composition. '
            'Return bounded JSON geometry ONLY, never Python, JavaScript, SVG markup, URLs or commands. '
            'Coordinates are normalized 0..1: objects x,y,w,h on canvas; each path points relative to its object. '
            'Each shape has 2..100 points, closed/smooth booleans, fill (palette index 0..7 or null), stroke (0..7), width (.001..02). '
            'Palette 0=dark ink, 1=main accent, 2=light accent, 3=secondary accent, 4..7=additional approved colours. '
            '1..12 objects, 1..60 shapes each, total <=240. Use recognisable silhouettes, generous empty space. '
            'Do not draw text or invented face details. Human faces must respect the supplied face rule. '
            'When the rule is reference and no detailed own human face has been approved, use blank featureless human faces. '
            'For an approved recurring object use {symbol:existing_id,x,y,w,h}; NEVER redraw its shapes or add a duplicate. '
            'Every required_symbol must appear. Other objects use {name,x,y,w,h,shapes:[{points:[[x,y],...],closed:true,smooth:true,fill:1,stroke:0,width:.004}]}. '
            'Return {objects:[...]}. Reference JSON:\n'+json.dumps({'brief':brief,'face_rule':design.get('faces','blank'),
            'approved_objects':registry,'required_symbols':[s['id'] for s in required]},ensure_ascii=False))
    path=Path(directory)/('scene_'+sb.digest([VERSION,prompt])+'.json')
    value=studio._query(client,prompt,path,progress)
    try:
        scene=validate_scene(value,drawing)
        used={o.get('symbol') for o in scene['objects']}
        if any(s['id'] not in used for s in required):raise ValueError('Модель не использовала закреплённый объект: '+', '.join(s['name'] for s in required if s['id'] not in used))
    except (ValueError,TypeError,KeyError) as error:
        sb.atomic(path.with_suffix('.received.json'),{'reason':str(error),'result':value})
        path.unlink(missing_ok=True);model_reply.invalidate(path.with_suffix('.reply.json'))
        raise ValueError('Сцена рисовки не прошла проверку: '+str(error)+'. Ответ сохранён; картинки не оплачивались.') from None
    return scene


def pin_object(drawing,scene,index,name,aliases):
    drawing=settings(drawing);scene=validate_scene(scene,drawing)
    if type(index) is not int or not 0<=index<len(scene['objects']):raise ValueError('Выберите объект из пробной сцены.')
    obj=scene['objects'][index]
    shapes=obj.get('shapes') or next(s['shapes'] for s in drawing['symbols'] if s['id']==obj['symbol'])
    symbol={'id':'s_'+sb.digest(shapes)[:16],'name':name,'aliases':aliases,'shapes':copy.deepcopy(shapes),'aspect':obj['w']/obj['h']}
    drawing['symbols']=[s for s in drawing['symbols'] if s['id']!=symbol['id']]+[symbol]
    return settings(drawing)


def palette(design):
    raw=[]
    for c in design.get('palette',[]):
        try:
            rgb=ImageColor.getrgb(c)
            if len(rgb)==3 and 35<sum(rgb)/3<220:raw.append('#%02x%02x%02x'%rgb)
        except (ValueError,TypeError):pass
    base=(raw+['#167b88','#8bcbd1','#e4a84d','#d87563','#546b8a','#9f91b2','#657653'])[:7]
    return ['#253441',*base]


def paths(scene,drawing,width,height):
    symbols={s['id']:s for s in drawing['symbols']}
    for obj in scene['objects']:
        for shape in symbols[obj['symbol']]['shapes'] if obj.get('symbol') else obj['shapes']:
            points=[((obj['x']+x*obj['w'])*width,(obj['y']+y*obj['h'])*height) for x,y in shape['points']]
            if shape['smooth']:
                from drawing_vendor.cloveric.core import spline
                source=points+[points[0]] if shape['closed'] else points
                points=spline(source,per=8).tolist()
            if shape['closed'] and points[-1]!=points[0]:points=points+[points[0]]
            yield shape,points


def browser_path():
    candidates=[os.environ.get('KADROGEN_DRAWING_BROWSER'),os.environ.get('KADROGEN_BROWSER')]
    for name in ('msedge','google-chrome','chromium','chromium-browser'):candidates.append(shutil.which(name))
    for env in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        if os.environ.get(env):candidates.extend(str(Path(os.environ[env])/p) for p in ('Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe'))
    candidates+=['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()),None)


def catalog():
    browser=browser_path()
    return [{'id':k,**v,'available':k!='sketchling' or bool(browser),
             'reason':'' if k!='sketchling' or browser else 'Для Sketchling нужен установленный Microsoft Edge или Chrome.'} for k,v in STYLES.items()]


def _sketchling(scene,drawing,colours,width,height):
    executable=browser_path()
    if not executable:raise ValueError('Для Sketchling установите Microsoft Edge или Google Chrome; Cloveric работает без браузера.')
    bundle=Path(__file__).resolve().parent/'assets/drawing/sketchling.js'
    if not bundle.is_file():raise ValueError('В сборке отсутствует движок Sketchling.')
    data={'scene':scene,'drawing':drawing,'colours':colours,'width':width,'height':height}
    payload=json.dumps(data,ensure_ascii=True).replace('<','\\u003c')
    html=('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; img-src data:; style-src \'unsafe-inline\';">'
          '<style>body{margin:0}</style><div id="stage"></div><pre id="result"></pre><script>'+bundle.read_text(encoding='utf-8')+'</script><script>window.kadroRender('+payload+');</script>')
    with tempfile.TemporaryDirectory(prefix='kadrogen-drawing-') as directory:
        page=Path(directory)/'scene.html';page.write_text(html,encoding='utf-8')
        command=[executable,'--headless','--no-sandbox','--disable-gpu','--disable-extensions','--disable-background-networking','--no-first-run','--no-default-browser-check',
                 '--host-resolver-rules=MAP * ~NOTFOUND','--virtual-time-budget=3000','--user-data-dir='+str(Path(directory)/'profile'),'--dump-dom',page.as_uri()]
        result=subprocess.run(command,capture_output=True,timeout=40,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        match=re.search(rb'<pre id="result">([A-Za-z0-9+/=]+)</pre>',result.stdout)
        if result.returncode or not match:raise ValueError('Sketchling не завершил отрисовку. Попробуйте Cloveric или проверьте доступность Edge/Chrome.')
        import io
        image=Image.open(io.BytesIO(base64.b64decode(match[1],validate=True)));image.load()
        if image.size!=(width,height):raise ValueError('Sketchling вернул неверный размер картинки.')
        return image.convert('RGB')


def render(scene,design,output,width=960,height=540):
    drawing=settings(design.get('drawing'));scene=validate_scene(scene,drawing)
    if not drawing:raise ValueError('Не выбран плагин рисовки.')
    if type(width) is not int or type(height) is not int or not 160<=width<=1920 or not 90<=height<=1080:raise ValueError('Недопустимый размер рисовки.')
    netutil.check_cancel();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    colours=palette(design);style=drawing['style'];key=sb.digest([VERSION,scene,{k:v for k,v in drawing.items() if k!='motion'},colours,width,height])
    meta_path=Path(str(output)+'.drawing.json')
    try:
        meta=json.loads(meta_path.read_text(encoding='utf-8'))
        if meta.get('key')==key and metadata(output):
            # Motion is not part of image identity; update it without redrawing.
            meta['drawing']=drawing;sb.atomic(meta_path,meta);return str(output)
    except (OSError,ValueError,AttributeError):pass
    if style=='sketchling':image=_sketchling(scene,drawing,colours,width,height)
    elif style=='sketchbook':
        from drawing_vendor.cloveric.sketchbook import Sketchbook
        canvas=Sketchbook(width,height,seed=drawing['seed'],keep_stages=False);canvas.paper();canvas.finish()
        for shape,points in paths(scene,drawing,width,height):
            netutil.check_cancel()
            if shape['fill'] is not None and shape['closed']:
                canvas.tint(canvas.poly(points),colours[shape['fill']],strength=.85,grain=.65,seed=drawing['seed'])
            canvas.ink(points,width=max(.7,shape['width']*height),colour=colours[shape['stroke']],smooth=False,wobble=.55)
        image=canvas.composite()
    else:
        from drawing_vendor.cloveric.lineart import LineArt
        canvas=LineArt(width,height,seed=drawing['seed'],ss=2);canvas.backdrop('cream',colour='#faf7f0',edge='#faf7f0',grain=.002)
        for shape,points in paths(scene,drawing,width,height):
            netutil.check_cancel()
            if shape['fill'] is not None and shape['fill']>0 and shape['closed']:
                # Keep the brush's own accent-compositing rather than replacing it with a filter.
                from PIL import ImageDraw
                import numpy as np
                mask=Image.new('L',(width,height));ImageDraw.Draw(mask).polygon(points,fill=255)
                canvas.accent(np.asarray(mask,dtype=np.float32)/255,colours[shape['fill']],glow=0,inner=.7)
            canvas.line(points,width=max(.7,shape['width']*height),colour=colours[shape['stroke']],glow=0,sheen=0,taper=(1,1))
        image=canvas.composite(tip=False)
    netutil.check_cancel();temporary=output.with_suffix('.tmp.png');image.save(temporary);temporary.replace(output)
    sb.atomic(meta_path,{'version':VERSION,'key':key,'scene':scene,'drawing':drawing,'palette':colours,'size':[width,height],
                         'image_sha256':hashlib.sha256(output.read_bytes()).hexdigest()})
    return str(output)


def metadata(image):
    image=Path(image)
    try:
        value=json.loads(Path(str(image)+'.drawing.json').read_text(encoding='utf-8'))
        if value.get('version')!=VERSION or value.get('image_sha256')!=hashlib.sha256(image.read_bytes()).hexdigest():return None
        validate_scene(value['scene'],value['drawing'])
        return value
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return None


def restore_metadata(image,candidate):
    try:
        value=json.loads(Path(str(candidate)+'.drawing.json').read_text(encoding='utf-8'))
        if value.get('image_sha256')!=hashlib.sha256(Path(image).read_bytes()).hexdigest():return False
        validate_scene(value['scene'],value['drawing'])
        sb.atomic(Path(str(image)+'.drawing.json'),value)
        return True
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return False


def gallery(scene,design,directory,clips=False):
    drawing=settings(design.get('drawing') or {'style':'sketchbook'})
    result=[]
    for item in catalog():
        netutil.check_cancel()
        record=dict(item)
        if item['available']:
            choice={**drawing,'style':item['id']};value={**design,'drawing':choice}
            path=Path(directory)/(sb.digest([scene,value,VERSION])+'.png')
            try:
                render(scene,value,path,640,360);record.update(image='data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode('ascii'))
                if clips:
                    from media_tools import ffmpeg_bin
                    movie=Path(str(path)+'.preview-v1.mp4')
                    if not movie.is_file():render_clip(path,5,movie,640,360,15,ffmpeg_bin(),netutil.check_cancel)
                    record.update(video='data:video/mp4;base64,'+base64.b64encode(movie.read_bytes()).decode('ascii'),seconds=5,motion='draw')
            except (ValueError,OSError,subprocess.SubprocessError) as error:record.update(available=False,reason=str(error))
        result.append(record)
    return result


def local_context(cfg):
    design=copy.deepcopy(cfg['_channel_design']);drawing=settings(design.get('drawing'))
    if drawing['style']=='sketchling' and not browser_path():raise ValueError('Для выбранного Sketchling нужен Edge/Chrome. Выберите Cloveric в дизайне канала.')
    import studio
    client=studio.text_client(cfg)
    class Gate:threads=1
    def generate(scene,out,progress,refs):
        if scene.get('channel_characters') and any(a.get('role')=='character' for a in scene['channel_characters']):
            raise ValueError('Для местной рисовки закрепите персонажа как рисованный объект; фотореференсы не преобразуются в геометрию автоматически.')
        brief=scene.get('source_redesign') or scene.get('raw_prompt') or scene.get('prompt') or scene.get('text','')
        import pipeline
        directory=Path(pipeline.data_dir())/'drawing-plugins'/'scenes'
        say=lambda message:progress('images',0,0,message)
        try:
            geometry=plan(client,brief,design,directory,say)
            render(geometry,design,out,1280,720)
        except ValueError as error:
            from pipeline import SceneRejected
            raise SceneRejected(str(error)) from error
        return 'drawing:'+drawing['style']
    return {'api':None,'providers':['drawing'],'bases':[],'gates':{'drawing':Gate()},'image_gen':{'drawing':generate},
            'fallback':[],'cfg':cfg,'video_engine':cfg.get('video_engine','providers'),'video_config':dict(cfg),'sliders':[],'drawing':True}


def render_clip(image_path,duration,output,width,height,fps,ffmpeg,cancel):
    """Trace outlines, then reveal the approved colour image; no new model calls."""
    meta=metadata(image_path)
    if not meta:raise ValueError('Данные рисовки не соответствуют этому изображению. Перерисуйте этот план.')
    drawing=settings(meta['drawing']);scene=validate_scene(meta['scene'],drawing)
    final=Image.open(image_path).convert('RGB').resize((width,height),Image.Resampling.LANCZOS)
    background=Image.new('RGB',(width,height),'#f2e9e1' if drawing['style']=='sketchbook' else '#faf7f0')
    outline=background.copy();all_paths=list(paths(scene,drawing,width,height))
    lengths=[sum(math.dist(a,b) for a,b in zip(points,points[1:])) for _,points in all_paths];total=max(1,sum(lengths))
    frames=max(2,round(duration*fps));temporary=str(output)+'.tmp.mp4'
    command=[ffmpeg,'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r',str(fps),'-i','-',
             '-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',temporary]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for index in range(frames):
            if index%fps==0:cancel()
            t=index/max(1,frames-1);canvas=outline.copy();draw=ImageDraw.Draw(canvas)
            remaining=min(1,t/.5)*total
            for (shape,points),length in zip(all_paths,lengths):
                allowed=max(0,min(length,remaining));remaining-=length
                if allowed<=0:continue
                part=[points[0]];left=allowed
                for a,b in zip(points,points[1:]):
                    segment=math.dist(a,b)
                    if left>=segment:part.append(b);left-=segment
                    else:
                        if segment:part.append((a[0]+(b[0]-a[0])*left/segment,a[1]+(b[1]-a[1])*left/segment))
                        break
                if len(part)>1:draw.line(part,fill=meta['palette'][shape['stroke']],width=max(1,round(shape['width']*height)),joint='curve')
            if t>=.5:
                # Object-local reveals follow path order, not a whole-screen wipe.
                mask=Image.new('L',(width,height));md=ImageDraw.Draw(mask);count=max(1,len(all_paths));fraction=min(1,(t-.5)/.32)
                for n,(_,points) in enumerate(all_paths):
                    alpha=round(255*max(0,min(1,fraction*count-n)))
                    if alpha:md.polygon(points,fill=alpha)
                if fraction>=1:canvas=final
                else:canvas=Image.composite(final,canvas,mask)
            process.stdin.write(canvas.tobytes())
        process.stdin.close();error=process.stderr.read().decode(errors='replace');code=process.wait()
        if code:raise ValueError('Не удалось собрать прорисовку: '+error[:300])
        cancel();Path(temporary).replace(output)
    finally:
        if process.poll() is None:process.kill();process.wait()
        Path(temporary).unlink(missing_ok=True)
    return str(output)
