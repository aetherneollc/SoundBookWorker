#!/usr/bin/env python3
"""Download the CC0 packs used by the godot3d trial and record their provenance.

    python tools/fetch_cc0_assets.py            # fetch anything missing, then extract
    python tools/fetch_cc0_assets.py --offline  # only re-extract and re-hash local archives

Packs land in ``media/assets3d/cc0/_archives``. The GLB files the player loads
are extracted into ``media/assets3d/cc0/<pack>/``. Every archive and every
extracted file is hashed into ``media/assets3d/licenses.json``. Entries in that
file owned by other tools (anything without ``"tool": "fetch_cc0_assets"``)
are kept untouched.

Kenney hands out a short-lived signed link from ``/download-link/<id>``.
itch.io free packs go through ``download_url`` then ``file/<upload>``. Both
flows are the same ones the sites' own "Download" buttons run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'media' / 'assets3d'
CC0 = ASSETS / 'cc0'
ARCHIVES = CC0 / '_archives'
LICENSES = ASSETS / 'licenses.json'
TOOL = 'fetch_cc0_assets'
CC0_URL = 'https://creativecommons.org/publicdomain/zero/1.0/'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) SoundBookWorker-cc0-fetch/1.0'

PACKS = [
    {
        'id': 'kenney_space_kit',
        'title': 'Space Kit (2.0)',
        'author': 'Kenney (www.kenney.nl)',
        'source_page': 'https://kenney.nl/assets/space-kit',
        'flow': 'kenney',
        'kenney_download_id': '6173736574732f73706163652d6b6974',
        'archive': 'kenney_space-kit.zip',
        'extract': [r'^Models/GLTF format/[^/]+\.glb$', r'^License\.txt$'],
        'flatten': True,
    },
    {
        'id': 'quaternius_universal_animation_library',
        'title': 'Universal Animation Library [Standard]',
        'author': 'Quaternius',
        'source_page': 'https://quaternius.com/packs/universalanimationlibrary.html',
        'itch_page': 'https://quaternius.itch.io/universal-animation-library',
        'flow': 'itch',
        'itch_upload_name': 'Universal Animation Library[Standard].zip',
        'archive': 'Universal_Animation_Library_Standard.zip',
        'extract': [r'/Unreal-Godot/[^/]+\.glb$', r'(?i)(licen[sc]e[^/]*|readme)\.txt$'],
        'flatten': True,
    },
    {
        'id': 'quaternius_universal_base_characters',
        'title': 'Universal Base Characters [Standard]',
        'author': 'Quaternius',
        'source_page': 'https://quaternius.com/packs/universalbasecharacters.html',
        'itch_page': 'https://quaternius.itch.io/universal-base-characters',
        'flow': 'itch',
        'itch_upload_name': 'Universal Base Characters[Standard].zip',
        'archive': 'Universal_Base_Characters_Standard.zip',
        'extract': [
            r'/Base Characters/Godot - UE/[^/]+\.(gltf|bin|png)$',
            r'/Hairstyles/Origin at 0/glTF \(Godot\)/[^/]+\.(gltf|bin|png)$',
            r'/Base Characters/Textures/T_Superhero_(Female_Light_BaseColor|Male_Ligh)\.png$',
            r'(?i)licen[sc]e[^/]*\.txt$',
        ],
        'flatten': True,
        # The shipped .gltf files name two images that the zip stores under another name.
        'aliases': {
            'T_Eye_Normal_png.png': 'T_Eye_Normal.png',
            'T_Hair_1_Normal_png.png': 'T_Hair_1_Normal.png',
        },
    },
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _session():
    import requests

    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    return session


def _get(session, url: str, **kwargs):
    for attempt in range(6):
        response = session.get(url, timeout=120, **kwargs)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        time.sleep(15 * (attempt + 1))
    response.raise_for_status()
    return response


def _post(session, url: str, **kwargs):
    for attempt in range(6):
        response = session.post(url, timeout=120, **kwargs)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        time.sleep(15 * (attempt + 1))
    response.raise_for_status()
    return response


def _stream_to(session, url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + '.part')
    with session.get(url, stream=True, timeout=600) as response:
        response.raise_for_status()
        with partial.open('wb') as handle:
            for block in response.iter_content(1 << 20):
                handle.write(block)
    partial.replace(destination)


def kenney_signed_url(session, pack: dict) -> str:
    _get(session, pack['source_page'])
    link = _get(
        session,
        'https://kenney.nl/download-link/' + pack['kenney_download_id'],
        headers={'Referer': pack['source_page']},
    ).json()
    if not link.get('url'):
        raise RuntimeError(f"Kenney returned no download url for {pack['id']}")
    return str(link['url'])


def itch_signed_url(session, pack: dict) -> str:
    page = pack['itch_page']
    html = _get(session, page).text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not csrf:
        raise RuntimeError(f'itch.io page has no csrf token: {page}')
    time.sleep(2)
    download_page_url = _post(session, page + '/download_url', data={'csrf_token': csrf.group(1)}).json()['url']
    time.sleep(2)
    download_html = _get(session, download_page_url).text
    csrf2 = re.search(r'name="csrf_token" value="([^"]+)"', download_html)
    upload_id = None
    for match in re.finditer(r'data-upload_id="(\d+)"[\s\S]{0,1200}?class="upload_name"[\s\S]{0,200}?title="([^"]+)"', download_html):
        if match.group(2).strip() == pack['itch_upload_name']:
            upload_id = match.group(1)
            break
    if upload_id is None:
        ids = re.findall(r'data-upload_id="(\d+)"', download_html)
        names = re.findall(r'class="name" title="([^"]+)"', download_html)
        pairs = dict(zip(names, ids))
        upload_id = pairs.get(pack['itch_upload_name']) or (ids[0] if len(ids) == 1 else None)
    if upload_id is None:
        raise RuntimeError(f"Could not find upload {pack['itch_upload_name']} on {download_page_url}")
    time.sleep(2)
    # The download page authorises the session cookie; the button posts without a key.
    file_json = _post(
        session,
        f'{page}/file/{upload_id}',
        params={'source': 'game_download'},
        data={'csrf_token': csrf2.group(1) if csrf2 else csrf.group(1)},
    ).json()
    if not file_json.get('url'):
        raise RuntimeError(f'itch.io returned no file url: {file_json}')
    pack['_itch_upload_id'] = upload_id
    return str(file_json['url'])


def fetch(pack: dict, previous: dict | None) -> dict:
    archive = ARCHIVES / pack['archive']
    entry = dict(previous or {})
    if archive.is_file() and entry.get('archive_sha256') == sha256_file(archive):
        return entry
    if not archive.is_file():
        session = _session()
        url = kenney_signed_url(session, pack) if pack['flow'] == 'kenney' else itch_signed_url(session, pack)
        print(f"download {pack['id']} -> {archive.name}", flush=True)
        _stream_to(session, url, archive)
        entry['download_flow'] = (
            'kenney.nl/download-link signed URL' if pack['flow'] == 'kenney'
            else f"itch.io free download, upload {pack.get('_itch_upload_id')}"
        )
        entry['downloaded_utc'] = utc_now()
    else:
        entry.setdefault('downloaded_utc', dt.datetime.fromtimestamp(
            archive.stat().st_mtime, dt.timezone.utc,
        ).replace(microsecond=0).isoformat().replace('+00:00', 'Z'))
        entry.setdefault('download_flow', 'kenney.nl/download-link signed URL' if pack['flow'] == 'kenney' else 'itch.io free download')
    return entry


def extract(pack: dict) -> list[dict]:
    archive = ARCHIVES / pack['archive']
    target = CC0 / pack['id']
    patterns = [re.compile(item) for item in pack['extract']]
    files = []
    seen: dict[Path, str] = {}
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = info.filename
            if info.is_dir() or not any(pattern.search(name) for pattern in patterns):
                continue
            if '__MACOSX' in name:
                continue
            relative = Path(name).name if pack['flatten'] else Path(*Path(name).parts)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = bundle.read(info)
            digest = hashlib.sha256(data).hexdigest()
            if destination in seen:
                if seen[destination] != digest:
                    raise RuntimeError(f'{pack["id"]}: two different archive members flatten to {relative}')
                continue
            seen[destination] = digest
            if not destination.is_file() or destination.read_bytes() != data:
                destination.write_bytes(data)
            files.append({
                'path': destination.relative_to(ROOT).as_posix(),
                'archive_member': name,
                'bytes': len(data),
                'sha256': digest,
            })
    for alias, original in pack.get('aliases', {}).items():
        source = target / original
        destination = target / alias
        data = source.read_bytes()
        if not destination.is_file() or destination.read_bytes() != data:
            destination.write_bytes(data)
        files.append({
            'path': destination.relative_to(ROOT).as_posix(),
            'copy_of': source.relative_to(ROOT).as_posix(),
            'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
        })
    return files


def load_licenses() -> dict:
    if LICENSES.is_file():
        data = json.loads(LICENSES.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            data.setdefault('version', 1)
            data.setdefault('assets', [])
            return data
    return {'version': 1, 'assets': []}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--offline', action='store_true', help='do not download, only extract and hash')
    parser.add_argument('--only', action='append', default=[], help='pack id to process (repeatable)')
    args = parser.parse_args(argv)

    licenses = load_licenses()
    existing = {item.get('id'): item for item in licenses['assets'] if isinstance(item, dict)}
    for pack in PACKS:
        if args.only and pack['id'] not in args.only:
            continue
        previous = existing.get(pack['id']) if existing.get(pack['id'], {}).get('tool') == TOOL else None
        archive = ARCHIVES / pack['archive']
        if args.offline and not archive.is_file():
            print(f"skip {pack['id']}: archive missing", flush=True)
            continue
        state = fetch(pack, previous)
        files = extract(pack)
        record = {
            'id': pack['id'],
            'tool': TOOL,
            'title': pack['title'],
            'author': pack['author'],
            'license': 'CC0 1.0',
            'license_url': CC0_URL,
            'source_page': pack['source_page'],
            'download_page': pack.get('itch_page', pack['source_page']),
            'download_flow': state.get('download_flow'),
            'downloaded_utc': state.get('downloaded_utc'),
            'archive': archive.relative_to(ROOT).as_posix(),
            'archive_bytes': archive.stat().st_size,
            'archive_sha256': sha256_file(archive),
            'files': files,
        }
        existing[pack['id']] = record
        print(f"{pack['id']}: {len(files)} files, sha256 {record['archive_sha256'][:16]}", flush=True)
    licenses['assets'] = list(existing.values())
    licenses['updated_utc'] = utc_now()
    LICENSES.parent.mkdir(parents=True, exist_ok=True)
    LICENSES.write_text(json.dumps(licenses, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {LICENSES.relative_to(ROOT).as_posix()}', flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
