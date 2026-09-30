"""Persistent SQLite job queue and single-worker manga processing.

Run ``python worker.py --device cuda`` after implementing MagiService.
Call initialize_job_store() in each API process before using the queue.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from uuid import uuid4
import zipfile

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / 'jobs.sqlite3'
UPLOAD_DIR = BASE_DIR / 'uploads'
MAX_PAGES = 1000
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}


@contextmanager
def _connection():
    db = sqlite3.connect(DATABASE_PATH, timeout=30)
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize_job_store(database_path=DATABASE_PATH):
    """Select the database for this process and create persistent job storage."""
    global DATABASE_PATH
    DATABASE_PATH = Path(database_path).resolve()
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, upload_id TEXT NOT NULL,
            filename TEXT NOT NULL, status TEXT NOT NULL,
            completed_pages INTEGER NOT NULL DEFAULT 0,
            total_pages INTEGER NOT NULL DEFAULT 0,
            results TEXT NOT NULL DEFAULT '[]', error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')


def enqueue_job(upload_id, filename=None):
    """Queue an existing upload; return its ID. Optional filename is display metadata.

    Uploads have extensionless IDs, so file contents determine image versus ZIP.
    """
    if not re.fullmatch(r'[0-9a-f]{32}', upload_id):
        raise ValueError('Invalid upload ID')
    if not (UPLOAD_DIR / upload_id).is_file():
        raise FileNotFoundError('Upload does not exist')
    filename = filename or upload_id
    if not filename or any(c in filename for c in '/\\\x00'):
        raise ValueError('Filename must not contain a path')
    job_id = uuid4().hex
    with _connection() as db:
        db.execute('INSERT INTO jobs (job_id, upload_id, filename, status) VALUES (?, ?, ?, ?)',
                   (job_id, upload_id, filename, 'queued'))
    return job_id


def get_job(job_id):
    """Return status, progress and results, or None for an unknown job."""
    with _connection() as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['results'] = json.loads(result['results'])
    return result


def _page_order(name):
    return [(1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r'(\d+)', str(name))]


def _validate_image(path):
    from PIL import Image
    with Image.open(path) as image:
        if image.format not in {'PNG', 'JPEG', 'WEBP'}:
            raise ValueError('Only PNG, JPEG and WebP images are supported')
        if image.width * image.height > 40_000_000:
            raise ValueError('Image exceeds the 40 megapixel limit')
        image.verify()


def prepare_pages(upload_path, filename, output_dir):
    """Validate an image or extract ZIP/CBZ images in natural filename order.

    Generated output filenames prevent archive path traversal. Extraction is
    capped by page count and both declared and actually decompressed byte size.
    RAR is not supported by this upload worker; convert it to ZIP first.
    """
    upload_path = Path(upload_path)
    if not zipfile.is_zipfile(upload_path):
        _validate_image(upload_path)
        return [upload_path]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        with zipfile.ZipFile(upload_path) as archive:
            members = sorted((m for m in archive.infolist() if not m.is_dir()
                              and Path(m.filename).suffix.lower() in IMAGE_EXTENSIONS),
                             key=lambda m: _page_order(m.filename))
            if not members:
                raise ValueError('Archive contains no supported images')
            if len(members) > MAX_PAGES:
                raise ValueError('Archive contains too many pages')
            if sum(m.file_size for m in members) > MAX_EXTRACTED_BYTES:
                raise ValueError('Archive exceeds the extraction size limit')
            total = 0
            for index, member in enumerate(members):
                path = output_dir / f'{index + 1:05d}{Path(member.filename).suffix.lower()}'
                with path.open('xb') as target:
                    written.append(path)
                    with archive.open(member) as source:
                        while chunk := source.read(1024 * 1024):
                            total += len(chunk)
                            if total > MAX_EXTRACTED_BYTES:
                                raise ValueError('Archive exceeds the extraction size limit')
                            target.write(chunk)
                _validate_image(path)
        return written
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def _json_default(value):
    # Support NumPy arrays/scalars and tensors from model adapters.
    if hasattr(value, 'tolist'):
        return value.tolist()
    raise TypeError(f'Cannot serialize {type(value).__name__}')


def update_job(job_id, status, completed_pages=0, total_pages=0, results=None, error=None):
    """Persist progress and results; retain previous results when omitted."""
    if status not in {'queued', 'running', 'completed', 'failed'}:
        raise ValueError('Invalid job status')
    if not 0 <= completed_pages <= total_pages:
        raise ValueError('Invalid page progress')
    payload = None if results is None else json.dumps(results, default=_json_default)
    with _connection() as db:
        cursor = db.execute('''UPDATE jobs SET status=?, completed_pages=?, total_pages=?,
            results=COALESCE(?, results), error=?, updated_at=CURRENT_TIMESTAMP
            WHERE job_id=?''', (status, completed_pages, total_pages, payload, error, job_id))
        if cursor.rowcount != 1:
            raise KeyError(job_id)


def process_job(job_id, image_paths, service, ocr=None):
    """Detect and recognize one page at a time, saving partial results on failure.

    The service must return a dict containing ``texts`` (pixel xyxy boxes) and
    optionally ``text_character_associations`` (text-index, character-index pairs).
    All detection metadata is retained. Inject an OCR callable for testing or
    another language; otherwise OCR.get_text is loaded only when text is present.
    """
    from PIL import Image
    pages = list(image_paths)
    results = []
    update_job(job_id, 'running', total_pages=len(pages), results=[])
    try:
        if not pages:
            raise ValueError('Job has no pages')
        for index, path in enumerate(pages):
            detections = service.process_page(path)
            if not isinstance(detections, dict) or 'texts' not in detections:
                raise RuntimeError('MagiService.process_page must return a dict with texts; implement the model adapter first')
            speakers = dict(detections.get('text_character_associations', []))
            texts = []
            with Image.open(path) as original:
                image = original.convert('RGB')
                try:
                    for text_id, box in enumerate(detections['texts']):
                        if len(box) != 4:
                            raise ValueError('Text bounding box must have four coordinates')
                        x1, y1, x2, y2 = map(int, box)
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(image.width, x2), min(image.height, y2)
                        if x2 <= x1 or y2 <= y1:
                            raise ValueError('Invalid text bounding box')
                        if ocr is None:
                            from OCR import get_text
                            ocr = get_text
                        with image.crop((x1, y1, x2, y2)) as crop:
                            text = ocr(crop)
                        texts.append({'text_id': text_id, 'bbox': [x1, y1, x2, y2],
                                      'text': text, 'speaker_id': speakers.get(text_id)})
                finally:
                    image.close()
            results.append({'page_number': index + 1, 'filename': Path(path).name,
                            'detections': detections, 'texts': texts})
            update_job(job_id, 'running', len(results), len(pages), results)
        update_job(job_id, 'completed', len(results), len(pages), results)
    except Exception as error:
        update_job(job_id, 'failed', len(results), len(pages), results,
                   f'{type(error).__name__}: {error}')
    return get_job(job_id)


@contextmanager
def _worker_lock(database_path):
    """Hold an OS lock so only one worker can use this queue, even during inference."""
    import os
    with Path(str(database_path) + '.worker.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another worker is already running for this database') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _claim_job():
    with _connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT job_id FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET status='running', updated_at=CURRENT_TIMESTAMP WHERE job_id=?", row)
    return get_job(row[0]) if row else None


def run_worker(database_path=DATABASE_PATH, device='cpu', *, service=None, ocr=None,
               once=False, poll_interval=1.0):
    """Load one service and process queued jobs serially; --once drains then exits.

    Interrupted jobs are requeued on restart. Inference and OCR currently remain
    loaded together; separate low-memory stages can be added to the model adapter.
    """
    if poll_interval <= 0:
        raise ValueError('Poll interval must be positive')
    initialize_job_store(database_path)
    with _worker_lock(DATABASE_PATH):
        if service is None:
            from magi_service import MagiService
            service = MagiService(device=device)
        try:
            service.load_model()
            with _connection() as db:
                db.execute("UPDATE jobs SET status='queued', completed_pages=0, total_pages=0, results='[]', error=NULL WHERE status='running'")
            while True:
                job = _claim_job()
                if job is None:
                    if once:
                        break
                    time.sleep(poll_interval)
                    continue
                try:
                    with tempfile.TemporaryDirectory(prefix='magi-pages-') as directory:
                        pages = prepare_pages(UPLOAD_DIR / job['upload_id'], job['filename'], directory)
                        process_job(job['job_id'], pages, service, ocr=ocr)
                except Exception as error:
                    update_job(job['job_id'], 'failed', error=f'{type(error).__name__}: {error}')
        finally:
            service.unload_model()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=DATABASE_PATH)
    parser.add_argument('--device', choices=['cpu', 'cuda', 'xpu'], default='cpu')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        run_worker(args.database, args.device, once=args.once)
    except KeyboardInterrupt:
        pass
