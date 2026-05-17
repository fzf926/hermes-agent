# Hermes Chat History API — YApi / Postman 导入

仅包含 2 个 MySQL 聊天记录查询接口。

## 文件

| 用途 | 文件 |
|------|------|
| Postman | `docs/api_server.chat_history.postman_collection.json` |
| YApi (Swagger) | `docs/api_server.chat_history.yapi.swagger.json` |

## 接口列表

1. `GET /api/chat/users/{user_id}/sessions` — 按用户查会话（`updated_at` 倒序）
2. `GET /api/chat/sessions/{session_id}/turns` — 按会话查问答轮次（`turn_no` 升序）

## 导入

### Postman

Import → 选择 `api_server.chat_history.postman_collection.json` → 设置变量 `baseUrl`、`apiKey`、`userId`、`sessionId`。

### YApi

数据导入 → Swagger → 上传 `api_server.chat_history.yapi.swagger.json`。

- Base URL: `http://127.0.0.1:8642`
- Header: `Authorization: Bearer <API_SERVER_KEY>`
