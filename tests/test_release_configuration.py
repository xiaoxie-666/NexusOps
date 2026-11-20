import pytest

from app.core.config import Settings, validate_runtime_configuration


def test_development_mode_allows_local_demo_credentials():
    settings = Settings(_env_file=None, app_env="development")
    validate_runtime_configuration(settings)


def test_production_rejects_demo_credentials():
    settings = Settings(_env_file=None, app_env="production")
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        validate_runtime_configuration(settings)


def test_production_requires_webhook_token():
    settings = Settings(
        _env_file=None,
        app_env="production",
        bootstrap_admin_password="admin-password-unique-001",
        bootstrap_engineer_password="engineer-password-unique-002",
        bootstrap_oncall_password="oncall-password-unique-003",
        mysql_root_password="mysql-root-password-unique-004",
        mysql_password="mysql-app-password-unique-005",
        database_url="mysql+pymysql://nexusops:mysql-app-password-unique-005@mysql:3306/nexusops",
        alert_webhook_token="",
    )
    with pytest.raises(RuntimeError, match="ALERT_WEBHOOK_TOKEN"):
        validate_runtime_configuration(settings)


def test_production_accepts_unique_strong_credentials():
    settings = Settings(
        _env_file=None,
        app_env="production",
        bootstrap_admin_password="admin-password-unique-001",
        bootstrap_engineer_password="engineer-password-unique-002",
        bootstrap_oncall_password="oncall-password-unique-003",
        mysql_root_password="mysql-root-password-unique-004",
        mysql_password="mysql-app-password-unique-005",
        database_url="mysql+pymysql://nexusops:mysql-app-password-unique-005@mysql:3306/nexusops",
        alert_webhook_token="webhook-token-unique-value-with-32-plus-characters",
    )
    validate_runtime_configuration(settings)
