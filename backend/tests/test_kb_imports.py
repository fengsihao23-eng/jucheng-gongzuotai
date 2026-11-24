"""自建知识库（上传/分类/可见范围/审核/版本）与统一资料导入（分流/待整理池/撤回）。

运行：cd backend && TEST_DB=jucheng_test_kbi .venv/bin/python -m pytest tests/test_kb_imports.py -q
"""
import io
import uuid

from tests.conftest import data, deletion_body

EMP, HR, CP, KF, YX, ADMIN = "zengli", "dengxin", "xiekai", "huangqi", "songjia", "admin"


def _docx(*paras: str, heading: str | None = None) -> bytes:
    from docx import Document
    d = Document()
    if heading:
        d.add_heading(heading, level=1)
    for t in paras:
        d.add_paragraph(t)
    tbl = d.add_table(rows=1, cols=2)
    tbl.rows[0].cells[0].text, tbl.rows[0].cells[1].text = "适用范围", "全体员工"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    """最小的带文字层 PDF（Helvetica，ASCII 文本）。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _scan_pdf(pages: int = 1) -> bytes:
    """没有文字层的扫描版 PDF（每页一张图），用于验证多模态识别分流。"""
    import pymupdf
    from PIL import Image, ImageDraw
    buf = io.BytesIO()
    im = Image.new("RGB", (600, 800), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, 560, 300], outline="black", width=4)
    d.rectangle([60, 80, 540, 120], fill="black")
    im.save(buf, "PNG")
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(40, 40, 555, 780), stream=buf.getvalue())
    return doc.tobytes()


def _xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _found(client, h, q: str, doc_id: int) -> bool:
    items = data(client.get("/api/v1/kb/search", params={"q": q, "limit": 50}, headers=h))["items"]
    return any(x["doc_id"] == doc_id for x in items)


def _upload(client, h, content: bytes, name: str, **form):
    return client.post("/api/v1/kb/docs", files={"file": (name, content)}, data=form, headers=h)


# ============================================================ 分类
def test_default_category_tree(client, login):
    tree = data(client.get("/api/v1/kb/tree", headers=login(EMP)))
    names = [c["name"] for c in tree]
    assert names[:8] == ["制度", "售前", "方案模板", "案例", "资质", "产品", "客服", "合同范本"]
    zd = tree[0]
    assert {"人事制度", "财务制度"} <= {c["name"] for c in zd["children"]} and zd["builtin"]
    sq = next(c for c in tree if c["name"] == "售前")
    assert [c["name"] for c in sq["children"]][:3] == ["数字校园", "综合智慧安防", "素质教育考评"]


def test_category_management(client, login):
    h, admin = login(EMP), login(ADMIN)
    assert client.post("/api/v1/kb/categories", json={"name": "培训资料"}, headers=h).json()["code"] == 40301
    cid = data(client.post("/api/v1/kb/categories", json={"name": "培训资料", "reviewer_roles": ["RS-01"]}, headers=admin))["id"]
    sub = data(client.post("/api/v1/kb/categories", json={"name": "新员工", "parent_id": cid}, headers=admin))["id"]
    assert client.post("/api/v1/kb/categories", json={"name": "x", "parent_id": sub}, headers=admin).json()["code"] == 40001
    assert client.post("/api/v1/kb/categories", json={"name": "培训资料"}, headers=admin).json()["code"] == 40901
    tree = data(client.get("/api/v1/kb/tree", headers=admin))
    zd = next(c for c in tree if c["name"] == "制度")
    assert client.put(f"/api/v1/kb/categories/{zd['id']}", json={"name": "规章"}, headers=admin).json()["code"] == 40001
    assert client.request("DELETE", f"/api/v1/kb/categories/{zd['id']}", headers=admin, json=deletion_body()).json()["code"] == 40001
    # 改名会同步到文档
    r = _upload(client, admin, _docx("新员工第一周的培训安排与考核方式说明。"), "新员工培训.docx",
                category="培训资料", subcategory="新员工")
    doc_id = data(r)["id"]
    data(client.put(f"/api/v1/kb/categories/{sub}", json={"name": "入职培训"}, headers=admin))
    assert data(client.get(f"/api/v1/kb/docs/{doc_id}", headers=admin))["subcategory"] == "入职培训"
    assert client.request("DELETE", f"/api/v1/kb/categories/{cid}", headers=admin, json=deletion_body()).json()["code"] == 40901  # 有文档不能删


# ============================================================ 上传 / 审核 / 可见范围
def test_upload_pending_review_then_publish(client, login):
    emp, hr = login(EMP), login(HR)
    word = f"差旅补贴{uuid.uuid4().hex[:6]}"
    content = _docx(f"员工出差按{word}标准执行，住宿每晚不超过 400 元，伙食补助每天 100 元。",
                    heading="差旅费用管理办法")
    sug = data(client.post("/api/v1/kb/suggest", files={"file": ("差旅费用管理办法.docx", content)}, headers=emp))
    assert sug["category"] == "制度" and sug["subcategory"] == "财务制度"

    d = data(_upload(client, emp, content, "差旅费用管理办法.docx", category="制度", subcategory="财务制度",
                     tags="差旅,报销", visibility="all"))
    assert d["status"] == "pending" and d["tags"] == ["差旅", "报销"] and d["has_file"]
    assert d["chunks"] and d["chunks"][0]["heading"] == "差旅费用管理办法"
    # 未发布：检索不到；上传人能看到；无关的人 404
    assert not _found(client, hr, word, d["id"])
    assert client.get(f"/api/v1/kb/docs/{d['id']}", headers=login(KF)).json()["code"] == 40401
    mine = data(client.get("/api/v1/kb/docs", params={"scope": "mine"}, headers=emp))
    assert any(x["id"] == d["id"] for x in mine)
    # 制度分类的审核人是 HR：待审核列表 + 不能空理由驳回
    queue = data(client.get("/api/v1/kb/docs", params={"scope": "review"}, headers=hr))
    assert any(x["id"] == d["id"] for x in queue)
    assert not any(x["id"] == d["id"] for x in data(client.get("/api/v1/kb/docs", params={"scope": "review"}, headers=login(CP))))
    assert client.post(f"/api/v1/kb/docs/{d['id']}/review", json={"approve": False}, headers=hr).json()["code"] == 40001
    assert client.post(f"/api/v1/kb/docs/{d['id']}/review", json={"approve": True}, headers=login(CP)).json()["code"] == 40301
    pub = data(client.post(f"/api/v1/kb/docs/{d['id']}/review", json={"approve": True, "note": "ok"}, headers=hr))
    assert pub["status"] == "published"
    hits = data(client.get("/api/v1/kb/search", params={"q": word}, headers=login(KF)))["items"]
    assert hits[0]["doc_id"] == d["id"] and hits[0]["subcategory"] == "财务制度"
    # 原件可下载
    f = pub["file"]
    assert f and client.get(f["url"], headers=emp).status_code == 200
    assert client.get(f["url"]).status_code == 401
    # 重复上传同一文件
    assert _upload(client, emp, content, "副本.docx", category="制度").json()["code"] == 40901


def test_reviewer_upload_publishes_directly_and_dept_visibility(client, login):
    hr = login(HR)
    word = f"薪酬保密{uuid.uuid4().hex[:6]}"
    d = data(_upload(client, hr, _docx(f"{word}：各级岗位薪酬带宽与调薪规则，仅人事部内部使用。"), "薪酬带宽.docx",
                     category="制度", subcategory="人事制度", visibility="dept", visible_depts="RS"))
    assert d["status"] == "published" and d["visibility"] == "dept"
    assert _found(client, login("caofang"), word, d["id"])
    assert not _found(client, login(EMP), word, d["id"])
    assert client.get(f"/api/v1/kb/docs/{d['id']}", headers=login(EMP)).json()["code"] == 40401
    # 内部调用（viewer=None）只用全员可见文档
    from app.core.db import SessionLocal
    from app.services import kb_service
    with SessionLocal() as db:
        assert all(x["doc_id"] != d["id"] for x in kb_service.search(db, word, limit=50))
    # 部门可见必须选部门
    r = _upload(client, hr, _docx("另一份内部资料，内容不同。" + word), "x.docx", category="制度", visibility="dept")
    assert r.json()["code"] == 40001


def test_version_archive_restore_delete(client, login):
    cp, emp = login(CP), login(EMP)
    word = f"录播方案{uuid.uuid4().hex[:6]}"
    d = data(_upload(client, cp, _docx(f"{word} 第一版：录播教室建设方案。"), "录播方案.docx", category="售前",
                     subcategory="数字校园"))
    assert d["status"] == "published" and d["version"] == "v1"
    v2 = data(client.post(f"/api/v1/kb/docs/{d['id']}/version", files={"file": ("录播方案.docx", _docx(f"{word} 第二版：增加常态化录播。"))},
                          headers=cp))
    assert v2["version"] == "v2" and len(v2["versions"]) == 2 and "第二版" in v2["content_text"]
    assert v2["category"] == "售前" and v2["subcategory"] == "数字校园"
    assert client.post(f"/api/v1/kb/docs/{d['id']}/archive", json={"archived": True}, headers=emp).json()["code"] == 40301
    data(client.post(f"/api/v1/kb/docs/{d['id']}/archive", json={"archived": True}, headers=cp))
    assert not _found(client, cp, word, d["id"])
    assert client.request("DELETE", f"/api/v1/kb/docs/{d['id']}", headers=emp, json=deletion_body()).status_code in (403, 404)
    data(client.post(f"/api/v1/kb/docs/{d['id']}/archive", json={"archived": False}, headers=cp))
    assert _found(client, cp, word, d["id"])
    assert data(client.get(f"/api/v1/kb/docs/{d['id']}", headers=cp))["title"] == "录播方案"
    data(client.request("DELETE", f"/api/v1/kb/docs/{d['id']}", headers=cp, json=deletion_body()))
    assert client.get(f"/api/v1/kb/docs/{d['id']}", headers=cp).status_code == 404
    assert not _found(client, cp, word, d["id"])
    # 待审核文档上传人可删
    p = data(_upload(client, emp, _docx(f"{word} 员工整理的售前笔记。"), "笔记.docx", category="售前"))
    assert p["status"] == "pending"
    data(client.request("DELETE", f"/api/v1/kb/docs/{p['id']}", headers=emp, json=deletion_body()))


def test_extract_pdf_and_rejects(client, login):
    from app.core.response import BizError
    from app.services.kb_extract import extract_text
    assert "Campus network" in extract_text(_pdf("Campus network maintenance guide for schools, version 2026."), "a.pdf")
    for content, name, msg in [(_pdf(" "), "blank.pdf", "正文"), (_scan_pdf(), "scan.pdf", "多模态"),
                               (b"x" * 100, "old.doc", "docx"), (b"\x89PNG....", "a.png", "图片"),
                               (b"abc", "a.exe", "暂不支持")]:
        try:
            extract_text(content, name)
            raise AssertionError(name)
        except BizError as exc:
            assert msg in exc.message, (name, exc.message)


# ============================================================ 统一资料导入
def _import(client, h, content: bytes, name: str, purpose: str = ""):
    return client.post("/api/v1/imports", files={"file": (name, content)}, data={"purpose": purpose}, headers=h)


def test_import_document_routes_to_kb(client, login):
    emp = login(EMP)
    word = f"巡检规范{uuid.uuid4().hex[:6]}"
    content = _docx(f"{word}：机房每周巡检，检查 UPS、空调与交换机运行状态，并记录运维日志。")
    b = data(_import(client, emp, content, "机房运维巡检规范.docx"))
    assert b["kind"] == "document" and b["route"] == "kb" and b["status"] == "pending"
    assert b["suggestion"]["category"] == "客服" and "confirm_kb" in b["actions"]
    b = data(client.post(f"/api/v1/imports/{b['id']}/confirm-kb", json={"category": "客服", "subcategory": "运维规范"},
                         headers=emp))
    assert b["status"] == "done" and b["kb_doc"]["status"] == "pending"
    # 撤回：未发布的知识库文档被删除
    b = data(client.post(f"/api/v1/imports/{b['id']}/withdraw", headers=emp, json=deletion_body()))
    assert b["status"] == "withdrawn" and b["kb_doc"] is None
    # 撤回后可以重新上传同一文件
    assert _import(client, emp, content, "机房运维巡检规范.docx").json()["code"] == 0


def test_import_table_with_permission_goes_to_clean_and_rolls_back(client, login):
    yx = login(YX)
    tag = uuid.uuid4().hex[:6]
    content = _xlsx([["客户", "合同金额", "签约日期", "业务板块", "备注"],
                     [f"湛江某中学{tag}", "128000", "2026-09-15", "数字校园", "导入测试"],
                     [f"茂名某小学{tag}", "56000", "2026/9/20", "安防", "导入测试"]])
    b = data(_import(client, yx, content, "我的明细表.xlsx"))  # 文件名与用途都认不出 → 按表头识别
    assert b["route"] == "clean" and b["target"] == "contract" and "表头" in b["suggestion"]["reason"]
    job_id = b["clean_job"]["id"]
    job = data(client.post(f"/api/v1/clean/jobs/{job_id}/import", headers=yx))
    assert job["stats"]["imported"] == 2
    b = data(client.get(f"/api/v1/imports/{b['id']}", headers=yx))
    assert b["status"] == "done"
    rows = data(client.get("/api/v1/contracts", params={"keyword": tag}, headers=yx))
    assert rows["total"] == 2
    b = data(client.post(f"/api/v1/imports/{b['id']}/withdraw", headers=yx, json=deletion_body()))
    assert b["status"] == "withdrawn" and "2 条" in b["note"]
    assert data(client.get("/api/v1/contracts", params={"keyword": tag}, headers=yx))["total"] == 0


def test_import_rollback_refused_when_payment_exists(client, login, db):
    yx = login(YX)
    tag = uuid.uuid4().hex[:6]
    content = _xlsx([["合同编号", "客户名称", "合同金额", "签订日期", "业务线"],
                     ["", f"阳江某学校{tag}", "90000", "2026-08-01", "XY"]])
    b = data(_import(client, yx, content, "合同台账补录.xlsx", "导入合同台账"))
    job = data(client.post(f"/api/v1/clean/jobs/{b['clean_job']['id']}/import", headers=yx))
    no = next(r["_ref"] for r in job["rows"] if r.get("_imported"))
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import select

    from app.models import BizContract
    from app.services import payment_service
    c = db.scalar(select(BizContract).where(BizContract.contract_no == no))
    payment_service.register_payment(db, c, Decimal("1000"), date(2026, 9, 1))
    db.commit()
    r = client.post(f"/api/v1/imports/{b['id']}/withdraw", headers=yx, json=deletion_body()).json()
    assert r["code"] == 40901 and "回款" in r["message"]


def test_import_without_permission_goes_to_pool(client, login):
    emp, yx = login(EMP), login(YX)
    tag = uuid.uuid4().hex[:6]
    content = _xlsx([["商机名称", "客户名称", "业务线", "预计金额", "阶段"],
                     [f"智慧食堂{tag}", f"电白某中学{tag}", "安防", "300000", "方案报价"]])
    b = data(_import(client, emp, content, "跟进中的项目.xlsx", "我手上的商机"))
    assert b["route"] == "pool" and b["target"] == "opportunity" and "权限" in b["suggestion"]["reason"]
    assert client.get("/api/v1/imports", params={"scope": "pool"}, headers=emp).json()["code"] == 40301
    pool = data(client.get("/api/v1/imports", params={"scope": "pool"}, headers=yx))
    assert any(x["id"] == b["id"] for x in pool["items"]) and pool["is_handler"]
    b = data(client.post(f"/api/v1/imports/{b['id']}/to-clean", json={"target": "opportunity"}, headers=yx))
    assert b["route"] == "clean" and b["status"] == "pending" and b["clean_job"]
    # 上传人看得到进度
    mine = data(client.get("/api/v1/imports", headers=emp))
    assert next(x for x in mine["items"] if x["id"] == b["id"])["route"] == "clean"


def test_import_unknown_and_dismiss(client, login):
    emp, yx = login(EMP), login(YX)
    b = data(_import(client, emp, b"\x89PNG\r\n\x1a\n" + uuid.uuid4().bytes, "名片.png"))
    b = data(client.get(f"/api/v1/imports/{b['id']}", headers=emp))
    assert b["kind"] == "document" and b["status"] == "pool" and "图片" in b["suggestion"]["reason"]
    car = _xlsx([["日期", "车牌", "里程"], ["2026-10-01", "粤G12345", "120"]])
    b2 = data(_import(client, emp, car, "用车登记.xlsx"))
    assert b2["status"] == "pool" and "模板" in b2["suggestion"]["reason"]
    assert client.post(f"/api/v1/imports/{b['id']}/dismiss", json={"note": "已线下处理"}, headers=emp).json()["code"] == 40301
    assert client.post(f"/api/v1/imports/{b['id']}/dismiss", json={"note": ""}, headers=yx).json()["code"] == 40001
    r = data(client.post(f"/api/v1/imports/{b['id']}/dismiss", json={"note": "名片不入库"}, headers=yx))
    assert r["status"] == "dismissed" and r["handler"]
    # 表格也可以转为知识库资料
    r = data(client.post(f"/api/v1/imports/{b2['id']}/to-kb", headers=yx))
    assert r["route"] == "kb" and r["status"] == "pending"
    # 同一人重复上传同一文件
    assert _import(client, emp, car, "用车登记.xlsx").json()["code"] == 40901
    # 别人的导入记录看不到
    assert client.get(f"/api/v1/imports/{b2['id']}", headers=login(KF)).json()["code"] == 40401
