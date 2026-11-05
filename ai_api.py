from __future__ import annotations
import base64,ctypes,json,math,mimetypes,os,re,subprocess,tempfile,urllib.request,urllib.error,uuid,warnings
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from visual_sampling import build_contact_sheets, media_key

class DATA_BLOB(ctypes.Structure):_fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_byte))]
def _blob(data:bytes):
    buf=ctypes.create_string_buffer(data);return DATA_BLOB(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte))),buf
def protect_secret(text:str)->str:
    if not text:return ''
    if os.name!='nt':return base64.b64encode(text.encode()).decode()
    src,keep=_blob(text.encode());out=DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src),'LingJianAI',None,None,None,0,ctypes.byref(out)):raise ctypes.WinError()
    try:return base64.b64encode(ctypes.string_at(out.pbData,out.cbData)).decode()
    finally:ctypes.windll.kernel32.LocalFree(out.pbData)
def unprotect_secret(value:str)->str:
    if not value:return ''
    raw=base64.b64decode(value)
    if os.name!='nt':return raw.decode()
    src,keep=_blob(raw);out=DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src),None,None,None,None,0,ctypes.byref(out)):raise ctypes.WinError()
    try:return ctypes.string_at(out.pbData,out.cbData).decode()
    finally:ctypes.windll.kernel32.LocalFree(out.pbData)

@dataclass
class APIConfig:
    base_url:str='https://api.openai.com';api_key:str='';model:str='gpt-5-mini';transcription_model:str='gpt-4o-mini-transcribe';timeout:int=180
    refine_highlights:bool=False
    def endpoint(self,path):return self.base_url.rstrip('/')+'/v1/'+path.lstrip('/')

def _request(url,data,headers,timeout):
    req=urllib.request.Request(url,data=data,headers=headers,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body=e.read().decode('utf-8','replace');raise RuntimeError(f'API HTTP {e.code}: {body[:800]}')
    except Exception as e:raise RuntimeError('API 连接失败：'+str(e))

def transcribe_audio(cfg:APIConfig,audio_path:str)->str:
    boundary='----LingJian'+uuid.uuid4().hex;parts=[]
    def field(name,value):parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()])
    field('model',cfg.transcription_model);field('response_format','json');file=Path(audio_path);raw=file.read_bytes();mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream';parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode(),raw,b'\r\n',f'--{boundary}--\r\n'.encode()])
    result=_request(cfg.endpoint('audio/transcriptions'),b''.join(parts),{'Authorization':'Bearer '+cfg.api_key,'Content-Type':f'multipart/form-data; boundary={boundary}'},cfg.timeout)
    return result.get('text','')

def _output_text(result):
    if isinstance(result.get('output_text'),str):return result['output_text']
    for block in result.get('output',[]):
        for content in block.get('content',[]):
            if isinstance(content.get('text'),str):return content['text']
    raise RuntimeError('API 响应中没有文本结果')

STAGE_ORDER={'intro':0,'ingredients':1,'prep':2,'cook':3,'plate':4,'taste':5,'outro':6,'other':7}

def _visual_content(sheets:list[dict])->tuple[str,list[dict]]:
    maps=[];images=[]
    for index,sheet in enumerate(sheets,1):
        maps.append(f"图{index}："+', '.join(f"格{i+1}={frame['time']:.6f}s" for i,frame in enumerate(sheet['frames'])))
        image='data:image/jpeg;base64,'+base64.b64encode(Path(sheet['path']).read_bytes()).decode()
        images.append({'type':'input_image','image_url':image})
    return '\n'.join(maps),images

def request_highlights(cfg:APIConfig,transcript:str,contact_sheet:str|list[dict],duration:float,user_prompt:str)->list[dict]:
    item_props={'start':{'type':'number'},'end':{'type':'number'},'score':{'type':'number'},'reason':{'type':'string'},'caption':{'type':'string'},'role':{'type':'string','enum':['hook','setup','development','climax','outro','broll']},'stage':{'type':'string','enum':list(STAGE_ORDER)},'chapter':{'type':'string'},'shot_type':{'type':'string'},'motion':{'type':'number'},'stability':{'type':'number'},'audio_value':{'type':'number'},'visual_signature':{'type':'string'}};schema={'type':'object','properties':{'summary':{'type':'string'},'segments':{'type':'array','items':{'type':'object','properties':item_props,'required':list(item_props),'additionalProperties':False}}},'required':['summary','segments'],'additionalProperties':False}
    if isinstance(contact_sheet,str):
        # Compatibility with callers supplying a legacy 6x6 sheet.
        interval=max(.5,duration/36)
        frame_map='旧版均匀采样，时间为估计值：'+', '.join(f'{i+1}≈{min(duration,(i+.5)*interval):.1f}s' for i in range(36))
        images=[{'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(Path(contact_sheet).read_bytes()).decode()}]
    else:
        frame_map,images=_visual_content(contact_sheet)
    prompt=f'''你是专业纪录片与短视频剪辑师。每张接触表按从左到右、从上到下排列，各格原素材时间为：\n{frame_map}\n抽帧不是连续视频；未展示的间隔没有视觉证据，不能声称看到了其中的瞬间或精确动作边界。语音转写没有逐句时间码，不能据此编造某镜头的对白；无法确定归属时字幕留空。分析画面与语音，从 {duration:.2f} 秒素材中找出可独立剪辑的候选镜头。
每个候选必须尽量落在动作或语句边界，保留动作前后约0.15秒余量；通常1.2到5.5秒，除非完整语义确实需要更长。拒绝严重抖动、失焦、重复构图、遮挡、无意义停顿、开关机动作。分别评估运动强度motion、稳定性stability、声音叙事价值audio_value（0-100），并给出简短visual_signature用于跨素材去重。role只能是hook/setup/development/climax/outro/broll。caption必须优先使用该时段真实说话内容的忠实短句（中文通常不超过18字），不得编造对白；没有语音时留空，除非用户明确要求旁白或标题。
stage必须判断事件阶段：intro开场、ingredients食材/对象展示、prep切洗准备、cook烹饪/执行、plate装盘/结果展示、taste试吃/验收、outro收尾、other其他。chapter写明当前事件章节。对于教程、做饭、改造、开箱等步骤型素材，成品画面可作为开头钩子，但其余步骤的stage必须按真实事件判断，不能因画面更精彩而改变阶段。
候选镜头必须至少贡献一种价值：新事实、新动作状态、新地点、新情绪或因果转折。只有重复动作、口头填充词、离场等待、镜头尚未摆稳的片段即使清晰也要降分。优先保留目标陈述、困难/意外、关键变化、人物反应、结果验证；动作过程应选“开始—关键变化—结果”，不要把同一动作切成多段反复返回。
用户要求：{user_prompt}\n语音转写：{transcript or '无可用语音'}\n画面超过60秒时必须覆盖开头、中段、后段和收尾的全部事件章节，不能只挑前半段；为120秒成片准备足够数量且时间分布均匀的候选。只返回可进入非线性时间线的候选，不要在单素材内决定最终顺序。'''
    body={'model':cfg.model,'input':[{'role':'user','content':[{'type':'input_text','text':prompt},*images]}],'text':{'format':{'type':'json_schema','name':'video_highlights','strict':True,'schema':schema}}}
    result=_request(cfg.endpoint('responses'),json.dumps(body).encode(),{'Authorization':'Bearer '+cfg.api_key,'Content-Type':'application/json'},cfg.timeout);text=_output_text(result).strip();text=re.sub(r'^```(?:json)?|```$','',text).strip();data=json.loads(text);valid=[]
    for s in data.get('segments',[]):
        if not all(math.isfinite(float(s[k])) for k in ('start','end','score')):continue
        a=max(0,float(s['start']));b=min(duration,float(s['end']))
        if b-a>=.5:valid.append({'start':round(a,3),'end':round(b,3),'score':max(0,min(100,float(s['score']))),'reason':str(s['reason']),'caption':str(s['caption']),'role':str(s.get('role','broll')),'stage':str(s.get('stage','other')),'chapter':str(s.get('chapter','')),'shot_type':str(s.get('shot_type','')),'motion':max(0,min(100,float(s.get('motion',50)))),'stability':max(0,min(100,float(s.get('stability',50)))),'audio_value':max(0,min(100,float(s.get('audio_value',0)))),'visual_signature':str(s.get('visual_signature',''))})
    return sorted(valid,key=lambda x:x['score'],reverse=True)

def extract_assets(ffmpeg:str,source:str,folder:str,duration:float):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);audio=folder/(media_key(source,duration)+'.m4a')
    flags=0x08000000 if os.name=='nt' else 0
    if not audio.exists() or audio.stat().st_size<1000:
        with tempfile.TemporaryDirectory(prefix='audio-',dir=folder) as scratch:
            temporary=Path(scratch)/'audio.m4a'
            p=subprocess.run([ffmpeg,'-nostdin','-y','-i',source,'-map','0:a:0?','-vn','-ac','1','-ar','16000','-c:a','aac','-b:a','64k',str(temporary)],capture_output=True,creationflags=flags,timeout=180)
            if p.returncode:
                error=p.stderr.decode('utf-8','replace')
                if 'does not contain any stream' not in error:raise RuntimeError('无法提取 AI 音频：'+error[-800:])
            elif temporary.exists():os.replace(temporary,audio)
    return str(audio),build_contact_sheets(ffmpeg,source,str(folder),duration)

def refine_highlight_boundaries(ffmpeg:str,cfg:APIConfig,source:str,duration:float,folder:str,segments:list[dict])->list[dict]:
    """One optional request: eight frames around at most two top candidates."""
    windows=[];samples=[]
    for index,segment in enumerate(segments[:2]):
        a=max(0,segment['start']-1);b=min(duration-.001,segment['end']+1)
        if b<=a:continue
        windows.append({'candidate_index':index,'start':segment['start'],'end':segment['end'],'window_start':a,'window_end':b})
        samples.extend({'time':a+(b-a)*i/3,'kind':'refinement','score':0.0} for i in range(4))
    if not windows:return segments
    sheets=build_contact_sheets(ffmpeg,source,folder,duration,samples=sorted(samples,key=lambda s:s['time']))
    frame_map,images=_visual_content(sheets)
    prompt=('仅复核已有候选的剪切边界，不添加候选、不修改叙事角色或字幕。所有时间都是原素材秒数。'
            '仅在给出的窗口内调整，任一边界最多移动1秒；抽帧不足以证明精确动作边界时保留原值。'
            '候选与允许窗口：'+json.dumps(windows,ensure_ascii=False)+'\n画面：\n'+frame_map)
    props={'candidate_index':{'type':'integer'},'start':{'type':'number'},'end':{'type':'number'}}
    schema={'type':'object','properties':{'refinements':{'type':'array','items':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}}},'required':['refinements'],'additionalProperties':False}
    body={'model':cfg.model,'input':[{'role':'user','content':[{'type':'input_text','text':prompt},*images]}],'text':{'format':{'type':'json_schema','name':'video_boundaries','strict':True,'schema':schema}}}
    response=_request(cfg.endpoint('responses'),json.dumps(body).encode(),{'Authorization':'Bearer '+cfg.api_key,'Content-Type':'application/json'},cfg.timeout)
    data=json.loads(_output_text(response));out=[dict(segment) for segment in segments];seen=set()
    allowed={window['candidate_index']:window for window in windows}
    for change in data.get('refinements',[]):
        index=change.get('candidate_index')
        if type(index) is not int or index not in allowed or index in seen:continue
        a=float(change['start']);b=float(change['end']);window=allowed[index]
        if (not math.isfinite(a) or not math.isfinite(b) or b-a<.5
                or not window['window_start']<=a<b<=window['window_end']
                or abs(a-window['start'])>1 or abs(b-window['end'])>1):continue
        out[index].update(start=round(a,3),end=round(b,3));seen.add(index)
    return out

def analyze_video(ffmpeg:str,cfg:APIConfig,source:str,duration:float,folder:str,prompt:str,*,report:dict|None=None):
    if not cfg.api_key:raise RuntimeError('请先在“AI 接口”中填写 API Key')
    audio,sheets=extract_assets(ffmpeg,source,folder,duration);transcript=transcribe_audio(cfg,audio) if Path(audio).is_file() and Path(audio).stat().st_size>1000 else ''
    segments=request_highlights(cfg,transcript,sheets,duration,prompt)
    if cfg.refine_highlights and segments:
        try:segments=refine_highlight_boundaries(ffmpeg,cfg,source,duration,folder,segments)
        except Exception as exc:
            message='候选二次精看未完成，已保留首轮方案：'+str(exc)
            if report is not None:report.setdefault('warnings',[]).append(message)
            else:warnings.warn(message,RuntimeWarning)
    return segments

def _overlap(a,b):
    if a['source_index']!=b['source_index']:return 0.0
    shared=max(0,min(a['end'],b['end'])-max(a['start'],b['start']))
    gap=max(a['start'],b['start'])-min(a['end'],b['end'])
    if gap<.75:return 1.0
    return shared/max(.001,min(a['end']-a['start'],b['end']-b['start']))

def _stage_of(s):
    stage=str(s.get('stage','other')).lower()
    if stage in STAGE_ORDER:return stage
    text=' '.join(str(s.get(k,'')) for k in ('reason','caption','chapter','visual_signature')).lower()
    terms=[('taste','试吃 吃一口 品尝 验收 tasting eating'),('plate','装盘 成品 出锅 完成 finished result'),('cook','下锅 炒 煎 煮 烤 火候 调味 cooking pan pot'),('prep','切 洗 剥 腌 搅拌 准备 刀 洋葱 chopping cutting'),('ingredients','食材 原料 配料 ingredients'),('outro','结尾 收尾 再见 outro'),('intro','开场 介绍 intro')]
    for value,words in terms:
        if any(word in text for word in words.split()):return value
    return 'other'

def _source_key(s,analyses):
    meta=analyses[int(s['source_index'])]['meta'];return (str(meta.get('capture_order') or meta.get('creation_time') or meta.get('name','')).lower(),float(s.get('start',0)))

def _reliable_capture_lock(sequence,analyses,user_prompt=''):
    """Prefer camera chronology when filenames carry real capture timestamps.

    Stage labels are semantic guesses.  A DJI YYYYMMDDhhmmss filename is stronger
    evidence for event order and must not be overridden by a guessed food stage.
    """
    text=(user_prompt or '').lower()
    if any(x in text for x in ('按顺序拍','拍摄顺序','真实顺序','时间顺序','严格顺序','chronological')):return True
    source_indexes=sorted({int(s['source_index']) for s in sequence})
    if len(source_indexes)<2:return False
    orders=[str(analyses[i]['meta'].get('capture_order') or '') for i in source_indexes]
    return len(set(orders))==len(orders) and all(re.fullmatch(r'\d{14}',x) for x in orders)

def narrative_mode(user_prompt:str,candidates:list[dict]|None=None)->str:
    text=(user_prompt or '').lower()
    if any(x in text for x in ('美食','做饭','烹饪','菜谱','教程','步骤','改造','制作过程','food','cooking','tutorial')):return 'procedural'
    stages={_stage_of(x) for x in (candidates or [])}-{ 'other','intro','outro' }
    if 'prep' in stages and ('cook' in stages or 'plate' in stages) and len(stages)>=3:return 'procedural'
    if any(x in text for x in ('旅行','纪录片','时间推进','事件顺序','chronological')):return 'chronological'
    return 'creative'

def story_blueprint(user_prompt:str,target:float)->str:
    """A duration-aware beat sheet distilled into deterministic prompt text."""
    text=(user_prompt or '').lower();mode=narrative_mode(user_prompt)
    if any(x in text for x in ('剧情','短剧','反转','drama')):mode='drama'
    elif mode=='creative' and any(x in text for x in ('生活记录','日常','vlog')):mode='life'
    bucket=30 if target<=40 else 60 if target<=90 else 120
    templates={
        ('procedural',30):'0-2秒成品/反应钩子；2-6秒目标与核心食材；6-18秒关键准备；18-25秒烹饪变化；25-30秒装盘与试吃回报。',
        ('procedural',60):'0-3秒成品/反应钩子；3-10秒目标与食材；10-27秒准备和一个真实困难；27-45秒烹饪状态变化；45-53秒装盘；53-60秒试吃与简短结论。',
        ('procedural',120):'0-4秒成品/反应钩子；4-18秒目标与食材；18-48秒分阶段准备；48-88秒烹饪与关键变化；88-105秒收汁/装盘；105-120秒试吃、评价和回收开场。',
        ('chronological',30):'0-2秒目的地/意外钩子；2-6秒人物、地点和目标；6-20秒行程推进与一次小障碍；20-27秒发现/到达；27-30秒感受回收。',
        ('chronological',60):'0-3秒结果或问题钩子；3-10秒人物、地点、目标；10-28秒出发与环境建立；28-43秒障碍/变化；43-55秒发现与回报；55-60秒感受或下一程。',
        ('chronological',120):'0-4秒高价值钩子；4-18秒人物、路线和目标；18-48秒行程章节一；48-78秒障碍及应对；78-105秒核心发现/体验；105-120秒结果、感受和首尾呼应。',
        ('life',30):'0-2秒反差/结果钩子；2-7秒今天的小目标；7-20秒两到三个有效行动与一次变化；20-27秒结果；27-30秒人物反应回收。',
        ('life',60):'0-3秒反差或一句关键话；3-12秒人物处境与小目标；12-35秒行动递进；35-48秒意外/困难；48-56秒结果；56-60秒情绪回收。',
        ('life',120):'0-4秒结果/冲突钩子；4-20秒人物处境与目标；20-55秒行动章节一；55-85秒困难与调整；85-108秒结果；108-120秒反思、笑点或首尾呼应。',
        ('drama',30):'0-2秒冲突句/异常结果；2-7秒人物欲望；7-18秒尝试升级；18-25秒反转；25-30秒后果与回扣。',
        ('drama',60):'0-3秒冲突钩子；3-12秒人物与欲望；12-32秒两次递进尝试；32-46秒代价/误会；46-55秒反转；55-60秒回扣开场。',
        ('drama',120):'0-4秒冲突钩子；4-20秒人物、欲望、规则；20-55秒尝试升级；55-80秒失败与代价；80-105秒反转/选择；105-120秒结果与首尾呼应。',
    }
    if mode=='creative':mode='life'
    return templates.get((mode,bucket),templates[('life',bucket)])

def _candidate_rank(s):
    caption=str(s.get('caption','')).strip().lower();reason=str(s.get('reason','')).lower()
    filler_terms=('刚刚离开','等一下','稍等','没什么','不知道说什么','嗯嗯','呃','等待','空镜','误触')
    filler=any(x in caption or x in reason for x in filler_terms)
    stage_bonus=8 if _stage_of(s) in ('ingredients','cook','plate','taste') else 0
    return float(s.get('score',0))+.12*float(s.get('stability',50))+.1*float(s.get('audio_value',0))+stage_bonus-(32 if filler else 0)

def _procedural_sort(body,analyses):
    chronological=sorted(body,key=lambda s:_source_key(s,analyses));known=[(i,STAGE_ORDER[_stage_of(s)]) for i,s in enumerate(chronological) if _stage_of(s)!='other'];effective={}
    for i,s in enumerate(chronological):
        stage=_stage_of(s)
        if stage!='other':effective[id(s)]=float(STAGE_ORDER[stage]);continue
        previous=[(j,r) for j,r in known if j<i];following=[(j,r) for j,r in known if j>i]
        if previous and following:
            pj,pr=previous[-1];nj,nr=following[0];effective[id(s)]=pr+(nr-pr)*(i-pj)/max(1,nj-pj)
        elif previous:effective[id(s)]=previous[-1][1]+.25
        elif following:effective[id(s)]=following[0][1]-.25
        else:effective[id(s)]=3.0
    return sorted(chronological,key=lambda s:(effective[id(s)],_source_key(s,analyses)))

def enforce_continuity(sequence:list[dict],analyses:list[dict],user_prompt:str='',target:float=0)->list[dict]:
    """Deterministic safety pass: a model may select shots, but cannot reverse an event."""
    if not sequence:return []
    unique=[]
    for raw in sequence:
        s=dict(raw);s['stage']=_stage_of(s)
        if any(_overlap(s,x)>.32 for x in unique):continue
        unique.append(s)
    mode=narrative_mode(user_prompt,unique)
    hook=next((x for x in unique if x.get('role')=='hook'),None)
    if mode=='procedural':
        result_shots=[x for x in unique if _stage_of(x) in ('plate','taste')]
        if result_shots:hook=max(result_shots,key=lambda s:float(s.get('score',0))+.1*float(s.get('motion',50)))
        elif hook is None:
            chronological=sorted(unique,key=lambda s:_source_key(s,analyses));tail=chronological[max(0,int(len(chronological)*.8)):]
            hook=max(tail,key=lambda s:float(s.get('score',0))+.1*float(s.get('motion',50)))
    if hook is None:hook=max(unique,key=lambda s:float(s.get('score',0))+.1*float(s.get('motion',50)))
    body=[x for x in unique if x is not hook]
    chronology_locked=target>=60 or mode=='chronological' or _reliable_capture_lock(unique,analyses,user_prompt)
    if chronology_locked:
        # Real capture time is stronger evidence than a model-generated stage.
        body.sort(key=lambda s:_source_key(s,analyses))
    elif mode=='procedural':
        # Unknown shots are interpolated between neighboring stages instead of being dumped at the end.
        known=sum(_stage_of(x)!='other' for x in body)
        if known>=max(2,len(body)//3):body=_procedural_sort(body,analyses)
        else:body.sort(key=lambda s:_source_key(s,analyses))
    hook=dict(hook);hook['role']='hook';hook['transition']='cut'
    for i,s in enumerate(body):
        s['role']='setup' if i==0 else ('outro' if i==len(body)-1 else 'development')
    return [hook]+body

def local_sequence(analyses:list[dict],target:float,user_prompt:str='')->list[dict]:
    """Quota-free fallback: overlap suppression, diversity selection, then story-role ordering."""
    pool=[]
    for source_index,item in enumerate(analyses):
        for raw in item.get('segments',[]):
            s=dict(raw);s['source_index']=source_index;s['source_name']=item['meta']['name'];s['stage']=_stage_of(s);pool.append(s)
    rank=_candidate_rank
    pool.sort(key=rank,reverse=True);chosen=[];used=0.0
    # Give each source a establishing shot when the target is long enough.
    if target>=1.2*len(analyses):
        for source_index in range(len(analyses)):
            options=[s for s in pool if s['source_index']==source_index]
            if not options or target-used<1.2:continue
            picked=max(options,key=rank);length=min(picked['end']-picked['start'],4.8,target-used)
            if length>=1.2:
                pool.remove(picked);best=dict(picked);best['end']=round(best['start']+length,3);chosen.append(best);used+=length
    while pool and used<target:
        if target-used<1.2:break
        best=None;best_value=-1e9
        for s in pool:
            if any(_overlap(s,x)>.32 for x in chosen):continue
            same=sum(x['end']-x['start'] for x in chosen if x['source_index']==s['source_index'])
            source_cap=target*(.75 if target>=60 else .55)
            if len(analyses)>1 and same>=source_cap:continue
            signature=str(s.get('visual_signature','')).strip().lower()
            duplicate=any(signature and signature==str(x.get('visual_signature','')).strip().lower() for x in chosen)
            value=rank(s)-same*2.5-(24 if duplicate else 0)
            if value>best_value:best,best_value=s,value
        if best is None:break
        pool.remove(best);length=min(best['end']-best['start'],5.5,target-used)
        if length<1.2:continue
        best=dict(best);best['end']=round(best['start']+length,3);chosen.append(best);used+=length
    if not chosen:return []
    return enforce_continuity(chosen,analyses,user_prompt,target)

def plan_sequence(cfg:APIConfig,analyses:list[dict],target:float,user_prompt:str)->list[dict]:
    candidates=[]
    for source_index,item in enumerate(analyses):
        for candidate_index,s in enumerate(item.get('segments',[])):
            candidates.append({'id':f's{source_index}c{candidate_index}','source_index':source_index,'source_name':item['meta']['name'],**s})
    if not candidates:return []
    props={'id':{'type':'string'},'start':{'type':'number'},'end':{'type':'number'},'caption':{'type':'string'},'reason':{'type':'string'},'role':{'type':'string'},'transition':{'type':'string','enum':['cut','fade','dissolve','wipe_left','wipe_right','slide_left','slide_right','circle','smooth']}};schema={'type':'object','properties':{'strategy':{'type':'string'},'sequence':{'type':'array','items':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}}},'required':['strategy','sequence'],'additionalProperties':False}
    compact=json.dumps(candidates,ensure_ascii=False,separators=(',',':'))
    long_rule='这是长视频：先划分15-35秒的事件章节；冷开场之后，正片必须按素材真实时间单向推进；离开一个章节后不得返回该章节；每个章节必须有建立、发展和结果。' if target>=60 else ''
    blueprint=story_blueprint(user_prompt,target)
    prompt=f'''你是总剪辑师。请从候选镜头中设计约{target:.1f}秒的最终顺序。用户要求：{user_prompt}\n{long_rule}\n建议节拍表：{blueprint}
遵守：1) 前1-3秒最多一个hook；2) hook之后形成setup→development→climax→outro，最终输出必须能独立看懂，不能只是精彩镜头堆叠；3) 步骤型内容必须严格按intro→ingredients→prep→cook→plate→taste→outro单向推进，成品/试吃可在hook预览一次，但正片绝不允许从taste/plate/cook退回prep；4) 旅行与事件记录的正片按真实拍摄时间推进，口播回场只能作为当前章节的解说锚点；5) 相邻镜头必须带来新事实、新动作状态、新地点或新情绪，同时尽量变化景别；6) 去除重叠、重复构图、天花板、误触、等待和口头填充；7) 动作过程按开始—关键变化—结果组织，不拆散后反复返回；8) 尽量在动作/语句边界切，通常每镜头1.2-5.5秒；9) 总时长尽量接近目标但不可超过；10) 只能使用给定id及其时间范围；11) caption只写该镜头真实语音或必要的简短章节标题，不编造对白；12) 动作连续用cut，普通段落变化用fade/dissolve，只有节奏明显或空间变化时才少量使用wipe/slide/circle/smooth，禁止每个切点都用花哨转场；13) 情绪曲线应从好奇/目标逐步提高到困难或变化，在结果/反应处释放，并用最后一句或最后一个动作回收开场。
候选：{compact}'''
    body={'model':cfg.model,'input':[{'role':'user','content':[{'type':'input_text','text':prompt}]}],'text':{'format':{'type':'json_schema','name':'edit_decision_list','strict':True,'schema':schema}}}
    try:
        result=_request(cfg.endpoint('responses'),json.dumps(body).encode(),{'Authorization':'Bearer '+cfg.api_key,'Content-Type':'application/json'},cfg.timeout);data=json.loads(re.sub(r'^```(?:json)?|```$','',_output_text(result).strip()).strip());by_id={c['id']:c for c in candidates};out=[];used=0.0
        for planned in data.get('sequence',[]):
            base=by_id.get(planned.get('id'))
            if not base:continue
            a=max(base['start'],float(planned['start']));b=min(base['end'],float(planned['end']),a+target-used)
            if b-a<.5 or any(_overlap({'source_index':base['source_index'],'start':a,'end':b},x)>.32 for x in out):continue
            out.append({**base,'start':round(a,3),'end':round(b,3),'caption':str(planned['caption']),'reason':str(planned['reason']),'role':str(planned['role']),'transition':str(planned['transition'])});used+=b-a
            if used>=target-.05:break
        if used<target*.88:
            selected={(x['source_index'],x['start'],x['end']) for x in out};remaining=sorted((c for c in candidates if (c['source_index'],c['start'],c['end']) not in selected),key=lambda c:(-float(c.get('score',0)),_source_key(c,analyses)))
            for base in remaining:
                if target-used<.8:break
                a=float(base['start']);b=min(float(base['end']),a+5.5,a+target-used)
                if b-a<.8 or any(_overlap({'source_index':base['source_index'],'start':a,'end':b},x)>.32 for x in out):continue
                out.append({**base,'start':round(a,3),'end':round(b,3),'caption':str(base.get('caption','')),'reason':'长视频章节补足：'+str(base.get('reason','')),'role':'development','transition':'cut'});used+=b-a
        return enforce_continuity(out,analyses,user_prompt,target) if out else local_sequence(analyses,target,user_prompt)
    except Exception:
        return local_sequence(analyses,target,user_prompt)

