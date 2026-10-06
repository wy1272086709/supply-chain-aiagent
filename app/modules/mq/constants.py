"""MQ 拓扑常量 — 逐字对应 src/mq/mq.constants.ts。"""

# 交换机（topic、durable）
RAG_REINDEX_EXCHANGE = "rag.reindex.exchange"
SEARCH_INDEX_EXCHANGE = "search.index.exchange"
KG_GRAPH_EXCHANGE = "kg.graph.exchange"

# 队列（durable，kh. 前缀）
RAG_REINDEX_QUEUE = "kh.rag.reindex.queue"
SEARCH_INDEX_QUEUE = "kh.search.index.queue"
KG_GRAPH_QUEUE = "kh.kg.graph.queue"

# 路由键
RAG_REINDEX_BY_IDS_RK = "rag.reindex.by_ids"
RAG_REINDEX_DELETE_RK = "rag.reindex.delete"
SEARCH_INDEX_DOCUMENT_RK = "search.index.document"
SEARCH_INDEX_DELETE_RK = "search.index.delete"
KG_GRAPH_BUILD_BY_IDS_RK = "kg.graph.build.by_ids"
KG_GRAPH_DELETE_RK = "kg.graph.delete"

# 绑定：每个队列绑定到对应交换机的两个 RK
BINDINGS = {
    RAG_REINDEX_QUEUE: (RAG_REINDEX_EXCHANGE, [RAG_REINDEX_BY_IDS_RK, RAG_REINDEX_DELETE_RK]),
    SEARCH_INDEX_QUEUE: (SEARCH_INDEX_EXCHANGE, [SEARCH_INDEX_DOCUMENT_RK, SEARCH_INDEX_DELETE_RK]),
    KG_GRAPH_QUEUE: (KG_GRAPH_EXCHANGE, [KG_GRAPH_BUILD_BY_IDS_RK, KG_GRAPH_DELETE_RK]),
}
