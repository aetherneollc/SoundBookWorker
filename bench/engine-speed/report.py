"""Collect Godot timings and print a plain-language speed summary."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


SCENE = "1280x720, 2 capsule characters, 8 boxes, one 1024 directional shadow"


def load_json(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def annotate(args: argparse.Namespace) -> None:
    out = pathlib.Path(args.json)
    data = load_json(out) if out.exists() and out.stat().st_size else None
    log_path = pathlib.Path(args.log)
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    if data is None:
        match = ""
        for line in log.splitlines():
            if "BENCH_JSON " in line:
                match = line.split("BENCH_JSON ", 1)[1].strip()
        if match:
            try:
                parsed = json.loads(match)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
    if data is None:
        data = {
            "engine": "godot",
            "pixel_ok": False,
            "error": "Godot 没有写出测速结果",
            "log_tail": "\n".join(log.splitlines()[-40:]),
        }
    data["wall_s"] = round(args.end - args.start, 3)
    data["exit_code"] = args.exit
    artifact = pathlib.Path(args.artifact) if args.artifact else None
    if artifact and artifact.exists():
        data["artifact_bytes"] = artifact.stat().st_size
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def software(adapter: str) -> bool:
    text = adapter.lower()
    return any(name in text for name in ("llvmpipe", "lavapipe", "swiftshader", "softpipe"))


def judge(row: dict) -> str:
    if not row.get("pixel_ok"):
        return "无效：画面没有画出来，帧率不能用"
    fps = float(row.get("loop_fps") or 0)
    adapter = str(row.get("adapter") or "")
    if adapter and not software(adapter):
        return "这台机器不是软件渲染，数字不能代表没有显卡的 GitHub Actions"
    if row.get("method") == "forward_plus":
        if fps < 0.4:
            return "偏慢：软件 Vulkan 跑这一档也不该低于 0.4 帧/秒"
        if fps < 12:
            return "软件 Vulkan 的常见速度，能画，但出成片会很慢"
        return "偏快：确认一下是不是没用软件 Vulkan"
    if fps < 1:
        return "偏慢：这种简单场景不该低于 1 帧/秒"
    if fps < 4:
        return "能跑，但偏慢，只适合几秒的镜头"
    if fps <= 45:
        return "正常：没有显卡时，720p 的简单三维场景就在这个范围"
    return "偏快：可能被刷新率卡住，或阴影没有真正打开"


def episode_seconds(fps: float, video_seconds: float) -> float | None:
    if fps <= 0:
        return None
    return video_seconds * 24.0 / fps


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f} 秒"
    return f"{seconds / 60:.1f} 分钟"


def mode_name(mode: str) -> str:
    return {
        "readback": "读回像素",
        "movie": "写成 avi",
        "render": "只渲染不读回",
    }.get(mode, mode or "未知")


def summary(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.results)
    host = load_json(root / "host.json") or {}
    rows = []
    for path in sorted(root.glob("*.json")):
        if path.name == "host.json":
            continue
        data = load_json(path)
        if data:
            data["_file"] = path.name
            rows.append(data)

    print("场景：%s" % SCENE)
    print("帧数：每个测试 %s 帧，动画按 %s fps 推进，机器能多快就多快，不故意等实时。" % (
        host.get("frames", "72"), host.get("fps", "24")))
    if host:
        print("机器：%s 核，%s，内存 %s" % (
            host.get("cpus", "?"), host.get("cpu", "未知 CPU"), host.get("memory", "未知")))
    print()

    ranked = sorted(rows, key=lambda row: (
        0 if row.get("engine") == "godot" else 1,
        str(row.get("method") or ""),
        str(row.get("mode") or ""),
    ))
    godot_ok = False
    three_ok = False
    for row in ranked:
        engine = str(row.get("engine") or "unknown")
        if engine == "godot" and row.get("pixel_ok"):
            godot_ok = True
        if engine == "three" and row.get("pixel_ok"):
            three_ok = True
        title = "%s  %s  %s" % (engine, row.get("method") or "—", mode_name(str(row.get("mode") or "")))
        print(title)
        if row.get("error") and not row.get("loop_fps"):
            print("  失败：%s" % row["error"])
        else:
            fps = float(row.get("loop_fps") or 0)
            one = episode_seconds(fps, 60)
            five = episode_seconds(fps, 300)
            startup = None
            if row.get("startup_s") is not None:
                startup = max(0.0, float(row["startup_s"]))
            elif row.get("wall_s") is not None and row.get("loop_s") is not None:
                startup = max(0.0, float(row["wall_s"]) - float(row["loop_s"]))
            print("  %.2f 帧/秒，%.1f 毫秒/帧，分辨率 %sx%s，画面覆盖 %.0f%%" % (
                fps,
                float(row.get("ms_per_frame") or 0),
                row.get("width") or "?",
                row.get("height") or "?",
                float(row.get("pixel_coverage") or 0) * 100,
            ))
            print("  1 分钟成片约 %s，5 分钟成片约 %s，准备约 %s，整段墙钟 %s" % (
                clock(one), clock(five), clock(startup), clock(row.get("wall_s"))))
            print("  适配器：%s" % (row.get("adapter") or "没读到"))
            if row.get("artifact_bytes"):
                print("  输出文件：%.1f MB" % (int(row["artifact_bytes"]) / 1048576))
            print("  判定：%s" % judge(row))
        attempts = row.get("attempts")
        if isinstance(attempts, list):
            for attempt in attempts:
                if isinstance(attempt, dict):
                    print("  - %s：%s" % (attempt.get("method"), attempt.get("error") or attempt.get("adapter")))
        if row.get("log_tail") and not row.get("pixel_ok"):
            print("  日志结尾：")
            for line in str(row["log_tail"]).splitlines()[-12:]:
                print("    " + line)
        print()

    print("怎么看：")
    print("- 读回像素，以及 Godot 写成 avi，才是出片速度。只渲染不读回拿不到画面。")
    print("- 成片时间按帧循环外推，不含配音、下载素材。启动一整集只付一次。")
    print("- 适配器里要有 llvmpipe、lavapipe 或 SwiftShader，才是没有显卡的 Actions。")
    print("- 这一档简单场景，软件 OpenGL 大约 4 到 45 帧/秒算正常；低于 1 帧/秒不正常。")
    return 0 if godot_ok and three_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    ann = sub.add_parser("annotate")
    ann.add_argument("--json", required=True)
    ann.add_argument("--log", required=True)
    ann.add_argument("--exit", type=int, required=True)
    ann.add_argument("--start", type=float, required=True)
    ann.add_argument("--end", type=float, required=True)
    ann.add_argument("--artifact", default="")

    shown = sub.add_parser("summary")
    shown.add_argument("results")

    args = parser.parse_args()
    if args.cmd == "annotate":
        annotate(args)
        return 0
    return summary(args)


if __name__ == "__main__":
    sys.exit(main())
