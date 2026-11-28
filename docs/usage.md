# 生成第一份工作报告

[返回首页](../README.md)

按首页完成安装、填写 `.env` 并启动服务，然后打开 <http://127.0.0.1:8000/docs>。下面的接口都能在交互文档中执行。

## 1. 上传一组记录

找到 `POST /api/v1/tasks`，点击 **Try it out**，填写：

| 字段 | 示例 |
| --- | --- |
| `report_type` | `work_summary`（周报），或 `performance_review`（述职） |
| `period` | `2026-W01` |
| `files` | [合成案例](../evals-v2/dev/wp-001-regular/input/)中的 `weekly.md`、`status.txt`、`tasks.csv` |

执行后返回 `202` 和任务记录，保存其中的 `task_id`。记录中的发布成果与耗时变化供流程体验使用。

上传支持 Markdown、TXT 和 CSV，单文件不超过 1 MiB，一次总计不超过 10 MiB。同批文件名不能重复，CSV 每条记录的列数需要与表头一致。

忘记 `task_id` 时，可以在交互文档中运行 `GET /api/v1/tasks`，或从本机终端查询最近的任务：

```powershell
curl.exe --fail --silent --show-error "http://127.0.0.1:8000/api/v1/tasks?limit=10"
```

列表按创建时间倒序排列。`limit` 默认为 `100`，服务端将取值限制在 `1` 到 `500`。从对应任务记录中复制 `task_id`，继续查看状态或下载报告。

## 2. 查看处理过程与草稿

通过 `GET /api/v1/tasks/{task_id}` 查看状态。任务经过导入、提取、校验、语义核验和写作，到达 `waiting_approval` 后开始审阅。

| 接口后缀 | 用途 |
| --- | --- |
| `/materials` | 导入的材料与 Evidence ID |
| `/facts` | 核验后的事实、冲突与风险 |
| `/draft` | 报告草稿及引用 |
| `/usage` | 模型调用用量 |
| `/history` | 持久化的阶段事件 |
| `/artifacts/draft.md` | 下载草稿 Markdown |

上述后缀均接在 `/api/v1/tasks/{task_id}` 后面。材料、事实或草稿尚未就绪时，相应接口返回 `409`；稍后再查看任务状态。

程序接入可通过 `/events` 订阅 SSE 阶段事件，重连时传入上次收到的非负 `Last-Event-ID`。

## 3. 审阅并导出

核对草稿及来源后，调用 `POST /api/v1/tasks/{task_id}/decisions`。批准示例：

```json
{"decision": "approve"}
```

需要修改时提交 `{"decision": "edit", "edited_markdown": "修改后的完整 Markdown"}`；修改稿仍需通过引用与格式检查。使用 `{"decision": "reject"}` 拒绝本次报告。

批准或编辑通过后，任务变为 `completed`，可下载：

- `GET /api/v1/tasks/{task_id}/artifacts/report.md`
- `GET /api/v1/tasks/{task_id}/artifacts/report.json`

默认工作目录为 `workspace/`，每个任务保存材料、阶段产物、草稿和最终报告。

## 取消与重试

`POST /api/v1/tasks/{task_id}/cancel` 请求取消任务；`POST /api/v1/tasks/{task_id}/retry` 重试已失败或已取消的任务。任务执行状态、错误和恢复阶段可继续通过任务接口查看。

## Docker 运行

[Compose](../compose.yaml)包含应用与 Caddy，使用数据卷保存工作区。先在 `.env` 设置 `WORKPILOT_DOMAIN=http://localhost`、`WORKPILOT_ADMIN_USER` 与 `WORKPILOT_ADMIN_HASH`。

密码哈希可通过交互命令生成，再将哈希值用单引号包裹后填入 `.env`：

```powershell
docker run --rm -it caddy:2.10-alpine caddy hash-password
docker compose up --build -d
```

打开 <http://localhost/docs>，使用设置的用户名及生成哈希时输入的密码登录。
