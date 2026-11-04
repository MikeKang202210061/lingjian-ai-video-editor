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

