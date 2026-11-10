import React from 'react';
import {AbsoluteFill, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Audio} from '@remotion/media';
import {Rover, type RoverAction} from './rover';

type LayerSet = {background: string; body: string; head?: string; arm_left?: string; arm_right?: string;
  subject_x: number; subject_y: number; character?: string;
  head_pivot_x?: number; head_pivot_y?: number; arm_left_pivot_x?: number; arm_left_pivot_y?: number;
  arm_right_pivot_x?: number; arm_right_pivot_y?: number;
  mouth?: Record<string, string>; mouth_mode?: 'led'; mouth_x?: number; mouth_y?: number; mouth_width?: number;
  action?: 'idle' | 'talk' | 'wave'};
type StageActor = {cells: Record<string, string>; expression: string; talking: boolean;
  x: number; y: number; scale: number};
type Stage = {background: string; foreground?: string; camera?: string; enter?: string;
  speaker?: string; line?: string; actors: StageActor[]};
type Shot = {src: string; from: number; duration: number; index: number; camera?: string;
  label?: string; fact_card?: string; section?: string; layers?: LayerSet; stage?: Stage; scene_number?: number;
  technical_overlay?: boolean; rover?: {action: RoverAction; speaker: string}; storyboard?: string; storyboard_phase?: number};
type Track = {src: string; from: number; volume: number; loop: boolean};
type Caption = {from: number; to: number; text: string};
type Speech = {from: number; to: number; value: string; speaker: string; scene_number: number};
type Card = {name: string; tagline?: string; url?: string; qr?: string};
export type SoundBookProps = {width: number; height: number; fps: number; durationInFrames: number;
  introFrames: number; shots: Shot[]; audio: Track[]; captions: Caption[]; speech: Speech[];
  episodeNumber: number; title: string; language: string; cta: Card | null;
  ctaFrom: number; ctaFrames: number};

const font = '"Noto Sans CJK SC", "Noto Sans", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const lerp = (frame: number, start: number, end: number, a: number, b: number) =>
  interpolate(frame, [start, Math.max(start + 1, end)], [a, b], clamp);

const scienceBoards: Record<string, {heading: string; rows: string[]; accent: string}> = {
  assembly: {heading: '模块集成', rows: ['底盘 / 四轮腿', '机械臂 / 两级关节', '电源 / 隔离开关'], accent: '#71cef2'},
  arm: {heading: '关节检查', rows: ['肩轴 → 伸出', '肘轴 → 抓取', '力矩 → 反馈'], accent: '#75d9b2'},
  boot: {heading: '通电自检', rows: ['驱动器 ONLINE', '关节编码器 ONLINE', '惯导 IMU ONLINE'], accent: '#85e4bc'},
  sensors: {heading: '传感器校准', rows: ['视觉定位', '惯性测量', '关节零位'], accent: '#7bcaf4'},
  stand: {heading: '站立测试', rows: ['支撑多边形', '质心投影', '缓慢抬升'], accent: '#f4c77d'},
  balance: {heading: '失稳预警', rows: ['机械臂伸出', '质心向前移动', '保护阈值触发'], accent: '#fa9079'},
  fall: {heading: '紧急制动', rows: ['先切断前进指令', '机械臂尝试支撑', '记录倒地姿态'], accent: '#fa9079'},
  operator: {heading: '人工复核', rows: ['检查时间戳', '核对姿态记录', '确认测试边界'], accent: '#f0ca8d'},
  diagnosis: {heading: '根因分析', rows: ['原模型：底盘质量', '漏算：机械臂动态质量', '修正：实时质心补偿'], accent: '#b2b4fa'},
  telemetry: {heading: '故障日志', rows: ['IMU 倾角', '关节电流', '安全回路状态'], accent: '#78cae9'},
  review: {heading: '验收记录', rows: ['受控跌倒：已记录', '恢复动作：待复核', '自主停止：下一阶段'], accent: '#f0ca8d'},
  recover: {heading: '恢复站立', rows: ['重算支撑区域', '降低机械臂', '逐步抬升底盘'], accent: '#82ddbb'},
};

const ScienceBoard = ({kind, frame, fps, width, height, phase}: {kind: string; frame: number; fps: number; width: number; height: number; phase: number}) => {
  const board = scienceBoards[kind];
  if (!board) return null;
  const appear = spring({frame, fps, config: {damping: 19, stiffness: 100}});
  const alert = kind === 'fall' || kind === 'balance';
  return <div style={{position: 'absolute', ...(phase ? {left: width * .045} : {right: width * .045}),
    top: height * .18, width: width * .31,
    padding: '20px 22px', border: `2px solid ${board.accent}`, borderRadius: 12,
    color: '#f4fbff', background: '#07172be8', boxShadow: '0 12px 38px #06101ea8',
    transform: `translateX(${(1-appear)*(phase ? -70 : 70)}px)`, opacity: appear}}>
    <div style={{fontSize: 19, letterSpacing: 3, color: board.accent, fontWeight: 900}}>{board.heading}</div>
    <div style={{height: 2, background: board.accent, opacity: .65, margin: '13px 0'}} />
    {board.rows.map((row, i) => {
      const show = spring({frame: frame - fps * (i * .42 + .25), fps, config: {damping: 18, stiffness: 110}});
      const active = alert && i === 2;
      return <div key={row} style={{display: 'flex', gap: 12, alignItems: 'center', margin: '13px 0',
        fontSize: 21, fontWeight: 700, opacity: show, transform: `translateY(${(1-show)*12}px)`}}>
        <span style={{width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
          background: active ? (Math.sin(frame*.35)>0 ? '#fff' : board.accent) : board.accent,
          boxShadow: `0 0 10px ${board.accent}`}} />{row}
      </div>;
    })}
    {['stand','balance','fall','recover'].includes(kind) && <svg width="100%" height="95" viewBox="0 0 320 95" style={{marginTop: 8}}>
      <path d="M20 72 L300 72 M60 72 L90 49 L242 49 L270 72" fill="none" stroke="#84a8b8" strokeWidth="2" />
      <path d="M90 49 L242 49" stroke={board.accent} strokeWidth="5" />
      <path d={`M ${kind==='fall'||kind==='balance' ? 168+Math.min(95,frame/fps*28) : 168+Math.sin(frame/fps*1.8)*6} 19 L ${kind==='fall'||kind==='balance' ? 168+Math.min(95,frame/fps*28) : 168+Math.sin(frame/fps*1.8)*6} 58`}
        stroke={board.accent} strokeWidth="3" strokeDasharray="5 4" />
      <circle cx={kind==='fall'||kind==='balance' ? 168+Math.min(95,frame/fps*28) : 168+Math.sin(frame/fps*1.8)*6} cy="18" r="9" fill={board.accent} />
      <text x="8" y="19" fill="#e9f6fc" fontSize="12">质心投影</text>
    </svg>}
    {['assembly','arm','boot','sensors','diagnosis','telemetry','operator','review'].includes(kind) &&
      <svg width="100%" height="95" viewBox="0 0 320 95" style={{marginTop: 8}}>
        <path d="M13 79 H307 M13 79 V13" fill="none" stroke="#6a94aa" strokeWidth="2" />
        {kind === 'assembly' || kind === 'arm' ? <>
          <rect x={lerp(frame,0,fps*2,58,99)} y="31" width="88" height="38" rx="6" fill="none" stroke={board.accent} strokeWidth="4" />
          <path d={`M ${lerp(frame,0,fps*2,270,190)} 53 L ${lerp(frame,0,fps*2,234,220)} 28 L 259 22`}
            fill="none" stroke={board.accent} strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx={lerp(frame,0,fps*2,234,220)} cy="28" r="7" fill="#102638" stroke={board.accent} strokeWidth="3" />
          <circle cx="120" cy="72" r="13" fill="#102638" stroke={board.accent} strokeWidth="4" />
          <circle cx="166" cy="72" r="13" fill="#102638" stroke={board.accent} strokeWidth="4" />
        </> : kind === 'sensors' ? <>
          <circle cx="160" cy="53" r="35" fill="none" stroke="#6089a0" strokeWidth="2" />
          <circle cx="160" cy="53" r="20" fill="none" stroke="#6089a0" strokeWidth="2" />
          <path d={`M160 53 L${160+35*Math.cos(frame/16)} ${53+35*Math.sin(frame/16)}`}
            stroke={board.accent} strokeWidth="4" />
          <circle cx="175" cy="42" r="4" fill={board.accent} />
        </> : <>
          <path d="M16 54 L53 54 L70 40 L84 68 L101 34 L117 53 L142 53 L158 41 L173 61 L190 50 L220 50 L240 37 L260 62 L278 52 L305 52"
            fill="none" stroke={board.accent} strokeWidth="3" strokeDasharray="520"
            strokeDashoffset={lerp(frame,0,fps*3,520,0)} />
          <circle cx={lerp(frame,0,fps*3,16,305)} cy="52" r="4" fill="#fff" />
        </>}
      </svg>}
    <div style={{marginTop: 8, height: 4, background: '#1b3446', overflow: 'hidden'}}>
      <div style={{height: '100%', width: `${lerp(frame,0,fps*3,0,100)}%`, background: board.accent}} />
    </div>
  </div>;
};

const Ambient = ({frame, width, height}: {frame: number; width: number; height: number}) => <>
  <div style={{position: 'absolute', inset: 0, background: 'radial-gradient(circle at 72% 32%, #234b74 0%, #101b32 44%, #07101f 100%)'}} />
  {Array.from({length: 25}, (_, i) => {
    const x = (i * 193 + 81) % width, y = (i * 131 + 53) % height;
    const drift = Math.sin(frame / 40 + i) * 9;
    return <div key={i} style={{position: 'absolute', left: x + drift, top: y,
      width: i % 7 === 0 ? 3 : 1.5, height: i % 7 === 0 ? 3 : 1.5, borderRadius: '50%',
      background: '#d9ecff', opacity: .25 + .25 * Math.sin(frame / 22 + i)}} />;
  })}
</>;

const Intro = ({props}: {props: SoundBookProps}) => {
  const frame = useCurrentFrame(), {fps, width, height} = useVideoConfig();
  const enter = spring({fps, frame, config: {damping: 18, stiffness: 105}});
  const title = props.title.replace(/^第\s*\d+\s*[集話话]\s*[｜|·:：\-—]?\s*/u, '')
    .replace(/^Episode\s+\d+\s*[｜|·:：\-—]?\s*/iu, '') || props.title;
  const episode = ({zh: `第 ${props.episodeNumber} 集`, en: `EPISODE ${props.episodeNumber}`,
    ja: `第 ${props.episodeNumber} 話`, ko: `제 ${props.episodeNumber} 화`} as Record<string, string>)[props.language] || `EPISODE ${props.episodeNumber}`;
  return <AbsoluteFill style={{overflow: 'hidden', color: 'white', fontFamily: font}}>
    <Ambient frame={frame} width={width} height={height} />
    <div style={{position: 'absolute', width: width * .76, height: width * .76, border: '2px solid #f5cf78',
      borderRadius: '50%', left: width * .12, top: -width * .52,
      transform: `rotate(${frame * .5}deg)`, opacity: .5}} />
    <div style={{position: 'absolute', width: width * .56, height: width * .56, border: '1px solid #8cc4ec',
      borderRadius: '50%', left: width * .22, top: -width * .36,
      transform: `rotate(${-frame * .8}deg)`, opacity: .4}} />
    <div style={{position: 'absolute', left: width * .11, right: width * .11, top: height * .17,
      height: height * .65, background: 'linear-gradient(130deg,#132d50ee,#0a182de8)',
      border: '2px solid #f4cb73', boxShadow: '0 24px 65px #0009', borderRadius: 24,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      transform: `translateY(${(1 - enter) * 100}px) scale(${.9 + .1 * enter})`, opacity: enter}}>
      <div style={{fontSize: height * .055, fontWeight: 800, color: '#f6d27d', letterSpacing: 4}}>{episode}</div>
      <div style={{width: width * .32, height: 3, background: '#f6d27d', margin: '27px 0'}} />
      <div style={{fontSize: Math.min(height * .078, width * .75 / Math.max(8, title.length) * 1.5),
        fontWeight: 900, maxWidth: '82%', textAlign: 'center', lineHeight: 1.35,
        textShadow: '0 4px 15px #000b'}}>{title}</div>
    </div>
    <div style={{position: 'absolute', left: 0, bottom: 0, width: `${lerp(frame, 0, props.introFrames, 0, 100)}%`,
      height: 8, background: '#f6d27d'}} />
  </AbsoluteFill>;
};

const StageShot = ({shot, width, height, speech}: {shot: Shot; width: number; height: number; speech: Speech[]}) => {
  const frame = useCurrentFrame(), {fps} = useVideoConfig();
  const stage = shot.stage!;
  const p = Math.min(1, frame / Math.max(1, shot.duration - 1));
  const science = Boolean(shot.storyboard && scienceBoards[shot.storyboard]);
  const sciencePhase = science ? Number(shot.storyboard_phase || 0) : 0;
  const operatorShot = science && stage.actors.length > 0;
  const detailShot = science && (sciencePhase === 1 || ['arm', 'sensors', 'balance', 'diagnosis', 'telemetry'].includes(shot.storyboard || ''));
  const push = /push|推|近/.test(stage.camera || '');
  const pull = /pull|拉远|后退/.test(stage.camera || '');
  const cam = science ? (detailShot ? lerp(p, 0, 1, 1.27, 1.38) : lerp(p, 0, 1, 1.05, 1.17))
    : push ? lerp(p, 0, 1, 1.04, 1.16) : pull ? lerp(p, 0, 1, 1.16, 1.04) : lerp(p, 0, 1, 1.07, 1.13);
  const driftX = (shot.index % 2 ? -1 : 1) * lerp(p, 0, 1, 0, width * (science ? .04 : .025));
  const enter = stage.enter === 'slide'
    ? spring({fps, frame, config: {damping: 16, stiffness: 80}})
    : 1;
  const titleEnter = spring({fps, frame, config: {damping: 20, stiffness: 130}});
  const fontFamily = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';
  const speaker = stage.actors.find(actor => actor.talking) || stage.actors[0];
  return <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#0c1220', fontFamily}}>
    <AbsoluteFill style={{transform: `translateX(${driftX}px) scale(${cam})`,
      transformOrigin: sciencePhase ? '76% 67%' : detailShot ? '24% 67%' : '52% 64%'}}>
      <Img src={staticFile(stage.background)} style={{width: '100%', height: '100%', objectFit: 'cover',
        filter: science ? 'brightness(.86) saturate(.82) contrast(1.08)' : 'brightness(1.08) saturate(1.08)'}} />
      {!operatorShot && stage.actors.map((actor, i) => {
        const base = actor.cells[actor.expression] || actor.cells.neutral;
        if (!base) return null;
        const speakingNow = speech.some(cue => cue.scene_number === shot.scene_number
          && cue.speaker === stage.speaker && cue.value !== 'X'
          && shot.from + frame >= cue.from && shot.from + frame < cue.to);
        const flap = actor.talking && speakingNow && !!actor.cells.talk_delta
          && (actor.expression === 'neutral' || actor.expression === 'talk');
        const breath = Math.sin(frame / (fps * 0.95) + i);
        const boxH = height * 0.64 * actor.scale;
        const boxW = boxH * 0.86;
        const left = width * actor.x - boxW / 2;
        const anchorY = stage.foreground ? actor.y : Math.max(actor.y, 1.02);
        const top = height * anchorY - boxH + (1 - enter) * 36 + breath * 2.2;
        const pose = {width: '100%', height: '100%', objectFit: 'contain' as const,
          objectPosition: 'center bottom', filter: 'brightness(1.08) saturate(1.06)'};
        return <div key={i}>
          <div style={{position: 'absolute', left: left + boxW * 0.22, top: top + boxH - 10,
            width: boxW * 0.5, height: 16, borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(0,0,0,.22), transparent 70%)'}} />
          <div style={{position: 'absolute', left, top, width: boxW, height: boxH,
            transform: `scaleY(${1 + breath * 0.006})`, transformOrigin: 'center bottom',
            maskImage: stage.foreground ? undefined : 'linear-gradient(to bottom, #000 88%, transparent 100%)',
            WebkitMaskImage: stage.foreground ? undefined : 'linear-gradient(to bottom, #000 88%, transparent 100%)'}}>
            <Img src={staticFile(base)} style={pose} />
            {flap && <Img src={staticFile(actor.cells.talk_delta)} style={{...pose, position: 'absolute', inset: 0}} />}
          </div>
        </div>;
      })}
      {shot.rover && <Rover frame={frame} fps={fps} duration={shot.duration} action={shot.rover.action}
        speaking={speech.some(cue => cue.scene_number === shot.scene_number && cue.speaker === shot.rover?.speaker
          && cue.value !== 'X' && cue.value !== 'A' && shot.from + frame >= cue.from && shot.from + frame < cue.to)}
        width={width * (detailShot ? .58 : .49)} height={height * (detailShot ? .75 : .60)}
        left={science ? (sciencePhase ? '45%' : detailShot ? '1%' : '5%') : '43%'} />}
      {stage.foreground && <Img src={staticFile(stage.foreground)} style={{position: 'absolute', inset: 0,
        width: '100%', height: '100%', objectFit: 'cover'}} />}
    </AbsoluteFill>
    {operatorShot && stage.actors.slice(0, 1).map((actor, i) => {
      const base = actor.cells[actor.expression] || actor.cells.neutral;
      if (!base) return null;
      const bob = Math.sin(frame / (fps * 1.1)) * 3;
      return <div key={i} style={{position: 'absolute', left: width * (sciencePhase ? .55 : .065), top: height * .25,
        width: width * .38, height: height * .53, overflow: 'hidden',
        border: '5px solid #80b8d1', borderRadius: 16, background: '#11324a',
        boxShadow: '0 18px 50px #03101dba', transform: `translateY(${bob}px)`}}>
        <Img src={staticFile(base)} style={{position: 'absolute', left: '5%', bottom: '-15%', width: '90%', height: '116%',
          objectFit: 'contain', objectPosition: 'center bottom', filter: 'saturate(.76) contrast(1.08)'}} />
        <div style={{position: 'absolute', inset: 0, pointerEvents: 'none',
          background: 'repeating-linear-gradient(0deg,transparent 0px,transparent 4px,#a2e4ff12 5px)',
          boxShadow: 'inset 0 0 35px #071a2c'}} />
        <div style={{position: 'absolute', top: 10, left: 14, color: '#d3f6ff', fontSize: 16,
          fontWeight: 800, letterSpacing: 2}}>AETHERNEO / TEST CONTROL</div>
        <div style={{position: 'absolute', bottom: 10, right: 14, color: '#89d7ef', fontSize: 16,
          fontWeight: 800}}>● LIVE</div>
      </div>;
    })}
    {science && <ScienceBoard kind={shot.storyboard || ''} frame={frame} fps={fps} width={width} height={height} phase={sciencePhase} />}
    {shot.label && <div style={{position: 'absolute', top: height * .055, left: width * .055,
      maxWidth: width * .43, padding: '10px 17px', borderLeft: '5px solid #f0a73f',
      background: '#08192bc9', color: '#fff', textShadow: '0 2px 7px #000',
      transform: `translateX(${(1-titleEnter)*-35}px)`, opacity: titleEnter}}>
      {shot.section && <div style={{fontSize: 16, color: '#f5c777', fontWeight: 800, letterSpacing: 2}}>{shot.section}</div>}
      <div style={{fontSize: 28, lineHeight: 1.3, fontWeight: 850}}>{shot.label}</div>
    </div>}
    {stage.line && speaker && <div style={{position: 'absolute',
      left: speaker.x > 0.58 ? width * 0.06 : width * 0.46,
      top: height * 0.07, width: width * 0.46, background: '#fff', color: '#1b1b1b',
      border: '4px solid #161616', borderRadius: 16, padding: '18px 22px 16px',
      boxShadow: '0 10px 24px rgba(0,0,0,.28)'}}>
      {stage.speaker && <div style={{position: 'absolute', top: -16, left: 18, background: '#161616',
        color: '#f6d27d', fontSize: 16, fontWeight: 800, letterSpacing: 1,
        padding: '3px 10px', borderRadius: 8}}>{stage.speaker}</div>}
      <div style={{fontSize: 32, fontWeight: 850, lineHeight: 1.35, textAlign: 'center'}}>{stage.line}</div>
      <div style={{position: 'absolute', bottom: -18, left: speaker.x > 0.58 ? '78%' : 42, width: 0, height: 0,
        borderLeft: '10px solid transparent', borderRight: '10px solid transparent',
        borderTop: '18px solid #161616'}} />
    </div>}
  </AbsoluteFill>;
};

const MotionShot = ({shot, width, height, speech}: {shot: Shot; width: number; height: number; speech: Speech[]}) => {
  const frame = useCurrentFrame(), {fps} = useVideoConfig();
  const p = Math.min(1, frame / Math.max(1, shot.duration - 1));
  const camera = (shot.camera || '').toLowerCase();
  const left = /左|left/.test(camera), right = /右|right/.test(camera), pull = /拉远|后退|pull|zoom out/.test(camera);
  const direction = left ? 1 : right ? -1 : shot.index % 2 ? 1 : -1;
  const scale = pull ? lerp(p, 0, 1, 1.18, 1.04) : lerp(p, 0, 1, 1.04, 1.18);
  const panX = direction * lerp(p, 0, 1, -width * .025, width * .025);
  const panY = lerp(p, 0, 1, height * .018, -height * .018);
  const settle = spring({fps, frame, config: {damping: 22, stiffness: 160}});
  const exit = lerp(frame, shot.duration - Math.round(fps * .22), shot.duration, 1, 0);
  const flash = Math.max(0, 1 - frame / 6) * .35;
  const accent = shot.index % 2 === 0 ? '#81d6f0' : '#f3d184';
  const layers = shot.layers;
  const subjectSide = layers && layers.subject_x < .5 ? -1 : 1;
  const alive = spring({fps, frame, config: {damping: 19, stiffness: 90}});
  const breath = Math.sin(frame / (fps * .9) + shot.index * 1.3);
  const bodyX = subjectSide * (1 - alive) * 18 + Math.sin(frame / (fps * 1.7)) * 7;
  const bodyY = (1 - alive) * 12 + breath * 3;
  const headNod = Math.sin(frame / (fps * 1.35) + shot.index) * .9;
  const headY = Math.sin(frame / (fps * 1.35) + shot.index) * 2.5;
  const actor = layers?.character || shot.rover?.speaker;
  const activeSpeech = speech.find(c => frame + shot.from >= c.from && frame + shot.from < c.to &&
    (!actor || c.speaker === actor) && c.scene_number === Number(shot.scene_number || 0));
  const speaking = activeSpeech && activeSpeech.value !== 'X' && activeSpeech.value !== 'A';
  const mouthShape = activeSpeech?.value || 'X';
  const mouthSprite = layers?.mouth?.[mouthShape] || layers?.mouth?.[speaking ? 'D' : 'X'];
  const mouthSize = width * (layers?.mouth_width || .08);
  const imageStyle = {position: 'absolute' as const, inset: 0, width: '100%', height: '100%', objectFit: 'cover' as const,
    filter: 'contrast(1.08) saturate(1.1)'};
  return <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#101827', color: 'white', fontFamily: font}}>
    <Img src={staticFile(layers?.background || shot.src)} style={{position: 'absolute', width: '110%', height: '110%',
      top: '-5%', left: '-5%', objectFit: 'cover', transform: `scale(${scale}) translate(${panX * .35}px,${panY * .35}px)`,
      filter: 'blur(17px) brightness(.57) saturate(.9)'}} />
    {layers ? <>
      <Img src={staticFile(layers.background)} style={{...imageStyle,
        transform: `scale(${scale}) translate(${panX * .58}px,${panY * .58}px)`}} />
      <Img src={staticFile(layers.body)} style={{...imageStyle,
        transformOrigin: `${layers.subject_x * 100}% ${layers.subject_y * 100}%`,
        transform: `scale(${scale}) translate(${panX + bodyX}px,${panY + bodyY}px) scaleY(${1 + breath * .003})`}} />
      {(['arm_left', 'arm_right'] as const).map((part, partIndex) => {
        const src = layers[part];
        if (!src) return null;
        const pivotX = part === 'arm_left' ? layers.arm_left_pivot_x : layers.arm_right_pivot_x;
        const pivotY = part === 'arm_left' ? layers.arm_left_pivot_y : layers.arm_right_pivot_y;
        const wave = layers.action === 'wave' && part === 'arm_right'
          ? lerp(frame, 0, fps * .55, 0, -18) + Math.sin(frame / (fps * .25)) * 5 : 0;
        const talk = speaking && partIndex === 1 ? Math.sin(frame / (fps * .24)) * 3 : 0;
        return <Img key={part} src={staticFile(src)} style={{...imageStyle,
          transformOrigin: `${(pivotX || layers.subject_x) * 100}% ${(pivotY || layers.subject_y) * 100}%`,
          transform: `scale(${scale}) translate(${panX + bodyX}px,${panY + bodyY}px) rotate(${Math.sin(frame / (fps * 1.2) + partIndex * 2) * 2.3 + talk + wave}deg)`}} />;
      })}
      {layers.head && <Img src={staticFile(layers.head)} style={{...imageStyle,
        transformOrigin: `${(layers.head_pivot_x || layers.subject_x) * 100}% ${(layers.head_pivot_y || layers.subject_y) * 100}%`,
        transform: `scale(${scale}) translate(${panX + bodyX}px,${panY + bodyY + headY}px) rotate(${headNod + (speaking ? .7 : 0)}deg)`}} />}
      {mouthSprite && <Img src={staticFile(mouthSprite)} style={{...imageStyle,
        transformOrigin: `${(layers.head_pivot_x || layers.subject_x) * 100}% ${(layers.head_pivot_y || layers.subject_y) * 100}%`,
        transform: `scale(${scale}) translate(${panX + bodyX}px,${panY + bodyY + headY}px) rotate(${headNod + (speaking ? .7 : 0)}deg)`}} />}
      {layers.mouth_mode === 'led' && typeof layers.mouth_x === 'number' && typeof layers.mouth_y === 'number' &&
        <div style={{position: 'absolute', inset: 0, pointerEvents: 'none',
          transformOrigin: `${(layers.head_pivot_x || layers.subject_x) * 100}% ${(layers.head_pivot_y || layers.subject_y) * 100}%`,
          transform: `scale(${scale}) translate(${panX + bodyX}px,${panY + bodyY + headY}px) rotate(${headNod}deg)`}}>
          <div style={{position: 'absolute', left: layers.mouth_x * width - mouthSize / 2,
            top: layers.mouth_y * height - mouthSize * .18, width: mouthSize, height: mouthSize * .34,
            borderRadius: 20, background: speaking ? '#ffbc40' : '#cc6916',
            opacity: speaking ? .68 : .22, boxShadow: speaking ? '0 0 20px 8px #ff9d35a0' : 'none',
            transform: `scaleY(${speaking ? mouthShape === 'D' ? 1.7 : .9 : .35})`}} />
        </div>}
    </> : <Img src={staticFile(shot.src)} style={{...imageStyle,
      transform: `scale(${scale}) translate(${panX}px,${panY}px)`}} />}
    {shot.rover && <Rover frame={frame} fps={fps} duration={shot.duration} action={shot.rover.action}
      speaking={Boolean(speaking)} width={width * .50} height={height * .60} />}
    <div style={{position: 'absolute', inset: 0,
      background: 'linear-gradient(90deg,#0814268a,transparent 35%,transparent 70%,#07121e45),linear-gradient(0deg,#07111dc7,transparent 29%)'}} />
    {/* A scanning beam, technical grid and connected markers animate as part of the world. */}
    {shot.technical_overlay !== false && <div style={{position: 'absolute', left: `${lerp(p, 0, 1, -20, 110)}%`, top: 0,
      height: '100%', width: width * .12, transform: 'skewX(-16deg)',
      background: `linear-gradient(90deg, transparent, ${accent}18, transparent)`, opacity: .75}} />}
    {shot.technical_overlay !== false && shot.index % 3 !== 2 && <svg width={width} height={height} style={{position: 'absolute', inset: 0, opacity: .78}}>
      <path d={`M ${width*.72} ${height*.22} L ${width*.84} ${height*.22} L ${width*.9} ${height*.31}`}
        fill="none" stroke={accent} strokeWidth="2" strokeDasharray="280"
        strokeDashoffset={lerp(frame, 0, fps * .8, 280, 0)} />
      <circle cx={width*.72} cy={height*.22} r={6 + 3 * Math.sin(frame * .13)} fill={accent} />
      <circle cx={width*.9} cy={height*.31} r="5" fill={accent} />
      <circle cx={width*.9} cy={height*.31} r={lerp(frame % 34, 0, 34, 6, 33)} fill="none"
        stroke={accent} strokeWidth="2" opacity={lerp(frame % 34, 0, 34, .8, 0)} />
    </svg>}
    {(shot.label || shot.section) && <div style={{position: 'absolute', top: height * .06, left: width * .055,
      display: 'flex', gap: 18, alignItems: 'center', transform: `translateX(${(1-settle)*-60}px)`, opacity: settle}}>
      <div style={{width: 7, height: 63, background: accent}} />
      <div><div style={{fontSize: 18, color: accent, fontWeight: 800, letterSpacing: 2}}>{shot.section}</div>
        <div style={{fontSize: 32, fontWeight: 850, textShadow: '0 3px 14px #000'}}>{shot.label}</div></div>
    </div>}
    {shot.fact_card && <div style={{position: 'absolute', left: width * .08, right: width * .08,
      bottom: height * .19, padding: '16px 26px', borderLeft: `5px solid ${accent}`,
      background: '#08192bc9', borderRadius: 8, fontSize: 24, fontWeight: 700,
      transform: `translateY(${(1-settle)*45}px)`, opacity: settle}}>{shot.fact_card}</div>}
    <div style={{position: 'absolute', inset: 0, background: `white`, opacity: flash}} />
    <div style={{position: 'absolute', inset: 0, background: '#07101f', opacity: 1-exit}} />
  </AbsoluteFill>;
};

const CaptionLayer = ({captions, width, height}: {captions: Caption[]; width: number; height: number}) => {
  const frame = useCurrentFrame(), {fps} = useVideoConfig();
  const cue = captions.find(c => frame >= c.from && frame < c.to);
  if (!cue) return null;
  const reveal = spring({fps, frame: frame-cue.from, config: {damping: 20, stiffness: 200}});
  return <div style={{position: 'absolute', left: width * .08, right: width * .08, bottom: height * .045,
    minHeight: height * .11, padding: '12px 20px', borderRadius: 12, background: '#07111ee0',
    color: '#fff', fontFamily: font, fontSize: Math.max(26, Math.round(height * .041)),
    fontWeight: 850, textAlign: 'center', lineHeight: 1.4, textShadow: '0 2px 5px #000',
    transform: `translateY(${(1-reveal)*18}px)`, opacity: reveal,
    display: 'flex', justifyContent: 'center', alignItems: 'center'}}>{cue.text}</div>;
};

const EndCard = ({card, frames}: {card: Card; frames: number}) => {
  const frame = useCurrentFrame(), {fps, width, height} = useVideoConfig();
  const appear = spring({fps, frame, config: {damping: 17, stiffness: 110}});
  return <AbsoluteFill style={{overflow: 'hidden', fontFamily: font, color: 'white'}}>
    <Ambient frame={frame} width={width} height={height} />
    <div style={{position: 'absolute', width: width*.7, height: width*.7, border: '2px solid #8dd8eb77',
      borderRadius: '50%', left: width*.15, top: -height*.2, transform: `rotate(${frame}deg)`}} />
    <div style={{position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', transform: `scale(${.86 + appear*.14})`, opacity: appear}}>
      <div style={{fontSize: height*.095, fontWeight: 900}}>{card.name}</div>
      {card.tagline && <div style={{fontSize: height*.042, color: '#f5ce78', marginTop: 16}}>{card.tagline}</div>}
      {card.qr && <Img src={staticFile(card.qr)} style={{width: height*.23, height: height*.23, marginTop: 26, borderRadius: 8}} />}
      {card.url && <div style={{fontSize: height*.025, color: '#bbdceb', marginTop: 20}}>{card.url}</div>}
    </div>
    <div style={{position: 'absolute', bottom: 0, width: `${lerp(frame, 0, frames, 0, 100)}%`, height: 6, background: '#f5ce78'}} />
  </AbsoluteFill>;
};

export const SoundBook = (props: SoundBookProps) => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background: '#07101f'}}>
    {props.introFrames > 0 && <Sequence from={0} durationInFrames={props.introFrames}><Intro props={props} /></Sequence>}
    {props.shots.map(shot => <Sequence key={`${shot.index}-${shot.from}`} from={shot.from} durationInFrames={shot.duration}>
      {shot.stage ? <StageShot shot={shot} width={props.width} height={props.height} speech={props.speech || []} />
        : <MotionShot shot={shot} width={props.width} height={props.height} speech={props.speech || []} />}
    </Sequence>)}
    {props.cta && props.ctaFrames > 0 && <Sequence from={props.ctaFrom} durationInFrames={props.ctaFrames}>
      <EndCard card={props.cta} frames={props.ctaFrames} />
    </Sequence>}
    <CaptionLayer captions={props.captions} width={props.width} height={props.height} />
    {props.audio.map((track, i) => <Audio key={i} src={staticFile(track.src)} from={track.from}
      volume={track.volume} loop={track.loop} />)}
  </AbsoluteFill>;
};
