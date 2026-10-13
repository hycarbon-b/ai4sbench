"""Shared FastAPI-Mail connection configuration."""

from fastapi_mail import ConnectionConfig

from src.core.config import Settings


class SmtpConfigurationError(RuntimeError):
    pass


def smtp_connection_config(settings: Settings) -> ConnectionConfig:
    if not settings.smtp_enabled:
        raise SmtpConfigurationError("SMTP delivery is disabled")
    if not settings.smtp_server or not settings.smtp_from:
        raise SmtpConfigurationError("SMTP server and sender are not configured")
    return ConnectionConfig(
        MAIL_USERNAME=settings.smtp_username,
        MAIL_PASSWORD=settings.smtp_password.get_secret_value() if settings.smtp_password else "",
        MAIL_FROM=settings.smtp_from,
        MAIL_PORT=settings.smtp_port,
        MAIL_SERVER=settings.smtp_server,
        MAIL_FROM_NAME=settings.smtp_from_name,
        MAIL_STARTTLS=settings.smtp_starttls,
        MAIL_SSL_TLS=settings.smtp_ssl_tls,
        USE_CREDENTIALS=bool(settings.smtp_username),
        VALIDATE_CERTS=settings.smtp_validate_certs,
        TIMEOUT=settings.smtp_timeout_seconds,
    )
