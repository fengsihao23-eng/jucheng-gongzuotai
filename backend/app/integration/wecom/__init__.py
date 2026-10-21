from app.core.config import settings
from app.integration.wecom.base import WeComProvider
from app.integration.wecom.crypto import WeComCrypto


def get_wecom() -> WeComProvider:
    if settings.source_mode("wecom") == "live":
        from app.integration.wecom.live import LiveWeCom
        return LiveWeCom()
    from app.integration.wecom.mock import MockWeCom
    return MockWeCom()


def get_crypto() -> WeComCrypto:
    if settings.source_mode("wecom") == "live":
        return WeComCrypto(settings.wecom_callback_token, settings.wecom_callback_aes_key, settings.wecom_corp_id)
    from app.integration.wecom.mock import MOCK_AES_KEY, MOCK_CALLBACK_TOKEN, MOCK_CORP_ID
    return WeComCrypto(MOCK_CALLBACK_TOKEN, MOCK_AES_KEY, MOCK_CORP_ID)
