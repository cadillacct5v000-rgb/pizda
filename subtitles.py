"""Субтитры из слов с таймкодами: сборка .srt и наложение на видео через ffmpeg.

Правила как в хороших инструментах: строка не длиннее 42 символов, реплика режется по знакам
препинания и паузам, паузы до 5 секунд склеиваются (субтитр держится до следующего), чтобы на
экране не было мигания.
"""
import os
import re
import tempfile

from media_tools import run_media

CHARS_PER_LINE = 42
MAX_LINES = 2
MAX_CUE_SECONDS = 6.0
GLUE_MAX_GAP = 5.0
MIN_CUE_SECONDS = 0.8


def _fmt(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(words):
    """words: [{"text","start","end"}] -> [{"start","end","text"}] (text может быть из двух строк)."""
    cues, cur = [], []

    def cur_len(extra=None):
        ws = cur + ([extra] if extra else [])
        return len(" ".join(w["text"] for w in ws))

    def flush():
        if not cur:
            return
        text = " ".join(w["text"] for w in cur)
        cues.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": wrap(text)})
        cur.clear()

    for i, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            too_long = cur_len(w) > CHARS_PER_LINE * MAX_LINES - 4
            too_slow = w["end"] - cur[0]["start"] > MAX_CUE_SECONDS
            if too_long or too_slow or gap > 1.2:
                flush()
        cur.append(w)
        strong = re.search(r"[.!?…]$", w["text"]) is not None
        weak = re.search(r"[,;:]$", w["text"]) is not None
        if strong or (weak and cur_len() > CHARS_PER_LINE):
            flush()
    flush()
    # склейка пауз и минимальная длительность
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else None
        if nxt is not None:
            gap = nxt - c["end"]
            if 0 < gap <= GLUE_MAX_GAP:
                c["end"] = nxt
        if c["end"] - c["start"] < MIN_CUE_SECONDS:
            c["end"] = c["start"] + MIN_CUE_SECONDS
            if nxt is not None and c["end"] > nxt:
                c["end"] = nxt
    return cues


def wrap(text):
    """Разбить реплику на 1–2 строки не длиннее CHARS_PER_LINE, по возможности по знаку препинания."""
    if len(text) <= CHARS_PER_LINE:
        return text
    words = text.split()
    best, line1 = None, []
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        if len(a) > CHARS_PER_LINE or len(b) > CHARS_PER_LINE:
            continue
        score = abs(len(a) - len(b)) - (12 if re.search(r"[,.;:!?…]$", a) else 0)
        if best is None or score < best[0]:
            best = (score, a, b)
    if best:
        return best[1] + "\n" + best[2]
    # не влезает в две строки — режем как есть
    mid = len(words) // 2
    return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])


def write_srt(words, path):
    cues = build_cues(words)
    with open(path, "w", encoding="utf-8-sig") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{_fmt(c['start'])} --> {_fmt(c['end'])}\n{c['text']}\n\n")
    return path


# Шрифт пользователя (fonts/IntroDemo-BlackCAPS.otf, семейство «Intro Demo Black CAPS»); тонкая обводка, ниже к краю
STYLE = "FontName=Intro Demo Black CAPS,FontSize=15,Bold=0,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H60000000,Outline=1,Shadow=0.6,MarginV=12,Alignment=2"
FONT_FILE = "IntroDemo-BlackCAPS.otf"


def fonts_dir():
    import sys
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.argv[0]))
    for d in (os.path.join(base, "fonts"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")):
        if os.path.exists(os.path.join(d, FONT_FILE)):
            return d
    return None


def burn(ffmpeg, video_in, srt_path, video_out, no_window=0):
    """Наложить субтитры: перекодирует видео один раз, звук копируется. Шрифт кладётся рядом с .srt (папка fonts)."""
    import shutil
    video_in, video_out = os.path.abspath(video_in), os.path.abspath(video_out)
    style, fd = STYLE, fonts_dir()
    # A fixed filename avoids FFmpeg's multiple escaping layers for apostrophes,
    # colons, commas and brackets in user-selected paths (common on macOS).
    with tempfile.TemporaryDirectory(prefix="kadrogen-subs-") as workdir:
        is_ass = str(srt_path).lower().endswith('.ass')
        name = 'captions.ass' if is_ass else 'captions.srt'
        shutil.copyfile(srt_path, os.path.join(workdir, name))
        if is_ass:
            vf = 'subtitles=captions.ass'
        elif fd:
            local = os.path.join(workdir, "fonts")
            os.mkdir(local)
            shutil.copyfile(os.path.join(fd, FONT_FILE), os.path.join(local, FONT_FILE))
            vf = f"subtitles=captions.srt:fontsdir=fonts:force_style='{style}'"
        else:
            vf = f"subtitles=captions.srt:force_style='{style.replace('Intro Demo Black CAPS', 'Arial')}'"
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", video_in, "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-c:a", "copy", "-movflags", "+faststart", video_out]
        r = run_media(cmd, cwd=workdir, creationflags=no_window)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg (субтитры): " + r.stderr.decode(errors="replace")[:400])
    return video_out


# Studio captions use ASS for timed word highlights and safe post-produced labels.
def _ass_time(seconds):
    value=max(0,int(round(float(seconds)*100)))
    hours,value=divmod(value,360000);minutes,value=divmod(value,6000);secs,centis=divmod(value,100)
    return f'{hours}:{minutes:02d}:{secs:02d}.{centis:02d}'


def _ass_text(value):
    return str(value).replace('\\','＼').replace('{','｛').replace('}','｝').replace('\r',' ').replace('\n',' ')


def _ass_color(value, fallback='#D9B66F'):
    value=str(value)
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',value):value=fallback
    return '&H00'+value[5:7]+value[3:5]+value[1:3]


def source_accent(profile=None):
    palette=((profile or {}).get('visual_style') or {}).get('palette') or []
    colors=[v for v in palette if isinstance(v,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',v)]
    if not colors:
        # Colour sampling is analysis only; source frames are never modified.
        from PIL import Image
        shots=((profile or {}).get('montage') or {}).get('shots') or []
        for shot in shots[:3]:
            try:
                with Image.open(shot['reference_frame']) as image:
                    sampled=image.convert('RGB');sampled.thumbnail((64,36))
                    quantized=sampled.quantize(colors=5)
                    data=quantized.getpalette()
                    for count,index in quantized.getcolors() or []:
                        r,g,b=data[index*3:index*3+3]
                        if max(r,g,b)-min(r,g,b)>35 and 55<(r+g+b)/3<225:
                            colors.append(f'#{r:02X}{g:02X}{b:02X}')
            except (OSError,KeyError,TypeError,ValueError):
                continue
    return colors[0] if colors else '#D9B66F'


def caption_groups(words):
    groups,group=[],[]
    for word in words:
        try:
            start,end=float(word['start']),float(word['end']);text=str(word['text']).strip()
            import math
            if not text or not math.isfinite(start) or not math.isfinite(end) or end<=start:continue
        except (KeyError,TypeError,ValueError):continue
        if group and (len(group)>=5 or len(' '.join(w['text'] for w in group))+len(text)>42 or
                      start-group[-1]['end']>.6 or end-group[0]['start']>3.8):
            groups.append(group);group=[]
        group.append({'text':text,'start':max(0,start),'end':end})
        if re.search(r'[.!?…]$',text):groups.append(group);group=[]
    if group:groups.append(group)
    return groups


def write_ass(words,path,*,preset='source',profile=None,width=1920,height=1080,position='bottom',annotations=(),captions=True):
    if preset not in ('source','focus','panel','minimal'):raise ValueError('Неизвестный стиль субтитров.')
    width,height=int(width),int(height)
    if width<160 or height<90:raise ValueError('Неверный размер кадра.')
    accent=_ass_color(source_accent(profile));foreground='&H00F4F1E9'
    size=max(18,round(height*.044));margin=max(20,round(height*.075));align=8 if position=='top' else 2
    border=3 if preset=='panel' else 1
    label_align=1 if position=='top' else 7
    header=(f'[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n'
            '[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n'
            f'Style: Caption,Arial,{size},{foreground},{accent},&H00201B18,&H90130F0D,-1,0,0,0,100,100,0,0,{border},2.5,0,{align},{round(width*.10)},{round(width*.10)},{margin},1\n'
            f'Style: Label,Arial,{max(16,round(height*.035))},{accent},{accent},&H001B1714,&HA0181412,-1,0,0,0,100,100,0,0,3,10,0,{label_align},{round(width*.065)},{round(width*.065)},{round(height*.08)},1\n'
            '[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n')
    lines=[header]
    def emit(start,end,style,text):
        if end-start<.015:return
        lines.append(f'Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}\n')
    if captions:
        for group in caption_groups(words):
            if preset=='focus':
                for index,word in enumerate(group):
                    end=min(word['end'],group[index+1]['start']) if index+1<len(group) else word['end']
                    phrase=' '.join((f'{{\\c{accent}&}}'+_ass_text(w['text'])+f'{{\\c{foreground}&}}') if n==index else _ass_text(w['text']) for n,w in enumerate(group))
                    emit(word['start'],end,'Caption',phrase)
            else:
                longest=max(range(len(group)),key=lambda n:len(group[n]['text']))
                phrase=' '.join((f'{{\\c{accent}&}}'+_ass_text(w['text'])+f'{{\\c{foreground}&}}') if n==longest and preset!='minimal' else _ass_text(w['text']) for n,w in enumerate(group))
                emit(group[0]['start'],group[-1]['end'],'Caption','{\\fad(60,90)}'+phrase)
    seen={};last=-100
    for scene in annotations:
        text=str(scene.get('overlay_text') or '').strip()[:45]
        if not text:continue
        start=float(scene.get('start',0))+.15;end=min(float(scene.get('end',start+2.5)),start+2.5)
        if start-last<8 or start-seen.get(text,-100)<30:continue
        # A sparse key phrase, not a replacement for actual diagrams or evidence.
        emit(start,end,'Label','{\\fad(100,140)}'+_ass_text(text));seen[text]=start;last=start
    with open(path,'w',encoding='utf-8-sig') as output:output.writelines(lines)
    return path
