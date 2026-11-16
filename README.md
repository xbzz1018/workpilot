# WorkPilot

把零散工作记录整理成能核对来源的周报与述职草稿。导入 Markdown、TXT 或 CSV，WorkPilot 提取成果、校验事实，再生成带引用的报告；审阅通过后导出 Markdown 和 JSON。

[生成第一份报告](docs/usage.md) · [API 定义](openapi/openapi.json) · [评测样本](evals-v2/README.md)

## 主要功能

| 功能 | 做什么 |
| --- | --- |
| 工作成果整理 | 从日志、任务清单和进度记录中提取成果，生成周报或述职草稿 |
| 事实核对 | 校验数字、日期、单位和引用，标记记录中的冲突与风险 |
| 草稿审阅 | 查看原始依据，批准、修改后再次校验，或拒绝本次报告 |
| 任务恢复 | 保存阶段产物与调用记录，恢复未完成任务，支持失败后重试 |

通过 FastAPI 的交互文档上传材料、查看草稿和下载报告，也可调用 API 接入自己的工具。

## 从记录到报告

```mermaid
flowchart LR
    A["工作记录"] --> B["提取事实与核对来源"]
    B --> C["生成带引用的草稿"]
    C --> D["人工审阅与导出"]
```

成果提取、语义核验和报告写作使用独立的模型角色。数字、日期、单位与引用由本地规则检查，写作阶段消费核验后的事实。API 的阶段顺序由 Python Pipeline 固定，草稿通过检查后停在 `waiting_approval`，等待审阅。

材料、阶段产物、模型用量和 Checkpoint 按任务保存，已完成的阶段结果可在恢复时复用。

## 安装与启动

需要 Python 3.12；以下使用 Conda。从仓库根目录执行：

```powershell
git clone https://github.com/xbzz1018/workpilot.git
cd workpilot
conda env create -f environment.yml
conda activate workpilot
python -m pip install -r requirements.lock
python -m pip install -e ".[dev]" -c requirements.lock
python -m pip check
Copy-Item .env.example .env
```

编辑 `.env`，配置模型服务：

| 参数 | 用途 |
| --- | --- |
| `WORKPILOT_BASE_URL` | OpenAI 兼容服务地址，模板默认使用 VibeAPI |
| `WORKPILOT_MAIN_MODEL` / `WORKPILOT_MAIN_API_KEY` | 报告写作，默认 DeepSeek V4 Pro |
| `WORKPILOT_EXTRACTOR_MODEL` / `WORKPILOT_EXTRACTOR_API_KEY` | 成果提取，默认 DeepSeek V4 Flash |
| `WORKPILOT_VERIFIER_MODEL` / `WORKPILOT_VERIFIER_API_KEY` | 语义核验，默认 GLM-5.3 |
| `WORKPILOT_CHALLENGER_MODEL` / `WORKPILOT_CHALLENGER_API_KEY` | 开发评测，默认 Kimi K3；服务配置会读取此 Key |
| `WORKPILOT_WORKSPACE` | 材料、草稿和报告的保存目录，默认 `workspace` |

启动服务时显式加载配置文件：

```powershell
python -m uvicorn workpilot.api:app --env-file .env --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/docs>。先用[仓库自带的合成记录](evals-v2/dev/wp-001-regular/input/)创建一份 `work_summary` 任务，周期填写 `2026-W01`；查看草稿、提交审阅并下载报告的步骤见[使用指南](docs/usage.md)。提交任务会调用已配置的模型。

## 常用入口

| 内容 | 入口 |
| --- | --- |
| 上传、任务状态、审阅与下载 | [使用指南](docs/usage.md) |
| 阶段编排与报告生成 | [Pipeline](src/workpilot/pipeline.py)、[Renderer](src/workpilot/renderer.py) |
| 材料定位与事实校验 | [Ingestion](src/workpilot/ingestion.py)、[Validation](src/workpilot/validation.py) |
| 后台任务与恢复 | [ApplicationService](src/workpilot/service.py) |
| Docker 启动 | [运行说明](docs/usage.md#docker-运行) |

## 评测

[v2 数据集](evals-v2/README.md)包含 10 个 Dev 和 20 个 Test 合成案例，检查事实覆盖、引用有效性、冲突识别和报告格式。离线测试与模型评测分别运行：

```powershell
python -m pytest
workpilot eval --split dev --system dev_matrix --run-id dev-first --max-model-calls 400
```

模型评测调用配置的 API，结果写入 `results/`；增加 `--resume` 可继续同一运行中未完成的案例。

技术栈：Python、FastAPI、Deep Agents、LangGraph、Pydantic、SQLite。

## 许可证

[MIT](LICENSE)
