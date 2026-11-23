# Whale Core Console

为 [amwangfan/whale-core](https://github.com/amwangfan/whale-core) 重新设计的中文智能中枢控制台。React 19、TypeScript、Vite 与 Tailwind CSS v4。插画和字体均随项目提供，普通使用不请求外部图片、统计或字体服务。

## 开发与预览

[桌面效果](artifacts/overview-desktop.png) · [手机效果](artifacts/overview-mobile.png)

```sh
npm install
npm run dev
```

默认打开**本地示例工作区**。示例指标不是线上服务状态，所有新增的记忆、任务、对话和插件选择均保存在浏览器的 localStorage 中。媒体素材独立保存在 IndexedDB 中。

## 主要操作

- 总览支持 7 / 14 / 30 天趋势、逐日数据查看、指标跳转与活动详情。
- `Ctrl+K` 或 `Cmd+K` 搜索页面、记忆和任务，方向键选择，Enter 打开，Esc 关闭。
- 记忆支持新建、编辑、分类筛选、关键词搜索、分页、删除确认和 JSONL 导出。
- 本地任务支持创建、完成和取消。服务端任务支持取消与显式审批，以 API 响应为准。
- 插件可以启停，变更写入本地活动日志；服务模式使用真实插件接口。
- 本地对话助手支持工作区概况、记忆回顾、待办查询、`记住：内容`、`创建任务：标题`。它是确定性的本地工具，不是模拟 AI 模型。
- 三个本地工作流可以生成记忆回顾、待办清单和工作区简报，并导出结果。连接服务后加载服务注册的工作流。
- 支持本地媒体导入、预览、删除，以及浏览器语音朗读。语音支持取决于操作系统和已安装的语音包。
- 提供工作区偏好、减少动效、本地诊断、导出和带确认的重置。

## 部署到 Whale Core

通用静态托管使用 `npm run build`，输出目录为 `dist/`。

**向原项目部署时请使用专用构建**，避免单文件模式的内联脚本被原服务的 Content Security Policy 拦截：

```sh
node scripts/build-for-whale.mjs
```

该命令生成 `dist-whale/`，JS 和 CSS 均为独立的同源资源。将该目录的**全部内容**复制到 Whale Core 源码仓库的 `web/dist/`，再使用原项目的 `whale api` 启动服务。不要关闭或放宽原服务的 CSP。

1. 打开服务地址，进入「设置与诊断 > 服务连接」。
2. 使用当前同源地址，并输入服务配置目录 `api_token` 中的令牌。
3. 验证通过后，控制台切换到实际服务数据，不会将本地示例上传。
4. 新控制台的令牌仅保存在当前标签页内存，通过 Authorization 请求头传递，刷新后需要重新连接。

开发前后端使用不同端口时，建议通过同源反向代理转发 `/api/v1/`。不应通过开放任意 CORS 源解决认证问题。本控制台不会内置密钥、下载模型或代替后端配置模型服务。

## 保留的原生功能

连接同源服务后，可在「设置与诊断 > 服务连接 > 高级能力」打开 `public/classic/index.html` 对应的原生控制台。它保留上游的模型配置、Studio、主动行为、逐项审批、实时语音、媒体服务与高级诊断。原生控制台单独认证，使用上游自己的令牌存储策略；新界面不向其转移令牌。

`public/classic/` 是从上游 `web/` 以 `vite build --base /classic/` 构建的静态产物，仅在打开高级控制台时加载。新界面的源代码位于 `src/`，其中 `src/api.ts` 对接上游 `/api/v1` 的 `{ result: ... }` 响应契约。

## 校验

```sh
npx tsc --noEmit
npm run build
npx playwright install chromium
npx playwright test
```

常规测试使用独立的浏览器上下文，不改动实际用户数据。真实服务联调用例需要显式设置 `WHALE_TEST_URL` 和 `WHALE_TEST_TOKEN`，并使用单独的测试配置目录。

本次已通过 TypeScript 检查、两种生产构建和 10 项端到端测试。真实服务联调覆盖令牌拒绝与验证、数据读取、插件启停与状态回读、记忆查询、原生控制台加载及断开连接。生产预览已检查 320 至 1920 像素宽度，没有检测到横向溢出或资源加载错误。

未配置外部模型和云端语音凭据，因此不将真实模型回复、云端媒体生成或设备通话列为已验证能力。

## 素材

`public/images/whale-observatory.jpg` 为本次生成的鲸鱼插画。Manrope 与 Noto Sans SC 字体许可证位于 `public/fonts/`。中文字体已按界面用字进行子集化，用户内容中其余字符由设备字体回退显示。
