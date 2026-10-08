# WorkPilot

WorkPilot 是一个私有部署、证据约束的个人工作成果整理 Agent。它读取 Markdown、TXT 和 CSV 工作记录，提取原子事实，校验数字/日期/单位与来源，使用独立模型做语义核验，再生成带 Fact/Evidence 引用的周报或述职草稿；用户批准后才导出最终 Markdown 和 JSON。

这是从 Deep Agents 学习代码中拆出的独立业务项目。它不包含上层课程示例，也不要求上传父目录中的课程代码；本仓库只保留 WorkPilot 的 API、CLI、评测、合成工作包和测试。

## 核心流程

```text
材料导入与 Evidence ID
  -> DeepSeek V4 Flash 成果提取
  -> 本地确定性 Fact 校验
  -> GLM 独立语义核验
  -> DeepSeek V4 Pro 生成 ReportClaim
  -> 本地 Claim-Fact Gate 与 Markdown Renderer
  -> approve / edit / reject
  -> 最终报告、证据、风险、用量和 Checkpoint
```

阶段顺序由 Python Pipeline 强制，不依赖主 Agent 自觉调用工具。Prompt、Skill 与运行模型均保存版本/哈希；中断后优先读取已持久化阶段产物，避免重复调用已经完成的 Agent。

## 模型与密钥

所有模型通过 VibeAPI 的 OpenAI 兼容接口访问：

```text
https://www.vibeapi.cn/v1/chat/completions
```

四个 Key 按角色隔离：Main/V4 Pro、Extractor/V4 Flash、Verifier/GLM、Challenger/Kimi。真实值只能放在未提交的 `.env` 或部署 Secret 中；Key 如果曾进入公开日志、提交或截图，应立即轮换。

```powershell
Copy-Item .env.example .env
# 填入 Key，并用 /v1/models 确认准确模型 ID
python scripts/smoke_models.py --role all --update-env --output results/model-smoke.json
```

Smoke 工具验证模型可见性、普通回复、Tool Calling、Pydantic 结构化输出、流式和 usage 字段，输出中只包含 Key 别名。

## Conda 开发环境

```powershell
conda env create -f environment.yml
conda activate workpilot
python -m pytest
python scripts/export_openapi.py
workpilot-api
```

Windows 上若 `conda run` 因 GBK 输出失败，激活环境后直接运行命令，或使用环境内 `python.exe`。

## 后端 API

FastAPI OpenAPI 3.1 快照位于 `openapi/openapi.json`。主要接口：

- `POST /api/v1/tasks`：上传材料并返回 `202`。
- `GET /api/v1/tasks/{id}` 与 `/events`：状态和 SSE 阶段事件。
- `GET /materials`、`/facts`、`/draft`：证据审阅。
- `POST /decisions`：approve/edit/reject。
- `POST /cancel`、`/retry`：取消与阶段恢复。
- `GET /artifacts/{name}`、`/usage`、`/history`：产物和审计。
- `POST /api/v1/evaluations`：后台评测。

单机最多 3 个活跃任务，SQLite WAL 保存任务和事件；LangGraph SQLite 保存 Agent Checkpoint。终态任务默认保留 30 天并每日清理。CLI 与 API 复用同一领域、Pipeline 和工作区实现。

## 评测

- `evals/`：保留原始 v1 数据集。
- `evals-v2/`：原子 Fact/ReportClaim 与异构模型评测快照，Dev 10、冻结 Test 20。
- 评测组：单 Agent Baseline、同模型 WorkPilot、异构 WorkPilot；Kimi challenger 仅允许 Dev。

质量指标包括事实覆盖、无依据陈述、引用有效、冲突识别和格式通过；工程指标包括分角色调用、Token、缓存、重试、P50/P95 和可空费用。缺少 usage 或中转价格时费用必须为 `null`。

评测运行器使用稳定 `run_id`、逐案例原子写入、断点续跑和全局模型调用预算。Dev 配置冻结后，Test 会校验源码、Prompt、Skill、Profile、依赖和数据集哈希：

```powershell
workpilot eval --split dev --system dev_matrix --run-id dev-v2-initial --max-model-calls 400
workpilot eval --split test --system test_matrix --run-id test-v2-frozen --max-model-calls 400
# 仅恢复同一配置下未完成的案例
workpilot eval --split test --system test_matrix --run-id test-v2-frozen --max-model-calls 400 --resume
```

## 本地私有运行

```powershell
# .env 中将 WORKPILOT_DOMAIN 设为 http://localhost，并配置 Basic Auth 哈希
docker compose up --build -d
```

Compose 只包含 WorkPilot App 与 Caddy。App 端口不直接暴露，Caddy 负责单管理员 Basic Auth；SQLite、上传材料、产物和 Checkpoint 存在 Docker volume 中。当前本机入口为 `http://localhost`，用户名和随机密码只保存在 Git 忽略的 `.env`。Caddyfile 仍可在后续配置真实域名时启用自动 HTTPS。

## 当前验证状态

- Conda Python 3.12 环境已建立。
- 四角色真实 smoke 全部通过：普通回复、Tool Calling、Pydantic 结构化输出、流式、usage 和错误格式均可用。
- 完整 Dev 四组真实结果位于 `results/dev-v2-initial/`；初始完成率为 Baseline 0%、同模型 WorkPilot 20%、异构 WorkPilot 70%、Challenger 0%。
- 三个异构 Dev 失败案例只在 Dev 上完成定向修复与复测，最终配置记录在 `results/frozen-config.json`。
- 冻结 Test 在中转站 502 恢复过程中由用户为控制 Token 成本主动停止，当前为 47/60 个系统结果；`results/test-v2-frozen/` 明确标记 `user_stopped`，不能作为最终 Test 或简历数字。
- 55 项离线测试通过，覆盖率 88.76%。
- OpenAPI 3.1 快照已生成。
- v1/v2 Test manifest 均锁定 100 个文件。
- 本地 Compose 已验证未认证 401、认证后 health/OpenAPI 200，以及 App 重启后的 SQLite 卷持久化。

## 公开仓库边界

- `evals/` 和 `evals-v2/` 中的数据是合成评测输入，不代表真实雇主或客户材料。
- `results/`、`.env`、`.venv`、SQLite 数据库、上传工作区、模型 smoke 输出和覆盖率文件属于本地运行产物，不提交到 Git。
- README 中的完成率和测试数字只描述已有运行记录；冻结 Test 被主动停止的结果不能包装成最终模型质量。
- 该项目不提供公开注册、RAG、向量数据库、MCP、Temporal、Kafka 或多模态能力。

## 范围

首版不加入 RAG、向量数据库、MCP、GraphRAG、多模态、Temporal、Kafka、公开注册、前端、图片生成、图片理解或视觉校验。CLI 与 FastAPI/OpenAPI 是唯一交互入口。
