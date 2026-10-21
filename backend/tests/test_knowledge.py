"""知识库（乐享集成）/ 客服工单 / 标书助手 / 大模型适配器（PRD FR-06、7.7、7.8、MCP kb.search/kb.bid_check）。

运行：cd backend && TEST_DB=jucheng_test_kb .venv/bin/python -m pytest tests/test_knowledge.py -q
"""
import time
from pathlib import Path

import pytest

from tests.conftest import data

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
KF, CP, EMP, ADMIN = "huangqi", "xiekai", "zengli", "admin"


def _lexiang_base():
    from app.core.config import settings
    return settings.lexiang_web_base.rstrip("/")


# ============================================================ 知识库
def test_kb_search_with_source_url_and_relevance(client, login):
    h = login(EMP)  # 全员可用
    r = data(client.get("/api/v1/kb/search", params={"q": "年假"}, headers=h))
    assert r["items"], "年假应有检索结果"
    assert "年休假" in r["items"][0]["title"]
    for it in r["items"]:
        assert it["url"].startswith(_lexiang_base() + "/teams/k100054/docs/")  # 出处指回乐享
        assert {"doc_id", "title", "category", "heading", "snippet", "score"} <= set(it)
    assert "<mark>" in r["items"][0]["snippet"]

    r = data(client.get("/api/v1/kb/search", params={"q": "大屏无信号"}, headers=h))
    top = r["items"][0]
    assert top["category"] == "客服" and "无信号" in top["title"] and "排查手册" in top["title"]


def test_kb_search_stable_and_category_filter(client, login):
    h = login(EMP)
    a = data(client.get("/api/v1/kb/search", params={"q": "监控离线 录像丢失"}, headers=h))["items"]
    b = data(client.get("/api/v1/kb/search", params={"q": "监控离线 录像丢失"}, headers=h))["items"]
    assert [(x["doc_id"], x["score"]) for x in a] == [(x["doc_id"], x["score"]) for x in b]
    assert [x["score"] for x in a] == sorted([x["score"] for x in a], reverse=True)
    c = data(client.get("/api/v1/kb/search", params={"q": "ISO9001", "category": "资质"}, headers=h))["items"]
    assert c and all(x["category"] == "资质" for x in c)


def test_kb_preview_categories_and_status(client, login):
    h = login(EMP)
    cats = data(client.get("/api/v1/kb/categories", headers=h))
    names = {c["name"] for c in cats}
    assert {"制度", "售前", "方案模板", "案例", "资质", "客服"} <= names
    assert sum(c["count"] for c in cats) >= 25
    hit = data(client.get("/api/v1/kb/search", params={"q": "差旅住宿标准"}, headers=h))["items"][0]
    doc = data(client.get(f"/api/v1/kb/docs/{hit['doc_id']}", headers=h))
    assert doc["content_text"] and doc["chunks"] and doc["url"] == hit["url"]
    assert any(c["seq"] == hit["seq"] for c in doc["chunks"])  # 前端据 seq 定位命中段落
    st = data(client.get("/api/v1/kb/sync/status", headers=h))
    assert st["total_docs"] >= 25 and st["last_success_at"] and st["can_sync"] is False


def test_kb_corpus_size():
    from app.integration.lexiang.mock import CORPUS, MockLexiang
    assert len(CORPUS) >= 25
    assert all(800 <= len(d["content"]) <= 3000 for d in CORPUS)
    assert all("## " in d["content"] for d in CORPUS)  # 含小标题
    assert {c["name"] for c in MockLexiang().list_categories()} >= {"制度", "售前", "方案模板", "案例", "资质", "客服"}


def test_kb_sync_rbac_and_incremental(client, login):
    assert client.post("/api/v1/kb/sync", headers=login(EMP)).json()["code"] == 40301
    st = data(client.post("/api/v1/kb/sync", headers=login(ADMIN)))
    assert st["error"] is None and st["added"] == 0 and st["total_docs"] >= 25  # 增量：无新文档
    st = data(client.post("/api/v1/kb/sync", params={"full": True}, headers=login(ADMIN)))
    assert st["updated"] >= 25 and st["total_chunks"] > 100


def test_large_document_chunking_under_one_minute(db):
    """FR-06 验收：50MB 文档 ≤1min 完成抽取分块。"""
    from app.services import kb_service
    para = "## 第{n}节 校园网络建设\n" + "智慧校园网络采用万兆骨干、千兆到桌面，无线 AP 按教室部署，交互大屏与安防监控统一接入。" * 30 + "\n"
    text = "".join(para.format(n=i) for i in range(18000))
    assert len(text.encode()) >= 50 * 1024 * 1024
    t0 = time.perf_counter()
    chunks = kb_service.chunk_document(text)
    cost = time.perf_counter() - t0
    assert cost < 60, f"分块耗时 {cost:.1f}s"
    assert len(chunks) >= 18000 and all(len(c["text"]) <= kb_service.MAX_CHUNK + 1 for c in chunks[:200])
    # 入库（10MB 实测 + 线性外推 50MB）
    small = text[: len(text) // 5]
    t0 = time.perf_counter()
    d = kb_service.add_local_document(db, "大文本性能测试", "产品", small)
    db.commit()
    cost_db = time.perf_counter() - t0
    try:
        assert cost_db * 5 < 60, f"10MB 入库 {cost_db:.1f}s，外推 50MB 超 1 分钟"
    finally:
        db.delete(d)
        db.commit()
        kb_service.invalidate_index()


def test_add_local_document_searchable(db):
    from app.services import kb_service
    d = kb_service.add_local_document(db, "客服经验：录播主机风扇异响处理", "客服",
                                      "## 处理经验\n录播主机风扇异响多为灰尘积累，断电后清理风扇即可恢复。")
    db.commit()
    try:
        hits = kb_service.search(db, "录播主机风扇异响")
        assert hits[0]["doc_id"] == d.id and hits[0]["url"]
    finally:
        db.delete(d)
        db.commit()
        kb_service.invalidate_index()


# ============================================================ 客服工单
def test_ticket_register_one_sentence(client, login):
    h = login(KF)
    t0 = time.perf_counter()
    r = data(client.post("/api/v1/service/tickets", json={"text": "登记客户测试学校甲反馈：录播教室交互大屏无信号"}, headers=h))
    assert time.perf_counter() - t0 < 2, "登记应在 2 秒内完成（PRD ≤1min）"
    t = r["ticket"]
    assert t["customer_name"] == "测试学校甲" and t["category"] == "显示设备" and t["device"] == "交互大屏"
    assert t["ticket_no"].startswith("KF-")
    assert r["duplicate"]["is_duplicate"] is False
    assert all(s["id"] != t["id"] for s in r["similar"])  # 排除自身
    assert r["similar"] and all(s["category"] == "显示设备" for s in r["similar"])
    assert r["similar"][0]["device"] == "交互大屏"  # 同类设备+症状
    sug = r["suggestion"]
    assert sug["kb"] and sug["kb"][0]["url"].startswith(_lexiang_base())  # 排查建议带出处
    assert "无信号" in sug["kb"][0]["title"] and sug["steps"] and sug["steps_source"]["url"]
    assert sug["history"] and sug["history"][0]["solution"]
    assert t["ticket_no"] in sug["reply"] and sug["reply_engine"] == "rule"


def test_ticket_dedupe_merge_within_24h(client, login):
    h = login(KF)
    body = {"customer_name": "去重测试中学", "problem_desc": "宿舍楼门禁人脸识别失败，学生刷脸不开门", "channel": "电话"}
    a = data(client.post("/api/v1/service/tickets", json=body, headers=h))
    b = data(client.post("/api/v1/service/tickets", json={**body, "problem_desc": "宿舍楼人脸门禁识别失败，不开门", "channel": "微信"}, headers=h))
    assert b["duplicate"]["is_duplicate"] and b["duplicate"]["of"]["id"] == a["ticket"]["id"]
    assert b["ticket"]["duplicate_of"] == a["ticket"]["id"]
    assert all(s["id"] not in (b["ticket"]["id"], a["ticket"]["id"]) for s in b["similar"])
    detail = data(client.get(f"/api/v1/service/tickets/{a['ticket']['id']}", headers=h))
    assert [d["id"] for d in detail["duplicates"]] == [b["ticket"]["id"]]
    other = data(client.post("/api/v1/service/tickets", json={**body, "customer_name": "另一所学校"}, headers=h))
    assert other["duplicate"]["is_duplicate"] is False


def test_ticket_classification_rules():
    from app.services import cs_service as cs
    assert cs.classify("操场监控摄像机离线") == "安防监控"
    assert cs.classify("校园广播没声音，打铃不响") == "音视频"
    assert cs.classify("食堂消费机刷卡失败") == "一卡通"
    assert cs.classify("教室 Wi-Fi 连不上，AP 掉线") == "网络"
    assert cs.classify("素质评价平台密码错误登录不了") == "账号权限"
    assert cs.classify("打印纸用完了") == "其他"


def test_ticket_flow_and_reply_adopt(client, login):
    h = login(KF)
    r = data(client.post("/api/v1/service/tickets", json={"text": "登记客户流转测试小学反馈：三年级 2 班广播无声"}, headers=h))
    tid = r["ticket"]["id"]
    assert client.put(f"/api/v1/service/tickets/{tid}/status", json={"status": 3}, headers=h).json()["code"] == 40001
    data(client.put(f"/api/v1/service/tickets/{tid}/status", json={"status": 2}, headers=h))
    t = data(client.put(f"/api/v1/service/tickets/{tid}/status", json={
        "status": 3, "root_cause_type": "配置", "root_cause": "作息表未更新", "solution": "远程更新作息表"}, headers=h))
    assert t["status"] == 3 and t["resolved_at"]
    assert data(client.put(f"/api/v1/service/tickets/{tid}/status", json={"status": 4}, headers=h))["status"] == 4
    sug = data(client.post(f"/api/v1/service/tickets/{tid}/reply/adopt", json={"adopted": True}, headers=h))
    assert sug["adopted"] is True
    kpi = data(client.get("/api/v1/service/kpi", headers=h))
    assert kpi["month_total"] > 0 and 0 <= kpi["kb_hit_rate"] <= 1 and kpi["reply_adopt_rate"] > 0
    lst = data(client.get("/api/v1/service/tickets", params={"keyword": "流转测试小学"}, headers=h))
    assert lst["total"] == 1 and lst["items"][0]["status"] == 4


def test_attribution_top10_and_report(client, login):
    h = login(KF)
    a = data(client.get("/api/v1/service/attribution", params={"start": "2026-07-20", "end": "2026-10-20"}, headers=h))
    top = a["top10"]
    assert 5 <= len(top) <= 10 and [c["count"] for c in top] == sorted([c["count"] for c in top], reverse=True)
    assert top[0]["label"] == "交互大屏·无信号"  # 种子中聚类最明显的高频问题
    assert a["root_causes"] and a["advice"] and a["avg_resolve_hours"] and a["duplicate_rate"] > 0
    rep = data(client.post("/api/v1/service/attribution/report", json={"start": "2026-07-20", "end": "2026-10-20"}, headers=h))
    assert rep["metrics"]["top10"] and rep["metrics"]["advice"] and rep["files"]
    assert client.get(rep["files"][0]["url"]).status_code == 200
    reps = data(client.get("/api/v1/service/reports", headers=h))
    assert any(r["id"] == rep["id"] for r in reps)


def test_attribution_jobs(db):
    from app.jobs import load_all
    from app.jobs.registry import run_job
    load_all()
    e = run_job("cs_attribution_report", {"week": "2026-W42"}, trigger="manual", force=True, db=db)
    assert e.status == "success", e.error_msg
    e = run_job("cs_attribution_monthly", {"period": "2026-09"}, trigger="manual", force=True, db=db)
    assert e.status == "success", e.error_msg
    e = run_job("kb_sync", {}, trigger="manual", force=True, db=db)
    assert e.status == "success", e.error_msg


# ============================================================ RBAC
def test_rbac(client, login):
    assert client.get("/api/v1/tender/docs", headers=login(KF)).json()["code"] == 40301  # 客服访问标书 403
    assert client.get("/api/v1/service/kpi", headers=login(EMP)).json()["code"] == 40301
    assert client.get("/api/v1/service/kpi", headers=login(CP)).json()["code"] == 40301
    for u in (KF, CP, EMP):  # 全员可用知识库
        assert client.get("/api/v1/kb/search", params={"q": "报销"}, headers=login(u)).json()["code"] == 0
    assert client.get("/api/v1/kb/search", params={"q": "报销"}).json()["code"] == 40101


# ============================================================ 标书助手
def _upload(client, h, name, doc_type):
    p = SAMPLES / name
    r = client.post("/api/v1/tender/docs/upload", headers=h, data={"doc_type": doc_type},
                    files={"file": (name, p.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    return data(r)


@pytest.fixture(scope="module")
def tender_docs(client, login):
    from scripts import knowledge_samples as ks
    h = login(CP)
    return {"tender": _upload(client, h, ks.TENDER_FILE, "tender"), "bid": _upload(client, h, ks.BID_FILE, "bid"),
            "material": _upload(client, h, ks.MATERIAL_FILE, "material")}


def test_tender_clause_parsing(tender_docs):
    t = tender_docs["tender"]
    nos = {c["no"]: c for c in t["clauses"]}
    assert nos["2.5"]["required"] and nos["2.5"]["kind"] == "资质"
    assert nos["4.1"]["required"] and nos["4.1"]["kind"] == "商务"
    assert nos["5.2"]["kind"] == "格式"
    assert any(no.startswith("3.1-表") and c["kind"] == "技术参数" for no, c in nos.items())
    assert t["star_count"] >= 5


def test_bid_check_multi_round_recall(client, login, tender_docs):
    from scripts import knowledge_samples as ks
    h = login(CP)
    chk = data(client.post("/api/v1/tender/checks", json={"tender_doc_id": tender_docs["tender"]["id"],
                                                          "bid_doc_id": tender_docs["bid"]["id"]}, headers=h))
    assert chk["current_round"] == 1 and chk["rounds"][0]["dimension"] == "资质与资格" and chk["next_question"]
    dims = [chk["rounds"][0]["dimension"]]
    for ans in ["安防工程企业设计施工维护能力证书已补充", "继续", "继续", "继续", "确认"]:
        chk = data(client.post(f"/api/v1/tender/checks/{chk['id']}/reply", json={"answer": ans}, headers=h))
        dims.append(chk["rounds"][-1]["dimension"])
    assert chk["status"] == "done" and len(chk["rounds"]) == 5
    assert dims[:5] == ["资质与资格", "技术参数", "商务条款", "格式与签章", "★实质性条款"]
    findings = chk["findings"]
    tender_text = data(client.get(f"/api/v1/tender/docs/{tender_docs['tender']['id']}", headers=h))["content_text"]

    def found(exp):
        for f in findings:
            if f["dimension"] != exp["dimension"] or not f["clause_no"].startswith(exp["clause_prefix"]):
                continue
            if exp.get("param") and f.get("param") != exp["param"]:
                continue
            if exp.get("item") and f.get("item") != exp["item"]:
                continue
            if exp.get("keyword") and exp["keyword"] not in f["deviation"]:
                continue
            return f
        return None
    for exp in ks.EXPECTED_DEVIATIONS:  # 召回 100%
        f = found(exp)
        assert f, f"未检出预置偏离：{exp}"
        assert f["clause_text"][:12] in tender_text.replace("★", "")  # 可追溯需求书原文
        ev_types = {e["type"] for e in f["evidence"]}
        assert {"tender", "bid"} <= ev_types and all(e["url"] for e in f["evidence"])
    assert any(e["type"] == "lexiang" and e["url"].startswith(_lexiang_base()) for f in findings for e in f["evidence"])
    qual = next(f for f in findings if f.get("item") == "安防工程企业设计施工维护能力证书")
    assert qual["status"].startswith("已说明")  # 对话答复推进
    assert {f["severity"] for f in findings if f["required"]} == {"高"}
    # 无误报：满足项不进入整改清单
    assert not any(f.get("param") in ("像素", "红外距离", "报价有效期", "响应时间", "存储时长") for f in findings)

    f = data(client.post(f"/api/v1/tender/checks/{chk['id']}/export", params={"fmt": "xlsx"}, headers=h))
    assert f["filename"].endswith(".xlsx") and client.get(f["url"]).status_code == 200
    from io import BytesIO

    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(client.get(f["url"]).content))
    assert "口径说明" in wb.sheetnames and wb["整改清单"].max_row == len(findings) + 1
    f = data(client.post(f"/api/v1/tender/checks/{chk['id']}/export", params={"fmt": "docx"}, headers=h))
    assert client.get(f["url"]).status_code == 200


def test_bid_check_one_shot_for_mcp(db, tender_docs):
    from app.services import tender_service
    r = tender_service.bid_check(db, tender_docs["tender"]["id"], tender_docs["bid"]["id"], persist=False)
    db.rollback()
    assert r["status"] == "done" and len(r["rounds"]) == 5 and r["stats"]["total"] >= 5
    assert r["stats"]["by_severity"]["高"] >= 4


def test_generation_with_citations(client, login, tender_docs):
    h = login(CP)
    for gt in ("implementation", "installation", "presales"):
        g = data(client.post("/api/v1/tender/generations", headers=h, json={
            "gen_type": gt, "tender_doc_id": tender_docs["tender"]["id"], "material_ids": [tender_docs["material"]["id"]]}))
        assert g["engine"] == "rule"  # LLM 未配置走规则
        assert len(g["sections"]) >= 6
        for s in g["sections"]:
            assert s["citations"], f"{s['heading']} 缺少出处"
            ns = {c["n"] for c in s["citations"]}
            import re
            assert {int(x) for x in re.findall(r"\[(\d+)\]", s["body"])} <= ns
            assert all(c["url"].startswith("http") and c["excerpt"] for c in s["citations"])
        types = {c["type"] for c in g["references"]}
        assert {"lexiang", "tender"} <= types
        assert any(c["url"].startswith(_lexiang_base()) for c in g["references"])
    assert "material" in types
    f = data(client.post(f"/api/v1/tender/generations/{g['id']}/export", headers=h))
    assert f["filename"].endswith(".docx") and client.get(f["url"]).status_code == 200


def test_generation_llm_path_and_fallback(db, tender_docs, monkeypatch):
    from app.integration import llm
    from app.services import tender_service
    assert llm.enabled() is False and llm.complete("s", "u") is None
    monkeypatch.setattr(llm, "enabled", lambda: True)
    import re

    def fake(system, user, **kw):  # 引用本章材料中的第一个编号
        n = re.search(r"^\[(\d+)\]", user, re.M).group(1)
        return f"依据需求书与模板组织实施。[{n}]"
    monkeypatch.setattr(llm, "complete", fake)
    g = tender_service.generate(db, "installation", tender_docs["tender"]["id"])
    assert g.engine == "llm" and all(s["engine"] == "llm" and s["citations"] for s in g.sections)
    monkeypatch.setattr(llm, "complete", lambda system, user, **kw: "编造的内容[999]")  # 越界引用 → 规则兜底
    g = tender_service.generate(db, "installation", tender_docs["tender"]["id"])
    assert g.engine == "rule"
    db.rollback()


def test_tender_upload_rejects_pdf_and_write_permission(client, login):
    r = client.post("/api/v1/tender/docs/upload", headers=login(CP), data={"doc_type": "tender"},
                    files={"file": ("x.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert r.json()["code"] == 42201 and "PDF" in r.json()["message"]
    lib = data(client.get("/api/v1/tender/library", headers=login(CP)))
    assert lib["方案模板"] and lib["案例"] and lib["资质"] and lib["售前"]
