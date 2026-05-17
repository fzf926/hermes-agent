# Hermes API Server 导入 YApi 配置文档

本文档对应 `gateway/platforms/api_server.py` 当前对外接口。

## 1. 导入文件

- Swagger 文件：`docs/api_server.yapi.swagger.json`
- 导入方式：YApi 项目内选择 **数据导入 -> Swagger**

## 2. 建议的基础配置

- Base URL：`http://127.0.0.1:8642`
- 认证 Header：`Authorization: Bearer <API_SERVER_KEY>`
- Content-Type：`application/json`

说明：

- 当服务端未配置 `API_SERVER_KEY` 时，接口可无鉴权访问（仅建议本地开发）。
- 建议在 YApi 中设置全局 Token 变量，例如 `{{apiKey}}`。

## 3. SSE 接口说明（重点）

YApi 本身主要用于接口文档与调试，对 SSE 的实时事件展示能力有限。建议将 SSE 接口文档化后，在实际联调时用 curl 或前端 EventSource 验证。

### 3.1 `POST /v1/chat/completions`

- 当请求体 `stream=true` 时，返回 `text/event-stream`
- SSE 内容包含增量文本与工具事件，最终以 `[DONE]` 结束

### 3.2 `POST /v1/responses`

- 当请求体 `stream=true` 时，返回 `text/event-stream`
- 事件通常包含创建、delta、完成等阶段事件

### 3.3 `GET /v1/runs/{run_id}/events`

- 固定为 SSE 输出（`text/event-stream`）
- 事件载荷为 `data: {json}` 形式
- 会发送 keepalive 注释帧

## 4. 已覆盖的接口分组

- health
  - `GET /health`
  - `GET /health/detailed`
  - `GET /v1/health`
- chat/responses
  - `GET /v1/models`
  - `GET /v1/capabilities`
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
  - `GET /v1/responses/{response_id}`
  - `DELETE /v1/responses/{response_id}`
- runs
  - `POST /v1/runs`
  - `GET /v1/runs/{run_id}`
  - `GET /v1/runs/{run_id}/events` (SSE)
  - `POST /v1/runs/{run_id}/approval`
  - `POST /v1/runs/{run_id}/stop`
- jobs
  - `GET /api/jobs`
  - `POST /api/jobs`
  - `GET /api/jobs/{job_id}`
  - `PATCH /api/jobs/{job_id}`
  - `DELETE /api/jobs/{job_id}`
  - `POST /api/jobs/{job_id}/pause`
  - `POST /api/jobs/{job_id}/resume`
  - `POST /api/jobs/{job_id}/run`
- chat_history (MySQL)
  - `GET /api/chat/users/{user_id}/sessions` — 按用户查会话列表（`updated_at` 倒序）
  - `GET /api/chat/sessions/{session_id}/turns` — 按会话查问答轮次（`turn_no` 升序）

## 5. 导入后检查项

- 确认 YApi 中接口总数与上述列表一致
- 确认 `runs`、`jobs`、`chat_history` 的 path 参数（`run_id`, `job_id`, `response_id`, `user_id`, `session_id`）已生成
- 确认 `chat/responses` 请求体中的 `stream` 字段可见
- 对 SSE 接口补充备注：YApi 调试台可能无法完整展示流式事件
