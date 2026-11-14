import React from 'react';
import {AbsoluteFill, Img, interpolate, random, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {
  DEPTH_STAGE,
  Puppet,
  type CharacterRig,
  type PuppetAction,
  type PuppetClip,
  type PuppetDepth,
  type PuppetFacing,
} from './puppet';
import {RobotPuppet, type RobotRig, type RoverAction} from './robot';
import {compileShotChoreography, endMarks, type ShotChoreography} from './motion_director';
import {
  ConsoleDeskBody,
  ConsoleHoloOverlay,
  TelemetryScreenProp,
  WorkbenchProp,
  type StagePropKind,
  type StageRgb,
} from './stage_props';

export type StoryAction = PuppetAction;
export type StoryFraming = 'wide' | 'medium' | 'close';
export type StoryFocus = 'actor' | 'robot' | 'prop' | 'wide';

export type StoryActor = {
  name: string;
  character_id?: string;
  action: StoryAction;
  from_x: number;
  to_x: number;
  facing?: PuppetFacing;
  depth?: PuppetDepth;
  rig?: CharacterRig;
  side_rig?: CharacterRig;
  stature_scale?: number;
  clips?: PuppetClip[];
};

export type StoryPlate = {
  src: string;
  far?: string;
  near?: string;
};

export type StorySetProp = {
  kind: StagePropKind;
  prop_src?: string;
  x?: number;
  y?: number;
  floor_y?: number;
  depth?: PuppetDepth;
  active?: boolean;
  interactive_state?: string | boolean;
  occlude_waist?: boolean;
};

export type StoryMotion = {
  actors: StoryActor[];
  camera: string;
  focus: StoryFocus;
  framing?: StoryFraming;
  robot_action?: RoverAction;
  robot_rig?: RobotRig;
  set_prop?: StorySetProp;
};

export type PlateMetrics = {
  horizon_y?: number;
  wall_base_y?: number;
  far_y?: number;
  mid_y?: number;
  near_y?: number;
  ref_mid_figure_height?: number;
  ambient_rgb?: number[];
  floor_rgb?: number[];
};

type Cue = {from: number; to: number; value: string; speaker: string; scene_number: number};
type StoryLayers = {
  background: string;
  body: string;
  body2?: string;
  subject_x: number;
  subject_y: number;
  subject2_x?: number;
  subject2_y?: number;
};

type Shot = {
  from: number;
  duration: number;
  index: number;
  src: string;
  label?: string;
  scene_number?: number;
  plate?: StoryPlate;
  plate_metrics?: PlateMetrics;
  story_motion: StoryMotion;
  story_motion_phase?: number;
  layers?: StoryLayers;
  rover?: {action: RoverAction; speaker: string; rig?: RobotRig};
};

const font = '"Noto Sans CJK SC", "Noto Sans", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const clampNumber = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const eased = (frame: number, duration: number) => {
  const p = Math.max(0, Math.min(1, frame / Math.max(1, duration)));
  return p * p * (3 - 2 * p);
};
const mix = (p: number, a: number, b: number) => interpolate(p, [0, 1], [a, b], clamp);

type MetricPuppetRenderPass = 'full' | 'body_behind_desk' | 'arms_over_desk';
type MetricPuppetProps = Omit<
  React.ComponentProps<typeof Puppet>,
  | 'sideRig'
  | 'statureScale'
  | 'figureHeightRatioOverride'
  | 'floorYOverride'
  | 'renderPass'
  | 'ambientTint'
  | 'propTargetX'
> & {
  sideRig?: CharacterRig;
  statureScale?: number;
  figureHeightRatioOverride?: number;
  floorYOverride?: number;
  renderPass?: MetricPuppetRenderPass;
  ambientTint?: StageRgb;
  propTargetX?: number;
};
const MetricPuppet = Puppet as React.ComponentType<MetricPuppetProps>;

type MetricRobotProps = Omit<
  React.ComponentProps<typeof RobotPuppet>,
  'floorY' | 'sceneWidth' | 'sceneHeight' | 'targetRoverHeightPx' | 'ambientTint'
> & {
  floorY?: number;
  sceneWidth?: number;
  sceneHeight?: number;
  targetRoverHeightPx?: number;
  ambientTint?: StageRgb;
};
const MetricRobotPuppet = RobotPuppet as React.ComponentType<MetricRobotProps>;

type ResolvedMetrics = {
  horizonY: number;
  wallBaseY: number;
  farY: number;
  midY: number;
  nearY: number;
  refMidFigureHeight: number;
  ambientRgb: StageRgb;
  floorRgb: StageRgb;
};

type ResolvedSetProp = {
  kind: StagePropKind;
  propSrc?: string;
  x: number;
  screenY?: number;
  floorY: number;
  depth: PuppetDepth;
  occludeWaist: boolean;
  active: boolean;
  operatorIndex?: number;
  activation?: 'point' | 'busy' | 'always';
};

const finiteOr = (value: number | undefined, fallback: number) =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

const metricRgb = (value: number[] | undefined, fallback: StageRgb): StageRgb =>
  Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every(Number.isFinite)
    ? [
        clampNumber(Math.round(value[0]), 0, 255),
        clampNumber(Math.round(value[1]), 0, 255),
        clampNumber(Math.round(value[2]), 0, 255),
      ]
    : fallback;

const resolvePlateMetrics = (raw?: PlateMetrics): ResolvedMetrics => {
  const horizonY = finiteOr(raw?.horizon_y, 0.46);
  const wallBaseY = finiteOr(raw?.wall_base_y, 0.75);
  return {
    horizonY,
    wallBaseY,
    farY: finiteOr(raw?.far_y, clampNumber(wallBaseY + 0.05, 0.77, 0.83)),
    midY: finiteOr(raw?.mid_y, clampNumber(wallBaseY + 0.13, 0.86, 0.91)),
    nearY: finiteOr(raw?.near_y, 0.95),
    refMidFigureHeight: finiteOr(raw?.ref_mid_figure_height, 0.56),
    ambientRgb: metricRgb(raw?.ambient_rgb, [168, 188, 214]),
    floorRgb: metricRgb(raw?.floor_rgb, [196, 204, 210]),
  };
};

const perspectiveScale = (floorY: number, metrics: ResolvedMetrics) =>
  clampNumber(
    (floorY - metrics.horizonY) / Math.max(0.001, metrics.midY - metrics.horizonY),
    0.68,
    1.22,
  );

const floorForDepth = (depth: PuppetDepth, metrics: ResolvedMetrics) =>
  depth === 'far' ? metrics.farY : depth === 'near' ? metrics.nearY : metrics.midY;

const statureFor = (actor: StoryActor) =>
  clampNumber(
    finiteOr(actor.stature_scale, /林岚|Lin\s*Lan/i.test(actor.name) ? 0.93 : 1),
    0.82,
    1.12,
  );

const humanHeightRatioAt = (floorY: number, stature: number, metrics: ResolvedMetrics) =>
  metrics.refMidFigureHeight * perspectiveScale(floorY, metrics) * stature;

/** Legacy whole-body acting for pre-puppet full-scene cutout shots. */
const legacyAct = (
  action: StoryAction,
  frame: number,
  fps: number,
  duration: number,
  fromX: number,
  toX: number,
  speaking: boolean,
  index: number,
  width: number,
) => {
  const enter = spring({frame, fps, config: {damping: 18, stiffness: 100}});
  const breath = 1 + (0.5 + 0.5 * Math.sin(frame / (fps * 0.9) + index)) * 0.004;
  const sway = Math.sin(frame / (fps * 1.7) + index) * 1.15;
  let x = 0;
  let y = (1 - enter) * 30;
  let rot = sway;
  let lean = 0;
  if (action === 'walk') {
    x = (toX - fromX) * eased(frame, duration) * width;
    y += Math.abs(Math.sin((frame / fps) * 8)) * 7;
    rot = sway * 0.35;
  } else if (action === 'react') {
    const damp = Math.exp(-frame / (fps * 0.55));
    x = Math.sin((frame / fps) * 16) * 8 * damp;
    const back = spring({frame, fps, config: {damping: 8, stiffness: 140, mass: 0.65}});
    lean = (1 - back) * 9;
    rot = sway * 0.2;
  } else if (action === 'point' || action === 'work' || action === 'wave') {
    const pulse = 0.5 + 0.5 * Math.sin((frame / fps) * (action === 'work' ? 5.5 : 3.4));
    lean = -7 * pulse;
    rot = sway * 0.25;
  } else if (action === 'carry') {
    y += Math.abs(Math.sin(frame / (fps * 0.85) + index)) * 4;
  } else if (action === 'console') {
    const pulse = 0.5 + 0.5 * Math.sin((frame / fps) * 2.6);
    lean = -4 * pulse;
    rot = sway * 0.5;
  } else if (action === 'talk' && speaking) {
    y += Math.abs(Math.sin(frame / (fps * 0.42))) * 5;
  }
  return {x, y, rot, lean, scaleY: breath};
};

const findSpeakerCue = (
  speech: Cue[],
  sceneNumber: number | undefined,
  speakerName: string,
  globalFrame: number,
): Cue | undefined => {
  if (!speakerName) return undefined;
  return (
    speech.find(
      cue =>
        cue.scene_number === sceneNumber &&
        cue.speaker === speakerName &&
        globalFrame >= cue.from &&
        globalFrame < cue.to,
    ) ||
    speech.find(
      cue =>
        cue.scene_number === sceneNumber &&
        cue.speaker &&
        (cue.speaker.includes(speakerName) || speakerName.includes(cue.speaker)) &&
        globalFrame >= cue.from &&
        globalFrame < cue.to,
    )
  );
};

const resolveFraming = (shot: Shot): StoryFraming => {
  const motion = shot.story_motion;
  const actors = motion.actors ?? [];
  if (motion.framing === 'wide' || motion.framing === 'medium' || motion.framing === 'close') return motion.framing;
  if (/特写|近景|close/i.test(motion.camera || '')) return 'close';
  return actors.length >= 2 || (actors.length >= 1 && (shot.rover || motion.robot_action)) ? 'wide' : 'medium';
};

const robotPlacement = (shot: Shot) => {
  const actors = shot.story_motion.actors ?? [];
  const avgActorX = actors.length > 0 ? actors.reduce((sum, a) => sum + a.from_x, 0) / actors.length : 0.5;
  const robotLeftPct = actors.length === 0 ? 28 : avgActorX <= 0.52 ? 48 : 8;
  return {avgActorX, robotLeftPct, robotCenterX: (robotLeftPct + 23) / 100};
};

const propKind = (value: unknown): StagePropKind | undefined => {
  if (value === 'console_desk' || value === 'workbench' || value === 'telemetry_screen') return value;
  if (value === 'telemetry_monitor' || value === 'telemetry_pillar') return 'telemetry_screen';
  return undefined;
};

const stateIsActive = (value: StorySetProp['interactive_state']) =>
  value === true || (typeof value === 'string' && /active|live|on|lock|work/i.test(value));

const resolveSetProp = ({
  shot,
  choreography,
  framing,
  robotAct,
  robotCenterX,
  metrics,
}: {
  shot: Shot;
  choreography: ShotChoreography;
  framing: StoryFraming;
  robotAct?: RoverAction;
  robotCenterX: number;
  metrics: ResolvedMetrics;
}): ResolvedSetProp | null => {
  const actors = shot.story_motion.actors ?? [];
  const plans = choreography.actors;
  const explicit = shot.story_motion.set_prop;
  if (explicit) {
    const kind = propKind(explicit.kind);
    if (kind) {
      const x = clampNumber(finiteOr(explicit.x, kind === 'telemetry_screen' ? 0.76 : 0.5), 0.08, 0.92);
      let operatorIndex = -1;
      let nearest = Number.POSITIVE_INFINITY;
      plans.forEach((plan, index) => {
        const distance = Math.abs(plan.toX - x);
        if (distance < nearest) {
          nearest = distance;
          operatorIndex = index;
        }
      });
      const operatorDepth = operatorIndex >= 0 ? plans[operatorIndex]?.depth : undefined;
      const depth =
        explicit.depth === 'far' || explicit.depth === 'near' || explicit.depth === 'mid'
          ? explicit.depth
          : operatorDepth ?? 'mid';
      const actorAction = operatorIndex >= 0 ? actors[operatorIndex]?.action : undefined;
      const activation =
        explicit.active || stateIsActive(explicit.interactive_state)
          ? 'always'
          : kind === 'telemetry_screen' || actorAction === 'point'
          ? 'point'
          : kind === 'console_desk' || kind === 'workbench'
          ? 'busy'
          : undefined;
      return {
        kind,
        propSrc: explicit.prop_src,
        x,
        screenY: finiteOr(explicit.y, 0.34),
        floorY: clampNumber(finiteOr(explicit.floor_y, floorForDepth(depth, metrics)), 0.62, 0.98),
        depth,
        occludeWaist: explicit.occlude_waist ?? kind !== 'telemetry_screen',
        active: Boolean(explicit.active || stateIsActive(explicit.interactive_state)),
        operatorIndex: operatorIndex >= 0 ? operatorIndex : undefined,
        activation,
      };
    }
  }

  const pointIndex = actors.findIndex(
    (actor, index) => actor.action === 'point' || plans[index]?.clips.some(clip => clip.type === 'point'),
  );
  if (pointIndex >= 0) {
    const actor = actors[pointIndex];
    const plan = plans[pointIndex];
    const actorX = plan?.toX ?? actor.to_x ?? actor.from_x;
    const facing =
      actor.facing ??
      (typeof plan?.partnerX === 'number' && Math.abs(plan.partnerX - actorX) > 0.04
        ? plan.partnerX < actorX
          ? 'left'
          : 'right'
        : actorX <= 0.5
        ? 'right'
        : 'left');
    const side = facing === 'right' ? 1 : -1;
    const roverOnPointedSide = Boolean(robotAct && (robotCenterX - actorX) * side > 0.04);
    if (!roverOnPointedSide) {
      const depth = plan?.depth ?? 'mid';
      return {
        kind: 'telemetry_screen',
        x: facing === 'right' ? 0.78 : 0.22,
        screenY: 0.34,
        floorY: floorForDepth(depth, metrics),
        depth: 'far',
        occludeWaist: false,
        active: false,
        operatorIndex: pointIndex,
        activation: 'point',
      };
    }
  }

  if (!robotAct) {
    const busyIndex = actors.findIndex((actor, index) => {
      const action = actor.action;
      return (
        action === 'console' ||
        action === 'work' ||
        plans[index]?.clips.some(clip => clip.type === 'console' || clip.type === 'work')
      );
    });
    if (busyIndex >= 0) {
      const actor = actors[busyIndex];
      const plan = plans[busyIndex];
      const depth = plan?.depth ?? actor.depth ?? 'mid';
      return {
        kind: actor.action === 'work' ? 'workbench' : 'console_desk',
        x: clampNumber(plan?.toX ?? actor.to_x ?? actor.from_x, 0.14, 0.86),
        floorY: floorForDepth(depth, metrics),
        depth,
        occludeWaist: true,
        active: false,
        operatorIndex: busyIndex,
        activation: 'busy',
      };
    }

    if (actors.length === 2 && framing === 'wide') {
      const deskX = 0.47;
      const occludeWaist = plans.some(plan => Math.abs(plan.toX - deskX) < 0.09);
      return {
        kind: 'console_desk',
        x: deskX,
        floorY: metrics.midY,
        depth: 'mid',
        occludeWaist,
        active: true,
        activation: 'always',
      };
    }
  }
  return null;
};

const planXAt = (
  plan: ShotChoreography['actors'][number],
  localFrame: number,
) => {
  const clip =
    plan.clips.find(item => localFrame >= item.startFrame && localFrame < item.endFrame) ??
    plan.clips[plan.clips.length - 1];
  if (!clip) return plan.toX;
  const moves =
    clip.type === 'walk' ||
    (clip.type === 'carry' && Math.abs(clip.toX - clip.fromX) > 0.000001);
  if (!moves) return clip.fromX;
  const progress = eased(localFrame - clip.startFrame, Math.max(1, clip.endFrame - clip.startFrame));
  return clip.fromX + (clip.toX - clip.fromX) * progress;
};

const propIsActive = (
  prop: ResolvedSetProp,
  choreography: ShotChoreography,
  localFrame: number,
) => {
  if (prop.active || prop.activation === 'always') return true;
  const plan =
    prop.operatorIndex === undefined ? undefined : choreography.actors[prop.operatorIndex];
  if (!plan) return false;
  const clip = plan.clips.find(item => localFrame >= item.startFrame && localFrame < item.endFrame);
  if (prop.activation === 'point') return clip?.type === 'point';
  if (prop.activation === 'busy') return clip?.type === 'console' || clip?.type === 'work';
  return false;
};

const propTargetForShot = (
  shot: Shot,
  framing: StoryFraming,
  robotAct: RoverAction | undefined,
  robotCenterX: number,
) => {
  const actors = shot.story_motion.actors ?? [];
  const explicit = shot.story_motion.set_prop;
  if (explicit && typeof explicit.x === 'number' && Number.isFinite(explicit.x)) {
    return clampNumber(explicit.x, 0.08, 0.92);
  }
  const pointActor = actors.find(actor => actor.action === 'point');
  if (pointActor) {
    const actorX = finiteOr(pointActor.to_x, pointActor.from_x);
    const facing = pointActor.facing ?? (actorX <= 0.5 ? 'right' : 'left');
    const side = facing === 'right' ? 1 : -1;
    if (!(robotAct && (robotCenterX - actorX) * side > 0.04)) {
      return facing === 'right' ? 0.78 : 0.22;
    }
    return robotCenterX;
  }
  if (!robotAct) {
    const busyActor = actors.find(actor => actor.action === 'console' || actor.action === 'work');
    if (busyActor) return clampNumber(finiteOr(busyActor.to_x, busyActor.from_x), 0.14, 0.86);
    if (actors.length === 2 && framing === 'wide') return 0.47;
  }
  return robotAct ? robotCenterX : undefined;
};

const isPhasePair = (first?: Shot, second?: Shot) =>
  Boolean(
    first &&
      second &&
      first.story_motion_phase === 0 &&
      second.story_motion_phase === 1 &&
      first.scene_number === second.scene_number,
  );

/** Compile the beat timeline for one shot; a phase-1 shot continues from phase 0's directed end marks. */
export const directStoryShot = (shot: Shot, speech: Cue[], fps: number, prevShot?: Shot): ShotChoreography => {
  const continuation = isPhasePair(prevShot, shot);
  const startX = continuation && prevShot ? endMarks(directStoryShot(prevShot, speech, fps)) : undefined;
  const robotAct = shot.rover?.action || shot.story_motion.robot_action;
  const framing = resolveFraming(shot);
  const placement = robotPlacement(shot);
  return compileShotChoreography({
    actors: shot.story_motion.actors ?? [],
    framing,
    camera: shot.story_motion.camera || '',
    robotAct,
    robotX: robotAct ? placement.robotCenterX : undefined,
    propTargetX: propTargetForShot(shot, framing, robotAct, placement.robotCenterX),
    shotFromGlobal: shot.from,
    totalFrames: shot.duration,
    fps,
    speechCues: speech.filter(cue => cue.scene_number === shot.scene_number),
    startX,
    phaseContinuation: continuation,
  });
};

export const StoryShot = ({
  shot,
  prevShot,
  nextShot,
  lead,
  width,
  height,
  speech,
  robotRig: globalRobotRig,
}: {
  shot: Shot;
  prevShot?: Shot;
  nextShot?: Shot;
  lead: number;
  width: number;
  height: number;
  speech: Cue[];
  robotRig?: RobotRig | null;
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const actors = shot.story_motion.actors ?? [];
  const hasPlate = Boolean(shot.plate?.src || shot.plate?.far);
  const hasPuppetRigs = actors.some(a => Boolean(a.rig));
  const useCutoutStage = hasPlate || hasPuppetRigs;

  const camera = shot.story_motion.camera || '';
  const phase = shot.story_motion_phase;
  const split = phase === 0 || phase === 1;
  const fade = lead > 0 ? interpolate(frame, [0, lead], [0, 1], clamp) : 1;
  const globalFrame = shot.from - Math.max(0, lead) + frame;
  const turnKey = split ? Number(shot.scene_number ?? shot.index) : shot.index;
  const direction = /左|left/i.test(camera) ? -1 : /右|right/i.test(camera) ? 1 : turnKey % 2 ? 1 : -1;
  const focus = shot.story_motion.focus || 'wide';
  const plateMetrics = resolvePlateMetrics(shot.plate_metrics);
  const ambientTint = plateMetrics.ambientRgb;
  const floorTint = plateMetrics.floorRgb;
  const ambientCss = (alpha: number) =>
    `rgba(${ambientTint[0]}, ${ambientTint[1]}, ${ambientTint[2]}, ${alpha})`;
  const floorCss = (alpha: number) =>
    `rgba(${floorTint[0]}, ${floorTint[1]}, ${floorTint[2]}, ${alpha})`;
  const grade =
    focus === 'robot'
      ? 'rgba(150, 186, 220, .18)'
      : focus === 'prop'
      ? 'rgba(214, 176, 132, .16)'
      : focus === 'actor'
      ? 'rgba(214, 176, 150, .14)'
      : 'rgba(168, 188, 214, .12)';
  const cover: React.CSSProperties = {width: '100%', height: '100%', objectFit: 'cover'};
  const resolvedRobotRig = shot.rover?.rig ?? shot.story_motion.robot_rig ?? globalRobotRig ?? null;

  const phasePrev = phase === 1 && isPhasePair(prevShot, shot) ? prevShot : undefined;
  const phaseNext = phase === 0 && isPhasePair(shot, nextShot) ? nextShot : undefined;
  const choreography = React.useMemo(
    () => (useCutoutStage ? directStoryShot(shot, speech, fps, phasePrev) : null),
    [useCutoutStage, shot, speech, fps, phasePrev],
  );
  // The sibling phase shares one camera move, so its staging decides restraint too.
  const siblingChoreography = React.useMemo(
    () =>
      !useCutoutStage
        ? null
        : phasePrev
        ? directStoryShot(phasePrev, speech, fps)
        : phaseNext
        ? directStoryShot(phaseNext, speech, fps, shot)
        : null,
    [useCutoutStage, phasePrev, phaseNext, shot, speech, fps],
  );

  // ---------------------------------------------------------------------------
  // 1. Cutout Puppet + Reusable Location Plate Path
  // ---------------------------------------------------------------------------
  if (useCutoutStage && choreography) {
    // lead = 0 (hard cut) -> localFrame = frame; legacy lead > 0 just offsets the dissolve-in.
    const localFrame = Math.max(0, frame - Math.max(0, lead));
    const totalDuration = shot.duration * (split ? 2 : 1);
    const sceneElapsed = phase === 1 ? shot.duration + localFrame : localFrame;
    const continuousP = eased(sceneElapsed, totalDuration);

    const push = /push|zoom in|推|近/i.test(camera);
    const pull = /pull|zoom out|拉远|拉出|后退/i.test(camera);
    const panWord = /pan|横移|平移|摇/i.test(camera);
    const mode: 'push' | 'pull' | 'pan' = pull && !push ? 'pull' : panWord && !push ? 'pan' : 'push';

    const framing = resolveFraming(shot);
    const robotAct = shot.rover?.action || shot.story_motion.robot_action;
    const robotSpeaker = shot.rover?.speaker || '阿拓 AT-1';
    const {avgActorX, robotLeftPct, robotCenterX} = robotPlacement(shot);

    // Camera restraint: never move the camera while feet or wheels move on the floor.
    const siblingShot = phasePrev ?? phaseNext;
    const lockedWord = /固定|静止|锁定|hold|static|locked/i.test(camera);
    const locomotion = choreography.hasLocomotion || Boolean(siblingChoreography?.hasLocomotion);
    const roverDriving =
      robotAct === 'drive' ||
      (siblingShot?.rover?.action || siblingShot?.story_motion.robot_action) === 'drive';
    const isCameraMoving = !(lockedWord || locomotion || roverDriving);

    // Fixed origin per shot so a held scale never slides the plate when the speaker changes.
    const plans = choreography.actors;
    const shotEnd = shot.from + shot.duration;
    const leadPlanIdx = actors.findIndex(actor =>
      speech.some(
        cue =>
          cue.scene_number === shot.scene_number &&
          cue.speaker === actor.name &&
          cue.value !== 'X' &&
          cue.value !== 'A' &&
          cue.to > shot.from &&
          cue.from < shotEnd,
      ),
    );
    const leadPlan = plans[leadPlanIdx >= 0 ? leadPlanIdx : 0];
    let rawFocusX = 0.5;
    let rawFocusY = 0.52;
    if (focus === 'robot' && robotAct) {
      rawFocusX = robotCenterX;
      rawFocusY = 0.64;
    } else if (leadPlan) {
      rawFocusX = actors.length >= 2 && framing === 'wide' ? 0.5 : leadPlan.toX;
      rawFocusY = framing === 'close' ? 0.24 : framing === 'medium' ? 0.42 : 0.54;
    }
    const originX = Math.min(0.82, Math.max(0.18, rawFocusX));
    const originY = Math.min(0.76, Math.max(0.20, rawFocusY));

    const heldStage = framing === 'close' ? 2.06 : framing === 'medium' ? 1.34 : 1.0;
    let bgScale = 1.05;
    let stageScale = heldStage;
    let panX = 0;
    let panY = 0;
    if (isCameraMoving) {
      // Gentle, tasteful push on stationary acting beats only.
      const stageEnd = framing === 'close' ? 2.14 : framing === 'medium' ? 1.4 : 1.03;
      const bgFrom = mode === 'pan' ? 1.06 : 1.04;
      const bgTo = mode === 'pan' ? 1.08 : 1.09;
      bgScale = mode === 'pull' ? mix(continuousP, bgTo, bgFrom) : mix(continuousP, bgFrom, bgTo);
      stageScale = mode === 'pull' ? mix(continuousP, stageEnd, heldStage) : mix(continuousP, heldStage, stageEnd);
      const maxPanX = width * (mode === 'pan' ? 0.01 : 0.006);
      const maxPanY = height * 0.005;
      panX = direction * mix(continuousP, -maxPanX, maxPanX);
      panY = mix(continuousP, maxPanY, -maxPanY);
    }

    const bgSrc = shot.plate?.far || shot.plate?.src || shot.src;
    const nearSrc = shot.plate?.near;

    const setProp = resolveSetProp({
      shot,
      choreography,
      framing,
      robotAct,
      robotCenterX,
      metrics: plateMetrics,
    });
    const setPropActive = setProp ? propIsActive(setProp, choreography, localFrame) : false;

    const actorFloorY = (index: number) =>
      floorForDepth(plans[index]?.depth ?? actors[index]?.depth ?? 'mid', plateMetrics);
    const actorStature = (index: number) => statureFor(actors[index]);
    const propReferenceIndex = setProp
      ? setProp.operatorIndex ??
        plans.reduce(
          (nearest, plan, index) =>
            Math.abs(planXAt(plan, localFrame) - setProp.x) <
            Math.abs(planXAt(plans[nearest], localFrame) - setProp.x)
              ? index
              : nearest,
          0,
        )
      : 0;
    const propHumanHeightPx = setProp
      ? humanHeightRatioAt(
          setProp.floorY,
          actors[propReferenceIndex] ? actorStature(propReferenceIndex) : 1,
          plateMetrics,
        ) * height
      : plateMetrics.refMidFigureHeight * height;
    const propCommon = setProp
      ? {
          frame: sceneElapsed,
          fps,
          x: setProp.x,
          floorY: setProp.floorY,
          humanHeightPx: propHumanHeightPx,
          sceneWidth: width,
          sceneHeight: height,
          active: setPropActive,
          propSrc: setProp.propSrc,
          ambientTint,
        }
      : null;

    const occludedActors = new Set<number>();
    if (setProp?.occludeWaist && setProp.kind !== 'telemetry_screen') {
      plans.forEach((plan, index) => {
        if (Math.abs(planXAt(plan, localFrame) - setProp.x) < 0.09) occludedActors.add(index);
      });
      if (setProp.operatorIndex !== undefined) occludedActors.add(setProp.operatorIndex);
    }

    // Build depth-sorted midground elements ('far' -> 'mid' -> 'near').
    // Occluding furniture deliberately splits a puppet into body and arm passes.
    const depthItems: Array<{order: number; key: string; node: React.ReactNode}> = [];

    const actorNode = (actor: StoryActor, idx: number, renderPass: MetricPuppetRenderPass) => {
      const plan = plans[idx];
      if (!actor.rig || !plan) return null;
      const cue = findSpeakerCue(speech, shot.scene_number, actor.name, globalFrame);
      const isSpeaking = Boolean(cue && cue.value !== 'X' && cue.value !== 'A');
      const floorY = actorFloorY(idx);
      const baseFigureRatio = plateMetrics.refMidFigureHeight * perspectiveScale(floorY, plateMetrics);
      const targetsProp =
        Boolean(setProp) &&
        (setProp?.operatorIndex === idx ||
          (setProp?.operatorIndex === undefined && setProp?.kind === 'console_desk'));
      return (
        <MetricPuppet
          name={actor.name}
          rig={actor.rig}
          sideRig={actor.side_rig}
          action={plan.primaryAction}
          fromX={plan.fromX}
          toX={plan.toX}
          facing={actor.facing}
          depth={plan.depth}
          frame={localFrame}
          fps={fps}
          duration={shot.duration}
          speaking={isSpeaking}
          mouthCue={cue?.value || 'X'}
          width={width}
          height={height}
          index={idx}
          phaseContinuation={phase === 1}
          clips={plan.clips}
          cameraMoving={isCameraMoving}
          partnerX={plan.partnerX}
          propTargetX={targetsProp ? setProp?.x : undefined}
          statureScale={actorStature(idx)}
          figureHeightRatioOverride={baseFigureRatio}
          floorYOverride={floorY}
          renderPass={renderPass}
          ambientTint={ambientTint}
        />
      );
    };

    actors.forEach((actor, idx) => {
      const plan = plans[idx];
      if (!actor.rig || !plan) return;
      const splitByProp = occludedActors.has(idx);
      depthItems.push({
        order: splitByProp
          ? DEPTH_STAGE[setProp?.depth ?? plan.depth].order + 0.01
          : DEPTH_STAGE[plan.depth].order,
        key: `actor-${actor.name}-${idx}-${splitByProp ? 'body' : 'full'}`,
        node: actorNode(actor, idx, splitByProp ? 'body_behind_desk' : 'full'),
      });
    });

    if (setProp && propCommon) {
      const propOrder = DEPTH_STAGE[setProp.depth].order;
      if (setProp.kind === 'telemetry_screen') {
        depthItems.push({
          order: -0.25,
          key: 'set-prop-telemetry',
          node: <TelemetryScreenProp {...propCommon} screenY={setProp.screenY} />,
        });
      } else {
        depthItems.push({
          order: setProp.occludeWaist ? propOrder + 0.02 : propOrder - 0.04,
          key: `set-prop-${setProp.kind}-body`,
          node:
            setProp.kind === 'console_desk' ? (
              <ConsoleDeskBody {...propCommon} />
            ) : (
              <WorkbenchProp {...propCommon} />
            ),
        });
        occludedActors.forEach(idx => {
          const actor = actors[idx];
          if (!actor?.rig || !plans[idx]) return;
          depthItems.push({
            order: propOrder + 0.03,
            key: `actor-${actor.name}-${idx}-arms`,
            node: actorNode(actor, idx, 'arms_over_desk'),
          });
        });
        if (setProp.kind === 'console_desk') {
          depthItems.push({
            order: propOrder + 0.04,
            key: 'set-prop-console-overlay',
            node: <ConsoleHoloOverlay {...propCommon} />,
          });
        }
      }
    }

    if (robotAct) {
      const rCue = findSpeakerCue(speech, shot.scene_number, robotSpeaker, globalFrame);
      const rSpeaking = Boolean(rCue && rCue.value !== 'X' && rCue.value !== 'A');
      const robotOrder = focus === 'robot' && framing === 'close' ? 2 : 1;
      const roverFloorY =
        typeof shot.plate_metrics?.mid_y === 'number'
          ? plateMetrics.midY
          : clampNumber(plateMetrics.farY + 0.04, 0.78, 0.94);
      const targetRoverHeightPx =
        plateMetrics.refMidFigureHeight *
        perspectiveScale(roverFloorY, plateMetrics) *
        0.53 *
        height;
      const robotFacing: 'left' | 'right' =
        robotAct === 'drive'
          ? avgActorX > 0.65
            ? 'left'
            : 'right'
          : actors.length > 0 && avgActorX < robotCenterX
          ? 'left'
          : 'right';
      depthItems.push({
        order: robotOrder,
        key: 'story-robot',
        node: (
          <MetricRobotPuppet
            rig={resolvedRobotRig}
            frame={sceneElapsed}
            fps={fps}
            duration={totalDuration}
            action={robotAct}
            speaking={rSpeaking}
            width={width * 0.46}
            height={height * 0.56}
            left={`${robotLeftPct}%`}
            bottom={`${(1 - roverFloorY) * 100}%`}
            facing={robotFacing}
            floorY={roverFloorY}
            sceneWidth={width}
            sceneHeight={height}
            targetRoverHeightPx={targetRoverHeightPx}
            travelRangePx={robotAct === 'drive'
                ? robotFacing === 'right'
                  ? [-0.15 * width, 0.15 * width]
                  : [0.15 * width, -0.15 * width]
                : undefined
            }
            ambientTint={ambientTint}
          />
        ),
      });
    }

    depthItems.sort((a, b) => a.order - b.order);

    const fgScale = Math.min(1.22, Math.max(1.10, bgScale * 1.05));

    return (
      <AbsoluteFill style={{overflow: 'hidden', background: '#0c1c2c', opacity: fade, fontFamily: font}}>
        {/* Far / Base Location Plate: 0.4x parallax, scale strictly <= 1.14, subtle DOF blur on close */}
        {bgSrc && (
          <AbsoluteFill
            style={{
              transformOrigin: `${originX * 100}% ${originY * 100}%`,
              transform: `translate(${panX * 0.4}px, ${panY * 0.4}px) scale(${bgScale})`,
              filter: framing === 'close' ? 'blur(2.6px) brightness(0.96)' : undefined,
            }}
          >
            <Img src={staticFile(bgSrc)} style={cover} />
          </AbsoluteFill>
        )}

        {/* Depth-sorted Midground Stage: Puppets + RobotPuppet */}
        <AbsoluteFill
          style={{
            transformOrigin: `${originX * 100}% ${originY * 100}%`,
            transform: `translate(${panX}px, ${panY}px) scale(${stageScale})`,
          }}
        >
          {depthItems.map(item => (
            <React.Fragment key={item.key}>{item.node}</React.Fragment>
          ))}
        </AbsoluteFill>

        {/* Plate-derived floor bounce subtly tints feet, tires, contact shadows and set-piece bases. */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: `${Math.max(62, plateMetrics.wallBaseY * 100)}%`,
            bottom: 0,
            pointerEvents: 'none',
            mixBlendMode: 'soft-light',
            background: `radial-gradient(ellipse at 50% 88%, ${floorCss(0.18)}, transparent 72%)`,
          }}
        />

        {/* Optional Foreground Plate: 1.18x parallax confined to bottom foreground apron so floor markings never float across shins */}
        {nearSrc && (
          <AbsoluteFill
            style={{
              pointerEvents: 'none',
              transformOrigin: `${originX * 100}% ${originY * 100}%`,
              transform: `translate(${panX * 1.18}px, ${panY * 1.18}px) scale(${fgScale})`,
              filter: framing === 'close' ? 'blur(3px)' : undefined,
              maskImage: 'linear-gradient(to bottom, transparent 0%, transparent 91%, rgba(0,0,0,0.88) 97%, #000 100%)',
              WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, transparent 91%, rgba(0,0,0,0.88) 97%, #000 100%)',
            }}
          >
            <Img src={staticFile(nearSrc)} style={cover} />
          </AbsoluteFill>
        )}

        {/* Static ambient window light, sparse slow dust, and color grade */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            pointerEvents: 'none',
            mixBlendMode: 'screen',
            background: `linear-gradient(104deg, transparent 30%, ${ambientCss(0.055)} 46%, transparent 64%)`,
          }}
        />
        {Array.from({length: 6}, (_, i) => {
          const seed = `story-${turnKey}-dust-${i}`;
          const depthVal = 0.35 + random(`${seed}-d`) * 0.65;
          const x =
            random(`${seed}-x`) * width + Math.sin(sceneElapsed / (60 + depthVal * 40) + i) * 8 * depthVal;
          const y =
            ((random(`${seed}-y`) * height + sceneElapsed * (0.08 + depthVal * 0.18)) % (height + 24)) - 12;
          const size = 1.2 + random(`${seed}-s`) * 1.6;
          return (
            <div
              key={i}
              style={{
                position: 'absolute',
                left: x,
                top: y,
                width: size,
                height: size,
                borderRadius: '50%',
                background: '#f4efe4',
                opacity:
                  (0.06 + random(`${seed}-o`) * 0.12) * (0.75 + 0.25 * Math.sin(sceneElapsed / 30 + i)),
                pointerEvents: 'none',
              }}
            />
          );
        })}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            pointerEvents: 'none',
            mixBlendMode: 'soft-light',
            background: `linear-gradient(180deg, ${ambientCss(0.15)}, transparent 34%), ${grade}`,
          }}
        />
        {shot.label && (
          <div
            style={{
              position: 'absolute',
              left: width * 0.055,
              top: height * 0.05,
              padding: '8px 15px',
              background: '#0d2035a8',
              borderLeft: '4px solid #f3b96c',
              color: '#f7f9f4',
              fontSize: 23,
              fontWeight: 800,
              textShadow: '0 2px 6px #000',
              maxWidth: width * 0.44,
            }}
          >
            {shot.label}
          </div>
        )}
      </AbsoluteFill>
    );
  }

  // ---------------------------------------------------------------------------
  // 2. Legacy Full-Scene Story Fallback (when neither plate nor actor rig exists)
  // ---------------------------------------------------------------------------
  const motionFrames = shot.duration + Math.max(0, lead);
  const p = eased(frame, motionFrames);
  const actDuration = shot.duration * (split ? 2 : 1);
  const actFrame =
    phase === 1 ? Math.max(0, shot.duration + (frame - lead)) : Math.max(0, frame - lead);
  const sweepP = phase === 1 ? 0.5 + 0.5 * p : phase === 0 ? p * 0.5 : p;
  const push = /push|zoom in|推|近/i.test(camera);
  const pull = /pull|zoom out|拉远|拉出|后退/i.test(camera);
  const panWord = /pan|横移|平移|摇/i.test(camera);
  let mode: 'push' | 'pull' | 'pan' = 'push';
  if (phase === 1) mode = 'pan';
  else if (phase !== 0 && pull && !push) mode = 'pull';
  else if (phase !== 0 && panWord && !push) mode = 'pan';

  const layers = shot.layers?.background && shot.layers.body ? shot.layers : null;
  const anchorX =
    focus === 'actor'
      ? layers?.subject_x ?? actors[0]?.from_x ?? 0.38
      : focus === 'robot'
      ? 0.68
      : focus === 'prop'
      ? 0.32
      : 0.5;
  const anchorY = focus === 'wide' ? 0.5 : focus === 'prop' ? 0.72 : 0.62;
  const originX = Math.min(0.82, Math.max(0.18, anchorX));
  const originY = Math.min(0.78, Math.max(0.28, anchorY));

  const roomAt = (minScale: number) => ({
    x: Math.min(originX, 1 - originX) * (minScale - 1) * width * 0.8,
    y: Math.min(originY, 1 - originY) * (minScale - 1) * height * 0.8,
  });
  const spin = turnKey % 2 ? -1 : 1;
  const pushRoom = roomAt(1.04);
  const pushEndX = direction * pushRoom.x * 0.35;
  const pushEndY = -pushRoom.y * 0.28;
  let scale: number;
  let panX: number;
  let panY: number;
  let rot: number;
  if (phase === 1) {
    const panRoom = roomAt(1.1);
    scale = Math.min(1.14, Math.max(1.04, mix(p, 1.12, 1.14)));
    panX = mix(p, pushEndX, pushEndX + direction * panRoom.x * 0.85);
    panY = mix(p, pushEndY, pushEndY * 0.35);
    rot = Math.max(-0.3, Math.min(0.3, spin * mix(p, 0.28, 0.12)));
  } else {
    const startScale = mode === 'pull' ? 1.14 : mode === 'pan' ? 1.1 : 1.04;
    const endScale = mode === 'pull' ? 1.04 : mode === 'pan' ? 1.14 : 1.12;
    scale = Math.min(1.14, Math.max(1.04, mix(p, startScale, endScale)));
    const room = roomAt(Math.min(startScale, endScale));
    const travel = mode === 'pan' ? 1 : 0.35;
    panX = direction * mix(p, -room.x, room.x) * travel;
    panY = mix(p, room.y * 0.22, -room.y * 0.28) * (mode === 'pan' ? 0.35 : 1);
    rot = Math.max(-0.3, Math.min(0.3, spin * mix(p, 0.04, 0.28)));
  }
  const layerScale = (amount: number) => Math.min(1.14, Math.max(1.04, 1.04 + (scale - 1.04) * amount));
  const cameraStyle = (amount: number): React.CSSProperties => ({
    transform: `translate(${panX * amount}px, ${panY * amount}px) rotate(${rot * amount}deg) scale(${layerScale(amount)})`,
    transformOrigin: `${originX * 100}% ${originY * 100}%`,
  });
  const active = (speaker: string) =>
    speech.some(
      cue =>
        cue.scene_number === shot.scene_number &&
        cue.speaker === speaker &&
        cue.value !== 'X' &&
        cue.value !== 'A' &&
        globalFrame >= cue.from &&
        globalFrame < cue.to,
    );
  const motionFrame = frame + (phase ? shot.duration : 0);
  const motionDuration = shot.duration * (phase === undefined ? 1 : 2);
  const legacyRoverFloorY =
    typeof shot.plate_metrics?.mid_y === 'number'
      ? plateMetrics.midY
      : clampNumber(plateMetrics.farY + 0.04, 0.78, 0.94);
  const legacyRoverHeightPx =
    humanHeightRatioAt(legacyRoverFloorY, 1, plateMetrics) * 0.53 * height;
  const placed = [
    layers ? {src: layers.body, x: layers.subject_x, y: layers.subject_y} : null,
    layers?.body2 && typeof layers.subject2_x === 'number' && typeof layers.subject2_y === 'number'
      ? {src: layers.body2, x: layers.subject2_x, y: layers.subject2_y}
      : null,
  ];

  return (
    <AbsoluteFill style={{overflow: 'hidden', background: '#0c1c2c', opacity: fade, fontFamily: font}}>
      {layers ? (
        <AbsoluteFill style={cameraStyle(0.45)}>
          <Img src={staticFile(layers.background)} style={cover} />
        </AbsoluteFill>
      ) : (
        <AbsoluteFill style={cameraStyle(1)}>
          <Img src={staticFile(shot.src)} style={cover} />
        </AbsoluteFill>
      )}
      {layers && (
        <AbsoluteFill style={cameraStyle(1)}>
          {actors.map((actor, i) => {
            const body = placed[i];
            if (!body) return null;
            const motion = legacyAct(
              actor.action,
              actFrame,
              fps,
              actDuration,
              actor.from_x,
              actor.to_x,
              active(actor.name),
              i,
              width,
            );
            const origin = `${body.x * 100}% ${body.y * 100}%`;
            return (
              <div key={`${actor.name}-${i}`}>
                <div
                  style={{
                    position: 'absolute',
                    left: body.x * width,
                    top: body.y * height,
                    width: width * 0.16,
                    height: height * 0.04,
                    transform: `translate(-50%, -35%) translate(${motion.x}px, ${motion.y}px)`,
                    borderRadius: '50%',
                    background: 'radial-gradient(ellipse, rgba(0,0,0,.42), rgba(0,0,0,0) 72%)',
                    filter: 'blur(3px)',
                  }}
                />
                <Img
                  src={staticFile(body.src)}
                  style={{
                    ...cover,
                    position: 'absolute',
                    inset: 0,
                    transformOrigin: origin,
                    transform: `perspective(900px) translate(${motion.x}px, ${motion.y}px) rotateX(${motion.lean}deg) rotate(${motion.rot}deg) scaleY(${motion.scaleY})`,
                  }}
                />
              </div>
            );
          })}
        </AbsoluteFill>
      )}
      {shot.rover && (
        <AbsoluteFill style={cameraStyle(1)}>
          <MetricRobotPuppet
            rig={resolvedRobotRig}
            frame={motionFrame}
            fps={fps}
            duration={motionDuration}
            action={shot.rover.action}
            speaking={active(shot.rover.speaker)}
            width={width * 0.5}
            height={height * 0.6}
            left="45%"
            bottom={`${(1 - legacyRoverFloorY) * 100}%`}
            floorY={legacyRoverFloorY}
            sceneWidth={width}
            sceneHeight={height}
            targetRoverHeightPx={legacyRoverHeightPx}
            ambientTint={ambientTint}
          />
        </AbsoluteFill>
      )}
      {layers && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            pointerEvents: 'none',
            background: 'radial-gradient(ellipse at 50% 46%, transparent 46%, rgba(5,10,18,.58) 100%)',
            filter: 'blur(8px)',
          }}
        />
      )}
      <div
        style={{
          position: 'absolute',
          inset: '-22%',
          pointerEvents: 'none',
          mixBlendMode: 'screen',
          background: `linear-gradient(104deg, transparent 36%, ${ambientCss(0.055)} 49%, transparent 62%)`,
          transform: `translateX(${mix(sweepP, -42, 58)}%)`,
        }}
      />
      {Array.from({length: 16}, (_, i) => {
        const seed = `story-${shot.index}-dust-${i}`;
        const depth = 0.35 + random(`${seed}-d`) * 0.65;
        const dustFrame = frame + (phase === 1 ? shot.duration : 0);
        const x = random(`${seed}-x`) * width + Math.sin(dustFrame / (36 + depth * 28) + i) * 14 * depth;
        const y = ((random(`${seed}-y`) * height + dustFrame * (0.18 + depth * 0.42)) % (height + 24)) - 12;
        const size = 1.2 + random(`${seed}-s`) * 2.2;
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x,
              top: y,
              width: size,
              height: size,
              borderRadius: '50%',
              background: '#f4efe4',
              opacity:
                (0.12 + random(`${seed}-o`) * 0.28) * (0.65 + 0.35 * Math.sin(frame / 18 + i)),
              pointerEvents: 'none',
            }}
          />
        );
      })}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          pointerEvents: 'none',
          mixBlendMode: 'soft-light',
          background: `linear-gradient(180deg, ${ambientCss(0.18)}, transparent 32%), ${grade}`,
        }}
      />
      {shot.label && (
        <div
          style={{
            position: 'absolute',
            left: width * 0.055,
            top: height * 0.05,
            padding: '8px 15px',
            background: '#0d2035a8',
            borderLeft: '4px solid #f3b96c',
            color: '#f7f9f4',
            fontSize: 23,
            fontWeight: 800,
            textShadow: '0 2px 6px #000',
            maxWidth: width * 0.44,
          }}
        >
          {shot.label}
        </div>
      )}
    </AbsoluteFill>
  );
};
