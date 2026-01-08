import httpx
from openai import OpenAI
from facto.config import Config

class AIService:
    def __init__(self, config: Config):
        # Configure longer timeout for slow APIs
        self.client = OpenAI(
            api_key=config.deepseek_api_key,
            base_url=config.openai_base_url,
            timeout=httpx.Timeout(120.0, connect=30.0),  # 120s total, 30s connect
            max_retries=3
        )
        self.model_name = config.model_name

    def get_response(self, messages: list[dict]) -> str:
        """
        Sends the conversation history to the LLM and returns the content of the response.
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages
            )
            return response.choices[0].message.content
        except httpx.TimeoutException as e:
            raise RuntimeError(f"AI Service Timeout: The AI took too long to respond. Please try again.") from e
        except httpx.ConnectError as e:
            raise RuntimeError(f"AI Service Connection Error: Could not connect to AI service. Please try again.") from e
        except Exception as e:
            raise RuntimeError(f"AI Service Error: {e}") from e
