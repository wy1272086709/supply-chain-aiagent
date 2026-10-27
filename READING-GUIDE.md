# Knowledge Hub 阅读指南（AI Agent 岗位面试版）

> 目标读者：准备 AI Agent / RAG 岗位面试的你（项目作者本人）。
> 用法：先看「一、全局架构」建立心智模型 → 按「三、推荐阅读顺序」过代码 → 面试前重点复盘「四、重点分级」和「五、高频追问与参考答法」。

---

## 一、全局架构：两条主线

整个系统是一个**企业知识库 + Agentic RAG** 后端，理解它只需抓住两条路径：

### 写路径（文档 → 索引）
```
上传文件(RustFS/S3) → 解析(PDF/DOCX/XLSX/PPTX/MD/TXT) → 草稿 → 审核流(提交/通过/驳回)
  → 发布 → 发 RabbitMQ 消息(3 交换机/3 队列/6 路由键) → 消费者异步执行三条管线：
  ① RAG 管线：分块 → Embedding → ES kh_chunk(dense_vector)
  ② Search 管线：全文 → ES kh_document(IK 分词高亮)
  ③ KG 管线：同款分块 → LLM 逐块抽实体/关系 → Neo4j 建图
```

### 读路径（提问 → 回答）
```
用户提问 → 意图路由(5 类意图, 决定挂哪些工具) → Agent 工具循环：
  retrieve_knowledge + retrieve_graph 首次强制并行
  → LLM 评估检索结果切题性(grade)
  → 不切题则 rewrite_query 改写 → 再查(知识库硬上限 2 次)
  → 仍不足按意图决定是否 web_search(Bocha)
→ 生成带 [n] 引用的回答 → SSE 流式输出(复刻 Vercel AI SDK 协议)
→ onFinish：落库 PG + 写 Redis 短期记忆 + Mem0 长期记忆(分类器决定是否写入)
```

**一句话介绍项目（30 秒版）**：
> 我做了一个企业知识库后端，核心是 Agentic RAG。文档发布后经 RabbitMQ 走三条异步索引管线（ES 全文、ES 向量块、Neo4j 知识图谱）；问答侧用 LangChain 1.x 的 Agent 做检索循环：意图路由决定挂载哪些工具，知识库与图谱检索首次强制并行，检索结果由 LLM 评估切题性，不足则改写重查（最多两次），仍不足按意图决定联网；检索是 BM25 + kNN → RRF → 重排的混合方案，权限过滤下推到 ES filter 与 Neo4j Cypher；输出走 SSE 流式并带引用回收，记忆分 Redis 热窗口 / PG 持久化 / Mem0 长期记忆三层。

---

## 二、模块地图（每个模块一句话）

| 模块 | 功能 | 面试权重 |
|---|---|---|
| [app/modules/ai/](app/modules/ai/) | **Agentic RAG 全部核心**：混合检索、重排、意图路由、Agent 工具循环、SSE 流式、三层记忆、会话管理 | ⭐⭐⭐⭐⭐ |
| [app/modules/pipeline/](app/modules/pipeline/) | 索引管线：分块、向量化、ES 双索引、KG 抽取、Neo4j 建图、编排器 | ⭐⭐⭐⭐⭐ |
| [app/modules/mq/](app/modules/mq/) | RabbitMQ 拓扑声明、发布者、消费者（写路径的异步骨架） | ⭐⭐⭐ |
| [app/modules/documents/](app/modules/documents/) | 文档 CRUD、状态机、审核流、6 种文件解析器、**访问控制 access.py** | ⭐⭐⭐ |
| [app/modules/auth/](app/modules/auth/) | JWT 鉴权依赖链、注册/登录/刷新、邮箱激活、密码重置 | ⭐ |
| [app/modules/users/](app/modules/users/) | 用户/角色/权限 RBAC | ⭐ |
| [app/modules/teams/](app/modules/teams/) | 团队树与成员 | ⭐ |
| [app/modules/search/](app/modules/search/) | POST /search 文档级全文搜索（IK 高亮） | ⭐⭐ |
| [app/modules/graph/](app/modules/graph/) | 图谱 4 个读接口（节点/边/搜索/概览） | ⭐⭐ |
| [app/modules/storage/](app/modules/storage/) | RustFS（S3 兼容）文件上传 | ⭐ |
| [app/core/](app/core/) | 配置、雪花 ID、异常体系、分页、安全工具 | ⭐ |
| [app/db/](app/db/) | PG/Redis/Mongo 客户端 + 12 张表 ORM 模型 | ⭐ |
| [tests/](tests/) | 单测：RRF、分块、引用、SSE 帧协议、访问范围等 | ⭐⭐ |

根目录的 [agentic-rag.md](agentic-rag.md)、[chat-memory.md](chat-memory.md)、[curl-rag.md](curl-rag.md) 是接口契约文档，读代码前扫一遍能对上"接口长什么样"。

---

## 三、推荐阅读顺序（约 3 天）

### 第 0 步：骨架（30 分钟）
1. [README.md](README.md) — 技术栈与目录总览
2. [app/main.py](app/main.py) — 入口装配：lifespan 里依次连 ES（建索引）→ Neo4j → RustFS → RabbitMQ（注册消费者），观察"不可用即降级"的基调

### 第 1 天：写路径（文档怎么变成可检索的索引）
3. [app/modules/documents/status.py](app/modules/documents/status.py) — 文档状态机（草稿/待审/已发布/已归档）
4. [app/modules/mq/constants.py](app/modules/mq/constants.py) — 3 交换机 / 3 队列 / 6 路由键的拓扑，26 行看完
5. [app/modules/pipeline/orchestrator.py](app/modules/pipeline/orchestrator.py) — 三条管线的分发中枢
6. [app/modules/pipeline/chunking.py](app/modules/pipeline/chunking.py) — Markdown 感知分块 + 标题继承（块前缀补当前标题）
7. [app/modules/pipeline/embedding.py](app/modules/pipeline/embedding.py) — OpenAIEmbeddings 指向 DashScope，批大小钳制 ≤10
8. [app/modules/pipeline/vector_index.py](app/modules/pipeline/vector_index.py) — kh_chunk 索引、kNN/关键词/混合检索、**RRF 融合**
9. [app/modules/pipeline/search_index.py](app/modules/pipeline/search_index.py) — kh_document 全文索引与 IK 高亮搜索
10. [app/modules/pipeline/kg_schema.py](app/modules/pipeline/kg_schema.py) + [extraction.py](app/modules/pipeline/extraction.py) — 10 实体类型 / 12 关系类型的抽取 schema 与结构化输出
11. [app/modules/pipeline/graph_build.py](app/modules/pipeline/graph_build.py) — Neo4j 建图（先清再建幂等）与 `retrieve_for_chat`（对话用图谱检索）
12. [app/modules/documents/access.py](app/modules/documents/access.py) — **横切重点**：同一套可见性规则下推到 ES filter / Neo4j Cypher / 内存判断

### 第 2 天：读路径上半场——检索与 Agent（面试核心，逐行读）
13. [app/modules/ai/hybrid_retrieval.py](app/modules/ai/hybrid_retrieval.py) — 混合检索编排：召回 → RRF → 重排 → 分数过滤
14. [app/modules/ai/reranker.py](app/modules/ai/reranker.py) — DashScope text-rerank，失败降级 RRF 序
15. [app/modules/ai/query_rewrite.py](app/modules/ai/query_rewrite.py) — 三个结构化输出 runner：意图路由 / 切题评估 / 失败改写（**提示词工程范本**）
16. [app/modules/ai/agentic_retrieve.py](app/modules/ai/agentic_retrieve.py) — 单次"检索+评估"，以及无 Agent 时的等价循环
17. [app/modules/ai/agent.py](app/modules/ai/agent.py) — **全项目最重要的文件**：create_agent + 4 工具 + 2 中间件 + 按意图拼装的系统提示词
18. [app/modules/ai/chat_service.py](app/modules/ai/chat_service.py) — 非流式等价环（对照流式看，理解"同一套逻辑两种编排"）

### 第 3 天：读路径下半场——流式与记忆
19. [app/modules/ai/ui_stream.py](app/modules/ai/ui_stream.py) — Vercel AI SDK UI Message Stream 协议的 SSE 编码器
20. [app/modules/ai/stream_service.py](app/modules/ai/stream_service.py) — 流式编排 + `_TurnTracker`（text/reasoning/tool-call 帧交错的状态机）+ 引用回收落库
21. [app/modules/ai/short_memory.py](app/modules/ai/short_memory.py) + [sessions.py](app/modules/ai/sessions.py) — Redis 热窗口 + PG 持久化，miss 回填
22. [app/modules/ai/long_memory.py](app/modules/ai/long_memory.py) — Mem0 长期记忆：user/session 双层 + LLM 写入分类器
23. [tests/](tests/) — 挑 [test_rrf.py](tests/test_rrf.py)、[test_citations.py](tests/test_citations.py)、[test_ui_stream.py](tests/test_ui_stream.py) 看，面试可提"协议有测试兜底"

> auth/users/teams/storage 扫一眼 router 即可，面试一句话带过："常规 JWT + RBAC + 对象存储"。

---

## 四、重点分级（面试素材密度）

### 🔴 P0 —— 必须能脱稿讲清（被问概率 ~100%）

#### 1. Agentic RAG 循环 — [agent.py](app/modules/ai/agent.py)
- **四工具**：`retrieve_knowledge`（检索+评估）、`rewrite_query`（改写）、`retrieve_graph`（图谱）、`web_search`（联网）— 见 [agent.py:75-114](app/modules/ai/agent.py#L75-L114) 的入参 schema 与工具描述
- **防失控三层设计**（面试金句）：
  1. 提示词层：系统提示词明确"知识库最多两次、eval.ok 后不要再检索"（[agent.py:287-331](app/modules/ai/agent.py#L287-L331)）
  2. 工具体层：`state["retrieve_count"] >= 2` 直接返回 insufficient（[agent.py:177](app/modules/ai/agent.py#L177)）——**不信任模型的自控力，工具内部做硬限制**
  3. 全局层：`ModelCallLimitMiddleware(run_limit=8)` 兜底总调用数（[agent.py:282](app/modules/ai/agent.py#L282)）
- **中间件**：`SummarizationMiddleware(trigger 12 / keep 6)` 压缩长对话控 token（[agent.py:274-282](app/modules/ai/agent.py#L274-L282)）
- **并行工具调用**：`parallel_tool_calls: True` + 提示词强制"首次必须同一回复里并行调 retrieve_knowledge 和 retrieve_graph"——用提示词 + 模型能力实现结构化并行检索
- **按意图动态挂工具**：chitchat 不挂检索、profile 只用记忆、kb 挂库+图、web 挂联网——`plan` 由意图路由产出，工具集和系统提示词都按 plan 生成

#### 2. 混合检索三段式 — [hybrid_retrieval.py](app/modules/ai/hybrid_retrieval.py) + [vector_index.py](app/modules/pipeline/vector_index.py)
- 召回：BM25（`multi_match`，IK 分词，标题加权）与 kNN（dense_vector cosine，`num_candidates = max(k*10, 50)`）**并行**（[vector_index.py:244-265](app/modules/pipeline/vector_index.py#L244-L265)）
- 融合：RRF `score(d) = Σ 1/(C + rank + 1)`，C=60（[vector_index.py:278-302](app/modules/pipeline/vector_index.py#L278-L302)）
- 精排：DashScope text-rerank，之后 `rag_min_score` 过滤 + top_k 截断（[hybrid_retrieval.py:31-42](app/modules/ai/hybrid_retrieval.py#L31-L42)）
- **降级链**（面试金句）：嵌入失败 → 只走关键词；重排失败/空结果 → 用 RRF 序；评估器失败但有召回 → 放行。每一环都有 fallback，检索链路"可降级而非可失败"

#### 3. LLM 作为检索质量裁判 — [query_rewrite.py](app/modules/ai/query_rewrite.py)
- 三个 `with_structured_output` runner：**router**（5 意图分类 + standalone query 指代消解 + 图谱短实体抽取）、**grader**（切题性二分类 + 理由）、**retryWriter**（换一种问法）
- 意图体系：`chitchat / profile / kb / web / kb_then_web`，提示词里有易混样例（"我是哪个部门→profile，预算审核员属于哪个部门→kb"）——** Few-shot 反例纠偏**
- 失败兜底哲学：意图识别失败 → 按 kb 处理（"宁可多检索也不漏内部制度"，[query_rewrite.py:167-169](app/modules/ai/query_rewrite.py#L167-L169)）；改写结果与上次相同 → 回退原问题
- 改写防退化：`_distinct_query` 保证新 query ≠ 旧 query，否则宁可退回原问题

#### 4. 三层记忆架构
| 层 | 存储 | 内容 | 生命周期 |
|---|---|---|---|
| 短期 | Redis（[short_memory.py](app/modules/ai/short_memory.py)） | LangChain 消息热窗口（默认截断保留） | TTL |
| 持久 | PG kh_ai_session/message（[sessions.py](app/modules/ai/sessions.py)） | 全量问答 + sources | 永久（删会话级联） |
| 长期 | Mem0 云（[long_memory.py](app/modules/ai/long_memory.py)） | user 层（身份/偏好）+ session 层（本会话任务） | 跨会话 / 会话内 |

- 读：Redis miss → PG 回填最近 N 条 → 写回 Redis（[chat_service.py:54-67](app/modules/ai/chat_service.py#L54-L67)）
- 写：**LLM 分类器决定是否写入 Mem0**（`MemoryDecision`: write_user / write_session，[long_memory.py:15-21](app/modules/ai/long_memory.py#L15-L21)）
- **防记忆污染**（面试金句）：分类器提示词明确"知识库内容永远不要写成 user 记忆"——助手根据检索说出的制度条文是文档事实不是用户事实；写入时只传用户原话，答案仅截 300 字供判断且标注"不要当作用户事实"

#### 5. SSE 流式协议 — [ui_stream.py](app/modules/ai/ui_stream.py) + [stream_service.py](app/modules/ai/stream_service.py)
- 逐字节复刻 Vercel AI SDK UI Message Stream：`start → data-session/data-intent/data-eval/data-sources/data-graph → tool-input-start/available/output-available → reasoning-* / text-start/delta/end → finish → [DONE]`
- `_TurnTracker`（[stream_service.py:63-133](app/modules/ai/stream_service.py#L63-L133)）处理三路交错增量：文本、思考（DashScope 把思考放 `additional_kwargs.reasoning_content`，需要单独取）、工具调用参数流式拼接（`tool_call_chunks` 的 args 是碎片，攒够在回合结束时补发 `tool-input-available`）
- 工具消息到达 = 模型回合结束的信号，此时收口 text/reasoning 再发 tool output——**用 LangGraph `stream_mode=["messages","tools"]` 双通道还原前端协议**
- **引用回收**（[stream_service.py:246-248](app/modules/ai/stream_service.py#L246-L248)）：正则抽答案里实际出现的 `[n]`，只持久化被引用的 sources——引用与落库强一致，防止 sources 里挂着没用的文档

### 🟡 P1 —— 要能讲清设计取舍（被问概率 ~50%）

#### 6. 分块策略 — [chunking.py](app/modules/pipeline/chunking.py)
- `RecursiveCharacterTextSplitter` + Markdown 分隔符，还补了 `\n# `（内置不含 H1）
- **标题继承**：块内无标题时把最近标题拼回块首（[chunking.py:74-76](app/modules/pipeline/chunking.py#L74-L76)），让每个向量块自带语义上下文——检索命中后 heading 也进了引用和 rerank 文本
- chunk_id = sha256(docId:序号)，天然幂等

#### 7. KG 构建与图谱检索 — [graph_build.py](app/modules/pipeline/graph_build.py)
- 图模型：`(KnowledgeDocument)-[:HAS_CHUNK]->(DocumentChunk)-[:MENTIONS]->(KnowledgeEntity)`，实体间 `RELATED_TO`
- **复用 RAG 同款分块**（[graph_build.py:193-207](app/modules/pipeline/graph_build.py#L193-L207)）——图谱粒度与向量块一致，两条链路可对照
- 每块 LLM 结构化抽取（pydantic schema），类型规范化（未知实体→CONCEPT、未知关系→RELATED_TO）、**挂空关系直接丢弃**（source/target 必须是已抽实体，[extraction.py:102-115](app/modules/pipeline/extraction.py#L102-L115)）
- 先清再建 + MERGE 幂等，重复发布不翻倍
- `retrieve_for_chat`（[graph_build.py:516](app/modules/pipeline/graph_build.py#L516)）：实体名相等/前缀/后缀/别名四路匹配，再查关系子图与来源文档；无向图关系在已选实体集合内闭合
- **图谱定位**（面试金句）：提示词反复强调"图谱只说明实体关系，不能当制度原文"——GraphRAG 的边界意识，防模型拿拓扑当事实

#### 8. 权限下推到检索层 — [access.py](app/modules/documents/access.py)
- 同一规则（admin/reviewer 无限制；否则 本人作者 ∪ 已发布公开 ∪ 同团队）三处生效：
  - ES：`bool.should` + `minimum_should_match:1`，放在 **filter 上下文不打分不缓存失效**（[access.py:40-50](app/modules/documents/access.py#L40-L50)），kNN 用 `knn.filter`（[vector_index.py:230-231](app/modules/pipeline/vector_index.py#L230-L231)）
  - Neo4j：参数化 Cypher where 子句（[access.py:67-71](app/modules/documents/access.py#L67-L71)）
  - 应用层：`can_read_document`
- **安全要点**：RAG 检索在向量库层面就过滤，无权限文档根本不进候选集，而不是检索后过滤——避免"上下文泄露 + 先污染 rerank 分数"两个问题

#### 9. 异步索引管线 — MQ + orchestrator
- 发布/下架/删除只发消息，三条管线异步消费，接口响应快、失败隔离（单文档失败不阻断批次）
- 文档可见性变更（改公开/换团队）不重建索引，只 `update_by_query` / Cypher 批量改三个字段（[orchestrator.py:165-181](app/modules/pipeline/orchestrator.py#L165-L181)）
- 拓扑：3 topic exchange / 3 durable queue / 6 routing key（[constants.py](app/modules/mq/constants.py)）

### 🟢 P2 —— 了解即可，被问到再展开
- **非流式 vs 流式双实现**：[chat_service.py](app/modules/ai/chat_service.py)（编排式，检索用 `retrieve_until_relevant` 等价环）vs [stream_service.py](app/modules/ai/stream_service.py)（Agent 式）。面试可讲"同一检索语义的两种编排：代码式适合调试/非流式，Agent 式让模型自主决定工具序列"
- **NestJS → FastAPI 迁移**：逐契约迁移——REST 路径、请求响应形状、中文错误文案、**SSE 帧字节级一致**（`separators=(",",":")` 等细节，[ui_stream.py:1-8](app/modules/ai/ui_stream.py#L1-L8)），有 [test_ui_stream.py](tests/test_ui_stream.py) 兜底。这是工程素养的加分项
- 文档审核流（submit/approve/reject + 待审列表）、6 种解析器、雪花 ID、PG/Mongo/Redis/ES/Neo4j/Neo4j 多存储选型理由（元数据 PG、正文 Mongo、热数据 Redis）

---

## 五、高频追问与参考答法

**Q1: 为什么用 RRF 而不是分数加权融合？**
> BM25 分数无上界、cosine 有界，两者尺度不可比，加权需要调参且不稳。RRF 只用排名不用分值，天然免调参（C=60 是学界常用值），对单路异常分数稳健。代价是丢掉分数信息，所以后面还有 cross-encoder 重排补精度。

**Q2: 为什么 rerank 放召回之后，而不是直接用大模型检索？**
> 分工：召回层要快且全（ES ANN + BM25 毫秒级，各取 top20）；重排模型（cross-encoder）精度高但慢且按条计费，只对 RRF 融合后的候选精排。这是"召回求全、精排求准"的经典两段式。rerank 失败自动降级用 RRF 序，链路不断。

**Q3: 怎么防止 Agent 循环失控 / token 爆炸？**
> 三层：提示词写清规则（最多检索两次）；工具体内维护 `retrieve_count` 硬限制，超限直接返回 insufficient——不依赖模型自觉；全局 `ModelCallLimitMiddleware(run_limit=8)` 兜底。另有 `SummarizationMiddleware` 压历史，12 条触发压到 6 条。

**Q4: 检索质量怎么评估、失败怎么办？**
> 用 LLM-as-judge 对命中做切题性二分类（带理由，前端可见 eval 帧）。失败策略是 fail-open：评估器挂了但有召回就放行，意图识别挂了按知识库处理——企业场景宁可多答不可漏答。改写后仍不切题会明确说"知识库没有相关内容"，禁止编造。

**Q5: 记忆系统怎么设计、怎么防污染？**
> 三层：Redis 热窗口（快、TTL、miss 从 PG 回填）、PG 全量持久化、Mem0 长期记忆。Mem0 写入前有独立分类器判断 user 层（跨会话的身份偏好）还是 session 层（本会话任务），核心防线是"知识库检索出的制度条文永远不写成用户记忆"——写入只喂用户原话，助手回答仅截断供参考并显式标注不是用户事实。

**Q6: 引用标注怎么保证可信？**
> 检索命中即分配全局递增编号并推给前端 data-sources；生成时系统提示词要求句末标 [n] 且"不要标注未使用的编号"；落库前正则回收答案中实际出现的编号，只持久化被引用的来源。未标注时回退为全部召回（非流式路径）。

**Q7: 权限怎么进 RAG？**
> 检索时把用户 scope（是否无限制/本人/团队列表）下推到存储层：ES 用 bool should filter（filter 上下文不影响相关性打分），kNN 走 knn.filter，Neo4j 用参数化 where。无权限文档不进候选集——先过滤后打分，避免污染排序，也避免上下文注入。

**Q8: 为什么知识库和图谱要并行查？**
> 两者互补：向量库给原文片段（制度条文），图谱给结构化关系（谁负责什么、流程依赖）。串行会加一倍延迟且图谱检索词是短实体名、与全文 query 不同源，没有依赖关系，所以首次强制并行（parallel_tool_calls + 提示词约定），图谱结果仅作关系参考不当原文。

**Q9: 流式输出为什么要自己写协议层？**
> 前端用 Vercel AI SDK 的 useChat，要求 UI Message Stream 帧协议（text-start/delta/end、tool-input-*、data-* 自定义帧、[DONE]）。后端在 Python 侧逐字节复刻，包括 JSON 无空格序列化、DashScope 思考增量从 additional_kwargs.reasoning_content 取、工具参数碎片在回合结束时拼装补发。有单测锁帧格式，迁移前后前端零改动。

**Q10: 这个项目遇到最难的问题？**
> 可选方向（挑真实的讲）：① 让模型稳定并行调用两个工具（提示词多次迭代 + 工具描述里写清调用协议）；② DashScope 兼容口与 Vercel 协议的对齐（思考流、工具参数碎片、usage 字段差异）；③ KG 抽取的类型漂移（模型乱给类型/把 relation 写成 type，用宽松 schema + normalize 兜底而不是严格 enum 拒绝）。

---

## 六、面试演示动线（如果可以现场演示）

1. 上传一份 Markdown 制度文档 → 审核通过 → 发布，观察日志里三条管线依次执行（分块数、实体数）
2. 问一个文档里的问题 → 前端看 SSE 帧流：intent → 工具调用（并行检索）→ eval → 引用作答
3. 故意问库里没有的 → 看 eval.insufficient → rewrite → 二次检索 → "知识库没有相关内容"
4. 说"以后回答短一点" → 新会话再问 → 演示长期记忆生效
5. 用普通用户问管理员私有文档 → 演示检索层权限过滤

---

## 附：核心数字速记

| 项 | 值 |
|---|---|
| Agent 模型调用上限 | 8（ModelCallLimitMiddleware） |
| 知识库检索硬上限 | 2 次/轮（工具体内计数） |
| 历史压缩 | 12 条触发 → 保留 6 条 |
| RRF | C=60，两路各 top20 |
| Embedding 批上限 | 10（DashScope 限制） |
| 重排 | DashScope text-rerank，min_score 过滤 |
| MQ | 3 exchange / 3 queue / 6 routing key |
| KG | 10 实体类型 / 12 关系类型 |
| 意图 | 5 类（chitchat/profile/kb/web/kb_then_web） |
| 上下文块截断 | 800 字符（进模型），摘录 200 字符（给前端） |
