#!/usr/bin/env python3
"""Generate an episode's speech on the Actions runner.

``tts_engine`` selects the backend:

* ``edge`` — Microsoft neural voices via ``edge-tts`` (clear Chinese, no model weights)
* ``moss`` — MOSS-TTS-Nano, kept for episodes that already pin that engine

Set ``SOUNDBOOK_TTS_ENGINE=edge`` or ``moss`` to override the manifest locally or in CI.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_VOICE = {"zh": "Weiguo", "en": "Nathan", "ja": "Saki", "ko": "Junhao"}
MIN_WAV_BYTES = 4096
EDGE_ATTEMPTS = 3

_ZH_DIGITS = "零一二三四五六七八九"
_STAGE_DIRECTION_RE = re.compile(
    r"（[^）]{0,80}）|\([^)\n]{0,80}\)|【[^】]{0,80}】|\[[^\]\n]{0,80}\]"
)
_CONTROL_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\ufeff]")
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,8})-(\d{1,4})(?![A-Za-z0-9])")
_TEMPERATURE_RE = re.compile(r"(?<![A-Za-z0-9.])(-?\d+(?:\.\d+)?)\s*(?:℃|°C)")
_PERCENT_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[%％]")
_DECIMAL_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+\.\d+)(?![A-Za-z0-9.])")

_ZH_EDGE = {
    "xiaoyu": "zh-CN-XiaoxiaoNeural",
    "longxiaochun": "zh-CN-XiaoxiaoNeural",
    "女性主声线": "zh-CN-XiaoxiaoNeural",
    "林岚": "zh-CN-XiaoxiaoNeural",
    "junhao": "zh-CN-YunxiNeural",
    "longcheng": "zh-CN-YunxiNeural",
    "男性主声线": "zh-CN-YunxiNeural",
    "陈工": "zh-CN-YunxiNeural",
    "weiguo": "zh-CN-YunyangNeural",
    "longhua": "zh-CN-YunyangNeural",
    "旁白": "zh-CN-YunyangNeural",
    "片头": "zh-CN-YunyangNeural",
    "zhiming": "zh-CN-YunjianNeural",
    "longshu": "zh-CN-YunjianNeural",
    "阿拓": "zh-CN-YunjianNeural",
    "at1": "zh-CN-YunjianNeural",
    "at-1": "zh-CN-YunjianNeural",
    "robot": "zh-CN-YunjianNeural",
    "yuewen": "zh-CN-XiaoyiNeural",
    "longanyang": "zh-CN-XiaoyiNeural",
    "lingyu": "zh-CN-XiaochenNeural",
}
_EN_EDGE = {
    "nathan": "en-US-AndrewNeural",
    "adam": "en-US-BrianNeural",
    "ava": "en-US-AriaNeural",
    "bella": "en-US-JennyNeural",
}
_JA_FEMALE = {"saki": "ja-JP-NanamiNeural", "soyo": "ja-JP-NanamiNeural"}
_KO_FEMALE = {"xiaoyu", "yuewen", "lingyu", "longxiaochun", "longanyang", "林岚"}
_CROSS_EDGE = {**_ZH_EDGE, **_EN_EDGE, **_JA_FEMALE}


def speech_cache_id(language: str, voice: str, text: str) -> str:
    """Stable cache identity for one utterance; changes in text or voice miss safely."""
    return hashlib.sha256(f"{language}\0{voice}\0{text.strip()}".encode("utf-8")).hexdigest()[:20]


def ffmpeg_bin() -> str:
    """ffmpeg on PATH, otherwise the binary shipped with imageio-ffmpeg."""
    try:
        from renderer.mux import ffmpeg_executable
    except ImportError:
        from mux import ffmpeg_executable
    return ffmpeg_executable()


def _zh_int(value: int) -> str:
    if value < 0:
        return "负" + _zh_int(-value)
    if value == 0:
        return "零"
    if value < 10:
        return _ZH_DIGITS[value]
    if value < 20:
        return "十" + (_ZH_DIGITS[value % 10] if value % 10 else "")
    if value < 100:
        tens, ones = divmod(value, 10)
        return _ZH_DIGITS[tens] + "十" + (_ZH_DIGITS[ones] if ones else "")
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        head = _ZH_DIGITS[hundreds] + "百"
        if rest == 0:
            return head
        if rest < 10:
            return head + "零" + _ZH_DIGITS[rest]
        if rest < 20:
            return head + "一" + _zh_int(rest)
        return head + _zh_int(rest)
    return str(value)


def _zh_number(token: str) -> str:
    negative = token.startswith("-")
    body = token[1:] if negative else token
    if "." in body:
        whole, frac = body.split(".", 1)
        whole_read = "零" if int(whole or "0") == 0 else _zh_int(int(whole))
        frac_read = "".join(_ZH_DIGITS[int(char)] for char in frac if char.isdigit())
        read = f"{whole_read}点{frac_read}"
    else:
        read = _zh_int(int(body))
    return ("零下" + read) if negative else read


def _spell_letters(token: str) -> str:
    return " ".join(char for char in token.upper())


def normalize_tts_text(text: str, language: str) -> str:
    """Make a line safe to read aloud.

    Stage directions such as ``（轻笑）`` or ``[停顿]`` are dropped. Designators
    and units (``AT-1``, ``15%``, ``0.5``, ``℃``) are rewritten so a neural
    voice does not skip, split, or invent syllables. Extra whitespace and
    control characters are removed.
    """
    cleaned = _CONTROL_RE.sub("", str(text or ""))
    cleaned = _STAGE_DIRECTION_RE.sub(" ", cleaned)
    lang = (language or "zh").split("-")[0].lower()

    def code(match: re.Match[str]) -> str:
        letters = _spell_letters(match.group(1))
        number = match.group(2)
        if lang == "zh":
            return f"{letters} {_zh_int(int(number))}号"
        return f"{letters} {number}"

    def temperature(match: re.Match[str]) -> str:
        token = match.group(1)
        if lang == "zh":
            return f"{_zh_number(token)}摄氏度"
        if lang == "en":
            return f"{token} degrees Celsius"
        return f"{token} °C"

    def percent(match: re.Match[str]) -> str:
        token = match.group(1)
        if lang == "zh":
            return f"百分之{_zh_number(token)}"
        if lang == "en":
            return f"{token} percent"
        return f"{token}%"

    def decimal(match: re.Match[str]) -> str:
        if lang != "zh":
            return match.group(0)
        return _zh_number(match.group(1))

    cleaned = _TEMPERATURE_RE.sub(temperature, cleaned)
    cleaned = _PERCENT_RE.sub(percent, cleaned)
    cleaned = _DECIMAL_RE.sub(decimal, cleaned)
    cleaned = _CODE_RE.sub(code, cleaned)
    if lang == "zh":
        cleaned = cleaned.replace("℃", "摄氏度").replace("%", "百分之").replace("％", "百分之")
    elif lang == "en":
        cleaned = cleaned.replace("℃", " degrees Celsius ").replace("%", " percent ").replace("％", " percent ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _voice_key(voice: str) -> str:
    return re.sub(r"[\s_]+", "", (voice or "").strip().lower())


def _lookup_voice(table: dict[str, str], voice: str) -> str | None:
    raw = (voice or "").strip()
    if not raw:
        return None
    if raw in table:
        return table[raw]
    key = _voice_key(raw)
    if key in table:
        return table[key]
    hyphenless = key.replace("-", "")
    return table.get(hyphenless)


def resolve_edge_voice(voice: str, language: str, speaker: str = "") -> str:
    """Map a MOSS name, Aliyun name, character name, or Edge name to an Edge voice."""
    requested = (voice or "").strip()
    if re.search(r"Neural$", requested):
        return requested
    lang = (language or "zh").split("-")[0].lower()
    speaker_blob = f"{speaker or ''} {requested}"
    if lang == "zh" and re.search(r"阿拓|AT-1|robot", speaker_blob, re.IGNORECASE):
        mapped = _lookup_voice(_ZH_EDGE, requested)
        robot_aliases = {"", "zhiming", "longshu", "阿拓", "at1", "at-1", "robot", "weiguo", "旁白"}
        if mapped is None or _voice_key(requested) in robot_aliases or requested in {"阿拓", "旁白"}:
            return "zh-CN-YunjianNeural"
        return mapped
    tables = {"zh": _ZH_EDGE, "en": _EN_EDGE, "ja": _JA_FEMALE}
    mapped = _lookup_voice(tables.get(lang, {}), requested)
    if mapped:
        return mapped
    if lang == "ja":
        return "ja-JP-KeitaNeural" if requested else "ja-JP-NanamiNeural"
    if lang == "ko":
        key = _voice_key(requested)
        if requested in _KO_FEMALE or key in _KO_FEMALE or re.search(r"女|female|林岚", speaker_blob, re.IGNORECASE):
            return "ko-KR-SunHiNeural"
        return "ko-KR-InJoonNeural"
    mapped = _lookup_voice(_CROSS_EDGE, requested)
    if mapped:
        return mapped
    defaults = {
        "zh": "zh-CN-YunyangNeural",
        "en": "en-US-AndrewNeural",
        "ja": "ja-JP-NanamiNeural",
        "ko": "ko-KR-InJoonNeural",
    }
    return defaults.get(lang, "zh-CN-YunyangNeural")


def edge_rate(language: str) -> str:
    """Match the old short-video pacing without a second atempo pass."""
    return {"en": "+14%", "zh": "+12%", "ja": "+8%", "ko": "+8%"}.get(
        (language or "").split("-")[0].lower(), "+0%"
    )


def edge_audio_filter(speaker: str) -> str:
    """Trim only the outer silence and level the line.

    Silence is removed from the start, then the clip is reversed and the
    same trim is applied to the other end. ``stop_periods`` in one pass
    treats pauses inside a sentence as removable silence and cuts words.

    A robot speaker gets a gentle high-pass plus a small presence peak.
    That is a light band emphasis, not a hard bandpass: a hard bandpass
    muffles consonants and makes the line unintelligible.
    """
    parts: list[str] = []
    if re.search(r"阿拓|AT-1|robot", speaker or "", re.IGNORECASE):
        parts.append("highpass=f=100")
        parts.append("equalizer=f=3200:t=q:w=1.4:g=1.5")
    trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.04:detection=peak"
    parts.extend([trim, "areverse", trim, "areverse", "loudnorm=I=-18:TP=-2:LRA=8"])
    return ",".join(parts)


def edge_ffmpeg_command(ffmpeg: str, source: Path, destination: Path, speaker: str) -> list[str]:
    return [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(source),
        "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le",
        "-af", edge_audio_filter(speaker),
        str(destination),
    ]


def convert_speech_wav(source: Path, destination: Path, speaker: str = "") -> None:
    """Decode to 24 kHz mono 16-bit PCM, then trim and loudness-match."""
    binary = ffmpeg_bin()
    temporary = destination.with_name(f"{destination.stem}.tmp.wav")
    command = edge_ffmpeg_command(binary, source, temporary, speaker)
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode or not temporary.exists() or temporary.stat().st_size < MIN_WAV_BYTES:
        if temporary.exists():
            temporary.unlink()
        detail = (proc.stderr or "").strip()[-500:]
        raise RuntimeError(f"Could not convert speech to wav: {detail}")
    temporary.replace(destination)


def write_edge_mp3(text: str, voice: str, rate: str, destination: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    asyncio.run(communicate.save(str(destination)))


def synthesize_edge_file(text: str, voice: str, rate: str, destination: Path, speaker: str = "") -> None:
    """Synthesize one line, retrying the network call up to three times."""
    last_error: Exception | None = None
    mp3 = destination.with_suffix(".mp3")
    for attempt in range(1, EDGE_ATTEMPTS + 1):
        try:
            if mp3.exists():
                mp3.unlink()
            write_edge_mp3(text, voice, rate, mp3)
            if not mp3.is_file() or mp3.stat().st_size < 128:
                raise RuntimeError("edge-tts returned an empty audio file")
            convert_speech_wav(mp3, destination, speaker)
            if not destination.is_file() or destination.stat().st_size < MIN_WAV_BYTES:
                raise RuntimeError("converted speech is empty")
            return
        except Exception as exc:
            last_error = exc
            if destination.exists():
                destination.unlink()
            if attempt >= EDGE_ATTEMPTS:
                break
            time.sleep(0.4 * attempt)
        finally:
            if mp3.exists():
                mp3.unlink()
    raise RuntimeError(f"Edge neural TTS failed after {EDGE_ATTEMPTS} attempts: {last_error}")


def active_tts_engine(doc: dict) -> str:
    forced = os.environ.get("SOUNDBOOK_TTS_ENGINE", "").strip().lower()
    if forced in {"edge", "moss"}:
        return forced
    engine = str(doc.get("tts_engine") or "").strip().lower()
    if engine in {"edge", "moss"}:
        return engine
    return ""


def _manifest_path(path: Path) -> str:
    return path.as_posix()


def _usable(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= MIN_WAV_BYTES


def tighten_pacing(path: Path, language: str) -> None:
    """Short-video pacing without changing pitch or cutting any words."""
    speed = {"en": 1.14, "zh": 1.12, "ja": 1.08, "ko": 1.08}.get(language, 1.0)
    adjusted = path.with_name(f"{path.stem}-paced.wav")
    proc = subprocess.run(
        [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(path), "-filter:a",
         f"atempo={speed},loudnorm=I=-18:TP=-2:LRA=8", str(adjusted)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode or not adjusted.exists() or adjusted.stat().st_size < MIN_WAV_BYTES:
        if adjusted.exists():
            adjusted.unlink()
        raise RuntimeError(f"Could not adjust speech pacing: {proc.stderr[-500:]}")
    adjusted.replace(path)


def moss_synthesize(runtime, **kwargs):
    """Call MOSS with text normalization enabled, and fall back if wetext is unavailable."""
    try:
        return runtime.synthesize(**kwargs, enable_wetext=True)
    except Exception:
        try:
            return runtime.synthesize(**kwargs, enable_wetext=False)
        except TypeError as exc:
            if "enable_wetext" in str(exc) or "unexpected keyword" in str(exc).lower():
                return runtime.synthesize(**kwargs)
            raise


def load_moss_runtime():
    moss_dir = Path(os.environ.get("MOSS_REPO", "moss-tts-nano")).resolve()
    sys.path.insert(0, str(moss_dir))
    from onnx_tts_runtime import OnnxTtsRuntime  # type: ignore

    return OnnxTtsRuntime(
        model_dir=moss_dir / "models", thread_count=4, max_new_frames=500,
        execution_provider="cpu",
    )


def _write_manifest(manifest_path: Path, doc: dict) -> None:
    manifest_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def synthesize_edge(manifest_path: Path, doc: dict) -> None:
    language = str(doc.get("language") or "zh")
    default_voice = DEFAULT_VOICE.get(language, "Weiguo")
    output_dir = Path("media/tts")
    output_dir.mkdir(parents=True, exist_ok=True)
    rate = edge_rate(language)

    def edge_voice(requested: str, speaker: str) -> str:
        return resolve_edge_voice(requested or default_voice, language, speaker)

    intro_text = normalize_tts_text(str(doc.get("intro_tts_text") or ""), language)
    intro_speaker = "片头"
    intro_voice = edge_voice(str(doc.get("intro_tts_voice") or default_voice), intro_speaker)
    intro_output = output_dir / f"line-{speech_cache_id(f'edge:{language}', intro_voice, intro_text)}.wav"
    tracks = []
    for track in doc.get("audio_tracks", []):
        text = normalize_tts_text(str(track.get("tts_text") or ""), language)
        if not text:
            continue
        speaker = str(track.get("speaker") or "")
        voice = edge_voice(str(track.get("tts_voice") or default_voice), speaker)
        output = output_dir / f"line-{speech_cache_id(f'edge:{language}', voice, text)}.wav"
        tracks.append((track, voice, text, speaker, output))

    missing_intro = bool(intro_text) and not _usable(intro_output)
    missing_lines = [item for item in tracks if not _usable(item[4])]
    if not missing_intro and not missing_lines:
        if intro_text:
            doc["intro_voice_file"] = _manifest_path(intro_output)
        for track, _voice, _text, _speaker, output in tracks:
            track["file"] = _manifest_path(output)
        _write_manifest(manifest_path, doc)
        print(f"Edge cache linked: {len(tracks)} speech tracks", flush=True)
        return

    if intro_text:
        if missing_intro:
            synthesize_edge_file(intro_text, intro_voice, rate, intro_output, intro_speaker)
        doc["intro_voice_file"] = _manifest_path(intro_output)
        print(f"Edge intro: {len(intro_text)} characters, voice {intro_voice}", flush=True)
    count = 0
    for track, voice, text, speaker, output in tracks:
        count += 1
        if not _usable(output):
            synthesize_edge_file(text, voice, rate, output, speaker)
        track["file"] = _manifest_path(output)
        print(f"Edge speech {count}: {len(text)} characters, voice {voice}, {output.stat().st_size} bytes", flush=True)
    if count == 0:
        raise RuntimeError("Edge render has no spoken lines")
    _write_manifest(manifest_path, doc)


def synthesize_moss(manifest_path: Path, doc: dict) -> None:
    language = str(doc.get("language") or "zh")
    default_voice = DEFAULT_VOICE.get(language, "Weiguo")
    output_dir = Path("media/moss")
    output_dir.mkdir(parents=True, exist_ok=True)
    intro_text = normalize_tts_text(str(doc.get("intro_tts_text") or ""), language)
    intro_voice = str(doc.get("intro_tts_voice") or default_voice)
    intro_output = output_dir / "intro.wav"
    tracks = []
    for track in doc.get("audio_tracks", []):
        text = normalize_tts_text(str(track.get("tts_text") or ""), language)
        if not text:
            continue
        voice = str(track.get("tts_voice") or default_voice)
        tracks.append((track, voice, text))
    cached_paths = [
        (track, output_dir / f"line-{speech_cache_id(language, voice, text)}.wav")
        for track, voice, text in tracks
    ]
    missing_intro = bool(intro_text) and not _usable(intro_output)
    missing_lines = [
        (track, voice, text, output)
        for (track, voice, text), (_, output) in zip(tracks, cached_paths)
        if not _usable(output)
    ]

    # On rerenders, exact episode caches already contain paced WAVs. Link those
    # files to the manifest without importing or installing the MOSS runtime.
    if not missing_intro and not missing_lines:
        if intro_text:
            doc["intro_voice_file"] = _manifest_path(intro_output)
        for track, output in cached_paths:
            track["file"] = _manifest_path(output)
        _write_manifest(manifest_path, doc)
        print(f"MOSS cache linked: {len(cached_paths)} speech tracks", flush=True)
        return

    runtime = load_moss_runtime()
    voice_rows = runtime.list_builtin_voices()
    available_voices = {str(row["voice"]) for row in voice_rows}
    if not voice_rows:
        raise RuntimeError("MOSS model has no built-in voices")
    if default_voice not in available_voices:
        default_voice = str(voice_rows[0]["voice"])

    def moss_kwargs(text: str, voice: str, output: Path) -> dict:
        return {
            "text": text,
            "voice": voice,
            "output_audio_path": output,
            "sample_mode": "fixed",
            "do_sample": True,
            "streaming": False,
            "max_new_frames": 500,
            "voice_clone_max_text_tokens": 65,
            "enable_normalize_tts_text": True,
        }

    if intro_text:
        intro_voice = str(doc.get("intro_tts_voice") or default_voice)
        if intro_voice not in available_voices:
            intro_voice = default_voice
        if not _usable(intro_output):
            intro_result = moss_synthesize(runtime, **moss_kwargs(intro_text, intro_voice, intro_output))
            if not _usable(intro_output) or not intro_result["text_chunks"]:
                raise RuntimeError("MOSS could not synthesize the episode announcement")
            tighten_pacing(intro_output, language)
        doc["intro_voice_file"] = _manifest_path(intro_output)
        print(f"MOSS intro: {len(intro_text)} characters, voice {intro_voice}", flush=True)
    count = 0
    for track, requested_voice, text in tracks:
        count += 1
        voice = requested_voice if requested_voice in available_voices else default_voice
        cache_id = speech_cache_id(language, voice, text)
        output = output_dir / f"line-{cache_id}.wav"
        if not _usable(output):
            result = moss_synthesize(runtime, **moss_kwargs(text, voice, output))
            if not _usable(output) or not result["text_chunks"]:
                raise RuntimeError(f"MOSS could not synthesize line {count}")
            tighten_pacing(output, language)
        track["file"] = _manifest_path(output)
        print(
            f"MOSS speech {count}: {len(text)} characters, voice {voice}, {output.stat().st_size} bytes",
            flush=True,
        )
    if count == 0:
        raise RuntimeError("MOSS render has no spoken lines")
    _write_manifest(manifest_path, doc)


def synthesize(manifest_path: Path) -> None:
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    engine = active_tts_engine(doc)
    if engine == "edge":
        synthesize_edge(manifest_path, doc)
    elif engine == "moss":
        synthesize_moss(manifest_path, doc)


if __name__ == "__main__":
    synthesize(Path(sys.argv[1]))
