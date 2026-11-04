# 两套可执行剪辑 Skill

基于 `59cc7cbd5da07e4306efe554236c8b620b6e0f02`，按用户追加要求实现。
Skill 保存在灵剪仓库中，由桌面程序读取，不是安装到 ChatGPT 的个人技能，也不是作者官方资源。

| Skill | 分析与编排重点 | 当前配方 |
| --- | --- | --- |
| [科技实验讲解 · 参考何同学](../skills/hetongxue-inspired/SKILL.md) | 问题、操作细节、失败修正、证据、结果回收 | 至少间隔 8 秒强调细节/结果；已有字幕高亮、重点运镜、轻提示音、低配乐 |
| [影像探索纪实 · 参考影视飓风](../skills/mediastorm-inspired/SKILL.md) | 空间建立、行动过程、远中近景、现场声、发现与回望 | 至少间隔 16 秒强调发现/回收；克制运镜、简洁字幕、默认无装饰音效、更低配乐 |

镜头时长、颜色和效果参数是本项目的设计推断，**不是作者公布的规范或全频道统计**。
未完整播放参考视频，不声称逐镜复刻、已实现全部特效或保证同等成片质量。

## 电脑试用

1. 下载分支最新源码，按 [桌面试用步骤](FINAL_DELIVERY.md)准备 Python 和 FFmpeg，运行 `python app.py`。
   保留完整 `skills` 目录。旧 Windows 安装程序没有更新。
2. 导入素材并加入时间线，在“AI 导演 → 剪辑 Skill”选择配方，输入真实故事目标。
3. 可先用“离线分析”或“快速节奏方案”，无需模型费用。离线模式只知道场景/质量及现有角色，
   不能保证认出实验、地点、人物或结果。已自行配置接口时，可主动运行云端分析；Skill 正文会同时进入素材分析和总剪辑提示词。
4. 阅读方案的来源范围、理由、Skill 版本/摘要、能力表与未映射项目，再应用。
   `Ctrl+Z` 整体撤销；重做和工程保存保留配方及审计快照。
5. 用“效果预览”或本地导出检查效果，节目监视器的原片播放不代表全部处理。
   内置声音资源存在时才应用，缺少资源不能视为已听到配乐；本轮未验收全资源包。
6. 已有兼容 Resolve Studio 时，按 [达芬奇配置](RESOLVE_BACKEND.md)连接并实验导出。
   初始配置及异常仍可能需要进入达芬奇；不自动安装/购买/授权，不保证完全隐藏窗口。

“不使用 Skill”保留原通用导演流程。选择 Skill 后，默认“AI 智能推荐”使用干净开头；
明确指定高级开篇仍可叠加原有能力。用户事实和限制优先，其次 Skill，再参考通用导演风格。

## 指令与执行

每套包括 `SKILL.md`、`profile.json` 和 `references/sources.md`。
`editing_skills.py` 只接受登记 ID，校验文档、版本、字段、白名单与范围，不加载任意目录或执行脚本。
模型仍输出原有结构化候选与序列，不能提供任意 SDK 方法或可执行代码。
开始任务时捕获正文和配方并深拷贝进线程；之后换选项/改文件不会替换运行中任务。
方案保存正文、配方、版本及 SHA256；摘要用于一致性和审计，不是第三方可信签名。
确定性编译器保留镜头来源、入出点、字幕事实和正文顺序，只安排已有重点效果，不生成不存在的素材。

Resolve 配方绑定在 `Clip.resolve_treatment`，随镜头重排、分割、工程保存和撤销/重做移动。
从当前时间线生成请求，不把历史方案镜头序号套在修改后的工程上。
`render_basic` 仅增加可选受限 `treatment`，禁止任意 Python、Shell、方法名、LUT/模板路径。

| 后端 | 当前执行 | 限制 |
| --- | --- | --- |
| 本地 FFmpeg | 原有剪切、已有字幕强调、现有运镜、可用内置声音资源 | 未新增三维、跟踪或速度曲线；不执行 Resolve 专用饱和度 |
| Resolve 实验映射 | 导入裁切与顺序；`ZoomX/ZoomY` 静态构图；首个节点的中性 CDL 加小幅 Saturation | 不等同本地成片或动态推镜；字幕、声音设计、转场、叠加、Fusion、关键帧未映射 |

Resolve 两套起点分别为重点镜头 1.06 倍/饱和度 1.03，与 1.025 倍/饱和度 0.97；普通镜头保持 1 倍。
缩放范围 1–1.12，饱和度 0.85–1.15。这不是作者调色，不负责曝光、白平衡或 Log/色彩转换。
接口拒绝任一属性/CDL 即失败，不静默跳过；旧输出保留，可检查新建的自有工程。

## 研究证据

- 何同学原账号：[自己打字的键盘](https://www.bilibili.com/list/163637592?bvid=BV1W14y1b7Mq&oid=771908203)、
  [一万行备忘录动画](https://www.bilibili.com/list/163637592?bvid=BV1Xi4y1x7eM&oid=540601597)。
  简介区分实拍装置与预录动画，并列出程序、动画、特效等工作。
- 影视飓风原账号：[后期流程](https://www.bilibili.com/video/BV1mv411e7DE/)、
  [摄影测量](https://www.bilibili.com/video/BV1yS4y1w7V1/)、
  [得克萨斯探索](https://www.bilibili.com/list/946974?bvid=BV1LoN76dEmF&oid=116894749233171)。
  公开简介支持分层流程、地点探索和技术过程题材，不能证明具体切点、缓动、调色或声轨参数。
- 2026-11-04 核对以上第一方简介；直接打开原视频返回 412，未完整观看。配方的节奏/色彩选择是设计推断。
- [Blackmagic 官方 Studio 页](https://www.blackmagicdesign.com/products/davinciresolve/studio)介绍脚本 API；
  [官方 20.2 更新说明](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20.2_New_Features_Guide.pdf)指向安装版 Developer 文档。
  接口合同核对随软件分发的 Scripting README 文本，其 [公开镜像](https://gist.github.com/X-Raym/2f2bf453fc481b9cca624d7ca0e19de8)
  不是官方托管。以用户实际安装版 README 和实机结果为准。

## 实际验证和未验收项

- `python tests/run_tests.py` 通过，含新增 9 项 Skill/参数/模拟 SDK 检查及原 20 项后端检查。
- 原 Qt 8 模块联合运行 73 项通过；另运行 `python -m unittest tests.test_desktop_editing_skills -v`，6 项通过。
  覆盖选择器、提示词、任务快照、失效配方拒绝、默认/指定开篇、真实对话框取消、应用/撤销/重做。
- `python -m unittest tests.test_editing_skills_ffmpeg tests.test_render_jobs_ffmpeg tests.test_visual_sampling_ffmpeg -v`：9 项通过。
  新媒体检查实际生成素材，将两套配方分别渲染 H.264/AAC，校验尺寸、时长、原声、源文件未变及解码帧不同。
  它证明本地链路，不证明作者风格相似度或真实 Resolve 效果。
- 修改模块及测试编译通过。1366×768、1120×680 离屏检查 AI 面板宽度未超出视口。
  此轮缺少中文字体，截图出现方框，未据此宣称中文排版通过视觉验收；Windows 字体/缩放仍需实测。
- 没有安装/连接真实达芬奇，未调用付费模型。原生 SDK、授权、GPU、CDL 节点、SDK 镜头返回顺序、帧精度及 Fusion 未验收。

保留原功能，通过共享选择器、纯数据编译器和现有预览/撤销/导出路径接入，没有新增独立编辑器或重复任务页面。
