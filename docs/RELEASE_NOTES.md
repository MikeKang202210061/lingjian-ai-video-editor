# 灵剪 AI 4.13 · 全片创意导演

## 已实现

- AI 不再只设计片头：为全片建立章节、动作、时间、冲突、细节、揭示、回收事件图。
- 强视听强调有最小间隔，普通因果切点保持干净，避免 120 秒视频后半段被随机特效淹没。
- 真实渲染三种镜头运动：慢推、冲击推近、轻手持；按语义自动匹配高级转场。
- 八种可编辑字幕动效：干净、Q 弹果冻、节奏滑动、逐字输入感、印章、气泡、重点高亮、极简。
- 三款可随安装包分发的开源中文字体：站酷快乐体、站酷庆科黄油体、霞鹜文楷。
- 音效库由 10 个扩至 16 个，新增章节蓄力、低频坠落、结果叮、气泡、柔和钟声、心跳。
- 六首本地原创循环音乐；AI 按旅行、美食、生活、剧情、纪录、口播风格自动选曲。
- 背景音乐具备开头/结尾淡化与对白侧链闪避，人物说话时自动压低音乐。
- 方案预览列出全片所有重点事件的时间、动效、字幕样式、音效和自动配乐，应用后仍可逐项人工修改。

## 质量保证

- DJI 真实素材全组合渲染连续 5 轮通过。
- 核心 85 项连续 5 轮通过。
- 101 片段 / 365.4 秒正文顺序修复连续 5 轮通过。
- 多轨合成、8 套创意开篇、16 音效混音旧功能回归全部通过。


# Scene-aware footage analysis

- Local change/motion screening plus full-video coverage, capped at 24 frames in
  three labelled contact sheets per source. The visual overview remains one call.
- Source-relative decoded frame timestamps and a stable, source-aware cache with
  an inspectable manifest. Silent clips can be analyzed without a transcript.
- Optional candidate-boundary refinement in AI 接口, off by default: up to two
  candidates, eight frames, and one extra visual request per source. Invalid moves
  are rejected; provider/extraction failures retain the initial plan and report a warning.
- Existing preview, plan validation, application, undo, and rendering are retained.
- Added portable selection/request/fallback tests and real FFmpeg scene, timestamp,
  contact-sheet grouping, silent-media, and cache-recovery tests.
- Limits: four-fps screening can miss very short events; extra images/refinement
  may increase API cost. No live-provider quality or Windows GUI claim is made by
  the automated checks.

# Desktop workspace and project saves

- Compact monitor tabs plus an optional dual-monitor review workspace.
- A named inspector selector, scrollable parameter pages and a two-row timeline
  toolbar improve access on 1366×768 and smaller desktop windows.
- Text-entry focus keeps editing keys inside captions/prompts; timeline shortcuts
  remain available when the timeline has focus.
- File-menu Import, Open and Save As commands; failed saves retain the active path.
- Atomic project writes shared by manual save and autosave; styles, workspace
  behavior and persistence are separated from the application controller.
- Six offscreen desktop interaction tests and three persistence tests added.
- Research, feature tradeoffs and remaining priorities: `docs/IMPROVEMENT_REVIEW.md`.

# Unsaved project protection and recovery

- Shared Save / Discard / Cancel handling for close and project replacement;
  cancelled or failed saves preserve the current document.
- Visible modified/saved/new status, based on content rather than undo-stack size.
- Independent recovery snapshots per window, a startup banner and File-menu
  restore chooser; restored documents use Save As to protect the original.
- Legacy autosave support, unchanged-snapshot write avoidance and selective cleanup.
- Opening the current file after Save reloads the newly saved version.
- Close/project replacement waits for existing background work to finish; general
  cancellation and concurrency management remain pending.
- Six portable session tests and fourteen offscreen interaction tests added.

# Protected and cancellable export

- One final export per window, shared busy states for both existing export
  buttons, and a visible cancel control in the export inspector.
- Retain the worker until its finished signal, restore controls after success,
  failure or cancellation, and allow retry without replacing a running thread.
- Render the startup timeline snapshot to a unique adjacent temporary MP4;
  publish only after encoding and validation succeed. Failed or cancelled jobs
  preserve an existing output and clean temporary files when possible.
- Reject input-file destinations, including hard links and symbolic links.
- Export and effect previews share process/error handling; short previews use
  media probing rather than the final-export minimum file-size requirement.
- Ten portable process tests, four real FFmpeg tests and eleven offscreen export
  tests added. General import/AI cancellation and Windows device/installer
  validation remain pending.

# Serialized imports and proxy lifecycle

- Additional imports queue behind the active read; equivalent paths and already
  known/pending videos are skipped without replacing a running worker.
- The library displays visible/total videos, pending reads, proxy jobs and issue
  counts. New items and proxy-status changes respect the active search.
- Read failures are collected in copyable, bounded issue details instead of one
  modal dialog per file. Failed reads can be retried after their batch finishes.
- Retain read/proxy workers until `finished`; queued work also blocks closing or
  switching projects. Catch worker startup and media/cache errors.
- Proxy rendering shares validated temporary publication. A failed proxy keeps
  original media and the source monitor path available.
- Fourteen import interaction tests and six real worker checks added, including
  locally generated FFmpeg media. General read/AI cancellation, old-cache
  validation and Windows device/installer checks remain pending.

# Analysis consistency and workflow verification

- Offline, cloud and quick analysis share a re-entry check so a running analysis
  keeps its worker and result UI. Existing analysis choices remain available.
- Offline/cloud result envelopes retain the startup target and prompt rather
  than reading changed controls when the result arrives.
- Seven mocked-provider/re-entry checks and one generated-media desktop workflow
  added. Real import, save, project reopen, export publication and source-file
  preservation pass together with the existing desktop regressions.
- Branch trial instructions distinguish source changes from the old installers.
- This remains a draft source update; Windows hardware and installer acceptance,
  general cancellation and the remaining task lifecycles are not claimed complete.

# Experimental DaVinci Resolve backend

- Optional, collapsed controls in Audio & Export for local SDK detection, a
  restricted basic-edit render, cooperative cancellation and an editor-owned
  output preview window. The FFmpeg backend remains independent.
- Existing reviewed AI timelines supply media paths, source ranges and sequence;
  a fixed JSON contract is expanded by an isolated Python SDK process. No
  model-generated Python or shell commands are executed.
- New owned projects only; an existing open Resolve project blocks execution.
  Output is rendered beside the destination, checked and then published.
  Unknown native task state preserves its working files and requires inspection.
- Basic mode does not transfer captions, effects, overlays or mixing; an explicit
  confirmation lists the omitted capabilities before sending.
- Twenty protocol/client/simulated-SDK tests and fourteen offscreen UI tests added;
  portable, 73 desktop and eight existing real-media checks pass.
- No real Resolve installation, Studio license, SDK render, GPU or advanced Fusion
  template was tested. This is a source prototype, not a validated Resolve release.
- Setup and boundaries: `docs/RESOLVE_BACKEND.md`.

