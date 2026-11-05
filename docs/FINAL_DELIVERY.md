# 灵剪桌面版源码交付与验收记录

这是上午源码交付的历史基线记录。用户后续追加的实验性达芬奇后端及其独立验证范围，
见 [RESOLVE_BACKEND.md](RESOLVE_BACKEND.md)；不能将本页原基线的结果视为真实达芬奇已验收。

日期：2026-11-04（America/Los_Angeles）。

本次交付是**已验证的工作分支源码**，不是重新打包的 Windows 安装程序。
程序验证基线为 `4083686e581a6d413e750988ef330a9f40842f32`；本次最终提交只补充说明文件，
不改变已经检查的程序代码。主分支没有合并，PR 保留草稿。

- [草稿 PR #1](https://github.com/MikeKang202210061/lingjian-ai-video-editor/pull/1)
- [最新工作分支 ZIP](https://github.com/MikeKang202210061/lingjian-ai-video-editor/archive/refs/heads/codex/scene-aware-video-analysis.zip)
- [逐轮研究、功能取舍及实施记录](IMPROVEMENT_REVIEW.md)

## 已完成与保留的能力

| 范围 | 已落实的行为 |
| --- | --- |
| 桌面操作 | 小屏可滚动检查器、完整工具选择器、剪辑/双屏审片布局、输入焦点保护、导入/打开/另存为快捷键 |
| 工程保护 | 原子写入、未保存状态、关闭/换工程的保存/放弃/取消、独立自动恢复副本、恢复后另存为 |
| 素材工作流 | 追加导入排队、路径去重、搜索与可见数量、可复制的问题汇总；代理失败保留原素材与源预览路径 |
| 导出 | 单个窗口限制最终导出并发、取消及重试；先写临时 MP4、校验后替换，失败保留旧成片，禁止覆盖输入素材 |
| AI 分析 | 变化筛帧与最多 24 帧/3 张拼图，精看默认关闭；运行中任务重入保护，结果保留启动时的目标与提示词 |
| 原编辑能力 | 保留时间线剪切/分割/移动、源/节目监视器、方案预览与校验、确认应用、撤销/重做、本地 FFmpeg 导出与高级效果入口 |

冗余处理以复用为主：两个导出入口共用控制器，导出/预览/代理共用进程发布模块；
原保存和恢复入口共用工程会话。没有因缺乏使用数据的判断删除离线、云端、快速模式或高级效果。

结构改进将样式、工作区/键盘、工程文件写入、工程会话/恢复、导出控制、导入队列和后台媒体 I/O
拆分为独立模块，保留 `Main` 与 `Project.save()` 的既有入口。

## 最终实际执行的检查

环境：Linux、Python 3.12.14、PySide6 离屏平台、本地 FFmpeg。45 个 Python 文件与远端基线的
Git blob 哈希逐一一致。检查均使用本地生成素材或模拟模型结果，没有调用真实付费模型。

| 检查 | 结果与证据 |
| --- | --- |
| `python tests/run_tests.py` | 通过；工程保存/恢复、进程保护、采样请求及原有编辑计划/顺序/模拟 AI 回归 |
| 下方桌面联合命令 | 59 项通过；包括真实“导入→加入时间线→保存→重开→再次读取→导出→关闭”，原素材逐字节不变 |
| `python -m unittest tests.test_render_jobs_ffmpeg tests.test_visual_sampling_ffmpeg -v` | 8 项通过；成片校验、取消、失败保护、短预览、场景/时间戳/拼图/无音轨素材 |
| `python -m tests.test_core /tmp/lingjian-final-core` | 5 轮全部通过，每轮 17 次检查，共 85 次；含修剪、分割、移动、删除、工程回读、字幕和 H.264/AAC 成片 |
| 实际方案预览对话框检查 | 只读方案详情可见；取消保留原时间线；确认生成可编辑片段；撤销与重做分别恢复原工程与已应用方案 |
| 修改模块编译检查 | `compileall` 通过；未引入新的运行依赖 |

桌面联合检查命令（在仓库根目录运行）：

```text
python -m unittest tests.test_desktop_analysis tests.test_desktop_workflow_ffmpeg tests.test_desktop_import tests.test_desktop_media_workers tests.test_desktop_session tests.test_desktop_export tests.test_desktop_workspace -v
```

核心媒体测试应使用上表的模块方式运行；直接运行 `tests/test_core.py` 时需要另外配置根目录导入路径。
方案对话框检查使用本地合成计划和素材，未验证真实模型的叙事质量或费用。

## 在 Windows 电脑试用

1. 下载上方 ZIP 并解压，进入包含 `app.py`、`requirements.txt` 和 `assets` 的目录。
2. 准备 Python 3.11 或 3.12，以及 FFmpeg；在终端运行 `ffmpeg -version` 确认可用。
   也可把 `ffmpeg.exe` 放在 `app.py` 旁边。Python 下载入口见
   [Python Windows 页面](https://www.python.org/downloads/windows/)，FFmpeg 下载入口见
   [FFmpeg 下载页](https://ffmpeg.org/download.html)。
3. 在该目录打开 PowerShell，使用下方命令。若安装的是 Python 3.11，将第一行的 `-3.12` 改为 `-3.11`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

这些命令直接使用虚拟环境内的解释器，不要求运行 PowerShell 激活脚本；
虚拟环境用法参见 [Python venv 官方说明](https://docs.python.org/3.12/library/venv.html)。
以上是电脑试用指引，尚未在真实 Windows 电脑上执行安装验收。

首次试用可先导入一段短视频、加入时间线、保存为 `.ljproject`，再导出 MP4。
本地编辑和导出无需付费模型。人物分割的可选模型不在 ZIP 内，安装要求见 README；
普通剪辑不要求先下载该模型。

## 尚未完成或未实测

- **未重打 Windows 安装包**；旧安装包不包含这些分支改动。
- **未执行 Windows 实机、显示缩放、中文输入法、音视频设备和安装程序验收**；离屏通过不等同这些已通过。
- 未实测真实付费模型、真实用户素材的叙事质量、费用、全部高级效果组合、完整内置字体/音效/音乐与人物分割模型。
- 导入/代理/AI 尚无通用取消和探测超时；其他任务生命周期、旧代理有效性、跨窗口仲裁仍有待处理。
- 素材重新定位、系统文件拖入、最近工程等候选没有在截止前扩展实现。
- 恢复只覆盖已序列化工程及工程名/配乐音量，不恢复未应用参数、未使用素材库、播放器位置或运行任务。

没有为最终交付增加新功能，也没有把未运行的检查写成通过。
