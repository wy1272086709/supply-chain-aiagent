"""Token 消耗实测 — 抽样问题经计量代理跑 /rag/search + /ai/chat，按调用角色归账。

前置：
    1) token_proxy.py 已在 :9090 运行
    2) 应用已用指向代理的环境变量重启（见 token_proxy.py 头部注释）

用法：
    cd evaluation
    BASE_URL=http://127.0.0.1:3000 uv run python measure_tokens.py               # 采集+归账
    uv run python measure_tokens.py --aggregate-only                             # 只按已有代理日志重算

产物：
    results/token_report.md   按题/按角色/按题型的 token 账单
    results/token_windows.json（归账明细）

归账方式（按调用序列模式，确定性）：
    每个问答以 generation 结尾——按 generation 切段，第 i 段对应第 i 个问答行（SAMPLE 顺序×轮次展开）；
    段内首个 router 之前的调用属于该题的检索窗口（emb+rerank 对），追问段直接以 router 开头。
    （曾用时间窗归账，边界处会把检索窗口的 rerank 错记到相邻问答窗口，已废弃。）

口径：
    - "检索窗口" = /rag/search 一次调用（embedding + rerank）
    - "问答窗口" = /ai/chat 一次端到端调用（router + 内部检索 embedding/rerank + grader/rewriter + generation）
    - 生产环境单题成本 = 问答窗口（用户只调 /ai/chat；检索窗口是评测对检索模块的单独测量）
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

import httpx

HERE = Path(__file__).parent
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")
USERNAME = os.environ.get("EVAL_USERNAME", "user")
PASSWORD = os.environ.get("EVAL_PASSWORD", "123456")
PROXY_LOG = HERE / "results" / "token_proxy_log.jsonl"

# 抽样：覆盖全部题型 + 摇摆题 + 多轮链（15 题 16 个问答行）
SAMPLE = [
    "CS-L1-01", "CS-L1-03", "CS-L2-01", "CS-L2-03", "CS-L3-01",
    "CS-L4-01", "CS-L5-01", "ST-L1-02", "ST-L2-01", "ST-L3-02",
    "OP-L1-02", "OP-L2-01", "OP-L3-01", "OP-L4-01", "CS-MT-01",
]

KINDS = ["router", "grader", "rewriter", "generation", "embedding", "rerank"]


def load_golden() -> dict[str, dict]:
    out = {}
    for line in (HERE / "golden_set_v2.jsonl").open(encoding="utf-8"):
        if line.strip():
            item = json.loads(line)
            out[item["id"]] = item
    return out


def collect(golden: dict[str, dict]) -> None:
    """采集：对每个抽样题跑 /rag/search + /ai/chat（多轮链逐轮续会话）。"""
    with httpx.Client(timeout=180) as client:
        token = client.post(
            f"{BASE_URL}/auth/login", json={"username": USERNAME, "password": PASSWORD}, timeout=30
        ).json()["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        for qid in SAMPLE:
            item = golden[qid]
            r = client.post(f"{BASE_URL}/rag/search", headers=headers,
                            json={"query": item["question"], "topK": 5}, timeout=60)
            r.raise_for_status()
            n_hits = len(r.json() or [])

            sid = None
            for i, turn in enumerate([item["question"], *(f["question"] for f in item.get("followups", []))]):
                payload = {"content": turn, "topK": 5}
                if sid:
                    payload["sessionId"] = sid
                resp = client.post(f"{BASE_URL}/ai/chat", headers=headers, json=payload, timeout=180)
                resp.raise_for_status()
                sid = resp.json().get("sessionId") or sid
            print(f"  {qid}: 检索{n_hits}块 + {i + 1} 轮问答完成")
            time.sleep(0.3)


def attribute_by_pattern(entries: list[dict], golden: dict[str, dict]):
    """按调用序列模式归账（见文件头说明）。返回 (attributed, chat_rows)。"""
    attributed: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "total": 0}))
    chat_rows: list[dict] = []

    plan = []  # 展开后的问答行：(row_id, category, is_turn1)
    for qid in SAMPLE:
        item = golden[qid]
        for i in range(1 + len(item.get("followups", []))):
            plan.append((qid if i == 0 else f"{qid}#{i + 1}", item["category"], i == 0))

    segments, cur = [], []
    for e in entries:
        if e["kind"] == "other":
            continue
        cur.append(e)
        if e["kind"] == "generation":
            segments.append(cur)
            cur = []
    assert not cur, f"尾部有 {len(cur)} 条未成段调用（问答未完成？）"
    assert len(segments) == len(plan), f"段数 {len(segments)} ≠ 计划问答行数 {len(plan)}"

    def add(label: str, e: dict) -> None:
        slot = attributed[label][e["kind"]]
        slot["calls"] += 1
        slot["in"] += e["prompt_tokens"]
        slot["out"] += e["completion_tokens"]
        slot["total"] += e["total_tokens"]

    for seg, (row_id, category, is_turn1) in zip(segments, plan):
        seen_router = False
        for e in seg:
            if e["kind"] == "router":
                seen_router = True
            add((f"{row_id.split('#')[0]}/search") if (is_turn1 and not seen_router) else f"{row_id}/chat", e)
        chat_rows.append({"id": row_id, "category": category})
    return attributed, chat_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aggregate-only", action="store_true", help="不采集，按已有代理日志重算")
    args = ap.parse_args()

    golden = load_golden()
    if not args.aggregate_only:
        collect(golden)

    entries = [json.loads(l) for l in PROXY_LOG.open(encoding="utf-8") if l.strip()]
    attributed, chat_rows = attribute_by_pattern(entries, golden)
    log_total = sum(e["total_tokens"] for e in entries if e["kind"] != "other")
    attr_total = sum(v["total"] for w in attributed.values() for v in w.values())
    assert attr_total == log_total, f"归账 {attr_total} ≠ 日志 {log_total}，有漏记"

    def window_total(label: str) -> int:
        return sum(v["total"] for v in attributed.get(label, {}).values())

    chat_totals = [window_total(f"{r['id']}/chat") for r in chat_rows]
    search_totals = [window_total(f"{g}/search") for g in SAMPLE]

    role_totals: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "total": 0})
    for r in chat_rows:
        for kind, v in attributed[f"{r['id']}/chat"].items():
            for k in ("calls", "in", "out", "total"):
                role_totals[kind][k] += v[k]

    lines = ["# Token 消耗实测（计量代理抽样）", ""]
    lines.append(f"- 抽样：{len(SAMPLE)} 题 / {len(chat_rows)} 个问答行；代理日志 {len(entries)} 条调用，归账核对 {attr_total}/{log_total} tokens 无漏记")
    lines.append("- 归账方式：按调用序列模式分段（generation 结尾切段），确定性无重复计数")
    lines.append("")
    lines.append("## 1. 按题账单（total tokens；「检索」列为 /rag/search 单独调用）")
    lines.append("")
    lines.append("| 题 | 题型 | router | grader | rewriter | generation(in/out) | embedding | rerank | 问答合计 | 检索单独 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in chat_rows:
        w = attributed[f"{r['id']}/chat"]
        gen = w.get("generation", {"in": 0, "out": 0})
        base_id = r["id"].split("#")[0]
        lines.append(
            f"| {r['id']} | {r['category']} | {w.get('router', {}).get('total', 0)} "
            f"| {w.get('grader', {}).get('total', 0)} | {w.get('rewriter', {}).get('total', 0)} "
            f"| {gen['in']}/{gen['out']} | {w.get('embedding', {}).get('total', 0)} "
            f"| {w.get('rerank', {}).get('total', 0)} | {window_total(f'{r['id']}/chat')} "
            f"| {window_total(f'{base_id}/search') if base_id in SAMPLE else '—'} |"
        )
    lines.append("")
    lines.append("## 2. 按角色汇总")
    lines.append("")
    lines.append("| 角色 | 调用次数 | 输入 | 输出 | 合计 | 均值/次 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for kind in KINDS:
        v = role_totals.get(kind)
        if v and v["calls"]:
            lines.append(f"| {kind} | {v['calls']} | {v['in']} | {v['out']} | {v['total']} | {v['total'] // v['calls']} |")
    lines.append("")
    lines.append("## 3. 均值")
    lines.append("")
    lines.append(f"- **问答窗口（生产口径）均值：{statistics.mean(chat_totals):.0f} tokens/题**（中位数 {statistics.median(chat_totals):.0f}，最大 {max(chat_totals)}）")
    lines.append(f"- 检索单独调用均值：{statistics.mean(search_totals):.0f} tokens（embedding+rerank）")
    by_cat: dict[str, list[int]] = defaultdict(list)
    for r in chat_rows:
        by_cat[r["category"]].append(window_total(f"{r['id']}/chat"))
    for cat in sorted(by_cat):
        vals = by_cat[cat]
        lines.append(f"  - {cat}：均值 {statistics.mean(vals):.0f} tokens（n={len(vals)}）")
    lines.append("")

    report = HERE / "results" / "token_report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    (HERE / "results" / "token_windows.json").write_text(
        json.dumps({k: dict(v) for k, v in attributed.items()}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("\n".join(lines[-12:]))
    print(f"\n账单 → {report}")


if __name__ == "__main__":
    main()
