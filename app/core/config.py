"""全局配置 — 读取与原 NestJS 版完全相同的环境变量名（.env.example 未变）。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 基础 ---
    port: int = 3000

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "user"
    postgres_password: str = "123456"
    postgres_db: str = "knowledge_hub"

    # --- MongoDB ---
    mongo_uri: str = "mongodb://mongo_user:mongo_pass123@localhost:27017/knowledge_hub?authSource=admin"

    # --- Snowflake ---
    snowflake_worker_id: int = 1
    snowflake_offset: int = 1704067200000

    # --- RustFS (S3) ---
    rustfs_enabled: bool = True
    rustfs_endpoint: str = "http://localhost:9000"
    rustfs_public_url: str = ""
    rustfs_access_key: str = "rustfsadmin"
    rustfs_secret_key: str = "rustfsadmin"
    rustfs_bucket: str = "knowledge-hub"
    rustfs_region: str = "us-east-1"

    # --- RabbitMQ ---
    rabbitmq_enabled: bool = True
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672"
    rabbitmq_connect_timeout_ms: int = 15000

    # --- RAG 分块（token 单位，×2 转字符） ---
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64

    # --- Embedding ---
    embedding_dimension: int = 1024
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v3"
    embedding_batch_size: int = 10
    embedding_api_key: str = ""
    dashscope_api_key: str = ""

    # --- 混合检索 ---
    rag_hybrid_top_k: int = 20
    rag_rrf_c: int = 60
    rag_rerank_enabled: bool = True
    rag_rerank_model: str = "qwen3-rerank"
    rag_min_score: float = 0.4
    rerank_api_key: str = ""
    rerank_base_url: str = "https://dashscope.aliyuncs.com"

    # --- LLM（DashScope 兼容） ---
    openai_api_key: str = ""
    openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_name: str = "qwen-plus"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_enable_thinking: str = ""
    ai_chat_timeout_ms: int = 60000
    ai_query_rewrite_timeout_ms: int = 15000
    kg_llm_timeout_ms: int = 120000
    kg_max_entities: int = 12
    kg_max_relations: int = 15

    # --- 联网搜索 ---
    bocha_api_key: str = ""

    # --- Elasticsearch ---
    elasticsearch_enabled: bool = True
    elasticsearch_node: str = "http://localhost:9200"

    # --- Neo4j ---
    neo4j_enabled: bool = True
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "12345678"

    # --- JWT ---
    jwt_secret: str = "dev-secret-change-me"
    jwt_access_expires: str = "2h"
    jwt_refresh_expires: str = "7d"

    # --- 邮箱 ---
    require_email_verification: str = "false"
    app_public_url: str = "http://localhost:3000"
    mail_host: str = ""
    mail_port: int = 587
    mail_secure: str = "false"
    mail_user: str = ""
    mail_pass: str = ""
    mail_from: str = ""

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # --- 对话短期记忆 ---
    chat_short_memory_ttl_seconds: int = 86400
    chat_short_memory_max_messages: int = 20
    chat_short_memory_key_prefix: str = "kh:chat"

    # --- 长期记忆 Mem0 ---
    mem0_api_key: str = ""
    mem0_host: str = ""
    mem0_top_k: int = 5

    # --- LangSmith ---
    langsmith_tracing: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "knowledge-hub"

    # --- 文档审核 ---
    document_require_approval: str = "true"

    # ---------------- OR 回退链（对应 TS 中 `a || b || c`） ----------------

    @property
    def llm_api_key_resolved(self) -> str:
        return self.openai_api_key or self.llm_api_key or self.dashscope_api_key

    @property
    def llm_base_url_resolved(self) -> str:
        return self.openai_base_url or self.llm_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def llm_model_resolved(self) -> str:
        return self.model_name or self.llm_model or "qwen-plus"

    @property
    def embedding_api_key_resolved(self) -> str:
        return self.embedding_api_key or self.dashscope_api_key or self.openai_api_key

    @property
    def rerank_api_key_resolved(self) -> str:
        return self.rerank_api_key or self.dashscope_api_key or self.openai_api_key

    @property
    def rerank_base_url_resolved(self) -> str:
        return (self.rerank_base_url or "https://dashscope.aliyuncs.com").rstrip("/")

    @property
    def mem0_host_resolved(self) -> str:
        return self.mem0_host or "https://api.mem0.ai"

    @property
    def rustfs_public_url_resolved(self) -> str:
        return (self.rustfs_public_url or self.rustfs_endpoint).rstrip("/")

    # ---------------- 布尔语义（对应 TS 的 `!== 'false'` / `=== 'true'`） ----------------

    @property
    def thinking_enabled(self) -> bool:
        return self.llm_enable_thinking != "false"

    @property
    def email_verification_required(self) -> bool:
        return self.require_email_verification == "true"

    @property
    def approval_required(self) -> bool:
        return self.document_require_approval != "false"


@lru_cache
def get_settings() -> Settings:
    return Settings()
