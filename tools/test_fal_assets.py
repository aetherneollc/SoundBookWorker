"""Ledger, cache, budget, and download tests. No real fal API calls."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fal_client import Completed, InProgress

from tools.fal_assets import (
    BudgetExceededError,
    MaxAttemptsError,
    PollTimeoutError,
    PricingUnavailableError,
    TRELLIS_ENDPOINT,
    generate_hunyuan3d,
    generate_rig,
    generate_trellis,
)


class _StreamResponse:
    def __init__(self, content: bytes):
        self.status_code = 200
        self._content = content

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=1024 * 1024):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FalAssetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.assets = self.root / 'assets3d'
        self.ledger = self.assets / 'ledger.json'
        self._saved_key = os.environ.get('FAL_KEY')
        os.environ['FAL_KEY'] = 'unit-test-placeholder'
        self.uploads = []
        self.submits = []
        self.statuses = []
        self.results = []
        self.gets = []
        self.streams = []
        self.price = Decimal('0.02')
        self.pricing_mode = 'ok'
        self.glb_bytes = b'glb-unit-test-mesh'
        self.downloads = {}
        self.status_queue = []
        self.result_payload = {
            'model_mesh': {'url': 'https://example.invalid/model.glb', 'file_name': 'model.glb'},
        }
        self.assert_submitted = True
        self.patches = [
            mock.patch('tools.fal_assets.load_dotenv', side_effect=AssertionError('dotenv')),
            mock.patch('tools.fal_assets.fal_client.upload_file', side_effect=self._upload),
            mock.patch('tools.fal_assets.fal_client.submit', side_effect=self._submit),
            mock.patch('tools.fal_assets.fal_client.status', side_effect=self._status),
            mock.patch('tools.fal_assets.fal_client.result', side_effect=self._result),
            mock.patch('tools.fal_assets.httpx.get', side_effect=self._get),
            mock.patch('tools.fal_assets.httpx.stream', side_effect=self._stream),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        if self._saved_key is None:
            os.environ.pop('FAL_KEY', None)
        else:
            os.environ['FAL_KEY'] = self._saved_key
        self.tmp.cleanup()

    def options(self, **extra):
        base = {
            'budget_micro_usd': 5_000_000,
            'max_attempts': 2,
            'ledger_path': self.ledger,
            'assets_dir': self.assets,
            'timeout_s': 30,
            'poll_interval_s': 0,
        }
        base.update(extra)
        return base

    def write_file(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def write_ledger(self, entries: list[dict]) -> None:
        self.assets.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(
            json.dumps({'version': 1, 'entries': entries}, indent=2),
            encoding='utf-8',
        )

    def ledger_text(self) -> str:
        return self.ledger.read_text(encoding='utf-8')

    def assert_ledger_has_no_secret(self):
        if not self.ledger.is_file():
            return
        text = self.ledger_text()
        self.assertNotIn('unit-test-placeholder', text)
        self.assertNotIn('FAL_KEY', text)
        self.assertNotIn('Authorization', text)
        key = self._saved_key
        if key and len(key) >= 12 and key in text:
            self.fail('ledger contains FAL_KEY')

    def _upload(self, path, **_kwargs):
        self.uploads.append(str(path))
        return 'https://example.invalid/uploads/' + Path(path).name

    def _submit(self, endpoint, arguments, **_kwargs):
        self.submits.append((endpoint, arguments))
        handle = mock.Mock()
        handle.request_id = 'req-%d' % len(self.submits)
        return handle

    def _status(self, endpoint, request_id, **_kwargs):
        self.statuses.append((endpoint, request_id))
        if self.assert_submitted:
            book = json.loads(self.ledger_text())
            entry = book['entries'][-1]
            if entry.get('status') != 'SUBMITTED' or not entry.get('request_id'):
                raise AssertionError('ledger was not persisted before polling')
            if entry.get('request_id') != request_id:
                raise AssertionError('polling a different request than the ledger')
        if self.status_queue:
            return self.status_queue.pop(0)
        return Completed(logs=None, metrics={})

    def _result(self, endpoint, request_id):
        self.results.append((endpoint, request_id))
        return self.result_payload

    def _get(self, url, **kwargs):
        self.gets.append(str(url))
        if 'api.fal.ai/v1/models/pricing' not in str(url):
            raise AssertionError('unexpected HTTP GET')
        params = kwargs.get('params') or {}
        if 'endpoint_id' not in params:
            raise AssertionError('pricing endpoint_id must be a query parameter')
        auth = str((kwargs.get('headers') or {}).get('Authorization', ''))
        if not auth.startswith('Key ') or len(auth) <= 4:
            raise AssertionError('pricing request missing key authorization')
        if self.pricing_mode == 'missing':
            return mock.Mock(status_code=200, text='{"prices":[]}')
        if self.pricing_mode == 'http':
            return mock.Mock(status_code=503, text='unavailable')
        endpoint_id = params['endpoint_id']
        amount = format(self.price, 'f')
        text = (
            '{"prices":[{"endpoint_id":%s,"unit_price":%s,"unit":"generation","currency":"USD"}],'
            '"next_cursor":null,"has_more":false}'
        ) % (json.dumps(endpoint_id), amount)
        return mock.Mock(status_code=200, text=text)

    def _stream(self, method, url, **_kwargs):
        self.streams.append(url)
        content = self.downloads.get(url, self.glb_bytes)
        return _StreamResponse(content)

    def test_cache_hit_makes_no_api_calls(self):
        front = self.write_file('front.png', b'front-v1')
        back = self.write_file('back.png', b'back-v1')
        left = self.write_file('left.png', b'left-v1')
        first = generate_hunyuan3d('linlan', front, back, left, **self.options())
        self.assertFalse(first['cached'])
        self.assertEqual(first['cost_micro_usd'], 60_000)
        self.assertEqual(len(self.submits), 1)
        self.assert_ledger_has_no_secret()

        self.uploads.clear()
        self.submits.clear()
        self.statuses.clear()
        self.results.clear()
        self.gets.clear()
        self.streams.clear()
        saved = os.environ.pop('FAL_KEY')
        try:
            cached = generate_hunyuan3d('linlan', front, back, left, **self.options())
        finally:
            os.environ['FAL_KEY'] = saved
        self.assertTrue(cached['cached'])
        self.assertEqual(cached['cost_micro_usd'], 0)
        self.assertEqual(cached['files']['model_glb'], first['files']['model_glb'])
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.submits, [])
        self.assertEqual(self.statuses, [])
        self.assertEqual(self.results, [])
        self.assertEqual(self.gets, [])
        self.assertEqual(self.streams, [])

        front.write_bytes(b'front-v2')
        again = generate_hunyuan3d('linlan', front, back, left, **self.options())
        self.assertFalse(again['cached'])
        self.assertEqual(len(self.submits), 1)

    def test_pricing_missing_refuses_submit(self):
        self.pricing_mode = 'missing'
        image = self.write_file('atuo.png', b'atuo')
        with self.assertRaises(PricingUnavailableError) as caught:
            generate_trellis('atuo', image, **self.options())
        self.assertEqual(self.submits, [])
        self.assertEqual(self.uploads, [])
        message = str(caught.exception)
        self.assertNotIn('unit-test-placeholder', message)
        key = self._saved_key
        if key and len(key) >= 12 and key in message:
            self.fail('pricing error includes FAL_KEY')

    def test_budget_cap_refuses_submit(self):
        self.price = Decimal('0.05')
        self.write_ledger([{
            'asset_id': 'spent',
            'endpoint': TRELLIS_ENDPOINT,
            'cache_key': 'different-cache-key',
            'status': 'COMPLETED',
            'request_id': 'old-spent',
            'reserved_micro_usd': 4_960_000,
            'final_micro_usd': 4_960_000,
            'files': {},
        }])
        image = self.write_file('sensor.png', b'sensor')
        with self.assertRaises(BudgetExceededError):
            generate_trellis('sensor', image, **self.options())
        self.assertEqual(self.submits, [])
        self.assertEqual(self.uploads, [])
        self.assertEqual(len(self.gets), 1)
        book = json.loads(self.ledger_text())
        self.assertEqual(len(book['entries']), 1)

    def test_max_attempts_refuses_submit(self):
        self.write_ledger([
            {
                'asset_id': 'hero',
                'endpoint': TRELLIS_ENDPOINT,
                'cache_key': 'attempt-1',
                'status': 'FAILED',
                'request_id': 'old-1',
                'reserved_micro_usd': 20_000,
                'final_micro_usd': None,
                'files': {},
            },
            {
                'asset_id': 'hero',
                'endpoint': TRELLIS_ENDPOINT,
                'cache_key': 'attempt-2',
                'status': 'FAILED',
                'request_id': 'old-2',
                'reserved_micro_usd': 20_000,
                'final_micro_usd': None,
                'files': {},
            },
        ])
        image = self.write_file('hero.png', b'hero')
        with self.assertRaises(MaxAttemptsError):
            generate_trellis('hero', image, **self.options())
        self.assertEqual(self.submits, [])
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.gets, [])

    def test_resume_by_request_id_on_restart(self):
        self.status_queue = [InProgress(logs=None)]
        image = self.write_file('rover.png', b'rover')
        with self.assertRaises(PollTimeoutError):
            generate_trellis('atuo', image, **self.options(timeout_s=0))
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(self.streams, [])
        book = json.loads(self.ledger_text())
        self.assertEqual(book['entries'][0]['status'], 'SUBMITTED')
        request_id = book['entries'][0]['request_id']
        self.assertTrue(request_id)
        self.assertEqual(book['entries'][0]['reserved_micro_usd'], 20_000)

        resumed = generate_trellis('atuo', image, **self.options())
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(len(self.gets), 1)
        self.assertEqual(len(self.uploads), 1)
        self.assertTrue(resumed['resumed'])
        self.assertFalse(resumed['cached'])
        self.assertEqual(resumed['request_id'], request_id)
        self.assertEqual(resumed['cost_micro_usd'], 20_000)
        book = json.loads(self.ledger_text())
        self.assertEqual(len(book['entries']), 1)
        self.assertEqual(book['entries'][0]['status'], 'COMPLETED')
        self.assertEqual(book['entries'][0]['final_micro_usd'], 20_000)
        saved = self.assets / book['entries'][0]['files']['model_glb']['path']
        self.assertEqual(saved.read_bytes(), self.glb_bytes)
        self.assert_ledger_has_no_secret()

    def test_successful_download_flow(self):
        front = self.write_file('front.png', b'front')
        back = self.write_file('back.png', b'back')
        left = self.write_file('left.png', b'left')
        self.glb_bytes = b'hunyuan-textured-glb'
        result = generate_hunyuan3d('linlan', front, back, left, **self.options())
        self.assertFalse(result['cached'])
        self.assertEqual(len(self.uploads), 3)
        endpoint, arguments = self.submits[0]
        self.assertEqual(endpoint, 'fal-ai/hunyuan3d/v2/multi-view')
        self.assertEqual(arguments['textured_mesh'], True)
        self.assertEqual(
            set(arguments),
            {'front_image_url', 'back_image_url', 'left_image_url', 'textured_mesh'},
        )
        self.assertEqual(result['cost_micro_usd'], 60_000)
        model = Path(result['files']['model_glb'])
        self.assertEqual(model.read_bytes(), self.glb_bytes)
        book = json.loads(self.ledger_text())
        entry = book['entries'][0]
        self.assertEqual(entry['status'], 'COMPLETED')
        self.assertEqual(entry['asset_id'], 'linlan')
        self.assertEqual(entry['endpoint'], 'fal-ai/hunyuan3d/v2/multi-view')
        self.assertEqual(entry['reserved_micro_usd'], 60_000)
        self.assertEqual(entry['final_micro_usd'], 60_000)
        self.assertEqual(entry['files']['model_glb']['sha256'], hashlib.sha256(self.glb_bytes).hexdigest())
        self.assertTrue(entry['cache_key'])
        self.assertTrue(entry['request_id'])
        self.assert_ledger_has_no_secret()

        self.uploads.clear()
        self.submits.clear()
        self.streams.clear()
        self.price = Decimal('0.02')
        rigged = b'rigged-glb'
        walking = b'walking-glb'
        running = b'running-glb'
        self.downloads = {
            'https://example.invalid/rigged.glb': rigged,
            'https://example.invalid/walking.glb': walking,
            'https://example.invalid/running.glb': running,
        }
        self.result_payload = {
            'rigged_character_glb': {'url': 'https://example.invalid/rigged.glb'},
            'basic_animations': {
                'walking_glb': {'url': 'https://example.invalid/walking.glb'},
                'running_glb': {'url': 'https://example.invalid/running.glb'},
                'walking_fbx': {'url': 'https://example.invalid/walking.fbx'},
            },
        }
        model_path = self.write_file('character.glb', b'source-glb')
        rigged_result = generate_rig('chen', model_path, 1.7, **self.options())
        endpoint, arguments = self.submits[0]
        self.assertEqual(endpoint, 'fal-ai/meshy/rigging')
        self.assertEqual(arguments['skeleton_template'], 'mixamo')
        self.assertEqual(arguments['height_meters'], 1.7)
        self.assertIs(arguments['enable_safety_checker'], True)
        self.assertIn('model_url', arguments)
        self.assertEqual(Path(rigged_result['files']['rigged_character_glb']).read_bytes(), rigged)
        self.assertEqual(Path(rigged_result['files']['walking_glb']).read_bytes(), walking)
        self.assertEqual(Path(rigged_result['files']['running_glb']).read_bytes(), running)
        self.assertNotIn('walking_fbx', rigged_result['files'])
        self.assertEqual(len(self.streams), 3)

        self.submits.clear()
        self.result_payload = {
            'model_mesh': {'url': 'https://example.invalid/trellis.glb'},
        }
        self.glb_bytes = b'trellis-glb'
        self.downloads = {}
        image = self.write_file('atuo.png', b'atuo-side')
        trellis = generate_trellis('atuo', image, **self.options())
        endpoint, arguments = self.submits[0]
        self.assertEqual(endpoint, 'fal-ai/trellis')
        self.assertEqual(arguments['texture_size'], 1024)
        self.assertEqual(arguments['mesh_simplify'], 0.95)
        self.assertIs(arguments['enable_safety_checker'], True)
        self.assertEqual(set(arguments), {'image_url', 'texture_size', 'mesh_simplify', 'enable_safety_checker'})
        self.assertEqual(trellis['cost_micro_usd'], 20_000)
        self.assertEqual(Path(trellis['files']['model_glb']).read_bytes(), b'trellis-glb')


if __name__ == '__main__':
    unittest.main()
