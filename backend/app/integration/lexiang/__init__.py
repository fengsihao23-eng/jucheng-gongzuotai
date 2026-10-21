from app.core.config import settings
from app.integration.lexiang.base import CATEGORIES, LexiangProvider  # noqa: F401


def get_lexiang() -> LexiangProvider:
    if settings.source_mode("lexiang") == "live":
        from app.integration.lexiang.live import LiveLexiang
        return LiveLexiang()
    from app.integration.lexiang.mock import MockLexiang
    return MockLexiang()


def doc_url(doc_id: str) -> str:
    return f"{settings.lexiang_web_base.rstrip('/')}/teams/k100054/docs/{doc_id}"
