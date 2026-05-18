# Hermes SQL 收藏 API — YApi 导入说明

仅包含 SQL 收藏相关的 3 个接口，与聊天记录查询文档分离，便于单独导入 YApi 项目。

## 文件

| 用途 | 文件 |
|------|------|
| YApi (Swagger 2.0) | `docs/api_server.sql_favorites.yapi.swagger.json` |

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/favorites` | 按 `hermes_response_id` 收藏（仅 `fulfillment_status=satisfied`） |
| GET | `/api/chat/users/{user_id}/favorites` | 用户收藏列表（分页） |
| GET | `/api/chat/favorites/{favorite_id}/sql` | 某条收藏下的 SQL 详情 |

## 前置条件

- Gateway 已开启 MySQL 聊天持久化（`HERMES_MYSQL_ENABLED=1` 或 `config.yaml` → `mysql_chat.enabled`）
- 已执行收藏表迁移：`gateway/sql/chat_schema_migrate_sql_favorite.sql`（若曾用旧列名，再执行 `chat_schema_migrate_sql_favorite_summary.sql`）

## YApi 导入步骤

1. 打开 YApi → **数据管理** → **数据导入**
2. 选择 **Swagger**
3. 上传 `api_server.sql_favorites.yapi.swagger.json`
4. 导入后检查：
   - **Base URL**：`http://127.0.0.1:8642`（按环境修改 `host`）
   - **全局 Header**：`Authorization: Bearer <API_SERVER_KEY>`
5. 收藏接口 POST 建议再配：`X-Hermes-User-Id: <业务用户ID>`（与 body.user_id 二选一）

## 基于收藏继续对话（conversation_type=2）

在 `POST /v1/chat/completions` 或 `POST /v1/responses` 的 **JSON body** 中携带：

| 字段 | 说明 |
|------|------|
| `conversation_type` | `1` 历史（默认）、`2` 收藏、`3` 直查 SQL |
| `favorite_id` | `conversation_type=2` 时必填，`favorite_uid` 或数字 id |
| `sql` | `conversation_type=3` 时填写要执行的 SQL（也可在消息里粘贴 SQL） |
| `user_id` | 可选，与 Header `X-Hermes-User-Id` 二选一，用于收藏归属校验 |

- **类型 1（历史）**：默认，与现有 `X-Hermes-Session-Id` 续聊行为一致。
- **类型 2（收藏）**：按 `favorite_id` 查询关联 SQL 与摘要，注入本次对话的系统上下文。
- **类型 3（直查）**：须在 body 的 `sql` 字段或用户消息中提供 SQL；未提供时**不调用 Agent**，直接返回引导话术请用户补充 SQL。

```http
POST /v1/chat/completions
Authorization: Bearer <API_SERVER_KEY>
X-Hermes-User-Id: user-001
Content-Type: application/json

{
  "model": "hermes-agent",
  "conversation_type": 2,
  "favorite_id": "a1b2c3d4e5f6...",
  "messages": [{"role": "user", "content": "在刚才 SQL 基础上按部门再拆一版"}]
}
```

### 直查 SQL（conversation_type=3）

```http
POST /v1/chat/completions
Authorization: Bearer <API_SERVER_KEY>
Content-Type: application/json

{
  "model": "hermes-agent",
  "conversation_type": 3,
  "sql": "SELECT id, name FROM users LIMIT 10",
  "messages": [{"role": "user", "content": "帮我执行并解读结果"}]
}
```

未带 `sql` 且消息也不像 SQL 时，响应 `hermes.direct_sql_required: true`，内容为引导用户补充 SQL。

## 调用示例

### 1. 创建收藏

```http
POST /api/chat/favorites
Authorization: Bearer <API_SERVER_KEY>
X-Hermes-User-Id: 10001
Content-Type: application/json

{
  "hermes_response_id": "resp_xxx"
}
```

成功：`201`（新建）或 `200`（已存在）。响应字段 `favorite.question_summary` / `answer_summary` 为 AI 摘要。

### 2. 收藏列表

```http
GET /api/chat/users/10001/favorites?limit=20&page=1
Authorization: Bearer <API_SERVER_KEY>
```

### 3. 收藏 SQL 详情

```http
GET /api/chat/favorites/{favorite_uid}/sql
Authorization: Bearer <API_SERVER_KEY>
X-Hermes-User-Id: 10001
```

`favorite_id` 可为数字主键 `id` 或 `favorite_uid`。

## 常见错误

| HTTP | 含义 |
|------|------|
| 400 | 轮次非 satisfied、该轮无 SQL、缺少 `hermes_response_id` |
| 403 | `hermes_response_id` 或收藏不属于当前用户 |
| 404 | 找不到消息/轮次/收藏 |
| 503 | MySQL 未配置或未启用 |
