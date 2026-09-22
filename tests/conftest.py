import pytest


@pytest.fixture(autouse=True)
def _isolate_user_settings(tmp_path, monkeypatch):
    """Testler kullanıcının gerçek settings.json ve .env dosyalarını yazmasın.

    Render/anahtar endpoint'lerini çağıran testler save_settings/save_api_key ile bu
    dosyaları güncelliyor ve kullanıcının seçtiği ses/motor ayarlarını sıfırlıyordu."""
    import app.config as config

    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
