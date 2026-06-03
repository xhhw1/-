# PackVision Workbench Frontend

这是 React 生产版前端工程，用于在保持旧版 UI 完全一致的前提下，重构当前 `src/ai_visual_agent/web` 下的原生静态工作台。

旧版 `/app/` 是 UI 和功能对照基准。本工程构建产物挂载在 `/app-next/`，采用 React/Vite/TypeScript 架构，但复用旧版 CSS class 和样式体系。

## 技术栈

- React 19
- Vite 7
- TypeScript strict mode
- TanStack Query
- Phosphor Icons
- 原生 CSS tokens

## 本地开发

当前环境需要先安装 Node 包管理器。可用后执行：

```bash
cd frontend
npm install
npm run dev
```

开发服务会代理：

- `/api` 到 `http://127.0.0.1:8000`
- `/health` 到 `http://127.0.0.1:8000`

## 构建

```bash
cd frontend
npm run build
```

构建产物输出到 `frontend/dist`。后端启动时如果检测到该目录，会自动挂载：

```text
/app-next/
```

旧版对照入口是：

```text
/app/
```

## 迁移策略

1. 保持旧版 `/app/` 可用。
2. 在 `/app-next/` 使用 React 架构复刻旧版 UI。
3. 逐步把旧版的素材上传、@ 引用、审核卡片、图片预览、知识库模块迁移成组件。
4. 每次迁移都以 `/app/` 的视觉和交互为验收基准。

## 生产约束

- 所有 API 调用必须通过 `src/api/client.ts`，统一超时、重试和错误格式。
- 所有后端响应类型必须在 `src/api/types.ts` 声明。
- 复杂 UI 必须拆为组件，不再把全部逻辑写进一个文件，但 class 命名和布局需优先对齐旧版。
- 关键操作需要 loading、empty、error、disabled 状态。
- 不在组件中直接拼接未验证的 HTML。
