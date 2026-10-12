#!/usr/bin/env python3
"""Generate an episode's speech on the Actions runner with MOSS-TTS-Nano."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


VOICES = {
    "Junhao", "Zhiming", "Weiguo", "Xiaoyu", "Yuewen", "Lingyu",
    "Ava", "Bella", "Adam", "Nathan", "Sakura", "Yui", "Aoi", "Hina", "Mei",
}
DEFAULT_VOICE = {"zh": "Weiguo", "en": "Nathan", "ja": "Sakura", "ko": "Junhao"}


def synthesize(manifest_path: Path) -> None:
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    if doc.get("tts_engine") != "moss":
        return
    moss_dir = Path(os.environ.get("MOSS_REPO", "moss-tts-nano")).resolve()
    sys.path.insert(0, str(moss_dir))
    from onnx_tts_runtime import OnnxTtsRuntime  # type: ignore

    runtime = OnnxTtsRuntime(
        model_dir=moss_dir / "models", thread_count=4, max_new_frames=500,
        execution_provider="cpu",
    )
    default_voice = DEFAULT_VOICE.get(str(doc.get("language")), "Weiguo")
    output_dir = Path("media/moss")
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for track in doc.get("audio_tracks", []):
        text = str(track.get("tts_text") or "").strip()
        if not text:
            continue
        count += 1
        voice = str(track.get("tts_voice") or default_voice)
        if voice not in VOICES:
            voice = default_voice
        output = output_dir / f"line-{count:04d}.wav"
        result = runtime.synthesize(
            text=text, voice=voice, output_audio_path=output,
            sample_mode="fixed", do_sample=True, streaming=False,
            max_new_frames=500, voice_clone_max_text_tokens=65,
            enable_wetext=False, enable_normalize_tts_text=True,
        )
        if not output.exists() or output.stat().st_size < 4096 or not result["text_chunks"]:
            raise RuntimeError(f"MOSS could not synthesize line {count}")
        track["file"] = str(output)
        print(f"MOSS speech {count}: {len(text)} characters, voice {voice}, {output.stat().st_size} bytes", flush=True)
    if count == 0:
        raise RuntimeError("MOSS render has no spoken lines")
    manifest_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    synthesize(Path(sys.argv[1]))
