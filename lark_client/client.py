import lark_oapi as lark

from config import Config


def create_client(config: Config) -> lark.Client:
    """Create a Lark API client singleton."""
    return (
        lark.Client.builder()
        .app_id(config.lark_app_id)
        .app_secret(config.lark_app_secret)
        .domain(config.lark_domain)
        .log_level(lark.LogLevel.INFO)
        .build()
    )
