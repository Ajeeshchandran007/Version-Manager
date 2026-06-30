# Core/openai_client.py
from openai import AsyncOpenAI
from Utils.utils import config_mtime, logger, load_config


class OpenAIClient:
    """Thin async wrapper around OpenAI chat completions."""

    def __init__(self):
        self._config_mtime = 0.0
        self._configure()

    def _configure(self) -> None:
        config = load_config()
        self.model = config.get("openai_model_name", "gpt-4o-mini")
        self.client = AsyncOpenAI(api_key=config["openai_api_key"])
        self._config_mtime = config_mtime()

    def _refresh_if_config_changed(self) -> None:
        current_mtime = config_mtime()
        if current_mtime and current_mtime != self._config_mtime:
            logger.info("OpenAIClient: config changed; refreshing model/API settings.")
            self._configure()

    async def extract(self, system_prompt: str, user_prompt: str) -> str | None:
        self._refresh_if_config_changed()
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return None
