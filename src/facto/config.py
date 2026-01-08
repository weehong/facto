import os
from dataclasses import dataclass


@dataclass
class Config:
    telegram_bot_token: str
    deepseek_api_key: str
    openai_base_url: str
    model_name: str
    mongodb_uri: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TEST_FACTO_TOKEN") or os.getenv("FACTO_TOKEN")
        deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")

        if not token:
            raise ValueError("FACTO_TOKEN environment variable is required")
        if not deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY environment variable is required")

        return cls(
            telegram_bot_token=token,
            deepseek_api_key=deepseek_api_key,
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"),
            model_name=os.getenv("MODEL_NAME", "deepseek-chat"),
            mongodb_uri=os.getenv("MONGODB_URI", ""),
        )
