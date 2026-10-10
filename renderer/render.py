#!/usr/bin/env python3
"""Render a SoundBook v1 manifest with FFmpeg."""
from __future__ import annotations
import argparse, json, os, subprocess, tempfile
from pathlib import Path


def run(args: list[str]) -> None:
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(proc.stderr[-4000:])


def render(manifest_path: Path, out: Path) -> None:
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    if doc.get("version") != 1:
        raise ValueError("manifest version must be 1")
    shots = doc.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("manifest needs at least one shot")
    width = int(doc.get("width", 1920)); height = int(doc.get("height", 1080)); fps = int(doc.get("fps", 30))
    if width < 320 or height < 320 or width > 3840 or height > 2160 or not 1 <= fps <= 60:
        raise ValueError("invalid output dimensions or fps")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="soundbook-") as td:
        work = Path(td); segments=[]
        for i, shot in enumerate(shots):
            src = Path(shot["file"]).resolve()
            if not src.is_file(): raise FileNotFoundError(src)
            duration = max(.25, min(float(shot.get("duration", 4)), 300))
            seg = work / f"shot-{i:04d}.mp4"
            is_image = src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            args=["ffmpeg", "-y"]
            args += ["-loop", "1", "-i", str(src)] if is_image else ["-stream_loop", "-1", "-i", str(src)]
            # Respect the shot's camera direction using FFmpeg's zoompan filter.
            frames=max(1, round(duration*fps))
            camera = str(shot.get("camera", "")).lower()
            if any(word in camera for word in ("静", "固定", "static")):
                zoom, x, y = "1", "0", "0"
            elif any(word in camera for word in ("拉远", "后退", "pull", "zoom out")):
                zoom, x, y = "if(eq(on,0),1.08,max(zoom-0.0005,1.0))", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
            elif any(word in camera for word in ("左移", "向左", "pan left")):
                zoom, x, y = "1.08", f"(iw-iw/zoom)*on/{max(1,frames-1)}", "ih/2-ih/zoom/2"
            elif any(word in camera for word in ("右移", "向右", "pan right")):
                zoom, x, y = "1.08", f"(iw-iw/zoom)*(1-on/{max(1,frames-1)})", "ih/2-ih/zoom/2"
            else:
                zoom, x, y = "min(zoom+0.0005,1.08)", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
            vf=(f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2},"
                f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},format=yuv420p")
            run(args+["-t",f"{duration:.3f}","-vf",vf,"-an","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p",str(seg)])
            segments.append(seg)
        concat=work/"concat.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
        silent=work/"silent.mp4"
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(silent)])
        audio_inputs=[]
        if doc.get("audio"):
            audio_inputs.append({**doc["audio"], "start_ms": 0, "volume": 1.0})
        audio_inputs.extend(doc.get("audio_tracks", []))
        audio_args=[]
        filters=[]
        labels=[]
        for i, track in enumerate(audio_inputs, start=1):
            ap=Path(track["file"]).resolve()
            if not ap.is_file(): raise FileNotFoundError(ap)
            if track.get("loop"):
                audio_args += ["-stream_loop", "-1"]
            audio_args += ["-i", str(ap)]
            delay=max(0, int(track.get("start_ms", 0)))
            volume=max(0.0, min(4.0, float(track.get("volume", 1.0))))
            label=f"a{i}"
            filters.append(f"[{i}:a]adelay={delay}:all=1,volume={volume:.3f}[{label}]")
            labels.append(f"[{label}]")
        if labels:
            filters.append("".join(labels)+f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0[mix]")
            run(["ffmpeg","-y","-i",str(silent),*audio_args,"-filter_complex",";".join(filters),
                 "-map","0:v:0","-map","[mix]","-c:v","copy","-c:a","aac","-b:a","192k","-shortest",str(out)])
        else:
            run(["ffmpeg","-y","-i",str(silent),"-c","copy",str(out)])
        subtitle=doc.get("subtitle")
        if subtitle:
            sp=Path(subtitle["file"]).resolve()
            tmp=out.with_suffix(".subbed.mp4")
            run(["ffmpeg","-y","-i",str(out),"-vf",f"subtitles={sp.as_posix()}","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","copy",str(tmp)])
            tmp.replace(out)

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("manifest",type=Path); p.add_argument("output",type=Path)
    a=p.parse_args(); render(a.manifest,a.output)
