"""Step 0：把三份操作手册发布进知识库，并等待索引可见。

用法：
    cd evaluation
    BASE_URL=http://localhost:3000 python upload_manuals.py

前提：
    - 服务已启动，且 .env 中 DOCUMENT_REQUIRE_APPROVAL=false（免审直接发布）
    - 账号有 document:create、search 权限（默认 user/123456）
幂等性：标题重复时服务端会新建同题文档，重复运行前请先在管理端删除旧文档。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:3000")
USERNAME = os.environ.get("EVAL_USERNAME", "user")
PASSWORD = os.environ.get("EVAL_PASSWORD", "123456")

MANUALS_DIR = Path(__file__).parent / "manuals"
# (文件名, 文档标题, 摘要, 标签, 索引探针问题)
MANUALS = [
    (
        "01-客服-理赔SOP操作手册.md",
        "理赔SOP操作手册（客服）",
        "理赔报案时限、材料、判责、赔付标准、审批权限与拒赔情形。",
        "客服,理赔,SOP",
        "理赔报案时限是多久",
    ),
    (
        "02-结算-运费合同结算手册.md",
        "运费合同结算手册（结算）",
        "计费重、渠道报价、附加费、账期返点、对账差异与发票规则。",
        "结算,运费,合同",
        "体积重计算公式",
    ),
    (
        "03-运营-站点操作手册.md",
        "站点操作手册（运营）",
        "到件卸车、入库验视、派送、异常件、盘点、设备与禁运品管理。",
        "运营,站点,操作手册",
        "到件扫描完成时限",
    ),
]


def login(client: httpx.Client) -> str:
    resp = client.post(
        f"{BASE_URL}/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("accessToken")
    if not token:
        sys.exit("登录失败：响应中没有 accessToken")
    return token


def publish(client: httpx.Client, token: str, path: Path, title: str, summary: str, tags: str) -> None:
    content = path.read_text(encoding="utf-8")
    resp = client.post(
        f"{BASE_URL}/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": title, "summary": summary, "tags": tags, "status": 1, "isPublic": True, "content": content},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    print(f"  发布 {title}: id={body.get('id')} status={body.get('status')}")
    if body.get("status") != 1:
        sys.exit(f"  状态不是 1（未直接发布）：{body}。检查 DOCUMENT_REQUIRE_APPROVAL=false 并重启应用。")


def wait_indexed(client: httpx.Client, token: str, probe: str, title: str, timeout_s: int = 180) -> bool:
    """索引是异步的（MQ→分块→向量→ES），轮询探针问题直到命中该文档。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = client.post(
                f"{BASE_URL}/rag/search",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": probe, "topK": 5},
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json() or []
            if any(title[:6] in (h.get("documentTitle") or "") for h in hits):
                return True
        except httpx.HTTPError:
            pass
        time.sleep(5)
    return False


def main() -> None:
    with httpx.Client() as client:
        token = login(client)
        print(f"登录成功：{USERNAME}@{BASE_URL}")
        for path, title, summary, tags, probe in MANUALS:
            full = MANUALS_DIR / path
            if not full.exists():
                sys.exit(f"找不到手册文件：{full}")
            print(f"[发布] {path}")
            publish(client, token, full, title, summary, tags)

        print("\n[等待索引] 异步分块+向量+ES 写入，最长等 180 秒/篇…")
        for _, title, _, _, probe in MANUALS:
            ok = wait_indexed(client, token, probe, title)
            mark = "✅ 可检索" if ok else "❌ 超时未见命中"
            print(f"  {title}: {mark}")
            if not ok:
                sys.exit("索引未就绪：看 pipeline / ES 日志，确认 MQ 消费与 embedding 未整批失败。")
        print("\n三份手册均已发布且可检索，可以运行 run_eval.py。")


if __name__ == "__main__":
    main()
