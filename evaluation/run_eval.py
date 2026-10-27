"""Step 2：批量采集——对测试集逐题调 /rag/search + /ai/chat，落盘原始结果。

用法：
    cd evaluation
    BASE_URL=http://localhost:3000 python run_eval.py                        # 全量（golden_set.jsonl，48 题）
    BASE_URL=... python run_eval.py --golden golden_set_v2.jsonl            # 含 L6 多轮题（56 行）
    BASE_URL=... python run_eval.py --only CS-L1-01,ST-L3-01                # 只跑指定题
    BASE_URL=... python run_eval.py --skip-chat                             # 只测检索（无 LLM Key 时）
    BASE_URL=... python run_eval.py --topk 5
    BASE_URL=... python run_eval.py --repeat 3                              # 每题独立会话问 3 次（稳定性指标）

产物：
    results/raw_results.jsonl   每题一行（多轮题每个追问单独一行）

采集字段（v2，为分侧评测服务）：
    - hits 含块内容（截 800 字）→ 上下文精确率 / 信息点召回 / 忠实度评审的输入
    - answers / chat_latencies：--repeat 次独立会话的全部回答与耗时（answers[0] 同步写旧字段 answer）
    - L6 多轮：followups 逐轮追加行（id 带 #2/#3，turn>=2，同一 sessionId）；
      追问行 hits 为空——追问的真实检索发生在 /ai/chat 链路内部（改写后），API 不暴露，只做端到端与引用判定

指标计算与评审在 judge_and_report.py。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
BASE_URL = os.environ.get("BASE_URL", "http://localhost:3000")
USERNAME = os.environ.get("EVAL_USERNAME", "user")
PASSWORD = os.environ.get("EVAL_PASSWORD", "123456")


def load_golden(path: Path) -> list[dict]:
    items = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def title_matches(hit_title: str, golden_title: str | None) -> bool:
    """文档标题匹配：命中判据取标题前 6 个字符（如「理赔SOP操」「运费合同结」「站点操作手」），
    避免全角括号或标点差异造成漏判。"""
    if not golden_title:
        return False
    key = golden_title[:6]
    return key in (hit_title or "")


def login(client: httpx.Client) -> str:
    resp = client.post(f"{BASE_URL}/auth/login", json={"username": USERNAME, "password": PASSWORD}, timeout=30)
    resp.raise_for_status()
    return resp.json()["accessToken"]


def rag_search(client: httpx.Client, token: str, query: str, topk: int) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    resp = client.post(
        f"{BASE_URL}/rag/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "topK": topk},
        timeout=60,
    )
    cost = time.perf_counter() - t0
    resp.raise_for_status()
    hits = resp.json() or []
    # v2：保留块内容（截 800 字），供上下文精确率 / 信息点召回 / 忠实度评审
    slim = [
        {
            "documentTitle": h.get("documentTitle"),
            "heading": h.get("heading"),
            "score": h.get("score"),
            "content": (h.get("content") or "")[:800],
        }
        for h in hits
    ]
    return slim, cost


def chat_once(client: httpx.Client, token: str, content: str, topk: int, session_id: str | None = None) -> tuple[dict, float]:
    t0 = time.perf_counter()
    payload: dict = {"content": content, "topK": topk}
    if session_id:
        payload["sessionId"] = session_id
    resp = client.post(
        f"{BASE_URL}/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=180,
    )
    cost = time.perf_counter() - t0
    resp.raise_for_status()
    body = resp.json()
    sources = [
        {"documentTitle": s.get("documentTitle"), "heading": s.get("heading")} for s in body.get("sources") or []
    ]
    return {"answer": body.get("answer") or "", "sources": sources, "sessionId": body.get("sessionId")}, cost


def base_row(item: dict) -> dict:
    return {
        "id": item["id"],
        "role": item["role"],
        "category": item["category"],
        "question": item["question"],
        "golden_answer": item["golden_answer"],
        "answer_keywords": item.get("answer_keywords") or [],
        "source_doc": item.get("source_doc"),
        "baseline_seconds": item.get("baseline_seconds"),
        "hits": [],
        "golden_rank": None,
        "answers": [],
        "answer": None,
        "cited_titles": [],
        "chat_latencies": [],
        "chat_latency": None,
        "search_latency": None,
        "error": None,
        "turn": 1,
    }


def collect(item: dict, client: httpx.Client, token: str, topk: int, repeat: int, skip_chat: bool) -> list[dict]:
    """一道金标题 → 一或多行结果。多轮题展开为 turn1..turnN；单轮题按 repeat 次独立会话采集。"""
    head = base_row(item)

    # 检索只跑一次（同 query 结果确定）；多轮追问的检索在 /ai/chat 内部，不在此采集
    hits, s_cost = rag_search(client, token, item["question"], topk)
    head["hits"] = hits
    head["search_latency"] = round(s_cost, 3)
    for rank, h in enumerate(hits, 1):
        if title_matches(h.get("documentTitle"), item.get("source_doc")):
            head["golden_rank"] = rank
            break

    if skip_chat:
        return [head]

    if item.get("followups"):
        # 多轮链：整链只跑一遍（不参与 repeat），turn1 开新会话，追问续用同一 sessionId
        rows: list[dict] = []
        sid: str | None = None
        cur = head
        for turn_idx, turn in enumerate([item, *item["followups"]]):
            if turn_idx > 0:
                cur = base_row({
                    **item,
                    "id": f"{item['id']}#{turn_idx + 1}",
                    "question": turn["question"],
                    "golden_answer": turn["golden_answer"],
                    "answer_keywords": turn.get("answer_keywords") or [],
                    "source_doc": turn.get("source_doc") or item.get("source_doc"),
                })
                cur["turn"] = turn_idx + 1
                cur["parent_id"] = item["id"]
                cur["search_latency"] = None  # 追问不做独立检索
            chat, c_cost = chat_once(client, token, cur["question"], topk, sid)
            sid = chat["sessionId"] or sid
            cur["answers"] = [chat["answer"]]
            cur["answer"] = chat["answer"]
            cur["cited_titles"] = [s["documentTitle"] for s in chat["sources"]]
            cur["chat_latencies"] = [round(c_cost, 3)]
            cur["chat_latency"] = round(c_cost, 3)
            rows.append(cur)
            time.sleep(0.2)
        return rows

    # 单轮题：repeat 次独立会话（无 sessionId，每次全新对话，输入完全一致）
    for _ in range(repeat):
        chat, c_cost = chat_once(client, token, item["question"], topk)
        head["answers"].append(chat["answer"])
        head["chat_latencies"].append(round(c_cost, 3))
        if not head["cited_titles"]:
            head["cited_titles"] = [s["documentTitle"] for s in chat["sources"]]
        time.sleep(0.2)
    head["answer"] = head["answers"][0]
    head["chat_latency"] = head["chat_latencies"][0]
    return [head]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(HERE / "golden_set.jsonl"))
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--only", default="", help="逗号分隔的题目 id，只跑这些题")
    ap.add_argument("--repeat", type=int, default=1, help="每题独立会话重复次数（稳定性指标用）")
    ap.add_argument("--skip-chat", action="store_true", help="只测检索，不调 /ai/chat（省 LLM 费用/无 Key）")
    args = ap.parse_args()

    golden = load_golden(Path(args.golden))
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        golden = [g for g in golden if g["id"] in wanted]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "raw_results.jsonl"

    with httpx.Client() as client, out_path.open("w", encoding="utf-8") as fout:
        token = login(client)
        print(f"登录成功，共 {len(golden)} 题（repeat={args.repeat}），输出 → {out_path}")

        for i, item in enumerate(golden, 1):
            rows: list[dict] = []
            try:
                try:
                    rows = collect(item, client, token, args.topk, args.repeat, args.skip_chat)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 401:
                        raise
                    token = login(client)  # token 过期，重登后重试一次
                    rows = collect(item, client, token, args.topk, args.repeat, args.skip_chat)
            except Exception as e:  # noqa: BLE001 单题失败不中断整批
                rows = [base_row(item)]
                rows[0]["error"] = f"{type(e).__name__}: {e}"
            for row in rows:
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            fout.flush()
            for row in rows:
                rank = f"rank={row['golden_rank']}" if row["golden_rank"] else "rank=-"
                rep = f" ×{len(row['answers'])}" if len(row["answers"]) > 1 else ""
                err = f" ERR={row['error']}" if row["error"] else ""
                print(f"[{i:>2}/{len(golden)}] {row['id']} {rank} chat={row['chat_latency']}s{rep}{err}")
            time.sleep(0.3)

    print(f"\n采集完成 → {out_path}\n下一步：python judge_and_report.py")


if __name__ == "__main__":
    main()
