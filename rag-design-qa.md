# RAG 架构问答（设计取舍 / 失败处理 / 扩展方案）

承接 [agentic-rag-deep-dive.md](agentic-rag-deep-dive.md)（链路详解），本篇是七个架构问题的问答：为什么这么选、失败了怎么办、要扩展怎么改。前半部分是代码事实（带引用），标明「建议」的段落是设计讨论、当前代码没有实现。

---

## Q1. 为什么向量和全文放同一个 ES，而不是向量单独一个向量库？

**现状**：单节点 ES 8.17（自带 IK 分词的自定义镜像，512MB 堆内存，[docker-compose.yml:136-149](docker-compose.yml#L136-L149)），`kh_chunk` 一个索引里同时有 `content`（ik_max_word / ik_smart 全文）和 `embedding`（dense_vector 1024 维 cosine，[vector_index.py:35-57](app/modules/pipeline/vector_index.py#L35-L57)）。BM25 与 kNN 两路并行召回，RRF 在 Python 应用层融合（[vector_index.py:244-265](app/modules/pipeline/vector_index.py#L244-L265)）。

**理由**：

1. **权限下推只写一遍**。同一份 `es_visibility_filter` 在 BM25 路包成 `bool.filter`、在 kNN 路塞进 `knn.filter`（[access.py:40-56](app/modules/documents/access.py#L40-L56)）。拆成两个库，权限过滤、可见性同步、删除联动全部 ×2，还要处理跨库一致性（下架时两边都删干净）。
2. **生命周期统一**。块的建/删/改可见性只碰一个客户端（[orchestrator.py:165-181](app/modules/pipeline/orchestrator.py#L165-L181) 三路同步已经是三个库了，再加一个就是四路）。
3. **中文全文检索质量**。IK 分词是现成插件；专用向量库（Milvus/Qdrant）不含 BM25，混合检索仍然需要配一个全文引擎——结果还是两个库。
4. **规模匹配**。ES 8.x 的 HNSW 向量索引在这个量级（单节点开发/中小规模）够用；向量库的优势（亿级向量、GPU 索引、内存级标量过滤）用不上，拆库只增加运维面。
5. **融合层天然解耦**（这是最值钱的一点）。RRF 在应用层收两个列表（`rrf_fuse`），两路召回来自哪个引擎是实现细节——将来真要拆，替换 `knn_search` 一个函数即可，`keyword_search`、融合、评估、改写全不动。

**顺带**：PG 的镜像本身就是 `pgvector/pgvector:pg16`（[docker-compose.yml:4](docker-compose.yml#L4)），向量放 PG 是另一条"不加库"的路线，当前实现没走。何时该拆出专用向量库：向量到亿级、需要 GPU 索引或多租户物理隔离时，Qdrant/Milvus 才有明显收益。

---

## Q2. 实体关系抽取在索引阶段还是问答阶段？失败怎么处理？

**索引阶段做，问答阶段只读。**

链路：发布 → RabbitMQ（`kg.graph.build.by_ids`，[constants.py:18](app/modules/mq/constants.py#L18)）→ 消费者（[consumer.py:34-41](app/modules/mq/consumer.py#L34-L41)）→ `build_for_document` 逐块调 `extraction.extract`（LLM 结构化抽取，temperature 0.1，内容截 4000 字，[extraction.py:64-81](app/modules/pipeline/extraction.py#L64-L81)）→ MERGE 落 Neo4j。问答时 `retrieve_for_chat` 只有三条只读 MATCH，零抽取。

**为什么放索引期**：抽取是逐块 LLM 调用，一篇文档几十块就是几十次调用；放问答期时延不可接受。放索引期"一次付费、多次查询"，且经 MQ 异步化后发布接口即时返回——代价是图谱"稍后可见"，这就是自测文档里"等索引完成"的原因。

**失败处理是分层兜底的，从外到内**：

| 故障 | 行为 | 代码 |
|---|---|---|
| Neo4j 连不上 | `_driver=None`，建图跳过、对话检索返回空，**不中断问答**（前端卡显示「图谱中没有匹配实体」） | [graph_build.py:38-40](app/modules/pipeline/graph_build.py#L38-L40)、516-519 |
| 未配置 LLM Key | 抽取器抛 RuntimeError | [extraction.py:68-70](app/modules/pipeline/extraction.py#L68-L70) |
| 单块抽取失败 | 捕获后跳过该块，其余块继续 | [graph_build.py:236-243](app/modules/pipeline/graph_build.py#L236-L243) |
| 单文档失败 | `build_batch` 逐文档 try/except | [graph_build.py:254-259](app/modules/pipeline/graph_build.py#L254-L259) |
| 重复发布 | 先 `delete_for_document`（DETACH DELETE + 孤儿实体清理）再建，MERGE 幂等，不翻倍 | [graph_build.py:166](app/modules/pipeline/graph_build.py#L166)、287-308 |

**诚实的缺口**：没有重试队列/死信——抽取失败的块不会自动重试，只能发 `BUILD_ALL` 消息全量重建手动兜底（[orchestrator.py:208-209](app/modules/pipeline/orchestrator.py#L208-L209)）。要更稳，可以把失败的 documentIds 重新入队或落死信表。

---

## Q3. 切题评估的 prompt 原文？有没有量化指标？

原文（[query_rewrite.py:26-33](app/modules/ai/query_rewrite.py#L26-L33)，逐字）：

```
你是企业知识库的检索评估器。判断命中资料能否回答用户问题。

- 切题：同一主题、同一制度/岗位/流程，能支撑作答（不必覆盖每个细节）
- 不切题：只是词沾边（如问加班餐补却命中差旅报销）、或完全另一件事
- 只根据给定标题和摘录判断，不要假设库里还有别的文档
- 输出不要解释字段以外的内容
```

输入 = 这段系统词 + 用户原问题 + 前 5 条命中（`标题 / 章节（分=x.xx）+ 180 字摘录`）；输出 = `GradeResult {relevant: bool, reason: str}`，temperature=0，function calling 结构化输出（[query_rewrite.py:201-221](app/modules/ai/query_rewrite.py#L201-L221)）。

**没有量化指标，是纯语义二值判断。** 输入里给模型看的分数（rerank 分）只是参考信息，prompt 里没有任何阈值逻辑。量化存在于流水线的其他位置：`rag_min_score=0.4` 是**单条**硬阈值，RRF 是排名融合——但"整组切不切题"这个决策没有数字。

**为什么不用分数**：
- 布尔直接驱动控制流（ok → 作答；不足 → 改写），不需要阈值校准；
- "词沾边但不切题"恰恰是分数失灵的场景：问加班餐补时，差旅报销块的 rerank 分可能高达 0.7+（词确实重叠），只有语义判断能识别"不是同一件事"；
- 分数阈值和 rerank 模型的分布绑定，换模型就得重调。

**若要量化的演进方向（建议）**：给 GradeResult 加 confidence 分档——confidence 高的直通省一次评估调用、低的直接改写，用真实检索日志校准分界点。收益是省调用，代价是引入需要维护的阈值。

---

## Q4. 最多 2 次重试，怎么防死循环 / 成本爆炸？

护栏有七层，从硬到软：

1. **检索次数硬上限**：工具闭包里 `state["retrieve_count"] >= 2` 直接拒绝执行，返回错误消息（[agent.py:177-183](app/modules/ai/agent.py#L177-L183)）。这是代码级约束，模型无视提示词也没用。
2. **改写器防呆**：`rewrite_query` 只在上次评估不 ok 时可用；已切题返回 error；新词与旧词（空白归一后）相同视为改写失败 → 回退用原问题；原问题也相同 → 返回空串，链路自然终止（[query_rewrite.py:158-164](app/modules/ai/query_rewrite.py#L158-L164)、224-251）。
3. **模型调用总数上限**：`ModelCallLimitMiddleware(run_limit=8, exit_behavior="end")`（[agent.py:282](app/modules/ai/agent.py#L282)）——就算模型陷入"调工具→看结果→再调工具"的怪圈，最多 8 个模型回合强制收尾出答案。
4. **上下文钳制**：`SummarizationMiddleware`（12 条触发摘要、保留 6 条，[agent.py:274-281](app/modules/ai/agent.py#L274-L281)），多轮重试不会让上下文线性膨胀，单次调用成本有界。
5. **客户端零重试 + 超时**：所有 `ChatOpenAI` 都 `max_retries=0` + 显式 timeout（分类/评估/改写用 `ai_query_rewrite_timeout_ms`，主模型 `ai_chat_timeout_ms`；rerank 60s）——库层自动重试不与循环重试叠加。
6. **出口短路**：`eval.ok` → 提示词"不要再检索、不要改写"；`reason=error` → 不触发改写，直接交模型处置。
7. **工具入参钳制**：pydantic `ge/le`（topK 1-10、web count 1-10，[agent.py:75-90](app/modules/ai/agent.py#L75-L90)）。

**成本上界因此可计算**：最坏情况一轮 `kb_then_web` ≈ 1 次意图分类 + 1 次 Mem0 检索 + 2×(1 embedding + 1 rerank API + 1 评估 LLM) + 1 次改写 + ≤8 次主模型调用 + 1 次记忆分类器 + Mem0 写入。每一项都有独立上限，乘积有界。

---

## Q5. RRF 参数怎么调？重排模型是本地还是 API？

**参数**（[config.py:57-61](app/core/config.py#L57-L61)）：`rag_hybrid_top_k=20`（每路召回条数 = rerank 候选池）、`rag_rrf_c=60`、`rag_min_score=0.4`。

**公式**（[vector_index.py:278-302](app/modules/pipeline/vector_index.py#L278-L302)）：`score(d) = Σ 1/(c + rank + 1)`，rank 从 0 起，两路各按原始得分排序后累加。

**`c` 的直觉**：控制"名次差距"的权重。c 越大，第 1 名和第 20 名的贡献差被压得越平，两路话语权越趋同；c 越小，头部名次越主导。60 来自 RRF 原论文（Cormack et al. 2009）的实验最优值，是业界事实默认——**一般不调它**。

**真正值得调的三个旋钮**：
- `hybrid_top_k`：召回池大小，权衡漏召回 vs rerank 费用与噪声；当前 20 进 5 出（4:1 池）；
- `rag_min_score`（0.4）：决定"宁可少答不硬答"的激进度，直接影响自测用例 11/12 的表现；
- embedding 模型本身（当前 DashScope text-embedding-v3，1024 维，批量 ≤10，[embedding.py](app/modules/pipeline/embedding.py)）——召回质量的地基，比任何融合参数影响都大。

**验证方式**：`tests/test_rrf.py` 单测锁融合逻辑；效果回归用 [agentic-rag.md](agentic-rag.md) 的用例集。

**重排是 API，不是本地**：DashScope text-rerank 端点，模型 `qwen3-rerank`，key 复用 `rerank_api_key` 或 DASHSCOPE/OPENAI key（[reranker.py:25-76](app/modules/ai/reranker.py#L25-L76)）；每条候选截 2000 字；未启用/调用失败/返回空都返回 `None`，上层降级直接用 RRF 序（[hybrid_retrieval.py:31-42](app/modules/ai/hybrid_retrieval.py#L31-L42)）。选 API 的理由：中文 rerank 开箱即用、免 GPU 运维；代价是时延 + 按条计费——这正是权限过滤放召回期的经济动机（不可见的文档不进 rerank）。

---

## Q6. 权限下推、多租户 / 文档级权限模型怎么设计？

**现状（代码事实）**：
- 权限模型 = 三元组 `author_id / team_id / is_public` + `status`（发布才进索引）+ 角色 bypass（admin/reviewer → `unrestricted`）；
- 判定式（[access.py:18-27](app/modules/documents/access.py#L18-L27)）：`unrestricted OR 作者本人 OR (已发布 AND (公开 OR 我团队))`；
- 下推位置：ES filter 上下文（BM25 `bool.filter` / kNN `knn.filter`，不打分、可缓存）+ Neo4j `WHERE`/`EXISTS` 子查询；
- 冗余 + 同步：三字段冗余进每个 chunk 和每个图节点，变更时 `update_visibility` 三路并行同步。

**多租户扩展（建议，当前代码无 tenant 概念）**——隔离强度三档：

| 档 | 做法 | 适用 |
|---|---|---|
| 字段过滤（推荐起步） | 所有索引加 `tenant_id` keyword，filter 首位 `term tenant_id`；Neo4j WHERE 加一条 | 绝大多数 SaaS；现有模式的直线扩展 |
| routing / 分片 | `tenant_id` 做 index routing，查询带 routing 缩小扫描 | 租户多、单租户数据大 |
| 索引/集群级 | 每租户独立 index（`kh_chunk_{tenant}`）或独立部署 | 大客户/合规硬隔离；警惕 shard 爆炸 |

**文档级 ACL（比 team 更细）**：
- 小 ACL（允许者 < 几百）：`allowed_user_ids / allowed_team_ids` 数组冗余进 chunk，`terms` 查询——本项目的模式直接延伸；
- 大 ACL：ES Search Applications 的 documents-as-queries（权限表反转成查询），或召回前先取该用户可见 `doc_ids` 做 terms 过滤——注意这个集合过滤依然发生在**召回期**，不是取回后；
- Neo4j 的天然优势：ACL 本身就是图——`(User)-[:CAN_READ]->(Document)` 一跳遍历即可，权限模型越复杂，图比 filter 表达式越可读。

**无论哪档的不变式**：
1. 权限判定必须在检索引擎内完成，绝不取回后过滤（否则不可见文档占 top_k 名额且无法回填）；
2. 关系库是真源，索引是冗余副本，必须有单向同步管线（沿用 `update_visibility` 模式）；
3. 同步失败时安全取向是**宁可漏答不可越权**（可见性未知的文档按不可见处理）；
4. bypass 用参数短路（`$unrestricted`），不要让 filter 子句越拼越长。

**改造点清单**：`Document` 加 `tenant_id` 列 → `access_from_user` 装配 `tenantId` → `es_visibility_filter` 在 should 外加独立 `term` 子句 → `neo4j_document_access_where` 加租户条件 → MQ 索引消息携带租户上下文。

---

## Q7. Mem0 和 PG 持久化记忆的职责边界？Redis 热窗口何时清理？

**三层记忆，按"给谁看、活多久"划分**：

| 层 | 存储 | 内容 | 生命周期 |
|---|---|---|---|
| 会话档案 | PG `kh_ai_session / kh_ai_message` | 全部对话原文 + 引用来源（每轮 `append_turn` 落库） | 删会话才删，审计/回放真源 |
| 热窗口 | Redis（`kh:chat:{userId}:{sessionId}`） | 最近 20 条 human/ai 原文（`chat_short_memory_max_messages`） | TTL 24h；删会话显式清 |
| 事实记忆 | Mem0 云 | 提炼过的**事实**：user 层（跨会话身份/岗位/偏好）+ session 层（本次任务/进度，`run_id=sessionId`） | user 层不自动清；session 层随会话删 |

**边界标准**：PG = 档案（完整、可审计、会话列表 API 的真源）；Redis = 当前会话的低时延工作集（miss 时从 PG 回填最近 20 条再写回，[chat_service.py:54-67](app/modules/ai/chat_service.py#L54-L67)）；Mem0 = 个性化事实。**内容不重叠**：Redis 存原文消息，Mem0 存抽取事实——同一句话进 Mem0 前要过 LLM 分类器（`MemoryDecision`，[long_memory.py:15-46](app/modules/ai/long_memory.py#L15-L46)），且显式排除知识库条文（"知识库内容永远不要写成 user 记忆"），多数轮次会被拒写。注入时也声明边界："以上仅作背景，制度/流程以本轮检索资料为准"。

**Redis 热窗口的清理路径（四种）**：
1. **TTL 自然过期**：`SET ... EX 86400`（`chat_short_memory_ttl_seconds`，[short_memory.py:67-75](app/modules/ai/short_memory.py#L67-L75)），每次写重置 24h——一天不聊自动消失；
2. **滚动窗口挤出**：save 时只保留最近 20 条（`[-max_messages:]`），更早的即时丢弃（PG 里仍有）；
3. **显式删除**：删除会话 API → `sessions.remove` → `short_memory.clear`（Redis）+ `long_memory.clear_session`（Mem0 session 层 `delete_all`）联动清理（[sessions.py:98-101](app/modules/ai/sessions.py#L98-L101)）；
4. **故障降级**：Redis 挂了不清理也不报错，读路径降级走 PG（`try_load` 返回 None 即 miss）。

**每轮的读取时序**：`load_working_history`（Redis → miss 回填 PG）作为 history 注入；Mem0 `search`（user + session 双层 filter）作为 system 注入；两者与意图识别并行执行（[stream_service.py:183-199](app/modules/ai/stream_service.py#L183-L199)）。

顺带：PG 镜像虽是 pgvector 版，但记忆检索没用 PG 向量——Mem0 云端托管了向量检索，PG 只承担关系型的会话档案。
