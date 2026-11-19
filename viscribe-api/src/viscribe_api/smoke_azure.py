import argparse
import io
import wave
from pathlib import Path

from viscribe_api.azure_clients import AzureCallError, AzureGateway
from viscribe_api.config import get_settings
from viscribe_api.db import make_session_factory


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe each HIVN Azure deployment once")
    parser.add_argument("--audio", type=Path, required=True, help="Synthetic PCM16 mono 24 kHz WAV")
    args = parser.parse_args()
    # Keep the probe finite and only use a supplied synthetic fixture.
    try:
        with args.audio.open("rb") as file:
            audio = file.read(24_000 * 2 * 15 + 4096)
            if file.read(1):
                raise ValueError("Probe audio must be at most 15 seconds")
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24_000):
                raise ValueError("Expected PCM16 mono 24 kHz WAV")
            if not 2400 <= wav.getnframes() <= 24_000 * 15:
                raise ValueError("Probe audio must be between 0.1 and 15 seconds")
            pcm = wav.readframes(wav.getnframes())
            if len(pcm) != wav.getnframes() * 2:
                raise ValueError("Truncated WAV audio")
    except (OSError, ValueError, EOFError, wave.Error) as error:
        parser.error(str(error))

    settings = get_settings()
    sessions = make_session_factory(settings)
    azure = AzureGateway(settings, sessions)
    checks = [
        (
            settings.azure_openai_chat_luna,
            lambda: azure.check_chat(settings.azure_openai_chat_luna, structured=True),
        ),
        (
            settings.azure_openai_chat_terra,
            lambda: azure.check_chat(settings.azure_openai_chat_terra, structured=False),
        ),
        (
            settings.azure_openai_embedding,
            lambda: azure.embed(["ViScribe test"], operation="smoke_embedding", attempts=1),
        ),
        (settings.azure_stt_transcribe, lambda: azure.check_file_stt(audio)),
        (settings.azure_stt_live_transcribe, lambda: azure.check_realtime_stt(pcm)),
    ]
    failures = 0
    try:
        for name, check in checks:
            try:
                check()
            except AzureCallError as error:
                failures += 1
                print(f"{name}: FAIL ({error.code})", flush=True)
            else:
                print(f"{name}: OK", flush=True)
    finally:
        azure.close()
        sessions.kw["bind"].dispose()
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
