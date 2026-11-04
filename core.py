from __future__ import annotations
import json, os, re, subprocess, uuid, hashlib, sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from project_files import write_project

VIDEO_EXT={'.mp4','.mov','.mkv','.avi','.webm','.m4v'}
AUDIO_EXT={'.mp3','.wav','.m4a','.aac','.flac','.ogg'}

def run_hidden(args, **kwargs):
    kwargs.setdefault('creationflags', 0x08000000 if os.name=='nt' else 0)
    return subprocess.run(args, **kwargs)

def probe_media(ffmpeg:str, path:str)->dict:
    p=run_hidden([ffmpeg,'-hide_banner','-i',path],capture_output=True,text=True,encoding='utf-8',errors='replace')
    text=p.stderr
    m=re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)',text)
    if not m: raise ValueError(f'无法读取媒体：{Path(path).name}')
    duration=int(m[1])*3600+int(m[2])*60+float(m[3])
    video=re.search(r'Video:\s*([^,]+).*?(\d{2,5})x(\d{2,5}).*?(\d+(?:\.\d+)?)\s*fps',text)
    created=re.search(r'creation_time\s*:\s*([^\r\n]+)',text,re.I)
    creation_time=created.group(1).strip() if created else ''
    # Cameras commonly encode capture time in the filename even after metadata is lost.
    stamp=re.search(r'(?<!\d)(20\d{6})[_-]?(\d{6})(?!\d)',Path(path).stem)
    capture_order=(stamp.group(1)+stamp.group(2)) if stamp else creation_time
    if not capture_order:
        try:capture_order=f'{Path(path).stat().st_mtime_ns:020d}'
        except OSError:capture_order=Path(path).name.lower()
    return {'path':str(Path(path).resolve()),'name':Path(path).name,'duration':duration,'has_audio':'Audio:' in text,'width':int(video[2]) if video else 0,'height':int(video[3]) if video else 0,'codec':video[1].strip() if video else 'unknown','fps':float(video[4]) if video else 0,'needs_proxy':bool(video and ('hevc' in video[1].lower() or '10' in video[1].lower())),'creation_time':creation_time,'capture_order':capture_order}

def validate_rendered_mp4(ffmpeg:str,path:str)->dict:
    file=Path(path)
    if not file.exists() or file.stat().st_size<4096:raise ValueError('导出文件为空或不完整')
    meta=probe_media(ffmpeg,str(file))
    if 'h264' not in meta['codec'].lower():raise ValueError('导出视频不是 H.264 编码')
    p=run_hidden([ffmpeg,'-v','error','-sseof','-1','-i',str(file),'-t','0.5','-f','null','-'],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if p.returncode:raise ValueError('MP4 文件尾部校验失败：'+(p.stderr or '')[-300:])
    return meta

def thumbnail(ffmpeg:str,path:str,out:str,at:float=1.0):
    run_hidden([ffmpeg,'-y','-ss',str(max(0,at)),'-i',path,'-frames:v','1','-vf','scale=320:-2',out],capture_output=True)
    return out

def proxy_path(cache_dir:str,path:str)->str:
    src=Path(path);key=hashlib.sha1(f'{src.resolve()}|{src.stat().st_size}|{src.stat().st_mtime_ns}'.encode()).hexdigest()
    return str(Path(cache_dir)/f'{key}.proxy.mp4')

def build_proxy_command(ffmpeg:str,source:str,out:str):
    return [ffmpeg,'-y','-i',source,'-map','0:v:0','-map','0:a:0?','-vf','scale=540:-2','-r','30','-c:v','libx264','-preset','ultrafast','-crf','28','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',out]

def detect_scenes(ffmpeg:str,path:str,duration:float,threshold=.22)->list[float]:
    cmd=[ffmpeg,'-hide_banner','-i',path,'-vf',f"scale=320:-2,select='gt(scene,{threshold})',showinfo",'-an','-f','null','-']
    p=run_hidden(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace')
    cuts=[0.0]+[float(x) for x in re.findall(r'pts_time:([0-9.]+)',p.stderr)]+[float(duration)]
    return sorted(set(round(x,3) for x in cuts if 0<=x<=duration))

def analyze_segment_quality(ffmpeg:str,path:str,start:float,end:float)->dict:
    """Fast low-resolution technical QC used by the offline editor."""
    length=max(.2,end-start);vf='fps=2,scale=192:-2,signalstats,metadata=print,blurdetect=block_width=32:block_height=32:block_pct=80'
    p=run_hidden([ffmpeg,'-hide_banner','-ss',f'{start:.3f}','-t',f'{length:.3f}','-i',path,'-vf',vf,'-an','-f','null','-'],capture_output=True,text=True,encoding='utf-8',errors='replace')
    text=p.stderr or '';y=[float(x) for x in re.findall(r'lavfi\.signalstats\.YAVG=([0-9.]+)',text)];sat=[float(x) for x in re.findall(r'lavfi\.signalstats\.SATAVG=([0-9.]+)',text)];blur=[float(x) for x in re.findall(r'blur mean:\s*([0-9.]+)',text)]
    yavg=sum(y)/len(y) if y else 128;savg=sum(sat)/len(sat) if sat else 55;bavg=sum(blur)/len(blur) if blur else .35
    exposure=max(0,100-abs(yavg-128)*1.15);color=max(0,100-abs(savg-55)*.8);sharpness=max(0,min(100,110-bavg*10))
    return {'exposure':round(exposure,1),'color':round(color,1),'sharpness':round(sharpness,1),'technical':round(.35*exposure+.15*color+.5*sharpness,1)}

def scan_video_quality(ffmpeg:str,path:str)->dict:
    """Decode once at low sampling rate, producing time-addressable exposure/color QC."""
    vf='fps=1,scale=192:-2,signalstats,metadata=print,blurdetect=block_width=32:block_height=32:block_pct=80'
    p=run_hidden([ffmpeg,'-hide_banner','-i',path,'-vf',vf,'-an','-f','null','-'],capture_output=True,text=True,encoding='utf-8',errors='replace')
    blocks=re.split(r'(?=frame:\d+\s+pts:)',p.stderr or '');samples=[]
    for block in blocks:
        tm=re.search(r'pts_time:([0-9.]+)',block);ym=re.search(r'lavfi\.signalstats\.YAVG=([0-9.]+)',block);sm=re.search(r'lavfi\.signalstats\.SATAVG=([0-9.]+)',block)
        if tm and ym and sm:samples.append((float(tm.group(1)),float(ym.group(1)),float(sm.group(1))))
    blur=[float(x) for x in re.findall(r'blur mean:\s*([0-9.]+)',p.stderr or '')];bavg=sum(blur)/len(blur) if blur else 5.5
    return {'samples':samples,'sharpness':max(0,min(100,110-bavg*10))}

def quality_from_scan(scan:dict,start:float,end:float)->dict:
    values=[x for x in scan.get('samples',[]) if start<=x[0]<=end] or scan.get('samples',[])
    yavg=sum(x[1] for x in values)/len(values) if values else 128;savg=sum(x[2] for x in values)/len(values) if values else 55;sharpness=float(scan.get('sharpness',55))
    exposure=max(0,100-abs(yavg-128)*1.15);color=max(0,100-abs(savg-55)*.8)
    return {'exposure':round(exposure,1),'color':round(color,1),'sharpness':round(sharpness,1),'technical':round(.35*exposure+.15*color+.5*sharpness,1)}

@dataclass
class Clip:
    path:str; start:float; end:float; name:str=''; caption:str=''; position:str='bottom'; transition:str='fade'; volume:float=1.0; has_audio:bool=True; id:str=field(default_factory=lambda:str(uuid.uuid4()))
    caption_font:str='微软雅黑';caption_size:int=42;caption_color:str='#FFFFFF';caption_bg:str='#000000';caption_bg_opacity:float=.55
    transition_duration:float=.35;mask_shape:str='none';mask_x:float=.5;mask_y:float=.5;mask_width:float=.72;mask_height:float=.72;mask_feather:float=18.;mask_opacity:float=1.;person_effect_path:str='';person_effect_start:float=0.;person_effect_end:float=0.;person_effect_mode:str='';title_effect:str='';title_text:str='';title_fill_segments:list=field(default_factory=list);title_color:str='#FFFFFF'
    role:str='';reason:str='';capture_order:str='';ai_selected:bool=False
    caption_effect:str='clean';motion_effect:str='none'
    @property
    def duration(self): return round(self.end-self.start,3)
    def validate(self):
        if not self.path or self.start<0 or self.end<=self.start: raise ValueError('片段入点/出点无效')

@dataclass
class OverlayClip:
    path:str;start:float;end:float;timeline_start:float;name:str='';track:int=2
    layout:str='pip_right';mask_shape:str='ellipse';x:float=.76;y:float=.26;width:float=.36;height:float=.30
    feather:float=10.;opacity:float=1.;border:bool=True;id:str=field(default_factory=lambda:str(uuid.uuid4()))
    role:str='overlay';reason:str='';ai_selected:bool=False
    @property
    def duration(self):return round(self.end-self.start,3)
    @property
    def timeline_end(self):return round(self.timeline_start+self.duration,3)
    def validate(self):
        if not self.path or self.start<0 or self.end<=self.start:raise ValueError('叠加片段入点/出点无效')
        if self.timeline_start<0 or not 2<=int(self.track)<=8:raise ValueError('叠加轨道或时间线位置无效')
        if not 0<float(self.width)<=1 or not 0<float(self.height)<=1:raise ValueError('叠加片段尺寸无效')

@dataclass
class SFXCue:
    path:str; start:float; name:str=''; volume:float=.65; effect_id:str=''; id:str=field(default_factory=lambda:str(uuid.uuid4()))
    def validate(self):
        if not self.path or self.start<0:raise ValueError('音效路径或时间无效')

@dataclass
class Project:
    title:str='未命名作品'; clips:list[Clip]=field(default_factory=list); bgm:str=''; bgm_volume:float=.22; ratio:str='9:16'; prompt:str='';edit_plan:dict=field(default_factory=dict);edit_log:list[dict]=field(default_factory=list);sfx:list[SFXCue]=field(default_factory=list);overlays:list[OverlayClip]=field(default_factory=list)
    bgm_id:str='';bgm_ducking:bool=True;bgm_fade_in:float=1.;bgm_fade_out:float=2.
    def add(self,meta): self.clips.append(Clip(meta['path'],0,meta['duration'],meta['name'],has_audio=meta['has_audio']))
    def delete(self,index): self.clips.pop(index)
    def move(self,old,new): self.clips.insert(new,self.clips.pop(old))
    def trim(self,index,start,end):
        c=self.clips[index]; old=(c.start,c.end); c.start=float(start);c.end=float(end)
        try:c.validate()
        except: c.start,c.end=old;raise
    def split(self,index,at):
        c=self.clips[index];at=float(at)
        if at<=c.start+.05 or at>=c.end-.05: raise ValueError('分割点必须位于片段内部')
        data=asdict(c);data.pop('id',None);data['start']=at;second=Clip(**data);c.end=at;self.clips.insert(index+1,second)
    @property
    def duration(self): return round(max(sum(c.duration for c in self.clips),max((c.timeline_end for c in self.overlays),default=0)),3)
    def auto_edit(self,target=30):
        ranked=sorted(self.clips,key=lambda c:(c.duration,c.has_audio),reverse=True);out=[];used=0
        for c in ranked:
            if used>=target:break
            length=min(c.duration,max(.2,min(7,target-used)));mid=(c.start+c.end)/2
            out.append(Clip(c.path,max(c.start,mid-length/2),min(c.end,mid+length/2),c.name,'', 'bottom','fade',c.volume,c.has_audio));used+=out[-1].duration
        self.clips=out
    def save(self,path): write_project(path,self.to_dict())
    def to_dict(self): return {'version':5,'title':self.title,'bgm':self.bgm,'bgm_volume':self.bgm_volume,'bgm_id':self.bgm_id,'bgm_ducking':self.bgm_ducking,'bgm_fade_in':self.bgm_fade_in,'bgm_fade_out':self.bgm_fade_out,'ratio':self.ratio,'prompt':self.prompt,'edit_plan':self.edit_plan,'edit_log':self.edit_log[-100:],'clips':[asdict(c) for c in self.clips],'sfx':[asdict(x) for x in self.sfx],'overlays':[asdict(x) for x in self.overlays]}
    @classmethod
    def from_dict(cls,d):
        p=cls(d.get('title','未命名作品'),bgm=d.get('bgm',''),bgm_volume=d.get('bgm_volume',.22),ratio=d.get('ratio','9:16'),prompt=d.get('prompt',''),edit_plan=d.get('edit_plan') or {},edit_log=list(d.get('edit_log') or []),bgm_id=d.get('bgm_id',''),bgm_ducking=bool(d.get('bgm_ducking',True)),bgm_fade_in=float(d.get('bgm_fade_in',1)),bgm_fade_out=float(d.get('bgm_fade_out',2)));p.clips=[Clip(**c) for c in d.get('clips',[])];p.sfx=[SFXCue(**x) for x in d.get('sfx',[])];p.overlays=[OverlayClip(**x) for x in d.get('overlays',[])];return p
    @classmethod
    def load(cls,path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))

def escape_drawtext(s): return str(s).replace('\\','/').replace(':','\\:').replace("'","’").replace('%','%%')

def _hex(value,default='#FFFFFF'):
    value=str(value or '').strip().upper()
    return value if re.fullmatch(r'#[0-9A-F]{6}',value) else default

def _font_path(name,text=''):
    bundled={'站酷快乐体':'ZCOOLKuaiLe-Regular.ttf','站酷庆科黄油体':'ZCOOLQingKeHuangYou-Regular.ttf','霞鹜文楷':'LXGWWenKai-Regular.ttf'}
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent));custom=root/'assets'/'fonts'/bundled.get(str(name),'')
    if bundled.get(str(name)) and custom.exists():return str(custom).replace('\\','/').replace(':','\\:')
    fonts={'微软雅黑':'msyh.ttc','黑体':'simhei.ttf','宋体':'simsun.ttc','等线':'Deng.ttf','楷体':'simkai.ttf','仿宋':'simfang.ttf','Arial':'arial.ttf','Impact':'impact.ttf','Consolas':'consola.ttf'}
    if re.search(r'[\u3400-\u9fff]',str(text)) and str(name) in ('Arial','Impact','Consolas'):name='微软雅黑'
    return 'C\\:/Windows/Fonts/'+fonts.get(str(name),fonts['微软雅黑'])

def wrap_caption(text,max_chars=16):
    text=' '.join(str(text).strip().split())
    if len(text)<=max_chars:return text
    lines=[]
    while text:
        cut=min(max_chars,len(text))
        if ' ' in text[:cut+1]:cut=max(1,text.rfind(' ',0,cut+1))
        lines.append(text[:cut].strip());text=text[cut:].strip()
    return '\n'.join(lines[:2])

def build_render_command(ffmpeg:str,project:Project,out:str,width=720,height=1280,quality='standard'):
    if not project.clips: raise ValueError('时间线为空')
    args=[ffmpeg,'-y'];
    for c in project.clips:
        c.validate();effect=str(getattr(c,'person_effect_path','') or '');base_start=float(getattr(c,'person_effect_start',0));base_end=float(getattr(c,'person_effect_end',0));use_effect=bool(effect and Path(effect).exists() and c.start>=base_start-.001 and c.end<=base_end+.001);source=effect if use_effect else c.path;seek=max(0,c.start-base_start) if use_effect else c.start;args += ['-ss',f'{seek:.3f}','-t',f'{c.duration:.3f}','-i',source]
    next_input=len(project.clips);overlay_inputs=[]
    for overlay in sorted(getattr(project,'overlays',[]) or [],key=lambda x:(int(x.track),float(x.timeline_start))):
        overlay.validate()
        if not Path(overlay.path).exists():continue
        args+=['-ss',f'{overlay.start:.3f}','-t',f'{overlay.duration:.3f}','-i',overlay.path];overlay_inputs.append((next_input,overlay));next_input+=1
    title_inputs={}
    for clip_index,c in enumerate(project.clips):
        items=[]
        for segment in getattr(c,'title_fill_segments',[]) or []:
            path=str(segment.get('path') or '');start=max(0,float(segment.get('start',0)));duration=max(.08,float(segment.get('duration',.4)))
            if not path or not Path(path).exists():continue
            args+=['-ss',f'{start:.3f}','-t',f'{duration:.3f}','-i',path];items.append((next_input,duration));next_input+=1
        if items:title_inputs[clip_index]=items
    bgm_index=None
    if project.bgm: bgm_index=next_input;next_input+=1;args+=['-stream_loop','-1','-i',project.bgm]
    sfx_inputs=[]
    for cue in getattr(project,'sfx',[]) or []:
        if not cue.path or not Path(cue.path).exists():continue
        cue.validate();index=next_input;next_input+=1;args+=['-i',cue.path];sfx_inputs.append((index,cue))
    filters=[]
    for i,c in enumerate(project.clips):
        vf=f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,settb=AVTB'
        motion=str(getattr(c,'motion_effect','none') or 'none')
        if motion=='slow_push':vf+=f",scale={width*108//100}:{height*108//100},crop={width}:{height}:'(iw-ow)/2*(t/{max(.2,c.duration):.3f})':'(ih-oh)/2*(t/{max(.2,c.duration):.3f})'"
        elif motion=='punch_in':vf+=f",scale={width*114//100}:{height*114//100},crop={width}:{height}:'(iw-ow)/2*(1-exp(-7*t))':'(ih-oh)/2*(1-exp(-7*t))'"
        elif motion=='handheld':vf+=f",scale={width+20}:{height+20},crop={width}:{height}:'10+5*sin(17*t)':'10+4*cos(13*t)'"
        mask=str(getattr(c,'mask_shape','none'))
        if mask=='cinema':vf+=f',drawbox=x=0:y=0:w=iw:h=ih*0.115:color=black:t=fill,drawbox=x=0:y=ih*0.885:w=iw:h=ih*0.115:color=black:t=fill'
        elif mask in ('spotlight','ellipse','diamond','vertical_strip','split_left','split_right'):
            cx=max(0,min(1,float(getattr(c,'mask_x',.5))))*width;cy=max(0,min(1,float(getattr(c,'mask_y',.5))))*height
            rx=max(20,float(getattr(c,'mask_width',.72))*width/2);ry=rx if mask=='spotlight' else max(20,float(getattr(c,'mask_height',.72))*height/2)
            feather=max(1,float(getattr(c,'mask_feather',18)));opacity=max(0,min(1,float(getattr(c,'mask_opacity',1))))
            filters.append(vf+f',split=2[bg{i}][fg{i}]')
            filters.append(f'[bg{i}]boxblur=22:2,eq=brightness=-0.08:saturation=0.78[blur{i}]')
            if mask=='diamond':alpha=f'clip((1-abs(X-{cx:.2f})/{rx:.2f}-abs(Y-{cy:.2f})/{ry:.2f})*{min(rx,ry):.2f}/{feather:.2f}*255*{opacity:.3f},0,255)'
            elif mask=='vertical_strip':alpha=f'clip((1-abs(X-{cx:.2f})/{rx:.2f})*{rx:.2f}/{feather:.2f}*255*{opacity:.3f},0,255)'
            elif mask=='split_left':alpha=f'clip(({cx:.2f}-X)/{feather:.2f}*255*{opacity:.3f},0,255)'
            elif mask=='split_right':alpha=f'clip((X-{cx:.2f})/{feather:.2f}*255*{opacity:.3f},0,255)'
            else:alpha=f'clip((1-hypot((X-{cx:.2f})/{rx:.2f},(Y-{cy:.2f})/{ry:.2f}))*{min(rx,ry):.2f}/{feather:.2f}*255*{opacity:.3f},0,255)'
            filters.append(f"[fg{i}]format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}'[mask{i}]")
            filters.append(f'[blur{i}][mask{i}]overlay=shortest=1[pre{i}]');vf=f'[pre{i}]null'
        elif mask=='privacy_blur':
            cx=max(0,min(1,float(getattr(c,'mask_x',.5))))*width;cy=max(0,min(1,float(getattr(c,'mask_y',.5))))*height;rx=max(20,float(getattr(c,'mask_width',.35))*width/2);ry=max(20,float(getattr(c,'mask_height',.25))*height/2);feather=max(1,float(getattr(c,'mask_feather',18)));opacity=max(0,min(1,float(getattr(c,'mask_opacity',1))));alpha=f'clip((1-hypot((X-{cx:.2f})/{rx:.2f},(Y-{cy:.2f})/{ry:.2f}))*{min(rx,ry):.2f}/{feather:.2f}*255*{opacity:.3f},0,255)';filters.append(vf+f',split=2[base{i}][privacy{i}]');filters.append(f'[privacy{i}]boxblur=28:3,format=rgba,geq=r=\'r(X,Y)\':g=\'g(X,Y)\':b=\'b(X,Y)\':a=\'{alpha}\'[pmask{i}]');filters.append(f'[base{i}][pmask{i}]overlay=shortest=1[pre{i}]');vf=f'[pre{i}]null'
        elif mask=='vignette':vf+=',vignette=PI/5:eval=frame'
        elif mask=='portrait_card':
            mw=max(80,int(width*max(.2,min(1,float(getattr(c,'mask_width',.72))))));mh=max(80,int(height*max(.2,min(1,float(getattr(c,'mask_height',.72))))));x=int(width*max(0,min(1,float(getattr(c,'mask_x',.5))))-mw/2);y=int(height*max(0,min(1,float(getattr(c,'mask_y',.5))))-mh/2)
            x=max(0,min(width-mw,x));y=max(0,min(height-mh,y));filters.append(vf+f',split=2[bg{i}][fg{i}]');filters.append(f'[bg{i}]boxblur=22:2,eq=brightness=-0.08:saturation=0.78[blur{i}]');filters.append(f'[fg{i}]crop={mw}:{mh}:{x}:{y},drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=4[card{i}]');filters.append(f'[blur{i}][card{i}]overlay={x}:{y}:shortest=1[pre{i}]');vf=f'[pre{i}]null'
        title_effect=str(getattr(c,'title_effect','') or '');title_text=str(getattr(c,'title_text','') or '')
        if title_effect=='text_window' and title_text and i in title_inputs:
            fill_labels=[]
            for j,(input_index,_) in enumerate(title_inputs[i]):
                label=f'tfill{i}_{j}';filters.append(f'[{input_index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30,settb=AVTB,setpts=PTS-STARTPTS[{label}]');fill_labels.append(f'[{label}]')
            if len(fill_labels)>1:filters.append(''.join(fill_labels)+f'concat=n={len(fill_labels)}:v=1:a=0,trim=duration={c.duration:.3f},setpts=PTS-STARTPTS[tgallery{i}]');gallery=f'[tgallery{i}]'
            else:gallery=fill_labels[0]
            font=_font_path('Impact',title_text);safe=escape_drawtext(title_text);size=max(72,round(height*.20))
            filters.append(vf+f',eq=brightness=-0.05:saturation=0.92[titlebase{i}]')
            filters.append(f'color=c=black:s={width}x{height}:d={c.duration:.3f}:r=30,format=gray,drawtext=fontfile=\'{font}\':text=\'{safe}\':fontcolor=white:fontsize={size}:x=(w-text_w)/2:y=(h-text_h)/2[titlematte{i}]')
            filters.append(f'{gallery}format=rgba[titlefillrgba{i}]');filters.append(f'[titlefillrgba{i}][titlematte{i}]alphamerge[titleletters{i}]')
            filters.append(f'[titlebase{i}][titleletters{i}]overlay=shortest=1,drawtext=fontfile=\'{font}\':text=\'{safe}\':fontcolor=white@0.03:fontsize={size}:borderw=4:bordercolor=white@0.92:x=(w-text_w)/2:y=(h-text_h)/2[titlepre{i}]');vf=f'[titlepre{i}]null'
        elif title_effect=='bounce' and title_text:
            font=_font_path('黑体',title_text);safe=escape_drawtext(title_text);fg=_hex(getattr(c,'title_color','#FFFFFF'))[1:]
            vf+=f",drawtext=fontfile='{font}':text='{safe}':fontcolor=0x{fg}:fontsize='h*0.105*(1+0.34*exp(-4*t)*abs(sin(11*t)))':borderw=4:bordercolor=black@0.65:box=1:boxcolor=0x111827@0.34:boxborderw=20:x=(w-text_w)/2:y=(h-text_h)/2"
        if c.caption:
            y={'top':'h*0.10','center':'(h-text_h)/2','lower_third':'h*0.72','bottom':'h*0.86'}.get(c.position,'h*0.86');size=max(18,min(210,round(int(getattr(c,'caption_size',42))*width/720)));fg=_hex(getattr(c,'caption_color','#FFFFFF'))[1:];bg=_hex(getattr(c,'caption_bg','#000000'),'#000000')[1:];alpha=max(0,min(1,float(getattr(c,'caption_bg_opacity',.55))))
            effect=str(getattr(c,'caption_effect','clean') or 'clean');font_size=str(size);x='(w-text_w)/2';border=2;box=1
            if effect in ('jelly','pop'):font_size=f"{size}*(1+0.24*exp(-5*t)*abs(sin(12*t)))";border=4
            elif effect=='typewriter':x="(w-text_w)/2-2*sin(23*t)";box=0
            elif effect=='stamp':font_size=f"{size}*(1+0.12*lt(t,0.16))";border=5
            elif effect=='kinetic':x="(w-text_w)/2+12*sin(6*t)";border=3
            elif effect=='minimal':box=0
            elif effect=='highlight':border=4
            vf+=f",drawtext=fontfile='{_font_path(getattr(c,'caption_font','微软雅黑'),c.caption)}':text='{escape_drawtext(wrap_caption(c.caption,max(10,round(width/45))))}':fontcolor=0x{fg}:fontsize='{font_size}':borderw={border}:bordercolor=black@0.85:box={box}:boxcolor=0x{bg}@{alpha:.2f}:boxborderw=14:line_spacing=8:x='{x}':y={y}"
        filters.append(vf+f',settb=1/30,setpts=N/(30*TB),fps=30[v{i}]')
        if c.has_audio: filters.append(f'[{i}:a]aresample=48000,volume={c.volume},asetpts=PTS-STARTPTS[a{i}]')
        else: filters.append(f'anullsrc=r=48000:cl=stereo:d={c.duration:.3f}[a{i}]')
    if len(project.clips)==1:vmap='[v0]';amap='[a0]';render_duration=project.clips[0].duration
    else:
        vprev='v0';aprev='a0';render_duration=project.clips[0].duration
        xfade_map={'fade':'fade','dissolve':'dissolve','wipe_left':'wipeleft','wipe_right':'wiperight','slide_left':'slideleft','slide_right':'slideright','circle':'circleopen','smooth':'smoothleft','pip_zoom':'zoomin','tear_left':'hlslice','tear_right':'hrslice','pixelize':'pixelize','squeeze':'squeezeh','radial':'radial','fade_black':'fadeblack','fade_white':'fadewhite','cover_left':'coverleft','reveal_right':'revealright'}
        for i,c in enumerate(project.clips[1:],1):
            kind=str(getattr(c,'transition','none'));d=max(.001,min(float(getattr(c,'transition_duration',.35)),c.duration*.35,project.clips[i-1].duration*.35)) if kind!='none' else .001;offset=max(.001,render_duration-d);vout=f'vx{i}';aout=f'ax{i}'
            filters.append(f'[{vprev}][v{i}]xfade=transition={xfade_map.get(kind,"fade")}:duration={d:.3f}:offset={offset:.3f},settb=1/30,setpts=N/(30*TB),fps=30[{vout}]');filters.append(f'[{aprev}][a{i}]acrossfade=d={d:.3f}:c1=tri:c2=tri[{aout}]');vprev=vout;aprev=aout;render_duration+=c.duration-d
        vmap=f'[{vprev}]';amap=f'[{aprev}]'
    project_duration=float(project.duration)
    if project_duration>render_duration+.001:
        extra=project_duration-render_duration;filters.append(f'{vmap}tpad=stop_mode=clone:stop_duration={extra:.3f}[vbasepad]');filters.append(f'{amap}apad=pad_dur={extra:.3f}[abasepad]');vmap='[vbasepad]';amap='[abasepad]';render_duration=project_duration
    for n,(input_index,overlay) in enumerate(overlay_inputs):
        ow=max(32,round(width*float(overlay.width)/2)*2);oh=max(32,round(height*float(overlay.height)/2)*2);ox=round(width*float(overlay.x)-ow/2);oy=round(height*float(overlay.y)-oh/2);opacity=max(0,min(1,float(overlay.opacity)));feather=max(1,float(overlay.feather));source=f'ovsrc{n}';placed=f'ovplaced{n}';out_label=f'ovmix{n}'
        chain=f'[{input_index}:v]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},setsar=1,fps=30,format=rgba'
        if overlay.border:chain+=f',drawbox=x=0:y=0:w=iw:h=ih:color=white@0.82:t={max(2,round(min(ow,oh)*.012))}'
        if str(overlay.mask_shape) in ('ellipse','circle'):
            rx=ow/2;ry=(ow/2 if str(overlay.mask_shape)=='circle' else oh/2);chain+=f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='clip((1-hypot((X-{ow/2:.2f})/{rx:.2f},(Y-{oh/2:.2f})/{ry:.2f}))*{min(rx,ry):.2f}/{feather:.2f}*255*{opacity:.3f},0,255)'"
        else:chain+=f',colorchannelmixer=aa={opacity:.3f}'
        filters.append(chain+f',setpts=PTS-STARTPTS+{float(overlay.timeline_start):.3f}/TB[{source}]')
        filters.append(f'{vmap}[{source}]overlay=x={ox}:y={oy}:eof_action=pass:shortest=0:enable=\'between(t,{float(overlay.timeline_start):.3f},{float(overlay.timeline_end):.3f})\'[{out_label}]');vmap=f'[{out_label}]'
    if bgm_index is not None:
        fade_in=max(0,min(float(getattr(project,'bgm_fade_in',1)),render_duration/2));fade_out=max(0,min(float(getattr(project,'bgm_fade_out',2)),render_duration/2));fade_start=max(0,render_duration-fade_out)
        filters.append(f'[{bgm_index}:a]atrim=0:{render_duration:.3f},aresample=48000,volume={project.bgm_volume},afade=t=in:st=0:d={fade_in:.3f},afade=t=out:st={fade_start:.3f}:d={fade_out:.3f}[bgm]')
        if bool(getattr(project,'bgm_ducking',True)):
            filters += [f'{amap}asplit=2[programmix][programside]',f'[bgm][programside]sidechaincompress=threshold=0.025:ratio=7:attack=18:release=320[bgmduck]',f'[programmix][bgmduck]amix=inputs=2:duration=first:dropout_transition=2[aout]']
        else:filters.append(f'{amap}[bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]')
        amap='[aout]'
    for n,(index,cue) in enumerate(sfx_inputs):
        delay=max(0,round(float(cue.start)*1000));volume=max(0,min(2,float(cue.volume)));sfx_label=f'sfx{n}';mix_label=f'sfxmix{n}'
        filters.append(f'[{index}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={delay}:all=1,volume={volume:.3f}[{sfx_label}]')
        filters.append(f'{amap}[{sfx_label}]amix=inputs=2:duration=first:dropout_transition=0[{mix_label}]');amap=f'[{mix_label}]'
    crf='18' if quality=='high' else '23'
    return args+['-filter_complex',';'.join(filters),'-map',vmap,'-map',amap,'-map_metadata','-1','-write_tmcd','0','-c:v','libx264','-preset','medium','-crf',crf,'-profile:v','main','-level:v','4.1','-pix_fmt','yuv420p','-r','30','-fps_mode','cfr','-tag:v','avc1','-video_track_timescale','30000','-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart','-brand','mp42',out]

