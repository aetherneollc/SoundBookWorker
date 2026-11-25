#!/usr/bin/env python3
"""Build and render a ~3 minute four-scene demo episode.

Four generic manifests (corridor, med lab, command deck, observation bay)
share one story. Lines are voiced with edge-tts when the network is available.
Scenes are rendered separately and concatenated.

    python tools/gen_episode_demo.py
    python tools/gen_episode_demo.py --manifests-only
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    'gen_generality_manifests', ROOT / 'tools' / 'gen_generality_manifests.py',
)
gg = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(gg)

from renderer.scene_layout import SceneLayoutSystem
from renderer.synthesize import synthesize_edge_file

OUT = ROOT / 'media' / 'm6_3d' / 'episode_demo'
FPS = 24
VOICES = {
    'linlan': 'zh-CN-XiaoxiaoNeural',
    'chen': 'zh-CN-YunxiNeural',
    'director_zhou': 'zh-CN-YunyangNeural',
    'dr_su': 'zh-CN-XiaoyiNeural',
}
ROVER_MODEL = 'media/assets3d/atuo/atuo_rigged.glb'


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), 'rb') as handle:
        rate = handle.getframerate() or 24000
        return handle.getnframes() / rate


def _voice_line(audio_dir: Path, index: int, speaker: str, text: str) -> tuple[Path, float]:
    path = audio_dir / f'line_{index:02d}.wav'
    voice = VOICES.get(speaker, 'zh-CN-YunyangNeural')
    synthesize_edge_file(text, voice, '+0%', path, speaker=speaker)
    return path, _wav_seconds(path)


def _slate(scene: int, text: str, seconds: float = 2.4) -> dict:
    return gg.beat(
        f'slate_{scene}', scene, seconds,
        action='hold', importance=0.4, preferred_shot='wide',
        description=text, text=text,
    )


_LINE_N = 0


def _line(scene: int, speaker: str, listener: str, text: str, *, emotion: str, listen: str,
          shot: str, action: str = 'talk', importance: float = 0.75, seconds: float = 4.0,
          prop_event: dict | None = None, audio: Path | None = None) -> dict:
    global _LINE_N
    _LINE_N += 1
    return gg.beat(
        f's{scene}_{_LINE_N:02d}_{shot}', scene, seconds,
        speaker=speaker, listener=listener, emotion=emotion, listener_emotion=listen,
        action=action, importance=importance, preferred_shot=shot,
        description=text, text=text,
        prop_event=prop_event,
        audio_file=audio.resolve().as_posix() if audio else None,
        voice_lead=0.28,
    )


def _doc(title: str, story: str, scene_type: str, lighting: str, cast: dict, beats: list[dict],
         props: dict | None = None, rover: dict | None = None) -> dict:
    doc = {
        'version': 1,
        'mode': 'generic',
        'renderer': 'godot3d',
        'language': 'zh',
        'title': title,
        'story': story,
        'width': 1280,
        'height': 720,
        'fps': FPS,
        'scene_type': scene_type,
        'lighting_profile': lighting,
        'enable_cutaways': False,
        'cast': cast,
        'beats': beats,
        'shots': beats,
    }
    if props:
        doc['props'] = props
    if rover:
        doc['rover'] = rover
    return doc


def build_acts(voice: bool) -> list[tuple[str, dict, float]]:
    solver = SceneLayoutSystem()
    corridor = solver.solve('corridor', lighting_profile='dim_corridor', validate=True)
    med = solver.solve('med_lab', lighting_profile='med_lab', validate=True)
    command = solver.solve('command_deck', lighting_profile='night_alert', validate=True)
    bay = solver.solve('observation_bay', lighting_profile='day_bay', validate=True)
    audio_dir = OUT / 'audio'
    audio_dir.mkdir(parents=True, exist_ok=True)

    spoken: list[tuple[str, str, str]] = []

    def take(speaker: str, text: str) -> tuple[Path | None, float]:
        spoken.append((speaker, text, ''))
        index = len(spoken)
        if not voice:
            seconds = 0.85 + 0.16 * len(text)
            return None, seconds
        path, seconds = _voice_line(audio_dir, index, speaker, text)
        print(f'voiced {index:02d} {speaker} {seconds:.2f}s  {text}', flush=True)
        return path, seconds

    # ---- act 1 corridor
    c_lines = [
        ('director_zhou', 'linlan', '林岚，站住。', 'angry', 'worried', 'tracking'),
        ('director_zhou', 'linlan', '南侧阵列的信号断了这么久，你为什么不报？', 'angry', 'worried', 'over_the_shoulder'),
        ('linlan', 'director_zhou', '我在确认来源，周主任。不是故意压着。', 'worried', 'angry', 'close_up'),
        ('linlan', 'director_zhou', '丢包从凌晨三点开始，波形很干净，不像随机噪声。', 'worried', 'angry', 'medium'),
        ('director_zhou', 'linlan', '所以你一个人守在这条走廊里？', 'angry', 'worried', 'two_shot'),
        ('linlan', 'director_zhou', '医疗舱有人值守。我想先排除设备本身的故障。', 'worried', 'angry', 'over_the_shoulder'),
        ('director_zhou', 'linlan', '备用模块在苏医生手里。现在就去取。', 'angry', 'worried', 'medium'),
        ('linlan', 'director_zhou', '我叫上陈工，换完立刻回观测舱。', 'worried', 'angry', 'close_up'),
        ('director_zhou', 'linlan', '风暴比预报更近。别在外面耗时间。', 'angry', 'worried', 'two_shot'),
        ('linlan', 'director_zhou', '明白。我走北门。', 'neutral', 'angry', 'reaction'),
    ]
    c_audio = [take(row[0], row[2]) for row in c_lines]
    walk_s = max(4.2, gg.travel_frames(corridor, 'entry_threshold', 'center_a', 1.35) / FPS + 0.35)
    c_beats = [_slate(1, '第一场 · 昏暗走廊')]
    c_beats.append(gg.beat(
        'approach', 1, walk_s, speaker='director_zhou', listener='linlan',
        emotion='angry', listener_emotion='worried', action='walk', importance=0.7,
        preferred_shot='tracking', description='Zhou strides the dim corridor toward Linlan.',
    ))
    for row, (wav, secs) in zip(c_lines, c_audio):
        c_beats.append(_line(1, row[0], row[1], row[2], emotion=row[3], listen=row[4], shot=row[5],
                             seconds=max(3.4, secs + 0.85), audio=wav))
    c_beats.append(gg.beat('hold_c', 1, 2.6, action='hold', importance=0.4, preferred_shot='wide',
                           description='Hold on the corridor after the order.'))
    c_starts, c_total = gg.frame_starts(tuple(float(b['duration']) for b in c_beats))
    arrive = c_starts[1] + gg.shot_frames(walk_s) - 10
    c_cast = {
        'director_zhou': gg.actor(
            corridor, 'director_zhou', gait='heavy', height=1.80, start_anchor='entry_threshold',
            facing=[gg.face(0, 'linlan')],
            emotions=[gg.emotion(0, c_total, 'angry', 0.88)],
            moves=[gg.walk(c_starts[1], max(c_starts[1] + 12, arrive), 'center_a', 'urgent', ramp_s=0.55)],
        ),
        'linlan': gg.actor(
            corridor, 'linlan', gait='cautious', height=1.65, start_anchor='center_b',
            facing=[gg.face(0, 'director_zhou')],
            emotions=[gg.emotion(0, c_total, 'worried', 0.75)],
        ),
    }

    # ---- act 2 med lab
    m_lines = [
        ('dr_su', 'chen', '陈工，这块传感器的读数不对。', 'worried', 'neutral', 'two_shot', 'talk', None),
        ('dr_su', 'chen', '外壳完好，问题在校准。', 'confident', 'neutral', 'medium', 'inspect', 'inspect'),
        ('chen', 'dr_su', '晶振漂了，还是整块核心废了？', 'neutral', 'confident', 'over_the_shoulder', 'talk', None),
        ('dr_su', 'chen', '核心没坏。换上备件，链路就能回来。', 'confident', 'neutral', 'medium', 'talk', None),
        ('dr_su', 'chen', '拿去，换上这块备件。故障件留在台上。', 'confident', 'neutral', 'two_shot', 'handoff', 'handoff'),
        ('chen', 'dr_su', '我送到观测舱，林岚在窗口等。', 'neutral', 'confident', 'close_up', 'talk', None),
        ('dr_su', 'chen', '装好做一次全量自检，别跳过。', 'confident', 'neutral', 'over_the_shoulder', 'talk', None),
        ('chen', 'dr_su', '三分钟内给你结果。', 'neutral', 'confident', 'reaction', 'talk', None),
        ('dr_su', 'chen', '去吧。指挥台已经拉响警报。', 'worried', 'neutral', 'medium', 'talk', None),
    ]
    m_audio = [take(row[0], row[2]) for row in m_lines]
    m_beats = [_slate(2, '第二场 · 医疗舱')]
    m_beats.append(gg.beat('lab_wide', 2, 3.6, action='hold', importance=0.55, preferred_shot='establishing',
                           description='Wide view of the med lab.'))
    for row, (wav, secs) in zip(m_lines, m_audio):
        m_beats.append(_line(
            2, row[0], row[1], row[2], emotion=row[3], listen=row[4], shot=row[5],
            action=row[6], seconds=max(3.6, secs + 0.9), audio=wav,
        ))
    m_starts, m_total = gg.frame_starts(tuple(float(b['duration']) for b in m_beats))
    handoff_i = 2 + next(i for i, row in enumerate(m_lines) if row[7] == 'handoff')
    inspect_i = 2 + next(i for i, row in enumerate(m_lines) if row[7] == 'inspect')
    inspect_end = m_starts[handoff_i]
    handoff_end = m_starts[handoff_i] + gg.shot_frames(float(m_beats[handoff_i]['duration']))
    transfer = inspect_end + max(18, (handoff_end - inspect_end) // 2)
    doc_x, doc_z = gg.anchor_xz(med, 'exam_doctor')
    handoff_spot = [round(doc_x - 0.92, 3), round(doc_z - 0.12, 3)]
    meet = [
        round((doc_x + handoff_spot[0]) * 0.5, 3),
        1.16,
        round((doc_z + handoff_spot[1]) * 0.5, 3),
    ]
    for item in m_beats:
        if item.get('action') == 'inspect':
            item['prop_event'] = {'prop_id': 'spare_sensor', 'event': 'inspect', 'position': gg.interaction_point(med, 'exam_doctor', 'exam_assistant', 0.42, 1.14)}
        if item.get('action') == 'handoff':
            item['prop_event'] = {'prop_id': 'spare_sensor', 'event': 'handoff', 'position': meet}
    step_from = m_starts[inspect_i] + 8
    step_to = max(step_from + 18, m_starts[handoff_i] - 14)
    m_cast = {
        'dr_su': gg.actor(
            med, 'dr_su', gait='feminine', height=1.62, start_anchor='exam_doctor',
            facing=[gg.face(0, 'chen')],
            emotions=[gg.emotion(0, m_total, 'confident', 0.8)],
        ),
        'chen': gg.actor(
            med, 'chen', gait='casual', height=1.76, start_anchor='exam_assistant',
            facing=[gg.face(0, 'dr_su')],
            emotions=[gg.emotion(0, m_total, 'neutral', 0.5)],
            moves=[{
                'from': step_from, 'to': step_to, 'points': [handoff_spot],
                'ramp_s': 0.35, 'gait': 'casual',
            }],
        ),
    }
    bed = gg.surface_rest(med, 'exam_bed_main_top')
    m_props = {
        'faulty_sensor': {
            'kind': 'sensor', 'entity_role': 'faulty_sensor',
            'model': gg.require_asset(gg.SENSOR_MODEL),
            'surface': {**bed, 'pos': [round(bed['pos'][0] - 0.28, 3), round(bed['pos'][1] + 0.12, 3)], 'yaw_deg': 28.0},
            'timeline': [{'from': 0, 'to': m_total, 'action': 'surface'}],
        },
        'spare_sensor': {
            'kind': 'sensor', 'entity_role': 'spare_sensor',
            'model': gg.require_asset(gg.SENSOR_MODEL),
            'surface': bed,
            'timeline': [
                {'from': 0, 'to': inspect_end, 'action': 'inspect', 'actor': 'dr_su', 'side': 'right'},
                {'from': inspect_end, 'to': handoff_end, 'action': 'handoff', 'from_actor': 'dr_su', 'to_actor': 'chen',
                 'transfer_frame': transfer, 'height': 1.18, 'ramp_s': 0.35},
                {'from': handoff_end, 'to': m_total, 'action': 'held', 'actor': 'chen', 'side': 'right'},
            ],
        },
    }

    # ---- act 3 command
    k_lines = [
        ('director_zhou', 'dr_su', '红色警报。全员到指挥台。', 'angry', 'worried', 'establishing'),
        ('dr_su', 'director_zhou', '生命维持稳定，战术窗口还有三分钟。', 'confident', 'angry', 'medium'),
        ('chen', 'director_zhou', '备用模块已经在路上，观测舱可以接上。', 'neutral', 'angry', 'over_the_shoulder'),
        ('director_zhou', 'chen', '风暴比预报早了四十分钟。', 'angry', 'worried', 'close_up'),
        ('dr_su', 'director_zhou', '窗外的云墙已经压到天线阵列。', 'worried', 'angry', 'two_shot'),
        ('director_zhou', 'dr_su', '优先恢复通信，其余系统维持最低功耗。', 'angry', 'worried', 'medium'),
        ('chen', 'director_zhou', '南侧还有一条能用的馈线。', 'neutral', 'angry', 'reaction'),
        ('chen', 'director_zhou', '我去窗口位，装完立刻回报。', 'worried', 'angry', 'over_the_shoulder'),
        ('director_zhou', 'chen', '全员保持位置。我们撑过这一阵。', 'angry', 'worried', 'wide'),
    ]
    k_audio = [take(row[0], row[2]) for row in k_lines]
    k_walk = max(3.8, gg.travel_frames(command, 'entry_threshold', 'command_south', 1.15) / FPS + 0.3)
    k_beats = [_slate(3, '第三场 · 指挥中心')]
    k_beats.append(gg.beat(
        'enter', 3, k_walk, speaker='director_zhou', listener='dr_su',
        emotion='angry', action='walk', importance=0.8, preferred_shot='establishing',
        description='Chen reaches the holo-table under red alert.',
    ))
    for row, (wav, secs) in zip(k_lines, k_audio):
        k_beats.append(_line(3, row[0], row[1], row[2], emotion=row[3], listen=row[4], shot=row[5],
                             seconds=max(3.5, secs + 0.85), audio=wav, importance=0.82))
    k_starts, k_total = gg.frame_starts(tuple(float(b['duration']) for b in k_beats))
    k_arrive = k_starts[1] + gg.shot_frames(k_walk) - 8
    k_cast = {
        'director_zhou': gg.actor(
            command, 'director_zhou', gait='confident', height=1.80, start_anchor='command_north',
            facing=[gg.face(0, 'chen'), gg.face(k_starts[4], 'dr_su')],
            emotions=[gg.emotion(0, k_total, 'angry', 0.9)],
        ),
        'dr_su': gg.actor(
            command, 'dr_su', gait='feminine', height=1.62, start_anchor='command_west',
            facing=[gg.face(0, 'director_zhou')],
            emotions=[gg.emotion(0, k_total, 'worried', 0.7)],
        ),
        'chen': gg.actor(
            command, 'chen', gait='casual', height=1.76, start_anchor='entry_threshold',
            facing=[gg.face(0, 'director_zhou')],
            emotions=[gg.emotion(0, k_total, 'worried', 0.65)],
            moves=[gg.walk(k_starts[1] + 6, max(k_starts[1] + 18, k_arrive), 'command_south', 'casual', ramp_s=0.4)],
        ),
    }

    # ---- act 4 observation bay
    b_lines = [
        ('linlan', 'chen', '风暴来之前，得把它装好。', 'worried', 'neutral', 'medium', 'talk'),
        ('chen', 'linlan', '拿好，这是最后一个备件。', 'neutral', 'worried', 'two_shot', 'handoff'),
        ('linlan', 'chen', '放心，交给我。', 'confident', 'neutral', 'close_up', 'talk'),
        ('linlan', 'chen', '阿拓，对准南侧阵列，开始引导。', 'confident', 'neutral', 'medium', 'talk'),
        ('chen', 'linlan', '自检绿灯。链路在恢复。', 'neutral', 'confident', 'over_the_shoulder', 'talk'),
        ('chen', 'linlan', '信号回来了。阵列锁定。', 'confident', 'confident', 'reaction', 'talk'),
        ('linlan', 'chen', '窗外的风暴还在压过来。', 'worried', 'neutral', 'close_up', 'talk'),
        ('linlan', 'chen', '窗口还在。我们撑过这场风暴。', 'confident', 'neutral', 'medium', 'talk'),
    ]
    b_audio = [take(row[0], row[2]) for row in b_lines]
    b_walk = max(4.0, gg.travel_frames(bay, 'entry_threshold', 'center_b', 1.2) / FPS + 0.35)
    rover_travel = max(4.4, gg.travel_frames(bay, 'entry_threshold', 'rover_bay_spot', 1.05) / FPS + 0.4)
    b_beats = [_slate(4, '第四场 · 观测舱')]
    b_beats.append(gg.beat('bay_wide', 4, 3.2, action='hold', importance=0.6, preferred_shot='establishing',
                           description='Linlan waits by the lunar window.'))
    b_beats.append(_line(4, b_lines[0][0], b_lines[0][1], b_lines[0][2], emotion=b_lines[0][3], listen=b_lines[0][4],
                         shot=b_lines[0][5], seconds=max(3.6, b_audio[0][1] + 0.85), audio=b_audio[0][0]))
    b_beats.append(gg.beat(
        'chen_enter', 4, b_walk, speaker='chen', listener='linlan', emotion='neutral',
        action='walk', importance=0.7, preferred_shot='tracking',
        description='Chen carries the spare sensor in from the door.',
    ))
    for row, (wav, secs) in zip(b_lines[1:], b_audio[1:]):
        b_beats.append(_line(4, row[0], row[1], row[2], emotion=row[3], listen=row[4], shot=row[5],
                             action=row[6], seconds=max(3.5, secs + 0.9), audio=wav))
    # rover drive sits on the 阿拓 line; insert a dedicated move if that line is short
    rover_i = next(i for i, item in enumerate(b_beats) if '阿拓' in str(item.get('text', '')))
    if float(b_beats[rover_i]['duration']) < rover_travel:
        b_beats[rover_i]['duration'] = round(rover_travel, 2)
    b_starts, b_total = gg.frame_starts(tuple(float(b['duration']) for b in b_beats))
    handoff_bi = next(i for i, item in enumerate(b_beats) if item.get('action') == 'handoff')
    lin_x, lin_z = gg.anchor_xz(bay, 'center_a')
    chen_spot = [round(lin_x + 0.96, 3), round(lin_z + 0.05, 3)]
    bay_meet = [round((lin_x + chen_spot[0]) * 0.5, 3), 1.16, round((lin_z + chen_spot[1]) * 0.5, 3)]
    b_beats[handoff_bi]['prop_event'] = {'prop_id': 'spare_sensor', 'event': 'handoff', 'position': bay_meet}
    enter_i = next(i for i, item in enumerate(b_beats) if item.get('id') == 'chen_enter')
    enter_from = b_starts[enter_i] + 4
    enter_to = max(enter_from + 24, b_starts[handoff_bi] - 12)
    h0 = b_starts[handoff_bi]
    h1 = h0 + gg.shot_frames(float(b_beats[handoff_bi]['duration']))
    transfer_b = h0 + max(16, (h1 - h0) // 2)
    b_cast = {
        'linlan': gg.actor(
            bay, 'linlan', gait='confident', height=1.65, start_anchor='center_a',
            facing=[gg.face(0, 'chen')],
            emotions=[
                gg.emotion(0, b_starts[handoff_bi], 'worried', 0.7),
                gg.emotion(b_starts[handoff_bi], b_total, 'confident', 0.75),
            ],
        ),
        'chen': gg.actor(
            bay, 'chen', gait='casual', height=1.76, start_anchor='entry_threshold',
            facing=[gg.face(0, 'linlan')],
            emotions=[gg.emotion(0, b_total, 'neutral', 0.55)],
            moves=[{
                'from': enter_from, 'to': enter_to, 'points': [chen_spot],
                'ramp_s': 0.45, 'gait': 'casual', 'auto_path': True,
            }],
        ),
    }
    b_props = {
        'spare_sensor': {
            'kind': 'sensor', 'entity_role': 'spare_sensor',
            'model': gg.require_asset(gg.SENSOR_MODEL),
            'timeline': [
                {'from': 0, 'to': h0, 'action': 'held', 'actor': 'chen', 'side': 'right'},
                {'from': h0, 'to': h1, 'action': 'handoff', 'from_actor': 'chen', 'to_actor': 'linlan',
                 'transfer_frame': transfer_b, 'height': 1.16, 'ramp_s': 0.35},
                {'from': h1, 'to': b_total, 'action': 'held', 'actor': 'linlan', 'side': 'right'},
            ],
        },
    }
    rover_from = b_starts[rover_i] + 4
    rover_to = min(b_total - 8, rover_from + gg.shot_frames(rover_travel))
    goal = gg.anchor_xz(bay, 'rover_bay_spot')
    start = gg.anchor_xz(bay, 'entry_threshold')
    path = bay.find_path(start, goal) or [start, goal]
    rover = {
        'id': 'at1',
        'name': '阿拓',
        'model': gg.require_asset(ROVER_MODEL),
        'height_m': 0.95,
        'yaw_offset_deg': 0.0,
        'eye_offset': [0.0, 0.58, 0.05],
        'start': {'pos': [round(start[0], 3), round(start[1], 3)], 'yaw_deg': 180.0},
        'visibility': [{'from': 0, 'visible': False}, {'from': max(0, rover_from - 6), 'visible': True}],
        'moves': [{
            'from': rover_from,
            'to': max(rover_from + 12, rover_to),
            'points': [[round(p[0], 3), round(p[1], 3)] for p in path[1:]],
            'ramp_s': 0.45,
        }],
        'facing': [{'from': rover_to, 'target': 'linlan'}],
    }

    acts = [
        ('act1_corridor', _doc('昏暗走廊', 'Zhou confronts Linlan after the array drops.', 'corridor', 'dim_corridor', c_cast, c_beats), c_total / FPS),
        ('act2_medlab', _doc('医疗舱', 'Dr. Su hands Chen the spare sensor.', 'med_lab', 'med_lab', m_cast, m_beats, m_props), m_total / FPS),
        ('act3_command', _doc('指挥中心', 'Red alert briefing before the storm.', 'command_deck', 'night_alert', k_cast, k_beats), k_total / FPS),
        ('act4_bay', _doc('观测舱', 'Linlan and Chen install the last spare as the storm arrives.', 'observation_bay', 'day_bay', b_cast, b_beats, b_props, rover), b_total / FPS),
    ]
    return acts


def write_manifests(acts: list[tuple[str, dict, float]]) -> list[Path]:
    paths = []
    total = 0.0
    for name, doc, seconds in acts:
        folder = OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'manifest.json'
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'wrote {path.relative_to(ROOT)} ({seconds:.1f}s, {len(doc["beats"])} beats)', flush=True)
        total += seconds
        paths.append(path)
    print(f'episode target {total:.1f}s', flush=True)
    return paths


def render_and_join(paths: list[Path]) -> Path:
    videos = []
    for path in paths:
        folder = path.parent
        name = folder.name + '.mp4'
        cmd = [
            sys.executable, str(ROOT / 'tools' / 'render_episode3d.py'),
            '--manifest', str(path),
            '--out-dir', str(folder),
            '--video-name', name,
        ]
        print('render', name, flush=True)
        subprocess.run(cmd, cwd=ROOT, check=False)
        video = folder / name
        if not video.is_file():
            raise SystemExit(f'missing render {video}')
        videos.append(video)
    concat = OUT / 'concat.txt'
    concat.write_text(''.join(f"file '{video.as_posix()}'\n" for video in videos), encoding='utf-8')
    final = OUT / 'episode_demo.mp4'
    from renderer.mux import ffmpeg_executable
    ffmpeg = ffmpeg_executable()
    subprocess.run([
        ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', str(concat),
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
        '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart',
        str(final),
    ], check=True)
    print(f'joined {final}', flush=True)
    return final


def main() -> None:
    manifests_only = '--manifests-only' in sys.argv
    voice = '--no-tts' not in sys.argv
    try:
        acts = build_acts(voice)
    except Exception as exc:
        if not voice:
            raise
        print(f'TTS failed ({exc}); rebuilding with timed silence.', flush=True)
        acts = build_acts(False)
    paths = write_manifests(acts)
    if manifests_only:
        return
    render_and_join(paths)


if __name__ == '__main__':
    main()
