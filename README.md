# TranslatorAPI

Run the API with `fastapi dev main.py`.

Upload one file using `POST /upload?filename=page.png`, with
`Content-Type: application/octet-stream` and the file bytes as the request body.
For multiple files, create a ZIP and upload it as a single file. Multipart form
uploads are not supported. The maximum upload size is 100 MiB.

```powershell
curl.exe -X POST "http://127.0.0.1:8000/upload?filename=pages.zip" -H "Content-Type: application/octet-stream" --data-binary "@pages.zip"
```

The endpoint returns HTTP 201 with `upload_id`, `filename`, and `size_bytes`.
Files are streamed into `uploads/` under their generated upload ID. This endpoint
only stores files; extraction, OCR, and translation are not run yet.
