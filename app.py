from __future__ import annotations
import copy,json,os,sys,subprocess,shutil,tempfile,hashlib
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,QThread,Signal,QTimer,QStandardPaths,QSize,QSettings,QRectF,QPointF
from PySide6.QtGui import QAction,QIcon,QPixmap,QKeySequence,QPalette,QColor,QPainter,QPen,QBrush
from PySide6.QtWidgets import *
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from core import Project,Clip,OverlayClip,SFXCue,probe_media,build_render_command,proxy_path,detect_scenes,scan_video_quality,quality_from_scan
from timeline_widget import TimelineCanvas,MediaList
from ai_api import APIConfig,analyze_video,plan_sequence,protect_secret,unprotect_secret
from editing_profile import learn_profile,profile_prompt,profile_summary
from edit_protocol import (build_edit_plan,plan_to_clips,plan_to_overlays,plan_preview_text,project_quality_gate,
                           capture_key,apply_opening_treatment,opening_preset_choices)
from person_ai import process_person_video,MODEL_SHA256
from sfx_library import SFX_LIBRARY,resolve_sfx,sfx_by_id
from music_library import MUSIC_LIBRARY,resolve_music,music_by_id
from creative_director import apply_global_creative_treatment
from desktop_workspace import install_workspace
from desktop_theme import STYLE
from desktop_session import EditorSession
from desktop_render import RenderThread
from desktop_export import ExportController
from desktop_import import ImportController

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).parent));FFMPEG=str(ROOT/'ffmpeg.exe') if (ROOT/'ffmpeg.exe').exists() else (shutil.which('ffmpeg') or 'ffmpeg')


class SceneThread(QThread):
    progress=Signal(int,str);done=Signal(object);failed=Signal(str)
    def __init__(self,metas,target,prompt=''):super().__init__();self.metas=metas;self.target=target;self.prompt=prompt
    def run(self):
        try:
            analyses=[]
            for i,m in enumerate(self.metas):
                self.progress.emit(int(i/max(1,len(self.metas))*80),f'分析场景 {i+1}/{len(self.metas)}：{m["name"]}')
                analysis_path=m.get('analysis_path') or m['path'];src=Path(analysis_path);cache_dir=Path(tempfile.gettempdir())/'LingJianAI-quality-v2';cache_dir.mkdir(parents=True,exist_ok=True);key=hashlib.sha1(f'{src.resolve()}|{src.stat().st_size}|{src.stat().st_mtime_ns}'.encode()).hexdigest();cache_file=cache_dir/(key+'.json')
                if cache_file.exists():cached=json.loads(cache_file.read_text(encoding='utf-8'));cuts=cached['cuts'];scan=cached['scan']
                else:
                    cuts=detect_scenes(FFMPEG,analysis_path,m['duration'],.18);self.progress.emit(min(85,int((i+.35)/max(1,len(self.metas))*88)),f'一次扫描技术质量 {i+1}/{len(self.metas)}');scan=scan_video_quality(FFMPEG,analysis_path);cache_file.write_text(json.dumps({'cuts':cuts,'scan':scan}),encoding='utf-8')
                windows=[]
                for a,b in zip(cuts,cuts[1:]):
                    if b-a<.8:continue
                    cursor=a
                    while b-cursor>6:windows.append((cursor,cursor+4.8));cursor+=4.5
                    if b-cursor>=.8:windows.append((cursor,b))
                if not windows:windows=[(max(0,m['duration']/2-2.5),min(m['duration'],m['duration']/2+2.5))]
                segments=[];window_limit=max(24,min(120,int(self.target/3.2/max(1,len(self.metas))*1.8)+1));selected_windows=windows[:window_limit]
                for j,(a,b) in enumerate(selected_windows):
                    self.progress.emit(min(88,int((i+(j+1)/max(1,len(selected_windows)))/max(1,len(self.metas))*88)),f'长视频分章节质检 {i+1}/{len(self.metas)} · 镜头 {j+1}/{len(selected_windows)}')
                    q=quality_from_scan(scan,a,b);duration_score=max(0,100-abs(min(5.0,b-a)-3.2)*18);score=.7*q['technical']+.3*duration_score;progress=(a+b)/2/max(.1,m['duration']);role='hook' if score>=82 else ('setup' if progress<.25 else ('outro' if progress>.82 else 'development'))
                    segments.append({'start':a,'end':b,'score':score,'reason':f'离线质检 {q["technical"]:.0f} 分','caption':'','role':role,'shot_type':'','motion':50,'stability':q['sharpness'],'audio_value':0,'visual_signature':f'{m["name"]}-{round(progress*10)}'})
                analyses.append({'meta':m,'segments':segments})
            from ai_api import local_sequence
            sequence=local_sequence(analyses,self.target,self.prompt)
            self.progress.emit(100,'深度初剪方案已生成，等待确认');self.done.emit({'analyses':analyses,'sequence':sequence})
        except Exception as e:self.failed.emit(str(e))

class CloudAIThread(QThread):
    progress=Signal(int,str);done=Signal(object);failed=Signal(str)
    def __init__(self,metas,cfg,folder,prompt,target):super().__init__();self.metas=metas;self.cfg=cfg;self.folder=folder;self.prompt=prompt;self.target=target
    def run(self):
        try:
            out=[];analysis_warnings=[]
            for i,m in enumerate(self.metas):
                self.progress.emit(int(i/max(1,len(self.metas))*90),f'AI 分析 {i+1}/{len(self.metas)}：{m["name"]}')
                report={}
                segments=analyze_video(FFMPEG,self.cfg,m.get('analysis_path') or m['path'],m['duration'],self.folder,self.prompt,report=report)
                analysis_warnings.extend(report.get('warnings',[]))
                out.append({'meta':m,'segments':segments})
            self.progress.emit(92,'总剪辑师正在规划叙事顺序与切点')
            sequence=plan_sequence(self.cfg,out,self.target,self.prompt)
            self.progress.emit(100,'AI 内容分析与全局剪辑规划完成');self.done.emit({'analyses':out,'sequence':sequence,'warnings':analysis_warnings})
        except Exception as e:self.failed.emit(str(e))

class PersonTrackThread(QThread):
    progress=Signal(int,str);done=Signal(object,str);failed=Signal(str)
    def __init__(self,source,start,end,output,model,mode,quality,strength):super().__init__();self.source=source;self.start=start;self.end=end;self.output=output;self.model=model;self.mode=mode;self.quality=quality;self.strength=strength
    def run(self):
        try:self.done.emit(process_person_video(FFMPEG,self.source,self.start,self.end,self.output,self.model,self.mode,self.quality,self.strength,lambda p,m:self.progress.emit(p,m)),self.output)
        except Exception as e:self.failed.emit(str(e))

class MaskGuide(QWidget):
    positionChanged=Signal(float,float)
    def __init__(self,parent=None):
        super().__init__(parent);self.shape='none';self.cx=.5;self.cy=.5;self.mw=.72;self.mh=.72;self.setMinimumHeight(150);self.setCursor(Qt.CrossCursor);self.setToolTip('在示意画面中点击或拖动，直接改变蒙版中心位置')
    def set_values(self,shape,x,y,w,h):
        self.shape=shape or 'none';self.cx=float(x);self.cy=float(y);self.mw=float(w);self.mh=float(h);self.update()
    def sizeHint(self):return QSize(320,170)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);r=self.rect().adjusted(1,1,-1,-1);p.setPen(QPen(QColor('#CAD6E3'),1));p.setBrush(QColor('#111722'));p.drawRoundedRect(r,12,12)
        p.setPen(QPen(QColor(255,255,255,32),1,Qt.DashLine));p.drawLine(r.left()+r.width()//3,r.top()+8,r.left()+r.width()//3,r.bottom()-8);p.drawLine(r.left()+r.width()*2//3,r.top()+8,r.left()+r.width()*2//3,r.bottom()-8);p.drawLine(r.left()+8,r.top()+r.height()//3,r.right()-8,r.top()+r.height()//3);p.drawLine(r.left()+8,r.top()+r.height()*2//3,r.right()-8,r.top()+r.height()*2//3)
        cx=r.left()+self.cx*r.width();cy=r.top()+self.cy*r.height();mw=max(24,self.mw*r.width());mh=max(24,self.mh*r.height())
        if self.shape=='cinema':
            p.fillRect(r.left(),r.top(),r.width(),int(r.height()*.115),QColor(0,0,0,235));p.fillRect(r.left(),int(r.bottom()-r.height()*.115),r.width(),int(r.height()*.115),QColor(0,0,0,235));label='电影宽银幕：自动添加上下遮幅'
        elif self.shape=='vignette':
            p.setPen(QPen(QColor('#5CB0FF'),3));p.setBrush(Qt.NoBrush);p.drawEllipse(r.adjusted(22,12,-22,-12));label='暗角：压暗四周，把视线集中到画面中心'
        elif self.shape!='none':
            box=QRectF(cx-mw/2,cy-mh/2,mw,mh);p.setPen(QPen(QColor('#5CB0FF'),3));p.setBrush(QColor(63,151,238,38))
            if self.shape in ('spotlight','ellipse','privacy_blur'):p.drawEllipse(box)
            elif self.shape=='diamond':p.drawPolygon([QPointF(cx,box.top()),QPointF(box.right(),cy),QPointF(cx,box.bottom()),QPointF(box.left(),cy)])
            elif self.shape=='vertical_strip':p.drawRoundedRect(QRectF(box.left(),r.top()+8,box.width(),r.height()-16),10,10)
            elif self.shape=='split_left':p.drawRect(QRectF(r.left()+4,r.top()+4,max(8,cx-r.left()),r.height()-8))
            elif self.shape=='split_right':p.drawRect(QRectF(cx,r.top()+4,max(8,r.right()-cx),r.height()-8))
            else:p.drawRoundedRect(box,14,14)
            p.setBrush(QColor('#FFFFFF'));p.setPen(QPen(QColor('#1477E6'),2));p.drawEllipse(QPointF(cx,cy),6,6);label='隐私模糊：蓝色区域会被模糊' if self.shape=='privacy_blur' else '拖动蓝点，把主体放进高亮区域'
        else:label='选择用途预设，软件会自动给出合适参数'
        p.setPen(QColor('#DCEBFA'));p.drawText(r.adjusted(12,8,-12,-8),Qt.AlignLeft|Qt.AlignBottom,label)
    def _move(self,pos):
        r=self.rect().adjusted(1,1,-1,-1);x=max(0.,min(1.,(pos.x()-r.left())/max(1,r.width())));y=max(0.,min(1.,(pos.y()-r.top())/max(1,r.height())));self.cx=x;self.cy=y;self.positionChanged.emit(x,y);self.update()
    def mousePressEvent(self,event):self._move(event.position())
    def mouseMoveEvent(self,event):
        if event.buttons()&Qt.LeftButton:self._move(event.position())

class Main(QMainWindow):
    def __init__(self):
        super().__init__();self.project=Project();self.metas={};self.media_items={};self.thumbs={};self.proxies={};self.proxy_queue=[];self.proxy_thread=None;self.analyze_thread=None;self.scene_thread=None;self.cloud_thread=None;self.render_thread=None;self.effect_thread=None;self.person_thread=None;self.sfx_player=None;self.sfx_audio=None;self.current_clip=-1;self.history=[];self.redo_history=[];self._trim_snapshot=False;self.project_path='';self.pending_position=None;self.pending_autoplay=False;self.source_path='';self.source_in=0.;self.source_out=0.;self.settings=QSettings('LingJianAI','Editor');self.editing_profile=self.load_editing_profile()
        self.cache=Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation) or tempfile.gettempdir())/'LingJianAI';self.cache.mkdir(parents=True,exist_ok=True);self.autosave=self.cache/'autosave.ljproject'
        self.setWindowTitle('灵剪 AI 视频编辑器 4.13 · 全片创意导演');self.resize(1760,1000);self.setMinimumSize(1280,800);self.build();self.load_api_settings();self.bind_shortcuts();install_workspace(self);self.timeline.set_project(self.project);self.session=EditorSession(self,ROOT);self.export_controller=ExportController(self,FFMPEG);self.import_controller=ImportController(self,FFMPEG);self.statusBar().showMessage('就绪 · AI 会分析全片事件并自动安排字幕、动效、音效和音乐');self.auto_timer=QTimer(self);self.auto_timer.timeout.connect(self.auto_save);self.auto_timer.start(30000)
    def btn(self,text,fn,primary=False):
        b=QPushButton(text);b.clicked.connect(fn)
        if primary:b.setObjectName('primary')
        return b
    def build(self):
        root=QWidget();root.setObjectName('appRoot');self.setCentralWidget(root);outer=QVBoxLayout(root);outer.setContentsMargins(12,10,12,7);outer.setSpacing(9)
        top_card=QFrame();top_card.setObjectName('topGlass');top=QHBoxLayout(top_card);top.setContentsMargins(15,9,12,9);brand=QLabel('<b style="font-size:20px;color:#1769C2">✦ 灵剪</b> <span style="color:#6C7A90">AI Studio</span>');brand.setToolTip('可审计、可撤销、可验证的 AI 视频编辑器');top.addWidget(brand);self.title=QLineEdit('未命名作品');self.title.setMaximumWidth(270);self.title.setPlaceholderText('给作品起个名字');top.addWidget(self.title);self.mode_chip=QLabel('4.13 · 全片创意导演');self.mode_chip.setObjectName('statusChip');top.addWidget(self.mode_chip);top.addStretch();self.undo_btn=self.btn('↶  撤销',self.undo);self.undo_btn.setToolTip('撤销上一步 · Ctrl+Z');self.redo_btn=self.btn('↷  重做',self.redo);self.redo_btn.setToolTip('重做上一步 · Ctrl+Y');top.addWidget(self.undo_btn);top.addWidget(self.redo_btn);open_btn=self.btn('⌂  打开工程',self.open_project);open_btn.setToolTip('打开 .ljproject 工程');save_btn=self.btn('⌁  保存',self.save_project);save_btn.setToolTip('保存工程 · Ctrl+S');top.addWidget(open_btn);top.addWidget(save_btn);self.export_top=self.btn('↑  导出',self.export,True);self.export_top.setToolTip('导出兼容 MP4');top.addWidget(self.export_top);outer.addWidget(top_card)
        self.notice=QLabel('<b style="color:#1769C2">1  导入素材</b>　›　<b>2  选择片段</b>　›　<b>3  AI 方案 / 手动精剪</b>　›　<b>4  质量检查</b>　›　<b>5  导出</b>');self.notice.setObjectName('stepGuide');outer.addWidget(self.notice)
        main=QSplitter(Qt.Horizontal);self.main_workspace=main;outer.addWidget(main,1)
        left=QFrame();left.setObjectName('glassCard');ll=QVBoxLayout(left);ll.setContentsMargins(12,11,12,12);head=QHBoxLayout();head.addWidget(QLabel('<b style="font-size:15px">素材库</b>'));head.addStretch();import_btn=self.btn('＋ 导入',self.import_files,True);import_btn.setToolTip('导入视频或音频素材');head.addWidget(import_btn);ll.addLayout(head);self.search=QLineEdit();self.search.setPlaceholderText('⌕  搜索素材');self.search.textChanged.connect(self.filter_media);ll.addWidget(self.search);self.media=MediaList();self.media.itemDoubleClicked.connect(lambda _:self.add_selected_media());ll.addWidget(self.media,1);self.media_info=QLabel('MP4 · MOV · MKV · 音频\nHEVC 10-bit 自动创建流畅代理');self.media_info.setStyleSheet('color:#627187;padding:4px');ll.addWidget(self.media_info);add_timeline=self.btn('＋  添加到时间线',self.add_selected_media,True);add_timeline.setToolTip('把选中素材追加到时间线');ll.addWidget(add_timeline);main.addWidget(left)
        center=QFrame();center.setObjectName('mainStage');cl=QVBoxLayout(center);cl.setContentsMargins(9,8,9,8);self.vertical_workspace=QSplitter(Qt.Vertical);self.vertical_workspace.setChildrenCollapsible(False);cl.addWidget(self.vertical_workspace,1);preview_area=QWidget();preview_layout=QVBoxLayout(preview_area);preview_layout.setContentsMargins(0,0,0,0);preview_layout.setSpacing(5);monitors=QSplitter(Qt.Horizontal);self.monitors_workspace=monitors
        source_box=QGroupBox('源监视器 · 素材审片与入/出点');sv=QVBoxLayout(source_box);self.source_video=QVideoWidget();self.source_video.setMinimumHeight(145);self.source_video.setStyleSheet('background:#020306');sv.addWidget(self.source_video,1);source_ctl=QHBoxLayout();self.source_time=QLabel('00:00:00:00');source_ctl.addWidget(self.btn('◀ 1帧',lambda:self.source_nudge(-1)));source_ctl.addWidget(self.btn('▶/暂停',self.source_play));source_ctl.addWidget(self.btn('1帧 ▶',lambda:self.source_nudge(1)));source_ctl.addWidget(self.source_time);source_ctl.addStretch();source_ctl.addWidget(self.btn('[ 设入点  I',self.mark_in));source_ctl.addWidget(self.btn('设出点  O ]',self.mark_out));sv.addLayout(source_ctl);source_edit=QHBoxLayout();self.mark_label=QLabel('入点 --  出点 --');source_edit.addWidget(self.mark_label);source_edit.addStretch();source_edit.addWidget(self.btn('插入时间线  F9',self.insert_source,True));source_edit.addWidget(self.btn('覆盖选中片段  F10',self.overwrite_source));sv.addLayout(source_edit);monitors.addWidget(source_box)
        preview_box=QGroupBox('节目监视器 · 时间线输出');pv=QVBoxLayout(preview_box);pv.setContentsMargins(5,12,5,5);self.video=QVideoWidget();self.video.setMinimumHeight(145);self.video.setStyleSheet('background:#030407');self.placeholder=QLabel('选择时间线片段开始节目预览');self.placeholder.setAlignment(Qt.AlignCenter);self.placeholder.setStyleSheet('background:#06080c;color:#747d90;border:1px dashed #303746');self.preview_stack=QStackedLayout();self.preview_stack.addWidget(self.placeholder);self.preview_stack.addWidget(self.video);pv.addLayout(self.preview_stack);monitors.addWidget(preview_box);monitors.setSizes([500,500]);preview_layout.addWidget(monitors,1)
        self.player=QMediaPlayer();self.audio=QAudioOutput();self.player.setVideoOutput(self.video);self.player.setAudioOutput(self.audio);self.player.positionChanged.connect(self.on_position);self.player.durationChanged.connect(lambda d:self.seek.setMaximum(d));self.player.errorOccurred.connect(self.player_error);self.player.mediaStatusChanged.connect(self.media_status)
        self.source_player=QMediaPlayer();self.source_audio=QAudioOutput();self.source_player.setVideoOutput(self.source_video);self.source_player.setAudioOutput(self.source_audio);self.source_player.positionChanged.connect(self.source_position);self.source_player.durationChanged.connect(self.source_duration)
        controls=QHBoxLayout();controls.addWidget(self.btn('⏮ 上一段',lambda:self.step_clip(-1)));controls.addWidget(self.btn('▶  播放 / 暂停',self.play,True));controls.addWidget(self.btn('下一段 ⏭',lambda:self.step_clip(1)));self.seek=QSlider(Qt.Horizontal);self.seek.sliderMoved.connect(self.local_seek);controls.addWidget(self.seek,1);self.time=QLabel('00:00.0 / 00:00.0');controls.addWidget(self.time);preview_layout.addLayout(controls);self.vertical_workspace.addWidget(preview_area);timeline_area=QWidget();timeline_layout=QVBoxLayout(timeline_area);timeline_layout.setContentsMargins(0,0,0,0);timeline_layout.setSpacing(5)
        tool_card=QFrame();tool_card.setObjectName('toolGlass');bar=QHBoxLayout(tool_card);bar.setContentsMargins(9,4,9,4);bar.addWidget(QLabel('<b>V1 视频 · T1 字幕 · A1 音乐/原声</b>'));self.timeline_space_btn=self.btn('▣  扩大时间线',self.toggle_timeline_space);self.timeline_space_btn.setToolTip('在普通视图与大时间线视图间切换');bar.addWidget(self.timeline_space_btn);self.select_tool=self.btn('↖  选择  A',lambda:self.set_tool('select'),True);self.select_tool.setToolTip('选择、移动和裁切片段');self.blade_tool=self.btn('⌁  刀片  B',lambda:self.set_tool('blade'));self.blade_tool.setToolTip('点击片段进行分割');self.snap_btn=self.btn('⌁  吸附  N',self.toggle_snap,True);self.snap_btn.setToolTip('让片段边缘自动对齐');bar.addWidget(self.select_tool);bar.addWidget(self.blade_tool);bar.addWidget(self.snap_btn);split_btn=self.btn('✂  分割',self.split_clip);split_btn.setToolTip('在播放头处分割 · Ctrl+B');delete_btn=self.btn('−  删除',self.delete_clip);delete_btn.setToolTip('波纹删除选中片段 · Delete');copy_btn=self.btn('⊕  复制',self.duplicate_clip);copy_btn.setToolTip('复制选中片段 · Ctrl+D');bar.addWidget(split_btn);bar.addWidget(delete_btn);bar.addWidget(copy_btn);bar.addStretch();self.global_tc=QLineEdit('00:00:00:00');self.global_tc.setMaximumWidth(105);self.global_tc.setToolTip('输入 HH:MM:SS:FF 后回车定位');self.global_tc.returnPressed.connect(self.seek_timecode);bar.addWidget(self.global_tc);self.zoom=QSlider(Qt.Horizontal);self.zoom.setRange(5,70);self.zoom.setValue(18);self.zoom.setMaximumWidth(110);self.zoom.valueChanged.connect(self.timeline_zoom);bar.addWidget(self.zoom);timeline_layout.addWidget(tool_card)
        self.timeline_scroll=QScrollArea();self.timeline_scroll.setWidgetResizable(True);self.timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.timeline_scroll.setMinimumHeight(250);self.timeline=TimelineCanvas();self.timeline_scroll.setWidget(self.timeline);timeline_layout.addWidget(self.timeline_scroll,1);self.vertical_workspace.addWidget(timeline_area);self.vertical_workspace.setStretchFactor(0,3);self.vertical_workspace.setStretchFactor(1,2);self.vertical_workspace.setSizes([390,300]);self.timeline.selected.connect(self.select_clip);self.timeline.seeked.connect(self.global_seek);self.timeline.moved.connect(self.timeline_move);self.timeline.trimmed.connect(self.timeline_trim);self.timeline.mediaDropped.connect(self.add_media_path);self.timeline.editStarted.connect(self.begin_canvas_edit);self.timeline.editFinished.connect(self.end_canvas_edit);self.timeline.bladeRequested.connect(self.blade_split);self.timeline.trackStateChanged.connect(self.track_state_changed);main.addWidget(center)
        self.inspector=QTabWidget();right=self.inspector;right.setObjectName('inspectorTabs');right.setMinimumWidth(400);right.setTabPosition(QTabWidget.North);right.setDocumentMode(True);main.addWidget(right)
        edit=QWidget();el=QVBoxLayout(edit);el.setContentsMargins(14,14,14,14);self.clip_label=QLabel('尚未选择片段');self.clip_label.setWordWrap(True);self.clip_label.setStyleSheet('color:#1769C2;background:#EDF6FF;border:1px solid #D5E9FF;border-radius:10px;padding:9px');el.addWidget(self.clip_label);form=QFormLayout();form.setVerticalSpacing(9);self.start=QDoubleSpinBox();self.end=QDoubleSpinBox();[x.setRange(0,86400) for x in (self.start,self.end)];[x.setDecimals(2) for x in (self.start,self.end)];self.volume=QDoubleSpinBox();self.volume.setRange(0,2);self.volume.setSingleStep(.05);self.volume.setValue(1);form.addRow('素材入点（秒）',self.start);form.addRow('素材出点（秒）',self.end);form.addRow('原声音量',self.volume);el.addLayout(form);el.addWidget(self.btn('✓  应用片段参数',self.apply_clip,True));row=QHBoxLayout();row.addWidget(self.btn('✂  分割',self.split_clip));row.addWidget(self.btn('−  波纹删除',self.delete_clip));el.addLayout(row);row2=QHBoxLayout();row2.addWidget(self.btn('⊕  复制',self.duplicate_clip));row2.addWidget(self.btn('←  前移',lambda:self.move_clip(-1)));row2.addWidget(self.btn('后移  →',lambda:self.move_clip(1)));el.addLayout(row2);hint=QLabel('小提示：直接拖动片段可重排，拖动片段边缘可裁切。AI 生成的镜头和普通片段一样可编辑。');hint.setWordWrap(True);hint.setStyleSheet('color:#627187;background:#F6F8FB;border-radius:9px;padding:9px');el.addWidget(hint);el.addStretch();right.addTab(edit,'剪辑')
        sub=QWidget();sl=QVBoxLayout(sub);self.caption=QLineEdit();self.caption.setPlaceholderText('输入字幕、标题或字母内容');sf=QFormLayout();self.pos=QComboBox();[(self.pos.addItem(n,v)) for n,v in [('顶部安全区','top'),('画面中央','center'),('下三分之一','lower_third'),('底部安全区','bottom')]];self.caption_font=QComboBox();self.caption_font.addItems(['微软雅黑','黑体','宋体','等线','Arial','Impact','Consolas']);self.caption_size=QSpinBox();self.caption_size.setRange(18,140);self.caption_size.setValue(42);self.caption_color=QComboBox();[(self.caption_color.addItem(n,v)) for n,v in [('白色','#FFFFFF'),('醒目黄','#FFD43B'),('青色','#45E0E8'),('粉色','#FF6FAE'),('绿色','#6EE7A8'),('黑色','#111111')]];self.caption_bg=QComboBox();[(self.caption_bg.addItem(n,v)) for n,v in [('黑色','#000000'),('深蓝','#111827'),('紫色','#37245F'),('白色','#FFFFFF')]];self.caption_bg_opacity=QDoubleSpinBox();self.caption_bg_opacity.setRange(0,1);self.caption_bg_opacity.setSingleStep(.05);self.caption_bg_opacity.setValue(.55);sf.addRow('字幕内容',self.caption);sf.addRow('字幕区域',self.pos);sf.addRow('字体',self.caption_font);sf.addRow('字号',self.caption_size);sf.addRow('文字颜色',self.caption_color);sf.addRow('底色',self.caption_bg);sf.addRow('底色透明度',self.caption_bg_opacity);sl.addLayout(sf);title_box=QGroupBox('✦ AI 个性开场标题');title_form=QFormLayout(title_box);self.title_effect=QComboBox();self.title_effect.addItem('普通字幕 / 关闭','');self.title_effect.addItem('Q 弹标题 · 弹簧回弹','bounce');self.title_effect.addItem('镂空字内走马灯 · 视频只在字里播放','text_window');self.title_effect.setToolTip('镂空模式自动读取工程内不同素材，不需要手动画遮罩。');self.title_text_edit=QLineEdit('VLOG');self.title_text_edit.setPlaceholderText('例如 VLOG、夏日、出发');title_form.addRow('标题玩法',self.title_effect);title_form.addRow('标题文字',self.title_text_edit);title_note=QLabel('AI 方案会自动生成；这里用于选中片段后改字、切换玩法。镂空标题自动从每份素材各取一闪。');title_note.setWordWrap(True);title_note.setStyleSheet('color:#637187');title_form.addRow(title_note);sl.addWidget(title_box);sl.addWidget(self.btn('✓ 应用字幕与个性标题',self.apply_clip,True));sl.addWidget(self.btn('▶ 渲染当前效果预览',self.preview_effects));sl.addWidget(self.btn('将当前字幕样式应用到全部',self.apply_caption_style_all));sl.addWidget(self.btn('清除当前字幕',self.clear_caption));sl.addStretch();right.addTab(sub,'字幕/标题')
        trans=QWidget();tl=QVBoxLayout(trans);tf=QFormLayout();self.transition=QComboBox();[(self.transition.addItem(n,v)) for n,v in [('直接切换','none'),('交叉淡化','fade'),('溶解','dissolve'),('向左擦除','wipe_left'),('向右擦除','wipe_right'),('向左滑动','slide_left'),('向右滑动','slide_right'),('圆形展开','circle'),('平滑推移','smooth')]];self.transition_duration=QDoubleSpinBox();self.transition_duration.setRange(.05,2);self.transition_duration.setSingleStep(.05);self.transition_duration.setValue(.35);tf.addRow('进入当前片段',self.transition);tf.addRow('持续时间（秒）',self.transition_duration);tl.addLayout(tf);tl.addWidget(self.btn('✓ 应用当前转场',self.apply_clip,True));tl.addWidget(self.btn('▶ 渲染当前效果预览',self.preview_effects));tl.addWidget(self.btn('将转场应用到全部切点',self.apply_transition_all));note=QLabel('转场显示在片段左侧切点；直接切换适合动作连续，淡化/溶解适合段落变化，擦除/滑动适合节奏型内容。');note.setWordWrap(True);note.setStyleSheet('color:#5D6B7E');tl.addWidget(note);tl.addStretch();right.addTab(trans,'转场')
        mask=QScrollArea();mask.setWidgetResizable(True);mask.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);mask_body=QWidget();mask.setWidget(mask_body);ml=QVBoxLayout(mask_body);ml.setContentsMargins(12,12,12,12);mask_intro=QLabel('<b style="font-size:15px;color:#1769C2">先选用途，不需要理解参数</b><br><span style="color:#6F7C8F">软件会给出适合的形状、位置、大小与羽化；再在下方示意画面中拖动蓝点即可。</span>');mask_intro.setWordWrap(True);mask_intro.setStyleSheet('background:#EDF6FF;border:1px solid #D5E9FF;border-radius:10px;padding:10px');ml.addWidget(mask_intro);preset_box=QGroupBox('一键智能预设');preset_grid=QGridLayout(preset_box);preset_grid.addWidget(self.btn('✦  自动推荐',self.smart_mask,True),0,0,1,2);preset_grid.addWidget(self.btn('人物聚焦',lambda:self.apply_mask_preset('person')),1,0);preset_grid.addWidget(self.btn('美食 / 商品',lambda:self.apply_mask_preset('product')),1,1);preset_grid.addWidget(self.btn('口播卡片',lambda:self.apply_mask_preset('card')),2,0);preset_grid.addWidget(self.btn('电影感遮幅',lambda:self.apply_mask_preset('cinema')),2,1);ml.addWidget(preset_box);self.mask_guide=MaskGuide();self.mask_guide.positionChanged.connect(self.mask_position_changed);ml.addWidget(self.mask_guide);self.mask_help=QLabel('请选择一个用途预设。');self.mask_help.setWordWrap(True);self.mask_help.setStyleSheet('color:#536176;background:#F6F8FB;border-radius:9px;padding:8px');ml.addWidget(self.mask_help);advanced=QGroupBox('微调（可选）');mf2=QFormLayout(advanced);self.mask_shape=QComboBox();[(self.mask_shape.addItem(n,v)) for n,v in [('关闭蒙版','none'),('圆形人物聚焦','spotlight'),('椭圆主体聚焦','ellipse'),('口播画中画卡片','portrait_card'),('电影宽银幕','cinema')]];self.mask_x=QDoubleSpinBox();self.mask_y=QDoubleSpinBox();self.mask_width=QDoubleSpinBox();self.mask_height=QDoubleSpinBox();self.mask_opacity=QDoubleSpinBox();[x.setRange(0,1) for x in (self.mask_x,self.mask_y,self.mask_width,self.mask_height,self.mask_opacity)];[x.setSingleStep(.05) for x in (self.mask_x,self.mask_y,self.mask_width,self.mask_height,self.mask_opacity)];[x.setDecimals(2) for x in (self.mask_x,self.mask_y,self.mask_width,self.mask_height,self.mask_opacity)];self.mask_x.setValue(.5);self.mask_y.setValue(.5);self.mask_width.setValue(.72);self.mask_height.setValue(.72);self.mask_opacity.setValue(1);self.mask_feather=QDoubleSpinBox();self.mask_feather.setRange(1,120);self.mask_feather.setValue(28);mf2.addRow('效果类型',self.mask_shape);mf2.addRow('左右位置',self.mask_x);mf2.addRow('上下位置',self.mask_y);mf2.addRow('区域宽度',self.mask_width);mf2.addRow('区域高度',self.mask_height);mf2.addRow('边缘柔和',self.mask_feather);mf2.addRow('主体清晰度',self.mask_opacity);ml.addWidget(advanced);[x.valueChanged.connect(self.update_mask_guide) for x in (self.mask_x,self.mask_y,self.mask_width,self.mask_height)];self.mask_shape.currentIndexChanged.connect(self.update_mask_guide);ml.addWidget(self.btn('▶  应用并生成真实效果预览',self.apply_and_preview_mask,True));mask_actions=QHBoxLayout();mask_actions.addWidget(self.btn('应用到全部片段',self.apply_mask_all));mask_actions.addWidget(self.btn('关闭蒙版',self.reset_mask));ml.addLayout(mask_actions);ml.addStretch();right.addTab(mask,'智能蒙版')
        ai=QWidget();al=QVBoxLayout(ai);self.prompt=QTextEdit('请输出一个叙事完整的成片：建立人物和目标，保留动作因果，删除重复与无效画面，生成忠实字幕，并根据段落选择克制的转场。');self.prompt.setMaximumHeight(120);al.addWidget(QLabel('告诉 AI 你想讲什么故事'));al.addWidget(self.prompt);f=QFormLayout();self.story_style=QComboBox();self.story_style.addItems(['旅行叙事 · 目标—障碍—发现—回收','美食教程 · 成品钩子后严格按步骤','生活记录 · 小目标与阶段性回报','剧情短片 · 冲突—尝试—反转—回收','纪录片 · 尊重事件顺序与完整语义','电影感 · 克制转场与留白','口播知识 · 语义优先并删除停顿']);f.addRow('导演风格',self.story_style);self.opening_style=QComboBox();[(self.opening_style.addItem(name,value)) for name,value in opening_preset_choices()];self.opening_style.setToolTip('无需设置蒙版参数；AI 会把所选开篇自动编排到前 3 个镜头，并在方案预览中逐镜头说明。');f.addRow('高级开篇',self.opening_style);self.target=QSpinBox();self.target.setRange(5,600);self.target.setValue(60);f.addRow('目标时长（秒）',self.target);al.addLayout(f);opening_note=QLabel('✦ 生成方案时同时安排开场蒙版、画中画/撕裂/分屏转场与标题节奏；预览中可一键换整套开头，不需要手动调蒙版参数。');opening_note.setWordWrap(True);opening_note.setStyleSheet('color:#1769C2;background:#EDF6FF;border:1px solid #D5E9FF;border-radius:10px;padding:9px');al.addWidget(opening_note);self.cloud_btn=self.btn('✦ 生成完整方案并预览高级开头',self.cloud_ai,True);al.addWidget(self.cloud_btn);self.deep_btn=self.btn('离线分析并预览完整方案',self.deep_ai);al.addWidget(self.deep_btn);al.addWidget(self.btn('快速节奏方案',self.quick_ai));al.addWidget(self.btn('检查当前时间线',self.inspect_timeline));al.addWidget(self.btn('学习当前人工时间线',lambda:self.learn_current_order(True)));self.profile_status=QLabel(profile_summary(self.editing_profile));self.profile_status.setWordWrap(True);self.profile_status.setStyleSheet('color:#536E8B');al.addWidget(self.profile_status);self.ai_progress=QProgressBar();self.ai_progress.hide();al.addWidget(self.ai_progress);self.ai_status=QLabel('AI 方案预览会明确列出每个开篇镜头的蒙版、转场和用途；应用后仍可逐段微调，并可用 Ctrl+Z 整体撤销。');self.ai_status.setWordWrap(True);self.ai_status.setStyleSheet('color:#5D6B7E');al.addWidget(self.ai_status);al.addStretch();right.addTab(ai,'AI 导演')
        api=QWidget();apl=QVBoxLayout(api);api_box=QGroupBox('OpenAI / Responses 兼容接口');apf=QFormLayout(api_box);self.api_url=QLineEdit('https://api.openai.com');self.api_key=QLineEdit();self.api_key.setEchoMode(QLineEdit.Password);self.api_key.setPlaceholderText('API Key 由 Windows DPAPI 加密保存');self.api_model=QLineEdit('gpt-5-mini');self.api_transcribe=QLineEdit('gpt-4o-mini-transcribe');self.api_refine=QCheckBox('候选二次精看（每份素材最多额外 1 次视觉请求）');apf.addRow('接口地址',self.api_url);apf.addRow('API Key',self.api_key);apf.addRow('视觉模型',self.api_model);apf.addRow('转写模型',self.api_transcribe);apf.addRow('',self.api_refine);apl.addWidget(api_box);apl.addWidget(self.btn('加密保存接口设置',self.save_api_settings,True));self.api_note=QLabel('密钥只保存在当前 Windows 用户的加密配置中，不写入工程文件。每份素材先用最多 24 帧分组分析；可选二次精看会增加一次视觉请求。兼容接口需要支持 /v1/responses 与 /v1/audio/transcriptions。');self.api_note.setWordWrap(True);self.api_note.setStyleSheet('color:#5D6B7E');apl.addWidget(self.api_note);apl.addStretch();right.addTab(api,'AI 接口')
        out=QScrollArea();out.setWidgetResizable(True);out_body=QWidget();out.setWidget(out_body);ol=QVBoxLayout(out_body);music=QGroupBox('背景音乐');mf=QFormLayout(music);self.bgm=QLineEdit();self.bgm.setReadOnly(True);self.bgm.setPlaceholderText('未选择');mf.addRow(self.bgm,self.btn('选择',self.choose_bgm));self.bgm_volume=QDoubleSpinBox();self.bgm_volume.setRange(0,1);self.bgm_volume.setSingleStep(.05);self.bgm_volume.setValue(.22);mf.addRow('音乐音量',self.bgm_volume);ol.addWidget(music);sfx_box=QGroupBox('音效库 · 10 个本地原创声音');sfx_layout=QVBoxLayout(sfx_box);self.sfx_combo=QComboBox();[(self.sfx_combo.addItem(f'{x["category"]} · {x["name"]}',x['id'])) for x in SFX_LIBRARY];sfx_layout.addWidget(self.sfx_combo);sfx_controls=QHBoxLayout();sfx_controls.addWidget(self.btn('▶ 试听',self.preview_sfx));self.sfx_volume=QDoubleSpinBox();self.sfx_volume.setRange(0,1.5);self.sfx_volume.setSingleStep(.05);self.sfx_volume.setValue(.65);self.sfx_volume.setPrefix('音量 ');sfx_controls.addWidget(self.sfx_volume);sfx_controls.addWidget(self.btn('＋ 加到播放头',self.add_sfx_at_playhead,True));sfx_layout.addLayout(sfx_controls);self.sfx_list=QListWidget();self.sfx_list.setMaximumHeight(118);sfx_layout.addWidget(self.sfx_list);sfx_layout.addWidget(self.btn('删除选中音效',self.remove_sfx));sfx_note=QLabel('AI 高级开篇会自动匹配音效；手动添加时使用时间线红色播放头位置。音效只参与成片混音，不改写原素材。');sfx_note.setWordWrap(True);sfx_note.setStyleSheet('color:#6F7C8F');sfx_layout.addWidget(sfx_note);ol.addWidget(sfx_box);export_box=QGroupBox('导出设置');ef=QFormLayout(export_box);self.preset=QComboBox();self.preset.addItem('竖屏 720×1280 · 标准',(720,1280,'standard'));self.preset.addItem('竖屏 1080×1920 · 高质量',(1080,1920,'high'));self.preset.addItem('横屏 1280×720 · 标准',(1280,720,'standard'));self.preset.addItem('横屏 1920×1080 · 高质量',(1920,1080,'high'));self.preset.addItem('方形 1080×1080 · 高质量',(1080,1080,'high'));ef.addRow('输出预设',self.preset);ol.addWidget(export_box);ol.addWidget(self.btn('导出前质量检查',self.inspect_timeline));self.export_button=self.btn('导出 MP4',self.export,True);ol.addWidget(self.export_button);self.render_progress=QProgressBar();self.render_progress.hide();ol.addWidget(self.render_progress);self.render_status=QLabel('导出前检查素材、顺序、字幕可读性、转场比例、音效、磁盘空间和 H.264/AAC 兼容性。');self.render_status.setWordWrap(True);ol.addWidget(self.render_status);ol.addStretch();right.addTab(out,'音频与导出');layers=QWidget();ll=QVBoxLayout(layers);layer_intro=QLabel('<b>AI 会按开场复杂度自动创建 V2/V3</b><br>每条叠加轨都有独立时间、位置、尺寸、透明度和蒙版；也可手工把当前片段放到播放头。');layer_intro.setWordWrap(True);layer_intro.setStyleSheet('color:#1769C2;background:#EDF6FF;border-radius:10px;padding:10px');ll.addWidget(layer_intro);self.overlay_list=QListWidget();self.overlay_list.currentItemChanged.connect(self.overlay_selected);ll.addWidget(self.overlay_list,1);lf=QFormLayout();self.overlay_track=QComboBox();[(self.overlay_track.addItem(f'V{i}',i)) for i in range(2,7)];self.overlay_layout=QComboBox();[(self.overlay_layout.addItem(n,v)) for n,v in [('右上画中画','pip_right'),('左上画中画','pip_left'),('画面中央','center'),('铺满画面','full')]];self.overlay_mask=QComboBox();[(self.overlay_mask.addItem(n,v)) for n,v in [('椭圆柔边','ellipse'),('圆形柔边','circle'),('矩形','none')]];self.overlay_opacity=QDoubleSpinBox();self.overlay_opacity.setRange(.05,1);self.overlay_opacity.setSingleStep(.05);self.overlay_opacity.setValue(.96);self.overlay_time=QDoubleSpinBox();self.overlay_time.setRange(0,86400);self.overlay_time.setDecimals(2);self.overlay_duration=QDoubleSpinBox();self.overlay_duration.setRange(.10,60);self.overlay_duration.setValue(2.5);self.overlay_duration.setDecimals(2);lf.addRow('目标轨道',self.overlay_track);lf.addRow('时间线位置',self.overlay_time);lf.addRow('持续时间',self.overlay_duration);lf.addRow('画面布局',self.overlay_layout);lf.addRow('蒙版',self.overlay_mask);lf.addRow('透明度',self.overlay_opacity);ll.addLayout(lf);ll.addWidget(self.btn('＋ 把当前片段添加到播放头',self.add_overlay_at_playhead,True));layer_actions=QHBoxLayout();layer_actions.addWidget(self.btn('更新选中叠加层',self.update_selected_overlay));layer_actions.addWidget(self.btn('删除叠加层',self.remove_selected_overlay));ll.addLayout(layer_actions);ll.addStretch();right.addTab(layers,'多轨合成');main.setSizes([285,930,350])
    def bind_shortcuts(self):
        for i,name in enumerate(('剪辑','字幕','转场','智能蒙版','AI','接口','导出')):
            if i<self.inspector.count():self.inspector.setTabText(i,name)
        self.configure_advanced_effects()
        mask_page=self.inspector.widget(3)
        if isinstance(mask_page,QScrollArea) and mask_page.widget():
            for button in mask_page.findChildren(QPushButton):
                if '应用并生成真实效果预览' in button.text():mask_page.widget().layout().insertWidget(4,button);break
        for widget in (self.source_video,self.video):
            palette=widget.palette();palette.setColor(QPalette.Window,QColor('#06080D'));widget.setPalette(palette);widget.setAutoFillBackground(True)
        self.media.currentItemChanged.connect(self.source_item_changed)
        for seq,fn in [('Ctrl+Z',self.undo),('Ctrl+Y',self.redo),('Ctrl+S',self.save_project),('Ctrl+B',self.split_clip),('Ctrl+D',self.duplicate_clip),('Delete',self.delete_clip),('Space',self.play),('Left',lambda:self.nudge(-1)),('Right',lambda:self.nudge(1)),('A',lambda:self.set_tool('select')),('B',lambda:self.set_tool('blade')),('N',self.toggle_snap),('I',self.mark_in),('O',self.mark_out),('F9',self.insert_source),('F10',self.overwrite_source),('J',lambda:self.source_nudge(-1)),('K',self.source_pause),('L',self.source_play)]:
            a=QAction(self);a.setShortcut(QKeySequence(seq));a.triggered.connect(fn);self.addAction(a)
    def configure_advanced_effects(self):
        for name in reversed(('站酷快乐体','站酷庆科黄油体','霞鹜文楷')):
            if self.caption_font.findText(name)<0:self.caption_font.insertItem(0,name)
        self.caption_effect=QComboBox()
        for name,value in (('干净字幕','clean'),('Q 弹果冻','jelly'),('节奏滑动','kinetic'),('逐字输入感','typewriter'),('印章砸入','stamp'),('气泡弹出','pop'),('重点高亮','highlight'),('极简无底框','minimal')):self.caption_effect.addItem(name,value)
        subtitle_page=self.inspector.widget(1);title_groups=subtitle_page.findChildren(QGroupBox) if subtitle_page else []
        title_group=next((x for x in title_groups if '个性开场标题' in x.title()),None)
        if title_group:title_group.layout().addRow('字幕动效',self.caption_effect)
        output_page=self.inspector.widget(6);output_groups=output_page.findChildren(QGroupBox) if output_page else []
        music_group=next((x for x in output_groups if x.title()=='背景音乐'),None)
        self.music_combo=QComboBox()
        for item in MUSIC_LIBRARY:self.music_combo.addItem(f'{item["mood"]} · {item["name"]}',item['id'])
        if music_group:
            music_group.layout().addRow('内置原创音乐',self.music_combo)
            music_group.layout().addRow(self.btn('✦ 使用这首并开启对白闪避',self.choose_builtin_music,True))
        transitions=[
            ('画中画展开 · 中心放大','pip_zoom','下一镜头从画中画卡片感逐渐放大到全屏，适合人物或地点揭示。'),
            ('数字撕裂 · 向左切片','tear_left','将画面切成横向条带错位切换，适合节奏点与情绪突变。'),
            ('数字撕裂 · 向右切片','tear_right','反方向横向条带撕裂，避免连续使用造成眩晕。'),
            ('像素化切换','pixelize','先像素化再显现下一镜头，适合科技、游戏或回忆段落。'),
            ('横向挤压','squeeze','旧画面被横向压缩，让出下一镜头。'),('放射展开','radial','从中心放射式揭示下一镜头。'),
            ('淡黑过场','fade_black','经过黑场切换，适合章节、时间或地点明显变化。'),('闪白过场','fade_white','经过白场切换，适合闪回、拍照或强节拍。'),
            ('覆盖推进','cover_left','下一镜头从右向左覆盖当前画面。'),('揭幕退出','reveal_right','当前镜头向右揭开下一镜头。')]
        for name,value,tip in transitions:
            idx=self.transition.findData(value)
            if idx<0:self.transition.addItem(name,value);idx=self.transition.count()-1
            self.transition.setItemData(idx,tip,Qt.ToolTipRole)
        self.transition.setToolTip('选择后点击“渲染当前效果预览”；画中画与撕裂均为真实帧合成。');self.transition.currentIndexChanged.connect(self.transition_explained)
        masks=[('菱形聚焦','diamond'),('竖条聚焦','vertical_strip'),('左侧清晰分屏','split_left'),('右侧清晰分屏','split_right'),('局部隐私模糊','privacy_blur'),('电影暗角','vignette')]
        for name,value in masks:
            if self.mask_shape.findData(value)<0:self.mask_shape.addItem(name,value)
        mask_page=self.inspector.widget(3);groups=mask_page.findChildren(QGroupBox) if mask_page else []
        preset_group=next((g for g in groups if g.title()=='一键智能预设'),None)
        if preset_group:
            grid=preset_group.layout();grid.addWidget(self.btn('菱形聚焦',lambda:self.apply_mask_preset('diamond')),3,0);grid.addWidget(self.btn('竖条聚焦',lambda:self.apply_mask_preset('strip')),3,1);grid.addWidget(self.btn('左侧分屏',lambda:self.apply_mask_preset('split_left')),4,0);grid.addWidget(self.btn('右侧分屏',lambda:self.apply_mask_preset('split_right')),4,1);grid.addWidget(self.btn('隐私模糊',lambda:self.apply_mask_preset('privacy')),5,0);grid.addWidget(self.btn('电影暗角',lambda:self.apply_mask_preset('vignette')),5,1)
        person_group=QGroupBox('AI 自动抠人 · 移动跟踪');person_layout=QVBoxLayout(person_group);person_note=QLabel('本地模型逐帧生成随人物移动的遮罩。适合人物清晰、背景虚化或压暗；无需 API。');person_note.setWordWrap(True);person_note.setStyleSheet('color:#536176');person_layout.addWidget(person_note);person_form=QFormLayout();self.person_mode=QComboBox();self.person_mode.addItem('人物清晰 · 背景虚化','blur');self.person_mode.addItem('人物清晰 · 背景压暗','dim');self.person_mode.addItem('人物抠出 · 深色背景','color');self.person_quality=QComboBox();self.person_quality.addItem('均衡 · 每 3 帧更新 AI','balanced');self.person_quality.addItem('快速 · 每 5 帧更新 AI','fast');self.person_quality.addItem('精细 · 每帧更新 AI','precise');self.person_strength=QSpinBox();self.person_strength.setRange(20,100);self.person_strength.setValue(72);self.person_strength.setSuffix('%');person_form.addRow('合成效果',self.person_mode);person_form.addRow('跟踪精度',self.person_quality);person_form.addRow('背景强度',self.person_strength);person_layout.addLayout(person_form);self.person_track_btn=self.btn('✦  AI 抠人并跟踪当前片段',self.start_person_tracking,True);person_layout.addWidget(self.person_track_btn);clear_person=self.btn('恢复当前片段原始背景',self.clear_person_effect);person_layout.addWidget(clear_person);self.person_progress=QProgressBar();self.person_progress.hide();person_layout.addWidget(self.person_progress);self.person_status=QLabel('首次处理按片段长度计算，建议先裁切片段再运行。');self.person_status.setWordWrap(True);self.person_status.setStyleSheet('color:#627187');person_layout.addWidget(self.person_status)
        if isinstance(mask_page,QScrollArea) and mask_page.widget():mask_page.widget().layout().insertWidget(2,person_group)
        trans_page=self.inspector.widget(2);self.transition_help=QLabel('基础转场适合连续叙事；高级转场建议只放在章节、反转或音乐重拍。');self.transition_help.setWordWrap(True);self.transition_help.setStyleSheet('color:#536176;background:#F6F8FB;border-radius:9px;padding:9px');trans_page.layout().insertWidget(max(0,trans_page.layout().count()-1),self.transition_help);self.transition_explained()
    def transition_explained(self,*_):
        value=self.transition.currentData();tips={self.transition.itemData(i):self.transition.itemData(i,Qt.ToolTipRole) for i in range(self.transition.count())};text=tips.get(value) or '克制使用：动作连续优先直接切换，段落变化优先淡化或溶解。'
        if hasattr(self,'transition_help'):self.transition_help.setText(text+' 点击“渲染当前效果预览”查看真实成片。')
    def alert(self,msg,err=False): (QMessageBox.critical if err else QMessageBox.information)(self,'灵剪 AI',msg)
    def format_time(self,t):return f'{int(t)//60:02d}:{int(t)%60:02d}.{int(t*10)%10}'
    def filter_media(self,text):
        q=text.lower()
        for i in range(self.media.count()):self.media.item(i).setHidden(q not in self.media.item(i).text().lower())
        if hasattr(self,'import_controller'):self.import_controller.refresh()
    def source_item_changed(self,item,previous=None):
        if not item:return
        path=item.data(Qt.UserRole)
        if not path or path not in self.metas:return
        self.source_path=path;self.source_in=0.;self.source_out=self.metas[path]['duration'];self.source_player.setSource(QUrl.fromLocalFile(self.proxies.get(path,path)));self.source_player.setPosition(0);self.update_marks()
    def source_duration(self,ms):
        if self.source_out<=0:self.source_out=ms/1000
    def source_position(self,ms):
        fps=max(1,self.metas.get(self.source_path,{}).get('fps',30));total=self.source_player.duration()/1000;self.source_time.setText(self.timecode(ms/1000,fps)+' / '+self.timecode(total,fps))
    def source_play(self):
        if not self.source_path:return
        if self.source_player.playbackState()==QMediaPlayer.PlayingState:self.source_player.pause()
        else:self.source_player.play()
    def source_pause(self):self.source_player.pause()
    def source_nudge(self,d):
        fps=max(1,self.metas.get(self.source_path,{}).get('fps',30));self.source_player.pause();self.source_player.setPosition(max(0,self.source_player.position()+round(d*1000/fps)))
    def mark_in(self):
        if self.source_path:self.source_in=min(self.source_player.position()/1000,self.source_out-.01);self.update_marks()
    def mark_out(self):
        if self.source_path:self.source_out=max(self.source_in+.01,self.source_player.position()/1000);self.update_marks()
    def update_marks(self):self.mark_label.setText(f'入点 {self.timecode(self.source_in)}   出点 {self.timecode(self.source_out)}   时长 {max(0,self.source_out-self.source_in):.2f}s')
    def timecode(self,t,fps=30):
        t=max(0,float(t));whole=int(t);frames=min(round((t-whole)*fps),round(fps)-1);return f'{whole//3600:02d}:{whole//60%60:02d}:{whole%60:02d}:{frames:02d}'
    def parse_timecode(self,s,fps=30):
        parts=[int(x) for x in s.strip().split(':')]
        if len(parts)!=4:raise ValueError('时间码格式应为 HH:MM:SS:FF')
        return parts[0]*3600+parts[1]*60+parts[2]+parts[3]/fps
    def seek_timecode(self):
        try:self.global_seek(self.parse_timecode(self.global_tc.text()))
        except Exception as e:self.alert(str(e),True)
    def insert_source(self):
        if not self.source_path:return self.alert('请先在素材库选择视频。')
        m=self.metas[self.source_path];self.remember();c=Clip(m['path'],self.source_in,self.source_out,m['name'],has_audio=m['has_audio']);index=self.current_clip+1 if self.current_clip>=0 else len(self.project.clips);self.project.clips.insert(index,c);self.changed('源片段已插入');self.select_clip(index)
    def overwrite_source(self):
        if not self.source_path:return self.alert('请先在素材库选择视频。')
        if self.current_clip<0:return self.insert_source()
        m=self.metas[self.source_path];self.remember();self.project.clips[self.current_clip]=Clip(m['path'],self.source_in,self.source_out,m['name'],has_audio=m['has_audio']);self.changed('已覆盖选中片段');self.select_clip(self.current_clip)
    def set_tool(self,tool):
        self.timeline.set_tool(tool);self.select_tool.setObjectName('primary' if tool=='select' else '');self.blade_tool.setObjectName('primary' if tool=='blade' else '');self.select_tool.style().unpolish(self.select_tool);self.select_tool.style().polish(self.select_tool);self.blade_tool.style().unpolish(self.blade_tool);self.blade_tool.style().polish(self.blade_tool);self.statusBar().showMessage('选择工具' if tool=='select' else '刀片工具：点击片段进行帧级分割')
    def toggle_snap(self):self.timeline.set_snap(not self.timeline.snap);self.snap_btn.setText(('⌁ 吸附开 N' if self.timeline.snap else '吸附关 N'));self.statusBar().showMessage('时间线吸附已'+('开启' if self.timeline.snap else '关闭'))
    def toggle_timeline_space(self):
        sizes=self.vertical_workspace.sizes();expanded=len(sizes)>1 and sizes[1]>sizes[0]
        self.vertical_workspace.setSizes([390,270] if expanded else [210,450]);self.timeline_space_btn.setText('▣  扩大时间线' if expanded else '▤  恢复预览区');self.statusBar().showMessage('已恢复预览区与三轨时间线' if expanded else '已扩大时间线，V1/T1/A1 三轨保持完整可见')
    def blade_split(self,index,at):
        if self.timeline.video_locked:return
        self.remember()
        try:self.project.split(index,at);self.changed('刀片分割完成');self.select_clip(index+1)
        except Exception as e:self.history.pop();self.alert(str(e),True)
    def track_state_changed(self,locked,muted):self.audio.setMuted(muted);self.statusBar().showMessage(f'视频轨：{"锁定" if locked else "可编辑"} · 原声轨：{"静音" if muted else "开启"}')
    def import_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'导入视频或音频','','媒体 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mp3 *.wav *.m4a *.aac *.flac *.ogg)')
        if paths:self.start_import(paths)
    def start_import(self,paths):return self.import_controller.enqueue(paths)
    def media_ready(self,m):
        if m['path'] in self.media_items:return
        self.metas[m['path']]=m;pix=QPixmap(m['thumbnail']);self.thumbs[m['path']]=pix;item=QListWidgetItem(QIcon(pix),f"{m['name']}\n{m['duration']:.1f}s · {m['width']}×{m['height']} · {m['codec']}");item.setData(Qt.UserRole,m['path']);item.setToolTip(f"{m['codec']} · {m['fps']:.2f}fps · {'有声音' if m['has_audio'] else '无声音'}");self.media.addItem(item);self.media_items[m['path']]=item;self.timeline.set_thumbnails(self.thumbs)
        if m.get('needs_proxy'):
            out=proxy_path(str(self.cache/'proxies'),m['path'])
            if Path(out).exists():self.proxies[m['path']]=out;item.setText(item.text()+'\n✓ 流畅代理已就绪')
            else:self.proxy_queue.append((m['path'],out));item.setText(item.text()+'\n⏳ 等待生成流畅代理')
        item.setHidden(self.search.text().lower() not in item.text().lower())
    def start_next_proxy(self):return self.import_controller.start_next_proxy()
    def proxy_done(self,src,out,ok,err):
        item=self.media_items.get(src)
        if ok:self.proxies[src]=out;item and item.setText(item.text().replace('⏳ 等待生成流畅代理','✓ 流畅代理已就绪'));self.notice.setText('代理生成完成：'+Path(src).name)
        else:item and item.setText(item.text().replace('⏳ 等待生成流畅代理','⚠ 代理生成失败'));self.notice.setText('代理生成失败，可继续使用原素材')
        if item:item.setHidden(self.search.text().lower() not in item.text().lower())
        if ok and 0<=self.current_clip<len(self.project.clips) and self.project.clips[self.current_clip].path==src:self.load_clip(self.current_clip,False)
        if ok and self.source_path==src:
            pos=self.source_player.position();self.source_player.setSource(QUrl.fromLocalFile(out));self.source_player.setPosition(pos)
    def add_selected_media(self):
        item=self.media.currentItem()
        if not item:return self.alert('请先选择一个视频素材。')
        self.add_media_path(item.data(Qt.UserRole),len(self.project.clips))
    def add_media_path(self,path,index):
        if path not in self.metas:return self.alert('素材仍在分析，请稍候。')
        self.remember();self.project.add(self.metas[path]);c=self.project.clips.pop();self.project.clips.insert(max(0,min(index,len(self.project.clips))),c);self.changed('片段已加入时间线');self.select_clip(max(0,min(index,len(self.project.clips)-1)))
    def remember(self):self.history=(self.history+[self.project.to_dict()])[-60:];self.redo_history.clear();self.update_history_buttons()
    def undo(self):
        if not self.history:return
        self.redo_history.append(self.project.to_dict());self.project=Project.from_dict(self.history.pop());self.restore_project('已撤销')
    def redo(self):
        if not self.redo_history:return
        self.history.append(self.project.to_dict());self.project=Project.from_dict(self.redo_history.pop());self.restore_project('已重做')
    def update_history_buttons(self):self.undo_btn.setEnabled(bool(self.history));self.redo_btn.setEnabled(bool(self.redo_history))
    def restore_project(self,msg=''):
        self.timeline.set_project(self.project);self.timeline.set_thumbnails(self.thumbs);self.current_clip=-1;self.update_history_buttons();self.refresh_sfx_list();self.refresh_overlay_list();self.statusBar().showMessage(msg or '工程已更新');self.update_document_state()
    def changed(self,msg):self.timeline.set_project(self.project);self.timeline.set_thumbnails(self.thumbs);self.refresh_overlay_list();self.statusBar().showMessage(f'{msg} · V1 {len(self.project.clips)} 段 · 叠加 {len(self.project.overlays)} 层 · {self.project.duration:.1f}秒');self.update_document_state()
    def update_document_state(self):
        if hasattr(self,'session'):self.session.refresh()
    def begin_canvas_edit(self):
        if not self._trim_snapshot:self.remember();self._trim_snapshot=True
    def end_canvas_edit(self):self._trim_snapshot=False;self.changed('时间线已调整')
    def timeline_trim(self,index,a,b):
        try:
            limit=self.metas.get(self.project.clips[index].path,{}).get('duration',b);b=min(b,limit);self.project.trim(index,a,b);self.timeline.reflow();self.timeline.update();self.populate_inspector(index)
        except:pass
    def timeline_move(self,a,b):self.project.move(a,b);self.current_clip=b;self.changed('片段顺序已调整');self.select_clip(b);self.learn_current_order(False)
    def timeline_zoom(self,v):self.timeline.set_zoom(v)
    def select_clip(self,index):
        if not (0<=index<len(self.project.clips)):return
        self.current_clip=index;self.timeline.selected_index=index;self.timeline.update();self.populate_inspector(index);self.load_clip(index,False)
    def populate_inspector(self,index):
        if self.caption_font.findText('楷体')<0:self.caption_font.insertItems(4,['楷体','仿宋'])
        c=self.project.clips[index];self.clip_label.setText(f'<b>{index+1}. {c.name}</b><br>{c.duration:.2f} 秒 · ID {c.id[:8]}');self.start.setValue(c.start);self.end.setValue(c.end);self.caption.setText(c.caption);self.pos.setCurrentIndex(max(0,self.pos.findData(c.position)));self.caption_font.setCurrentText(getattr(c,'caption_font','微软雅黑'));self.caption_size.setValue(getattr(c,'caption_size',42));self.caption_color.setCurrentIndex(max(0,self.caption_color.findData(getattr(c,'caption_color','#FFFFFF'))));self.caption_bg.setCurrentIndex(max(0,self.caption_bg.findData(getattr(c,'caption_bg','#000000'))));self.caption_bg_opacity.setValue(getattr(c,'caption_bg_opacity',.55));self.caption_effect.setCurrentIndex(max(0,self.caption_effect.findData(getattr(c,'caption_effect','clean'))));self.title_effect.setCurrentIndex(max(0,self.title_effect.findData(getattr(c,'title_effect',''))));self.title_text_edit.setText(getattr(c,'title_text','') or 'VLOG');self.transition.setCurrentIndex(max(0,self.transition.findData(c.transition)));self.transition_duration.setValue(getattr(c,'transition_duration',.35));self.mask_shape.setCurrentIndex(max(0,self.mask_shape.findData(getattr(c,'mask_shape','none'))));self.mask_x.setValue(getattr(c,'mask_x',.5));self.mask_y.setValue(getattr(c,'mask_y',.5));self.mask_width.setValue(getattr(c,'mask_width',.72));self.mask_height.setValue(getattr(c,'mask_height',.72));self.mask_feather.setValue(getattr(c,'mask_feather',28));self.mask_opacity.setValue(getattr(c,'mask_opacity',1));self.volume.setValue(c.volume);self.update_mask_guide()
    def load_clip(self,index,autoplay=False,local_time=None):
        if not (0<=index<len(self.project.clips)):return
        c=self.project.clips[index];effect=str(getattr(c,'person_effect_path','') or '');base_start=float(getattr(c,'person_effect_start',0));base_end=float(getattr(c,'person_effect_end',0));use_effect=bool(effect and Path(effect).exists() and c.start>=base_start-.001 and c.end<=base_end+.001);src=effect if use_effect else self.proxies.get(c.path,c.path);source_time=c.start if local_time is None else local_time;self.pending_position=int(max(0,source_time-base_start if use_effect else source_time)*1000);self.pending_autoplay=autoplay;self.player.setSource(QUrl.fromLocalFile(src));self.preview_stack.setCurrentWidget(self.video)
    def media_status(self,status):
        if status in (QMediaPlayer.LoadedMedia,QMediaPlayer.BufferedMedia) and self.pending_position is not None:
            pos=self.pending_position;auto=self.pending_autoplay;self.pending_position=None;self.player.setPosition(pos)
            if auto:self.player.play()
    def play(self):
        if not self.project.clips:return
        if self.current_clip<0:self.select_clip(0)
        if self.player.playbackState()==QMediaPlayer.PlayingState:self.player.pause()
        else:self.player.play()
    def on_position(self,ms):
        self.seek.setValue(ms);d=self.player.duration();self.time.setText(f'{self.format_time(ms/1000)} / {self.format_time(d/1000)}')
        if self.current_clip<0:return
        c=self.project.clips[self.current_clip];effect=str(getattr(c,'person_effect_path','') or '');base_start=float(getattr(c,'person_effect_start',0));base_end=float(getattr(c,'person_effect_end',0));using_effect=bool(effect and Path(effect).exists() and c.start>=base_start-.001 and c.end<=base_end+.001);source_position=ms/1000+base_start if using_effect else ms/1000;global_t=sum(x.duration for x in self.project.clips[:self.current_clip])+max(0,source_position-c.start);self.timeline.set_playhead(global_t);self.global_tc.setText(self.timecode(global_t,self.metas.get(c.path,{}).get('fps',30)))
        if self.player.playbackState()==QMediaPlayer.PlayingState and source_position>=c.end-.04:
            if self.current_clip+1<len(self.project.clips):self.current_clip+=1;self.timeline.selected_index=self.current_clip;self.populate_inspector(self.current_clip);self.load_clip(self.current_clip,True)
            else:self.player.pause()
    def local_seek(self,ms):self.player.setPosition(ms)
    def global_seek(self,t):
        acc=0
        for i,c in enumerate(self.project.clips):
            if t<=acc+c.duration:self.current_clip=i;self.timeline.selected_index=i;self.populate_inspector(i);self.load_clip(i,False,c.start+(t-acc));self.timeline.set_playhead(t);return
            acc+=c.duration
    def step_clip(self,d):
        if self.project.clips:self.select_clip(max(0,min(len(self.project.clips)-1,(self.current_clip if self.current_clip>=0 else 0)+d)))
    def nudge(self,d):
        if self.current_clip>=0:
            fps=max(1,self.metas.get(self.project.clips[self.current_clip].path,{}).get('fps',30));self.player.pause();self.player.setPosition(max(0,self.player.position()+round(d*1000/fps)))
    def current_source_time(self):
        if self.current_clip<0:return self.player.position()/1000
        c=self.project.clips[self.current_clip];effect=str(getattr(c,'person_effect_path','') or '');base=float(getattr(c,'person_effect_start',0));valid=bool(effect and Path(effect).exists() and c.start>=base-.001 and c.end<=float(getattr(c,'person_effect_end',0))+.001);return self.player.position()/1000+(base if valid else 0)
    def player_error(self,error,msg):
        if self.current_clip>=0 and self.project.clips[self.current_clip].path not in self.proxies:self.notice.setText('原素材预览失败，正在等待代理文件；导出不受影响。')
        else:self.notice.setText('预览错误：'+msg)
    def apply_clip(self):
        if self.current_clip<0:return self.alert('请先在时间线上选择片段。')
        self.remember()
        try:
            self.project.trim(self.current_clip,self.start.value(),self.end.value());c=self.project.clips[self.current_clip];c.caption=self.caption.text().strip();c.position=self.pos.currentData();c.caption_font=self.caption_font.currentText();c.caption_size=self.caption_size.value();c.caption_color=self.caption_color.currentData();c.caption_bg=self.caption_bg.currentData();c.caption_bg_opacity=self.caption_bg_opacity.value();c.caption_effect=self.caption_effect.currentData() if hasattr(self,'caption_effect') else 'clean';c.title_effect=self.title_effect.currentData();c.title_text=self.title_text_edit.text().strip() or 'VLOG';c.title_color=self.caption_color.currentData();c.title_fill_segments=self.build_title_fill_segments() if c.title_effect=='text_window' else [];c.transition=self.transition.currentData();c.transition_duration=self.transition_duration.value();c.mask_shape=self.mask_shape.currentData();c.mask_x=self.mask_x.value();c.mask_y=self.mask_y.value();c.mask_width=self.mask_width.value();c.mask_height=self.mask_height.value();c.mask_feather=self.mask_feather.value();c.mask_opacity=self.mask_opacity.value();c.volume=self.volume.value();self.changed('片段创作参数已应用');self.load_clip(self.current_clip,False)
        except Exception as e:self.history.pop();self.alert(str(e),True)
    def clear_caption(self):
        if self.current_clip<0:return
        self.caption.clear();self.apply_clip()
    def build_title_fill_segments(self):
        seen=set();segments=[];unique=[]
        for c in self.project.clips:
            if c.path not in seen:seen.add(c.path);unique.append(c)
        piece=max(.28,min(.75,3.6/max(1,len(unique))))
        for c in unique:
            available=max(.12,c.duration);duration=min(piece,available);start=c.start+max(0,(available-duration)*.5)
            segments.append({'path':c.path,'start':round(start,3),'duration':round(duration,3),'name':c.name})
        return segments
    def apply_caption_style_all(self):
        if self.current_clip<0:return
        self.remember();values=(self.caption_font.currentText(),self.caption_size.value(),self.caption_color.currentData(),self.caption_bg.currentData(),self.caption_bg_opacity.value(),self.pos.currentData())
        for c in self.project.clips:c.caption_font,c.caption_size,c.caption_color,c.caption_bg,c.caption_bg_opacity,c.position=values
        self.changed('字幕样式已应用到全部片段')
    def apply_transition_all(self):
        if not self.project.clips:return
        self.remember();kind=self.transition.currentData();duration=self.transition_duration.value()
        for i,c in enumerate(self.project.clips):c.transition='none' if i==0 else kind;c.transition_duration=duration
        self.changed('转场已应用到全部切点')
    def apply_mask_all(self):
        if self.current_clip<0:return
        self.apply_clip();self.remember();src=self.project.clips[self.current_clip]
        for c in self.project.clips:
            for key in ('mask_shape','mask_x','mask_y','mask_width','mask_height','mask_feather','mask_opacity'):setattr(c,key,getattr(src,key))
        self.changed('创作蒙版已应用到全部片段')
    def update_mask_guide(self,*_):
        if hasattr(self,'mask_guide'):self.mask_guide.set_values(self.mask_shape.currentData(),self.mask_x.value(),self.mask_y.value(),self.mask_width.value(),self.mask_height.value())
    def mask_position_changed(self,x,y):
        self.mask_x.setValue(x);self.mask_y.setValue(y);self.mask_help.setText(f'主体中心已移动到：水平 {x:.0%}、垂直 {y:.0%}。点击下方蓝色按钮即可看到真实成片效果。')
    def apply_mask_preset(self,kind):
        presets={
            'person':('spotlight',.50,.42,.66,.66,34,1.0,'人物聚焦：脸部放在蓝点附近，背景会明显柔化并稍微压暗。'),
            'product':('ellipse',.50,.56,.82,.58,40,1.0,'美食 / 商品：保留桌面主体，横向椭圆适合盘子、商品和手部动作。'),
            'card':('portrait_card',.50,.46,.72,.70,22,1.0,'口播卡片：主体区域保持清晰并加边框，周围背景柔化。'),
            'cinema':('cinema',.50,.50,1.0,.77,18,1.0,'电影感遮幅：自动增加上下黑边，不需要调整蓝点。'),
            'diamond':('diamond',.50,.48,.68,.62,30,1.0,'菱形聚焦：适合人物登场、商品揭示和章节封面。'),
            'strip':('vertical_strip',.50,.50,.42,.92,36,1.0,'竖条聚焦：中间区域清晰、两侧虚化，适合竖屏人物和建筑。'),
            'split_left':('split_left',.50,.50,.50,1.0,28,1.0,'左侧分屏：蓝点控制分界线，左边清晰、右边柔化。'),
            'split_right':('split_right',.50,.50,.50,1.0,28,1.0,'右侧分屏：蓝点控制分界线，右边清晰、左边柔化。'),
            'privacy':('privacy_blur',.50,.36,.38,.22,26,1.0,'隐私模糊：拖动蓝点覆盖人脸、车牌或敏感信息。'),
            'vignette':('vignette',.50,.50,.82,.82,28,1.0,'电影暗角：轻微压暗四周，适合情绪镜头；无需移动蓝点。')}
        shape,x,y,w,h,feather,opacity,help_text=presets[kind];self.mask_shape.setCurrentIndex(max(0,self.mask_shape.findData(shape)));self.mask_x.setValue(x);self.mask_y.setValue(y);self.mask_width.setValue(w);self.mask_height.setValue(h);self.mask_feather.setValue(feather);self.mask_opacity.setValue(opacity);self.mask_help.setText(help_text);self.update_mask_guide();self.statusBar().showMessage('智能蒙版参数已准备好；点击“应用并生成真实效果预览”确认')
    def smart_mask(self):
        if self.current_clip<0:return self.alert('请先在时间线上选择一个片段，软件才能根据它的画幅推荐蒙版。')
        c=self.project.clips[self.current_clip];meta=self.metas.get(c.path,{});w=float(meta.get('width') or 0);h=float(meta.get('height') or 0);kind='person' if h>=w else 'product';self.apply_mask_preset(kind);self.mask_help.setText(('检测到竖屏素材，已推荐人物聚焦。' if kind=='person' else '检测到横屏素材，已推荐横向主体聚焦。')+' 拖动蓝点对准主体，然后生成真实预览。')
    def apply_and_preview_mask(self):
        if self.current_clip<0:return self.alert('请先选择需要添加蒙版的时间线片段。')
        self.preview_effects()
    def start_person_tracking(self):
        if self.current_clip<0:return self.alert('请先在时间线上选择一个人物片段。')
        if self.person_thread and self.person_thread.isRunning():return self.alert('人物跟踪正在处理中，请等待完成。')
        model=ROOT/'models'/'u2net_human_seg.onnx'
        if not model.exists():return self.alert('人物分割模型缺失，请重新安装完整版。',True)
        c=self.project.clips[self.current_clip];mode=self.person_mode.currentData();quality=self.person_quality.currentData();strength=self.person_strength.value();key=hashlib.sha256(f'{c.path}|{c.start:.3f}|{c.end:.3f}|{mode}|{quality}|{strength}|{MODEL_SHA256}'.encode()).hexdigest()[:20];folder=self.cache/'person-effects';folder.mkdir(parents=True,exist_ok=True);out=str(folder/f'person-{key}.mp4');self.person_target_id=c.id;self.person_target_bounds=(c.start,c.end);self.person_track_btn.setEnabled(False);self.person_progress.setValue(0);self.person_progress.show();self.person_status.setText('正在启动本地人物分割模型…');self.person_thread=PersonTrackThread(c.path,c.start,c.end,out,str(model),mode,quality,strength);self.person_thread.progress.connect(self.person_tracking_progress);self.person_thread.done.connect(self.person_tracking_done);self.person_thread.failed.connect(self.person_tracking_failed);self.person_thread.start()
    def person_tracking_progress(self,value,message):self.person_progress.setValue(value);self.person_status.setText(message)
    def person_tracking_done(self,report,output):
        index=next((i for i,c in enumerate(self.project.clips) if c.id==getattr(self,'person_target_id','')),None);self.person_track_btn.setEnabled(True);self.person_progress.hide()
        if index is None:return self.person_status.setText('片段已被删除，处理文件保留在缓存中但未应用。')
        self.remember();c=self.project.clips[index];start,end=self.person_target_bounds;c.person_effect_path=output;c.person_effect_start=start;c.person_effect_end=end;c.person_effect_mode=self.person_mode.currentData();c.mask_shape='none';self.changed('AI 自动抠人与人物移动跟踪已应用');self.person_status.setText(f'完成：处理 {report.get("frames",0)} 帧，记录 {len(report.get("tracks",[]))} 个跟踪关键点。人物移动会由逐帧遮罩跟随。');self.select_clip(index);self.load_clip(index,False,c.start)
    def person_tracking_failed(self,message):self.person_track_btn.setEnabled(True);self.person_progress.hide();self.person_status.setText('处理失败：'+message);self.alert('AI 抠人与跟踪失败：\n'+message,True)
    def clear_person_effect(self):
        if self.current_clip<0:return
        c=self.project.clips[self.current_clip]
        if not getattr(c,'person_effect_path',''):return self.person_status.setText('当前片段没有人物跟踪效果。')
        self.remember();c.person_effect_path='';c.person_effect_start=0;c.person_effect_end=0;c.person_effect_mode='';self.changed('已恢复原始人物画面');self.person_status.setText('人物抠出效果已关闭，原始素材没有被删除。');self.load_clip(self.current_clip,False)
    def reset_mask(self):
        if self.current_clip<0:return
        self.mask_shape.setCurrentIndex(max(0,self.mask_shape.findData('none')));self.mask_x.setValue(.5);self.mask_y.setValue(.5);self.mask_width.setValue(.72);self.mask_height.setValue(.72);self.mask_feather.setValue(28);self.mask_opacity.setValue(1);self.mask_help.setText('蒙版已关闭，画面恢复原始显示。');self.update_mask_guide();self.apply_clip()
    def split_clip(self):
        if self.current_clip<0:return
        self.remember()
        try:self.project.split(self.current_clip,self.current_source_time());self.changed('片段已分割');self.select_clip(self.current_clip+1)
        except Exception as e:self.history.pop();self.alert(str(e),True)
    def duplicate_clip(self):
        if self.current_clip<0:return
        self.remember();data=dict(self.project.to_dict()['clips'][self.current_clip]);data.pop('id',None);self.project.clips.insert(self.current_clip+1,Clip(**data));self.changed('片段已复制');self.select_clip(self.current_clip+1)
    def delete_clip(self):
        if self.current_clip<0:return
        self.remember();i=self.current_clip;self.project.delete(i);self.current_clip=min(i,len(self.project.clips)-1);self.changed('片段已删除')
        if self.current_clip>=0:self.select_clip(self.current_clip)
        else:self.preview_stack.setCurrentWidget(self.placeholder)
    def move_clip(self,d):
        i=self.current_clip;n=i+d
        if i>=0 and 0<=n<len(self.project.clips):self.remember();self.project.move(i,n);self.changed('片段顺序已调整');self.select_clip(n);self.learn_current_order(False)
    def load_editing_profile(self):
        seed={'version':1,'samples':1,'chronology_strength':1.0,'hook_rate':1.0,'avg_clip_duration':3.5,'caption_font':'黑体','caption_position':'lower_third','transition':'none'}
        try:
            loaded=json.loads(str(self.settings.value('editing_profile','{}')));return loaded if loaded else seed
        except Exception:return seed
    def learn_current_order(self,notify=True):
        if len(self.project.clips)<2:
            if notify:self.alert('时间线至少需要两个片段后才能学习。',True)
            return
        self.editing_profile=learn_profile(self.editing_profile,self.project.clips);self.settings.setValue('editing_profile',json.dumps(self.editing_profile,ensure_ascii=False))
        if hasattr(self,'profile_status'):self.profile_status.setText(profile_summary(self.editing_profile))
        if notify:self.alert('已学习当前人工顺序。下一次 AI 成片会读取这份本地偏好。')
    def edit_prompt(self):return self.prompt.toPlainText().strip()+'\n导演风格：'+self.story_style.currentText()+'\n'+profile_prompt(self.editing_profile)
    def quick_ai(self):
        if not self.project.clips:return self.alert('请先将素材加入时间线。')
        draft=Project.from_dict(self.project.to_dict());draft.auto_edit(self.target.value());draft.clips.sort(key=lambda c:capture_key(c.path,c.start));paths=list(dict.fromkeys(c.path for c in draft.clips));analyses=[]
        for path in paths:
            meta=dict(self.metas.get(path) or {'path':path,'name':Path(path).name,'duration':max(c.end for c in draft.clips if c.path==path),'has_audio':True});analyses.append({'meta':meta,'segments':[]})
        sequence=[]
        for i,c in enumerate(draft.clips):sequence.append({'source_index':paths.index(c.path),'start':c.start,'end':c.end,'role':'setup' if i==0 else ('outro' if i==len(draft.clips)-1 else 'development'),'caption':c.caption,'reason':'快速本地节奏筛选','transition':'none'})
        try:self.review_ai_plan(build_edit_plan(sequence,analyses,self.target.value(),self.edit_prompt(),'quick'))
        except Exception as e:self.alert('快速方案生成失败：'+str(e),True)
    def deep_ai(self):
        metas=[]
        for p in dict.fromkeys(c.path for c in self.project.clips):
            if p in self.metas:
                m=dict(self.metas[p]);proxy=self.proxies.get(p)
                if proxy and Path(proxy).exists():m['analysis_path']=proxy
                metas.append(m)
        if not metas:return self.alert('请先导入并加入视频素材。')
        self.deep_btn.setEnabled(False);self.ai_progress.show();self.scene_thread=SceneThread(metas,self.target.value(),self.edit_prompt());self.scene_thread.progress.connect(lambda p,s:(self.ai_progress.setValue(p),self.ai_status.setText(s)));self.scene_thread.done.connect(self.deep_done);self.scene_thread.failed.connect(self.deep_failed);self.scene_thread.start()
    def style_generated(self,clips,sequence=None):
        sequence=sequence or []
        for i,c in enumerate(clips):
            role=str(sequence[i].get('role','development')) if i<len(sequence) else ('hook' if i==0 else 'development')
            c.transition='none' if i==0 else (str(sequence[i].get('transition','cut')).replace('cut','none') if i<len(sequence) else ('dissolve' if i%5==0 else 'none'));c.transition_duration=.32
            if getattr(c,'caption_font','微软雅黑')=='微软雅黑':c.caption_font='黑体' if role=='hook' else '微软雅黑'
            c.caption_size=54 if role=='hook' else 42;c.caption_color=getattr(c,'caption_color','#FFD43B' if role=='hook' else '#FFFFFF');c.caption_bg='#000000';c.caption_bg_opacity=.48;c.position='lower_third' if role=='hook' else 'bottom'
        return clips
    def deep_done(self,result):
        self.deep_btn.setEnabled(True);self.ai_progress.setValue(100)
        try:self.review_ai_plan(build_edit_plan(result['sequence'],result['analyses'],self.target.value(),self.edit_prompt(),'offline'))
        except Exception as e:self.alert('离线方案校验失败：'+str(e),True)
    def deep_failed(self,msg):self.deep_btn.setEnabled(True);self.alert('深度分析失败：'+msg,True)
    def load_api_settings(self):
        self.api_url.setText(self.settings.value('api/base_url','https://api.openai.com'));self.api_model.setText(self.settings.value('api/model','gpt-5-mini'));self.api_transcribe.setText(self.settings.value('api/transcription_model','gpt-4o-mini-transcribe'));self.api_refine.setChecked(str(self.settings.value('api/refine_highlights','false')).lower() in ('true','1'))
        try:self.api_key.setText(unprotect_secret(self.settings.value('api/key','')))
        except:self.api_key.clear();self.api_note.setText('已保存的密钥无法解密，请重新填写。')
    def save_api_settings(self):
        try:
            self.settings.setValue('api/base_url',self.api_url.text().strip());self.settings.setValue('api/model',self.api_model.text().strip());self.settings.setValue('api/transcription_model',self.api_transcribe.text().strip());self.settings.setValue('api/refine_highlights',self.api_refine.isChecked());self.settings.setValue('api/key',protect_secret(self.api_key.text().strip()));self.settings.sync();self.api_note.setText('接口设置已由 Windows DPAPI 加密保存。')
        except Exception as e:self.alert('接口设置保存失败：'+str(e),True)
    def api_config(self):return APIConfig(self.api_url.text().strip(),self.api_key.text().strip(),self.api_model.text().strip(),self.api_transcribe.text().strip(),refine_highlights=self.api_refine.isChecked())
    def cloud_ai(self):
        cfg=self.api_config()
        if not cfg.api_key:return self.alert('请先在“AI 接口”标签页填写并保存 API Key。')
        paths=list(dict.fromkeys(c.path for c in self.project.clips)) or ([self.source_path] if self.source_path else [])
        metas=[]
        for p in paths:
            if p in self.metas:
                m=dict(self.metas[p]);proxy=self.proxies.get(p)
                if proxy and Path(proxy).exists():m['analysis_path']=proxy
                metas.append(m)
        if not metas:return self.alert('请先导入视频，并加入时间线或在素材库中选择。')
        self.cloud_btn.setEnabled(False);self.ai_progress.show();self.ai_progress.setValue(0);self.cloud_thread=CloudAIThread(metas,cfg,self.cache/'ai-assets',self.edit_prompt(),self.target.value());self.cloud_thread.progress.connect(lambda p,s:(self.ai_progress.setValue(p),self.ai_status.setText(s)));self.cloud_thread.done.connect(self.cloud_done);self.cloud_thread.failed.connect(self.cloud_failed);self.cloud_thread.start()
    def cloud_done(self,result):
        self.cloud_btn.setEnabled(True);self.ai_progress.setValue(100)
        try:
            self.review_ai_plan(build_edit_plan(result['sequence'],result['analyses'],self.target.value(),self.edit_prompt(),'cloud'))
            if result.get('warnings'):self.ai_status.setText('；'.join(result['warnings']))
        except Exception as e:self.alert('AI 方案校验失败：'+str(e),True)
    def cloud_failed(self,msg):
        self.cloud_btn.setEnabled(True);self.ai_status.setText('AI 分析失败，原时间线未改变');self.alert(msg,True)
    def review_ai_plan(self,plan):
        base_plan=copy.deepcopy(plan);chosen=self.opening_style.currentData() if hasattr(self,'opening_style') else 'smart';plan.clear();plan.update(apply_global_creative_treatment(apply_opening_treatment(base_plan,chosen,self.story_style.currentText()),self.story_style.currentText(),'balanced'));report=plan.get('validation') or {}
        dialog=QDialog(self);dialog.setWindowTitle('AI 完整方案预览 · 高级开篇已生成');dialog.resize(880,740);layout=QVBoxLayout(dialog)
        summary=QLabel();layout.addWidget(summary)
        creative_box=QGroupBox('高级开篇方案 · 可整套切换，无需手调蒙版');creative_layout=QVBoxLayout(creative_box);opening_selector=QComboBox();[(opening_selector.addItem(name,value)) for name,value in opening_preset_choices()];opening_selector.setCurrentIndex(max(0,opening_selector.findData(chosen)));creative_layout.addWidget(opening_selector);creative_summary=QLabel();creative_summary.setWordWrap(True);creative_summary.setStyleSheet('color:#1769C2;background:#EDF6FF;border:1px solid #D5E9FF;border-radius:9px;padding:9px');creative_layout.addWidget(creative_summary);layout.addWidget(creative_box)
        detail=QPlainTextEdit();detail.setReadOnly(True);layout.addWidget(detail,1);note=QLabel('方案中的蒙版与高级转场会随镜头一起应用到时间线。确认仅替换一次时间线；之后仍可删减、分割或在右侧检查器修改。');note.setWordWrap(True);note.setStyleSheet('color:#6F7C8F');layout.addWidget(note);buttons=QDialogButtonBox();apply_btn=buttons.addButton('应用完整方案到时间线',QDialogButtonBox.AcceptRole);buttons.addButton('取消',QDialogButtonBox.RejectRole);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        def refresh_opening(value):
            candidate=apply_global_creative_treatment(apply_opening_treatment(base_plan,value,self.story_style.currentText()),self.story_style.currentText(),'balanced');plan.clear();plan.update(candidate);current=plan.get('validation') or {};creative=plan.get('creative_direction') or {}
            summary.setText(f'<b>{"✓ 方案可以应用" if current.get("ok") else "⛔ 方案已阻止"}</b>　预计 {float(current.get("duration",0)):.1f} 秒　正文顺序一致率 {float(current.get("chronology_ratio",1))*100:.0f}%')
            summary.setStyleSheet('color:#16834A' if current.get('ok') else 'color:#D14355')
            creative_summary.setText(f'<b>{creative.get("name","")}</b>　约 {float(creative.get("duration_hint",0)):.1f} 秒<br>{creative.get("summary","")}<br><span style="color:#607087">已自动写入前 {len(creative.get("shots") or [])} 个镜头的蒙版、位置、羽化和转场。</span>')
            detail.setPlainText(plan_preview_text(plan));apply_btn.setEnabled(bool(current.get('ok')))
        opening_selector.currentIndexChanged.connect(lambda _:refresh_opening(opening_selector.currentData()));refresh_opening(chosen)
        if dialog.exec()!=QDialog.Accepted:self.ai_status.setText('已取消 AI 方案，原时间线未改变。');return False
        report=plan.get('validation') or {};clips=plan_to_clips(plan,Clip);overlays=plan_to_overlays(plan,OverlayClip);self.remember();self.project.clips=self.style_generated(clips,plan['decisions']);self.project.overlays=overlays;self.apply_plan_sfx(plan);self.project.prompt=self.edit_prompt();self.project.edit_plan=plan;self.project.edit_log.append({'transaction_id':plan['plan_id'],'type':'apply_ai_plan','engine':plan.get('engine'),'clip_count':len(clips),'overlay_count':len(overlays),'duration':report.get('duration'),'opening':(plan.get('creative_direction') or {}).get('preset_id'),'sfx_count':len(self.project.sfx)});self.changed('AI 完整方案、多轨开篇与音效已作为一次可撤销操作应用');self.ai_status.setText(f'已应用 V1 {len(clips)} 个片段、V2+ {len(overlays)} 个叠加层、{len(self.project.sfx)} 个同步音效。每条叠加轨均可继续人工微调。')
        if clips:self.select_clip(0)
        return True
    def inspect_timeline(self):
        durations={p:float(m.get('duration',0)) for p,m in self.metas.items()};report=project_quality_gate(self.project,durations);lines=[f'机械质量分：{report["score"]}/100',f'状态：{"可以导出" if report["ok"] else "需要修复后再导出"}']
        if report['blockers']:lines.append('\n必须修复：\n- '+'\n- '.join(report['blockers']))
        if report['warnings']:lines.append('\n建议检查：\n- '+'\n- '.join(report['warnings']))
        if not report['blockers'] and not report['warnings']:lines.append('\n顺序、字幕可读性、片段范围和转场比例均通过机械检查。审美与叙事效果仍请在节目监视器中人工确认。')
        self.alert('\n'.join(lines),not report['ok']);return report
    def preview_effects(self):
        if self.current_clip<0:return self.alert('请先选择一个时间线片段。')
        if self.effect_thread and self.effect_thread.isRunning():return self.statusBar().showMessage('当前效果预览仍在渲染…')
        self.apply_clip();first=max(0,self.current_clip-1);data=self.project.to_dict();window_start=sum(c.duration for c in self.project.clips[:first]);selected=data['clips'][first:self.current_clip+1];window_end=window_start+sum(float(x['end'])-float(x['start']) for x in selected);data['clips']=selected;visible=[]
        for overlay in data.get('overlays',[]):
            global_start=float(overlay.get('timeline_start',0));duration=float(overlay.get('end',0))-float(overlay.get('start',0));global_end=global_start+duration;left=max(window_start,global_start);right=min(window_end,global_end)
            if right-left>.08:overlay['start']=float(overlay['start'])+(left-global_start);overlay['end']=float(overlay['start'])+(right-left);overlay['timeline_start']=left-window_start;visible.append(overlay)
        data['overlays']=visible;preview=Project.from_dict(data);token=hashlib.sha1(os.urandom(12)).hexdigest()[:10];out=str(self.cache/f'effect-preview-{token}.mp4');w,h,_=self.preset.currentData();scale=.5 if max(w,h)>1000 else .75;pw=max(320,int(w*scale)//2*2);ph=max(320,int(h*scale)//2*2);start=0
        if len(preview.clips)>1:start=max(0,preview.clips[0].duration-getattr(preview.clips[1],'transition_duration',.35)-.6)
        try:cmd=build_render_command(FFMPEG,preview,out,pw,ph,'standard')
        except Exception as e:return self.alert('效果预览失败：'+str(e),True)
        self.notice.setText('正在渲染字幕、转场和蒙版预览…');self.effect_thread=RenderThread(cmd,preview.duration,validator=probe_media);self.effect_thread.done.connect(lambda ok,msg:self.effect_preview_done(ok,msg,out,start));self.effect_thread.start()
    def effect_preview_done(self,ok,msg,out,start):
        if not ok:return self.alert('效果预览渲染失败：'+msg,True)
        self.pending_position=int(start*1000);self.pending_autoplay=True;self.player.setSource(QUrl.fromLocalFile(out));self.preview_stack.setCurrentWidget(self.video);self.notice.setText('正在节目监视器播放渲染后的创作效果预览')
    def overlay_geometry(self,layout):
        return {'pip_right':(.76,.25,.36,.29),'pip_left':(.24,.25,.36,.29),'center':(.5,.5,.56,.42),'full':(.5,.5,1.,1.)}.get(layout,(.76,.25,.36,.29))
    def add_overlay_at_playhead(self):
        if self.current_clip<0:return self.alert('请先选择一个 V1 片段作为叠加素材。')
        self.remember();source=self.project.clips[self.current_clip];duration=min(self.overlay_duration.value(),source.duration);x,y,w,h=self.overlay_geometry(self.overlay_layout.currentData());timeline_start=float(self.timeline.playhead);overlay=OverlayClip(source.path,source.start,source.start+duration,timeline_start,source.name,self.overlay_track.currentData(),self.overlay_layout.currentData(),self.overlay_mask.currentData(),x,y,w,h,10.,self.overlay_opacity.value(),True,role='manual_overlay',reason='创作者手动创建的独立叠加层');self.project.overlays.append(overlay);self.overlay_time.setValue(timeline_start);self.changed(f'已添加到 V{overlay.track} 叠加轨')
    def selected_overlay(self):
        item=self.overlay_list.currentItem() if hasattr(self,'overlay_list') else None
        if not item:return None
        overlay_id=item.data(Qt.UserRole);return next((x for x in self.project.overlays if x.id==overlay_id),None)
    def update_selected_overlay(self):
        overlay=self.selected_overlay()
        if not overlay:return self.alert('请先在列表中选择叠加层。')
        self.remember();overlay.track=int(self.overlay_track.currentData());overlay.timeline_start=self.overlay_time.value();limit=float(self.metas.get(overlay.path,{}).get('duration',overlay.start+self.overlay_duration.value()));overlay.end=min(limit,overlay.start+self.overlay_duration.value());overlay.layout=self.overlay_layout.currentData();overlay.mask_shape=self.overlay_mask.currentData();overlay.opacity=self.overlay_opacity.value();overlay.x,overlay.y,overlay.width,overlay.height=self.overlay_geometry(overlay.layout);self.changed('叠加层参数已更新')
    def remove_selected_overlay(self):
        overlay=self.selected_overlay()
        if not overlay:return self.alert('请先在列表中选择叠加层。')
        self.remember();self.project.overlays=[x for x in self.project.overlays if x.id!=overlay.id];self.changed('叠加层已删除')
    def overlay_selected(self,current,previous=None):
        overlay=self.selected_overlay()
        if not overlay:return
        self.overlay_track.setCurrentIndex(max(0,self.overlay_track.findData(overlay.track)));self.overlay_layout.setCurrentIndex(max(0,self.overlay_layout.findData(overlay.layout)));self.overlay_mask.setCurrentIndex(max(0,self.overlay_mask.findData(overlay.mask_shape)));self.overlay_opacity.setValue(overlay.opacity);self.overlay_time.setValue(overlay.timeline_start);self.overlay_duration.setValue(overlay.duration)
    def refresh_overlay_list(self):
        if not hasattr(self,'overlay_list'):return
        self.overlay_list.clear()
        for overlay in sorted(self.project.overlays,key=lambda x:(x.track,x.timeline_start)):
            item=QListWidgetItem(f'V{overlay.track}　{overlay.timeline_start:05.2f}s–{overlay.timeline_end:05.2f}s　{overlay.name}　{overlay.mask_shape}');item.setData(Qt.UserRole,overlay.id);self.overlay_list.addItem(item)
    def refresh_sfx_list(self):
        if not hasattr(self,'sfx_list'):return
        self.sfx_list.clear()
        for cue in self.project.sfx:
            item=QListWidgetItem(f'{cue.start:06.2f}s　{cue.name or cue.effect_id}　音量 {cue.volume:.2f}');item.setData(Qt.UserRole,cue.id);self.sfx_list.addItem(item)
        self.timeline.update()
    def preview_sfx(self):
        effect_id=self.sfx_combo.currentData();path=resolve_sfx(ROOT,effect_id)
        if not path or not Path(path).exists():return self.alert('找不到内置音效文件。',True)
        if self.sfx_player is None:self.sfx_player=QMediaPlayer(self);self.sfx_audio=QAudioOutput(self);self.sfx_player.setAudioOutput(self.sfx_audio)
        self.sfx_audio.setVolume(min(1.,self.sfx_volume.value()));self.sfx_player.setSource(QUrl.fromLocalFile(path));self.sfx_player.play()
    def add_sfx_at_playhead(self):
        effect_id=self.sfx_combo.currentData();meta=sfx_by_id(effect_id);path=resolve_sfx(ROOT,effect_id)
        if not meta or not Path(path).exists():return self.alert('找不到内置音效文件。',True)
        self.remember();self.project.sfx.append(SFXCue(path,float(self.timeline.playhead),meta['name'],self.sfx_volume.value(),effect_id));self.refresh_sfx_list();self.changed('音效已添加到播放头')
    def remove_sfx(self):
        item=self.sfx_list.currentItem()
        if not item:return
        cue_id=item.data(Qt.UserRole);self.remember();self.project.sfx=[x for x in self.project.sfx if x.id!=cue_id];self.refresh_sfx_list();self.changed('音效已删除')
    def apply_plan_sfx(self,plan):
        cues=[]
        for raw in plan.get('sound_cues') or []:
            effect_id=str(raw.get('effect_id') or '');meta=sfx_by_id(effect_id);path=resolve_sfx(ROOT,effect_id)
            if meta and Path(path).exists():cues.append(SFXCue(path,float(raw.get('start',0)),meta['name'],float(raw.get('volume',meta['volume'])),effect_id))
        self.project.sfx=cues;direction=plan.get('global_creative_direction') or {};music_id=str(direction.get('music_id') or '');music=music_by_id(music_id);path=resolve_music(ROOT,music_id)
        if music and Path(path).exists():self.project.bgm=path;self.project.bgm_id=music_id;self.project.bgm_volume=float(direction.get('music_volume',music['volume']));self.project.bgm_ducking=bool(direction.get('music_ducking',True));self.project.bgm_fade_in=float(direction.get('music_fade_in',1));self.project.bgm_fade_out=float(direction.get('music_fade_out',2));self.bgm.setText('AI · '+music['name']);self.bgm_volume.setValue(self.project.bgm_volume)
        self.refresh_sfx_list()
    def choose_builtin_music(self):
        music_id=self.music_combo.currentData();meta=music_by_id(music_id);path=resolve_music(ROOT,music_id)
        if not meta or not Path(path).exists():return self.alert('找不到内置音乐文件。',True)
        self.remember();self.project.bgm=path;self.project.bgm_id=music_id;self.project.bgm_ducking=True;self.project.bgm_volume=meta['volume'];self.bgm.setText('内置原创 · '+meta['name']);self.bgm_volume.setValue(meta['volume']);self.changed('已设置内置原创音乐，并开启对白自动闪避')
    def choose_bgm(self):
        p,_=QFileDialog.getOpenFileName(self,'选择背景音乐','','音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)')
        if p:self.remember();self.project.bgm=p;self.bgm.setText(Path(p).name);self.changed('背景音乐已设置')
    def auto_save(self):return self.session.autosave()
    def save_project(self):return self.session.save()
    def save_project_as(self):return self.session.save_as()
    def _save_project_to(self,path):return self.session.save_to(path)
    def open_project(self):return self.session.open()
    def preflight(self,out):
        if not self.project.clips:raise ValueError('时间线为空')
        missing=[c.path for c in self.project.clips if not Path(c.path).exists()]+[c.path for c in self.project.overlays if not Path(c.path).exists()]
        if missing:raise ValueError('素材已移动或断开：\n'+'\n'.join(missing[:5]))
        missing_sfx=[x.path for x in self.project.sfx if not Path(x.path).exists()]
        if missing_sfx:raise ValueError('音效文件已移动或断开：\n'+'\n'.join(missing_sfx[:5]))
        durations={p:float(m.get('duration',0)) for p,m in self.metas.items()};qa=project_quality_gate(self.project,durations)
        if not qa['ok']:raise ValueError('导出质量门禁未通过：\n- '+'\n- '.join(qa['blockers']))
        estimate=max(200_000_000,int(self.project.duration*2_000_000));free=shutil.disk_usage(Path(out).parent).free
        if free<estimate:raise ValueError('目标磁盘空间不足，请更换导出位置。')
        return qa
    def export(self):return self.export_controller.start()
    def closeEvent(self,e):self.session.close(e)

if __name__=='__main__':
    if len(sys.argv)>=4 and sys.argv[1]=='--person-smoke':
        source,out=sys.argv[2],sys.argv[3];process_person_video(FFMPEG,source,0,.34,out,ROOT/'models'/'u2net_human_seg.onnx','blur','fast',72);sys.exit(0)
    app=QApplication(sys.argv);app.setStyle('Fusion');app.setStyleSheet(STYLE);w=Main();w.show();sys.exit(app.exec())

