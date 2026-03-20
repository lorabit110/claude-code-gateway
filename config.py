import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    lark_app_id: str = field(default_factory=lambda: os.environ["LARK_APP_ID"])
    lark_app_secret: str = field(default_factory=lambda: os.environ["LARK_APP_SECRET"])
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
    )
    claude_max_turns: int = field(
        default_factory=lambda: int(os.getenv("CLAUDE_MAX_TURNS", "10"))
    )
    claude_timeout: int = field(
        default_factory=lambda: int(os.getenv("CLAUDE_TIMEOUT", "120"))
    )
    lark_domain: str = field(
        default_factory=lambda: os.getenv("LARK_DOMAIN", "https://open.larksuite.com")
    )
