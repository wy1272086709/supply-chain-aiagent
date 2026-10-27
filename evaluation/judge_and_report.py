"""Step 3/4：评审 + 指标计算 + 报告生成（v2：端到端 + 分侧指标）。

用法：
    cd evaluation
    python judge_and_report.py                                # 无 Key 时退化为关键词评审（分侧指标缺失）
    DASHSCOPE_API_KEY=sk-xxx python judge_and_report.py       # LLM 评审（推荐，qwen-plus）
    python judge_and_report.py --baseline manual_baseline.csv # 用实测人工基线替换默认估计
    python judge_and_report.py --pass-at 1                    # 宽松口径（部分正确也算对）
    python judge_and_report.py --results results/raw_results.jsonl --out-dir results

产物：
    results/judged.jsonl   每题评审明细（含分侧评审）
    results/report.md      汇总报告（可直接贴进测评文档）

指标体系（v2，设计说明见《分侧评测方法与实测.md》）：
    端到端（原有）：Hit@K / Hit@3 / MRR / 回答准确率 / 拒答正确率 / 幻觉率 / 耗时 / 节省率
    检索侧（新）：  上下文精确率@K、@3（块级，LLM 逐块判相关）、信息点召回率（关键信息点被召回块覆盖）
    生成侧（新）：  忠实度（答案 vs 召回上下文）、误拒率（非 L5 可答题被"知识库没有"打发）
    溯源侧（新）：  引用召回率（引用了出处文档）、引用精确率（引用中出处文档占比，惩罚过度引用）
    稳定性（新）：  判定一致率、重复运行答案文本相似度（需 run_eval --repeat 3 采集）
    已知口径：上下文/忠实度评审的"资料"= /rag/search 直接检索的 top-K 块；/ai/chat 链路内部
    改写后的真实上下文 API 不暴露，此为近似（详见方法论文档"局限"一节）。
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).parent
DASHSCOPE_BASE = os.environ.get("DASHSCOPE_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen-plus")
REFUSAL_HINTS = ("知识库", "没有相关", "未收录", "未覆盖", "无法回答", "找不到", "抱歉", "没有找到", "不掌握", "未提供")

# 误拒判定用更严的匹配，避免正常答案里出现"知识库"三个字被误判
# 注意"未提供"不能进表：业务回答里"客户未提供材料"是高频正常表述（实测误报过）
REFUSAL_PATTERNS = (
    "没有相关内容", "未收录", "未覆盖", "无法回答", "没有找到相关", "没有相关规定", "没有相关资料",
    "知识库没有", "知识库里没有", "知识库中没有", "知识库中未", "手册中没有", "手册里没有", "手册中未",
    "不掌握", "查不到", "未涉及",
)

JUDGE_PROMPT = """你是一名严格的评测员，判断「学生回答」是否与「标准答案」一致。这是企业知识库问答测评，宁严勿宽。

[问题] {question}
[标准答案] {golden}
[答案关键点] {keywords}

{special}

[学生回答]
{answer}

[评分规则]
- 2 分：关键数字、规则、结论与标准答案一致，无编造，可直接采信；
- 1 分：方向正确但缺少关键数字，或只答对一部分；
- 0 分：结论错误、关键数字错误，或编造了标准答案里没有的数字/规则。

只输出 JSON：{{"score": 0或1或2, "reason": "30字以内理由"}}"""

RETRIEVAL_JUDGE_PROMPT = """你是检索质量评测员。给定「问题」「关键信息点」和「检索召回的资料块」，逐块、逐点判断。

[问题] {question}
[关键信息点] {points}

[召回资料块]
{chunks}

[判断规则]
- chunk_relevant：该块内容能否支撑回答该问题（或其一部分）。其他手册/其他章节的内容、只有词面沾边但主题无关的块 = 0
- info_covered：该信息点能否在任一召回块中找到直接依据（数字/规则一致；换措辞表述算覆盖）
- 两个数组的长度必须分别与资料块数、信息点数一一对应

只输出 JSON：{{"chunk_relevant": [0或1, ...], "info_covered": [0或1, ...]}}"""

FAITHFULNESS_PROMPT = """你是忠实度评测员。判断「学生回答」中的关键信息是否由「给定资料」支撑。

[问题] {question}

[给定资料（检索召回的资料块）]
{chunks}

[学生回答]
{answer}

[评分规则]
- 2 分：回答中的关键数字、规则、结论均可在资料中找到直接依据；
- 1 分：大体有依据，但含资料中找不到的补充性内容（合理的概括性表述可接受，具体数字/规则不可）；
- 0 分：关键数字/规则/结论在资料中无依据（疑似编造或来自资料外记忆）。

只输出 JSON：{{"score": 0或1或2, "reason": "30字以内"}}"""


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def answers_of(row: dict) -> list[str]:
    """v2 采集的 answers 数组；旧格式回退单 answer。"""
    if row.get("answers"):
        return row["answers"]
    return [row["answer"]] if row.get("answer") is not None else []


def is_over_refusal(answer: str) -> bool:
    a = answer or ""
    return any(p in a for p in REFUSAL_PATTERNS)


def info_points(row: dict) -> list[str]:
    """信息点 = 答案关键词 + 标准答案中的数字（含单位），去重，最多 8 个。"""
    pts = list(dict.fromkeys(row.get("answer_keywords") or []))
    for m in re.finditer(r"\d+(?:\.\d+)?(?:%|元|万|倍|天|小时|个工作日|kg|ml|cm|张|次|名|个|条|票)?", row.get("golden_answer") or ""):
        pts.append(m.group(0))
    return list(dict.fromkeys(pts))[:8]


def _chunks_block(hits: list[dict], per_chunk: int = 600) -> str:
    parts = []
    for i, h in enumerate(hits, 1):
        heading = f" / {h['heading']}" if h.get("heading") else ""
        parts.append(f"块{i}《{h.get('documentTitle')}{heading}》\n{(h.get('content') or '')[:per_chunk]}")
    return "\n\n".join(parts)


def _post_json(client: httpx.Client, prompt: str) -> dict:
    resp = client.post(
        f"{DASHSCOPE_BASE}/chat/completions",
        json={
            "model": JUDGE_MODEL,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0))


def llm_judge(item: dict, answer: str, client: httpx.Client | None) -> dict | None:
    if client is None:
        return None
    is_refusal_case = item["category"] == "L5-边界拒答"
    if is_refusal_case:
        special = "本题是边界题：标准答案不存在。学生回答正确=明确说明知识库未覆盖/无法回答且没有编造具体数字或规则；给出任何具体数字/规则=0分。"
    elif item.get("turn", 1) > 1:
        special = (
            "本题是多轮对话的追问：学生回答中引用对话上文已确认过的信息（如上一轮给出的时限/金额数字）属于正常指代消解，不算编造；"
            "只判断其对追问本身的回答是否与标准答案一致。"
        )
    else:
        special = "注意：数字必须与标准答案完全一致（含单位与倍数），差一点都算错。"
    prompt = JUDGE_PROMPT.format(
        question=item["question"],
        golden=item["golden_answer"],
        keywords="、".join(item["answer_keywords"]) or "（无）",
        special=special,
        answer=(answer or "（空回答）")[:2000],
    )
    try:
        verdict = _post_json(client, prompt)
        return {"score": int(verdict["score"]), "reason": verdict.get("reason", ""), "method": "llm"}
    except Exception as e:  # noqa: BLE001 评审失败退化为关键词
        print(f"  [judge-fallback] {item['id']}: {type(e).__name__}: {e}")
        return None


def retrieval_judge(row: dict, client: httpx.Client | None) -> dict | None:
    """块相关性 + 信息点覆盖（检索侧）。"""
    if client is None:
        return None
    points = info_points(row)
    prompt = RETRIEVAL_JUDGE_PROMPT.format(
        question=row["question"],
        points="、".join(points) or "（无）",
        chunks=_chunks_block(row["hits"]),
    )
    try:
        parsed = _post_json(client, prompt)
        n_hits, n_pts = len(row["hits"]), len(points)
        cr = [int(x) for x in (parsed.get("chunk_relevant") or [])][:n_hits]
        ic = [int(x) for x in (parsed.get("info_covered") or [])][:n_pts]
        if len(cr) < n_hits:
            cr = cr + [0] * (n_hits - len(cr))
        if len(ic) < n_pts:
            ic = ic + [0] * (n_pts - len(ic))
        return {"chunk_relevant": cr, "info_covered": ic, "method": "llm"}
    except Exception as e:  # noqa: BLE001
        print(f"  [retrieval-judge-fail] {row['id']}: {type(e).__name__}: {e}")
        return None


def faithfulness_judge(row: dict, client: httpx.Client | None) -> dict | None:
    """答案是否由召回上下文支撑（生成侧）。"""
    if client is None:
        return None
    prompt = FAITHFULNESS_PROMPT.format(
        question=row["question"],
        chunks=_chunks_block(row["hits"]),
        answer=(row.get("answer") or "（空回答）")[:1500],
    )
    try:
        verdict = _post_json(client, prompt)
        return {"score": int(verdict["score"]), "reason": verdict.get("reason", ""), "method": "llm"}
    except Exception as e:  # noqa: BLE001
        print(f"  [faith-judge-fail] {row['id']}: {type(e).__name__}: {e}")
        return None


def keyword_judge(item: dict, answer: str) -> dict:
    """无 LLM Key 时的兜底评审：关键词覆盖率；L5 看拒答且无编造数字。"""
    ans = answer or ""
    if item["category"] == "L5-边界拒答":
        has_refusal = any(h in ans for h in REFUSAL_HINTS)
        fabricated = bool(re.search(r"\d+(\.\d+)?\s*(%|元|倍|天|小时|个工作日|kg)", ans))
        ok = has_refusal and not fabricated
        return {"score": 2 if ok else 0, "reason": "拒答且无编造" if ok else "未拒答或编造数字", "method": "keyword"}
    kws = item["answer_keywords"]
    if not kws:
        return {"score": 2 if ans.strip() else 0, "reason": "非空即可", "method": "keyword"}
    hit = [k for k in kws if k in ans]
    coverage = len(hit) / len(kws)
    score = 2 if coverage >= 0.6 else (1 if coverage > 0 else 0)
    return {"score": score, "reason": f"关键词覆盖 {len(hit)}/{len(kws)}", "method": "keyword"}


def pctl(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def fmt(v: float | None, suffix: str = "") -> str:
    return "—" if v is None else f"{v:.1f}{suffix}"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.1%}"


def mean_or_none(vals: list[float]) -> float | None:
    return statistics.mean(vals) if vals else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(HERE / "results" / "raw_results.jsonl"))
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    ap.add_argument("--baseline", default="", help="人工基线 CSV：question_id,seconds[,notes]")
    ap.add_argument("--pass-at", type=int, default=2, choices=[1, 2], help="准确率判定向量：2=完全正确（默认，严格）")
    args = ap.parse_args()

    rows = load_rows(Path(args.results))
    if not rows:
        raise SystemExit(f"没有原始结果，先跑 run_eval.py：{args.results}")

    # 实测基线覆盖默认估计
    measured: dict[str, float] = {}
    baseline_source = "默认估计（见测评方案.md §6）"
    if args.baseline and Path(args.baseline).exists():
        baseline_source = f"实测基线 {args.baseline}"
        with Path(args.baseline).open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                sec = (r.get("seconds") or "").strip()
                if sec:
                    measured[r["question_id"].strip()] = float(sec)

    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    judge_client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}) if api_key else None
    judge_name = f"LLM 评审（{JUDGE_MODEL}）" if judge_client else "关键词评审（未配置 DASHSCOPE_API_KEY，精度较低）"

    # ---- 评审（线程池并行；金标逐次回答 + 检索侧 + 忠实度）----
    has_chunk_content = lambda r: any(h.get("content") for h in (r.get("hits") or []))  # noqa: E731
    jobs: list[tuple[str, dict, int, object]] = []
    for row in rows:
        if row.get("error") or row.get("answer") is None:
            row["judge"] = {"score": 0, "reason": f"采集失败：{row.get('error') or '无回答'}", "method": "none"}
            row["repeat_verdicts"] = [row["judge"]]
            continue
        ans_list = answers_of(row)
        for idx, ans in enumerate(ans_list):
            jobs.append(("golden", row, idx, None))  # 占位，稍后统一提交
        if (
            has_chunk_content(row)
            and row.get("source_doc")
            and row["category"] != "L5-边界拒答"
            and row.get("turn", 1) == 1
        ):
            jobs.append(("retrieval", row, -1, None))
            if ans_list and not is_over_refusal(ans_list[0]):
                jobs.append(("faith", row, -1, None))

    with ThreadPoolExecutor(max_workers=6) as pool:
        submitted = [
            (
                kind,
                row,
                idx,
                pool.submit(
                    llm_judge, row, answers_of(row)[idx], judge_client
                )
                if kind == "golden"
                else pool.submit(retrieval_judge, row, judge_client)
                if kind == "retrieval"
                else pool.submit(faithfulness_judge, row, judge_client),
            )
            for kind, row, idx, _ in jobs
        ]
        for kind, row, idx, fut in submitted:
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"  [judge-error] {row['id']} {kind}: {e}")
                res = None
            if kind == "golden":
                verdict = res or keyword_judge(row, answers_of(row)[idx])
                row.setdefault("repeat_verdicts", []).append(verdict)
                if idx == 0:
                    row["judge"] = verdict
            elif kind == "retrieval":
                row["retrieval_judge"] = res
            else:
                row["faithfulness"] = res

    for row in rows:
        row.setdefault("judge", {"score": 0, "reason": "未评审", "method": "none"})
        row["pass"] = row["judge"]["score"] >= args.pass_at

    # ---- 端到端指标（原有口径；检索指标只算 turn=1，L6 追问不做独立检索）----
    n = len(rows)
    answered = [r for r in rows if r.get("answer") is not None]
    scored = [r for r in rows if r["judge"]["method"] != "none"]
    retrievable = [r for r in rows if r["source_doc"] and r.get("turn", 1) == 1]  # L5 无出处、L6 追问无独立检索

    hit_k = sum(1 for r in retrievable if r["golden_rank"]) / len(retrievable) if retrievable else 0
    hit3 = sum(1 for r in retrievable if r["golden_rank"] and r["golden_rank"] <= 3) / len(retrievable) if retrievable else 0
    mrr = sum(1 / r["golden_rank"] for r in retrievable if r["golden_rank"]) / len(retrievable) if retrievable else 0
    acc = sum(1 for r in scored if r["pass"]) / len(scored) if scored else 0

    l5 = [r for r in scored if r["category"] == "L5-边界拒答"]
    l5_correct = sum(1 for r in l5 if r["pass"])
    hallucination = 1 - (l5_correct / len(l5)) if l5 else None

    lat = sorted(r["chat_latency"] for r in answered if r.get("chat_latency"))
    avg_lat = statistics.mean(lat) if lat else None

    # 节省时间（AI 耗时用端到端对话耗时；baseline 用实测或默认估计）
    time_rows = [r for r in answered if r.get("chat_latency") is not None and r["answer"] is not None]
    saved_pairs = []
    for r in time_rows:
        base = measured.get(r["id"], r.get("baseline_seconds"))
        if base:
            saved_pairs.append((r["id"], r["role"], r["category"], base, r["chat_latency"]))
    avg_base = statistics.mean(p[3] for p in saved_pairs) if saved_pairs else None
    total_saved = sum(p[3] - p[4] for p in saved_pairs) if saved_pairs else None
    saved_pct = (total_saved / sum(p[3] for p in saved_pairs) * 100) if saved_pairs else None

    # ---- 分侧指标（v2 新增）----
    ctx_rows = [r for r in rows if r.get("retrieval_judge")]  # 有块内容且做了检索评审
    prec_k = mean_or_none([sum(rj["chunk_relevant"]) / len(rj["chunk_relevant"]) for r in ctx_rows for rj in [r["retrieval_judge"]] if rj["chunk_relevant"]])
    prec_3 = mean_or_none([sum(rj["chunk_relevant"][:3]) / min(3, len(rj["chunk_relevant"])) for r in ctx_rows for rj in [r["retrieval_judge"]] if rj["chunk_relevant"]])
    recall_pts = mean_or_none([sum(rj["info_covered"]) / len(rj["info_covered"]) for r in ctx_rows for rj in [r["retrieval_judge"]] if rj["info_covered"]])

    faith_rows = [r for r in rows if r.get("faithfulness")]
    faith_full = mean_or_none([1.0 if r["faithfulness"]["score"] == 2 else 0.0 for r in faith_rows])

    non_l5_answered = [r for r in answered if r["category"] != "L5-边界拒答"]
    over_refused = [r for r in non_l5_answered if is_over_refusal(r["answer"])]
    over_refusal_rate = len(over_refused) / len(non_l5_answered) if non_l5_answered else None

    cite_rows = [r for r in non_l5_answered if r.get("source_doc")]
    cite_hit_rows, cite_prec_vals = [], []
    over_cited = []
    for r in cite_rows:
        gold = (r["source_doc"] or "")[:6]
        # 先按文档去重：同一文档的多个块被引用是正常行为（不同句子各有出处），不算过度引用
        cited = list(dict.fromkeys(r.get("cited_titles") or []))
        gold_in = any(gold in (t or "") for t in cited)
        cite_hit_rows.append(1.0 if gold_in else 0.0)
        if cited:
            cite_prec_vals.append((1.0 if gold_in else 0.0) / len(cited))
            if (gold_in and len(cited) > 1) or not gold_in:
                over_cited.append((r, cited, gold_in))
    citation_recall = mean_or_none(cite_hit_rows)
    citation_precision = mean_or_none(cite_prec_vals)

    l5_hits = [len(r.get("hits") or []) for r in rows if r["category"] == "L5-边界拒答" and not r.get("error")]
    l5_avg_hits = mean_or_none([float(x) for x in l5_hits])

    rep_rows = [r for r in rows if len(answers_of(r)) > 1 and r["judge"]["method"] != "none"]
    stable_flags, sim_vals = [], []
    for r in rep_rows:
        verdicts = r.get("repeat_verdicts") or []
        if verdicts:
            stable_flags.append(1.0 if len({v["score"] >= args.pass_at for v in verdicts}) == 1 else 0.0)
        answers = answers_of(r)
        sims = [
            difflib.SequenceMatcher(None, a, b).ratio()
            for i, a in enumerate(answers) for b in answers[i + 1:]
        ]
        sim_vals.extend(sims)
    verdict_stability = mean_or_none(stable_flags)
    answer_similarity = mean_or_none(sim_vals)

    # 分组表（原有）
    def group_table(key: str) -> list[tuple[str, int, float, float, float, float, float]]:
        g: list[tuple[str, int, float, float, float, float, float]] = []
        for k in sorted({r[key] for r in rows}):
            sub = [r for r in rows if r[key] == k]
            sub_ret = [r for r in sub if r["source_doc"] and r.get("turn", 1) == 1]
            sub_acc = sum(1 for r in sub if r["pass"] and r["judge"]["method"] != "none") / len(sub)
            sub_hit = (sum(1 for r in sub_ret if r["golden_rank"]) / len(sub_ret)) if sub_ret else None
            sub_lat = statistics.mean([r["chat_latency"] for r in sub if r.get("chat_latency") is not None]) if any(
                r.get("chat_latency") is not None for r in sub) else None
            sub_saved = statistics.mean([p[3] - p[4] for p in saved_pairs if p[0] in {r["id"] for r in sub}]) if any(
                p[0] in {r["id"] for r in sub} for p in saved_pairs) else None
            g.append((k, len(sub), sub_acc, sub_hit if sub_hit is not None else 0, sub_lat or 0, sub_saved or 0, 0))
        return g

    fails = [r for r in scored if not r["pass"]]
    unfaithful = [r for r in faith_rows if r["faithfulness"]["score"] < 2]
    missing_points = [
        (r, [p for p, c in zip(info_points(r), r["retrieval_judge"]["info_covered"]) if not c])
        for r in ctx_rows if r["retrieval_judge"].get("info_covered") and sum(r["retrieval_judge"]["info_covered"]) < len(r["retrieval_judge"]["info_covered"])
    ]
    low_prec = [
        (r, r["retrieval_judge"]["chunk_relevant"])
        for r in ctx_rows if r["retrieval_judge"]["chunk_relevant"] and sum(r["retrieval_judge"]["chunk_relevant"]) / len(r["retrieval_judge"]["chunk_relevant"]) < 0.5
    ]
    unstable = [r for r in rep_rows if stable_flags and len({v["score"] >= args.pass_at for v in (r.get("repeat_verdicts") or [])}) > 1]

    # ---- 报告 ----
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    topk_str = str(len(rows[0].get("hits") or []) or "K") if rows else "K"
    lines = [
        "# 知识库问答测评报告（v2：端到端 + 分侧指标）",
        "",
        f"- 测评时间：{now}　题量：{n} 行（含多轮追问行）",
        f"- 评审方式：{judge_name}　准确率口径：{'完全正确' if args.pass_at == 2 else '部分正确即计对'}",
        f"- 人工基线：{baseline_source}",
        "",
        "## 1. 总体指标（端到端）",
        "",
        "| 指标 | 数值 | 口径说明 |",
        "| --- | --- | --- |",
        f"| 检索命中率 Hit@topK | {hit_k:.1%} | 标准出处文档出现在召回前 {topk_str} 条内（仅 turn=1 行） |",
        f"| 检索命中率 Hit@3 | {hit3:.1%} | 标准出处文档出现在召回前 3 条内 |",
        f"| MRR | {mrr:.3f} | 标准出处文档首次命中排名的倒数均值 |",
        f"| 回答准确率 | {acc:.1%} | 评审得分 ≥ {args.pass_at} 的比例（{len(scored)} 行有效作答） |",
        f"| 边界拒答正确率 | {(l5_correct / len(l5)):.1%} | L5 题共 {len(l5)} 题 |",
        f"| 幻觉率（L5 编造） | {fmt(hallucination * 100 if hallucination is not None else None, '%')} | 1 − 拒答正确率 |",
        f"| 平均响应耗时 | {fmt(avg_lat, 's')} | /ai/chat 端到端，P90={fmt(pctl(lat, 0.9), 's')} |",
        f"| 平均人工查文档基线 | {fmt(avg_base, 's')} | {'实测' if measured else '估计'}值",
        f"| 累计节省查文档时间 | {fmt(total_saved, 's')} | 基线 − AI 耗时（{len(saved_pairs)} 行） |",
        f"| 平均节省率 | {fmt(saved_pct, '%')} | 节省时间 ÷ 人工基线 |",
        "",
        "## 2. 分岗位",
        "",
        "| 岗位 | 行数 | 准确率 | 检索命中率 | 平均耗时(s) | 平均节省(s) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for k, cnt, a, h, l, s, _ in group_table("role"):
        lines.append(f"| {k} | {cnt} | {a:.1%} | {h:.1%} | {l:.1f} | {s:.1f} |")
    lines += [
        "",
        "## 3. 分题型",
        "",
        "| 题型 | 行数 | 准确率 | 检索命中率 | 平均耗时(s) | 平均节省(s) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for k, cnt, a, h, l, s, _ in group_table("category"):
        lines.append(f"| {k} | {cnt} | {a:.1%} | {h:.1%} | {l:.1f} | {s:.1f} |")
    lines += [
        "",
        "## 4. 分侧指标（v2 新增）",
        "",
        "| 指标 | 数值 | 口径说明 |",
        "| --- | --- | --- |",
        f"| 上下文精确率@K | {pct(prec_k)} | 前 {topk_str} 个召回块中与问题相关的比例（LLM 逐块判定，{len(ctx_rows)} 行） |",
        f"| 上下文精确率@3 | {pct(prec_3)} | 前 3 块的相关比例 |",
        f"| 信息点召回率 | {pct(recall_pts)} | 标准答案关键信息点被召回块覆盖的比例 |",
        f"| 忠实度（完全支撑） | {pct(faith_full)} | 回答关键数字/规则均在召回资料中有依据（{len(faith_rows)} 行） |",
        f"| 误拒率 | {pct(over_refusal_rate)} | 非 L5 可答题被\"知识库没有\"打发（{len(over_refused)}/{len(non_l5_answered)}） |",
        f"| 引用召回率 | {pct(citation_recall)} | 回答引用了出处文档 |",
        f"| 引用精确率 | {pct(citation_precision)} | 引用文档中出处文档占比（惩罚过度引用） |",
        f"| L5 平均召回块数 | {fmt(l5_avg_hits)} | 库外问题仍返回的块数（越少越好） |",
        f"| 判定一致率 | {pct(verdict_stability)} | 同题重复运行（{len(rep_rows)} 题 × {len(answers_of(rep_rows[0])) if rep_rows else 0} 次）判定结果一致 |",
        f"| 重复运行答案相似度 | {pct(answer_similarity)} | 文本相似度均值（difflib） |",
        "",
        "> 分侧口径：\"资料\" = /rag/search 直接检索 top-K 块；/ai/chat 链路改写后的真实上下文 API 不暴露，此为近似。",
        "> L6 多轮追问行不做独立检索（hits 为空），不参与检索侧指标；参与准确率/引用/误拒。",
        "",
        f"## 5. 失败与风险明细（未通过 {len(fails)} 行）",
        "",
    ]
    if fails:
        lines += ["| 题号 | 题型 | 评审 | 问题 | 标准答案 | 实际回答（前150字） |", "| --- | --- | --- | --- | --- | --- |"]
        for r in fails:
            ans = (r["answer"] or "").replace("\n", " ")[:150]
            gold = r["golden_answer"].replace("\n", " ")[:120]
            lines.append(f"| {r['id']} | {r['category']} | {r['judge']['reason']} | {r['question'][:40]} | {gold} | {ans} |")
    else:
        lines.append("端到端全部通过 🎉")
    if unfaithful:
        lines += ["", "### 忠实度不足（答案含资料外关键信息）", "", "| 题号 | 评分 | 理由 |", "| --- | --- | --- |"]
        for r in unfaithful:
            lines.append(f"| {r['id']} | {r['faithfulness']['score']} | {r['faithfulness']['reason']} |")
    if missing_points:
        lines += ["", "### 信息点未被召回覆盖", "", "| 题号 | 缺失信息点 |", "| --- | --- |"]
        for r, pts in missing_points:
            lines.append(f"| {r['id']} | {'、'.join(pts)} |")
    if low_prec:
        lines += ["", "### 块相关率低（<50%）", "", "| 题号 | 相关块数/总块数 |", "| --- | --- |"]
        for r, cr in low_prec:
            lines.append(f"| {r['id']} | {sum(cr)}/{len(cr)} |")
    if over_refused:
        lines += ["", "### 误拒（可答题被拒答）", "", "| 题号 | 问题 | 回答（前100字） |", "| --- | --- | --- |"]
        for r in over_refused:
            lines.append(f"| {r['id']} | {r['question'][:36]} | {(r['answer'] or '').replace(chr(10), ' ')[:100]} |")
    if over_cited:
        lines += ["", "### 引用可疑（未引出处文档 / 引了无关文档）", "", "| 题号 | 引用了 | 含出处文档 |", "| --- | --- | --- |"]
        for r, cited, gold_in in over_cited[:20]:
            lines.append(f"| {r['id']} | {'、'.join(cited)[:60]} | {'是' if gold_in else '否'} |")
    if unstable:
        lines += ["", "### 重复运行判定不一致", "", "| 题号 | 各次判定得分 |", "| --- | --- |"]
        for r in unstable:
            scores = "/".join(str(v["score"]) for v in (r.get("repeat_verdicts") or []))
            lines.append(f"| {r['id']} | {scores} |")
    lines += [
        "",
        "## 6. 结论与改进建议",
        "",
        "- （填写：分侧指标短板——是检索没喂对料，还是生成不忠实；新指标失败模式分析）",
        "- （填写：对分块/向量/rerank/prompt 的下一步优化动作）",
    ]

    judged_path = Path(args.out_dir) / "judged.jsonl"
    with judged_path.open("w", encoding="utf-8") as fout:
        for row in rows:
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
    report_path = Path(args.out_dir) / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    if judge_client:
        judge_client.close()

    print(f"评审完成：{judge_name}")
    print(f"  端到端：Hit@topK={hit_k:.1%}  Hit@3={hit3:.1%}  MRR={mrr:.3f}  准确率={acc:.1%}  L5 拒答正确={l5_correct}/{len(l5)}")
    print(f"  分侧：块精确@K={pct(prec_k)}  信息点召回={pct(recall_pts)}  忠实度={pct(faith_full)}  误拒={pct(over_refusal_rate)}")
    print(f"  溯源/稳定：引用召回={pct(citation_recall)}  引用精确={pct(citation_precision)}  判定一致率={pct(verdict_stability)}  相似度={pct(answer_similarity)}")
    print(f"  明细 → {judged_path}\n  报告 → {report_path}")


if __name__ == "__main__":
    main()
