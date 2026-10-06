# Knowledge Hub Backend（Python / FastAPI）

企业知识库后端：认证与 RBAC、文档管理（审核流）、文件解析（PDF / DOCX / XLSX / PPTX / TXT / MD）、
Elasticsearch 全文检索、RAG 混合检索（BM25 + kNN → RRF → DashScope 重排）、
Agent RAG 流式问答（LangChain / LangGraph + DashScope qwen-plus，SSE）、
Neo4j 知识图谱、RabbitMQ 异步索引流水线。

由原 NestJS 版逐契约迁移（REST 路径 / 请求响应形状 / 中文错误文案 / SSE 流协议均保持一致），
接口目录见根目录 `curl*.md`、`agentic-rag.md`、`chat-memory.md`。

## 技术栈

| 层 | 选型 |
|---|---|
| Web | FastAPI + uvicorn（端口 3000，无全局前缀） |
| 校验 | Pydantic v2（`extra='forbid'`，错误体兼容 Nest 形状） |
| PostgreSQL | SQLAlchemy 2.0 async + asyncpg（schema 以 `init-scripts/postgresql/init.sql` 为准，不做 ORM 迁移） |
| MongoDB | Motor（`document_content` 正文） |
| Redis | redis.asyncio（重置码 / 激活 token / 对话短期记忆） |
| RabbitMQ | aio-pika（3 topic exchange / 3 队列 / 6 路由键） |
| Elasticsearch | elasticsearch[async] 8.17（`kh_document` + `kh_chunk` dense_vector，IK 分词） |
| Neo4j | neo4j AsyncGraphDatabase（KnowledgeDocument / DocumentChunk / KnowledgeEntity） |
| AI | langchain 1.x（`create_agent` + Summarization/ModelCallLimit 中间件）、langchain-openai（DashScope 兼容口） |
| 对象存储 | boto3（RustFS，S3 兼容） |
| 邮件 | aiosmtplib |

## 快速开始

```bash
# 1. 基础设施（postgres / mongo / redis / rabbitmq / es / neo4j / rustfs）
docker compose up -d

# 2. 配置环境变量（Key 按需填：OPENAI_API_KEY / EMBEDDING_API_KEY / BOCHA_API_KEY / MEM0_API_KEY）
cp .env.example .env

# 3. 安装依赖并启动（uv，Python 3.12）
uv sync
uv run uvicorn app.main:app --port 3000 --reload

# 4. 测试
uv run pytest
```

种子账号（见 `init-scripts/postgresql/init.sql`）：`admin` / `reviewer` / `user`，密码均为 `123456`。

> 说明：RAG 向量化、KG 抽取、对话生成需要 `OPENAI_API_KEY`（DashScope 兼容）；
> 未配置时这些能力软降级（与原版一致），其余功能不受影响。
> 各外部存储均可用 `*_ENABLED=false` 关闭。

## 目录结构

```
app/
├── main.py             # 入口：CORS、异常处理器、lifespan（ES 索引 / Neo4j / RustFS / RabbitMQ）
├── core/               # config（同名环境变量）snowflake errors security pagination ids
├── db/                 # postgres / mongo / redis 客户端 + models（12 张表）
├── common/             # 常量（角色/权限）序列化、utcnow
└── modules/
    ├── auth/           # JWT 鉴权依赖链、注册/登录/刷新、邮箱激活、密码重置
    ├── users/          # 用户 / 角色 / 权限 RBAC
    ├── teams/          # 团队树与成员（裸数组 body）
    ├── documents/      # 文档 CRUD、状态机、审核流、六种文件解析器
    ├── storage/        # RustFS 上传
    ├── search/         # POST /search（IK 高亮）
    ├── pipeline/       # 分块 / 向量化 / ES 双索引 / KG 抽取 / Neo4j 建图 / 编排器
    ├── mq/             # RabbitMQ 拓扑、发布者、消费者
    ├── graph/          # 图谱四个读接口
    └── ai/             # 混合检索、重排、意图改写、Agent（4 工具）、SSE 流式、会话、记忆
tests/                  # 单元测试（雪花 ID / 分块 / RRF / 引用 / SSE 帧协议等）
```
