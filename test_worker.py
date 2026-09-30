import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from PIL import Image
import worker


class FakeService:
    def load_model(self):
        self.loaded = True

    def unload_model(self):
        self.loaded = False

    def process_page(self, path):
        return {'texts': [[0, 0, 8, 8]], 'text_character_associations': [[0, 3]]}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_database = worker.DATABASE_PATH
        self.uploads = patch.object(worker, 'UPLOAD_DIR', self.root)
        self.uploads.start()
        worker.initialize_job_store(self.root / 'jobs.sqlite3')

    def tearDown(self):
        self.uploads.stop()
        worker.DATABASE_PATH = self.old_database
        self.temp.cleanup()

    def image(self):
        path = self.root / uuid4().hex
        Image.new('RGB', (10, 10), 'white').save(path, format='PNG')
        return path

    def test_queue_worker_and_restart_recovery(self):
        path = self.image()
        job_id = worker.enqueue_job(path.name, 'page.png')
        worker.update_job(job_id, 'running')  # Simulate an interrupted worker.
        service = FakeService()
        worker.run_worker(worker.DATABASE_PATH, service=service, ocr=lambda crop: 'hello', once=True)
        job = worker.get_job(job_id)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['completed_pages'], 1)
        self.assertEqual(job['results'][0]['texts'][0]['speaker_id'], 3)
        self.assertEqual(job['results'][0]['texts'][0]['text'], 'hello')
        self.assertFalse(service.loaded)
        self.assertIsNone(worker.get_job('missing'))

    def test_partial_failure_is_persisted(self):
        path = self.image()
        job_id = worker.enqueue_job(path.name)
        job = worker.process_job(job_id, [path, self.root / 'missing.png'], FakeService(), ocr=lambda crop: 'ok')
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['completed_pages'], 1)
        self.assertEqual(len(job['results']), 1)

    def test_zip_order_and_safe_output_names(self):
        image = self.image()
        archive = self.root / 'pages.zip'
        with ZipFile(archive, 'w') as z:
            z.writestr('../page10.png', image.read_bytes())
            z.writestr('page2.png', image.read_bytes())
        paths = worker.prepare_pages(archive, 'pages.zip', self.root / 'out')
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(p.parent == self.root / 'out' for p in paths))
        self.assertLess(worker._page_order('page2.png'), worker._page_order('page10.png'))
        with patch.object(worker, 'MAX_EXTRACTED_BYTES', 1):
            with self.assertRaises(ValueError):
                worker.prepare_pages(archive, 'pages.zip', self.root / 'limited')

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            worker.enqueue_job('../bad')
        bad = self.root / uuid4().hex
        bad.write_bytes(b'not an image')
        with self.assertRaises(Exception):
            worker.prepare_pages(bad, 'page.png', self.root / 'out')
        with self.assertRaises(KeyError):
            worker.update_job('missing', 'failed')

    def test_stub_model_does_not_succeed(self):
        from magi_service import MagiService
        path = self.image()
        job_id = worker.enqueue_job(path.name)
        job = worker.process_job(job_id, [path], MagiService())
        self.assertEqual(job['status'], 'failed')
        self.assertIn('implement the model adapter', job['error'])

    def test_only_one_worker_lock(self):
        with worker._worker_lock(worker.DATABASE_PATH):
            with self.assertRaises(RuntimeError):
                with worker._worker_lock(worker.DATABASE_PATH):
                    pass


if __name__ == '__main__':
    unittest.main()
