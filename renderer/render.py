#!/usr/bin/env python3
"""Render a SoundBook v1 manifest with FFmpeg."""
from __future__ import annotations
import argparse, json, math, os, struct, subprocess, tempfile, wave
from pathlib import Path

FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
if not Path(FONT_BOLD).is_file():
    FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
if not Path(FONT_REGULAR).is_file():
    FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def run(args: list[str]) -> None:
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(proc.stderr[-4000:])


def media_duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(f"Unable to read media duration: {proc.stderr[-1000:]}")
    return max(.05, float(proc.stdout.strip()))


def subtitle_chunks(text: str, speaker: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if any("\u3040" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af" for c in text):
        size = 24
        return [text[i:i + size] for i in range(0, len(text), size)]
    words = text.split()
    if len(words) <= 8:
        return [text]
    result=[]; current=[]
    for word in words:
        current.append(word)
        if len(current) >= 8:
            result.append(" ".join(current)); current=[]
    if current:
        result.append(" ".join(current))
    return result


def srt_time(seconds: float) -> str:
    ms=max(0, round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{(ms // 60_000) % 60:02d}:{(ms // 1000) % 60:02d},{ms % 1000:03d}"


def wrap_fact(text: str) -> str:
    text = " ".join(text.split())
    if any("\u4e00" <= char <= "\u9fff" or "\uac00" <= char <= "\ud7af" for char in text):
        if len(text) <= 27:
            return text
        split = max(12, max(text.rfind(mark, 12, 29) for mark in "，；：、。"))
        if split <= 12:
            split = 27
        return text[:split].strip() + "\n" + text[split:54].strip()
    words = text.split()
    line = ""
    for index, word in enumerate(words):
        if len(line) + len(word) + 1 > 54:
            return line + "\n" + " ".join(words[index:])[:54]
        line = f"{line} {word}".strip()
    return line


def write_intro_chime(path: Path) -> None:
    """A short original three-note signal; no external audio asset is used."""
    rate = 44100
    notes = [(0.0, .15, 523.25), (.15, .16, 659.25), (.31, .38, 783.99)]
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        for frame in range(round(rate * .82)):
            time = frame / rate
            sample = 0.0
            for start, length, frequency in notes:
                position = time - start
                if 0 <= position < length:
                    envelope = min(1.0, position / .015) * (1.0 - position / length) ** 1.2
                    sample += .22 * envelope * (
                        math.sin(2 * math.pi * frequency * position) +
                        .26 * math.sin(4 * math.pi * frequency * position)
                    )
            output.writeframes(struct.pack("<h", int(max(-1, min(1, sample)) * 32767)))


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
        intro_voice = Path(str(doc["intro_voice_file"])).resolve() if doc.get("intro_voice_file") else None
        intro_duration = max(2.8, media_duration(intro_voice) + .65) if doc.get("episode_title") and intro_voice else (2.8 if doc.get("episode_title") else 0.0)
        if intro_duration:
            language = str(doc.get("language", "zh"))
            episode_number = max(1, int(doc.get("episode_number") or 1))
            marker = {
                "zh": f"第 {episode_number} 集", "en": f"EPISODE {episode_number}",
                "ja": f"第 {episode_number} 話", "ko": f"제 {episode_number} 화",
            }.get(language, f"EPISODE {episode_number}")
            title = str(doc["episode_title"]).strip()[:80]
            marker_file = work / "episode-number.txt"
            title_file = work / "episode-title.txt"
            marker_file.write_text(marker, encoding="utf-8")
            title_file.write_text(title, encoding="utf-8")
            title_size = 58 if len(title) <= 22 else 43 if len(title) <= 38 else 31
            intro = work / "intro.mp4"
            intro_filter = (
                f"drawbox=x=0:y=0:w=iw:h=ih:color=0x123B65@1:t=fill,"
                f"drawbox=x=0:y=0:w=iw:h={round(height*.055)}:color=0xFFCC62@1:t=fill,"
                f"drawbox=x={round(width*.08)}:y={round(height*.24)}:w={round(width*.84)}:h={round(height*.58)}:color=0x0B2747@1:t=fill,"
                f"drawtext=fontfile={FONT_BOLD}:textfile={marker_file}:x=(w-text_w)/2:y=h*0.31:fontsize={max(22,round(height*.047))}:fontcolor=0xFFCC62,"
                f"drawtext=fontfile={FONT_BOLD}:textfile={title_file}:x=(w-text_w)/2:y=(h-text_h)/2+40:fontsize={max(24,round(title_size*height/720))}:fontcolor=white,"
                f"fade=t=in:st=0:d=0.18,fade=t=out:st={max(.1,intro_duration-.2):.3f}:d=0.18"
            )
            run(["ffmpeg","-y","-f","lavfi","-i",f"color=c=0x123B65:s={width}x{height}:r={fps}:d={intro_duration}",
                 "-vf",intro_filter,"-t",str(intro_duration),"-an","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p",str(intro)])
            segments.append(intro)
        audio_inputs=[]
        if intro_duration:
            chime = work / "intro-chime.wav"
            write_intro_chime(chime)
            audio_inputs.append({"file": str(chime), "start_ms": 0, "volume": .72})
            if intro_voice:
                audio_inputs.append({"file": str(intro_voice), "start_ms": 220, "volume": 1.0})
        if doc.get("audio"):
            audio_inputs.append({**doc["audio"], "start_ms": 0, "volume": 1.0})
        audio_inputs.extend(doc.get("audio_tracks", []))
        for track in audio_inputs:
            if track.get("loop") and intro_duration:
                track["start_ms"] = round(intro_duration * 1000)
        voice_locked = doc.get("timeline_mode") == "voice_locked"
        subtitle_cues=[]
        if voice_locked:
            scene_order=[]
            for shot in doc["shots"]:
                scene_no=int(shot.get("scene_number", 0))
                if scene_no not in scene_order:
                    scene_order.append(scene_no)
            voices=sorted((t for t in audio_inputs if t.get("scene_number") is not None and not t.get("loop")),
                          key=lambda t:(int(t.get("scene_number", 0)), int(t.get("dialogue_number", 0))))
            for track in voices:
                track["_duration"]=media_duration(Path(track["file"]).resolve())
            cursor=intro_duration
            for scene_no in scene_order:
                scene_voices=[t for t in voices if int(t.get("scene_number", 0)) == scene_no]
                planned=[s for s in doc["shots"] if int(s.get("scene_number", 0)) == scene_no]
                if not scene_voices:
                    continue
                local=0.0
                for track in scene_voices:
                    duration=track["_duration"]
                    track["start_ms"]=round((cursor + local) * 1000)
                    chunks=subtitle_chunks(str(track.get("caption_text", "")), str(track.get("speaker", "")))
                    char_total=sum(max(1, len(chunk.replace(" ", ""))) for chunk in chunks)
                    offset=0.0
                    for chunk in chunks:
                        share=max(1, len(chunk.replace(" ", ""))) / char_total if char_total else 1
                        cue_duration=duration * share
                        subtitle_cues.append((cursor + local + offset, cursor + local + offset + cue_duration, chunk))
                        offset += cue_duration
                    local += duration + .28
                # Leave a real breath after speech, including the final AAC packet.
                scene_duration=max(.25, local - .28 + .65)
                planned_total=sum(max(.25, float(s.get("duration", 4))) for s in planned) or 1.0
                for shot in planned:
                    original=max(.25, float(shot.get("duration", 4)))
                    shot["duration"]=max(.25, scene_duration * original / planned_total)
                cursor += scene_duration
            voice_locked_subtitle=out.with_suffix(".voice-locked.srt")
            voice_locked_subtitle.write_text("\n".join(f"{i}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n" for i,(start,end,text) in enumerate(subtitle_cues, 1)), encoding="utf-8")
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
            label = str(shot.get("label", "")).strip()
            section = str(shot.get("section", "")).strip()
            if label or shot.get("fact_card"):
                # Reserve a readable subtitle lane and cover provider corner marks.
                subtitle_top = round(height * .84)
                vf += f",drawbox=x=0:y={subtitle_top}:w=iw:h=ih-{subtitle_top}:color=0x101C30@0.94:t=fill"
            if label:
                # A consistent creator-style chapter bug and section marker make
                # each still feel like part of an edited explainer, not a slideshow.
                label_file = work / f"label-{i:04d}.txt"
                section_file = work / f"section-{i:04d}.txt"
                label_file.write_text(label.replace("\n", " "), encoding="utf-8")
                section_file.write_text(section.replace("\n", " "), encoding="utf-8")
                vf += (
                    f",drawbox=x=38:y=38:w=8:h=78:color=0xFF6B35@1:t=fill"
                    f",drawtext=fontfile={FONT_BOLD}:textfile={section_file.as_posix()}:"
                    "x=64:y=35:fontsize=17:fontcolor=0xFFD64F"
                    f",drawtext=fontfile={FONT_BOLD}:textfile={label_file.as_posix()}:"
                    "x=64:y=61:fontsize=31:fontcolor=white:box=1:boxcolor=0x111827@0.82:boxborderw=10"
                )
            fact_card = str(shot.get("fact_card", "")).strip()
            if fact_card:
                fact_file = work / f"fact-{i:04d}.txt"
                fact_file.write_text(wrap_fact(fact_card), encoding="utf-8")
                # Keep the evidence card above the subtitle safe area.
                panel_top = round(height * .59)
                panel_height = round(height * .12)
                font_size = max(17, round(height * .026))
                vf += (
                    f",drawbox=x=0:y={panel_top}:w=iw:h={panel_height}:color=0x101C30@0.88:t=fill"
                    f",drawtext=fontfile={FONT_BOLD}:textfile={fact_file.as_posix()}:"
                    f"x=(w-text_w)/2:y={panel_top}+({panel_height}-text_h)/2:"
                    f"fontsize={font_size}:fontcolor=white:line_spacing=6"
                )
            run(args+["-t",f"{duration:.3f}","-vf",vf,"-an","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p",str(seg)])
            segments.append(seg)
        concat=work/"concat.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
        silent=work/"silent.mp4"
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(silent)])
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
            filters.append("".join(labels)+f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,apad=pad_dur=1[mix]")
            run(["ffmpeg","-y","-i",str(silent),*audio_args,"-filter_complex",";".join(filters),
                 "-map","0:v:0","-map","[mix]","-c:v","copy","-c:a","aac","-b:a","192k","-t",f"{media_duration(silent):.3f}",str(out)])
        else:
            run(["ffmpeg","-y","-i",str(silent),"-c","copy",str(out)])
        subtitle={"file": str(voice_locked_subtitle)} if voice_locked and subtitle_cues else doc.get("subtitle")
        if subtitle:
            sp=Path(subtitle["file"]).resolve()
            tmp=out.with_suffix(".subbed.mp4")
            subtitle_style = (
                "FontName=Noto Sans CJK SC,FontSize=20,Bold=1,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                "BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=38"
            )
            run(["ffmpeg","-y","-i",str(out),"-vf",f"subtitles={sp.as_posix()}:force_style='{subtitle_style}'","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","copy",str(tmp)])
            tmp.replace(out)
            if voice_locked:
                sp.unlink(missing_ok=True)

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("manifest",type=Path); p.add_argument("output",type=Path)
    a=p.parse_args(); render(a.manifest,a.output)
