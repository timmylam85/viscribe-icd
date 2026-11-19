import hashlib
import io
import json
import math
import subprocess
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from viscribe_api.config import Settings

FORMATS = {"wav": "wav", "mp3": "mp3", "m4a": "mov", "mp4": "mov", "webm": "matroska"}
RATE = 16_000


class AudioError(Exception):
    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Upload:
    content: bytes
    format: str
    duration_ms: int
    sha256: str


@dataclass(frozen=True)
class Chunk:
    start_ms: int
    end_ms: int
    wav: bytes


def input_options(source: Path, extension: str) -> list[str]:
    demuxer = FORMATS[extension]
    options = ["-protocol_whitelist", "file", "-f", demuxer]
    if demuxer == "mov":
        options.extend(["-enable_drefs", "0"])
    return [*options, "-i", str(source)]


def media_command(args: list[str], timeout: int) -> bytes:
    try:
        result = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout
        )
    except FileNotFoundError:
        raise AudioError("media_tool_unavailable", 503) from None
    except subprocess.TimeoutExpired:
        raise AudioError("audio_decode_timeout") from None
    if result.returncode:
        raise AudioError("invalid_audio")
    return result.stdout


def normalize_audio(source: Path, target: Path, extension: str, maximum: int) -> None:
    media_command(
        [
            "ffmpeg",
            "-nostdin",
            "-xerror",
            *input_options(source, extension),
            "-map",
            "0:a:0",
            "-t",
            str(maximum + 1),
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        timeout=45,
    )
    with wave.open(str(target), "rb") as normalized:
        frames = normalized.getnframes()
        if frames > maximum * RATE:
            raise AudioError("audio_too_long", 413)
        if frames < RATE // 10:
            raise AudioError("audio_too_short")


def inspect_upload(file: BinaryIO, filename: str, settings: Settings) -> Upload:
    extension = Path(filename).suffix.lower().removeprefix(".")
    if extension not in FORMATS:
        raise AudioError("unsupported_audio_format", 415)
    content = file.read(settings.audio_max_bytes + 1)
    if len(content) > settings.audio_max_bytes:
        raise AudioError("audio_too_large", 413)
    if not content:
        raise AudioError("empty_audio")
    with TemporaryDirectory(prefix="viscribe-audio-") as work:
        source = Path(work) / "source.bin"
        source.write_bytes(content)
        metadata = json.loads(
            media_command(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    *input_options(source, extension),
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=duration:format=duration",
                    "-of",
                    "json",
                ],
                timeout=12,
            )
        )
        if not metadata.get("streams"):
            raise AudioError("invalid_audio")
        duration = 0.0
        for entry in (metadata["streams"][0], metadata.get("format", {})):
            try:
                candidate = float(entry.get("duration", "nan"))
            except ValueError:
                continue
            if math.isfinite(candidate) and candidate > 0:
                duration = candidate
                break
        if duration == 0:
            # WebM from MediaRecorder may not advertise a duration in its headers.
            normalized = Path(work) / "normalized.wav"
            normalize_audio(source, normalized, extension, settings.audio_max_seconds)
            with wave.open(str(normalized), "rb") as decoded:
                duration = decoded.getnframes() / RATE
    if duration > settings.audio_max_seconds:
        raise AudioError("audio_too_long", 413)
    if duration < 0.1:
        raise AudioError("audio_too_short")
    return Upload(content, extension, round(duration * 1000), hashlib.sha256(content).hexdigest())


def chunk_count(file: wave.Wave_read, seconds: int) -> int:
    return math.ceil(file.getnframes() / (RATE * seconds))


def read_chunks(file: wave.Wave_read, seconds: int, start_index: int) -> Iterator[Chunk]:
    frames_per_chunk = RATE * seconds
    file.setpos(min(start_index * frames_per_chunk, file.getnframes()))
    while file.tell() < file.getnframes():
        start_frame = file.tell()
        pcm = file.readframes(frames_per_chunk)
        if not pcm:
            raise AudioError("invalid_audio")
        with io.BytesIO() as raw:
            with wave.open(raw, "wb") as output:
                output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                output.writeframes(pcm)
            yield Chunk(
                round(start_frame * 1000 / RATE), round(file.tell() * 1000 / RATE), raw.getvalue()
            )
