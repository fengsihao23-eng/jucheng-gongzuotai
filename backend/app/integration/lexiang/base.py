"""腾讯乐享适配器契约（PRD FR-06-01）。工作台只读：镜像 + 检索，写入维护在乐享端完成（FR-06-04）。

文档 DTO（dict）：
    {id, title, category, url, content, updated_at(datetime, tz-aware), version}
content 为纯文本，小标题用 Markdown 形式（"## 标题"）保留，便于分块与定位。
"""
from datetime import datetime
from typing import Protocol

CATEGORIES = ["制度", "售前", "方案模板", "案例", "资质", "客服", "产品"]


class LexiangProvider(Protocol):
    def list_categories(self) -> list[dict]: ...  # [{id, name, count}]

    def list_documents(self, updated_since: datetime | None = None) -> list[dict]: ...  # 不含 content 也可

    def get_document(self, doc_id: str) -> dict: ...
