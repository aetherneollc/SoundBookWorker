#!/usr/bin/env python3
"""Paid fal.ai 3D jobs with a local ledger, content cache, and hard budget cap.

FAL_KEY is read from the process environment, or from a gitignored ``.env``
when the variable is unset. The key is never printed, logged, or written.

Examples
--------
python tools/fal_assets.py hunyuan3d --asset-id linlan --front front.png --back back.png --left left.png
python tools/fal_assets.py trellis --asset-id atuo --image atuo.png
python tools/fal_assets.py rig --asset-id linlan --model model.glb --height-meters 1.65

Optional flags (all commands): --budget-usd 5 --max-attempts 2
--assets-dir media/assets3d --ledger media/assets3d/ledger.json
--timeout 1800 --poll-interval 2 --env-file .env
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import httpx
import fal_client
from dotenv import load_dotenv
from fal_client import Completed

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS_DIR = ROOT / 'media' / 'assets3d'
DEFAULT_BUDGET_MICRO_USD = 5_000_000
DEFAULT_MAX_ATTEMPTS = 2
PRICING_URL = 'https://api.fal.ai/v1/models/pricing'

HUNYUAN_ENDPOINT = 'fal-ai/hunyuan3d/v2/multi-view'
TRELLIS_ENDPOINT = 'fal-ai/trellis'
MESHY_RIG_ENDPOINT = 'fal-ai/meshy/rigging'
TEXTURED_MESH_MULTIPLIER = 3

_ASSET_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_ATTEMPT_STATUSES = {'SUBMITTED', 'COMPLETED', 'FAILED'}
_OUTPUT_NAMES = {
    'model_glb': 'model.glb',
    'rigged_character_glb': 'rigged.glb',
    'walking_glb': 'walking.glb',
    'running_glb': 'running.glb',
}
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class FalAssetsError(RuntimeError):
    """Base error for the fal asset client."""


class PricingUnavailableError(FalAssetsError):
    """Pricing could not be fetched or parsed. The job was not submitted."""


class BudgetExceededError(FalAssetsError):
    """The quoted job would push the ledger past the budget cap."""


class MaxAttemptsError(FalAssetsError):
    """This asset_id already used its paid attempts."""


class PollTimeoutError(FalAssetsError):
    """Polling timed out. The submitted request stays in the ledger."""


class FalKeyMissingError(FalAssetsError):
    """FAL_KEY is not available."""


class LedgerError(FalAssetsError):
    """The ledger file could not be read."""


class DownloadError(FalAssetsError):
    """A result file could not be saved."""


class ResultShapeError(FalAssetsError):
    """The fal result did not contain the expected file URL."""


def decimal_to_micro_usd(value) -> int:
    """Convert a USD amount to integer micro-USD (1 USD = 1_000_000)."""
    if isinstance(value, bool) or value is None:
        raise ValueError('invalid dollar amount')
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            if not _finite(value):
                raise ValueError('invalid dollar amount')
            amount = Decimal(str(value))
        else:
            amount = Decimal(str(value).strip())
    except (ValueError, InvalidOperation, ArithmeticError):
        raise ValueError('invalid dollar amount') from None
    if not amount.is_finite() or amount < 0:
        raise ValueError('invalid dollar amount')
    micro = (amount * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_UP)
    return int(micro)


def make_cache_key(endpoint: str, params: dict, ref_tokens: list[str]) -> str:
    """sha256(endpoint + canonical JSON + sorted ``role:sha256`` reference tokens)."""
    canonical = json.dumps(params, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    refs = ','.join(sorted(ref_tokens))
    payload = f'{endpoint}{canonical}{refs}'.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def generate_hunyuan3d(
    asset_id: str,
    front: Path,
    back: Path,
    left: Path,
    **options,
) -> dict:
    """Submit ``fal-ai/hunyuan3d/v2/multi-view`` with ``textured_mesh`` (3x white-mesh price)."""
    files = {
        'front': _existing_file(front),
        'back': _existing_file(back),
        'left': _existing_file(left),
    }
    return _run_job(
        asset_id=asset_id,
        endpoint=HUNYUAN_ENDPOINT,
        cache_params={'textured_mesh': True},
        files=files,
        build_arguments=lambda urls: {
            'front_image_url': urls['front'],
            'back_image_url': urls['back'],
            'left_image_url': urls['left'],
            'textured_mesh': True,
        },
        extract_urls=_extract_model_mesh,
        **options,
    )


def generate_trellis(asset_id: str, image: Path, **options) -> dict:
    """Submit ``fal-ai/trellis`` for one reference image."""
    files = {'image': _existing_file(image)}
    return _run_job(
        asset_id=asset_id,
        endpoint=TRELLIS_ENDPOINT,
        cache_params={
            'texture_size': 1024,
            'mesh_simplify': 0.95,
            'enable_safety_checker': True,
        },
        files=files,
        build_arguments=lambda urls: {
            'image_url': urls['image'],
            'texture_size': 1024,
            'mesh_simplify': 0.95,
            'enable_safety_checker': True,
        },
        extract_urls=_extract_model_mesh,
        **options,
    )


def generate_rig(asset_id: str, model: Path, height_meters: float, **options) -> dict:
    """Submit ``fal-ai/meshy/rigging`` with the Mixamo skeleton."""
    height = _normalize_height(height_meters)
    files = {'model': _existing_file(model)}
    return _run_job(
        asset_id=asset_id,
        endpoint=MESHY_RIG_ENDPOINT,
        cache_params={
            'skeleton_template': 'mixamo',
            'height_meters': height,
            'enable_safety_checker': True,
        },
        files=files,
        build_arguments=lambda urls: {
            'model_url': urls['model'],
            'skeleton_template': 'mixamo',
            'height_meters': height,
            'enable_safety_checker': True,
        },
        extract_urls=_extract_rig,
        **options,
    )


def _run_job(
    *,
    asset_id: str,
    endpoint: str,
    cache_params: dict,
    files: dict[str, Path],
    build_arguments,
    extract_urls,
    budget_micro_usd: int = DEFAULT_BUDGET_MICRO_USD,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ledger_path: Path | None = None,
    assets_dir: Path | None = None,
    timeout_s: float = 1800.0,
    poll_interval_s: float = 2.0,
    env_file: Path | None = None,
) -> dict:
    if not _ASSET_ID.fullmatch(asset_id or ''):
        raise ValueError('asset_id must be a short identifier of letters, digits, "_" or "-"')
    if not isinstance(budget_micro_usd, int) or isinstance(budget_micro_usd, bool) or budget_micro_usd < 0:
        raise ValueError('budget_micro_usd must be a non-negative integer')
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError('max_attempts must be an integer >= 1')
    if timeout_s < 0 or poll_interval_s < 0:
        raise ValueError('timeout and poll interval must be >= 0')

    assets, ledger = _resolve_dirs(assets_dir, ledger_path)
    ref_tokens = [f'{role}:{_file_sha256(path)}' for role, path in files.items()]
    cache_key = make_cache_key(endpoint, cache_params, ref_tokens)

    with _ledger_lock(ledger):
        book = _load_ledger(ledger)
        reusable = _find_reusable(book['entries'], cache_key)
        if reusable and reusable.get('status') == 'COMPLETED' and _files_ready(reusable, assets):
            return _result_from_entry(reusable, assets, cached=True, resumed=False, cost_micro_usd=0)
        if reusable:
            return _finish_entry(
                book,
                ledger,
                assets,
                reusable,
                extract_urls,
                timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
                env_file=env_file,
                resumed=True,
            )
        attempts = _attempt_count(book['entries'], asset_id)
        if attempts >= max_attempts:
            raise MaxAttemptsError(
                'Refusing to submit: %s already has %s paid attempts (max %s).'
                % (asset_id, attempts, max_attempts)
            )
        _ensure_fal_key(env_file)
        unit = _fetch_unit_price_micro_usd(endpoint)
        quote = _quoted_micro_usd(endpoint, unit, cache_params)
        spent = _committed_micro_usd(book['entries'])
        if spent + quote > budget_micro_usd:
            raise BudgetExceededError(
                'Refusing to submit: quote %s micro-USD plus %s already committed exceeds the %s micro-USD cap.'
                % (quote, spent, budget_micro_usd)
            )
        urls = {role: fal_client.upload_file(str(path)) for role, path in files.items()}
        arguments = build_arguments(urls)
        handle = fal_client.submit(endpoint, arguments)
        request_id = getattr(handle, 'request_id', None)
        if not isinstance(request_id, str) or not request_id:
            raise FalAssetsError('fal submit did not return a request id')
        entry = {
            'asset_id': asset_id,
            'endpoint': endpoint,
            'cache_key': cache_key,
            'status': 'SUBMITTED',
            'request_id': request_id,
            'reserved_micro_usd': quote,
            'final_micro_usd': None,
            'files': {},
            'created_at': _now(),
            'updated_at': _now(),
        }
        book['entries'].append(entry)
        _save_ledger(ledger, book)
        return _finish_entry(
            book,
            ledger,
            assets,
            entry,
            extract_urls,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            env_file=env_file,
            resumed=False,
        )


def _finish_entry(
    book,
    ledger: Path,
    assets: Path,
    entry: dict,
    extract_urls,
    *,
    timeout_s: float,
    poll_interval_s: float,
    env_file: Path | None,
    resumed: bool,
) -> dict:
    _ensure_fal_key(env_file)
    endpoint = entry['endpoint']
    request_id = entry['request_id']
    try:
        status = _poll(endpoint, request_id, timeout_s, poll_interval_s)
    except PollTimeoutError:
        raise
    if getattr(status, 'error', None):
        _mark_failed(entry)
        _save_ledger(ledger, book)
        raise FalAssetsError('fal request failed: %s' % _redact(str(status.error)))
    try:
        payload = fal_client.result(endpoint, request_id)
    except Exception as exc:
        raise FalAssetsError('fal result fetch failed: %s' % _redact(str(exc))) from None
    try:
        remote_files = extract_urls(payload)
    except ResultShapeError:
        _mark_failed(entry)
        _save_ledger(ledger, book)
        raise
    local_files = {}
    file_meta = {}
    asset_id = entry['asset_id']
    try:
        for role, url in remote_files.items():
            dest = assets / asset_id / _OUTPUT_NAMES[role]
            digest = _download_file(url, dest)
            relative = dest.resolve().relative_to(assets.resolve()).as_posix()
            file_meta[role] = {'path': relative, 'sha256': digest}
            local_files[role] = str(dest.resolve())
    except DownloadError:
        _save_ledger(ledger, book)
        raise
    entry['status'] = 'COMPLETED'
    entry['final_micro_usd'] = int(entry.get('reserved_micro_usd') or 0)
    entry['files'] = file_meta
    entry['updated_at'] = _now()
    _save_ledger(ledger, book)
    return _result_from_entry(
        entry,
        assets,
        cached=False,
        resumed=resumed,
        cost_micro_usd=int(entry['final_micro_usd']),
        files=local_files,
    )


def _poll(endpoint: str, request_id: str, timeout_s: float, poll_interval_s: float):
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            status = fal_client.status(endpoint, request_id)
        except Exception as exc:
            if 'Timeout' in type(exc).__name__:
                raise PollTimeoutError(
                    'Polling timed out. The request stays submitted and will be resumed, not resubmitted.'
                ) from None
            raise FalAssetsError('fal status check failed: %s' % _redact(str(exc))) from None
        if isinstance(status, Completed):
            return status
        if timeout_s <= 0 or time.monotonic() >= deadline:
            raise PollTimeoutError(
                'Polling timed out. The request stays submitted and will be resumed, not resubmitted.'
            )
        if poll_interval_s:
            time.sleep(poll_interval_s)


def _fetch_unit_price_micro_usd(endpoint: str) -> int:
    key = os.environ.get('FAL_KEY') or ''
    try:
        response = httpx.get(
            PRICING_URL,
            params={'endpoint_id': endpoint},
            headers={'Authorization': 'Key ' + key},
            timeout=30.0,
        )
    except Exception:
        raise PricingUnavailableError(
            'Refusing to submit: fal pricing could not be fetched or parsed.'
        ) from None
    if getattr(response, 'status_code', 0) != 200:
        code = getattr(response, 'status_code', 0)
        raise PricingUnavailableError(
            'Refusing to submit: fal pricing could not be fetched or parsed (HTTP %s).' % code
        )
    try:
        payload = json.loads(response.text, parse_float=Decimal)
    except Exception:
        raise PricingUnavailableError(
            'Refusing to submit: fal pricing could not be fetched or parsed.'
        ) from None
    return _unit_price_micro_usd(payload, endpoint)


def _unit_price_micro_usd(payload, endpoint: str) -> int:
    if not isinstance(payload, dict):
        raise PricingUnavailableError('Refusing to submit: fal pricing could not be fetched or parsed.')
    prices = payload.get('prices')
    if not isinstance(prices, list) or not prices:
        raise PricingUnavailableError('Refusing to submit: fal pricing could not be fetched or parsed.')
    match = None
    for item in prices:
        if isinstance(item, dict) and item.get('endpoint_id') == endpoint:
            match = item
            break
    if match is None:
        raise PricingUnavailableError('Refusing to submit: fal pricing could not be fetched or parsed.')
    currency = match.get('currency', 'USD')
    if not isinstance(currency, str) or currency.upper() != 'USD':
        raise PricingUnavailableError('Refusing to submit: fal pricing could not be fetched or parsed.')
    if 'unit_price' not in match or match.get('unit_price') is None:
        raise PricingUnavailableError('Refusing to submit: fal pricing could not be fetched or parsed.')
    try:
        return decimal_to_micro_usd(match['unit_price'])
    except ValueError:
        raise PricingUnavailableError(
            'Refusing to submit: fal pricing could not be fetched or parsed.'
        ) from None


def _quoted_micro_usd(endpoint: str, unit_micro: int, cache_params: dict) -> int:
    if endpoint == HUNYUAN_ENDPOINT and cache_params.get('textured_mesh') is True:
        return int(unit_micro) * TEXTURED_MESH_MULTIPLIER
    return int(unit_micro)


def _extract_model_mesh(result: dict) -> dict[str, str]:
    if not isinstance(result, dict):
        raise ResultShapeError('Result is missing model_mesh.url.')
    return {'model_glb': _file_url(result.get('model_mesh'), 'model_mesh.url')}


def _extract_rig(result: dict) -> dict[str, str]:
    if not isinstance(result, dict):
        raise ResultShapeError('Result is missing rigged_character_glb.url.')
    files = {'rigged_character_glb': _file_url(result.get('rigged_character_glb'), 'rigged_character_glb.url')}
    animations = result.get('basic_animations')
    if isinstance(animations, dict):
        for role in ('walking_glb', 'running_glb'):
            value = animations.get(role)
            if isinstance(value, dict) and isinstance(value.get('url'), str) and value['url']:
                files[role] = value['url']
            elif isinstance(value, str) and value:
                files[role] = value
    return files


def _file_url(value, label: str) -> str:
    if isinstance(value, dict):
        url = value.get('url')
        if isinstance(url, str) and url:
            return url
    raise ResultShapeError('Result is missing %s.' % label)


def _download_file(url: str, dest: Path) -> str:
    if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
        raise DownloadError('failed to download a result file')
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + '.part')
    hasher = hashlib.sha256()
    try:
        with httpx.stream('GET', url, timeout=120.0, follow_redirects=True) as response:
            response.raise_for_status()
            with temporary.open('wb') as handle:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    handle.write(chunk)
                    hasher.update(chunk)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise DownloadError('failed to download a result file')
        os.replace(temporary, dest)
    except DownloadError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception:
        temporary.unlink(missing_ok=True)
        raise DownloadError('failed to download a result file') from None
    return hasher.hexdigest()


def _ensure_fal_key(env_file: Path | None) -> None:
    if os.environ.get('FAL_KEY'):
        return
    path = Path(env_file) if env_file is not None else ROOT / '.env'
    if path.is_file():
        load_dotenv(path, override=False)
    if not os.environ.get('FAL_KEY'):
        raise FalKeyMissingError(
            'FAL_KEY is not set. Add it to the environment or a gitignored .env file.'
        )


def _load_ledger(path: Path) -> dict:
    if not path.exists():
        return {'version': 1, 'entries': []}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        raise LedgerError('ledger could not be read: %s' % path) from None
    if not isinstance(data, dict) or not isinstance(data.get('entries'), list):
        raise LedgerError('ledger could not be read: %s' % path)
    if any(not isinstance(entry, dict) for entry in data['entries']):
        raise LedgerError('ledger could not be read: %s' % path)
    data.setdefault('version', 1)
    return data


def _save_ledger(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def _find_reusable(entries: list[dict], cache_key: str) -> dict | None:
    for entry in reversed(entries):
        if entry.get('cache_key') != cache_key:
            continue
        if entry.get('status') not in {'SUBMITTED', 'COMPLETED'}:
            continue
        if not entry.get('request_id'):
            continue
        return entry
    return None


def _files_ready(entry: dict, assets: Path) -> bool:
    files = entry.get('files')
    if not isinstance(files, dict) or not files:
        return False
    for info in files.values():
        if not isinstance(info, dict) or not info.get('path'):
            return False
        if not (assets / info['path']).is_file():
            return False
    return True


def _attempt_count(entries: list[dict], asset_id: str) -> int:
    total = 0
    for entry in entries:
        if entry.get('asset_id') != asset_id:
            continue
        if entry.get('request_id') and entry.get('status') in _ATTEMPT_STATUSES:
            total += 1
    return total


def _committed_micro_usd(entries: list[dict]) -> int:
    """COMPLETED spends its final cost. SUBMITTED holds its reservation.

    FAILED jobs are not added; fal bills a request when it completes.
    """
    total = 0
    for entry in entries:
        status = entry.get('status')
        if status == 'COMPLETED':
            amount = entry.get('final_micro_usd')
            if amount is None:
                amount = entry.get('reserved_micro_usd') or 0
            total += int(amount)
        elif status == 'SUBMITTED':
            total += int(entry.get('reserved_micro_usd') or 0)
    return total


def _mark_failed(entry: dict) -> None:
    entry['status'] = 'FAILED'
    entry['updated_at'] = _now()


def _result_from_entry(
    entry: dict,
    assets: Path,
    *,
    cached: bool,
    resumed: bool,
    cost_micro_usd: int,
    files: dict[str, str] | None = None,
) -> dict:
    if files is None:
        files = {}
        for role, info in (entry.get('files') or {}).items():
            files[role] = str((assets / info['path']).resolve())
    return {
        'asset_id': entry.get('asset_id'),
        'endpoint': entry.get('endpoint'),
        'cache_key': entry.get('cache_key'),
        'cached': cached,
        'resumed': resumed,
        'request_id': entry.get('request_id'),
        'cost_micro_usd': int(cost_micro_usd),
        'files': files,
    }


def _resolve_dirs(assets_dir: Path | None, ledger_path: Path | None) -> tuple[Path, Path]:
    assets = Path(assets_dir) if assets_dir is not None else DEFAULT_ASSETS_DIR
    assets = assets.resolve()
    ledger = Path(ledger_path).resolve() if ledger_path is not None else assets / 'ledger.json'
    return assets, ledger


def _existing_file(path: Path) -> Path:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError('file not found: %s' % file_path)
    return file_path


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def _normalize_height(value: float) -> float:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('height_meters must be a positive finite number') from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError('height_meters must be a positive finite number')
    quantized = amount.quantize(Decimal('0.000001'))
    text = format(quantized, 'f').rstrip('0').rstrip('.')
    if '.' not in text:
        text += '.0'
    return float(json.loads(text))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _finite(value: float) -> bool:
    return value == value and value not in (float('inf'), float('-inf'))


def _redact(text: str, limit: int = 300) -> str:
    value = text or ''
    key = os.environ.get('FAL_KEY') or ''
    if key:
        value = value.replace(key, '[redacted]')
    if len(value) > limit:
        value = value[:limit] + '...'
    return value


def _ledger_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='fal_assets',
        description='Generate 3D assets through fal.ai with a local ledger, cache, and budget cap.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'examples:\n'
            '  python tools/fal_assets.py hunyuan3d --asset-id linlan --front front.png --back back.png --left left.png\n'
            '  python tools/fal_assets.py trellis --asset-id atuo --image atuo.png\n'
            '  python tools/fal_assets.py rig --asset-id linlan --model model.glb --height-meters 1.65\n'
        ),
    )
    sub = parser.add_subparsers(dest='command', required=True)

    def add_common(command: argparse.ArgumentParser) -> None:
        command.add_argument('--asset-id', required=True)
        command.add_argument('--budget-usd', default='5', help='total cap in USD (default 5)')
        command.add_argument('--max-attempts', type=int, default=DEFAULT_MAX_ATTEMPTS)
        command.add_argument('--ledger', default=None, help='ledger JSON path')
        command.add_argument('--assets-dir', default=None, help='download root (default media/assets3d)')
        command.add_argument('--timeout', type=float, default=1800, help='poll timeout in seconds')
        command.add_argument('--poll-interval', type=float, default=2.0)
        command.add_argument('--env-file', default=None, help='dotenv file used only when FAL_KEY is unset')

    hunyuan = sub.add_parser('hunyuan3d', help='fal-ai/hunyuan3d/v2/multi-view textured mesh')
    add_common(hunyuan)
    hunyuan.add_argument('--front', required=True)
    hunyuan.add_argument('--back', required=True)
    hunyuan.add_argument('--left', required=True)

    trellis = sub.add_parser('trellis', help='fal-ai/trellis image to mesh')
    add_common(trellis)
    trellis.add_argument('--image', required=True)

    rig = sub.add_parser('rig', help='fal-ai/meshy/rigging with the Mixamo skeleton')
    add_common(rig)
    rig.add_argument('--model', required=True)
    rig.add_argument('--height-meters', required=True, type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        budget = decimal_to_micro_usd(Decimal(args.budget_usd))
    except (ValueError, InvalidOperation, ArithmeticError):
        print('invalid --budget-usd', file=sys.stderr)
        return 2
    options = {
        'budget_micro_usd': budget,
        'max_attempts': args.max_attempts,
        'ledger_path': Path(args.ledger) if args.ledger else None,
        'assets_dir': Path(args.assets_dir) if args.assets_dir else None,
        'timeout_s': args.timeout,
        'poll_interval_s': args.poll_interval,
        'env_file': Path(args.env_file) if args.env_file else None,
    }
    try:
        if args.command == 'hunyuan3d':
            result = generate_hunyuan3d(args.asset_id, Path(args.front), Path(args.back), Path(args.left), **options)
        elif args.command == 'trellis':
            result = generate_trellis(args.asset_id, Path(args.image), **options)
        elif args.command == 'rig':
            result = generate_rig(args.asset_id, Path(args.model), args.height_meters, **options)
        else:
            parser.error('unknown command')
            return 2
    except (FalAssetsError, ValueError, FileNotFoundError, OSError) as exc:
        print(_redact(str(exc)), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
