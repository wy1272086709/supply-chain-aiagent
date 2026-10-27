# 模拟面试题：Agentic RAG 知识库项目

> 生成于 2026-10-16。完全按简历那四条出的题，面试官视角的钻法：**主问 → 追问链 → 将军题**。
>
> 配合使用：
> - 答题弹药见 [INTERVIEW-PREP.md](INTERVIEW-PREP.md) 第二部分 A 节（题号基本对得上）
> - 代码定位见 [READING-GUIDE.md](READING-GUIDE.md)
>
> 用法建议：先遮住"考点"自测口答，再对照两个文档补漏。标 ⚠️ 的是最容易把人问住的题。

---

## 一、开场 & 项目整体（对应简历第 1 条：多源异构文档 Agentic RAG）

### Q1. 用 3 分钟介绍这个项目：业务背景是什么、解决谁的什么问题？
- 追问：物流理赔 SOP、运费合同、站点手册 —— 这三类文档"异构"具体异构在哪？解析难度差在哪？
- 追问：**"可审计、可溯源、低幻觉"这三个词，分别落地成了什么具体的产品/技术形态？**
  - ⚠️ 很多人这里只会复述名词。要有实指：溯源 = 句末 [n] + 落库前正则回收引用；可审计 = SSE 推 eval 帧（切题性二分类+理由）+ 推理过程全程可见；低幻觉 = 检索不足明确说"知识库没有相关内容"。

**考点**：讲故事的能力。讲"为什么做"比"做了什么"重要。

### Q2. 为什么选 Agentic RAG，而不是最朴素的一次性 retrieve-then-generate？
- 追问：Agentic 带来的收益和代价分别是什么？
- 追问：如果 80% 的问题一轮检索就能答，Agent 循环是不是过度设计？

**考点**：技术选型的判断力。诚实答法：项目里保留了双实现（chat_service.py 手写等价链 vs agent.py Agent 式），简单链更可控，需要模型自主决定工具序列/并行调用时才上 Agent。

### Q3. ⚠️ 这系统上线了吗？多少文档、多少用户、QPS 多少？
- 追问：没有的话，你怎么证明它"能用"？

**考点**：将军题。诚实框架：POC 阶段，但按生产标准做（异步管线隔离失败、每环可降级、SSE 帧有单测锁字节、权限下推存储层）；转生产缺的是评测集和灰度。**别编数据。** 对应 INTERVIEW-PREP A1-2 / A6。

---

## 二、Agent 循环 & SSE（对应简历第 2 条）

### Q4. 白板口述整个 Agent 工具循环的流转：从用户提问到吐出第一个 token，中间发生了什么？
- 追问：意图路由分几类？怎么实现的（规则还是 LLM）？**路由错了怎么办？**
- 追问："LLM 切题评估"的输入输出是什么？为什么切题判断用 LLM 而不是用相似度阈值？

**考点**：全局架构感。5 类意图（chitchat/profile/kb/web/kb_then_web），few-shot 放易混反例，失败兜底按 kb 处理（宁可多检索）。见 [router.py](app/modules/ai/router.py)、[agent.py](app/modules/ai/agent.py)。

### Q5. 查询改写为什么最多两轮？
- 追问：**防死循环具体在哪几层兜底？** 如果模型不听话，第三轮还是调检索工具，会发生什么？
- 追问：改写后的 query 和原 query 一样怎么办？

**考点**：三层防线，这是高频题：
1. 提示词写规则（最多检索两次）—— 不可靠
2. **工具体内硬计数**（`retrieve_count >= 2` 直接返回 insufficient）—— 不信任模型自觉
3. 全局 `ModelCallLimitMiddleware(run_limit=8)` 兜底

去重靠 `_distinct_query`：新 query = 旧 query 则回退原问题。见 [query_rewrite.py](app/modules/ai/query_rewrite.py)。

### Q6. SSE 流式输出：推理过程、正文、引用是怎么分帧推给前端的？
- 追问：文本、思考、工具参数三路增量交错到达，顺序怎么保证不乱？
- 追问：**答案里的引用 [n] 怎么保证和实际检索命中的文档一致？会不会出现标了 [2] 但落库的 sources 里没有 2？**
- 追问（将军）：SSE 断线重连做了吗？Last-Event-ID 了解吗？

**考点**：
- 协议层逐字节复刻 Vercel AI SDK 的 UI Message Stream（含 `separators=(",",":")`），[test_ui_stream.py](tests/test_ui_stream.py) 锁帧格式 —— 迁移前后前端零改动
- `_TurnTracker` 状态机：工具消息到达 = 模型回合结束信号，此时收口 text/reasoning 再发 tool output
- 引用强一致：检索命中分配全局编号 → 生成要求句末标 [n] → **落库前正则回收答案中实际出现的编号**，只持久化被引用的 sources
- 断线重连没做就坦诚，说"前端重发 + Last-Event-ID 服务端缓冲是已知演进项"。见 [ui_stream.py](app/modules/ai/ui_stream.py)、[stream_service.py](app/modules/ai/stream_service.py)。

---

## 三、混合检索（对应简历第 3 条，技术面主战场）

### Q7. BM25 和向量检索各自解决什么问题？
- 追问 ⚠️：**举两个具体例子 —— 一个只有 BM25 能命中、向量会漏的 query，和一个反过来的。**

**考点**：答不出具体例子 = 没真正做过。
- BM25 强：型号、术语、编号类精确词（"理赔编号 RX-2024 是什么格式"、"破损率超过 3% 怎么算"）
- 向量强：换个说法也能命中（"货摔坏了怎么赔" ↔ "货物破损理赔流程"）
见 [hybrid_retrieval.py](app/modules/ai/hybrid_retrieval.py)。

### Q8. IK 分词：ik_max_word 和 ik_smart 的区别？建索引和搜索时分别用哪个，为什么？
- 追问：标题加权具体怎么实现的？boost 为什么设 3？怎么验证加权有效？

**考点**：max_word 细粒度、smart 粗粒度，讲清权衡即可；标题加权用 multi_match 多字段 + `title^3` boost，验证方式：同一 query 有无 boost 的命中排序对比。对应 INTERVIEW-PREP A2。

### Q9. 手写 RRF 融合函数（伪代码即可）。
- 追问：为什么用 RRF 而不是分数归一化加权？k=60 是什么？
- 追问：RRF 丢掉了什么信息？拿什么补回来的？

**考点**：手写热身题（INTERVIEW-PREP 第四部分明确要求练）。BM25 无上界、cosine 有界，尺度不可比；RRF 只用排名 `score = Σ 1/(k+rank)`，k=60 是论文经验值，抑制头部排名霸权。代价：丢掉分数信息 → 用 cross-encoder 重排补精度。见 [vector_index.py](app/modules/pipeline/vector_index.py)。

### Q10. Rerank 用的什么模型？bi-encoder 和 cross-encoder 的区别？
- 追问：**为什么 rerank 只对融合后的 top 候选做，而不是全量？**
- 追问："Rerank 异常自动降级" —— 怎么判定"异常"？降级后用什么序？效果损失多少？
- 追问：阈值过滤的阈值怎么定的，拍脑袋还是有依据？

**考点**：
- DashScope text-rerank（cross-encoder，精度高但慢、按条计费）→ 所以只精排融合后 top 候选
- 异常判定：超时/异常/空结果 → 自动降级用 RRF 序。金句：**"检索链路可降级而非可失败"**。见 [reranker.py](app/modules/ai/reranker.py)。

### Q11.（排查题）用户反馈"问运费合同的事，答出来的是理赔 SOP"，你怎么排查？
- 追问：怎么区分是召回层的问题还是生成层的问题？

**考点**：分层定位：召回层（看 BM25/kNN 各自命中没有）→ 融合层（RRF 序是否合理）→ 精排层（rerank 是否翻车）→ 评估层（grader 误判）。这正是 JD 里"RAG 召回优化"经验。对应 INTERVIEW-PREP A2 最后一问。

---

## 四、权限体系（对应简历第 4 条）

### Q12. RBAC 一共几张表？关系画一下。为什么不是简单的 user→role→permission 三表？
- 追问：团队空间（team space）在模型里怎么表达的？文档归属怎么定？

**考点**：先对齐口径再答——面试官嘴里的"5 表 RBAC"是经典口径（user / role / permission 三张实体表 + user_role / role_permission 两张关联表）；**本项目实际是 6 表**：经典 5 表 + kh_user_permission（用户直授表，绕过角色直接给人开权限）。团队空间没有独立的"空间"表，由 kh_team + kh_team_member + kh_document.team_id 组合表达。两层隔离：RBAC 管"能用哪些功能"（横向），团队空间管"功能里能看到哪些数据"（纵向）。见 [access.py](app/modules/documents/access.py)。

**开场话术**："经典 RBAC 是 5 表，我们项目落地时是 6 表——多了张 kh_user_permission 用户直授表，用来给个别人开小灶权限而不用单建角色。"（把口径差异变成加分点，而不是被面试官纠正）

**参考答案**：

① 表关系（本项目实际 6 张，[user.py](app/db/models/user.py)）：

```
kh_user ──< kh_user_role >── kh_role ──< kh_role_permission >── kh_permission
 实体表        关联表         实体表          关联表               实体表
    │                                                            ▲
    └──────────────< kh_user_permission >────────────────────────┘
                     用户直授表（绕过角色的第 6 张）
```

- 登录时合并权限：`最终权限 = 直授(kh_user_permission) ∪ 经角色(user_role→role_permission)`，admin 再追加运营权限集，见 [permission_service.py:250](app/modules/users/permission_service.py#L250)
- **为什么不是三表**：三表意味着"一人一角色（user 表挂 role_id）、权限全挂角色"。表达不了：一人身兼数职（既是审核员又是法务）、给个别人临时开权限又不想单建角色、角色/权限要能独立软删和启停（status/deleted）而关联表只存 id 对

② 团队空间的表达（无独立 space 表，三处组合）：

- `kh_team`（parent_id 支持组织树）+ `kh_team_member`（谁在空间里）
- `kh_document.team_id`：文档归属是**单归属**——一个文档最多挂一个团队，另有 `author_id`（作者）和 `is_public`（公开位）
- 登录时算好 `teamIds = 我是成员的团队 ∪ 我当负责人的团队`（[teams/service.py:17](app/modules/teams/service.py#L17)）。注意：**不沿 parent_id 层级继承**，父团队的成员看不到子团队的空间
- JWT 里只存 user_id，roles/permissions/teamIds **每个请求从库上重建**（[deps.py:43](app/modules/auth/deps.py#L43)）→ 移出团队、改权限即时生效，不用等 token 过期（呼应 Q14）

③ 文档可见性（读），按 [access.py:18](app/modules/documents/access.py#L18) 顺序短路：

1. admin / **reviewer** → 全部可见（不只是管理员，审核员也 unrestricted）
2. 我是作者 → 可见（含草稿）
3. **非 PUBLISHED → 到此为止**：草稿/审核中只有上面两种人能看
4. is_public → 所有登录用户可见
5. doc.team_id ∈ 我的 teamIds → 团队空间内可见（也要先过第 3 条：没发布的团队文档别人照样看不了）

写权限更严：只有 admin 或作者本人（[access.py:30](app/modules/documents/access.py#L30)），团队负责人也改不了成员的文档。

④ 两层隔离怎么组合（本题满分点）：

- **岗位隔离（RBAC，横向）**：管"能按哪些按钮/调哪些接口"——接口层 `require_permissions('search:kb')` 挡人（[deps.py:64](app/modules/auth/deps.py#L64)）
- **空间隔离（team，纵向）**：管"按钮后面列出哪些数据"——两个同为 user 角色的人，分属不同团队，搜出来的文档集不同
- 同一套可见性规则三处生效：MySQL where / ES filter / Neo4j where，`teamIds` 就是那时下推的过滤参数（展开见 Q13）

面试一句话：**"角色决定我能按哪个按钮，团队决定按钮后面列出哪些文档。"**

### Q13. ⚠️ **为什么权限过滤要下推到 ES / 向量库层，而不是检索完在应用层过滤？**
- 追问：先过滤后打分 vs 先打分后过滤，除了安全还有什么区别？（提示：rerank 分数污染、top-k 被无权限文档占坑）
- 追问：ES 的 filter 上下文和 query 上下文区别？为什么权限用 filter？
- 追问（深挖将军）：**kNN 的 filter 是在 HNSW 图搜索之前还是之后生效的？对召回率有什么影响？**

**考点**：这道答好直接证明真做过。
- 同一套可见性规则三处生效：ES `bool.should + minimum_should_match:1` 放 **filter 上下文**（不打分、可缓存）、kNN 用 `knn.filter`、Neo4j 参数化 where
- **先过滤后打分**：无权限文档根本不进候选集 → 不会占 top-k 坑、不会污染 rerank 分数、避免上下文泄露
- ES kNN 的 filter 是 pre-filter（HNSW 搜索时就带上），所以权限集越小候选越少，极端情况召回会降 —— 这正是"精度/召回 vs 安全"的真实权衡点。见 [access.py](app/modules/documents/access.py)。

### Q14. 一个用户被移出团队后，他还能搜到那个空间的文档吗？权限变更是实时生效还是要重建索引？

**考点**：权限不冗余存进索引文档里的话，检索时实时 join 可见性 → 变更即时生效，无需重建。答得出"为什么不用重建"说明理解了过滤下推的原理。

---

## 五、交叉 & 将军题（跨四条）

### Q15. 整条链路里，哪一环挂了整个问答还能工作？哪一环挂了必须报错？
**考点**："可降级"的真实边界。可降级：rerank（退 RRF 序）、意图识别（按 kb）、图谱（纯 RAG 兜着）；不可降级：ES/向量库本体挂了、LLM 网关挂了 → 明确报错。

### Q16. Token 成本怎么控的？一次问答大概花多少 token，都花在哪？
**考点**：历史压缩（SummarizationMiddleware，12 条压到 6 条）、检索上下文截断（进模型 800 字符/块）、按意图挂工具减少无效调用、小任务用小模型。对应 INTERVIEW-PREP C 节。

### Q17. 换一个 embedding 模型，要做哪些事？为什么？
**考点**：维度和语义空间都变了 → 全量重建索引。顺势讲选型：为什么用 ES 而不是独立 Milvus（复用一套存储、运维简单，代价是 ES 向量能力不是最强）。

### Q18. ⚠️（终极将军）给这套系统建评测集，你会怎么设计？指标选什么？
**考点**：这道答好了，Q3 的"没上线"软肋直接对冲掉：
1. 离线：从文档造 QA 对，检索测 recall@k / MRR / nDCG；生成质量测 faithfulness / answer-relevancy（Ragas 思路）
2. 在线：引用点击率、"知识库没有相关内容"触发率、eval 帧切题性信号
对应 INTERVIEW-PREP A6，务必背熟。

---

## 危险题速查（优先准备）

| 题号 | 一句话考点 |
|---|---|
| Q3 | 没上线怎么证明能用 —— 诚实框架 + 评测方案 |
| Q6 追问 3 | SSE 断线重连 —— 没做就坦诚 |
| Q7 追问 | BM25/向量互补 —— 必须举得出具体 query 例子 |
| Q13 追问 3 | kNN filter 与 HNSW 的关系 —— 真做过的试金石 |
| Q18 | 评测集设计 —— 对冲"无量化指标"软肋 |

## 建议练法

1. 第一遍：遮住所有"考点"和追问，只看主问，口答录音，听回放找卡壳点
2. 第二遍：找人对读追问链，或让 AI 扮面试官逐题追问
3. 手写热身：Q9 的 RRF 函数必须能在白板上写出来（INTERVIEW-PREP 第四部分要求）
