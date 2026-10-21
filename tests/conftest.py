import pytest

# 起動用に export した環境変数(ALLOW_DEV_LOGIN など)がテスト結果に影響しないようにする
_APP_ENV_VARS = (
    "SECRET_KEY", "ADMIN_PASSWORD", "PLACE_SEARCH_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI", "ALLOWED_EMAIL_DOMAIN", "STAFF_EMAILS", "ALLOW_DEV_LOGIN",
)


@pytest.fixture(autouse=True)
def _clean_app_env(monkeypatch):
    for name in _APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
