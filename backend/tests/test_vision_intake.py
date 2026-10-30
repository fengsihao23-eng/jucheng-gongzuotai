"""扫描件/图片：用多模态模型逐页转录，管理员能处理同事的资料，确认入库不重复调用模型。"""
import io
import uuid

import pytest
from PIL import Image, ImageDraw

from app.core.response import BizError
from app.integration import llm
from app.services.kb_extract import extract_document, needs_vision
from tests.conftest import data
from tests.test_kb_imports import _found, _import, _pdf, _scan_pdf

OWNER, ADMIN, STRANGER = "zengli", "admin", "wujun"


class FakeVision:
    """按页返回固定转录；fail_page 可模拟某一页始终失败。"""

    def __init__(self):
        self.calls: list[str] = []
        self.text = "智慧校园设备采购合同\n合同编号：HT-2026-0731\n合同金额：￥128,600.00"
        self.fail_page: int | None = None

    def __call__(self, system, prompt, image, **kwargs):
        self.calls.append(prompt)
        if self.fail_page and f"第 {self.fail_page} 页" in prompt:
            return None
        return self.text


@pytest.fixture
def vision(monkeypatch):
    fake = FakeVision()
    state = {"on": True}
    monkeypatch.setattr(llm, "vision_enabled", lambda: state["on"])
    monkeypatch.setattr(llm, "vision", fake)
    return fake, state


def _png() -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (600, 500), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, 560, 460], outline="black", width=4)
    d.rectangle([70, 90, 530, 140], fill="black")
    im.save(buf, "PNG")
    return buf.getvalue()


def test_scanned_pdf_vision_transcription_is_cached_and_admin_can_publish(client, login, vision):
    fake, _ = vision
    token = f"VISION{uuid.uuid4().hex[:6]}"
    fake.text = f"{token} 智慧校园设备采购合同\n合同编号：{token}\n合同金额：￥128,600.00"
    owner, admin, stranger = login(OWNER), login(ADMIN), login(STRANGER)

    created = data(_import(client, owner, _scan_pdf(), "扫描合同.pdf"))
    assert created["status"] == "processing"  # 扫描件先返回已保存，不阻塞上传请求
    b = data(client.get(f"/api/v1/imports/{created['id']}", headers=owner))
    assert b["kind"] == "document" and b["route"] == "kb" and b["status"] == "pending"
    assert b["suggestion"]["extract"]["method"] == "vision"
    assert b["suggestion"]["extract"]["pages"] == 1 and b["content_chars"] > 0
    assert token in b["suggestion"]["preview"]
    calls = len(fake.calls)

    # 管理员能处理其他同事的扫描件；未授权的同事看不到正文。
    detail = data(client.get(f"/api/v1/imports/{b['id']}", headers=admin))
    assert "confirm_kb" in detail["actions"]
    assert client.get(f"/api/v1/imports/{b['id']}/text", headers=stranger).status_code == 404
    assert token in data(client.get(f"/api/v1/imports/{b['id']}/text", headers=admin))["text"]

    done = data(client.post(f"/api/v1/imports/{b['id']}/confirm-kb", headers=admin,
                            json={"category": "客服", "visibility": "all"}))
    assert done["status"] == "done" and done["kb_doc"]["status"] == "published"
    assert len(fake.calls) == calls  # 确认入库复用识别结果，不重复逐页调用模型
    assert _found(client, admin, token, done["kb_doc"]["id"])


def test_pool_scan_is_handled_by_admin_through_background_recognition(client, login, vision):
    fake, state = vision
    owner, admin = login(OWNER), login(ADMIN)

    state["on"] = False  # 模拟生产上还没配置视觉模型时，扫描件先落到待整理池
    created = data(_import(client, owner, _scan_pdf(), "旧扫描件.pdf"))
    pooled = data(client.get(f"/api/v1/imports/{created['id']}", headers=owner))
    assert pooled["status"] == "pool" and "多模态" in pooled["suggestion"]["reason"]
    assert "to_kb" in pooled["actions"]
    refused = client.post(f"/api/v1/imports/{pooled['id']}/to-kb", headers=admin).json()
    assert refused["code"] == 40001 and "视觉模型" in refused["message"]
    assert data(client.get(f"/api/v1/imports/{pooled['id']}", headers=admin))["status"] == "pool"

    state["on"] = True
    queued = data(client.post(f"/api/v1/imports/{pooled['id']}/to-kb", headers=admin))
    assert queued["status"] == "processing"  # 不再同步等模型，避免 180 秒网关超时
    final = data(client.get(f"/api/v1/imports/{pooled['id']}", headers=admin))
    assert final["status"] == "pending" and final["suggestion"]["extract"]["method"] == "vision"
    assert fake.calls and final["content_chars"] > 0


def test_image_upload_is_transcribed_as_document(client, login, vision):
    owner = login(OWNER)
    created = data(_import(client, owner, _png(), "合同照片.png"))
    b = data(client.get(f"/api/v1/imports/{created['id']}", headers=owner))
    assert b["kind"] == "document" and b["status"] == "pending"
    assert b["suggestion"]["extract"]["method"] == "vision" and b["suggestion"]["extract"]["pages"] == 1


def test_vision_page_limit_refuses_instead_of_truncating(monkeypatch, vision):
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_vision_max_pages", 1)
    with pytest.raises(BizError, match="上限"):
        extract_document(_scan_pdf(pages=2), "两页扫描件.pdf")


def test_one_failed_page_rejects_the_whole_document(vision):
    fake, _ = vision
    fake.fail_page = 2
    with pytest.raises(BizError, match="第 2 页"):
        extract_document(_scan_pdf(pages=2), "两页扫描件.pdf")


def test_text_pdf_does_not_need_vision():
    assert not needs_vision(_pdf("Campus network maintenance guide for schools, version 2026."), "guide.pdf")
    assert needs_vision(_scan_pdf(), "scan.pdf")
