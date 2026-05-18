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
