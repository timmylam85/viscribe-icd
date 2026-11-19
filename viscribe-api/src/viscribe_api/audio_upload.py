from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from viscribe_api.audio import AudioError, Upload, inspect_upload

MULTIPART_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    },
}


class UploadTooLarge(MultiPartException):
    pass


async def limited_stream(request: Request, limit: int) -> AsyncIterator[bytes]:
    total = 0
    async for block in request.stream():
        total += len(block)
        if total > limit:
            raise UploadTooLarge("audio_too_large")
        yield block


async def parse_audio(request: Request) -> Upload:
    if (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
        != "multipart/form-data"
    ):
        raise HTTPException(415, "expected_multipart_audio")
    settings = request.app.state.settings
    limit = settings.audio_max_bytes + 16 * 1024
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            raise HTTPException(400, "invalid_content_length") from None
        if declared > limit:
            raise HTTPException(413, "audio_too_large")
    try:
        form = await MultiPartParser(
            request.headers, limited_stream(request, limit), max_files=1, max_fields=0
        ).parse()
    except UploadTooLarge:
        raise HTTPException(413, "audio_too_large") from None
    except MultiPartException:
        raise HTTPException(400, "invalid_audio_form") from None
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise HTTPException(422, "audio_file_required")
        return await run_in_threadpool(inspect_upload, upload.file, upload.filename, settings)
    except AudioError as error:
        raise HTTPException(error.status, error.code) from None
    finally:
        await form.close()
