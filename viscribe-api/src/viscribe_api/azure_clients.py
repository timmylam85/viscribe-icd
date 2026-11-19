import base64
import json
import math
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from uuid import UUID

from openai import APIConnectionError, APIStatusError, AzureOpenAI, OpenAI
from sqlalchemy.orm import Session, sessionmaker
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import ClientConnection, connect

from viscribe_api.config import Settings
from viscribe_api.models import EMBEDDING_DIMENSIONS, AIRun


class AzureCallError(RuntimeError):
    def __init__(self, code: str, *, transient: bool = False, retry_after: float = 0):
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.retry_after = retry_after


@dataclass(frozen=True)
class Result[T]:
    value: T
    tokens_in: int | None = None
    tokens_out: int | None = None


def retry_delay(value: str | None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def status_error(status: int, retry_after: str | None) -> AzureCallError:
    return AzureCallError(
        f"http_{status}",
        transient=status in {408, 429, 500, 502, 503, 504},
        retry_after=retry_delay(retry_after),
    )


def call_error(error: Exception) -> AzureCallError:
    if isinstance(error, AzureCallError):
        return error
    if isinstance(error, APIStatusError):
        return status_error(error.status_code, error.response.headers.get("retry-after"))
    if isinstance(error, InvalidStatus):
        return status_error(error.response.status_code, error.response.headers.get("retry-after"))
    if isinstance(error, (APIConnectionError, ConnectionClosed, TimeoutError, OSError)):
        return AzureCallError("connection_or_timeout", transient=True)
    return AzureCallError("invalid_provider_response")


class AzureGateway:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session]):
        self.settings = settings
        self.sessions = sessions
        self.chat = (
            OpenAI(
                base_url=settings.azure_openai_endpoint.rstrip("/") + "/",
                api_key=settings.azure_openai_api_key.get_secret_value(),
                timeout=30,
                max_retries=0,
            )
            if settings.azure_openai_endpoint and settings.azure_openai_api_key.get_secret_value()
            else None
        )
        self.stt = (
            AzureOpenAI(
                azure_endpoint=settings.azure_stt_endpoint,
                api_key=settings.azure_stt_api_key.get_secret_value(),
                api_version=settings.azure_stt_api_version,
                timeout=30,
                max_retries=0,
            )
            if settings.azure_stt_endpoint
            and settings.azure_stt_api_key.get_secret_value()
            and settings.azure_stt_api_version
            else None
        )

    @property
    def chat_configured(self) -> bool:
        return self.chat is not None

    @property
    def file_stt_configured(self) -> bool:
        return self.stt is not None

    def close(self) -> None:
        for client in (self.chat, self.stt):
            if client is not None:
                client.close()

    def invoke[T](
        self,
        operation: str,
        deployment: str,
        call: Callable[[], Result[T]],
        *,
        attempts: int = 3,
        encounter_id: UUID | None = None,
        attempt_start: int = 1,
    ) -> T:
        for attempt in range(1, attempts + 1):
            start = time.monotonic()
            try:
                result = call()
            except (
                AzureCallError,
                APIConnectionError,
                APIStatusError,
                WebSocketException,
                TimeoutError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
            ) as error:
                failure = call_error(error)
                self.record(
                    operation,
                    deployment,
                    attempt_start + attempt - 1,
                    start,
                    error=failure,
                    encounter_id=encounter_id,
                )
                # Leave longer cooldowns to the caller rather than block a command or API request.
                if not failure.transient or attempt == attempts or failure.retry_after > 30:
                    raise failure from None
                time.sleep(max(failure.retry_after, 2 ** (attempt - 1) + random.random()))
            else:
                self.record(
                    operation,
                    deployment,
                    attempt_start + attempt - 1,
                    start,
                    result=result,
                    encounter_id=encounter_id,
                )
                return result.value
        raise ValueError("attempts must be positive")

    def record(
        self,
        operation: str,
        deployment: str,
        attempt: int,
        start: float,
        *,
        result: Result | None = None,
        error: AzureCallError | None = None,
        encounter_id: UUID | None = None,
    ) -> None:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        with self.sessions.begin() as session:
            session.add(
                AIRun(
                    encounter_id=encounter_id,
                    operation=operation,
                    deployment=deployment,
                    attempt=attempt,
                    status="failed" if error else "success",
                    error_code=error.code if error else None,
                    latency_ms=elapsed_ms,
                    tokens_in=result.tokens_in if result else None,
                    tokens_out=result.tokens_out if result else None,
                )
            )

    def embed(self, texts: list[str], *, operation: str, attempts: int = 3) -> list[list[float]]:
        if self.chat is None:
            raise AzureCallError("chat_resource_not_configured")
        client = self.chat
        deployment = self.settings.azure_openai_embedding

        def request() -> Result[list[list[float]]]:
            response = client.embeddings.create(model=deployment, input=texts)
            data = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in data] != list(range(len(texts))):
                raise AzureCallError("invalid_embedding_indices")
            vectors = [item.embedding for item in data]
            if any(
                len(vector) != EMBEDDING_DIMENSIONS
                or not all(math.isfinite(value) for value in vector)
                or not any(vector)
                for vector in vectors
            ):
                raise AzureCallError("invalid_embedding_vector")
            return Result(vectors, tokens_in=response.usage.prompt_tokens)

        return self.invoke(operation, deployment, request, attempts=attempts)

    def check_chat(self, deployment: str, *, structured: bool) -> None:
        if self.chat is None:
            raise AzureCallError("chat_resource_not_configured")
        client = self.chat

        def request() -> Result[None]:
            options = {}
            prompt = "This is a connectivity test. Reply with OK."
            if structured:
                prompt = 'This is a connectivity test. Return JSON {"ok":true}.'
                options["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "viscribe_smoke_test",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                            "additionalProperties": False,
                        },
                    },
                }
            response = client.chat.completions.create(
                model=deployment,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=384,
                **options,
            )
            content = response.choices[0].message.content if response.choices else None
            if not content or (structured and json.loads(content) != {"ok": True}):
                raise AzureCallError("empty_or_invalid_chat_output")
            usage = response.usage
            return Result(
                None,
                usage.prompt_tokens if usage else None,
                usage.completion_tokens if usage else None,
            )

        self.invoke("smoke_chat", deployment, request, attempts=1)

    def check_file_stt(self, audio: bytes) -> None:
        self.transcribe_file(audio, require_text=True, operation="smoke_file_stt")

    def transcribe_file(
        self,
        audio: bytes,
        *,
        encounter_id: UUID | None = None,
        attempt: int = 1,
        require_text: bool = False,
        operation: str = "transcribe_file",
    ) -> str:
        if self.stt is None:
            raise AzureCallError("file_stt_resource_not_configured")
        client = self.stt
        deployment = self.settings.azure_stt_transcribe

        def request() -> Result[str]:
            response = client.audio.transcriptions.create(
                model=deployment,
                file=("synthetic.wav", audio, "audio/wav"),
                language="vi",
            )
            text = response.text.strip()
            if require_text and not text:
                raise AzureCallError("empty_file_transcript")
            usage = response.usage
            return Result(
                text, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
            )

        return self.invoke(
            operation,
            deployment,
            request,
            attempts=1,
            encounter_id=encounter_id,
            attempt_start=attempt,
        )

    def check_realtime_stt(self, pcm: bytes) -> None:
        url = self.settings.azure_stt_realtime_url
        key = self.settings.azure_stt_api_key.get_secret_value()
        deployment = self.settings.azure_stt_live_transcribe
        if not url or not key:
            raise AzureCallError("realtime_stt_resource_not_configured")

        def request() -> Result[None]:
            with connect(
                url, additional_headers={"api-key": key}, open_timeout=10, close_timeout=2
            ) as socket:
                deadline = time.monotonic() + 25
                socket.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": {
                                "type": "transcription",
                                "audio": {
                                    "input": {
                                        "format": {"type": "audio/pcm", "rate": 24000},
                                        "transcription": {"model": deployment, "language": "vi"},
                                        "turn_detection": None,
                                    }
                                },
                            },
                        }
                    )
                )
                receive_event(socket, deadline, "session.updated")
                for offset in range(0, len(pcm), 9600):
                    chunk = pcm[offset : offset + 9600]
                    socket.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(chunk).decode("ascii"),
                            }
                        )
                    )
                socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                event = receive_event(
                    socket, deadline, "conversation.item.input_audio_transcription.completed"
                )
                if not event.get("transcript", "").strip():
                    raise AzureCallError("empty_realtime_transcript")
                usage = event.get("usage") or {}
                return Result(None, usage.get("input_tokens"), usage.get("output_tokens"))

        self.invoke("smoke_realtime_stt", deployment, request, attempts=1)


def receive_event(socket: ClientConnection, deadline: float, expected: str) -> dict:
    while (remaining := deadline - time.monotonic()) > 0:
        event = json.loads(socket.recv(timeout=remaining))
        if event["type"] in {"error", "conversation.item.input_audio_transcription.failed"}:
            code = (event.get("error") or {}).get("code") or "error"
            safe_code = code if re.fullmatch(r"[a-z_0-9]{1,64}", code) else "error"
            raise AzureCallError(f"realtime_{safe_code}")
        if event["type"] == expected:
            return event
    raise TimeoutError()
