# Agentic RAG 检索链路详解（结合代码）

配合 [agentic-rag.md](agentic-rag.md)（前端自测用例）食用：那份讲"测什么"，这份讲"为什么、代码在哪、怎么实现的"。架构取舍 / 失败处理 / 扩展方案的七问七答见 [rag-design-qa.md](rag-design-qa.md)。所有行号以当前 main 为准。

---

## 0. 一轮问答的全景时序

```
用户问题
  │
  ├─① 意图路由 classify()                     query_rewrite.py:167
  │     LLM 结构化输出 → {intent, standalone_query, graph_queries}
  │     （与此同时并行跑长期记忆检索 long_memory.search   stream_service.py:183）
  │
  ├─② to_plan() 意图 → 工具开关               query_rewrite.py:134
  │     {allowRetrieve, allowGraph, allowWeb}
  │
  ├─③ create_turn_agent() 按开关挂工具          agent.py:160
  │
  └─④ Agent 循环（langchain create_agent，模型调用上限 8 次）
        │
        第一步（kb / kb_then_web）：同一次回复里【并行】发两个工具调用
        ├─ retrieve_knowledge ──→ ES kh_chunk
        │     BM25 关键词 ┐（两路也并行，asyncio.gather）
        │     kNN 向量    ┘→ RRF 融合 → rerank 精排 → min_score → top_k
        │     ★ 权限过滤发生在 ES 召回阶段（filter 上下文）
        └─ retrieve_graph ──→ Neo4j Cypher
              短实体名 → 实体 → 关系 → 来源文档
              ★ 权限 WHERE 织进 Cypher
        │
        ⑤ 切题评估 grade_hits（LLM）           query_rewrite.py:201
        ├─ 切题   → 用文档 context 作答，标 [n]
        └─ 不足   → rewrite_query（LLM 改写）→ retrieve_knowledge 第二次
              └─ 仍不足 →
                   kb          ：明确说没有，禁止编造（web_search 根本没挂载）
                   kb_then_web ：web_search（Bocha）联网补充
```

前端过程条「意图 → 图谱 → 知识库 → 评估 → 改写 → 再检索 → 联网」对应的就是 ①④⑤ 各阶段 SSE 事件（`data-intent` / `data-graph` / `data-sources` / `data-eval` / `data-status:rewrite`）。

---

## 1. 首次强制并行、意图路由、切题评估

### 1.1 "同时查"并行的是什么

是 **Agent 在同一个模型回合里并行发起两个工具调用**，两个工具分别打不同的库——不是某一条查询同时打两个库：

| 工具 | 目标库 | 干什么 |
|---|---|---|
| `retrieve_knowledge` | Elasticsearch `kh_chunk` | 制度原文：BM25 + kNN → RRF → rerank |
| `retrieve_graph` | Neo4j | 实体关系：短实体名点查 → 关系 → 来源文档 |

"强制"有三层保障（层层加码，都在 agent.py）：

1. **模型能力层**：`parallel_tool_calls: True`（[agent.py:47](app/modules/ai/agent.py#L47)），允许一次回复吐多个 tool call；
2. **工具描述层**：`RETRIEVE_KNOWLEDGE_DESCRIPTION` 写明"首次必须与 retrieve_graph 在同一次并行调用"（[agent.py:93-97](app/modules/ai/agent.py#L93-L97)）；
3. **系统提示词层**：`system_for_plan` 最强硬——"第一步必须在同一次回复里并行调用 retrieve_knowledge 和 retrieve_graph，两个都要调：不要先查库再决定是否查图，不要只调其中一个，也不要等一个返回再调另一个"（[agent.py:298-300](app/modules/ai/agent.py#L298-L300)）。

**为什么要强制**：
- 文档命中预测不了图谱命中——两个库、两套匹配方式（全文/向量 vs 实体名精确匹配）；
- 两者回答不同侧面：图谱只说"张敏 → 负责 → 报销"这种实体关系，文档才有制度原文，信息互补；
- 图查询是按短实体名点查，成本低，永远查一下不亏；
- 不强制的话，模型在文档命中后倾向于跳过图谱（迁移自 TS 版的实测行为）。

另外注意 `retrieve_knowledge` **内部**也是并行的：ES 的 BM25 与 kNN 两路用 `asyncio.gather` 同时发（[vector_index.py:244-265](app/modules/pipeline/vector_index.py#L244-L265)）。

图谱检索词来自两处合并：路由阶段产出的 `graphQueries`（短实体名数组）+ 模型调工具时传的 `query`，后者按空格/斜杠再拆分（[agent.py:244-247](app/modules/ai/agent.py#L244-L247)、`split_graph_keywords` [graph_build.py:136-151](app/modules/pipeline/graph_build.py#L136-L151)）。

### 1.2 "意图路由决定挂载哪些工具"

三步：

**第一步：classify()**（[query_rewrite.py:167-192](app/modules/ai/query_rewrite.py#L167-L192)）。一次 LLM 结构化输出（`with_structured_output(RouteResult)`，function calling 强约束格式），输入是压缩后的对话历史 + 当前问题，输出三样东西：

| 字段 | 含义 |
|---|---|
| `intent` | 五选一：`chitchat` 闲聊 / `profile` 个人偏好 / `kb` 知识库 / `web` 联网 / `kb_then_web` 库不够再联网 |
| `standalone_query` | 消解了"这个/谁负责/怎么办"的独立检索句（≤40 字） |
| `graph_queries` | 图谱短实体名（仅 kb 类意图非空；每个 2~4 字，"发票如何报销"必须拆成"发票、报销"） |

**第二步：to_plan()**（[query_rewrite.py:134-145](app/modules/ai/query_rewrite.py#L134-L145)），意图翻译成三个布尔开关：

| intent | allowRetrieve | allowGraph | allowWeb |
|---|---|---|---|
| chitchat | ✗ | ✗ | ✗ |
| profile | ✗ | ✗ | ✗ |
| kb | ✓ | ✓ | ✗ |
| web | ✗ | ✗ | ✓ |
| kb_then_web | ✓ | ✓ | ✓ |

**第三步：create_turn_agent()**（[agent.py:160-267](app/modules/ai/agent.py#L160-L267)）——这里是"挂载"的实义：**只有开关为 true 的 `@tool` 才被定义、才进 `create_agent(tools=...)` 的列表**。闲聊时 agent 手里根本没有 `retrieve_knowledge`，想调也调不了，这是硬约束；`system_for_plan`（[agent.py:287-331](app/modules/ai/agent.py#L287-L331)）再叠一层提示词软约束（"不要调用 retrieve_knowledge…"）。

双重约束是刻意的：提示词会被模型"偶尔忽略"，工具不存在则永远不会被调用。

**失败兜底**：classify 异常（超时/格式错）→ 按 kb 处理，"宁可多检索也不漏内部制度"（[query_rewrite.py:167-169](app/modules/ai/query_rewrite.py#L167-L169)、190-192）。

顺带一提：意图识别和长期记忆检索是并发的（`asyncio.create_task`，[stream_service.py:183-184](app/modules/ai/stream_service.py#L183-L184)），互不阻塞。

### 1.3 LLM 怎么评估切题性

位置在 `retrieve_and_grade`（[agentic_retrieve.py:54-55](app/modules/ai/agentic_retrieve.py#L54-L55)）：每次检索完**立刻**评估，不等模型作答时自己判断。

流程（`grade_hits`，[query_rewrite.py:201-221](app/modules/ai/query_rewrite.py#L201-L221)）：

1. **空结果短路**：零命中直接返回 `{"ok": false, "reason": "empty"}`，不调模型（省钱省时延）；
2. **构造输入**：系统词 `GRADE_PROMPT` + 用户原问题 + 命中摘要——前 5 条，每条是 `标题 / 章节（分=x.xx）+ 180 字摘录`（`_summarize_hits`）；
3. **判据**（提示词原文，[query_rewrite.py:26-33](app/modules/ai/query_rewrite.py#L26-L33)）：
   - 切题 = 同一主题、同一制度/岗位/流程，能支撑作答（不必覆盖每个细节）；
   - 不切题 = 只是词沾边（如问加班餐补却命中差旅报销）、或完全另一件事；
   - 只根据给定标题和摘录判断，不要假设库里还有别的文档（防模型脑补"应该有"）；
4. **输出**：`GradeResult {relevant: bool, reason: str}` 结构化输出，reason 是给前端过程条看的一句中文；
5. **评估的作用**：`"hits": raw_hits if grade["ok"] else []`（[agentic_retrieve.py:58](app/modules/ai/agentic_retrieve.py#L58)）——不切题时命中**整组丢弃**，不进模型上下文，避免"沾边资料诱导模型硬答"；
6. **容错**：评估调用本身失败 → 有召回就放行（[query_rewrite.py:219-221](app/modules/ai/query_rewrite.py#L219-L221)），偏向能答而非拒答。

与分数阈值的分工：`rag_min_score=0.4`（config.py:61）是**单条质量**硬阈值，在 rerank 后过滤；grade 是**整组语义**判断。触发改写循环看 grade，不看分数。

---

## 2. Neo4j 与 Cypher

### 2.1 关系：一个是数据库，一个是它的查询语言

- **Neo4j = 图数据库**（存储引擎 + 产品）；
- **Cypher = Neo4j 的声明式查询语言**。

类比：PostgreSQL 之于 SQL，就是 Neo4j 之于 Cypher。Cypher 后来捐成 openCypher 标准、也被其他图数据库兼容，Neo4j 也支持新兴的 GQL 标准，但本项目里两者就是绑定关系：驱动 `neo4j.AsyncGraphDatabase`（[graph_build.py:31-34](app/modules/pipeline/graph_build.py#L31-L34)）发 Cypher 语句给 Neo4j 执行。

Neo4j 存的是**属性图**：
- 节点（Node）：带标签（label，类似类型）和属性（key-value）；
- 关系（Relationship）：带类型、**有方向**、也能带属性。

本项目的图模型（[graph_build.py:3-4](app/modules/pipeline/graph_build.py#L3-L4)）：

```
(KnowledgeDocument)-[:HAS_CHUNK]->(DocumentChunk)-[:MENTIONS]->(KnowledgeEntity)
(KnowledgeEntity)-[:RELATED_TO]->(KnowledgeEntity)
```

即：文档包含分块，分块提及实体，实体之间有关联。

### 2.2 Cypher 解决了什么问题

图查询的本质是**模式匹配 + 多跳遍历**。用 SQL 表达"找到了发票和报销两个实体之间、经由任意中间实体的关联"，要写递归 CTE 或层层自连接，每加一跳加一层 JOIN，写法和性能都恶化。Cypher 的解法：

1. **ASCII-art 模式语法**：`(a)-[:RELATED_TO]->(b)` 一行一跳，可变长路径 `-[:MENTIONS*1..3]-` 原生支持，模式即查询；
2. **与图执行模型互相成就**：Neo4j 用 index-free adjacency——每个节点直接持有指向邻居的指针，遍历是顺着指针走，代价正比于命中的子图而不是数据总量。语言负责把模式声明出来，引擎负责把它编译成指针遍历。

对建图场景还有个关键原语：**`MERGE` = "有则匹配、无则创建"** 的幂等 upsert。本项目重复发布同一文档不会让节点/边翻倍，就是靠 MERGE + 先删后建（[graph_build.py:166](app/modules/pipeline/graph_build.py#L166) 注释）。

### 2.3 本项目 Cypher 的三类用法（都在 graph_build.py）

**① 写入：建图**（`build_for_document`，[157-251](app/modules/pipeline/graph_build.py#L157-L251)）

发布文档时：先 `delete_for_document` 清旧图（`DETACH DELETE` 连边一起拆 + 孤儿实体清理），然后逐块：

```
MERGE (d:KnowledgeDocument {id: $id}) SET d.title=..., d.authorId=..., d.teamId=..., d.isPublic=...
MERGE (c:DocumentChunk {chunkId: $chunkId}) ... MERGE (d)-[:HAS_CHUNK]->(c)
```

每块内容先过 LLM 结构化抽取（`extraction.py`：实体名/类型/别名/描述 + 实体对之间的关系，数量上限可配），再落图：

```
MERGE (e:KnowledgeEntity {name: $name}) ON CREATE SET ... ON MATCH SET ...
MATCH (c:DocumentChunk {chunkId: $chunkId}) MATCH (e:...) MERGE (c)-[:MENTIONS]->(e)
MATCH (a:KnowledgeEntity {name: $source}) MATCH (b:...) MERGE (a)-[:RELATED_TO]->(b)
```

注意文档节点上**冗余了 `authorId/teamId/isPublic` 三个权限字段**——这是第 3.6 节权限过滤的伏笔。

图谱分块与 RAG 分块复用同一套 `chunking.chunk`，保证图粒度与向量块一致。

**② 读取：对话检索**（`retrieve_for_chat`，[516-613](app/modules/pipeline/graph_build.py#L516-L613)）三段式：

1. **实体匹配**：`any(kw IN $kws WHERE 精确相等 / 去书名号后前缀 / 后缀 / 别名匹配)`，排序按"精确=0，前后缀=1，其余=2，再按名字长度"，取前 8；WHERE 里织入可见性 EXISTS 子查询；
2. **关系**：`MATCH (a)-[:RELATED_TO]->(b) WHERE a.name IN $names AND b.name IN $names`——实体已经过权限过滤，关系不用再过；
3. **来源文档**：`MATCH (d)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e) WHERE e.name IN $names AND (权限WHERE)`，给模型标注"这些关系出自哪几篇文档"。

前端图谱页的 `search_graph` / `get_overview` / `list_nodes` / `list_edges` 也是 Cypher，只是排序/聚合更复杂。

**③ 权限 WHERE 片段**（[access.py:67-71](app/modules/documents/access.py#L67-L71)）拼进所有读查询：

```
($unrestricted OR d.authorId = $accessUserId OR d.isPublic = true OR d.teamId IN $accessTeamIds)
```

实体的可见性定义是**存在式的**：`EXISTS { MATCH (d:KnowledgeDocument)-[:HAS_CHUNK]->(:DocumentChunk)-[:MENTIONS]->(e) WHERE <上述条件> }`——"只要有一篇你可见的文档提及该实体，实体对你可见"（[graph_build.py:543-548](app/modules/pipeline/graph_build.py#L543-L548)）。

### 2.4 参数化，无注入面

所有用户可控输入（`$kws/$kw/$names/$id` …）都是**查询参数**；代码里的 f-string 只拼入固定的权限 WHERE 模板和别名（`d1`/`d2`/`n`），不含任何用户数据。所以不存在 Cypher 注入。

---

## 3. 改写重查、不足判定、权限过滤

### 3.1 "不足"怎么判

"不足"（insufficient）是评估环节的三种出口之一，**不是分数低**：

| 出口 | 判定方式 | 后果 |
|---|---|---|
| `empty` | 检索零命中（短路，不调评估模型） | 触发改写 |
| `irrelevant` | LLM 评估不切题（见 1.3） | 触发改写 |
| `error` | 检索异常（ES 挂了等） | **不重试**，模型按已有信息作答或联网 |

再次强调分工：`rag_min_score=0.4` 只过滤**单条**低质命中；触发改写循环的是**整组语义**评估。

### 3.2 改写规则

提示词 `RETRY_REWRITE_PROMPT`（[query_rewrite.py:35-43](app/modules/ai/query_rewrite.py#L35-L43)）五条：

1. 紧扣用户原问题，不要跑题；
2. **不要重复上次检索词**；
3. 可换同义、补全制度/岗位/补贴类型等核心实体；
4. 不要编造条款号、专有名词；
5. 一句中文，尽量不超过 40 字。

改写器的输入（[query_rewrite.py:232-243](app/modules/ai/query_rewrite.py#L232-L243)）：用户原问题 + 上次检索词 + 不足原因（grade 的一句话）+ 上次命中 top3 摘要（"有命中但不切题"时给，帮它知道上次捞到了什么歪资料；零命中时只写"上次无命中"）。

防呆链（`rewrite_after_retrieve`，[224-251](app/modules/ai/query_rewrite.py#L224-L251)）：
- `_distinct_query`：新词与上次词（空白归一后）相同 → 视为改写失败；
- 改写失败/相同 → 回退用**原问题**再查一次（毕竟改写器可能把词改得更偏）；
- 原问题也和上次相同 → 返回空串，循环终止。

典型改写样例（对应自测用例 11）：问"加班餐补怎么报"→ 首查"加班餐补 报销 标准"命中差旅报销（词沾边不切题）→ 改写器看懂"上次命中的是差旅报销不是加班餐补"→ 改写为"加班 餐费补贴 标准 报销流程"再查 → 仍无 → 明确说库里没有。

### 3.3 "最多两次"在哪强制

**代码硬约束优先，提示词辅助**：

- 硬约束在 `retrieve_knowledge` 工具闭包里：`state["retrieve_count"] >= 2` 时不再执行检索，直接返回错误消息"本轮知识库最多检索两次…"（[agent.py:177-183](app/modules/ai/agent.py#L177-L183)）——就算模型无视提示词发起第三次调用，也查不了；
- 软约束在工具描述和系统提示词（"不要第三次 retrieve_knowledge"），让模型别浪费回合；
- 兜底护栏：`ModelCallLimitMiddleware(run_limit=8, exit_behavior="end")` 整轮模型调用上限 8 次（[agent.py:282](app/modules/ai/agent.py#L282)），加上 `SummarizationMiddleware`（12 条消息触发摘要、保留 6 条）防上下文爆炸。

Agent 路径的循环由模型驱动：`retrieve_knowledge`（eval 不足）→ `rewrite_query` 工具（内部再调一次改写 LLM，可带 `focus` 描述缺口）→ 模型拿返回的新词再调 `retrieve_knowledge`。非流式 `/ai/chat` 没有模型自主决策，走 `retrieve_until_relevant` 等价环（[agentic_retrieve.py:80-116](app/modules/ai/agentic_retrieve.py#L80-L116)）：查一次 → 评估 → 不足 → 改写 → 再查一次，语义与 Agent 版一致。

### 3.4 两次后"按意图决定联网"——双重决定

能不能联网在**路由时**就定死了（工具挂载），该不该联网由**提示词**引导：

| intent | web_search 挂载？ | 提示词指令（system_for_plan） |
|---|---|---|
| kb | ✗ 根本没挂 | "明确说知识库没有相关内容，禁止编造制度，不要联网" |
| kb_then_web | ✓ | "先走完知识库循环；两次后仍 insufficient 再 web_search；网页不要写成内部制度" |
| web | ✓（且不挂检索） | 直接 web_search，用标题+链接说明 |

所以自测用例 20（年会抽奖规则）永远不会触发联网——`kb` 意图下模型手里没有 web_search 工具，物理上做不到。

### 3.5 先重排还是先权限过滤？——权限在最早

一次 `retrieve_knowledge` 内部（`hybrid_retrieval.retrieve`，[hybrid_retrieval.py:15-42](app/modules/ai/hybrid_retrieval.py#L15-L42)）的完整顺序：

```
① 查询向量化 embedding（失败 → 降级只走关键词）
② ES 召回，两路并行 —— ★权限过滤在这里★
     keyword_search: multi_match + filter=可见性bool     vector_index.py:182
     knn_search:     kNN + knn.filter=可见性             vector_index.py:214
     （filter 上下文：不影响相关性打分、可被 ES 缓存）
③ RRF 融合  score(d) = Σ 1/(60 + rank + 1)              vector_index.py:278
④ rerank 精排（DashScope qwen3-rerank；失败降级用 RRF 序） reranker.py:25
⑤ min_score=0.4 过滤 + 截 top_k（默认 5）                hybrid_retrieval.py:33-34
⑥ LLM 切题评估 grade_hits → 决定 hits 进不进模型上下文
```

**不是"先重排再权限过滤"，权限过滤在最早的召回阶段**。理由：

1. 权限是二值判断，不依赖语义排序，放哪都行，那就放最早；
2. 若 rerank 后再滤：不可见文档会占掉 top_k 名额，模型可见资料变少，且没有回填机制；
3. rerank API 按条计费，先滤省的是真金白银；
4. ES `filter` 上下文不打分、可缓存，对 BM25 和 kNN 都零打分干扰。

### 3.6 权限存哪、怎么关联过滤

**真源（source of truth）：PostgreSQL**

| 数据 | 表/来源 | 字段 |
|---|---|---|
| 文档归属 | `kh_document`（[document.py](app/db/models/document.py)） | `author_id` / `team_id` / `is_public` / `status` |
| 角色 | `kh_role` × `kh_user_role`（`get_role_codes`） | admin / reviewer → `unrestricted` |
| 团队 | 成员关系 → `list_accessible_team_ids` | 装配进 `user["teamIds"]` |

登录态把这些装配成 `user = {userId, roles, permissions, teamIds}`（`to_auth_user`，[users/service.py:57-68](app/modules/users/service.py#L57-L68)），随请求全程传递。

**访问规则**（`can_read_document`，[access.py:18-27](app/modules/documents/access.py#L18-L27)）：管理员/审核员全可见；否则 = **作者本人 OR（已发布 AND（公开 OR 我所在团队））**。检索入口先转成轻量 scope：`access_from_user(user) → {unrestricted, userId, teamIds}`（[access.py:9-15](app/modules/documents/access.py#L9-L15)）。

**关联过滤的核心问题**：ES 和 Neo4j 都无法 join 回 Postgres 查权限。解法是**冗余 + 同步**——把三个可见性字段冗余进每个检索单元：

| 存储 | 冗余位置 | 写入时 | 变更时同步 |
|---|---|---|---|
| ES | 每个 `kh_chunk` 文档都带 `is_public/author_id/team_id` | `_build_doc_map` 建索引时带上 | `update_by_query` 批量改块（[vector_index.py:85-113](app/modules/pipeline/vector_index.py#L85-L113)） |
| Neo4j | 每个 `KnowledgeDocument` 节点带同样三属性 | 建图 MERGE 时 SET | `update_visibility` SET 节点（[graph_build.py:262-284](app/modules/pipeline/graph_build.py#L262-L284)） |

同步入口统一在 `orchestrator.update_visibility`（[orchestrator.py:165-180](app/modules/pipeline/orchestrator.py#L165-L180)），三路并行（搜索索引/向量块/图节点）。另外**未发布的文档根本不进索引**：发布才建块建图，下架即删（[service.py:196](app/modules/documents/service.py#L196) + orchestrator 120/141），所以 ES filter 里不需要再查 status。

**过滤的具体织入**：

- **ES**（`es_visibility_filter`，[access.py:40-56](app/modules/documents/access.py#L40-L56)）：

  ```json
  {"bool": {"should": [
      {"term": {"is_public": true}},
      {"term": {"author_id": "<我>"}},
      {"terms": {"team_id": ["我的团队们"]}}
  ], "minimum_should_match": 1}}
  ```

  BM25 路用 `wrap_es_query` 包成 `bool.must + filter`；kNN 路塞进 `knn.filter`。管理员/审核员返回 `None`（不加过滤）。

- **Neo4j**：`neo4j_document_access_where` 生成的 WHERE 片段（见 2.3③），实体可见性用 EXISTS 子查询回溯到"可见文档 → 块 → 实体"链。

**安全属性（重要）**：过滤以**请求认证用户**为准，发生在**服务端检索工具内部、数据库查询层**。Agent/LLM 只能拿到过滤后的结果——就算有提示词注入让模型乱调工具，它也读不到任何越权文档；工具描述里那句"仅当前用户可见文档"只是给模型看的说明，真正的强制在查询里。

---

## 4. 关键文件索引

| 文件 | 职责 |
|---|---|
| [stream_service.py](app/modules/ai/stream_service.py) | 流式编排：会话 → 意图 → 建 Agent → SSE 转发 → 落库 |
| [query_rewrite.py](app/modules/ai/query_rewrite.py) | 意图路由 / 切题评估 / 改写，三个结构化输出 runner |
| [agent.py](app/modules/ai/agent.py) | 按意图挂工具、系统提示词、护栏中间件 |
| [agentic_retrieve.py](app/modules/ai/agentic_retrieve.py) | 单次"检索+评估"；非流式等价环 |
| [hybrid_retrieval.py](app/modules/ai/hybrid_retrieval.py) | 混合检索编排：嵌入→召回→RRF→rerank→阈值 |
| [vector_index.py](app/modules/pipeline/vector_index.py) | ES kh_chunk 索引、BM25/kNN/混合检索、RRF 实现 |
| [reranker.py](app/modules/ai/reranker.py) | DashScope text-rerank 精排，失败降级 |
| [graph_build.py](app/modules/pipeline/graph_build.py) | Neo4j 建图（MERGE）+ 对话/页面读查询（Cypher） |
| [extraction.py](app/modules/pipeline/extraction.py) | LLM 实体/关系抽取（图谱数据来源） |
| [access.py](app/modules/documents/access.py) | scope 装配、ES 可见性 filter、Neo4j 权限 WHERE |
| [users/service.py](app/modules/users/service.py) | user dict 装配（roles/teamIds 来源） |
| [orchestrator.py](app/modules/pipeline/orchestrator.py) | 发布管线；可见性三路同步 |

## 5. 相关配置（config.py）

| 配置 | 默认 | 含义 |
|---|---|---|
| `rag_hybrid_top_k` | 20 | 混合召回每路条数（rerank 候选池） |
| `rag_rrf_c` | 60 | RRF 平滑常数，越大两路排名越均衡 |
| `rag_rerank_enabled` / `rag_rerank_model` | true / qwen3-rerank | DashScope 精排 |
| `rag_min_score` | 0.4 | rerank 后单条硬阈值 |
| `embedding_dimension` | 1024 | 查询/块向量维度 |
| `neo4j_enabled` | true | 图谱开关（关闭则检索降级为空，不中断问答） |
