import React from 'react';
import {interpolate} from 'remotion';

export type RoverAction = 'idle' | 'drive' | 'sample' | 'signal' | 'stand' | 'fall' | 'recover' | 'reach';

const bound = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/** AT-1's first, mechanically plausible rover body. All motion is frame deterministic. */
export const Rover = ({frame, fps, duration, action, speaking, width, height, left = '43%'}: {
  frame: number; fps: number; duration: number; action: RoverAction;
  speaking: boolean; width: number; height: number; left?: string;
}) => {
  const t = frame / fps;
  const drive = action === 'drive';
  const sample = action === 'sample' || action === 'reach';
  const signal = action === 'signal';
  const posture = action === 'stand' ? interpolate(frame, [0, fps * 2.5], [0, 1], bound)
    : action === 'fall' ? interpolate(frame, [0, fps * .7, fps * 1.8], [1, 1, .12], bound)
      : action === 'recover' ? interpolate(frame, [0, fps * .8, fps * 3], [.12, .12, 1], bound) : .72;
  const hullLift = posture * 90;
  const balance = Math.sin(t * 2.1) * (action === 'stand' || action === 'recover' ? 1.5 : .5);
  const hullTilt = (action === 'fall' ? interpolate(frame, [0, fps * .7, fps * 1.8], [0, 0, 18], bound)
    : action === 'recover' ? interpolate(frame, [0, fps * .8, fps * 3], [18, 18, 0], bound) : 0) + balance;
  const wheelAngle = drive ? frame * 3.8 : 0;
  const travel = drive ? interpolate(frame, [0, Math.max(1, duration - 1)], [-95, 95], bound) : 0;
  const bodyBob = Math.sin(t * (drive ? 14 : 2.2)) * (drive ? 3.8 : 1.1);
  const mastPan = Math.sin(t * 1.2) * (signal ? 8 : 3);
  const armLift = sample ? interpolate(frame, [0, fps * 1.5, fps * 3], [-18, 26, 36], bound) + Math.max(0, t - 3) / (1 + Math.max(0, t - 3)) * Math.sin(t * 1.4) * 5
    : -18 + Math.sin(t * 1.1) * 1.2;
  const elbow = sample ? interpolate(frame, [0, fps * 1.6, fps * 3.3], [-8, 39, 57], bound) + Math.max(0, t - 3.3) / (1 + Math.max(0, t - 3.3)) * Math.sin(t * 1.1) * 4 : -8;
  const pulse = speaking || signal ? .45 + .55 * Math.max(0, Math.sin(t * 11)) : .16;
  const wheels = [153, 302, 489, 634];
  return <svg width={width} height={height} viewBox="0 0 760 480" xmlns="http://www.w3.org/2000/svg"
    style={{position: 'absolute', left, bottom: '3%', overflow: 'visible',
      transform: `translateX(${travel}px)`, filter: 'drop-shadow(0 13px 12px #050b18a8)'}}>
    <defs>
      <linearGradient id="at1-shell" x1="0" y1="0" x2=".7" y2="1"><stop stopColor="#fffdfa"/><stop offset=".42" stopColor="#cfdeea"/><stop offset="1" stopColor="#7795a9"/></linearGradient>
      <linearGradient id="at1-blue" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#80d2fa"/><stop offset=".4" stopColor="#1777bd"/><stop offset="1" stopColor="#123553"/></linearGradient>
      <linearGradient id="at1-dark" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#2d4459"/><stop offset="1" stopColor="#0a1523"/></linearGradient>
      <linearGradient id="at1-solar" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#183d60"/><stop offset="1" stopColor="#061726"/></linearGradient>
      <radialGradient id="at1-eye"><stop stopColor="#fff0ad"/><stop offset=".36" stopColor="#ffa634"/><stop offset="1" stopColor="#be4c0d"/></radialGradient>
    </defs>
    <ellipse cx="390" cy="448" rx="354" ry="22" fill="#06121a" opacity=".52" />
    {drive && Array.from({length: 10}, (_, i) => <circle key={i} cx={125 - ((frame * (2 + i % 3) + i * 41) % 180)}
      cy={398 + (i * 13) % 38} r={1.6 + i % 4} fill="#b5c1c3" opacity={.36 + .35 * Math.sin((frame + i) / 12)} />)}
    {/* Four articulated wheel-legs keep their feet on the ground as the hull rises. */}
    {wheels.map((cx, i) => {
      const hip = [190, 290, 493, 587][i];
      const kneeX = cx + (i < 2 ? 24 : -24);
      const kneeY = 348 - hullLift * .42 + bodyBob;
      return <g key={cx}>
        <path d={`M ${hip} ${310-hullLift+bodyBob} L ${kneeX} ${kneeY} L ${cx} 397`}
          fill="none" stroke="#142d40" strokeWidth="31" strokeLinejoin="round" strokeLinecap="round" />
        <path d={`M ${hip} ${310-hullLift+bodyBob} L ${kneeX} ${kneeY} L ${cx} 397`}
          fill="none" stroke={i % 2 ? '#7fb4ce' : '#d3e4e6'} strokeWidth="19" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={kneeX} cy={kneeY} r="16" fill="#173d58" stroke="#f4a342" strokeWidth="4" />
        <g transform={`rotate(${wheelAngle} ${cx} 414)`}>
          <circle cx={cx} cy="414" r="31" fill="#091420" stroke="#80a4b7" strokeWidth="5" />
          {Array.from({length: 8}, (_, n) => <path key={n} d={`M ${cx-5} 384 L ${cx+5} 384`}
            transform={`rotate(${n*45} ${cx} 414)`} stroke="#b1d4dd" strokeWidth="4" />)}
          <circle cx={cx} cy="414" r="16" fill="#163e5a" stroke="#e5edf0" strokeWidth="4" />
          <circle cx={cx} cy="414" r="5" fill="#ffad45" />
        </g>
      </g>;
    })}
    <g transform={`translate(0 ${bodyBob-hullLift}) rotate(${hullTilt} 383 270)`}>
      {/* Low, insulated hull with a visible equipment bay and radiator seams. */}
      <path d="M116 236 L205 197 L537 197 L639 236 L656 304 L601 345 L157 345 L105 303 Z" fill="url(#at1-dark)" stroke="#06101b" strokeWidth="7" />
      <path d="M133 229 L211 193 L530 194 L625 233 L616 301 L569 324 L183 324 L131 293 Z" fill="url(#at1-shell)" stroke="#1e3546" strokeWidth="6" />
      <path d="M139 244 L228 216 L526 216 L613 245 L600 282 L157 282 Z" fill="#eaf3ef" opacity=".53" />
      <path d="M161 307 L590 307 L567 336 L188 336 Z" fill="#1b425f" stroke="#71b9d7" strokeWidth="3" />
      <path d="M159 232 L206 200 L246 207 L222 279 L162 286 Z" fill="url(#at1-blue)" stroke="#193449" strokeWidth="4" />
      <path d="M545 211 L616 239 L605 288 L559 283 Z" fill="url(#at1-blue)" stroke="#193449" strokeWidth="4" />
      <path d="M251 205 L520 205 L495 296 L244 296 Z" fill="#e8f0ed" stroke="#577084" strokeWidth="3" />
      {[275, 323, 371, 419, 467].map(x => <path key={x} d={`M ${x} 209 L ${x-7} 285`} stroke="#94adba" strokeWidth="2" />)}
      <path d="M282 295 L483 295" stroke="#f59a35" strokeWidth="6" />
      <rect x="350" y="237" width="70" height="31" rx="6" fill="#1a2937" stroke="#769db0" strokeWidth="3" />
      {[366, 383, 400].map(x => <rect key={x} x={x} y="247" width="8" height="10" rx="2" fill="#5ac8e8" opacity={.55 + pulse * .35} />)}
      <path d="M137 284 L94 301 L107 316 L153 311 M620 285 L674 306 L663 319 L604 308" fill="#f7a03c" stroke="#513e36" strokeWidth="3" />
      {/* Hinged solar panel. A fixed large array would be unsafe while driving. */}
      <g transform={`rotate(${drive ? -4 : Math.sin(t * .6) * .6} 377 198)`}>
        <path d="M306 191 L325 158 L501 158 L518 191 Z" fill="#1a3348" stroke="#e2e8e5" strokeWidth="5" />
        <path d="M321 165 L496 165 L505 185 L310 185 Z" fill="url(#at1-solar)" stroke="#5ba2bf" strokeWidth="2" />
        {[342, 376, 410, 444, 478].map(x => <path key={x} d={`M ${x} 163 L ${x-6} 186`} stroke="#61aec9" strokeWidth="2" />)}
        <path d="M313 176 L502 176" stroke="#5ba2bf" strokeWidth="2" />
      </g>
      {/* Sensor mast swivels independently; no human face is needed. */}
      <path d="M356 199 L358 124" stroke="#263a49" strokeWidth="14" />
      <path d="M356 199 L358 124" stroke="#d1e3e5" strokeWidth="7" />
      <g transform={`rotate(${mastPan} 358 128)`}>
        <path d="M319 82 L390 82 L405 103 L395 139 L322 139 L308 104 Z" fill="url(#at1-shell)" stroke="#153047" strokeWidth="5" />
        <path d="M324 96 L391 96 L388 126 L325 126 Z" fill="#152735" stroke="#58b7d6" strokeWidth="3" />
        <circle cx="356" cy="111" r="18" fill="#061827" stroke="#a3d8e7" strokeWidth="3" />
        <circle cx="356" cy="111" r="10" fill="url(#at1-eye)" />
        <circle cx="353" cy="107" r="3" fill="#fff6cf" />
        <circle cx="385" cy="111" r="3.8" fill="#ffab36" opacity={pulse} />
        <path d="M393 90 L405 64" stroke="#d9e8e7" strokeWidth="3" />
        <circle cx="406" cy="63" r={signal ? 5 + Math.sin(t * 12) * 1.8 : 4} fill="#f89a35" />
        {(signal || speaking) && [1, 2].map(i => <circle key={i} cx="406" cy="63" r={12 + i * 12 + (frame % 18)}
          fill="none" stroke="#ffc56b" strokeWidth="2" opacity={.34 * (1 - (frame % 18) / 18)} />)}
      </g>
      {/* A two-link gripper arm demonstrates collection without inventing a magic tool. */}
      <g transform={`rotate(${armLift} 584 276)`}>
        <circle cx="584" cy="276" r="24" fill="#1b3548" stroke="#b9d3db" strokeWidth="5" />
        <path d="M585 266 L664 206 L681 224 L603 289 Z" fill="url(#at1-shell)" stroke="#223a4d" strokeWidth="5" />
        <path d="M602 268 L654 225" stroke="#f39832" strokeWidth="5" />
        <g transform={`rotate(${elbow} 672 215)`}>
          <circle cx="672" cy="215" r="19" fill="#183b56" stroke="#c8dce0" strokeWidth="5" />
          <path d="M674 205 L733 260 L720 277 L661 228 Z" fill="url(#at1-shell)" stroke="#1b394e" strokeWidth="5" />
          <path d="M719 268 L745 285 L740 296 L711 282 Z" fill="#1b3c53" stroke="#9dc8d1" strokeWidth="3" />
          <path d="M738 293 L751 310 M738 293 L719 307" fill="none" stroke="#b7d6d9" strokeWidth="7" strokeLinecap="round" />
        </g>
      </g>
      <path d="M215 228 L248 225 M509 224 L540 230" stroke="#fff" strokeWidth="3" opacity=".85" />
    </g>
  </svg>;
};
