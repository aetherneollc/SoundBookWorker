import React from 'react';
import {Img, random, spring, staticFile} from 'remotion';

export type PuppetAction = 'idle' | 'listen' | 'talk' | 'walk' | 'settle' | 'point' | 'wave' | 'work' | 'carry' | 'console' | 'react';
export type PuppetDepth = 'far' | 'mid' | 'near';
export type PuppetFacing = 'left' | 'right';

export type PuppetClipType = 'idle' | 'listen' | 'talk' | 'walk' | 'settle' | 'point' | 'wave' | 'work' | 'carry' | 'console' | 'react';

export type PuppetClip = {
  type: PuppetClipType;
  startFrame: number;
  endFrame: number;
  fromX: number;
  toX: number;
  fromScale?: number;
  toScale?: number;
  facing?: PuppetFacing;
  /** Camera pose of the cutout: profile while walking, three-quarter when settled / talking. */
  pose?: 'side' | 'three_quarter';
  /** Normalised stage X (0..1) the actor looks / aims at (partner, console, rover). */
  targetX?: number;
};

export type PuppetPart = {
  src: string;
  pivot: [number, number];
};

export type PuppetFace = {
  left_eye: [number, number, number, number];
  right_eye: [number, number, number, number];
  mouth: [number, number, number, number];
  skin_rgb: [number, number, number];
  line_rgb: [number, number, number];
};

export type CharacterRig = {
  level: 'full' | 'upper' | 'sprite';
  width: number;
  height: number;
  anchor_x: number;
  foot_y: number;
  head_top_y: number;
  native_facing: PuppetFacing;
  parts: Record<string, PuppetPart>;
  ankles?: {
    l: [number, number];
    r: [number, number];
  };
  face?: PuppetFace;
  view_angle?: 'side' | 'three_quarter' | 'front';
  /** Wrist joints in normalised rig-canvas coordinates (screen-left `l`, screen-right `r`). */
  wrists?: {
    l: [number, number];
    r: [number, number];
    radius?: number;
  };
};

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));
const clampValue = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const smoothstep = (value: number) => {
  const p = clamp01(value);
  return p * p * (3 - 2 * p);
};
const sinusoidalEase = (value: number) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(value));
const mix = (p: number, a: number, b: number) => a + (b - a) * clamp01(p);
const positiveModulo = (value: number, modulus: number) => ((value % modulus) + modulus) % modulus;

const rgbCss = (rgb: [number, number, number] | undefined, fallback: string) =>
  rgb && rgb.length >= 3 ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : fallback;

/** Depth plane floor Y and figure height ratios so full head-to-shoes is visible in wide framing. */
export const DEPTH_STAGE: Record<PuppetDepth, {floorY: number; figureHeightRatio: number; order: number}> = {
  far: {floorY: 0.80, figureHeightRatio: 0.54, order: 0},
  mid: {floorY: 0.89, figureHeightRatio: 0.68, order: 1},
  near: {floorY: 0.95, figureHeightRatio: 0.78, order: 2},
};

/** Deterministic seeded blink every 3.2-4.8s with occasional anime double-blink. */
const computeBlinkClosure = (name: string, frame: number, fps: number): number => {
  const period = Math.max(48, Math.round(fps * 3.8));
  const bucket = Math.floor(frame / period);
  const offsetRatio = 0.18 + random(`puppet-blink-${name}-${bucket}`) * 0.64;
  const start = bucket * period + Math.round(offsetRatio * period);
  const dt = frame - start;
  const profile = (d: number) => {
    if (d < 0 || d >= 5) return 0;
    if (d === 0) return 0.55;
    if (d === 1 || d === 2) return 1.0;
    if (d === 3) return 0.65;
    return 0.22;
  };
  const first = profile(dt);
  if (first > 0) return first;
  const isDouble = random(`puppet-dblblink-${name}-${bucket}`) > 0.84;
  return isDouble ? profile(dt - 7) : 0;
};

/** Procedural 2D TV anime viseme mouth patch (Rhubarb shapes A-H, X) over face.mouth. */
const VisemeMouth = ({
  face,
  cue,
  speaking,
}: {
  face: PuppetFace;
  cue: string;
  speaking: boolean;
}) => {
  const [mx, my, mw, mh] = face.mouth;
  const skin = rgbCss(face.skin_rgb, 'rgb(242, 212, 194)');
  const line = rgbCss(face.line_rgb, 'rgb(36, 28, 34)');
  const shape = speaking ? (cue || 'B').toUpperCase() : (cue === 'A' ? 'A' : 'X');

  // Subtle feathered skin mask hides only the static closed mouth line without a noticeable chin patch
  const padX = mw * 0.08;
  const padY = mh * 0.24;
  const leftPct = (mx - padX) * 100;
  const topPct = (my - padY * 0.5) * 100;
  const widthPct = (mw + padX * 2) * 100;
  const heightPct = (mh + padY * 1.1) * 100;

  return (
    <div
      style={{
        position: 'absolute',
        left: `${leftPct}%`,
        top: `${topPct}%`,
        width: `${widthPct}%`,
        height: `${heightPct}%`,
        pointerEvents: 'none',
      }}
    >
      {/* Feathered skin patch masks only the static closed mouth stroke in head.png */}
      <div
        style={{
          position: 'absolute',
          inset: '14% 12%',
          borderRadius: '50%',
          background: skin,
          filter: 'blur(0.8px)',
        }}
      />
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 100 70"
        preserveAspectRatio="none"
        style={{position: 'absolute', inset: 0, overflow: 'visible'}}
      >
        {shape === 'X' && (
          <path
            d="M 24 34 Q 50 40 76 33"
            fill="none"
            stroke={line}
            strokeWidth="5.5"
            strokeLinecap="round"
          />
        )}
        {shape === 'A' && (
          <path
            d="M 20 35 Q 50 37 80 35"
            fill="none"
            stroke={line}
            strokeWidth="6.2"
            strokeLinecap="round"
          />
        )}
        {shape === 'B' && (
          <>
            <rect x="23" y="25" width="54" height="20" rx="9" fill="#6b2937" stroke={line} strokeWidth="5" />
            <path d="M 28 29 H 72" stroke="#faf7f5" strokeWidth="4.5" strokeLinecap="round" />
          </>
        )}
        {shape === 'C' && (
          <>
            <path
              d="M 21 24 Q 50 20 79 24 Q 82 52 50 54 Q 18 52 21 24 Z"
              fill="#632331"
              stroke={line}
              strokeWidth="5"
              strokeLinejoin="round"
            />
            <path d="M 29 26 H 71" stroke="#f9f6f4" strokeWidth="4" strokeLinecap="round" />
            <ellipse cx="50" cy="46" rx="18" ry="6.5" fill="#d46c77" />
          </>
        )}
        {shape === 'D' && (
          <>
            <path
              d="M 19 19 Q 50 14 81 19 Q 86 60 50 63 Q 14 60 19 19 Z"
              fill="#591e2c"
              stroke={line}
              strokeWidth="5.2"
              strokeLinejoin="round"
            />
            <path d="M 28 22 H 72" stroke="#faf8f6" strokeWidth="4.5" strokeLinecap="round" />
            <ellipse cx="50" cy="52" rx="21" ry="9" fill="#dc737e" />
          </>
        )}
        {shape === 'E' && (
          <>
            <ellipse cx="50" cy="37" rx="22" ry="17" fill="#612230" stroke={line} strokeWidth="5" />
            <ellipse cx="50" cy="46" rx="13" ry="6" fill="#d46c77" />
          </>
        )}
        {shape === 'F' && (
          <ellipse cx="50" cy="36" rx="13" ry="13" fill="#5e212f" stroke={line} strokeWidth="5.2" />
        )}
        {shape === 'G' && (
          <>
            <rect x="25" y="27" width="50" height="15" rx="7" fill="#682735" stroke={line} strokeWidth="4.8" />
            <rect x="29" y="28" width="42" height="6" rx="2" fill="#fbf9f7" />
          </>
        )}
        {shape === 'H' && (
          <>
            <path
              d="M 22 23 Q 50 19 78 23 Q 80 51 50 53 Q 20 51 22 23 Z"
              fill="#622331"
              stroke={line}
              strokeWidth="5"
            />
            <ellipse cx="50" cy="41" rx="17" ry="9" fill="#df7882" />
          </>
        )}
      </svg>
    </div>
  );
};

/** Curved 2D anime eyelid overlay over left_eye and right_eye during deterministic blink frames. */
const BlinkOverlay = ({face, closure}: {face: PuppetFace; closure: number}) => {
  if (closure <= 0.02) return null;
  const skin = rgbCss(face.skin_rgb, 'rgb(242, 212, 194)');
  const line = rgbCss(face.line_rgb, 'rgb(36, 28, 34)');
  return (
    <>
      {([face.left_eye, face.right_eye] as const).map((box, idx) => {
        const [ex, ey, ew, eh] = box;
        const padX = ew * 0.08;
        const padY = eh * 0.10;
        const hRatio = Math.min(1, closure);
        const lashY = Math.round(12 + hRatio * 68);
        return (
          <div
            key={idx}
            style={{
              position: 'absolute',
              left: `${(ex - padX) * 100}%`,
              top: `${(ey - padY) * 100}%`,
              width: `${(ew + padX * 2) * 100}%`,
              height: `${(eh + padY * 2) * 100}%`,
              pointerEvents: 'none',
            }}
          >
            <svg
              width="100%"
              height="100%"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              style={{position: 'absolute', inset: 0, overflow: 'visible'}}
            >
              <ellipse
                cx="50"
                cy={lashY * 0.52}
                rx="50"
                ry={Math.max(12, lashY * 0.55)}
                fill={skin}
              />
              {closure >= 0.42 && (
                <path
                  d={`M 6 ${lashY - 6} Q 50 ${lashY + 12} 94 ${lashY - 6}`}
                  fill="none"
                  stroke={line}
                  strokeWidth="8.5"
                  strokeLinecap="round"
                />
              )}
            </svg>
          </div>
        );
      })}
    </>
  );
};

type PuppetPose = {
  bobY: number;
  torsoRot: number;
  breathScaleY: number;
  headRot: number;
  leadUpperArm: number;
  leadLowerArm: number;
  backUpperArm: number;
  backLowerArm: number;
  upperLegL: number;
  lowerLegL: number;
  footL: number;
  upperLegR: number;
  lowerLegR: number;
  footR: number;
  /** Vertical thigh foreshortening in box px (knee lift toward camera / support-leg compression). */
  thighLiftL: number;
  thighLiftR: number;
};

type LegSide = 'l' | 'r';
type Point2 = {x: number; y: number};
type LegGeometry = {
  hip: Point2;
  knee: Point2;
  ankle: Point2;
  upperLength: number;
  lowerLength: number;
};

/**
 * Relaxed arm hang relative to the A-pose master (degrees, positive = toward
 * the hips).  Keeps hands near the thighs instead of a flared mannequin pose.
 */
const ARM_REST_UPPER = 6;
const ARM_REST_LOWER = 3;

const POSE_KEYS: ReadonlyArray<keyof PuppetPose> = [
  'bobY',
  'torsoRot',
  'breathScaleY',
  'headRot',
  'leadUpperArm',
  'leadLowerArm',
  'backUpperArm',
  'backLowerArm',
  'upperLegL',
  'lowerLegL',
  'footL',
  'upperLegR',
  'lowerLegR',
  'footR',
  'thighLiftL',
  'thighLiftR',
];

const blendPose = (from: PuppetPose, to: PuppetPose, weight: number): PuppetPose => {
  const result = {...from};
  for (const key of POSE_KEYS) {
    result[key] = mix(weight, from[key], to[key]);
  }
  return result;
};

const neutralPose = (
  timelineFrame: number,
  fps: number,
  nativeDir: number,
  seedOffset: number,
  cameraMoving: boolean,
): PuppetPose => {
  const t = timelineFrame / Math.max(1, fps);
  const secondary = cameraMoving ? 0.5 : 1;
  const breathDamping = cameraMoving ? 0.72 : 1;
  const slowBreath = Math.sin((t * Math.PI * 2) / 3.6 + seedOffset * 0.23);
  const slowSway = Math.sin((t * Math.PI * 2) / 5.4 + seedOffset);
  const armDrift = Math.sin((t * Math.PI * 2) / 4.8 + seedOffset * 0.71);
  return {
    bobY: 0,
    torsoRot: slowSway * 0.28 * nativeDir * secondary,
    breathScaleY: 1 + 0.0028 * slowBreath * breathDamping,
    headRot: -slowSway * 0.22 * nativeDir * secondary,
    leadUpperArm: (ARM_REST_UPPER + armDrift * 0.42 * secondary) * nativeDir,
    leadLowerArm: (ARM_REST_LOWER + armDrift * 0.24 * secondary) * nativeDir,
    backUpperArm: -(ARM_REST_UPPER + armDrift * 0.38 * secondary) * nativeDir,
    backLowerArm: -(ARM_REST_LOWER + armDrift * 0.20 * secondary) * nativeDir,
    upperLegL: 0,
    lowerLegL: 0,
    footL: 0,
    upperLegR: 0,
    lowerLegR: 0,
    footR: 0,
    thighLiftL: 0,
    thighLiftR: 0,
  };
};

const pointDistance = (a: Point2, b: Point2) => Math.hypot(b.x - a.x, b.y - a.y);

const legGeometry = (
  rig: CharacterRig,
  side: LegSide,
  boxW: number,
  boxH: number,
): LegGeometry => {
  const parts = rig.parts || {};
  const sideOffset = side === 'l' ? -0.045 : 0.045;
  const upperPivot = parts[`upper_leg_${side}`]?.pivot ?? [rig.anchor_x + sideOffset, 0.55];
  const lowerPivot = parts[`lower_leg_${side}`]?.pivot ?? [rig.anchor_x + sideOffset, 0.76];
  const anklePivot =
    rig.ankles?.[side] ??
    parts[`foot_${side}`]?.pivot ??
    [lowerPivot[0], rig.foot_y - 0.05];
  const hip = {x: upperPivot[0] * boxW, y: upperPivot[1] * boxH};
  const knee = {x: lowerPivot[0] * boxW, y: lowerPivot[1] * boxH};
  const ankle = {x: anklePivot[0] * boxW, y: anklePivot[1] * boxH};
  return {
    hip,
    knee,
    ankle,
    upperLength: Math.max(4, pointDistance(hip, knee)),
    lowerLength: Math.max(4, pointDistance(knee, ankle)),
  };
};

const scaledOneShotDurations = (clipFrames: number, durations: number[]) => {
  const total = durations.reduce((sum, value) => sum + value, 0);
  const scale = total > clipFrames ? clipFrames / Math.max(1, total) : 1;
  return durations.map(value => value * scale);
};

const evaluateActionPose = ({
  type,
  localFrame,
  clipFrames,
  timelineFrame,
  fps,
  speaking,
  nativeDir,
  seedOffset,
  cameraMoving,
}: {
  type: PuppetClipType;
  localFrame: number;
  clipFrames: number;
  timelineFrame: number;
  fps: number;
  speaking: boolean;
  nativeDir: number;
  seedOffset: number;
  cameraMoving: boolean;
}): PuppetPose => {
  const pose = neutralPose(timelineFrame, fps, nativeDir, seedOffset, cameraMoving);
  const t = timelineFrame / Math.max(1, fps);
  const secondary = cameraMoving ? 0.5 : 1;

  if (type === 'listen') {
    const nodPeriod = Math.max(1, fps * 2.8);
    const nodPhase = positiveModulo(timelineFrame + seedOffset * fps * 0.37, nodPeriod) / nodPeriod;
    const nod = nodPhase < 0.22 ? Math.sin((nodPhase / 0.22) * Math.PI) : 0;
    pose.torsoRot *= 0.58;
    pose.headRot += nod * 1.2 * nativeDir * secondary;
  } else if (type === 'talk') {
    const cycleFrames = Math.max(24, fps * 2.15);
    const phase = positiveModulo(localFrame + seedOffset * 3, cycleFrames) / cycleFrames;
    const emphasisWindow = phase < 0.43 ? Math.sin((phase / 0.43) * Math.PI) : 0;
    const emphasis = emphasisWindow * emphasisWindow * (speaking ? 1 : 0.35);
    const syllable =
      Math.sin(t * Math.PI * 2 * 1.8 + seedOffset) * 1.0 +
      Math.sin(t * Math.PI * 2 * 0.72 + seedOffset * 0.4) * 0.45;
    pose.torsoRot = (-0.35 + Math.sin(t * 1.2 + seedOffset) * 0.22) * nativeDir * secondary;
    pose.headRot = clampValue(syllable, -1.6, 1.6) * nativeDir * secondary;
    pose.leadUpperArm =
      (ARM_REST_UPPER + (-2.2 * emphasis + Math.sin(t * 1.7) * 0.35) * secondary) * nativeDir;
    pose.leadLowerArm -=
      (8 + 6 * Math.sin(Math.PI * clamp01(phase / 0.43))) * emphasis * nativeDir * secondary;
    pose.backLowerArm -= 2.5 * emphasis * nativeDir * secondary;
  } else if (type === 'point') {
    const holdFrames = Math.min(26, clipFrames * 0.45);
    const [anticipateFrames, raiseFrames, hold, lowerFrames] = scaledOneShotDurations(
      clipFrames,
      [4, 8, holdFrames, 10],
    );
    const raiseStart = anticipateFrames;
    const holdStart = raiseStart + raiseFrames;
    const lowerStart = holdStart + hold;
    const finish = lowerStart + lowerFrames;
    const anticipation =
      localFrame < anticipateFrames
        ? Math.sin((localFrame / Math.max(1, anticipateFrames)) * Math.PI)
        : 0;
    let gesture = 0;
    if (localFrame >= raiseStart && localFrame < holdStart) {
      gesture = smoothstep((localFrame - raiseStart) / Math.max(1, raiseFrames));
    } else if (localFrame >= holdStart && localFrame < lowerStart) {
      gesture = 1;
    } else if (localFrame >= lowerStart && localFrame < finish) {
      gesture = 1 - smoothstep((localFrame - lowerStart) / Math.max(1, lowerFrames));
    }
    const heldBreath = gesture * Math.sin(t * Math.PI * 2 * 0.42) * 0.22;
    pose.bobY += anticipation * 0.7;
    pose.torsoRot += (gesture * 2 - anticipation * 0.45) * nativeDir;
    pose.headRot += gesture * 2 * nativeDir;
    pose.leadUpperArm += (-36 * gesture + anticipation * 3 + heldBreath) * nativeDir;
    pose.leadLowerArm += (-18 * gesture + anticipation * 2 + heldBreath) * nativeDir;
    pose.backUpperArm += 2.5 * gesture * nativeDir;
    pose.backLowerArm += -5 * gesture * nativeDir;
  } else if (type === 'wave') {
    const [raiseFrames, waveFrames, lowerFrames] = scaledOneShotDurations(clipFrames, [7, 22, 9]);
    const waveStart = raiseFrames;
    const lowerStart = waveStart + waveFrames;
    const finish = lowerStart + lowerFrames;
    let gesture = 0;
    let wave = 0;
    if (localFrame < waveStart) {
      gesture = smoothstep(localFrame / Math.max(1, raiseFrames));
    } else if (localFrame < lowerStart) {
      gesture = 1;
      const waveProgress = (localFrame - waveStart) / Math.max(1, waveFrames);
      wave = Math.sin(waveProgress * Math.PI * 2 * 2.5);
    } else if (localFrame < finish) {
      gesture = 1 - smoothstep((localFrame - lowerStart) / Math.max(1, lowerFrames));
    }
    // Compact "hi" wave: the elbow stays low and the forearm comes up nearly
    // vertical so the hand waves beside the shoulder instead of reaching into
    // a neighbour's space.  Elbow leads on the way up, trails on the way down.
    const elbowLead = smoothstep(gesture * 1.25);
    const forearmUp = smoothstep((gesture - 0.15) / 0.85);
    pose.torsoRot += -1.1 * gesture * nativeDir;
    pose.headRot += 0.7 * gesture * nativeDir;
    pose.leadUpperArm += -34 * elbowLead * nativeDir;
    pose.leadLowerArm += (-120 * forearmUp + wave * 10 * gesture) * nativeDir;
    pose.backUpperArm += 2.5 * gesture * nativeDir;
    pose.backLowerArm += -4.5 * gesture * nativeDir;
  } else if (type === 'react') {
    const [anticipateFrames, startleFrames, settleFrames] = scaledOneShotDurations(
      clipFrames,
      [3, 6, 16],
    );
    const startleStart = anticipateFrames;
    const settleStart = startleStart + startleFrames;
    const finish = settleStart + settleFrames;
    const anticipation =
      localFrame < startleStart
        ? Math.sin((localFrame / Math.max(1, anticipateFrames)) * Math.PI)
        : 0;
    let startle = 0;
    if (localFrame >= startleStart && localFrame < settleStart) {
      startle = smoothstep((localFrame - startleStart) / Math.max(1, startleFrames));
    } else if (localFrame >= settleStart && localFrame < finish) {
      startle = 1 - smoothstep((localFrame - settleStart) / Math.max(1, settleFrames));
    }
    pose.bobY += anticipation * 0.55 - startle * 3;
    pose.torsoRot += (anticipation * 0.8 - startle * 4) * nativeDir;
    pose.headRot += -startle * 2.2 * nativeDir;
    pose.leadUpperArm += -13 * startle * nativeDir;
    pose.leadLowerArm += -18 * startle * nativeDir;
    pose.backUpperArm += 9 * startle * nativeDir;
    pose.backLowerArm += -14 * startle * nativeDir;
  } else if (type === 'settle') {
    const settleFrames = Math.min(
      clipFrames,
      Math.max(1, Math.min(10, Math.max(6, Math.floor(clipFrames * 0.6)))),
    );
    const settleProgress = clamp01(localFrame / Math.max(1, settleFrames));
    pose.bobY += Math.sin(settleProgress * Math.PI) * 1.8;
    pose.torsoRot *= 0.35;
    pose.headRot *= 0.35;
  } else if (type === 'work') {
    const workPulse = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 0.82 + seedOffset);
    pose.torsoRot = (2.2 + workPulse * 0.8) * nativeDir;
    pose.headRot = (1.4 + workPulse * 0.6) * nativeDir;
    pose.leadUpperArm = (-19 - workPulse * 5) * nativeDir;
    pose.leadLowerArm = (-22 - workPulse * 7) * nativeDir;
    pose.backUpperArm = (-14 - (1 - workPulse) * 4) * nativeDir;
    pose.backLowerArm = (-18 - (1 - workPulse) * 5) * nativeDir;
  } else if (type === 'console') {
    const consolePulse = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 0.7 + seedOffset);
    pose.torsoRot = 2.3 * nativeDir;
    pose.headRot = (1.8 + consolePulse * 0.7) * nativeDir;
    pose.leadUpperArm = (-22 - consolePulse * 3) * nativeDir;
    pose.leadLowerArm = (-25 - consolePulse * 5) * nativeDir;
    pose.backUpperArm = (-18 - (1 - consolePulse) * 2) * nativeDir;
    pose.backLowerArm = (-21 - (1 - consolePulse) * 4) * nativeDir;
  } else if (type === 'carry') {
    const carryBreath = Math.sin(t * Math.PI * 2 * 0.55 + seedOffset) * 0.8;
    pose.leadUpperArm = (-22 + carryBreath) * nativeDir;
    pose.leadLowerArm = (-36 + carryBreath) * nativeDir;
    pose.backUpperArm = (-20 - carryBreath) * nativeDir;
    pose.backLowerArm = (-33 - carryBreath) * nativeDir;
  }

  if (speaking && (type === 'idle' || type === 'listen')) {
    pose.headRot += Math.sin(t * Math.PI * 2 * 1.35 + seedOffset) * 0.7 * nativeDir * secondary;
  }
  return pose;
};

/**
 * Front / three-quarter cutout walk.
 *
 * Both legs cycle every half-step: the support foot slides back from +A to -A
 * (phase-locked to body travel, so no step happens while the body is still)
 * while the swing foot lifts and travels -A -> +A.  Stride amplitude A stays
 * well inside the ankle spacing so the feet never cross on a front-facing
 * body, and knee lift is drawn as thigh foreshortening instead of a sideways
 * knee rotation (which reads as knock-knees in 2D).
 */
const applyWalkingGait = ({
  pose,
  clip,
  localFrame,
  clipFrames,
  rig,
  boxW,
  boxH,
  targetFigureH,
  screenWidth,
  localForward,
  nativeDir,
  index,
  cameraMoving,
}: {
  pose: PuppetPose;
  clip: PuppetClip;
  localFrame: number;
  clipFrames: number;
  rig: CharacterRig;
  boxW: number;
  boxH: number;
  targetFigureH: number;
  screenWidth: number;
  localForward: number;
  nativeDir: number;
  index: number;
  cameraMoving: boolean;
}): PuppetPose => {
  const totalWalkPx = Math.abs(clip.toX - clip.fromX) * screenWidth;
  if (totalWalkPx < 1) return pose;

  const u = clamp01(localFrame / Math.max(1, clipFrames));
  const travel = sinusoidalEase(u);
  const stepLengthPx = Math.max(38, targetFigureH * 0.19);
  const numHalfSteps = Math.max(2, Math.round(totalWalkPx / stepLengthPx));
  const stepProgress = travel * numHalfSteps;
  const halfStepIndex = u >= 1 ? numHalfSteps - 1 : Math.min(numHalfSteps - 1, Math.floor(stepProgress));
  const p = u >= 1 ? 1 : stepProgress - halfStepIndex;
  // Ease stride in over the first half-step and out over the last so the walk
  // starts and ends with both feet together under the hips.
  const envelope = smoothstep(Math.min(stepProgress, numHalfSteps - stepProgress) / 0.6);

  const carryMultiplier = clip.type === 'carry' ? 0.76 : 1;

  // True side-profile stride: legs scissor in the picture plane (no front-view foreshortening cheat).
  if (rig.view_angle === 'side') {
    const phi = stepProgress * Math.PI;
    const sL = Math.cos(phi);
    const sR = -sL;
    const strideDeg = 12.5 * carryMultiplier * envelope;
    const flexNormL = Math.max(0, -Math.sin(phi)) * envelope;
    const flexNormR = Math.max(0, Math.sin(phi)) * envelope;
    const flexMax = 16 * carryMultiplier;
    const sideLiftPx = targetFigureH * 0.03 * carryMultiplier;
    const sideDip = 2 * carryMultiplier * envelope * Math.cos(phi) ** 2;
    const out = {...pose};
    out.bobY = sideDip;
    out.upperLegL = -sL * strideDeg * localForward;
    out.upperLegR = -sR * strideDeg * localForward;
    out.lowerLegL = flexNormL * flexMax * localForward;
    out.lowerLegR = flexNormR * flexMax * localForward;
    out.footL = -flexNormL * flexMax * 0.2 * localForward;
    out.footR = -flexNormR * flexMax * 0.2 * localForward;
    out.thighLiftL = sideDip + flexNormL * sideLiftPx;
    out.thighLiftR = sideDip + flexNormR * sideLiftPx;
    const armSwing = sL * envelope;
    const sideSecondary = cameraMoving ? 0.5 : 1;
    if (clip.type === 'carry') {
      out.torsoRot = 0.8 * localForward;
      out.leadUpperArm += armSwing * 1.2 * nativeDir;
      out.backUpperArm -= armSwing * 1.2 * nativeDir;
    } else {
      out.torsoRot = 1.0 * localForward * Math.max(0.4, envelope);
      out.headRot = Math.sin(phi * 2) * 0.4 * localForward * sideSecondary;
      out.leadUpperArm = (ARM_REST_UPPER * 0.8 + 6 * armSwing) * nativeDir;
      out.backUpperArm = (-ARM_REST_UPPER * 0.8 - 6 * armSwing) * nativeDir;
      out.leadLowerArm = (ARM_REST_LOWER + 7.5 * armSwing) * nativeDir;
      out.backLowerArm = (-ARM_REST_LOWER - 7.5 * armSwing) * nativeDir;
    }
    return out;
  }

  const left = legGeometry(rig, 'l', boxW, boxH);
  const right = legGeometry(rig, 'r', boxW, boxH);
  const ankleSeparation = Math.abs(right.ankle.x - left.ankle.x);
  const amplitude =
    Math.min(
      stepLengthPx * 0.3,
      boxW * 0.06,
      ankleSeparation > 2 ? ankleSeparation * 0.32 : boxW * 0.03,
    ) *
    carryMultiplier *
    envelope;
  const liftPx = targetFigureH * 0.036 * carryMultiplier * Math.max(0.35, envelope);

  const swingP = clamp01((p - 0.1) / 0.8);
  const swingLift = Math.sin(swingP * Math.PI);
  const supportOffset = amplitude * (1 - 2 * p);
  const swingOffset = amplitude * (-1 + 2 * smoothstep(swingP));
  // Body dips at double support and passes over a straight support leg.
  const dip = 2.2 * carryMultiplier * envelope * Math.cos(p * Math.PI) ** 2;

  const swingSide: LegSide = (halfStepIndex + Math.floor(Math.abs(index))) % 2 === 0 ? 'l' : 'r';
  const legAngles = (geometry: LegGeometry, offset: number, lift: number) => {
    const legLength = Math.max(8, geometry.ankle.y - geometry.hip.y);
    const liftNorm = lift / Math.max(1, liftPx);
    return {
      upper: -(Math.atan2(offset * localForward, legLength) * 180) / Math.PI - 3 * liftNorm * localForward,
      lower: 5 * liftNorm * localForward,
      foot: 3 * liftNorm * localForward,
    };
  };
  const swing = legAngles(swingSide === 'l' ? left : right, swingOffset, swingLift * liftPx);
  const support = legAngles(swingSide === 'l' ? right : left, supportOffset, 0);

  const result = {...pose};
  result.bobY = dip;
  const swingValues = {upper: swing.upper, lower: swing.lower, foot: swing.foot, lift: dip + swingLift * liftPx};
  const supportValues = {upper: support.upper, lower: support.lower, foot: 0, lift: dip};
  const l = swingSide === 'l' ? swingValues : supportValues;
  const r = swingSide === 'r' ? swingValues : supportValues;
  result.upperLegL = l.upper;
  result.lowerLegL = l.lower;
  result.footL = l.foot;
  result.thighLiftL = l.lift;
  result.upperLegR = r.upper;
  result.lowerLegR = r.lower;
  result.footR = r.foot;
  result.thighLiftR = r.lift;

  // Arms swing opposite the stepping leg; only a few degrees because on a
  // front-facing body the real swing happens in depth.
  const gaitWave = Math.sin(p * Math.PI) * (swingSide === 'l' ? -1 : 1) * envelope;
  const secondary = cameraMoving ? 0.5 : 1;
  if (clip.type === 'carry') {
    result.torsoRot = 0.8 * localForward;
    result.headRot = Math.sin(p * Math.PI * 2) * 0.35 * localForward * secondary;
    result.leadUpperArm += gaitWave * 1.2 * nativeDir;
    result.backUpperArm -= gaitWave * 1.2 * nativeDir;
  } else {
    result.torsoRot = 1.2 * localForward * Math.max(0.4, envelope);
    result.headRot = Math.sin(p * Math.PI * 2) * 0.45 * localForward * secondary;
    result.leadUpperArm = (ARM_REST_UPPER * 0.8 + gaitWave * 3) * nativeDir;
    result.backUpperArm = (-ARM_REST_UPPER * 0.8 + gaitWave * 3) * nativeDir;
    result.leadLowerArm = (ARM_REST_LOWER + 1.5 + Math.abs(gaitWave) * 1.5) * nativeDir;
    result.backLowerArm = -(ARM_REST_LOWER + 1.5 + Math.abs(gaitWave) * 1.5) * nativeDir;
  }
  return result;
};

/** Planted clips never leave the floor: body dips compress both thighs, rises are dropped. */
const plantStationaryPose = (pose: PuppetPose, rig: CharacterRig): PuppetPose => {
  if (rig.level !== 'full') return pose;
  const dip = Math.max(0, pose.bobY);
  return {...pose, bobY: dip, thighLiftL: dip, thighLiftR: dip};
};

type PoseContext = {
  timelineFrame: number;
  fps: number;
  speaking: boolean;
  cameraMoving: boolean;
  rig: CharacterRig;
  boxW: number;
  boxH: number;
  targetFigureH: number;
  screenWidth: number;
  nativeDir: number;
  flipX: number;
  fallbackLocalForward: number;
  index: number;
};

const evaluateClipPose = (clip: PuppetClip, context: PoseContext): PuppetPose => {
  const clipFrames = Math.max(1, clip.endFrame - clip.startFrame);
  const localFrame = clampValue(context.timelineFrame - clip.startFrame, 0, clipFrames);
  const seedOffset = context.index * 1.37;
  const worldTravelDir = Math.sign(clip.toX - clip.fromX);
  const localForward =
    worldTravelDir === 0 ? context.fallbackLocalForward : worldTravelDir * context.flipX;
  let pose = evaluateActionPose({
    type: clip.type,
    localFrame,
    clipFrames,
    timelineFrame: context.timelineFrame,
    fps: context.fps,
    speaking: context.speaking,
    nativeDir: context.nativeDir,
    seedOffset,
    cameraMoving: context.cameraMoving,
  });
  const totalWalkPx = Math.abs(clip.toX - clip.fromX) * context.screenWidth;
  const moving = clip.type === 'walk' || (clip.type === 'carry' && totalWalkPx >= 1);
  if (moving && totalWalkPx >= 1) {
    pose = applyWalkingGait({
      pose,
      clip,
      localFrame,
      clipFrames,
      rig: context.rig,
      boxW: context.boxW,
      boxH: context.boxH,
      targetFigureH: context.targetFigureH,
      screenWidth: context.screenWidth,
      localForward,
      nativeDir: context.nativeDir,
      index: context.index,
      cameraMoving: context.cameraMoving,
    });
  } else {
    pose = plantStationaryPose(pose, context.rig);
  }
  return pose;
};

const findActiveClip = (timeline: PuppetClip[], timelineFrame: number): PuppetClip => {
  let active = timeline[0];
  for (const clip of timeline) {
    if (timelineFrame < clip.startFrame) break;
    active = clip;
    if (timelineFrame < clip.endFrame) break;
  }
  return active;
};

const evaluateTimelinePose = (
  timeline: PuppetClip[],
  activeClip: PuppetClip,
  context: PoseContext,
): PuppetPose => {
  let result = evaluateClipPose(activeClip, context);
  for (let i = 1; i < timeline.length; i++) {
    const outgoing = timeline[i - 1];
    const incoming = timeline[i];
    const outgoingFrames = Math.max(1, outgoing.endFrame - outgoing.startFrame);
    const incomingFrames = Math.max(1, incoming.endFrame - incoming.startFrame);
    const outgoingBlend = Math.min(7, Math.floor(outgoingFrames * 0.35));
    const incomingBlend = Math.min(7, Math.floor(incomingFrames * 0.35));
    const blendFrames = Math.max(1, Math.min(outgoingBlend, incomingBlend));
    const boundary = incoming.startFrame;
    if (
      context.timelineFrame >= boundary - blendFrames &&
      context.timelineFrame <= boundary + blendFrames
    ) {
      const weight = smoothstep(
        (context.timelineFrame - (boundary - blendFrames)) / (blendFrames * 2),
      );
      result = blendPose(
        evaluateClipPose(outgoing, context),
        evaluateClipPose(incoming, context),
        weight,
      );
      break;
    }
  }
  return result;
};

const partImgStyle: React.CSSProperties = {
  position: 'absolute',
  inset: 0,
  width: '100%',
  height: '100%',
  objectFit: 'fill',
  pointerEvents: 'none',
};

const pivotOrigin = (part: PuppetPart | undefined, fallback: [number, number]): string => {
  const p = part?.pivot ?? fallback;
  return `${p[0] * 100}% ${p[1] * 100}%`;
};

const LimbChain = ({
  upper,
  lower,
  foot,
  upperDeg,
  lowerDeg,
  footDeg = 0,
  liftPx = 0,
  upperLengthPx = 0,
  fallbackUpper,
  fallbackLower,
  fallbackFoot,
}: {
  upper?: PuppetPart;
  lower?: PuppetPart;
  foot?: PuppetPart;
  upperDeg: number;
  lowerDeg: number;
  footDeg?: number;
  /** Foreshorten the upper segment by this many px and raise the rest of the chain with it. */
  liftPx?: number;
  upperLengthPx?: number;
  fallbackUpper: [number, number];
  fallbackLower: [number, number];
  fallbackFoot?: [number, number];
}) => {
  if (!upper && !lower && !foot) return null;
  const lift = upperLengthPx > 0 ? clampValue(liftPx, 0, upperLengthPx * 0.3) : 0;
  const upperScaleY = upperLengthPx > 0 ? 1 - lift / upperLengthPx : 1;
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        transformOrigin: pivotOrigin(upper, fallbackUpper),
        transform: `rotate(${upperDeg}deg)`,
      }}
    >
      {upper && (
        <Img
          src={staticFile(upper.src)}
          style={
            lift > 0.01
              ? {...partImgStyle, transformOrigin: pivotOrigin(upper, fallbackUpper), transform: `scaleY(${upperScaleY})`}
              : partImgStyle
          }
        />
      )}
      {(lower || foot) && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            transformOrigin: pivotOrigin(lower, fallbackLower),
            transform: `translateY(${-lift}px) rotate(${lowerDeg}deg)`,
          }}
        >
          {lower && <Img src={staticFile(lower.src)} style={partImgStyle} />}
          {foot && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                transformOrigin: pivotOrigin(foot, fallbackFoot ?? fallbackLower),
                transform: `rotate(${footDeg}deg)`,
              }}
            >
              <Img src={staticFile(foot.src)} style={partImgStyle} />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/** 5-frame cushioned anime turnaround: scaleX squash (1 -> 0.75 -> 1) with a small body dip, rig/flip swap at the middle frame. */
const TURN_SCALE_X = [0.9, 0.78, 0.75, 0.78, 0.9];
const TURN_DIP_PX = [1.2, 2.5, 1.6, 0.6, 0];

type GestureHandKind = 'point' | 'wave' | 'console';

const gestureHandKind = (type: PuppetClipType): GestureHandKind | null => {
  if (type === 'point') return 'point';
  if (type === 'wave') return 'wave';
  if (type === 'console' || type === 'work') return 'console';
  return null;
};

const shadeRgb = (rgb: [number, number, number] | undefined, k: number, fallback: string) =>
  rgb && rgb.length >= 3
    ? `rgb(${Math.round(rgb[0] * k)}, ${Math.round(rgb[1] * k)}, ${Math.round(rgb[2] * k)})`
    : fallback;

/**
 * Crisp cel-shaded gesture hand drawn in skin / shadow / ink colours from the face sampler.
 * Drawn in a 100x120 viewBox with the wrist anchor at (50, 14) and fingers extending along +y
 * (continuing the forearm).  The retained sleeve cuff sits on top of the hand base.
 */
const AnimeGestureHand = ({
  kind,
  face,
  left,
  top,
  size,
  rotateDeg,
  mirror,
}: {
  kind: GestureHandKind;
  face: PuppetFace;
  left: number;
  top: number;
  size: number;
  rotateDeg: number;
  mirror: number;
}) => {
  const skin = rgbCss(face.skin_rgb, 'rgb(242, 212, 194)');
  const shade = shadeRgb(face.skin_rgb, 0.8, 'rgb(205, 168, 150)');
  const line = rgbCss(face.line_rgb, 'rgb(36, 28, 34)');
  const sw = 3;
  const finger = (x: number, y: number, w: number, h: number, key: string) => (
    <g key={key}>
      <rect x={x} y={y} width={w} height={h} rx={w / 2} fill={skin} />
      <path
        d={`M ${x + w * 0.62} ${y + h * 0.35} Q ${x + w * 0.9} ${y + h * 0.6} ${x + w * 0.62} ${y + h * 0.9}`}
        fill="none"
        stroke={shade}
        strokeWidth={2.4}
        opacity={0.7}
      />
    </g>
  );
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width: size,
        height: size * 1.2,
        transformOrigin: '50% 11.7%',
        transform: `rotate(${rotateDeg}deg) scaleX(${mirror})`,
        pointerEvents: 'none',
      }}
    >
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 100 120"
        style={{position: 'absolute', inset: 0, overflow: 'visible'}}
      >
        <g stroke={line} strokeWidth={sw} strokeLinejoin="round" strokeLinecap="round">
          {kind === 'point' && (
            <>
              <path d="M 66 26 Q 87 32 83 55 Q 80 64 70 58 Z" fill={skin} />
              <path d="M 31 6 L 69 6 Q 74 36 72 54 Q 70 72 54 74 L 42 74 Q 28 72 29 50 Q 29 28 31 6 Z" fill={skin} />
              <path d="M 58 18 Q 71 40 66 64 Q 62 70 54 72 Q 66 52 58 18 Z" fill={shade} stroke="none" />
              <rect x="50" y="46" width="25" height="30" rx="11" fill={skin} />
              <path d="M 53 57 H 71 M 53 66 H 70" fill="none" strokeWidth={2.2} />
              <rect x="31" y="46" width="18" height="68" rx="9" fill={skin} />
              <path d="M 34 66 H 46" fill="none" strokeWidth={2.2} />
              <path d="M 44 54 Q 47 82 44 106" fill="none" stroke={shade} strokeWidth={2.6} opacity={0.65} />
            </>
          )}
          {kind === 'wave' && (
            <>
              <path d="M 70 30 Q 91 40 88 62 Q 86 71 76 65 Q 77 52 68 46 Z" fill={skin} />
              <path d="M 29 6 L 71 6 Q 76 38 72 58 Q 68 74 50 74 Q 32 74 28 58 Q 25 38 29 6 Z" fill={skin} />
              <path d="M 60 16 Q 74 40 68 64 Q 64 72 52 73 Q 68 52 60 16 Z" fill={shade} stroke="none" />
              {finger(27, 58, 11, 52, 'f0')}
              {finger(39, 58, 11, 60, 'f1')}
              {finger(51, 58, 11, 55, 'f2')}
              {finger(63, 58, 10, 42, 'f3')}
            </>
          )}
          {kind === 'console' && (
            <>
              <path d="M 68 30 Q 84 38 81 56 Q 79 63 71 58 Q 72 48 66 42 Z" fill={skin} />
              <path d="M 30 6 L 70 6 Q 75 36 72 56 Q 69 74 50 74 Q 31 74 28 56 Q 26 36 30 6 Z" fill={skin} />
              <path d="M 60 16 Q 73 40 67 64 Q 63 72 52 73 Q 67 52 60 16 Z" fill={shade} stroke="none" />
              {finger(28, 60, 11, 34, 'f0')}
              {finger(40, 60, 11, 44, 'f1')}
              {finger(52, 60, 11, 40, 'f2')}
              {finger(64, 60, 10, 30, 'f3')}
            </>
          )}
        </g>
      </svg>
    </div>
  );
};

export const Puppet = ({
  name,
  rig,
  sideRig = null,
  action = 'idle',
  fromX,
  toX,
  clips,
  facing,
  depth = 'mid',
  frame,
  fps,
  duration,
  speaking,
  mouthCue = 'X',
  cameraMoving = false,
  partnerX,
  width,
  height,
  index = 0,
  phaseContinuation = false,
  statureScale = 1,
  figureHeightRatioOverride,
  floorYOverride,
  renderPass = 'all',
  ambientTint,
}: {
  name: string;
  rig: CharacterRig;
  /** Optional 80-90 degree profile rig used for walking / face-to-face profile beats. */
  sideRig?: CharacterRig | null;
  action?: PuppetAction;
  fromX: number;
  toX: number;
  clips?: PuppetClip[];
  facing?: PuppetFacing;
  depth?: PuppetDepth;
  frame: number;
  fps: number;
  duration: number;
  speaking: boolean;
  mouthCue?: string;
  cameraMoving?: boolean;
  partnerX?: number;
  width: number;
  height: number;
  index?: number;
  phaseContinuation?: boolean;
  /** Per-character height multiplier (default 1.0). */
  statureScale?: number;
  figureHeightRatioOverride?: number;
  floorYOverride?: number;
  /**
   * 'body_behind_desk': shadow + legs + back arm + torso + head + lead upper arm.
   * 'arms_over_desk': ONLY lead forearm + gesture hand (no ground shadow).
   */
  renderPass?: 'all' | 'body_behind_desk' | 'arms_over_desk';
  ambientTint?: string;
}) => {
  const stagePlane = DEPTH_STAGE[depth] || DEPTH_STAGE.mid;
  const stature = Number.isFinite(statureScale) && statureScale > 0 ? statureScale : 1;
  const baseRatio =
    typeof figureHeightRatioOverride === 'number' &&
    Number.isFinite(figureHeightRatioOverride) &&
    figureHeightRatioOverride > 0
      ? figureHeightRatioOverride
      : stagePlane.figureHeightRatio;
  const targetFigureH = height * baseRatio * stature;
  const floorYNorm =
    typeof floorYOverride === 'number' && Number.isFinite(floorYOverride) ? floorYOverride : stagePlane.floorY;
  const floorPxY = floorYNorm * height;

  const timelineFrame = clampValue(frame, 0, Math.max(0, duration));
  const fallbackClip: PuppetClip = {
    type: action,
    startFrame: 0,
    endFrame: Math.max(1, duration),
    fromX,
    toX,
  };
  const timeline: PuppetClip[] =
    clips && clips.length > 0
      ? clips
          .map(clip => {
            const startFrame = clampValue(
              Number.isFinite(clip.startFrame) ? clip.startFrame : 0,
              0,
              Math.max(0, duration),
            );
            const rawEnd = Number.isFinite(clip.endFrame) ? clip.endFrame : duration;
            return {
              ...clip,
              startFrame,
              endFrame: Math.max(startFrame + 1, clampValue(rawEnd, 0, Math.max(1, duration))),
              fromX: Number.isFinite(clip.fromX) ? clip.fromX : fromX,
              toX: Number.isFinite(clip.toX) ? clip.toX : toX,
            };
          })
          .sort((a, b) => a.startFrame - b.startFrame)
      : [fallbackClip];
  const activeClip = findActiveClip(timeline, timelineFrame);
  const activeClipFrames = Math.max(1, activeClip.endFrame - activeClip.startFrame);
  const activeU = clamp01((timelineFrame - activeClip.startFrame) / activeClipFrames);
  const activeTravel = Math.abs(activeClip.toX - activeClip.fromX);
  const activeMoves =
    activeClip.type === 'walk' || (activeClip.type === 'carry' && activeTravel > 0.000001);
  const motionProgress = sinusoidalEase(activeU);
  const worldX =
    (activeMoves
      ? mix(motionProgress, activeClip.fromX, activeClip.toX)
      : activeClip.fromX) * width;
  const approachScale = mix(
    motionProgress,
    activeClip.fromScale ?? 1,
    activeClip.toScale ?? 1,
  );
  const activeXNorm = worldX / Math.max(1, width);

  // ---- Rig selection (profile vs three-quarter) and facing per clip -------------------------
  const nativeOf = (r: CharacterRig): PuppetFacing => (r.native_facing === 'left' ? 'left' : 'right');
  const primaryNative = nativeOf(rig);
  const rigForClip = (clip: PuppetClip): CharacterRig =>
    clip.pose === 'side' && sideRig ? sideRig : rig;
  const facingForClip = (clip: PuppetClip, xNorm: number): PuppetFacing => {
    const requested = clip.facing ?? facing;
    if (requested === 'left' || requested === 'right') return requested;
    const travel = Math.sign(clip.toX - clip.fromX);
    if (travel < 0) return 'left';
    if (travel > 0) return 'right';
    if (typeof partnerX === 'number' && Math.abs(partnerX - xNorm) > 0.005) {
      return partnerX < xNorm ? 'left' : 'right';
    }
    return primaryNative;
  };

  const activeRig = rigForClip(activeClip);
  const desiredFacing = facingForClip(activeClip, activeXNorm);

  // ---- 5-frame turnaround window around a rig / facing change ---------------------------------
  const activeIndex = timeline.indexOf(activeClip);
  const prevClip = activeIndex > 0 ? timeline[activeIndex - 1] : undefined;
  const nextClip = activeIndex >= 0 && activeIndex < timeline.length - 1 ? timeline[activeIndex + 1] : undefined;
  const stateChanged = (aRig: CharacterRig, aFacing: PuppetFacing, bRig: CharacterRig, bFacing: PuppetFacing) =>
    aRig !== bRig || aFacing !== bFacing;
  let turnD: number | null = null;
  const sinceStart = timelineFrame - activeClip.startFrame;
  if (prevClip && sinceStart >= 0 && sinceStart <= 2) {
    if (stateChanged(rigForClip(prevClip), facingForClip(prevClip, prevClip.toX), activeRig, desiredFacing)) {
      turnD = sinceStart;
    }
  }
  if (turnD === null && nextClip) {
    const untilNext = nextClip.startFrame - timelineFrame;
    if (
      untilNext >= 1 &&
      untilNext <= 2 &&
      stateChanged(activeRig, desiredFacing, rigForClip(nextClip), facingForClip(nextClip, nextClip.fromX))
    ) {
      turnD = -untilNext;
    }
  }
  const turnScaleX = turnD === null ? 1 : TURN_SCALE_X[turnD + 2];
  const turnDip = turnD === null ? 0 : TURN_DIP_PX[turnD + 2];

  // ---- Rig geometry for the rig actually on screen -------------------------------------------
  const rigSpan = Math.max(0.35, activeRig.foot_y - activeRig.head_top_y);
  const boxH = targetFigureH / rigSpan;
  const aspect = Math.max(
    0.35,
    Math.min(2.0, (activeRig.width || 768) / Math.max(1, activeRig.height || 1024)),
  );
  const boxW = boxH * aspect;

  const nativeFacing = nativeOf(activeRig);
  const travelDirection = Math.sign(activeClip.toX - activeClip.fromX);
  const partnerDirection =
    typeof partnerX === 'number' && Math.abs(partnerX - activeXNorm) > 0.005
      ? Math.sign(partnerX - activeXNorm)
      : 0;
  const flipX = desiredFacing !== nativeFacing ? -1 : 1;
  const nativeDir = nativeFacing === 'right' ? 1 : -1;
  const fallbackWorldDirection =
    travelDirection || partnerDirection || (desiredFacing === 'right' ? 1 : -1);
  const fallbackLocalForward = fallbackWorldDirection * flipX;

  const enter = spring({frame, fps, config: {damping: 20, stiffness: 105}});
  const angles = evaluateTimelinePose(timeline, activeClip, {
    timelineFrame,
    fps,
    speaking,
    cameraMoving,
    rig: activeRig,
    boxW,
    boxH,
    targetFigureH,
    screenWidth: width,
    nativeDir,
    flipX,
    fallbackLocalForward,
    index,
  });
  const blinkClosure = computeBlinkClosure(name || `actor-${index}`, timelineFrame, fps);

  // ---- 2.5D head yaw / gaze toward partner, console or rover ----------------------------------
  const gazeTargetX =
    typeof activeClip.targetX === 'number' && Number.isFinite(activeClip.targetX)
      ? activeClip.targetX
      : partnerX;
  let gazeLocal = 0;
  let gazeStrength = 0;
  if (typeof gazeTargetX === 'number') {
    const dx = gazeTargetX - activeXNorm;
    if (Math.abs(dx) > 0.015) {
      gazeLocal = Math.sign(dx) * flipX;
      gazeStrength = smoothstep(Math.abs(dx) / 0.12);
    }
  }
  const neckShiftPx = 2.2 * gazeLocal * gazeStrength;
  const gazeTiltDeg = 1.8 * gazeLocal * gazeStrength;

  const left = worldX - activeRig.anchor_x * boxW;
  const top = floorPxY - activeRig.foot_y * boxH;
  const shadowW = boxW * 0.44;
  const shadowH = Math.max(10, height * 0.026);
  const shadowScale = Math.max(0.75, approachScale * (1 + angles.bobY / 60));

  const leadSuffix = nativeFacing === 'right' ? 'r' : 'l';
  const backSuffix = nativeFacing === 'right' ? 'l' : 'r';

  const parts = activeRig.parts || {};
  const torsoPart = parts.torso || parts.body;
  const headPart = parts.head;
  const thighLength = (side: LegSide) => {
    const geometry = legGeometry(activeRig, side, boxW, boxH);
    return Math.max(0, geometry.knee.y - geometry.hip.y);
  };
  const legPose: Record<
    LegSide,
    {upper: number; lower: number; foot: number; lift: number; thigh: number}
  > = {
    l: {upper: angles.upperLegL, lower: angles.lowerLegL, foot: angles.footL, lift: angles.thighLiftL, thigh: thighLength('l')},
    r: {upper: angles.upperLegR, lower: angles.lowerLegR, foot: angles.footR, lift: angles.thighLiftR, thigh: thighLength('r')},
  };
  const fallbackHip = (side: LegSide): [number, number] => [
    activeRig.anchor_x + (side === 'l' ? -0.045 : 0.045),
    0.55,
  ];
  const fallbackKnee = (side: LegSide): [number, number] => [
    activeRig.anchor_x + (side === 'l' ? -0.045 : 0.045),
    0.76,
  ];
  const fallbackAnkle = (side: LegSide): [number, number] =>
    activeRig.ankles?.[side] ?? [fallbackKnee(side)[0], activeRig.foot_y - 0.05];

  // ---- Gesture hand overlay (point / wave / console / work) ------------------------------------
  const leadLowerPart = parts[`lower_arm_${leadSuffix}`];
  const leadUpperPart = parts[`upper_arm_${leadSuffix}`];
  const wrist = activeRig.wrists?.[leadSuffix];
  const handKind = gestureHandKind(activeClip.type);
  const armDeviation = Math.abs(
    angles.leadUpperArm + angles.leadLowerArm - nativeDir * (ARM_REST_UPPER + ARM_REST_LOWER),
  );
  const showGestureHand = Boolean(
    handKind && activeRig.face && wrist && leadLowerPart && activeRig.level !== 'sprite' && armDeviation >= 12,
  );
  let handNode: React.ReactNode = null;
  let forearmClip: string | undefined;
  if (showGestureHand && handKind && wrist && activeRig.face && leadLowerPart) {
    const radiusPx = Math.max(6, (activeRig.wrists?.radius ?? 0.024) * boxW);
    const handSize = radiusPx * 5.2;
    const wristPx = {x: wrist[0] * boxW, y: wrist[1] * boxH};
    const elbowPivot = leadLowerPart.pivot;
    const elbowPx = {x: elbowPivot[0] * boxW, y: elbowPivot[1] * boxH};
    let ux = wristPx.x - elbowPx.x;
    let uy = wristPx.y - elbowPx.y;
    const len = Math.hypot(ux, uy);
    if (len > 4) {
      ux /= len;
      uy /= len;
    } else {
      ux = 0;
      uy = 1;
    }
    // Forearm bitmap direction -> hand rotation (fingers continue along the forearm axis).
    const axisDeg = (Math.atan2(-ux, uy) * 180) / Math.PI;
    const extraDeg = handKind === 'console' ? -18 * nativeDir : 0;
    // Remove the clenched rest fist beyond the cuff: keep the half-plane behind the wrist.
    const cutX = wristPx.x + ux * radiusPx * 0.35;
    const cutY = wristPx.y + uy * radiusPx * 0.35;
    const vx = -uy;
    const vy = ux;
    const L = (boxW + boxH) * 2;
    const pts = [
      [cutX + vx * L, cutY + vy * L],
      [cutX - vx * L, cutY - vy * L],
      [cutX - vx * L - ux * L, cutY - vy * L - uy * L],
      [cutX + vx * L - ux * L, cutY + vy * L - uy * L],
    ];
    forearmClip = `polygon(${pts.map(p => `${p[0].toFixed(1)}px ${p[1].toFixed(1)}px`).join(', ')})`;
    handNode = (
      <AnimeGestureHand
        kind={handKind}
        face={activeRig.face}
        left={wristPx.x - handSize / 2}
        top={wristPx.y - handSize * 1.2 * 0.1167}
        size={handSize}
        rotateDeg={axisDeg + extraDeg}
        mirror={nativeDir}
      />
    );
  }

  const showBody = renderPass !== 'arms_over_desk';
  const showArmsPass = renderPass !== 'body_behind_desk';
  if (!showBody && activeRig.level === 'sprite') return null;

  const tintId = `puppet-tint-${index}-${renderPass}`;
  const tintFilter = ambientTint ? `url(#${tintId})` : '';
  const dropShadow = showBody ? 'drop-shadow(0 6px 8px rgba(6, 12, 22, 0.28))' : '';
  const rootFilter = [tintFilter, dropShadow].filter(Boolean).join(' ') || undefined;

  return (
    <div style={{position: 'absolute', inset: 0, pointerEvents: 'none'}}>
      {ambientTint && (
        <svg width={0} height={0} style={{position: 'absolute'}} aria-hidden>
          <defs>
            <filter id={tintId} colorInterpolationFilters="sRGB" x="-5%" y="-5%" width="110%" height="110%">
              <feFlood floodColor={ambientTint} floodOpacity={0.16} result="tint" />
              <feBlend in="tint" in2="SourceGraphic" mode="multiply" result="blend" />
              <feComposite in="blend" in2="SourceGraphic" operator="in" />
            </filter>
          </defs>
        </svg>
      )}
      {/* Grounded contact shadow on the location plate floor */}
      {showBody && (
        <div
          style={{
            position: 'absolute',
            left: worldX - shadowW * 0.5,
            top: floorPxY - shadowH * 0.52,
            width: shadowW,
            height: shadowH,
            borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(6, 12, 22, 0.44) 0%, rgba(6, 12, 22, 0.16) 52%, rgba(6, 12, 22, 0) 76%)',
            transform: `scale(${shadowScale})`,
            filter: 'blur(2px)',
          }}
        />
      )}

      {/* Character rig root box aligned so (anchor_x, foot_y) lands on (worldX, floorPxY) */}
      <div
        style={{
          position: 'absolute',
          left,
          top,
          width: boxW,
          height: boxH,
          transformOrigin: `${activeRig.anchor_x * 100}% ${activeRig.foot_y * 100}%`,
          transform: `translateY(${(phaseContinuation ? 0 : (1 - enter) * 4) + angles.bobY}px) scale(${approachScale}) scaleX(${flipX * turnScaleX})`,
          filter: rootFilter,
        }}
      >
        {activeRig.level === 'sprite' ? (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              transformOrigin: `${activeRig.anchor_x * 100}% ${activeRig.foot_y * 100}%`,
              transform: `translateY(${turnDip}px) rotate(${angles.torsoRot * 0.6}deg) scaleY(${angles.breathScaleY})`,
            }}
          >
            {torsoPart && <Img src={staticFile(torsoPart.src)} style={partImgStyle} />}
            {activeRig.face && <BlinkOverlay face={activeRig.face} closure={blinkClosure} />}
            {activeRig.face && <VisemeMouth face={activeRig.face} cue={mouthCue} speaking={speaking} />}
          </div>
        ) : (
          <>
            {/* Full-rig hip -> knee leg chains */}
            {showBody && activeRig.level === 'full' && (
              <>
                <LimbChain
                  upper={parts[`upper_leg_${backSuffix}`]}
                  lower={parts[`lower_leg_${backSuffix}`]}
                  foot={parts[`foot_${backSuffix}`]}
                  upperDeg={legPose[backSuffix].upper}
                  lowerDeg={legPose[backSuffix].lower}
                  footDeg={legPose[backSuffix].foot}
                  liftPx={legPose[backSuffix].lift}
                  upperLengthPx={legPose[backSuffix].thigh}
                  fallbackUpper={fallbackHip(backSuffix)}
                  fallbackLower={fallbackKnee(backSuffix)}
                  fallbackFoot={fallbackAnkle(backSuffix)}
                />
                <LimbChain
                  upper={parts[`upper_leg_${leadSuffix}`]}
                  lower={parts[`lower_leg_${leadSuffix}`]}
                  foot={parts[`foot_${leadSuffix}`]}
                  upperDeg={legPose[leadSuffix].upper}
                  lowerDeg={legPose[leadSuffix].lower}
                  footDeg={legPose[leadSuffix].foot}
                  liftPx={legPose[leadSuffix].lift}
                  upperLengthPx={legPose[leadSuffix].thigh}
                  fallbackUpper={fallbackHip(leadSuffix)}
                  fallbackLower={fallbackKnee(leadSuffix)}
                  fallbackFoot={fallbackAnkle(leadSuffix)}
                />
              </>
            )}

            {/* Torso group carries back arm, torso/body, head (with blink + viseme), and front arm */}
            <div
              style={{
                position: 'absolute',
                inset: 0,
                transformOrigin: pivotOrigin(torsoPart, [activeRig.anchor_x, 0.54]),
                transform: `translateY(${turnDip}px) rotate(${angles.torsoRot}deg) scaleY(${angles.breathScaleY})`,
              }}
            >
              {showBody && (
                <LimbChain
                  upper={parts[`upper_arm_${backSuffix}`]}
                  lower={parts[`lower_arm_${backSuffix}`]}
                  upperDeg={angles.backUpperArm}
                  lowerDeg={angles.backLowerArm}
                  fallbackUpper={[activeRig.anchor_x - 0.1, 0.24]}
                  fallbackLower={[activeRig.anchor_x - 0.14, 0.38]}
                />
              )}

              {/* Lead upper arm sits just behind the vest/jacket seam so the round shoulder ball joint stays tucked cleanly under the vest! */}
              {showBody && (
                <LimbChain
                  upper={leadUpperPart}
                  lower={undefined}
                  upperDeg={angles.leadUpperArm}
                  lowerDeg={0}
                  fallbackUpper={[activeRig.anchor_x + 0.1, 0.24]}
                  fallbackLower={[activeRig.anchor_x + 0.14, 0.38]}
                />
              )}

              {showBody && torsoPart && <Img src={staticFile(torsoPart.src)} style={partImgStyle} />}

              {showBody && headPart && (
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    transformOrigin: pivotOrigin(headPart, [activeRig.anchor_x, 0.18]),
                    transform: `translateX(${neckShiftPx}px) rotate(${angles.headRot + gazeTiltDeg}deg)`,
                  }}
                >
                  <Img src={staticFile(headPart.src)} style={partImgStyle} />
                  {activeRig.face && <BlinkOverlay face={activeRig.face} closure={blinkClosure} />}
                  {activeRig.face && <VisemeMouth face={activeRig.face} cue={mouthCue} speaking={speaking} />}
                </div>
              )}

              {/* Lead forearm + hand renders in front of torso/head so gestures (point/wave/work/carry/talk) cross over the body naturally! */}
              {showArmsPass && leadLowerPart && (
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    transformOrigin: pivotOrigin(leadUpperPart, [activeRig.anchor_x + 0.1, 0.24]),
                    transform: `rotate(${angles.leadUpperArm}deg)`,
                  }}
                >
                  <div
                    style={{
                      position: 'absolute',
                      inset: 0,
                      transformOrigin: pivotOrigin(leadLowerPart, [activeRig.anchor_x + 0.14, 0.38]),
                      transform: `rotate(${angles.leadLowerArm}deg)`,
                    }}
                  >
                    {/* Gesture hand sits under the sleeve cuff so the retained forearm overlaps its base */}
                    {handNode}
                    <Img
                      src={staticFile(leadLowerPart.src)}
                      style={forearmClip ? {...partImgStyle, clipPath: forearmClip} : partImgStyle}
                    />
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
};
