# CampusVoice 声程

## v0.3 校园通知变化雷达

声程现在可以把同一 `NoticeSeries` 中显式确认的 v1/v2 版本编译为带原文证据的结构化变化，按确定性规范化比较 claim，判断专业/年级/课程适用性，并只找出仍精确依赖旧 claim（或业务字段仍等于旧 claim 值）的待办和日程。用户手工改过的值不会因为仍指向旧文档而被覆盖；supporting claims 进入来源历史但不会替换 primary source claim。v1 适用而 v2 不再适用时会产生 `keep`、`cancel` 或 `manual_review` 建议，不会静默显示零影响。

迁移预览按 change set 生成递增 generation，冻结整组 before/after、实体版本、来源与稳定排序的日历冲突。审核被拒绝会使 ready plan 失效并 dismiss 影响；重新通过审核后只能创建新 generation。普通更新一次确认；冲突覆盖和整组撤销使用两次分离交互、请求体绑定、一次性 write challenge，第一次只准备最终 challenge，第二次才写入。执行在数据库事务和状态锁内重新校验审核、适用性、实体版本、旧 claim 依赖与当前冲突；任一变化都使整组零写入。执行与撤销分别保存不可互相覆盖的回执，提交后使用全新数据库会话逐项复查；断线恢复先读取最新 plan，只有 `applied`/`undo_applied` 等已提交状态才用相同幂等键补做核验，`verified`/`undone` 只读回执，`ready` 必须重新确认。

首页 `Campus Radar` 展示 new notice、version change、upcoming deadline 和 needs review 四类卡片；详情页依次展示 v1/v2 diff 与证据、Impact Canvas、真实日历时间线、确认面板、数据库核验详情和整组撤销。验证失败会显示实际数据库快照并允许继续核验，瞬时失败使用同一 plan 和 sessionStorage 中的稳定幂等键重试。通知页还可创建或选择 series、查看时间线、显式导入 v1/v2、选择 predecessor，并在后继关系有歧义时要求确认。合成演示运行 `python scripts/seed_demo.py`，会建立“2026 人工智能专业考试安排”v1（09:00–11:00 / A302）和 v2（14:00–16:00 / B205）、一个考试日程、两个复习待办、一条“携带校园卡”任务提醒及来源链。任务提醒绑定 v1 的材料证据，截止与提醒时间会随考试时间迁移；重复运行不会创建重复版本或安排。

设计与安全语义见 [`ADR 0007`](docs/decisions/0007-notice-evidence-impact-migrations.md)，API 与表结构见 [`services/api/docs/api-contract.md`](services/api/docs/api-contract.md) 和 [`services/api/docs/data-model.md`](services/api/docs/data-model.md)。

面向大学生的可验证校园语音学习助手。CampusVoice 把浏览器语音转写、校园术语纠错、结构化意图、风险确认、数据库事务和执行后验证连接成一条可审计链路；模型输出本身永远不被当作写入成功的证据。

## 已实现能力

- 浏览器 AudioWorklet 采集 16 kHz 单声道 PCM，通过 `/ws/asr` 实时传输。
- FunASR 流式中文识别、FSMN-VAD、CT-Punc、热词和 Whisper 离线基线。
- 创建/修改/删除任务与日历、冲突与重复检测、乐观版本检查和撤销。
- 严格 Pydantic 意图 Schema、一次结构化修复、缺失字段追问和低置信度保护。
- development/test 显式 demo 身份、Bearer JWT 适配器，以及服务端校园 OIDC Authorization Code + PKCE 会话边界。
- 确定性风险分级：服务端签发并绑定用户、操作、请求内容、阶段和过期时间的一次性挑战；高风险删除需要两次分离交互。
- ASR 使用短时、一次性 WebSocket ticket，不在 URL、日志或数据库中保存长期访问令牌，并限制帧、空闲、会话、音频与并发配额。
- PDF、DOCX、TXT、Markdown 通知库、中文向量检索、证据约束 LLM 问答、真实页码和证据不足拒答。
- 同名通知的版本/适用群体冲突检测、筛选与消歧前禁止转为待办或日历。
- 课程、教师、AI 术语及自定义热词的候选纠错和确认记录。
- 从原始 JSONL 生成 CER、关键词、术语、槽位、延迟、RTF 与可靠执行指标。
- 分离存活/就绪检查、低基数运行指标、安全结构化日志，以及当前用户的数据导出、保留清理和可验证删除。

系统架构见 [`docs/architecture/system.md`](docs/architecture/system.md)，ER 与字段见 [`services/api/docs/data-model.md`](services/api/docs/data-model.md)，接口契约见 [`services/api/docs/api-contract.md`](services/api/docs/api-contract.md)。

## 环境要求

- Node.js 20.9 以上；本项目验证版本为 Node.js 24。
- pnpm 11.7。
- Python 3.11。不要使用当前仍可能缺少 AI wheel 的 Python 3.13/3.14。
- FFmpeg。
- 可选：支持 CUDA 的 NVIDIA GPU。本机验证组合为 PyTorch/Torchaudio 2.11.0 + CUDA 13.0。
- Docker 部署需要 Docker Desktop/Engine 和 Compose；Windows 家庭版使用 WSL 2 Linux 容器。

所有示例资料均为合成数据。项目不连接真实教务系统，也不应存储真实学生隐私数据。

## 原生本地启动

### 1. Python 环境

```powershell
conda create -n campusvoice python=3.11 pip -y
conda activate campusvoice
Push-Location services/api
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --no-deps -e .
Pop-Location
```

`runtime.lock` 与 `dev.lock` 均由 `pip-compile` 生成并包含哈希；重生成、审计及 AI/CUDA 专用安装方法见 [`services/api/requirements/README.md`](services/api/requirements/README.md)。AI 依赖按硬件单独安装，不混入跨平台核心锁文件。

如需 NVIDIA GPU，请用 PyTorch 官方 CUDA 索引替换默认 CPU wheel，并保持 `torch` 与 `torchaudio` 完全同版。例如本机已验证：

```powershell
python -m pip install --force-reinstall --no-deps `
  torch==2.11.0+cu130 torchaudio==2.11.0+cu130 `
  --index-url https://download.pytorch.org/whl/cu130
```

### 2. 前端依赖与环境变量

```powershell
pnpm install --frozen-lockfile
Copy-Item .env.example .env
```

本地默认仅在 `development` 下显式启用 demo auth。校园试点使用 `CAMPUSVOICE_ENV=production` 与 `CAMPUSVOICE_AUTH_MODE=oidc`，并配置 HTTPS issuer、client ID、API 回调地址、登录/登出跳转地址、`openid` scope、非对称 ID-token 算法，以及至少 32 字符的 `CAMPUSVOICE_CONFIRMATION_SECRET`；缺项会启动失败且绝不回退到 `user_demo`。PKCE verifier、state、nonce、可选 client secret 和 code exchange 全在 API 端；浏览器只持有 `HttpOnly` 随机会话 cookie，不接收或持久化 access token。非浏览器客户端仍可使用 `jwt` 模式。详见 [`docs/decisions/0004-oidc-authorization-code-pkce.md`](docs/decisions/0004-oidc-authorization-code-pkce.md)。

生产 ASR 不使用浏览器 `SpeechRecognition`。在 `.env` 中启用真实 FunASR：

```dotenv
CAMPUSVOICE_ASR_PROVIDER=funasr
CAMPUSVOICE_ASR_DEVICE=cuda:0
CAMPUSVOICE_ASR_MODEL=paraformer-zh-streaming
CAMPUSVOICE_ASR_VAD_MODEL=fsmn-vad
CAMPUSVOICE_ASR_PUNC_MODEL=ct-punc
```

没有 GPU 时把设备改为 `cpu`。Whisper 基线需设置 `CAMPUSVOICE_ASR_PROVIDER=whisper`、`CAMPUSVOICE_ASR_MODEL=small`，不能沿用 Paraformer 模型名。首次使用会下载模型，请预留数 GB 空间。

`.env.example` 为无模型下载的安全启动默认使用 `lexical` 检索。安装 AI 依赖后，可设置 `CAMPUSVOICE_KNOWLEDGE_RETRIEVER=embedding`，并按需把 `CAMPUSVOICE_EMBEDDING_DEVICE` 设为 `cpu` 或 `cuda:0`；首次检索会下载配置的中文 Embedding 模型。

意图抽取与通知问答可接入任意 OpenAI-compatible `chat/completions` 服务：

```dotenv
CAMPUSVOICE_LLM_BASE_URL=http://localhost:11434/v1
CAMPUSVOICE_LLM_API_KEY=
CAMPUSVOICE_LLM_MODEL=your-structured-output-model
```

未配置 LLM 时，九类意图仍有确定性安全基线，通知问答返回检索原文而不伪造结论。配置 LLM 后，问答只向模型提供编号证据，要求每行结论带引用并严格校验 JSON；模型不可用、引用越界或缺少引用时自动退回原文摘录。通知页可指定版本与适用群体，存在冲突时必须先消歧才能转为待办或日历。

### 3. 数据库与服务

终端一：

```powershell
conda activate campusvoice
Set-Location services/api
alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

终端二：

```powershell
pnpm dev:web
```

打开 <http://localhost:3000>。API 文档位于 <http://localhost:8000/docs>。`/health/live` 只表示进程存活；`/health/ready` 还检查数据库、Alembic head 与已启用组件配置。兼容入口 `/api/health` 保留，运行指标位于 `/api/metrics`。

浏览器麦克风只允许在 `localhost` 或 HTTPS 安全上下文使用。拒绝权限、WebSocket 断线和模型未配置都会显示真实错误，不会回退为伪造转写。

### 4. 合成演示数据

API 启动后运行：

```powershell
python scripts/seed_demo.py
```

在 `development/test + demo auth` 下，应用会创建固定演示身份 `user_demo`；它只用于本地合成数据，不是生产账户。JWT/OIDC 模式下用户 ID 由服务端根据受验证的 issuer 与 subject 稳定映射，客户端不能通过请求头、路径或正文选择用户。种子脚本通过 REST 接口写入合成课程、热词、待办、考试日程和两份校园通知，可安全重复运行。

## Docker Compose

普通核心服务：

```powershell
docker compose up --build
```

默认 Compose 镜像不安装 AI 额外依赖，并明确使用 `ASR=disabled` 与 `knowledge=lexical`；它适合先验证任务、日历、确认、事务和引用链路，不会伪造语音转写结果。若手动把检索器切到 `embedding`，必须同时构建 AI 镜像。

多 worker ASR 必须启用 Redis 共享租约，不能让每个 worker 各自计算配额：

```powershell
$env:CAMPUSVOICE_ASR_WORKER_COUNT="2"
$env:CAMPUSVOICE_CONFIRMATION_SECRET="replace-with-one-shared-random-secret-at-least-32-characters"
docker compose -f docker-compose.yml -f docker-compose.multi-worker.yml `
  --profile multi-worker up --build
```

覆盖文件会让 API 等待 Redis 健康后再启动，并把 worker count 传给 Uvicorn。所有 worker 还必须显式共享同一个 confirmation secret；缺失或短于 32 字符时配置会 fail closed，绝不能依赖各进程随机生成的开发密钥。Redis 不可用时多 worker 配置与 readiness 同样会 fail closed；`local` 后端只适用于明确的单 worker 降级模式。租约在同一个 Lua 脚本中读取 Redis 时间并原子计数，worker 崩溃后按最大会话时长加宽限期自动释放。GPU ASR 每个 worker 都会加载模型，部署前必须核算显存；不足时应拆分推理服务而不是盲目增加 worker。详见 [`docs/decisions/0005-shared-asr-quota.md`](docs/decisions/0005-shared-asr-quota.md)。

包含 CUDA AI 依赖的镜像体积较大，可显式启用，并通过 GPU 覆盖文件向 Docker 申请 NVIDIA GPU：

```powershell
$env:CAMPUSVOICE_DOCKER_INSTALL_AI="true"
$env:CAMPUSVOICE_ASR_PROVIDER="funasr"
$env:CAMPUSVOICE_ASR_DEVICE="cuda:0"
$env:CAMPUSVOICE_KNOWLEDGE_RETRIEVER="embedding"
$env:CAMPUSVOICE_EMBEDDING_DEVICE="cuda:0"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

GPU 容器还需要主机正确配置 WSL 2、Docker GPU 支持和 NVIDIA 驱动。仅需 CPU AI 时不要加载 GPU 覆盖文件，并把 `CAMPUSVOICE_PYTORCH_VARIANT=cpu`、`CAMPUSVOICE_PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cpu`、ASR/Embedding 设备均设为 `cpu`。模型与 SQLite 数据分别保存在 Compose 命名卷中。

## 测试与质量门禁

```powershell
# 后端
Set-Location services/api
python -m pytest --cov=app --cov-fail-under=80 --cov-report=json:coverage.json
python scripts/check_critical_coverage.py coverage.json
python -m ruff check app tests
python -m ruff format --check app tests
python -m mypy app
python -m pip_audit --require-hashes --disable-pip -r requirements/runtime.lock
python -m pip_audit --require-hashes --disable-pip -r requirements/dev.lock

# 回到仓库根目录后运行前端
pnpm test
pnpm test:coverage
pnpm typecheck
pnpm lint
pnpm build
pnpm format:check
pnpm audit --audit-level=high

# 首次安装 Playwright Chromium，随后运行浏览器合约场景
pnpm --filter @campusvoice/web exec playwright install chromium
pnpm test:e2e

# Windows 已安装 Microsoft Edge 时也可直接验证 Edge 通道
pnpm --filter @campusvoice/web test:e2e:edge
```

后端 coverage 总门槛为 80%；关键 `actions/service.py` 门槛为 75%，`verification/service.py` 门槛为 90%。前端 coverage 门槛由 `apps/web/vitest.config.ts` 强制执行。所有阈值都基于本次生成的报告，不能沿用旧报告。

默认 `pnpm test:e2e` 是前端浏览器合约测试：它使用真实 Next 页面，但会拦截 REST，并在页面上下文中模拟 AudioWorklet、WebSocket 和合成 PCM `ArrayBuffer`，因此不等同于完整系统 E2E。Compose smoke 另行启动真实 Web、API 与 SQLite，且不拦截 `/api/**`；两套测试都只用合成数据，不请求或录制真实麦克风。

安装并启动 Docker Engine/Desktop 后，可从 Git Bash、WSL 或 CI shell 执行真实全栈场景；脚本无论成功失败都会停止服务并删除 smoke volume：

```bash
bash scripts/run-compose-smoke.sh
```

Windows PowerShell 可直接运行等价脚本：

```powershell
pnpm test:e2e:smoke:windows
```

两个 smoke 脚本默认叠加 `docker-compose.multi-worker.yml` 并启用 `multi-worker` profile，
因此本地和 CI 都使用 Redis、两个真实 Uvicorn worker、真实 API、SQLite 和页面，而不只
验证两个内存对象。可用 `CAMPUSVOICE_EXTRA_COMPOSE_FILE` 和
`CAMPUSVOICE_SMOKE_PROFILE` 显式覆盖这两个默认值。

隐私保留可由 cron、Task Scheduler 或 Kubernetes CronJob 调用一次性执行器：

```powershell
Set-Location services/api
python -m app.jobs.retention
```

执行器按配置进行有界指数退避；生产建议只运行一个外部调度实例。SQLite WAL checkpoint、备份一致性、恢复演练、失败升级和物理清理步骤见 [`docs/runbooks/privacy-retention.md`](docs/runbooks/privacy-retention.md)。

迁移验证：

```powershell
Set-Location services/api
$previousDatabaseUrl=$env:CAMPUSVOICE_DATABASE_URL
$env:CAMPUSVOICE_DATABASE_URL="sqlite+aiosqlite:///./migration-check.db"
alembic upgrade head
alembic check
alembic downgrade base
alembic upgrade head
if ($null -eq $previousDatabaseUrl) { Remove-Item Env:CAMPUSVOICE_DATABASE_URL } else { $env:CAMPUSVOICE_DATABASE_URL=$previousDatabaseUrl }
Remove-Item .\migration-check.db, .\migration-check.db-shm, .\migration-check.db-wal -ErrorAction SilentlyContinue
```

## AI 评测

格式说明位于 [`data/evaluation/manifests/README.md`](data/evaluation/manifests/README.md)。指标必须由原始记录生成：

```powershell
python scripts/evaluate_asr.py data/evaluation/manifests/asr.jsonl --output data/evaluation/results/asr.json
python scripts/evaluate_intent.py data/evaluation/manifests/intent.jsonl --output data/evaluation/results/intent.json
python scripts/evaluate_reliability.py data/evaluation/manifests/reliability.jsonl --output data/evaluation/results/reliability.json
```

仓库还提供 `data/evaluation/manifests/examples` 下的合成小样例，用于验证命令与指标管线；它们不是正式实验结果，也不能替代目标为 150 至 200 条授权语音的评测集。

一条命令可重复生成默认 160 条 WAV、源清单、四路 ASR 待推理模板和数据卡：

```powershell
python scripts/generate_synthetic_evaluation.py
```

默认 `auto` 在 Windows 尝试使用本机已安装的中文 SAPI 语音；没有可用中文语音时降级为确定性 PCM 载波。载波只能验证文件、清单和推理管线，不能用于宣称 ASR 质量。可用 `--engine sapi` 强制要求可理解语音，或用 `--engine tone` 明确生成跨平台管线样本；重复覆盖时添加 `--force`。输出位于 `data/evaluation/generated/synthetic-160` 并默认不提交。每次生成的数据卡都会记录引擎、种子、类别分布和授权边界。

`data/evaluation/audio` 应只存放合成、公开许可或明确授权的音频。生成的结果目录默认不提交，防止手工聚合结果冒充实验输出。

## 安全与范围

- API 密钥只从环境变量读取；`.env` 已被 Git 忽略。
- production 禁止 demo。OIDC 会校验 state、PKCE、nonce、issuer、audience、JWKS 签名、到期和必需 claims；JWT 模式执行同等 JWT 边界。所有 REST 查询与写入只使用服务端派生的当前用户。
- 日志不记录完整认证令牌、密钥、请求正文、原始音频或不必要的学生文本；`user_id` 只记录进程盐化摘要。
- 数据库存储 UTC，前端默认按 `Asia/Shanghai` 展示。
- 所有关键写入使用事务并在提交后重新查询验证。
- 所有确认判断在服务端完成。挑战只保存哈希并绑定用户、方法/操作、规范化 payload、阶段和有效期；重放、跨用户、篡改与并发重复消费均失败。
- 原始音频持久化未实现且不能开启。转写、纠错、对话和审计记录按配置保留；用户可导出或清除自己的业务数据。发送到外部 LLM 的边界仅为意图输入/上下文或通知问答所需的编号证据，启用外部服务前应完成校方数据处理评审。
- 测试适配器仅存在于测试目录，生产演示不会用固定文本冒充 AI 输出。

SQLite 删除后的空闲页、WAL 与备份不等同于物理擦除；校园试点必须执行隐私保留运维手册中的 checkpoint、备份保留、恢复演练与安全擦除流程。本项目采用 [Apache License 2.0](LICENSE)。
