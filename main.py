from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import FastAPI, HTTPException, Query, Request

app = FastAPI()
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post(
    "/upload",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_file(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
):
    """Save one raw file (up to 100 MiB). ZIP multiple files before uploading."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise HTTPException(415, "Send the raw file with Content-Type: application/octet-stream.")
    if filename in {".", ".."} or any(c in filename for c in '/\\\x00'):
        raise HTTPException(400, "Provide a filename, not a path.")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length.")
        if declared_size < 0:
            raise HTTPException(400, "Invalid Content-Length.")
        if declared_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File exceeds the 100 MiB limit.")

    upload_id = uuid4().hex
    # Never use a client-supplied filename as a filesystem path.
    destination = UPLOAD_DIR / upload_id
    await anyio.Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    size = 0
    completed = False
    try:
        async with await anyio.open_file(destination, "xb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File exceeds the 100 MiB limit.")
                await target.write(chunk)
            if size == 0:
                raise HTTPException(400, "File is empty.")
        completed = True
    finally:
        if not completed:
            with anyio.CancelScope(shield=True):
                await anyio.Path(destination).unlink(missing_ok=True)

    return {"upload_id": upload_id, "filename": filename, "size_bytes": size}
