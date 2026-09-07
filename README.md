# Zibuyu Top TikTok Operations Specialist

Zibuyu 美国 / 德国 TikTok 服装视频制作插件与本地工作台。插件负责商品证据、三视图、导演计划、PopBoom 制作、实际成片质检与完整文案交付；工作台提供上传、颜色归类、多模特批次、进度、用量记录和发布准备。

插件版本：`0.1.0+codex.20260907023455`。工作台同步日期：2026-09-07。

## 本次优化

- 主流程、PopBoom 和导演技能改为短入口，按阶段读取详细规则；新增只读任务快照和校验结果摘要，保留完整校验回执。
- 工作台固定模特可多选，每个颜色使用全部所选模特；3 色 × 2 位模特 = 6 条计划视频。不同模特拥有独立会话和生成授权，US / DE 资料分别处理。
- 修复颜色识别的结构化输出兼容问题、错误提示丢失和重复长颜色名导致的卡住问题。
- 采用玻璃导航与进度栏、清晰的内容区；制作与发布状态分开，按阶段记录 CLI 原始 Token 用量。
- 整批真实验收与完整交付后才进入发布准备；最终发布表仍须明确授权，不明提交只核对、不重发。

完整变更与验证结果见 [CHANGELOG.md](CHANGELOG.md)。

## 安装插件

下载 [插件安装包](https://github.com/anjaiahtinku513-creator/zibuyu-top-tiktok-operations-specialist/raw/main/plugin-packages/zibuyu-top-tiktok-operations-specialist.zip)，解压后通过 Codex 的插件安装流程安装其中的 `zibuyu-top-tiktok-operations-specialist` 目录。校验和位于 [SHA-256 文件](plugin-packages/zibuyu-top-tiktok-operations-specialist.zip.sha256)。

安装包只包含插件文件。工作台源码位于 [workbench/](workbench/)，随完整仓库一起下载。

## 启动工作台

需要 Windows、Node.js 22.13 或更高版本、npm、Python，以及已经安装并登录的 Codex CLI。先安装插件，然后执行：

```powershell
git clone https://github.com/anjaiahtinku513-creator/zibuyu-top-tiktok-operations-specialist.git
cd zibuyu-top-tiktok-operations-specialist/workbench
npm ci
.\start-local.ps1
```

工作台在本机 `http://127.0.0.1:4318/` 运行，桥接服务只监听本机。任务数据默认保存到 `%USERPROFILE%\.codex\zibuyu-runs`，使用已登录的 Codex CLI，不需要单独填写 OpenAI API Key。更多配置、工作流与发布限制见 [工作台说明](workbench/README.md)。

**发布接口当前限制：** PopBoom 的完整正文及五标签传输方式尚未验证，真实排期提交仍被服务端阻断。保存发布意向和核对交付已可用；模型描述或前端参数不能解除限制。

## 仓库结构

| 目录 | 内容 |
| --- | --- |
| `.codex-plugin/` | 插件清单 |
| `skills/` | 五个工作流技能及分阶段规则、校验器 |
| `hooks/` | 原始图片本地上传处理与测试 |
| `scripts/` | 校验结果摘要工具 |
| `tests/` | 插件合同与恢复、交付测试 |
| `assets/` | 插件静态资源 |
| `workbench/` | 本地网站前后端源码、测试、依赖锁文件及启动器 |
| `plugin-packages/` | 插件 ZIP 与 SHA-256 校验和 |

仓库不包含登录凭据、用户商品图、生成视频、运行台账、授权回执、日志或依赖与构建缓存。
