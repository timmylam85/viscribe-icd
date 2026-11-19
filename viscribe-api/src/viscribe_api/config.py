import re
from functools import lru_cache
from pathlib import Path
from typing import Self
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", env_file_encoding="utf-8", hide_input_in_errors=True
    )

    database_url: SecretStr = Field(validation_alias="VISCRIBE_DATABASE_URL")
    icd_catalog_version: str = Field(
        default="who2019-demo-v1", validation_alias="VISCRIBE_ICD_CATALOG_VERSION"
    )
    dev_users: dict[str, SecretStr] = Field(
        default_factory=dict, validation_alias="VISCRIBE_DEV_USERS"
    )

    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_chat_luna: str = "gpt-5.6-luna-1"
    azure_openai_chat_terra: str = "gpt-5.6-terra-1"
    azure_openai_embedding: str = "text-embedding-3-small"
    azure_stt_endpoint: str = ""
    azure_stt_api_key: SecretStr = SecretStr("")
    azure_stt_api_version: str = ""
    azure_stt_transcribe: str = "gpt-transcribe"
    azure_stt_realtime_url: str = ""
    azure_stt_live_transcribe: str = "gpt-live-transcribe"

    @model_validator(mode="after")
    def check_credentials_and_destinations(self) -> Self:
        tokens = [secret.get_secret_value() for secret in self.dev_users.values()]
        if len(tokens) != len(set(tokens)) or any(len(token) < 32 for token in tokens):
            raise ValueError(
                "Development bearer secrets must be distinct and at least 32 characters"
            )
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", actor) for actor in self.dev_users):
            raise ValueError("Development doctor IDs must be opaque identifiers")
        for value, scheme in (
            (self.azure_openai_endpoint, "https"),
            (self.azure_stt_endpoint, "https"),
            (self.azure_stt_realtime_url, "wss"),
        ):
            if not value:
                continue
            url = urlsplit(value)
            if (
                url.scheme != scheme
                or not (url.hostname or "").endswith(".openai.azure.com")
                or url.username is not None
                or url.password is not None
                or url.port not in (None, 443)
            ):
                raise ValueError("Azure destinations must use the Azure OpenAI resource domain")
        if self.azure_openai_endpoint:
            chat = urlsplit(self.azure_openai_endpoint)
            if chat.path.rstrip("/") != "/openai/v1" or chat.query or chat.fragment:
                raise ValueError("Chat and embeddings require the Azure OpenAI /openai/v1 endpoint")
        if self.azure_stt_endpoint:
            stt = urlsplit(self.azure_stt_endpoint)
            if stt.path not in ("", "/") or stt.query or stt.fragment:
                raise ValueError("File STT requires the base Azure resource endpoint")
        if self.azure_stt_realtime_url:
            realtime = urlsplit(self.azure_stt_realtime_url)
            query = parse_qs(realtime.query)
            if (
                realtime.hostname != urlsplit(self.azure_stt_endpoint).hostname
                or realtime.path != "/openai/v1/realtime"
                or realtime.fragment
                or query.get("intent") != ["transcription"]
                or query.get("deployment") != [self.azure_stt_live_transcribe]
            ):
                raise ValueError(
                    "Realtime STT must use the same STT resource and transcription intent"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    cwd = Path.cwd()
    env_file = cwd / "../.env" if cwd.name == "viscribe-api" else cwd / ".env"
    return Settings(_env_file=env_file)
