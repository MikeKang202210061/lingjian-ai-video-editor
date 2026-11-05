# 在 Windows 本机接续开发

用户要求把开发环境转到其 Windows 电脑，电脑已安装 **DaVinci Resolve**。
用户先前选择过 Studio，随后更正产品名称；**不能把 Studio 授权或外部脚本可用性视为已确认**。
本文件承接已完成的云端工作，供本地会话读取。迁移准备基线为 `9937f64f4fa17801ce74a72d0ad276b4d9509ec6`。

## 先把执行位置切到这台电脑

在 ChatGPT Windows 桌面应用中选择 Codex，创建或打开本地项目，将 **Work in → This computer** 作为运行位置。
只在浏览器打开原会话，或仅切换到电脑聊天，不会让云端获得本机文件、进程或 Resolve。
如果界面没有本地运行选项，先确认使用桌面应用，以及账号/工作区是否启用了本地功能。
不要通过公开网络端口、提交密钥或改变系统安全策略来代替这一步。

官方说明：[运行位置](https://learn.chatgpt.com/docs/environments/modes)、
[Windows 桌面应用](https://learn.chatgpt.com/docs/windows/windows-app)、
[本地环境](https://learn.chatgpt.com/docs/environments/local-environment)。

## 获取项目

本地已有仓库时，先检查 `git status`、remote 和分支，保护用户未提交的更改，不覆盖它们。
需要新副本时，可在 PowerShell 中运行：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\source" | Out-Null
Set-Location "$env:USERPROFILE\source"
git clone --branch codex/scene-aware-video-analysis --single-branch https://github.com/MikeKang202210061/lingjian-ai-video-editor.git
```

目标已存在时 `git clone` 会停止，不要因此删除该目录。也可以使用最新分支 ZIP，解压完整目录；
ZIP 不含 Git 历史，后续推送需要建立真实 Git 工作副本。
在桌面应用打开包含 `app.py` 的项目文件夹。

## 本地环境入口

- 双击 `setup_windows.cmd`：寻找 64 位 Python 3.12/3.11，建立项目 `.venv`，只在该环境安装 `requirements.txt`，
  执行 `pip check`，再做本机诊断。不会安装/购买 Resolve，不安装全局 Python 包，不修改系统权限或执行策略。
- 双击 `check_windows.cmd`：检查依赖版本、Qt/原生 DLL 启动、FFmpeg、两套 Skill、资源目录，
  并尝试**只读** Resolve 连接，报告实际产品名/版本/当前工程状态。
- 双击 `start_windows.cmd`：使用项目 `.venv` 启动灵剪。
- 诊断写在 `.local/environment.json`，仅留在本机并被 Git 忽略；不枚举环境变量、密钥或授权文件。

自动化运行时直接用以下命令，避免双击窗口最后的 `pause` 等待：

```powershell
py -3.12 scripts/local_environment.py setup
.\.venv\Scripts\python.exe scripts/local_environment.py check --resolve
.\.venv\Scripts\python.exe tests/run_tests.py
.\.venv\Scripts\python.exe app.py
```

只有 3.11 时用 `py -3.11`。缺少 Python 时从 [Python 官方](https://www.python.org/downloads/windows/)准备 64 位 3.12；
缺少 FFmpeg 时按 [官方页面](https://ffmpeg.org/download.html)获取适合 Windows 的版本，将 `ffmpeg.exe` 放项目根目录或加入 PATH。
若依赖、FFmpeg 或资源缺失，检查返回失败并列出缺口，不能据此认定环境已经就绪。
不能把云端 Linux 的 `.venv` 搬到 Windows 使用，必须重建对应平台依赖。

## 交给本地会话的下一条任务

> 阅读 docs/LOCAL_HANDOFF.md、docs/IMPROVEMENT_REVIEW.md、最新远端分支与 PR #1。
> 用户已授权研究、修改、测试和上传 codex/scene-aware-video-analysis；当前在 Windows 本机，安装产品名为 DaVinci Resolve，Studio 未确认。
> 先检查实际操作系统、工作目录及未提交改动，然后建立本地环境、运行诊断并报告实际产品/版本与 SDK 是否连接。
> 连接失败时依据安装版 Developer/Scripting README 排查，不能要求用户提供授权码或假装已做真实渲染。
> 继续处理未完成的 Fusion/动态关键帧、效果映射和 Windows 可运行交付；先完成有限范围的可验证增量，保留现有功能。
> 不购买服务、不调用真实付费模型、不合并 main。以最新分支为父提交上传，更新 PR 和改进记录；没有实际运行的验证不能写成通过。

## 当前程序与待办

### Windows 本机接续（2026-11-04）

已在用户电脑建立 Git 工作副本 `F:\应用测试\lingjian-ai-video-editor-local`，原 ZIP 解压目录保留。
项目 `.venv` 使用项目 `.local/python` 下的 Python 3.12.16；依赖、pip check、Qt、现有 FFmpeg、
两套 Skill 和资源目录检查已执行。之后从这个新目录运行 `start_windows.cmd`。

Resolve 进程安装在 `E:\达芬奇\Resolve.exe`，文件版本 `20.3.1.6`。
这只是安装文件证据，**不是 SDK 返回的产品名，也未确认 Studio 授权**。
自定义脚本库路径可保存到被忽略的 `.local/resolve.json`，诊断和界面共用，不修改全局环境：

```powershell
.\.venv\Scripts\python.exe scripts/local_environment.py check --resolve --resolve-library "E:\达芬奇\fusionscript.dll"
```

本机 Python 3.12.16 与 Anaconda 3.13.9 在加载该 DLL 时退出 `0xC0000005`；
现有 `F:\python\python.exe`（3.14.2）可加载 SDK，但 `scriptapp('Resolve')` 返回空。
没有操作用户工程或执行真实 Resolve 渲染。后续应先定位 SDK 与解释器兼容性、产品能力及 Local 设置，
再做 Fusion/关键帧与渲染验收；不要把模块加载或模拟 SDK 测试当作真实连接成功。

设置依据为本机 `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\README.txt`。
Resolve 左上角 DaVinci Resolve → Preferences → System → General；若提供 External scripting using，
选择 Local。没有该项时不要要求购买或提交授权，先核对安装产品的外部脚本能力。

- 仓库：`MikeKang202210061/lingjian-ai-video-editor`；分支：`codex/scene-aware-video-analysis`。
- PR：<https://github.com/MikeKang202210061/lingjian-ai-video-editor/pull/1>，保留草稿，不合并主分支。
- 两套参考型剪辑 Skill 已接到分析/方案/确认/撤销和保存；详见 [EDITING_SKILLS.md](EDITING_SKILLS.md)。
- Resolve 已有隔离进程、只读探测、新建自有工程、基础剪辑/渲染、取消、校验发布、独立预览及静态缩放/轻量饱和度映射。
  [RESOLVE_BACKEND.md](RESOLVE_BACKEND.md)列出初始配置、用户工程保护与能力边界。
- **未完成**：真实 Resolve 兼容性/授权能力与渲染验收，Fusion/动态关键帧和复杂效果映射，Windows 安装包更新及实机设备/字体/缩放验收。
- 未完整逐镜观看参考作者视频；配方是有来源边界的设计推断，不是作者官方规范或完整复刻。
- 云端本轮新增的迁移入口只有 Linux 上的测试与诊断代码验证，**未在用户电脑执行 `.cmd`，未完成本机依赖安装或 SDK 探测**。

确认迁移成功的证据应来自本机：工作路径是用户选定的 Windows 目录，实际命令由该目录 `.venv\Scripts\python.exe` 执行，
诊断反映真实 Windows 和 Resolve 状态。聊天端显示在电脑上，不是这一证据。
