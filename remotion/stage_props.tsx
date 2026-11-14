import React from 'react';
import {Img, staticFile} from 'remotion';

export type StagePropKind = 'console_desk' | 'telemetry_screen' | 'workbench';
export type StageRgb = [number, number, number];

export type StagePropProps = {
  frame: number;
  fps: number;
  x: number;
  floorY: number;
  humanHeightPx: number;
  sceneWidth: number;
  sceneHeight: number;
  active?: boolean;
  propSrc?: string;
  ambientTint?: StageRgb;
};

const INK = '#1d293d';
const PANEL = '#547084';
const PANEL_DARK = '#304a5e';
const SHADOW = '#26394c';
const TRIM = '#d8e3e5';
const GREEN = '#58f0ac';
const AMBER = '#ffc15d';

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const tint = (rgb: StageRgb | undefined, alpha: number) =>
  rgb ? `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})` : 'transparent';

const consoleBox = ({
  x,
  floorY,
  humanHeightPx,
  sceneWidth,
  sceneHeight,
}: Pick<StagePropProps, 'x' | 'floorY' | 'humanHeightPx' | 'sceneWidth' | 'sceneHeight'>) => {
  // The SVG deck (including its raised holo screens) is ~0.95 m tall at 0.66x the human height; the
  // work surface itself sits at ~0.8 m, i.e. just above the operator's hips like the AT-1 rover top.
  const height = clamp(humanHeightPx * 0.66, sceneHeight * 0.27, sceneHeight * 0.46);
  const width = clamp(humanHeightPx * 0.96, sceneWidth * 0.24, sceneWidth * 0.38);
  return {
    left: x * sceneWidth - width / 2,
    top: floorY * sceneHeight - height,
    width,
    height,
  };
};

const PropCutout = ({
  src,
  ambientTint,
}: {
  src?: string;
  ambientTint?: StageRgb;
}) => {
  if (!src) return null;
  const resolved = staticFile(src);
  return (
    <>
      <Img
        src={resolved}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          filter: 'drop-shadow(0 8px 8px rgba(9, 19, 31, .34))',
        }}
      />
      {ambientTint && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: tint(ambientTint, 0.1),
            mixBlendMode: 'soft-light',
            maskImage: `url("${resolved}")`,
            WebkitMaskImage: `url("${resolved}")`,
            maskPosition: 'center',
            WebkitMaskPosition: 'center',
            maskRepeat: 'no-repeat',
            WebkitMaskRepeat: 'no-repeat',
            maskSize: 'contain',
            WebkitMaskSize: 'contain',
          }}
        />
      )}
    </>
  );
};

/**
 * Solid desk and control deck. Render this between the puppet's
 * `body_behind_desk` and `arms_over_desk` passes for genuine waist occlusion.
 */
export const ConsoleDeskBody = (props: StagePropProps) => {
  const box = consoleBox(props);
  return (
    <div
      style={{
        position: 'absolute',
        ...box,
        pointerEvents: 'none',
        filter: 'drop-shadow(0 8px 8px rgba(9, 19, 31, .28))',
      }}
    >
      {props.propSrc ? (
        <PropCutout src={props.propSrc} ambientTint={props.ambientTint} />
      ) : (
        <svg width="100%" height="100%" viewBox="0 0 480 290" preserveAspectRatio="none">
          <ellipse cx="240" cy="279" rx="186" ry="10" fill="rgba(11, 22, 35, .28)" />
          <path
            d="M54 74 L420 74 L458 118 L28 118 Z"
            fill="#7894a5"
            stroke={INK}
            strokeWidth="7"
            strokeLinejoin="round"
          />
          <path d="M28 118 L458 118 L426 273 L59 273 Z" fill={PANEL} stroke={INK} strokeWidth="7" />
          <path d="M28 118 L101 132 L91 267 L59 273 Z" fill={SHADOW} />
          <path d="M381 125 L458 118 L426 273 L395 267 Z" fill="#3e596d" />
          <path d="M118 135 L362 135 L347 244 L133 244 Z" fill={PANEL_DARK} stroke={INK} strokeWidth="5" />
          <path d="M139 150 L239 150 L229 226 L149 226 Z" fill="#21384c" stroke={INK} strokeWidth="4" />
          <path d="M250 150 L342 150 L333 184 L246 184 Z" fill="#21384c" stroke={INK} strokeWidth="4" />
          <path d="M246 194 L333 194 L327 226 L243 226 Z" fill="#e7a34f" opacity=".78" />
          <path d="M73 103 L413 103" stroke={TRIM} strokeWidth="5" strokeLinecap="round" opacity=".82" />
          <path d="M103 273 L93 286 L61 286 L72 273 M378 273 L389 286 L421 286 L409 273" fill={INK} />
          <circle cx="390" cy="99" r="8" fill={props.active ? GREEN : '#668074'} stroke={INK} strokeWidth="4" />
          <rect x="176" y="88" width="48" height="10" rx="5" fill="#dbe7e8" stroke={INK} strokeWidth="3" />
          <rect x="230" y="88" width="74" height="10" rx="5" fill="#efb458" stroke={INK} strokeWidth="3" />
          <path d="M102 74 L123 53 H349 L370 74" fill="#3e5a6f" stroke={INK} strokeWidth="6" />
          <path d="M74 126 L405 126" stroke={tint(props.ambientTint, 0.26)} strokeWidth="11" />
        </svg>
      )}
    </div>
  );
};

/** Live angled monitor glass and animated telemetry, rendered above the operator's hands. */
export const ConsoleHoloOverlay = (props: StagePropProps) => {
  const box = consoleBox(props);
  const pulse = 0.72 + 0.28 * Math.sin((props.frame / Math.max(1, props.fps)) * Math.PI * 3.2);
  const scan = ((props.frame * 2.4) % 92) - 16;
  const liveOpacity = props.active ? 0.82 + pulse * 0.18 : 0.2;
  return (
    <div style={{position: 'absolute', ...box, pointerEvents: 'none'}}>
      <svg width="100%" height="100%" viewBox="0 0 480 290" preserveAspectRatio="none">
        <g opacity={props.active ? 1 : 0.58}>
          <path
            d="M108 78 L164 18 L261 18 L230 79 Z"
            fill="rgba(23, 66, 79, .84)"
            stroke={INK}
            strokeWidth="6"
            strokeLinejoin="round"
          />
          <path
            d="M244 79 L274 29 L375 29 L411 79 Z"
            fill="rgba(29, 60, 74, .88)"
            stroke={INK}
            strokeWidth="6"
            strokeLinejoin="round"
          />
          <path d="M126 65 L158 34 L241 34 L222 65 Z" fill={`rgba(88, 240, 172, ${liveOpacity * 0.22})`} />
          {[0, 1, 2, 3].map(i => {
            const y = 40 + i * 7;
            const offset = Math.sin(props.frame / 5 + i * 1.7) * (props.active ? 7 : 1.5);
            return (
              <path
                key={i}
                d={`M${153 + offset} ${y} L${173 + offset} ${y - 4} L${193 + offset} ${y + 3} L${
                  216 + offset
                } ${y - 2}`}
                fill="none"
                stroke={i % 2 ? AMBER : GREEN}
                strokeWidth="3"
                strokeLinecap="round"
                opacity={liveOpacity}
              />
            );
          })}
          {[0, 1, 2, 3, 4].map(i => {
            const value = props.active ? 24 + ((props.frame * (i + 2) + i * 17) % 44) : 13;
            return (
              <rect
                key={i}
                x={290 + i * 17}
                y={67 - value * 0.48}
                width="10"
                height={value * 0.48}
                rx="2"
                fill={i === 4 ? AMBER : GREEN}
                opacity={liveOpacity}
              />
            );
          })}
          <path d={`M137 ${scan} L230 ${scan - 17}`} stroke="#d7fff1" strokeWidth="3" opacity={props.active ? 0.28 : 0} />
          <circle cx="392" cy="68" r={props.active ? 8 + pulse * 2 : 6} fill={props.active ? AMBER : '#607077'} />
        </g>
        {props.active && (
          <path
            d="M111 81 L408 81"
            stroke={tint(props.ambientTint, 0.5)}
            strokeWidth="9"
            opacity=".55"
          />
        )}
      </svg>
    </div>
  );
};

export type TelemetryScreenPropProps = StagePropProps & {
  screenY?: number;
};

/** Wall/pillar display that changes from dim standby to a four-wheel suspension lock diagnostic. */
export const TelemetryScreenProp = (props: TelemetryScreenPropProps) => {
  const width = clamp(props.humanHeightPx * 0.72, props.sceneWidth * 0.2, props.sceneWidth * 0.3);
  const height = clamp(props.sceneHeight * 0.22, 122, props.humanHeightPx * 0.48);
  const top = clamp(props.screenY ?? 0.34, 0.3, 0.5) * props.sceneHeight;
  const pulse = 0.74 + 0.26 * Math.sin((props.frame / Math.max(1, props.fps)) * Math.PI * 3.6);
  const glow = props.active ? 0.8 + pulse * 0.2 : 0.16;
  const dash = -((props.frame * 2.2) % 36);
  const curves = [
    'M58 112 C89 75 116 137 148 91 S206 69 245 104 S303 126 338 80',
    'M58 124 C88 94 119 145 151 107 S207 91 246 118 S301 139 338 101',
    'M58 136 C92 111 121 154 153 121 S209 108 247 132 S302 150 338 119',
    'M58 148 C94 129 124 160 156 136 S211 125 250 145 S304 158 338 137',
  ];
  return (
    <div
      style={{
        position: 'absolute',
        left: props.x * props.sceneWidth - width / 2,
        top,
        width,
        height,
        pointerEvents: 'none',
        filter: props.active
          ? `drop-shadow(0 0 ${10 + pulse * 7}px rgba(88, 240, 172, .38))`
          : 'drop-shadow(0 6px 7px rgba(9, 19, 31, .3))',
      }}
    >
      {props.propSrc && <PropCutout src={props.propSrc} ambientTint={props.ambientTint} />}
      <svg width="100%" height="100%" viewBox="0 0 400 190" preserveAspectRatio="none">
        <path d="M15 18 H385 V172 H15 Z" fill="#435d70" stroke={INK} strokeWidth="8" />
        <path d="M31 34 H369 V155 H31 Z" fill="#132c3b" stroke={INK} strokeWidth="5" />
        <path d="M31 34 H369 V155 H31 Z" fill={tint(props.ambientTint, 0.12)} />
        {[0, 1, 2, 3].map(i => (
          <path
            key={i}
            d={curves[i]}
            fill="none"
            stroke={i === 3 ? AMBER : GREEN}
            strokeWidth={props.active ? 4 : 2.5}
            strokeDasharray={props.active ? '13 5' : '4 10'}
            strokeDashoffset={props.active ? dash - i * 7 : 0}
            strokeLinecap="round"
            opacity={glow * (1 - i * 0.08)}
          />
        ))}
        {[72, 150, 228, 306].map((cx, i) => (
          <g key={cx} opacity={props.active ? 0.95 : 0.24}>
            <circle cx={cx} cy="57" r="13" fill="#203f4f" stroke={GREEN} strokeWidth="3" />
            <path d={`M${cx - 7} 57 H${cx + 7} M${cx} 50 V64`} stroke={i === 3 ? AMBER : GREEN} strokeWidth="3" />
          </g>
        ))}
        <g opacity={props.active ? 0.92 + pulse * 0.08 : 0.3}>
          <path d="M274 39 H359 V68 H274 Z" fill={props.active ? '#315f4d' : '#344853'} stroke={INK} strokeWidth="3" />
          <text x="316.5" y="59" textAnchor="middle" fill={props.active ? '#d9fff0' : '#81929a'} fontSize="15" fontWeight="900">
            4W LOCK
          </text>
        </g>
        <circle cx="379" cy="95" r={props.active ? 5.5 + pulse * 1.5 : 4} fill={props.active ? AMBER : '#566871'} />
      </svg>
    </div>
  );
};

/** Waist-high diagnostic bench with a crisp tool rack and live work indicator. */
export const WorkbenchProp = (props: StagePropProps) => {
  const height = clamp(props.humanHeightPx * 0.5, props.sceneHeight * 0.21, props.sceneHeight * 0.4);
  const width = clamp(props.humanHeightPx * 0.86, props.sceneWidth * 0.22, props.sceneWidth * 0.35);
  const pulse = 0.72 + 0.28 * Math.sin((props.frame / Math.max(1, props.fps)) * Math.PI * 2.8);
  return (
    <div
      style={{
        position: 'absolute',
        left: props.x * props.sceneWidth - width / 2,
        top: props.floorY * props.sceneHeight - height,
        width,
        height,
        pointerEvents: 'none',
        filter: 'drop-shadow(0 8px 8px rgba(9, 19, 31, .3))',
      }}
    >
      {props.propSrc ? (
        <PropCutout src={props.propSrc} ambientTint={props.ambientTint} />
      ) : (
        <svg width="100%" height="100%" viewBox="0 0 470 270" preserveAspectRatio="none">
          <ellipse cx="235" cy="262" rx="180" ry="9" fill="rgba(11, 22, 35, .28)" />
          <path d="M42 79 H429 L451 113 H22 Z" fill="#879eaa" stroke={INK} strokeWidth="7" strokeLinejoin="round" />
          <path d="M39 113 H434 L408 252 H65 Z" fill={PANEL} stroke={INK} strokeWidth="7" />
          <path d="M65 119 L124 127 L113 247 L65 252 Z" fill={SHADOW} />
          <path d="M348 121 L434 113 L408 252 L367 247 Z" fill="#3d586b" />
          <rect x="132" y="137" width="207" height="91" rx="5" fill="#2b4355" stroke={INK} strokeWidth="5" />
          <path d="M147 153 H324 M147 177 H324 M147 201 H324" stroke="#668191" strokeWidth="4" />
          {[164, 207, 250, 293].map((x, i) => (
            <g key={x}>
              <path
                d={i % 2 ? `M${x} 147 V193 L${x - 10} 211 H${x + 10} L${x} 193` : `M${x} 148 V207`}
                fill="none"
                stroke={i === 3 ? AMBER : TRIM}
                strokeWidth="7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx={x} cy="145" r="6" fill={INK} />
            </g>
          ))}
          <path d="M81 96 H258" stroke="#dce7e8" strokeWidth="6" strokeLinecap="round" />
          <rect x="275" y="88" width="92" height="16" rx="4" fill="#183345" stroke={INK} strokeWidth="4" />
          <rect
            x="283"
            y="93"
            width={props.active ? 51 + pulse * 25 : 18}
            height="6"
            rx="3"
            fill={props.active ? GREEN : '#60747d'}
          />
          <circle cx="397" cy="96" r={props.active ? 8 + pulse * 2 : 7} fill={props.active ? AMBER : '#63757b'} stroke={INK} strokeWidth="4" />
          <path d="M72 252 L61 265 H90 L101 252 M370 252 L381 265 H410 L398 252" fill={INK} />
          <path d="M45 119 H429" stroke={tint(props.ambientTint, 0.28)} strokeWidth="10" />
        </svg>
      )}
    </div>
  );
};
