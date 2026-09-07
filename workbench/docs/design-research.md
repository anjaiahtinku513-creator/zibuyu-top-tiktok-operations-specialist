# Zibuyu Studio 设计依据

核验日期：2026-09-07。目标是服装内容制作、成片验收与发布排期的实际工作表面。

## Apple 的层级，而非满屏特效

[Apple Human Interface Guidelines · Materials](https://developer.apple.com/design/human-interface-guidelines/materials?changes=l_8_2) 把 Liquid Glass 放在导航和控件功能层。内容层保持稳定，让用户关注自己要处理的对象。

据此，本网站将玻璃集中在侧栏、顶部栏、底部固定进度 Dock；商品表单、视频、完整文案和发布表使用白色内容面。蓝色只强调当前选择和主要操作，绿色表示实际验收/完成，警示色解释需要处理的状态。

具体配色、圆角和动画时长属于此项目设计选择，不是 Apple 官方给定的网页规范。避免宣称 CSS 模糊等同于原生 Liquid Glass 的完整光学渲染。

## GitHub 产品研究

| 项目 | 已核验用途 | 本次取舍 |
| --- | --- | --- |
| [Motion](https://github.com/motiondivision/motion) | MIT；React peer 包含 18/19，适合状态、布局、弹簧动画 | 参考其状态过渡与减少动态原则；当前变化可由 CSS 完成，无新增运行时 |
| [Base UI](https://github.com/mui/base-ui) | MIT；无预设样式的可访问控件，支持 React 19 | 继续使用已安装的基础组件，保留键盘与焦点行为 |
| [shadcn/ui](https://github.com/shadcn-ui/ui) | MIT；官方支持 React 19 与 Tailwind 4 | 复用现有 Dialog、按钮、表单、进度条，统一主题变量 |
| [Liquid Glass React](https://github.com/rdev/liquid-glass-react) | MIT；React >=18；README 指明 Safari/Firefox 位移折射受限，shader 模式仍不稳定 | 研究边缘高光，首版用 CSS 渐进增强，关键动作不依赖 shader |

兼容性资料：[Motion package](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion/package.json)、[Base UI package](https://raw.githubusercontent.com/mui/base-ui/master/packages/react/package.json)、[shadcn Tailwind 4](https://ui.shadcn.com/docs/tailwind-v4)、[Liquid Glass package](https://raw.githubusercontent.com/rdev/liquid-glass-react/master/package.json)、[Motion Accessibility](https://motion.dev/docs/react-accessibility)。仓库声明不等于本项目实际兼容；项目通过自身类型检查和构建验证。

## 页面决策

- 保留新建制作、任务队列、模特库、历史交付，增加明确的发布中心入口。
- 发布中心用批次列表与详情构成主从布局；窄屏转为纵向，不把完整文案压成一条不可读的表格行。
- 展示用户真正需要做的四步：成片交付、账号与日期、最终审核、排期结果。
- 状态、账号时区、视频与文案在同一工作区；技术参数与完整发布请求通过展开区保留可审核性。
- 只展示实际台账数字，空状态使用明确下一步，不填充演示销量或虚假已发布内容。
- 固定 Dock 占据正常布局空间，避免遮住提交按钮。只使用短暂透明度/位移动画，支持 reduced-motion 和模糊降级。

## 验证范围

验证包括发布状态与门禁测试、项目原有回归、TypeScript、lint、生产构建和本机 HTTP/API 检查。未执行真实 PopBoom 生成、上传或发帖来测试页面。浏览器视觉/交互专项测试需要实际执行后再记录，不能由代码构建结果推断。
