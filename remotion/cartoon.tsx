import React from 'react';
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Rover, type RoverAction} from './rover';

export type CartoonAction = 'idle' | 'walk' | 'work' | 'point' | 'talk' | 'react' | 'carry' | 'console' | 'enter' | 'drive';
export type CartoonRoom = 'exterior' | 'interior';
export type CartoonWeather = 'clear' | 'dust' | 'rain';
export type CartoonLight = 'day' | 'night' | 'lamp';
/** Optional pixel_story state. CartoonShot does not read it. */
export type CartoonWorld = {room?: CartoonRoom; weather?: CartoonWeather; light?: CartoonLight};
export type CartoonActorPlan = {name: string; action: CartoonAction; from_x: number; to_x: number; portrait?: string; mouth?: string; body?: string};
export type StageAnchor = {x: number; depth: number; surface: 'floor' | 'test_platform'};
export type StageLayout = {version: 1; coordinate_space: {width: 320; height: 180};
  dimensions: {actor_height: number; rover_width: number; rover_height: number};
  floor: {far_y: number; near_y: number; vanishing_x: number};
  actors: Record<string, StageAnchor>; rover: StageAnchor; setpiece: {id: string; depth: number}};
export type CartoonActionEvent = {kind: 'move' | 'reach' | 'press' | 'secure' | 'open' | 'close' | 'vibrate' | 'sample' | 'signal' | 'inspect' | 'fall' | 'recover';
  subject: 'character' | 'rover' | 'environment';
  target: 'fairing' | 'cable' | 'latch' | 'test_platform' | 'rover' | 'panel' | 'ground';
  start: number; contact: number; end: number; from_state: string; to_state: string};
export type CartoonPlan = {actors: CartoonActorPlan[]; prop?: 'tool' | 'battery' | 'sensor' | 'console' | 'crate' | 'none';
  focus?: 'actor' | 'robot' | 'prop' | 'wide'; world?: CartoonWorld; stage_layout?: StageLayout; action_event?: CartoonActionEvent};
type Cue = {from: number; to: number; value: string; speaker: string; scene_number: number};
type Stage = {background: string; foreground?: string};
type Shot = {from: number; duration: number; index: number; label?: string; scene_number?: number;
  stage?: Stage; src: string; rover?: {action: RoverAction; speaker: string}; cartoon: CartoonPlan; cartoon_phase?: number};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const eased = (frame: number, duration: number) => {
  const p = Math.max(0, Math.min(1, frame / Math.max(1, duration)));
  return p * p * (3 - 2 * p);
};
const appearance = (name: string) => {
  if (/林岚|Lin Lan/i.test(name)) return {hair: '#192b54', hairLight: '#5275b7', suit: '#1c5d9c', trim: '#f4a54b', skin: '#dca990', short: true};
  if (/顾承宇|Gu Chengyu/i.test(name)) return {hair: '#222c38', hairLight: '#56657b', suit: '#394b66', trim: '#f3c476', skin: '#d6a087', short: true};
  if (/周岳|Zhou Yue/i.test(name)) return {hair: '#515969', hairLight: '#a8aab0', suit: '#255776', trim: '#e68e66', skin: '#d3a184', short: true};
  return {hair: '#25364a', hairLight: '#69819b', suit: '#31678b', trim: '#f0ae70', skin: '#d8a58c', short: true};
};

/** A reusable generated full-body sprite, with independently moving legs. */
const FullBodyPerson = ({actor, frame, fps, duration, width, height, index}: {
  actor: CartoonActorPlan; frame: number; fps: number; duration: number; width: number; height: number; index: number;
}) => {
  const moving = actor.action === 'walk' || Math.abs(actor.to_x - actor.from_x) > .08;
  const stride = moving ? Math.sin(frame / fps * 9 + index) : 0;
  const x = interpolate(eased(frame, duration), [0, 1], [actor.from_x, actor.to_x], clamp) * width;
  const turn = actor.to_x < actor.from_x ? -1 : 1;
  const sway = actor.action === 'react' ? Math.sin(frame / fps * 11) * 2
    : actor.action === 'work' || actor.action === 'console' ? Math.sin(frame / fps * 4) * 1.6 : stride * 1.8;
  const bob = moving ? Math.abs(stride) * 5 : Math.sin(frame / fps * 1.5 + index) * 1.5;
  const spriteHeight = height * .78;
  const spriteWidth = spriteHeight;
  const picture: React.CSSProperties = {position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain'};
  return <div style={{position: 'absolute', left: x-spriteWidth*.5, bottom: height*.075+bob,
    width: spriteWidth, height: spriteHeight, transform: `scaleX(${turn}) rotate(${sway}deg)`,
    transformOrigin: '50% 80%', filter: 'drop-shadow(0 10px 7px #07131e88)'}}>
    <Img src={staticFile(actor.body!)} style={{...picture, clipPath: 'polygon(0 0,100% 0,100% 64%,0 64%)'}} />
    <Img src={staticFile(actor.body!)} style={{...picture, clipPath: 'polygon(0 63%,50% 63%,50% 100%,0 100%)',
      transform: `rotate(${stride*11}deg)`, transformOrigin: '44% 64%'}} />
    <Img src={staticFile(actor.body!)} style={{...picture, clipPath: 'polygon(50% 63%,100% 63%,100% 100%,50% 100%)',
      transform: `rotate(${-stride*11}deg)`, transformOrigin: '56% 64%'}} />
  </div>;
};

/** Reusable side-view game sprite. Every part is redrawn from the same palette and bone positions. */
const Person = ({name, action, frame, fps, duration, fromX, toX, speaking, width, height, index}: {
  name: string; action: CartoonAction; frame: number; fps: number; duration: number; fromX: number; toX: number;
  speaking: boolean; width: number; height: number; index: number;
}) => {
  const color = appearance(name);
  const walking = action === 'walk' || Math.abs(toX - fromX) > .08;
  const stride = walking ? Math.sin(frame / fps * 10) : 0;
  const actionWave = Math.sin(frame / fps * (action === 'work' ? 8 : 3));
  const wave = action === 'point' ? -52 : action === 'work' ? -25 + actionWave * 18
    : action === 'console' ? -39 + actionWave * 6 : action === 'carry' ? -34 : stride * 17;
  const leftArm = action === 'react' ? 44 + actionWave * 8 : walking ? -stride * 16 : -6 + Math.sin(frame / fps * 1.7) * 2;
  const x = interpolate(eased(frame, duration), [0, 1], [fromX, toX], clamp) * width;
  const turn = toX < fromX ? -1 : 1;
  const enter = spring({frame, fps, config: {damping: 22, stiffness: 95}});
  const bob = (walking ? Math.abs(stride) * 8 : Math.sin(frame / fps * 2 + index) * 2);
  const react = action === 'react' ? Math.sin(frame / fps * 12) * Math.max(0, 1 - frame / (fps * 1.2)) * 7 : 0;
  const blink = frame % (fps * 4 + 17) < 3;
  const mouth = speaking && Math.sin(frame * .54) > -.15;
  return <div style={{position: 'absolute', left: x - height * .18, bottom: height * .08 + bob,
    width: height * .36, height: height * .63, transform: `scaleX(${turn}) translateY(${(1-enter)*28 + react}px)`,
    transformOrigin: 'center bottom', filter: 'drop-shadow(0 9px 5px #07131e88)'}}>
    <svg width="100%" height="100%" viewBox="0 0 220 430" style={{overflow: 'visible'}}>
      <ellipse cx="110" cy="418" rx="73" ry="11" fill="#071527" opacity=".30" />
      {/* Two hip and knee chains make a genuine walk cycle instead of a bobbing cutout. */}
      <g transform={`rotate(${stride*23} 87 274)`}>
        <path d="M83 269 L77 338 L68 392" stroke="#15283f" strokeWidth="32" fill="none" strokeLinecap="round" />
        <path d="M83 269 L77 338 L68 392" stroke={color.suit} strokeWidth="25" fill="none" strokeLinecap="round" />
        <path d="M55 386 L92 386 Q100 390 96 402 L45 402 Q41 393 55 386Z" fill="#15283b" stroke="#7ea7b9" strokeWidth="3" />
      </g>
      <g transform={`rotate(${-stride*23} 132 274)`}>
        <path d="M132 268 L139 335 L149 390" stroke="#15283f" strokeWidth="32" fill="none" strokeLinecap="round" />
        <path d="M132 268 L139 335 L149 390" stroke={color.suit} strokeWidth="25" fill="none" strokeLinecap="round" />
        <path d="M137 385 L174 385 Q181 390 177 401 L126 401 Q123 392 137 385Z" fill="#15283b" stroke="#7ea7b9" strokeWidth="3" />
      </g>
      <path d="M69 155 Q109 139 153 155 L169 266 Q111 289 54 267 Z" fill={color.suit} stroke="#152b43" strokeWidth="5" />
      <path d="M70 160 L95 174 L88 239 L59 245 M153 159 L127 174 L132 241 L164 246" fill="#6aa9d2" opacity=".5" />
      <path d="M66 256 Q109 272 156 257 L153 282 Q109 291 67 279Z" fill="#1b3552" />
      <path d="M77 186 L147 186" stroke={color.trim} strokeWidth="4" />
      <rect x="91" y="191" width="39" height="23" rx="4" fill="#18344a" stroke="#77c5db" strokeWidth="2" />
      <path d="M98 201 H122 M98 206 H115" stroke="#92d2e5" strokeWidth="2" />
      <g transform={`rotate(${leftArm} 63 168)`}>
        <path d="M65 168 L39 227 L53 274" stroke="#153047" strokeWidth="28" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M65 168 L39 227 L53 274" stroke={color.suit} strokeWidth="21" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="53" cy="279" r="12" fill={color.skin} stroke="#8a5d59" strokeWidth="2" />
      </g>
      <g transform={`rotate(${wave} 155 166)`}>
        <path d="M155 168 L181 224 L167 269" stroke="#153047" strokeWidth="28" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M155 168 L181 224 L167 269" stroke={color.suit} strokeWidth="21" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="167" cy="274" r="12" fill={color.skin} stroke="#8a5d59" strokeWidth="2" />
        {action === 'work' && <path d="M167 279 L193 318 M185 314 L211 300" stroke="#bfcdd4" strokeWidth="7" strokeLinecap="round" />}
      </g>
      <path d="M94 137 L94 158 Q110 169 126 158 L126 137" fill={color.skin} stroke="#8f605d" strokeWidth="3" />
      <g transform={`rotate(${action === 'react' ? -7 + react : Math.sin(frame/fps*1.5)*1.5} 110 105)`}>
        <ellipse cx="110" cy="83" rx="55" ry="69" fill={color.hair} stroke="#0e1e35" strokeWidth="4" />
        <path d="M63 61 Q60 19 111 16 Q158 21 161 74 L148 131 Q140 148 107 149 Q71 138 62 111Z"
          fill={color.skin} stroke="#7e5557" strokeWidth="3" />
        <path d="M55 70 Q60 10 119 13 Q161 13 167 66 Q149 60 145 38 Q117 62 80 56 L62 93Z" fill={color.hair} />
        <path d="M63 51 Q82 25 112 23" fill="none" stroke={color.hairLight} strokeWidth="7" strokeLinecap="round" />
        <path d="M80 86 Q91 79 101 84 M120 84 Q133 78 143 87" stroke="#243344" strokeWidth="3" fill="none" />
        {blink ? <path d="M83 94 L100 94 M121 94 L138 94" stroke="#263041" strokeWidth="3" /> : <>
          <ellipse cx="92" cy="95" rx="7" ry="9" fill="#f6faf6" /><circle cx="94" cy="96" r="4" fill="#1b334b" />
          <ellipse cx="130" cy="95" rx="7" ry="9" fill="#f6faf6" /><circle cx="132" cy="96" r="4" fill="#1b334b" />
        </>}
        <path d="M112 103 L107 117 L115 120" stroke="#ac7770" strokeWidth="2" fill="none" />
        {mouth ? <ellipse cx="112" cy="130" rx="9" ry="6" fill="#572e3b" />
          : <path d="M103 130 Q112 135 122 129" stroke="#793d49" strokeWidth="2.5" fill="none" />}
      </g>
    </svg>
  </div>;
};

const Prop = ({kind, frame, fps, width, height}: {kind: CartoonPlan['prop']; frame: number; fps: number; width: number; height: number}) => {
  if (!kind || kind === 'none') return null;
  const pulse = .4 + .4 * Math.sin(frame / fps * 6);
  return <svg width={width} height={height} style={{position: 'absolute', inset: 0, pointerEvents: 'none'}}>
    {kind === 'console' ? <>
      <path d={`M ${width*.09} ${height*.77} L ${width*.37} ${height*.77} L ${width*.34} ${height*.84} L ${width*.11} ${height*.84}Z`}
        fill="#26455e" stroke="#8db6c5" strokeWidth="4" />
      <rect x={width*.12} y={height*.57} width={width*.20} height={height*.20} rx="8" fill="#112e46" stroke="#72aec2" strokeWidth="5" />
      <path d={`M ${width*.14} ${height*.67} H ${width*.29}`} stroke="#78d8e5" strokeWidth="3" strokeDasharray="9 7" strokeDashoffset={-frame%16} />
    </> : <>
      <path d={`M ${width*.13} ${height*.77} H ${width*.46} L ${width*.43} ${height*.82} H ${width*.16}Z`}
        fill="#2b5367" stroke="#a5bfca" strokeWidth="4" />
      <path d={`M ${width*.19} ${height*.81} L ${width*.18} ${height*.9} M ${width*.4} ${height*.81} L ${width*.41} ${height*.9}`}
        stroke="#536d76" strokeWidth="10" />
      {kind === 'battery' ? <rect x={width*.25} y={height*.68} width={width*.09} height={height*.08} rx="5" fill="#d7e7df" stroke="#2d4d60" strokeWidth="4" />
        : kind === 'crate' ? <rect x={width*.26} y={height*.67} width={width*.12} height={height*.09} fill="#bd8a54" stroke="#684d39" strokeWidth="4" />
          : <circle cx={width*.3} cy={height*.73} r="15" fill="#1e425c" stroke="#d6e5e0" strokeWidth="5" />}
      <circle cx={width*.3} cy={height*.73} r="5" fill="#ffc365" opacity={pulse} />
    </>}
  </svg>;
};

export const CartoonShot = ({shot, width, height, speech}: {shot: Shot; width: number; height: number; speech: Cue[]}) => {
  const frame = useCurrentFrame(), {fps} = useVideoConfig();
  const stage = shot.stage;
  const p = eased(frame, shot.duration);
  const phase = Number(shot.cartoon_phase || 0);
  const motionFrame = frame + (phase ? shot.duration : 0);
  const motionDuration = shot.duration * (shot.cartoon_phase === undefined ? 1 : 2);
  const focus = shot.cartoon.focus || (shot.cartoon.actors.length ? 'actor' : shot.rover ? 'robot' : 'wide');
  const focusX = focus === 'actor' ? (shot.cartoon.actors[0]?.from_x || .20) : focus === 'robot' ? .68 : focus === 'prop' ? .29 : .5;
  const focusY = focus === 'actor' ? .61 : focus === 'robot' ? .69 : focus === 'prop' ? .74 : .5;
  const cam = interpolate(p, [0, 1], phase ? [1.48, 1.60] : [1.01, 1.08], clamp);
  const crop = (cam - 1) / 2 - .006;
  const limit = (value: number) => Math.max(-crop, Math.min(crop, value));
  const targetX = focus === 'actor' ? .38 : focus === 'prop' ? .42 : .52;
  const targetY = focus === 'actor' ? .56 : focus === 'prop' ? .62 : .60;
  const shiftX = phase ? limit(targetX - (.5 + cam * (focusX - .5))) * width
    : (shot.index%2 ? -1 : 1)*p*width*.018;
  const shiftY = phase ? limit(targetY - (.5 + cam * (focusY - .5))) * height : 0;
  const bg = stage?.background || shot.src;
  const active = (speaker: string) => speech.some(cue => cue.scene_number === shot.scene_number && cue.speaker === speaker
    && cue.value !== 'X' && cue.value !== 'A' && shot.from + frame >= cue.from && shot.from + frame < cue.to);
  const closeActor = phase && focus === 'actor' && shot.cartoon.actors[0]?.portrait ? shot.cartoon.actors[0] : null;
  const portraitStyle: React.CSSProperties = {position: 'absolute', left: -width*.22, bottom: -height*.06,
    width: width*.88, height: height*1.14, objectFit: 'contain', objectPosition: 'center bottom',
    transform: `translateY(${Math.sin(frame / fps * 1.6) * 2}px)`, filter: 'drop-shadow(10px 12px 12px #051321aa)'};
  return <AbsoluteFill style={{overflow: 'hidden', background: '#0c1c2c', fontFamily: '"Noto Sans CJK SC", sans-serif'}}>
    <AbsoluteFill style={{transform: `translate(${shiftX}px, ${shiftY}px) scale(${cam})`,
      transformOrigin: phase ? '50% 50%' : '50% 70%'}}>
      {bg && <Img src={staticFile(bg)} style={{width: '100%', height: '100%', objectFit: 'cover',
        filter: 'saturate(.67) contrast(.94) brightness(.86) blur(1.4px)'}} />}
      <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(0deg,#0c1a2a66,transparent 42%), #2049701c'}} />
      <Prop kind={shot.cartoon.prop} frame={frame} fps={fps} width={width} height={height} />
      {shot.cartoon.actors.filter(actor => actor !== closeActor && !/阿拓|AT-1/i.test(actor.name)).map((actor, i) => actor.body
        ? <FullBodyPerson key={`${actor.name}-${i}`} actor={actor} frame={motionFrame} fps={fps}
          duration={motionDuration} width={width} height={height} index={i} />
        : <Person key={`${actor.name}-${i}`} name={actor.name} action={actor.action}
          frame={motionFrame} fps={fps} duration={motionDuration} fromX={actor.from_x} toX={actor.to_x}
          speaking={active(actor.name)} width={width} height={height} index={i} />)}
      {shot.rover && <Rover frame={motionFrame} fps={fps} duration={motionDuration} action={shot.rover.action}
        speaking={active(shot.rover.speaker)} width={width*.50} height={height*.60} left="45%" />}
    </AbsoluteFill>
    {closeActor && <>
      <Img src={staticFile(closeActor.portrait!)} style={portraitStyle} />
      {closeActor.mouth && active(closeActor.name) && Math.sin(frame * .6) > -.12
        && <Img src={staticFile(closeActor.mouth)} style={portraitStyle} />}
    </>}
    {shot.label && <div style={{position: 'absolute', left: width*.055, top: height*.05, padding: '8px 15px',
      background: '#0d2035a8', borderLeft: '4px solid #f3b96c', color: '#f7f9f4', fontSize: 23,
      fontWeight: 800, textShadow: '0 2px 6px #000', maxWidth: width*.44}}>{shot.label}</div>}
  </AbsoluteFill>;
};
