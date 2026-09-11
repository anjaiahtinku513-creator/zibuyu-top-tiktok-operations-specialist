# 子不语 · 跨境电商 AI 视频工作流

## Zibuyu Top TikTok Operations Specialist

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

把商品资料变成可验收、可交付、可排期的 TikTok 带货视频。将实际使用的跨境电商运营逻辑开源，让卖家、运营团队和开发者可以复用、改造，并接入自己的账号与工具。

本项目提供 **Codex 插件 + 五个工作流技能 + 本地可视化工作台**，覆盖商品卖点研究、服装三视图、差异化 UGC 分镜、多颜色与多模特制作、成片验收、当地语言文案，以及 TikTok 商品视频定时发布与回读核验。目前预设主要面向**美国与德国市场的服装商品**，其他市场和品类可在此基础上适配。

**Open-source AI workflow for cross-border ecommerce:** product evidence, garment references, localized UGC direction, batch production, video acceptance, captions, and audited TikTok scheduling. Current presets focus on US/DE apparel. Bring your own accounts and service access. See [setup and customization](docs/GETTING_STARTED.md).

## 能做什么

| 功能 | 实际工作与交付 |
| --- | --- |
| 商品与卖点研究 | 根据商品链接、图片与可获取的评论建立证据，区分已核实属性与创意表达，避免编造面料、功效和评价。 |
| 服装三视图 | 按颜色制作白底正面、侧面、背面参考图，去除人物身份，锁定领口、袖型、结点、下摆与颜色。 |
| 差异化 UGC 导演 | 将购买顾虑转化为展示动作，描述神态、情绪、口播语气、场景、镜头与衣料形变；支持英语或德语及中文审核翻译。 |
| 多颜色 × 多模特 | 每个颜色匹配所选模特，例如 3 位模特 × 3 个颜色得到 9 条计划；共用商品准备结果，分别记录进度。 |
| PopBoom 制作 | 上传参考资料、绑定固定模特、提交生成、查询状态、下载结果，保存任务身份和恢复记录。 |
| 成片验收与交付 | 分开记录技术检查、画面与服装一致性、用户验收、视频文件及完整文案，避免把提交成功当成视频合格。 |
| 本地工作台 | 上传、颜色归类、模特选择、阶段进度、用量记录、交付与发布准备，前后端运行在本机。 |
| TikTok 商品排期 | 绑定账号与商品 PID，将完整文案和五个标签写入视频标题，按账号当地日期及时区换算北京时间排期。 |
| 回读与防重复 | 查询平台保存的账号、商品、文案和时间；证据缺失或不符进入待核查，状态不明先核对，避免重复提交。 |

## 五个技能，可组合也可单独使用

| 技能 | 用途 |
| --- | --- |
| [主运营技能](skills/zibuyu-top-tiktok-operations-specialist/SKILL.md) | 商品资料、制作、整批验收、交付与发布交接。 |
| [clothing-three-view](skills/clothing-three-view/SKILL.md) | 每色无人物服装三视图。 |
| [seedance-ugc-cn-director](skills/seedance-ugc-cn-director/SKILL.md) | 中文导演计划与当地语言口播。 |
| [popboom](skills/popboom/SKILL.md) | 视频生成、任务查询、下载与恢复。 |
| [zibuyu-popboom-auto-publish](skills/zibuyu-popboom-auto-publish/SKILL.md) | 已验收视频的独立排期与平台回读审核。 |

```mermaid
flowchart LR
    A[商品链接与图片] --> B[卖点证据与三视图]
    B --> C[模特与差异化分镜]
    C --> D[批量视频制作]
    D --> E[实际成片验收]
    E --> F[视频与文案交付]
    F --> G[授权范围内排期]
    G --> H[平台回读核验]
```

## 快速开始

### 使用插件

下载 [插件 ZIP](https://github.com/anjaiahtinku513-creator/zibuyu-top-tiktok-operations-specialist/raw/main/plugin-packages/zibuyu-top-tiktok-operations-specialist.zip)，对照 [SHA-256](plugin-packages/zibuyu-top-tiktok-operations-specialist.zip.sha256) 校验，解压后通过支持本地插件的 Codex 安装入口加载 `zibuyu-top-tiktok-operations-specialist` 目录。安装包包含插件与许可证，工作台需下载完整仓库。

**先看 [接入与定制指南](docs/GETTING_STARTED.md)**：配置自己的 PopBoom 服务、固定模特及 TikTok 账号。德1、美1等别名与模型资源属于原部署预设，不代表你已取得使用权或访问权限。

配置后可以这样描述任务：

> 使用我已配置的三位德国模特，为三个颜色各制作一条视频，共九条。商品链接为……，货号为……。每条 15 秒，差异化展示版型、细节和活动度。提供德语口播及中文审核翻译，完成实际成片验收后交付视频与文案。

也可只使用导演计划、三视图，或对已验收视频使用独立发布技能。

### 运行本地工作台

目前提供 Windows 启动脚本。需要 Git、Node.js 22.13+、npm、Python，以及已安装并登录的 Codex CLI；先完成插件及外部服务配置。

```powershell
git clone https://github.com/anjaiahtinku513-creator/zibuyu-top-tiktok-operations-specialist.git
cd zibuyu-top-tiktok-operations-specialist/workbench
npm ci
.\start-local.ps1
```

打开 `http://127.0.0.1:4318/`，桥接服务监听本机 `127.0.0.1:4319`。任务默认保存到 `%USERPROFILE%\.codex\zibuyu-runs`。工作台调用已登录的 Codex CLI，不要求在工作台单独填写 OpenAI API Key；账号仍需可用额度。详见 [工作台说明](workbench/README.md)。

## 已固化的发布逻辑

- **视频标题 = 完整文案 + 恰好五个唯一、相关、非品牌标签**，合为一个段落传入 `video_title`。
- **以账号所在地时间为准**，用 IANA 时区和实际日期计算夏令时，再转换为北京时间 `+08:00` 提交，保留跨日结果。例如德国夏令时当地 18:00 对应次日北京时间 00:00。
- 用户明确要求发布，且账号、PID、日期和已验收视频范围完整时，沿用同一范围授权和已确认默认规则，不反复询问格式及时区转换。
- 逐条回读平台结果。现有 PopBoom 契约下，显式北京时间请求的无偏移时间回读按北京时间核对；显式偏移优先。更换服务或接口应重新验证契约。
- **排期创建、审核通过和最终发布成功是不同状态**。未知提交先查证，不以自动重发解决不确定性。

制作分辨率由服务参数决定，提示词中的“4K / 120fps”不能代替实际输出检查；选择 720p 生成时不会仅因提示词包含 4K 而成为原生 4K 视频。

## 开源范围与服务依赖

原创代码与项目文档采用 [MIT 许可证](LICENSE)，允许商业使用、修改和再分发，需保留许可证与版权声明。欢迎卖家、服务商和开发者建设自己的流程。

开源内容包括技能、规则、校验脚本、测试和本地工作台。**不包含 PopBoom 或 TikTok 服务本身、账号权限、付费生成额度、用户商品素材及视频成果**。对应自动化环节需要可用的外部工具与服务。第三方依赖和素材遵循自身许可，见 [第三方说明](THIRD_PARTY_NOTICES.md)。

## 参与建设

欢迎通过 [Issues](https://github.com/anjaiahtinku513-creator/zibuyu-top-tiktok-operations-specialist/issues) 反馈问题，通过 Pull Request 贡献更多市场、品类和服务适配。请阅读 [贡献指南](CONTRIBUTING.md)。本项目不保证流量、订单或收益。

| 目录 | 内容 |
| --- | --- |
| `.codex-plugin/` | 插件清单 |
| `skills/` | 五个技能、分阶段规则与校验器 |
| `hooks/`、`scripts/`、`tests/` | 上传处理、校验工具与测试 |
| `workbench/` | 工作台前后端、测试与启动器 |
| `docs/` | 接入与定制指南 |
| `plugin-packages/` | 插件 ZIP 与 SHA-256 |

插件版本：`0.1.0+codex.20260908143002`。开源说明与发布规则更新：2026-09-11。历史修改见 [CHANGELOG.md](CHANGELOG.md)。
