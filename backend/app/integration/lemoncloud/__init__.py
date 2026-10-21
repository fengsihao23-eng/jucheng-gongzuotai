from app.core.config import settings
from app.integration.lemoncloud.base import LemonCloudProvider


def get_lemoncloud(db) -> LemonCloudProvider:
    from app.core.integration_config import refresh
    refresh()
    if settings.source_mode("lemoncloud") == "live":
        from app.integration.lemoncloud.live import LiveLemonCloud
        return LiveLemonCloud(db)
    from app.integration.lemoncloud.mock import MockLemonCloud
    return MockLemonCloud(db)
