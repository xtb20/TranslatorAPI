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

## Processing worker

The queue and worker are implemented in `worker.py`. MAGI's service is still a
scaffold: implement `MagiService.load_model()` and `process_page()` before real
inference. The worker expects a page dictionary with `texts` as pixel-coordinate
bounding boxes and optional `text_character_associations` pairs. It retains other
detection metadata and attaches OCR text to each region.

Queue a saved upload from Python (HTTP job endpoints are not implemented yet):

```python
from worker import initialize_job_store, enqueue_job, get_job

initialize_job_store()
job_id = enqueue_job("UPLOAD_ID_FROM_RESPONSE", filename="pages.zip")
print(get_job(job_id))
```

Start a worker using the same database:

```text
python worker.py --device cuda
```

Use `--device cpu` or `--device xpu` when supported by the implemented model
service. `--once` drains the current queue and exits. `--database PATH` selects a
different SQLite database. The worker requeues interrupted jobs after restart,
processes one job at a time, and saves partial page results if a job fails.

Images may be PNG, JPEG, or WebP; batches must be ZIP/CBZ. Archives are limited to
1,000 images and 512 MiB of extracted image data, with a 40-megapixel per-image
limit. Extracted files use generated names and are removed after each job.
Original uploads remain on disk. MAGI and OCR currently remain loaded together;
the laptop memory footprint has not been measured.

Run worker tests with `python -m unittest test_worker -v`. Tests use a fake model
and OCR callable, requiring Pillow but no GPU or downloaded model.
