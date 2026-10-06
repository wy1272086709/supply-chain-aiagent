"""对话长期记忆（Mem0 云）— 对应 chat-long-memory.service.ts。未配置 MEM0_API_KEY 全部跳过。"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MemoryDecision(BaseModel):
    write_user: bool = Field(
        description="写入用户层：换会话仍应保留的身份、岗位、回答偏好、长期约束。不含本轮任务、不含知识库条文。"
    )
    write_session: bool = Field(description="写入会话层：仅当前会话的任务、进度、待办、临时约定。")
    reason: str = Field(description="分类理由，一句话")


CLASSIFIER_PROMPT = (
    "你是企业知识库助手的记忆分类器。判断本轮是否有「新事实」要写入 Mem0。\n"
    "\n"
    "## user 层（跨会话）\n"
    "- 用户身份、岗位、所在团队自称\n"
    "- 长期偏好：答短一点、只要本团队制度、技术回答带示例\n"
    "- 持久约束：过敏、语言、称呼\n"
    "\n"
    "## session 层（仅当前会话）\n"
    "- 正在排查的问题、本次要写的文档、已确认的下一步\n"
    "- 用户说「这次」「本轮」的工作上下文\n"
    "\n"
    "## 均不写入\n"
    "- 寒暄、致谢、纯确认\n"
    "- 助手根据知识库/检索资料说出的制度、流程、负责人、系统名（那是文档事实，不是用户记忆）\n"
    "- 联网搜索结果、引用编号 [n]\n"
    "- 无信息增量的复述\n"
    "\n"
    "## 原则\n"
    "1. 知识库内容永远不要写成 user 记忆\n"
    "2. 「这次先看差旅制度第三节」→ session，不要标成 user\n"
    "3. user 与 session 可同时为 true\n"
    "4. 一次性提问且未产生需跨轮记住的约定 → 均为 false"
)

_client = None
_classifier = None
_tried = False


def _top_k() -> int:
    return get_settings().mem0_top_k


def enabled() -> bool:
    return bool(get_settings().mem0_api_key)


def _get_client():
    global _client
    if _client is not None:
        return _client
    cfg = get_settings()
    if not cfg.mem0_api_key:
        logger.warning("未配置 MEM0_API_KEY，跳过长期记忆")
        return None
    from mem0 import MemoryClient

    kwargs = {"api_key": cfg.mem0_api_key}
    if cfg.mem0_host:
        kwargs["host"] = cfg.mem0_host
    _client = MemoryClient(**kwargs)
    return _client


def _get_classifier():
    global _classifier, _tried
    if _tried:
        return _classifier
    _tried = True
    cfg = get_settings()
    client = _get_client()
    if not cfg.llm_api_key_resolved or client is None:
        return None

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        api_key=cfg.llm_api_key_resolved,
        model=cfg.llm_model_resolved,
        temperature=0,
        timeout=cfg.ai_chat_timeout_ms,
        max_retries=0,
        base_url=cfg.llm_base_url_resolved,
    )
    _classifier = llm.with_structured_output(MemoryDecision, method="function_calling")
    return _classifier


async def search(user_id: str, session_id: str | None, query: str) -> dict:
    empty = {"user": [], "session": []}
    client = _get_client()
    if client is None:
        return empty
    try:
        user_res = client.search(query, filters={"user_id": user_id}, top_k=_top_k())
        session_res = (
            client.search(
                query,
                filters={"AND": [{"user_id": user_id}, {"run_id": session_id}]},
                top_k=_top_k(),
            )
            if session_id
            else {"results": []}
        )
        return {
            "user": [m.get("memory") for m in (user_res.get("results") or []) if m.get("memory")],
            "session": [m.get("memory") for m in (session_res.get("results") or []) if m.get("memory")],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Mem0 检索失败：%s", e)
        return empty


def build_system_message(hits: dict) -> str | None:
    blocks: list[str] = []
    if hits.get("user"):
        blocks.append("【用户长期记忆】\n" + "\n".join(f"- {line}" for line in hits["user"]))
    if hits.get("session"):
        blocks.append("【当前会话记忆】\n" + "\n".join(f"- {line}" for line in hits["session"]))
    if not blocks:
        return None
    return "\n\n".join(blocks) + "\n\n以上仅作背景，制度/流程以本轮检索资料为准，不要用记忆替代文档。"


async def remember_turn(user_id: str, session_id: str, question: str, answer: str) -> None:
    client = _get_client()
    classifier = _get_classifier()
    if client is None or classifier is None:
        return
    try:
        decision = await classifier.ainvoke(
            [
                SystemMessage(CLASSIFIER_PROMPT),
                HumanMessage(f"用户：{question}\n助手（仅供判断，不要当作用户事实）：{answer[:300]}"),
            ]
        )

        add_opts = {
            "custom_instructions": (
                "只用用户说的话抽取记忆，一句完整中文。"
                "只保存身份、岗位、偏好、约束，或用户声明的本轮任务。"
                "不要保存制度条文、流程、时限、负责人、系统名、引用编号。"
                "不要译成英文。"
            )
        }
        written: list[str] = []
        if decision.write_user:
            client.add([{"role": "user", "content": question}], user_id=user_id, **add_opts)
            written.append("user")
        if decision.write_session:
            client.add(
                [{"role": "user", "content": question}],
                user_id=user_id,
                run_id=session_id,
                **add_opts,
            )
            written.append("session")
        logger.info("Mem0 分类：%s；写入=%s", decision.reason, ",".join(written) or "无")
    except Exception as e:  # noqa: BLE001
        logger.warning("Mem0 写入失败：%s", e)


async def clear_session(user_id: str, session_id: str) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.delete_all(user_id=user_id, run_id=session_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Mem0 会话层清理失败：%s", e)
