/** Pure turn, hand, stride, and gaze helpers shared by the puppet and its tests. */

export const TURN_EDGE_SCALE_X = 0.16;
export const SIDE_STRIDE_DEG = 6;
export const SIDE_SHOE_LIFT_MAX_PX = 6;

export type TurnStage = 'none' | 'planted-side' | 'edge' | 'front';
export type HandKind = 'fist' | 'point' | 'wave' | 'desk';
export type HandRoles = {lead: HandKind; back: HandKind};

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));
const smoothstep = (value: number) => {
  const p = clamp01(value);
  return p * p * (3 - 2 * p);
};

/**
 * Eight frames around a side -> three-quarter swap.
 * Frames boundary-5..boundary-1 stay on the feet-together side.
 * Frame boundary is the thin edge (side squeezed over the front).
 * Frames boundary+1..boundary+2 are the front drawing.
 */
export function resolveSideToFrontTurn(
  frame: number,
  boundary: number,
  fromSide: boolean,
  toFront: boolean,
): TurnStage {
  if (!fromSide || !toFront) return 'none';
  const local = frame - (boundary - 5);
  if (local < 0 || local > 7) return 'none';
  if (local <= 4) return 'planted-side';
  if (local === 5) return 'edge';
  return 'front';
}

export function feetPlantedForTurn(stage: TurnStage) {
  return stage === 'planted-side';
}

/** Shoe lift for a feet-together side drawing. Never stacks a 12.5° split on top. */
export function sideShoeLiftPx(targetFigureH: number, carry = 1) {
  const carryScale = Number.isFinite(carry) && carry > 0 ? carry : 1;
  return Math.min(SIDE_SHOE_LIFT_MAX_PX, Math.max(0, targetFigureH) * 0.012) * carryScale;
}

export function sideStrideDegrees(envelope: number, carry = 1) {
  const carryScale = Number.isFinite(carry) && carry > 0 ? carry : 1;
  return SIDE_STRIDE_DEG * carryScale * clamp01(envelope);
}

export function handModesForClip(type: string): HandRoles {
  if (type === 'point') return {lead: 'point', back: 'fist'};
  if (type === 'wave') return {lead: 'wave', back: 'fist'};
  if (type === 'console' || type === 'work') return {lead: 'desk', back: 'fist'};
  return {lead: 'fist', back: 'fist'};
}

export function handSizeForForearm(forearmPx: number, kind: HandKind) {
  const length = Math.max(16, forearmPx);
  if (kind === 'point') return length * 0.62;
  if (kind === 'wave') return length * 0.58;
  return length * 0.48;
}

/** CSS clockwise degrees that aim local +y from origin toward target. */
export function aimDegrees(originX: number, originY: number, targetX: number, targetY: number) {
  const dx = targetX - originX;
  const dy = targetY - originY;
  return (Math.atan2(-dx, dy) * 180) / Math.PI;
}

export type GazeAddress = {neckPx: number; headDeg: number; torsoDeg: number; nodDeg: number};

const ZERO_GAZE: GazeAddress = {neckPx: 0, headDeg: 0, torsoDeg: 0, nodDeg: 0};

/**
 * Lateral address plus a downward nod.
 * A rover standing in front (similar x) is still a target: the 0.04 band must not
 * zero that gaze. `hasTarget` false is the only "nothing to look at" case.
 */
export function gazeAddress(dx: number, flipX: number, hasTarget = false): GazeAddress {
  const lateral = Number.isFinite(dx) ? dx : 0;
  if (Math.abs(lateral) <= 0.04) {
    if (!hasTarget) return ZERO_GAZE;
    return {neckPx: 0, headDeg: 0, torsoDeg: 3.2, nodDeg: -32};
  }
  // Nearby targets (the parked rover just beside her) already turn the head.
  // A rover driving farther away keeps adding yaw instead of sitting on the cap.
  const signed = Math.max(-1, Math.min(1, lateral / 0.9));
  // Head rotation sits inside the figure's flip, so the screen-space lean
  // toward the target is the opposite of the unflipped CSS sign.
  const sign = (signed < 0 ? 1 : -1) * (flipX < 0 ? -1 : 1);
  const aimed = Math.min(1, 0.38 + Math.abs(signed) * 0.9);
  return {
    neckPx: 16 * sign * aimed,
    headDeg: 42 * sign * aimed,
    torsoDeg: 6 * sign * aimed,
    nodDeg: -22,
  };
}

type PivotPart = {pivot?: [number, number]};

export function shoulderNorm(rig: {
  parts?: Record<string, PivotPart>;
  view_angle?: string;
}) {
  const left = rig.parts?.upper_arm_l?.pivot?.[0];
  const right = rig.parts?.upper_arm_r?.pivot?.[0];
  if (typeof left === 'number' && typeof right === 'number' && Math.abs(right - left) > 0.02) {
    return Math.abs(right - left);
  }
  return rig.view_angle === 'side' ? 0.11 : 0.2;
}

/** Shrink a drawing that is wider at the shoulders than the reference. Never stretch a profile. */
export function shoulderLockScale(activeShoulder: number, referenceShoulder: number) {
  if (!(activeShoulder > 0) || !(referenceShoulder > 0)) return 1;
  const scale = referenceShoulder / activeShoulder;
  if (scale >= 1) return 1;
  return Math.max(0.84, scale);
}

export function lockedFigureBox(args: {
  master: {width: number; height: number; foot_y: number; head_top_y: number};
  active: {width: number; height: number; foot_y: number; head_top_y: number};
  targetFigureH: number;
}) {
  const masterSpan = Math.max(0.35, args.master.foot_y - args.master.head_top_y);
  const activeSpan = Math.max(0.35, args.active.foot_y - args.active.head_top_y);
  const boxH = args.targetFigureH / masterSpan;
  const aspect = Math.max(0.35, Math.min(2, (args.master.width || 768) / Math.max(1, args.master.height || 1024)));
  const boxW = boxH * aspect;
  return {
    boxW,
    boxH,
    heightScale: masterSpan / activeSpan,
    figureH: args.targetFigureH,
  };
}

export type TravelFacing = 'left' | 'right';

export type FacingClip = {
  type: string;
  fromX: number;
  toX: number;
  facing?: TravelFacing;
  pose?: 'side' | 'three_quarter';
  targetX?: number;
};

export function facingAfterTravel(x: number, lookX: number | undefined, travelFacing: TravelFacing): TravelFacing {
  if (lookX === undefined || !Number.isFinite(lookX)) return travelFacing;
  // A rover a few percent away is still the thing she turned to face.
  if (Math.abs(lookX - x) < 0.001) return travelFacing;
  return lookX < x ? 'left' : 'right';
}

/** After a walk, stationary beats look at the partner or the rover instead of the camera. */
export function faceClipsAfterWalk<T extends FacingClip>(clips: T[], lookX: number | undefined): T[] {
  let seenTravel = false;
  return clips.map(clip => {
    const travel = Math.abs(clip.toX - clip.fromX) > 0.005;
    const moving = clip.type === 'walk' || (clip.type === 'carry' && travel);
    if (moving) {
      seenTravel = true;
      return clip;
    }
    if (!seenTravel) return clip;
    // The planted profile after the walk stays a side drawing until the turn.
    if (clip.pose === 'side') return clip;
    if (clip.type === 'point' || clip.type === 'console' || clip.type === 'work' || clip.type === 'carry') {
      return clip;
    }
    const facing = facingAfterTravel(clip.fromX, lookX, clip.facing ?? 'right');
    return {
      ...clip,
      facing,
      pose: 'three_quarter' as const,
      targetX: clip.targetX ?? lookX,
    };
  });
}

export function cssTint(value: string | [number, number, number] | undefined) {
  if (!value) return undefined;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed || undefined;
  }
  if (value.length >= 3 && value.slice(0, 3).every(channel => Number.isFinite(channel))) {
    return `rgb(${Math.round(value[0])}, ${Math.round(value[1])}, ${Math.round(value[2])})`;
  }
  return undefined;
}

/** Room ambient mixed toward white, then multiplied. A tint, not a full relight. */
export function ambientMultiplyColor(value: string | [number, number, number] | undefined) {
  if (!value) return undefined;
  if (typeof value === 'string') return cssTint(value);
  if (value.length < 3 || !value.slice(0, 3).every(channel => Number.isFinite(channel))) return undefined;
  const mix = (channel: number) => Math.round(channel * 0.42 + 255 * 0.58);
  return `rgb(${mix(value[0])}, ${mix(value[1])}, ${mix(value[2])})`;
}

export function parseRgb(value: string | [number, number, number] | undefined): [number, number, number] | undefined {
  if (!value) return undefined;
  if (typeof value !== 'string') {
    if (value.length >= 3 && value.slice(0, 3).every(channel => Number.isFinite(channel))) {
      return [value[0], value[1], value[2]];
    }
    return undefined;
  }
  const match = value.match(/(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)/);
  if (!match) return undefined;
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

export function contactShadowBackground(floor: string | [number, number, number] | undefined) {
  const rgb = parseRgb(floor);
  if (!rgb) {
    return 'radial-gradient(ellipse, rgba(6, 12, 22, 0.44) 0%, rgba(6, 12, 22, 0.16) 52%, rgba(6, 12, 22, 0) 76%)';
  }
  const shade = rgb.map(channel => Math.round(channel * 0.38));
  return `radial-gradient(ellipse, rgba(${shade[0]}, ${shade[1]}, ${shade[2]}, 0.62) 0%, rgba(${shade[0]}, ${shade[1]}, ${shade[2]}, 0.22) 54%, rgba(${shade[0]}, ${shade[1]}, ${shade[2]}, 0) 78%)`;
}

export function forearmClipPolygon(
  wrist: {x: number; y: number},
  elbow: {x: number; y: number},
  radiusPx: number,
  boxW: number,
  boxH: number,
) {
  let ux = wrist.x - elbow.x;
  let uy = wrist.y - elbow.y;
  const len = Math.hypot(ux, uy);
  if (len > 4) {
    ux /= len;
    uy /= len;
  } else {
    ux = 0;
    uy = 1;
  }
  const cutX = wrist.x + ux * radiusPx * 0.35;
  const cutY = wrist.y + uy * radiusPx * 0.35;
  const vx = -uy;
  const vy = ux;
  const span = (boxW + boxH) * 2;
  const pts = [
    [cutX + vx * span, cutY + vy * span],
    [cutX - vx * span, cutY - vy * span],
    [cutX - vx * span - ux * span, cutY - vy * span - uy * span],
    [cutX + vx * span - ux * span, cutY + vy * span - uy * span],
  ];
  return `polygon(${pts.map(point => `${point[0].toFixed(1)}px ${point[1].toFixed(1)}px`).join(', ')})`;
}

export type ScreenGlass = {x: number; y: number; w: number; h: number};

/**
 * Pull a detected monitor in from its edges. The right side of this plate's
 * detection runs onto the orange pillar, so that edge steps back further.
 * No second bezel — this is only the glass the overlay is allowed to use.
 */
export function insetScreenGlass(rect: ScreenGlass): ScreenGlass {
  const left = rect.w * 0.05;
  const right = rect.w * 0.22;
  const yPad = rect.h * 0.08;
  return {
    x: rect.x + left,
    y: rect.y + yPad,
    w: Math.max(0.02, rect.w - left - right),
    h: Math.max(0.02, rect.h - yPad * 2),
  };
}

/** Wall-screen aim. Y is the painted glass center, not a fixed fraction of the frame. */
export function pointTarget(
  targetX: number | undefined,
  actorX: number,
  width: number,
  height: number,
  screen?: ScreenGlass,
) {
  if (screen && screen.w > 0 && screen.h > 0 && Number.isFinite(screen.x) && Number.isFinite(screen.y)) {
    return {
      x: (screen.x + screen.w / 2) * width,
      y: (screen.y + screen.h / 2) * height,
    };
  }
  const xNorm = typeof targetX === 'number' && Number.isFinite(targetX) ? targetX : actorX;
  return {x: xNorm * width, y: height * 0.42};
}

/**
 * Extra upward degrees for the upper arm when the aim point sits above the shoulder.
 * Zero when the target is at or below the shoulder.
 */
export function pointArmLiftDegrees(shoulderY: number, targetY: number) {
  if (!Number.isFinite(shoulderY) || !Number.isFinite(targetY)) return 0;
  const above = shoulderY - targetY;
  if (above <= 2) return 0;
  const rise = Math.min(1, above / 150);
  return 64 + 28 * rise;
}

/**
 * Clockwise degrees that swing a baked stride ankle onto `togetherX`.
 * Feet-together is the motion zero; a walk cycles around this, not around the split.
 */
export function sideTogetherDegrees(
  hipX: number,
  hipY: number,
  ankleX: number,
  ankleY: number,
  togetherX = hipX,
) {
  const dx = ankleX - hipX;
  const dy = ankleY - hipY;
  const radius = Math.hypot(dx, dy);
  if (!(radius > 1e-4)) return 0;
  const target = Math.max(-radius + 1e-4, Math.min(radius - 1e-4, togetherX - hipX));
  const alpha = Math.atan2(dx, dy);
  const beta = Math.asin(target / radius);
  return ((alpha - beta) * 180) / Math.PI;
}

/**
 * Plate pixels and the puppet stage are scaled by different amounts around the same
 * origin. This puts a plate rectangle into stage-local space so that, after the stage
 * scale, it lands on the painted glass instead of sliding onto the pillar.
 */
export function stageRectForPlate(
  rect: ScreenGlass,
  originX: number,
  originY: number,
  bgScale: number,
  stageScale: number,
  panX: number,
  panY: number,
  width: number,
  height: number,
): ScreenGlass {
  const safeStage = Math.abs(stageScale) > 1e-4 ? stageScale : 1;
  const ox = originX * width;
  const oy = originY * height;
  const map = (px: number, py: number) => {
    const screenX = ox + (px - ox) * bgScale + panX * 0.4;
    const screenY = oy + (py - oy) * bgScale + panY * 0.4;
    return {
      x: ox + (screenX - ox - panX) / safeStage,
      y: oy + (screenY - oy - panY) / safeStage,
    };
  };
  const a = map(rect.x * width, rect.y * height);
  const b = map((rect.x + rect.w) * width, (rect.y + rect.h) * height);
  return {
    x: a.x / width,
    y: a.y / height,
    w: (b.x - a.x) / width,
    h: (b.y - a.y) / height,
  };
}

/** Half-width of the parked rover body on a 1280-wide stage, plus a foot clearance. */
export const ROVER_BODY_HALF = 0.15;

/** Move a stop mark that lands inside the rover out to the nearer open floor beside it. */
export function markBesideRover(startX: number, endX: number, roverX: number | undefined) {
  if (roverX === undefined || !Number.isFinite(roverX) || !Number.isFinite(endX)) return endX;
  const margin = 0.06;
  const leftEdge = roverX - ROVER_BODY_HALF;
  const rightEdge = roverX + ROVER_BODY_HALF;
  if (endX <= leftEdge - 0.015 || endX >= rightEdge + 0.015) return endX;
  const leftSpot = Math.max(0.06, leftEdge - margin);
  const rightSpot = Math.min(0.94, rightEdge + margin);
  return Math.abs(endX - rightSpot) <= Math.abs(endX - leftSpot) ? rightSpot : leftSpot;
}
