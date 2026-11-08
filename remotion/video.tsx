import React from 'react';
import {AbsoluteFill, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Audio} from '@remotion/media';

type Shot = {src: string; from: number; duration: number; index: number; camera?: string;
  label?: string; fact_card?: string; section?: string};
type Track = {src: string; from: number; volume: number; loop: boolean};
type Caption = {from: number; to: number; text: string};
type Card = {name: string; tagline?: string; url?: string; qr?: string};
export type SoundBookProps = {width: number; height: number; fps: number; durationInFrames: number;
  introFrames: number; shots: Shot[]; audio: Track[]; captions: Caption[];
  episodeNumber: number; title: string; language: string; cta: Card | null;
  ctaFrom: number; ctaFrames: number};

const font = '"Noto Sans CJK SC", "Noto Sans", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const lerp = (frame: number, start: number, end: number, a: number, b: number) =>
  interpolate(frame, [start, Math.max(start + 1, end)], [a, b], clamp);

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
      <div style={{fontSize: Math.min(height * .078, width * .75 / Math.max(8, props.title.length) * 1.5),
        fontWeight: 900, maxWidth: '82%', textAlign: 'center', lineHeight: 1.35,
        textShadow: '0 4px 15px #000b'}}>{props.title}</div>
    </div>
    <div style={{position: 'absolute', left: 0, bottom: 0, width: `${lerp(frame, 0, props.introFrames, 0, 100)}%`,
      height: 8, background: '#f6d27d'}} />
  </AbsoluteFill>;
};

const MotionShot = ({shot, width, height}: {shot: Shot; width: number; height: number}) => {
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
  return <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#101827', color: 'white', fontFamily: font}}>
    <Img src={staticFile(shot.src)} style={{position: 'absolute', width: '110%', height: '110%',
      top: '-5%', left: '-5%', objectFit: 'cover', transform: `scale(${scale}) translate(${panX * .35}px,${panY * .35}px)`,
      filter: 'blur(17px) brightness(.57) saturate(.9)'}} />
    <Img src={staticFile(shot.src)} style={{position: 'absolute', inset: 0, width: '100%', height: '100%',
      objectFit: 'cover', transform: `scale(${scale}) translate(${panX}px,${panY}px)`,
      filter: 'contrast(1.08) saturate(1.1)'}} />
    {/* A broad, feathered foreground plane moves at a different speed from the camera. */}
    <div style={{position: 'absolute', inset: 0, overflow: 'hidden',
      maskImage: 'linear-gradient(120deg, transparent 0%, transparent 61%, black 85%)',
      WebkitMaskImage: 'linear-gradient(120deg, transparent 0%, transparent 61%, black 85%)', opacity: .48}}>
      <Img src={staticFile(shot.src)} style={{width: '100%', height: '100%', objectFit: 'cover',
        transform: `scale(${scale * 1.07}) translate(${panX * 1.8}px,${panY * 1.5}px)`}} />
    </div>
    <div style={{position: 'absolute', inset: 0,
      background: 'linear-gradient(90deg,#0814268a,transparent 35%,transparent 70%,#07121e45),linear-gradient(0deg,#07111dc7,transparent 29%)'}} />
    {/* A scanning beam, technical grid and connected markers animate as part of the world. */}
    <div style={{position: 'absolute', left: `${lerp(p, 0, 1, -20, 110)}%`, top: 0,
      height: '100%', width: width * .12, transform: 'skewX(-16deg)',
      background: `linear-gradient(90deg, transparent, ${accent}18, transparent)`, opacity: .75}} />
    {shot.index % 3 !== 2 && <svg width={width} height={height} style={{position: 'absolute', inset: 0, opacity: .78}}>
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
      <MotionShot shot={shot} width={props.width} height={props.height} />
    </Sequence>)}
    {props.cta && props.ctaFrames > 0 && <Sequence from={props.ctaFrom} durationInFrames={props.ctaFrames}>
      <EndCard card={props.cta} frames={props.ctaFrames} />
    </Sequence>}
    <CaptionLayer captions={props.captions} width={props.width} height={props.height} />
    {props.audio.map((track, i) => <Audio key={i} src={staticFile(track.src)} from={track.from}
      volume={track.volume} loop={track.loop} />)}
  </AbsoluteFill>;
};
