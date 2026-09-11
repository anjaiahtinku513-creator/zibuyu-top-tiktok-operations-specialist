# 接入自己的跨境电商工作流

这是现有 US/DE 服装流程的开源实现，包含原部署预设。首次接入需要配置，不能直接用作者的模特别名与账号生成或发布。

## 准备工具与资源

安装支持本地插件的 Codex，并配置图像生成工具与 PopBoom MCP 服务。完整制作需要商品链接、每色商品图、货号、市场、语言、已授权固定模特与生成额度；发布还需要自己的 TikTok 账号绑定、商品 PID 和发布日期。仓库不提供共享账号或公共免费生成服务。

## 替换原部署预设

| 位置 | 检查或替换 |
| --- | --- |
| `skills/popboom/assets/fixed-model-registry.json` | 服务端点、租户、模特资源 ID、参考图与真实身型资料。 |
| `skills/zibuyu-popboom-auto-publish/SKILL.md` | 账号别名、用户名、市场和 IANA 时区；运行时查询真实 channel_id。 |
| `workbench/server/publishing.mjs` | 工作台账号预设，与个人发布技能保持一致。 |
| `workbench/server/workflow.mjs` | 检查工作流与模特预设是否需同步个人注册表。 |

在自己的分支维护配置，凭据留在本机工具连接中，不提交 GitHub。当前尚未把所有租户预设抽成统一配置界面，修改后需验证技能和工作台入口的一致性。德国通常使用 `Europe/Berlin`；美国需使用具体账号所在地时区，不能一律视为纽约。

## 验证一个最小批次

先用一个颜色与一位自有模特验证服装结构、口播、实际规格和完整交付，再扩展多模特多颜色。付费生成在用户授权范围内执行。技术检查、画面审核与用户验收分别记录，不把用户验收改写为机器完成所有视觉检查。

## 接入发布

选择已验收视频，绑定账号、PID、当地日期和时段，标题为当地语言完整文案加五个唯一相关非品牌标签。账号当地 IANA 时间转北京时间提交，保留当地时间、北京时间和 UTC 时间点用于核对。

已授权且资料完整的范围直接执行，不重复确认固定格式。更换接口或租户后验证文案传输与时间契约。回读缺失或不符时保留任务 ID 并核查，不重复提交；排期创建不等于最终发布成功。

## 其他市场与品类

复用“商品证据 → 展示动作 → 实际验收 → 完整交付 → 排期回读”的逻辑。新增市场需适配语言、商品来源、尺码单位、账号时区和文案规则；新增品类需调整产品结构与动作约束；新增服务需验证回执、恢复及防重复。服装专用提示词不能直接当作其他品类的可靠预设。

## English setup notes

Configure your own Codex plugin, image/video services, authorized model assets, and TikTok channels. Replace the original deployment presets above. Validate one color and one model before scaling. Scheduling requires accepted media, product IDs, complete captions, account-local time zones, and authorization. Services and credits are not bundled. Other markets and categories require adaptation.
