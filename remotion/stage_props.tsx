import React from 'react';
import {Img, staticFile} from 'remotion';
import {insetScreenGlass} from './puppet_motion';

export type StagePropKind = 'console_desk' | 'telemetry_screen' | 'workbench';
export type StageRgb = [number, number, number];
/** Normalized top-left rectangle on the 1280×720 plate. */
export type StageRect = {x: number; y: number; w: number; h: number};

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
  /** Painted monitor on the location plate. Curves and the lock label stay inside it. */
  screenRect?: StageRect;
  /** Rect is already the inset glass in this layer's coordinate space. Do not inset again. */
  glassReady?: boolean;
};

const TELEMETRY_CURVES = [
  'M14 46 C34 26 50 60 70 38 S102 28 122 44 S142 56 146 34',
  'M14 56 C34 40 52 68 72 50 S104 42 124 56 S142 66 146 46',
  'M14 66 C36 52 54 76 74 60 S106 54 126 66 S144 76 146 58',
  'M14 76 C38 64 56 84 78 72 S108 66 128 76 S144 84 146 70',
];

/** Curves and “4W LOCK” inside a plate monitor. No second bezel. */
export const TelemetryScreenProp = (props: TelemetryScreenPropProps) => {
  const raw = props.screenRect;
  const rect =
    raw && raw.w > 0.015 && raw.h > 0.015
      ? props.glassReady
        ? raw
        : insetScreenGlass(raw)
      : undefined;
  const fitted = Boolean(rect);
  const width = fitted && rect
    ? rect.w * props.sceneWidth
    : clamp(props.sceneWidth * 0.12, 88, props.sceneWidth * 0.14);
  const height = fitted && rect
    ? rect.h * props.sceneHeight
    : clamp(props.sceneHeight * 0.075, 40, props.sceneHeight * 0.1);
  const left = fitted && rect ? rect.x * props.sceneWidth : props.x * props.sceneWidth - width / 2;
  const top = fitted && rect
    ? rect.y * props.sceneHeight
    : clamp(props.screenY ?? 0.36, 0.28, 0.52) * props.sceneHeight;
  const pulse = 0.74 + 0.26 * Math.sin((props.frame / Math.max(1, props.fps)) * Math.PI * 3.6);
  const glow = props.active ? 0.85 + pulse * 0.15 : 0.28;
  const dash = -((props.frame * 2.2) % 36);
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width,
        height,
        overflow: 'hidden',
        pointerEvents: 'none',
      }}
    >
      <svg
        width={width}
        height={height}
        viewBox="0 0 160 96"
        preserveAspectRatio="xMidYMid meet"
        overflow="hidden"
        style={{display: 'block', overflow: 'hidden'}}
      >
        <defs>
          <clipPath id="telemetry-glass">
            <rect x="0" y="0" width="160" height="96" />
          </clipPath>
        </defs>
        <g clipPath="url(#telemetry-glass)">
        {TELEMETRY_CURVES.map((d, i) => (
          <path
            key={i}
            d={d}
            fill="none"
            stroke={i === 3 ? AMBER : GREEN}
            strokeWidth={props.active ? 5 : 3}
            strokeDasharray={props.active ? '13 5' : '4 10'}
            strokeDashoffset={props.active ? dash - i * 7 : 0}
            strokeLinecap="round"
            opacity={glow * (1 - i * 0.08)}
          />
        ))}
        <text
          x="80"
          y="30"
          textAnchor="middle"
          fill={props.active ? '#d9fff0' : '#c5ddd4'}
          fontSize="22"
          fontWeight="900"
          opacity={props.active ? 0.95 : 0.55}
        >
          4W LOCK
        </text>
        </g>
      </svg>
    </div>
  );
};

export type CabinetHoloStripProps = StagePropProps & {
  cabinetTopY: number;
  spanX?: number;
  spanW?: number;
};

/** A thin trace sitting on a cabinet that is already painted on the plate. */
export const CabinetHoloStrip = (props: CabinetHoloStripProps) => {
  const spanLeft = (props.spanX ?? 0.04) * props.sceneWidth;
  const spanWidth = (props.spanW ?? 0.4) * props.sceneWidth;
  const width = clamp(Math.min(spanWidth * 0.62, props.sceneWidth * 0.26), 88, props.sceneWidth * 0.3);
  const height = clamp(props.sceneHeight * 0.036, 16, 28);
  const unclamped = props.x * props.sceneWidth - width / 2;
  const left = clamp(unclamped, spanLeft, Math.max(spanLeft, spanLeft + spanWidth - width));
  const top = props.cabinetTopY * props.sceneHeight - height;
  const pulse = 0.7 + 0.3 * Math.sin((props.frame / Math.max(1, props.fps)) * Math.PI * 3.2);
  const live = props.active ? 0.55 + pulse * 0.4 : 0.22;
  const shift = Math.sin(props.frame / 6) * (props.active ? 10 : 2);
  return (
    <div style={{position: 'absolute', left, top, width, height, pointerEvents: 'none'}}>
      <svg width="100%" height="100%" viewBox="0 0 320 36" preserveAspectRatio="none">
        <path d="M6 27 H314" stroke={GREEN} strokeWidth="3" strokeLinecap="round" opacity={live} />
        <path
          d={`M18 ${22 + shift * 0.05} C48 ${14 - shift * 0.08} 78 26 112 16 S176 12 214 20 S268 24 304 15`}
          fill="none"
          stroke={AMBER}
          strokeWidth="2.5"
          strokeLinecap="round"
          opacity={live}
        />
        <path d="M18 30 H304" stroke={tint(props.ambientTint, 0.45)} strokeWidth="4" opacity={props.active ? 0.55 : 0.2} />
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
