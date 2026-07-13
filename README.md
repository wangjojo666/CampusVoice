# CampusVoice 声程

面向大学生的可验证校园语音学习助手。CampusVoice 把浏览器语音转写、校园术语纠错、结构化意图、风险确认、数据库事务和执行后验证连接成一条可审计链路；模型输出本身永远不被当作写入成功的证据。

## 已实现能力

- 浏览器 AudioWorklet 采集 16 kHz 单声道 PCM，通过 `/ws/asr` 实时传输。
- FunASR 流式中文识别、FSMN-VAD、CT-Punc、热词和 Whisper 离线基线。
- 创建/修改/删除任务与日历、冲突与重复检测、乐观版本检查和撤销。
- 严格 Pydantic 意图 Schema、一次结构化修复、缺失字段追问和低置信度保护。
- development/test 显式 demo 身份，以及 production 强制 OIDC/JWT（issuer、audience、JWKS、非对称签名）身份边界。
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

本地默认仅在 `development` 下显式启用 demo auth。生产环境必须设置 `CAMPUSVOICE_ENV=production`、`CAMPUSVOICE_AUTH_MODE=jwt`、JWT issuer/audience/JWKS、允许的非对称算法，以及至少 32 字符的 `CAMPUSVOICE_CONFIRMATION_SECRET`；缺少任何一项都会在启动时失败，不会回退到 `user_demo`。Web 客户端只在内存中持有 Bearer token；不要把 token 放入 URL、浏览器持久存储或日志。

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

在 `development + demo auth` 下，应用会创建固定演示身份 `user_demo`；它只用于本地合成数据，不是生产账户。JWT 模式下用户 ID 由服务端根据受验证的 issuer 与 subject 稳定映射，客户端不能通过请求头、路径或正文选择用户。种子脚本通过 REST 接口写入合成课程、热词、待办、考试日程和两份校园通知，可安全重复运行。

## Docker Compose

普通核心服务：

```powershell
docker compose up --build
```

默认 Compose 镜像不安装 AI 额外依赖，并明确使用 `ASR=disabled` 与 `knowledge=lexical`；它适合先验证任务、日历、确认、事务和引用链路，不会伪造语音转写结果。若手动把检索器切到 `embedding`，必须同时构建 AI 镜像。

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
python -m pytest --cov=app
python -m ruff check app tests
python -m ruff format --check app tests
python -m mypy app
python -m pip_audit --require-hashes --disable-pip -r requirements/runtime.lock

# 回到仓库根目录后运行前端
pnpm test
pnpm test:coverage
pnpm typecheck
pnpm lint
pnpm build
pnpm format:check

# 首次安装 Playwright Chromium，随后运行浏览器合约场景
pnpm --filter @campusvoice/web exec playwright install chromium
pnpm test:e2e

# Windows 已安装 Microsoft Edge 时也可直接验证 Edge 通道
pnpm --filter @campusvoice/web test:e2e:edge
```

默认 `pnpm test:e2e` 是前端浏览器合约测试：它使用真实 Next 页面，但会拦截 REST，并在页面上下文中模拟 AudioWorklet、WebSocket 和合成 PCM `ArrayBuffer`，因此不等同于完整系统 E2E。Compose smoke 另行启动真实 Web、API 与 SQLite，且不拦截 `/api/**`；两套测试都只用合成数据，不请求或录制真实麦克风。

安装并启动 Docker Engine/Desktop 后，可从 Git Bash、WSL 或 CI shell 执行真实全栈场景；脚本无论成功失败都会停止服务并删除 smoke volume：

```bash
bash scripts/run-compose-smoke.sh
```

Windows PowerShell 可直接运行等价脚本：

```powershell
pnpm test:e2e:smoke:windows
```

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
- production 只接受经 issuer、audience、签名和必需 claims 验证的 JWT；所有 REST 查询与写入都使用服务端派生的当前用户。
- 日志不记录完整认证令牌、密钥、请求正文、原始音频或不必要的学生文本；`user_id` 只记录进程盐化摘要。
- 数据库存储 UTC，前端默认按 `Asia/Shanghai` 展示。
- 所有关键写入使用事务并在提交后重新查询验证。
- 所有确认判断在服务端完成。挑战只保存哈希并绑定用户、方法/操作、规范化 payload、阶段和有效期；重放、跨用户、篡改与并发重复消费均失败。
- 原始音频持久化未实现且不能开启。转写、纠错、对话和审计记录按配置保留；用户可导出或清除自己的业务数据。发送到外部 LLM 的边界仅为意图输入/上下文或通知问答所需的编号证据，启用外部服务前应完成校方数据处理评审。
- 测试适配器仅存在于测试目录，生产演示不会用固定文本冒充 AI 输出。

SQLite 删除后的空闲页、WAL 与备份不等同于物理擦除；内测部署仍需独立制定备份保留、访问控制与安全擦除策略。项目当前不自动选择开源许可证，MIT 与 Apache-2.0 的取舍需由项目所有者决定。
