import type {
  CharacterRig,
  PuppetAction,
  PuppetClip,
  PuppetClipType,
  PuppetDepth,
  PuppetFacing,
} from './puppet';

/**
 * Deterministic shot choreography compiler.
 *
 * Turns one coarse `action` per actor into a short beat timeline
 * (walk -> settle -> talk/listen, one-shot punctuation gestures) that obeys
 * TV-anime staging rules: framing locks, brisk entrances, a single primary
 * mover, and stillness for everyone who is not the focus.
 */

export type DirectorFraming = 'wide' | 'medium' | 'close';

export type DirectorActor = {
  name: string;
  action: PuppetAction | string;
  from_x: number;
  to_x: number;
  facing?: PuppetFacing;
  depth?: PuppetDepth;
  rig?: CharacterRig;
  clips?: PuppetClip[];
};

export type DirectorCue = {from: number; to: number; value: string; speaker: string; scene_number?: number};

export type DirectedActorPlan = {
  name: string;
  clips: PuppetClip[];
  depth: PuppetDepth;
  partnerX?: number;
  /** Directed start / end marks (after framing locks and travel caps). */
  fromX: number;
  toX: number;
  /** Representative action for consumers that still need a single action. */
  primaryAction: PuppetAction;
  walks: boolean;
};

export type ShotChoreography = {
  actors: DirectedActorPlan[];
  /** True when any actor travels horizontally in this shot. */
  hasLocomotion: boolean;
};

export type ShotChoreographyInput = {
  actors: DirectorActor[];
  framing: DirectorFraming;
  camera: string;
  robotAct?: string;
  /** Horizontal centre of the robot (0..1 of stage width) when on stage. */
  robotX?: number;
  /** Horizontal centre (0..1) of the interactive prop / console / display targeted by point/console/work. */
  propTargetX?: number;
  /** Global frame that maps to local frame 0 of this shot. */
  shotFromGlobal: number;
  totalFrames: number;
  fps: number;
  /** Speech cues for this scene (global frames). */
  speechCues: DirectorCue[];
  /** Phase-continuation marks: actor name -> X where the previous phase ended. */
  startX?: Record<string, number>;
  phaseContinuation?: boolean;
};

const SETTLE_FRAMES = 8;
const SPEECH_PAD = 4;
const MIN_SEGMENT = 6;
const POINT_FRAMES = 38;
const WAVE_FRAMES = 28;
const REACT_FRAMES = 26;
const WALK_MIN_DIST = 0.05;
const CARRY_MIN_DIST = 0.06;
const MEDIUM_MAX_DRIFT = 0.03;
const ENTRANCE_MAX_TRAVEL = 0.24;
const ENTRANCE_PULLED_TRAVEL = 0.2;

const clampNum = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const clampX = (v: number) => clampNum(Number.isFinite(v) ? v : 0.5, 0.04, 0.96);

type Span = {start: number; end: number};

const speakerMatches = (cueSpeaker: string, name: string) =>
  Boolean(cueSpeaker && name) &&
  (cueSpeaker === name || cueSpeaker.includes(name) || name.includes(cueSpeaker));

const isVoiced = (cue: DirectorCue) => cue.value !== 'X' && cue.value !== 'A';

/** Padded, merged local-frame speech spans so short pauses between words never flicker. */
const speechSpans = (
  cues: DirectorCue[],
  predicate: (cue: DirectorCue) => boolean,
  shotFromGlobal: number,
  total: number,
): Span[] => {
  const raw = cues
    .filter(cue => isVoiced(cue) && predicate(cue))
    .map(cue => ({
      start: Math.max(0, Math.round(cue.from - shotFromGlobal) - SPEECH_PAD),
      end: Math.min(total, Math.round(cue.to - shotFromGlobal) + SPEECH_PAD),
    }))
    .filter(span => span.end > span.start)
    .sort((a, b) => a.start - b.start);
  const merged: Span[] = [];
  for (const span of raw) {
    const last = merged[merged.length - 1];
    if (last && span.start <= last.end) last.end = Math.max(last.end, span.end);
    else merged.push({...span});
  }
  return merged;
};

const overlap = (a: Span, spans: Span[]) =>
  spans.reduce((sum, s) => sum + Math.max(0, Math.min(a.end, s.end) - Math.max(a.start, s.start)), 0);

const toAction = (type: PuppetClipType): PuppetAction =>
  type === 'listen' ? 'idle' : type === 'settle' ? 'idle' : (type as PuppetAction);

const normaliseAction = (action: string): PuppetAction => {
  const known: PuppetAction[] = ['idle', 'talk', 'walk', 'point', 'wave', 'work', 'carry', 'console', 'react'];
  return (known as string[]).includes(action) ? (action as PuppetAction) : 'idle';
};

/** Close-ups allow only head / mouth / blink / subtle reaction. */
const closeSafeType = (type: PuppetClipType, speaking: boolean): PuppetClipType => {
  if (type === 'walk' || type === 'carry' || type === 'wave' || type === 'point' || type === 'work' || type === 'console' || type === 'settle') {
    return speaking ? 'talk' : 'idle';
  }
  return type;
};

const facingToward = (x: number, target: number | undefined, fallback?: PuppetFacing): PuppetFacing | undefined => {
  if (fallback) return fallback;
  if (target === undefined || Math.abs(target - x) < 0.04) return undefined;
  return target < x ? 'left' : 'right';
};

const FACING_DEADZONE = 0.04;

/**
 * Stamp each clip with its camera pose (profile while travelling, three-quarter once settled),
 * its gaze/aim target and a facing that turns toward the active target.
 * Authored clips keep any explicit `facing` / `pose` / `targetX`.
 */
const orientClip = (
  clip: PuppetClip,
  opts: {
    partnerX?: number;
    propTargetX?: number;
    robotX?: number;
    drive: boolean;
    authored: boolean;
  },
): PuppetClip => {
  const {partnerX, propTargetX, robotX, drive, authored} = opts;
  const moving = clip.type === 'walk' || (clip.type === 'carry' && Math.abs(clip.toX - clip.fromX) > 0.005);
  const pose: 'side' | 'three_quarter' = clip.pose ?? (moving ? 'side' : 'three_quarter');

  let target: number | undefined;
  if (moving) {
    target = clip.toX;
  } else if (drive && robotX !== undefined) {
    target = robotX;
  } else if (clip.type === 'point') {
    target = propTargetX ?? robotX ?? partnerX;
  } else if (clip.type === 'console' || clip.type === 'work') {
    target = propTargetX ?? robotX;
  } else {
    target = partnerX;
  }
  if (clip.targetX !== undefined && Number.isFinite(clip.targetX)) target = clip.targetX;

  let facing = clip.facing;
  const keepAuthoredFacing = authored && clip.facing !== undefined;
  if (!keepAuthoredFacing && !moving && clip.type !== 'carry' && target !== undefined) {
    if (Math.abs(target - clip.fromX) >= FACING_DEADZONE) {
      facing = target < clip.fromX ? 'left' : 'right';
    }
  } else if (moving && facing === undefined) {
    facing = clip.toX < clip.fromX ? 'left' : 'right';
  }
  return {...clip, pose, targetX: target, facing};
};

/** Sort, clamp, de-gap and absorb sub-6-frame slivers so the beat timeline is contiguous. */
const finaliseClips = (clips: PuppetClip[], total: number): PuppetClip[] => {
  const sorted = clips
    .map(c => ({
      ...c,
      startFrame: clampNum(Math.round(c.startFrame), 0, total),
      endFrame: clampNum(Math.round(c.endFrame), 0, total),
    }))
    .filter(c => c.endFrame > c.startFrame)
    .sort((a, b) => a.startFrame - b.startFrame);
  const out: PuppetClip[] = [];
  for (const clip of sorted) {
    const last = out[out.length - 1];
    if (last) {
      clip.startFrame = Math.max(clip.startFrame, last.endFrame);
      if (clip.endFrame <= clip.startFrame) continue;
      if (clip.startFrame > last.endFrame) last.endFrame = clip.startFrame;
      if (
        last.type === clip.type &&
        last.fromX === last.toX &&
        clip.fromX === clip.toX &&
        last.toX === clip.fromX &&
        last.facing === clip.facing
      ) {
        last.endFrame = clip.endFrame;
        continue;
      }
    }
    out.push(clip);
  }
  // Absorb slivers into the previous stationary beat (never into a moving or one-shot clip).
  for (let i = out.length - 1; i > 0; i--) {
    const clip = out[i];
    const prev = out[i - 1];
    const tiny = clip.endFrame - clip.startFrame < MIN_SEGMENT;
    const fillerKinds: PuppetClipType[] = ['talk', 'listen', 'idle'];
    if (tiny && fillerKinds.includes(clip.type) && fillerKinds.includes(prev.type)) {
      prev.endFrame = clip.endFrame;
      out.splice(i, 1);
    }
  }
  if (out.length) {
    out[0].startFrame = 0;
    out[out.length - 1].endFrame = total;
  }
  return out;
};

type ActorContext = {
  actor: DirectorActor;
  index: number;
  baseAction: PuppetAction;
  startX: number;
  endX: number;
  wantsWalk: boolean;
  walkDist: number;
  depth: PuppetDepth;
  spans: Span[];
  othersSpans: Span[];
};

export const compileShotChoreography = (input: ShotChoreographyInput): ShotChoreography => {
  const {actors, framing, robotAct, robotX, shotFromGlobal, fps} = input;
  const total = Math.max(1, Math.round(input.totalFrames));
  const totalSeconds = total / Math.max(1, fps);
  const drive = robotAct === 'drive';

  // ---- 1. Marks, framing locks, travel caps -------------------------------------------------
  const contexts: ActorContext[] = actors.map((actor, index) => {
    const baseAction = normaliseAction(String(actor.action || 'idle'));
    const override = input.startX?.[actor.name];
    let startX = clampX(actor.from_x);
    let endX = clampX(actor.to_x);
    if (override !== undefined && Number.isFinite(override)) {
      // Continue exactly from the previous phase's mark; a same-mark hold stays put.
      if (Math.abs(startX - endX) < 0.005) endX = clampX(override);
      startX = clampX(override);
    }
    const stationaryAction = baseAction === 'work' || baseAction === 'console';

    if (framing === 'close') {
      endX = startX;
    } else if (framing === 'medium') {
      if (Math.abs(endX - startX) > MEDIUM_MAX_DRIFT) startX = endX;
      else endX = startX;
    } else if (stationaryAction) {
      endX = startX;
    }

    let dist = Math.abs(endX - startX);
    const minDist = baseAction === 'carry' ? CARRY_MIN_DIST : WALK_MIN_DIST;
    let wantsWalk = framing === 'wide' && !drive && dist >= minDist;
    if (wantsWalk && dist > ENTRANCE_MAX_TRAVEL) {
      startX = clampX(endX - Math.sign(endX - startX) * ENTRANCE_PULLED_TRAVEL);
      dist = Math.abs(endX - startX);
    }
    if (!wantsWalk) {
      // Already at the mark: actors land where they were meant to end up.
      // A continuing phase never snaps away from the previous phase's final mark.
      if (!stationaryAction && framing === 'wide' && override === undefined) startX = endX;
      else endX = startX;
      dist = 0;
    }

    const depth: PuppetDepth =
      actor.depth === 'far' || actor.depth === 'mid' || actor.depth === 'near'
        ? actor.depth
        : framing === 'close'
        ? 'near'
        : 'mid';
    return {
      actor,
      index,
      baseAction,
      startX,
      endX,
      wantsWalk,
      walkDist: dist,
      depth,
      spans: speechSpans(input.speechCues, cue => speakerMatches(cue.speaker, actor.name), shotFromGlobal, total),
      othersSpans: speechSpans(input.speechCues, cue => !speakerMatches(cue.speaker, actor.name), shotFromGlobal, total),
    };
  });

  // ---- 2. Single primary mover --------------------------------------------------------------
  const walkers = contexts.filter(c => c.wantsWalk);
  if (walkers.length > 1) {
    const primary = walkers.reduce((best, c) => {
      const score = (c.baseAction === 'walk' || c.baseAction === 'carry' ? 1 : 0) * 10 + c.walkDist;
      const bestScore = (best.baseAction === 'walk' || best.baseAction === 'carry' ? 1 : 0) * 10 + best.walkDist;
      return score > bestScore ? c : best;
    });
    walkers.forEach(c => {
      if (c === primary) return;
      c.wantsWalk = false;
      c.startX = c.endX;
      c.walkDist = 0;
    });
  }

  const walkFramesFor = (c: ActorContext) => {
    if (!c.wantsWalk) return 0;
    const cap = Math.min(c.baseAction === 'carry' ? 1.6 : 1.65, totalSeconds * 0.42);
    const seconds = Math.min(cap, Math.max(1.0, 0.85 + c.walkDist * 3.2));
    return Math.max(1, Math.min(total - 1, Math.round(seconds * fps)));
  };
  const walkWindows = contexts.map(c => {
    const frames = walkFramesFor(c);
    return frames > 0 ? {start: 0, end: frames} : null;
  });

  let waveTaken = false;

  // ---- 3. Per-actor beat timeline -----------------------------------------------------------
  const rawPlans = contexts.map((c, i): DirectedActorPlan => {
    const others = contexts.filter(o => o !== c);
    const partnerX =
      robotX !== undefined && (drive || others.length === 0)
        ? robotX
        : others.length > 0
        ? others.reduce((best, o) => (Math.abs(o.endX - c.endX) < Math.abs(best.endX - c.endX) ? o : best)).endX
        : robotX;
    const partnerWalk = walkWindows.find((w, j) => j !== i && w !== null) ?? null;
    const speaking = c.spans.length > 0;
    const firstSpeech = speaking ? c.spans[0].start : undefined;
    const restFacing = facingToward(c.endX, partnerX, c.actor.facing);

    // Pre-authored clips: honour them, only enforcing the framing locks.
    if (c.actor.clips && c.actor.clips.length > 0) {
      const authored = c.actor.clips.map(clip => {
        if (framing === 'close') {
          return {...clip, type: closeSafeType(clip.type, speaking), toX: clip.fromX, fromScale: undefined, toScale: undefined};
        }
        if (framing === 'medium' && Math.abs(clip.toX - clip.fromX) > MEDIUM_MAX_DRIFT) {
          return {...clip, fromX: clip.toX, type: clip.type === 'walk' ? ('idle' as PuppetClipType) : clip.type};
        }
        return clip;
      });
      const clips = finaliseClips(authored, total);
      return {
        name: c.actor.name,
        clips,
        depth: c.depth,
        partnerX,
        fromX: clips[0]?.fromX ?? c.startX,
        toX: clips[clips.length - 1]?.toX ?? c.endX,
        primaryAction: c.baseAction,
        walks: clips.some(clip => Math.abs(clip.toX - clip.fromX) > 0.005),
      };
    }

    const fixed: PuppetClip[] = [];
    let cursor = 0;

    // Walk -> settle.
    const walkFrames = walkFramesFor(c);
    if (walkFrames > 0) {
      const travelFacing: PuppetFacing = c.endX < c.startX ? 'left' : 'right';
      const entrance = !input.phaseContinuation;
      fixed.push({
        type: c.baseAction === 'carry' ? 'carry' : 'walk',
        startFrame: 0,
        endFrame: walkFrames,
        fromX: c.startX,
        toX: c.endX,
        fromScale: entrance ? 0.93 : undefined,
        toScale: entrance ? 1.0 : undefined,
        facing: travelFacing,
      });
      const settleEnd = Math.min(total, walkFrames + SETTLE_FRAMES);
      fixed.push({type: 'settle', startFrame: walkFrames, endFrame: settleEnd, fromX: c.endX, toX: c.endX, facing: travelFacing});
      cursor = settleEnd;
    }

    // One-shot punctuation gesture.
    let gesture: PuppetClipType | null = null;
    if (framing !== 'close') {
      if (c.baseAction === 'point') gesture = 'point';
      else if (c.baseAction === 'wave') gesture = 'wave';
    }
    if (c.baseAction === 'react') gesture = 'react';
    if (gesture === 'wave') {
      if (waveTaken) gesture = null;
      else waveTaken = true;
    }

    if (gesture) {
      const length = gesture === 'point' ? POINT_FRAMES : gesture === 'wave' ? WAVE_FRAMES : REACT_FRAMES;
      let start =
        gesture === 'point'
          ? firstSpeech !== undefined
            ? Math.max(6, firstSpeech + 4)
            : 8
          : gesture === 'wave'
          ? 4
          : firstSpeech !== undefined
          ? Math.max(0, firstSpeech - 10)
          : 6;
      start = Math.max(start, cursor);
      // Never flail while the partner is the primary mover; greet as they arrive.
      if (partnerWalk && start < partnerWalk.end) {
        start = gesture === 'wave' ? Math.max(start, partnerWalk.end - 6) : Math.max(start, partnerWalk.end);
      }
      start = Math.min(start, total - length);
      if (start >= cursor && start >= 0) {
        const gx = c.endX;
        fixed.push({type: gesture, startFrame: start, endFrame: start + length, fromX: gx, toX: gx, facing: restFacing});
      }
    }

    // Busy-hands base action (work / console / carry) holds until the actor turns to speak.
    const busy: PuppetClipType | null =
      framing === 'close'
        ? null
        : c.baseAction === 'work' || c.baseAction === 'console'
        ? c.baseAction
        : c.baseAction === 'carry'
        ? 'carry'
        : null;

    // Fill every free window with talk / listen / idle (or busy hands).
    const occupied = fixed
      .map(clip => ({start: clip.startFrame, end: clip.endFrame}))
      .sort((a, b) => a.start - b.start);
    const free: Span[] = [];
    let at = 0;
    for (const o of occupied) {
      if (o.start > at) free.push({start: at, end: o.start});
      at = Math.max(at, o.end);
    }
    if (at < total) free.push({start: at, end: total});

    const fill: PuppetClip[] = [];
    const restClip = (type: PuppetClipType, span: Span): PuppetClip => ({
      type,
      startFrame: span.start,
      endFrame: span.end,
      fromX: c.endX,
      toX: c.endX,
      facing: type === 'work' || type === 'console' || type === 'carry' ? c.actor.facing : restFacing,
    });
    const silentType = (span: Span): PuppetClipType => {
      if (busy && (busy === 'carry' || firstSpeech === undefined || span.start < firstSpeech || span.end - span.start >= 24)) {
        return busy;
      }
      const othersTalking = overlap(span, c.othersSpans) > (span.end - span.start) * 0.3;
      return othersTalking || drive || (robotX !== undefined && others.length === 0) ? 'listen' : 'idle';
    };
    for (const window of free) {
      let t = window.start;
      for (const s of c.spans) {
        if (s.end <= t || s.start >= window.end) continue;
        if (s.start > t) fill.push(restClip(silentType({start: t, end: s.start}), {start: t, end: s.start}));
        const talkSpan = {start: Math.max(t, s.start), end: Math.min(window.end, s.end)};
        fill.push(restClip(busy === 'carry' ? 'carry' : 'talk', talkSpan));
        t = talkSpan.end;
      }
      if (t < window.end) fill.push(restClip(silentType({start: t, end: window.end}), {start: t, end: window.end}));
    }

    let clips = finaliseClips([...fixed, ...fill], total);
    if (framing === 'close') {
      clips = finaliseClips(
        clips.map(clip => ({...clip, type: closeSafeType(clip.type, speaking), toX: clip.fromX})),
        total,
      );
    }
    if (clips.length === 0) {
      clips = [restClip(speaking ? 'talk' : 'idle', {start: 0, end: total})];
    }

    const primaryAction: PuppetAction =
      walkFrames > 0 ? 'walk' : gesture ? toAction(gesture) : busy ? toAction(busy) : speaking ? 'talk' : 'idle';

    return {
      name: c.actor.name,
      clips,
      depth: c.depth,
      partnerX,
      fromX: c.startX,
      toX: c.endX,
      primaryAction,
      walks: walkFrames > 0,
    };
  });

  // ---- 4. Turnaround orientation: pose (side vs 3/4), facing and gaze target per clip -------------
  const plans = rawPlans.map((plan, i): DirectedActorPlan => {
    const authored = Boolean(contexts[i].actor.clips && contexts[i].actor.clips!.length > 0);
    return {
      ...plan,
      clips: plan.clips.map(clip =>
        orientClip(clip, {
          partnerX: plan.partnerX,
          propTargetX: input.propTargetX,
          robotX,
          drive,
          authored,
        }),
      ),
    };
  });

  return {actors: plans, hasLocomotion: plans.some(p => p.walks)};
};

/** Directed final X per actor, used to seed the next split phase. */
export const endMarks = (choreography: ShotChoreography): Record<string, number> =>
  Object.fromEntries(choreography.actors.map(plan => [plan.name, plan.clips[plan.clips.length - 1]?.toX ?? plan.toX]));
