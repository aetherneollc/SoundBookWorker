/**
 * Pure skeleton math for pixel_story. No React.
 *
 * World is 320×180. x increases right, y increases down.
 * Limb angles are radians: 0 points straight down, positive rotates toward +x.
 *
 * Contract consumed from each shot's `cartoon` object:
 * - actors[].action: idle|walk|work|point|talk|react|carry|console|enter|drive
 * - actors[].from_x / to_x: 0..1 fractions of WORLD_W (320)
 * - cartoon.world?: { room?: exterior|interior, weather?: clear|dust|rain, light?: day|night|lamp }
 * Missing world fields default to exterior, clear, day.
 * `enter` walks to the exterior door for progress < 0.5, then the room is interior.
 * `drive` seats the actor on the rover; vehicleMotion moves both together.
 * `console` plants the interaction hand on CONSOLE_ANCHOR.
 */

export type PoseAction =
  | 'idle' | 'walk' | 'work' | 'point' | 'talk' | 'react' | 'carry' | 'console' | 'enter' | 'drive';

export type Room = 'exterior' | 'interior';
export type Weather = 'clear' | 'dust' | 'rain';
export type Light = 'day' | 'night' | 'lamp';

export type WorldInput = {room?: Room; weather?: Weather; light?: Light} | null | undefined;
export type WorldState = {room: Room; weather: Weather; light: Light};

export type Vec2 = {x: number; y: number};

export type PoseFrame = {
  action: PoseAction;
  frame: number;
  duration: number;
  fps?: number;
  fromX?: number;
  toX?: number;
};

export type VehicleMotion = {
  /** Rover center on the ground line. Increases with progress when toX > fromX. */
  x: number;
  y: number;
  seat: Vec2;
  riderPelvis: Vec2;
};

export type Pose = {
  action: PoseAction;
  progress: number;
  facing: 1 | -1;
  groundY: number;
  pelvis: Vec2;
  spine: Vec2;
  neck: Vec2;
  head: Vec2;
  leftShoulder: Vec2;
  rightShoulder: Vec2;
  leftElbow: Vec2;
  rightElbow: Vec2;
  leftHand: Vec2;
  rightHand: Vec2;
  /** Interaction hand. For `console` this is the hand on the console. */
  hand: Vec2;
  leftHip: Vec2;
  rightHip: Vec2;
  leftKnee: Vec2;
  rightKnee: Vec2;
  leftAnkle: Vec2;
  rightAnkle: Vec2;
  leftFoot: Vec2;
  rightFoot: Vec2;
  /** Radians from straight down, positive toward +x. */
  leftThigh: number;
  rightThigh: number;
  leftShin: number;
  rightShin: number;
  leftArm: number;
  rightArm: number;
  leftForearm: number;
  rightForearm: number;
  lean: number;
  consoleAnchor: Vec2;
  vehicle: VehicleMotion | null;
};

export const WORLD_W = 320;
export const WORLD_H = 180;
export const GROUND_Y = 150;
export const DOOR_PROGRESS = 0.5;
export const EXTERIOR_DOOR_X = 258;
export const INTERIOR_DOOR_X = 34;
export const INTERIOR_STAND_X = 64;
export const CONSOLE_STAND_X = 142;
export const CONSOLE_ANCHOR: Vec2 = {x: 166, y: 124};
export const HAND_REACH_THRESHOLD = 4;
export const ROVER_PARK_X = 100;

export const BONE = {
  thigh: 14,
  shin: 13,
  upperArm: 12,
  forearm: 11,
  hip: 4,
  shoulder: 6,
  shoulderDrop: 13,
  torso: 14,
} as const;

const STAND_DROP = 22;
/** Seat relative to the rover's ground center. The rider pelvis is this seat. */
export const SEAT_OFFSET = {x: -8, y: -16};
const DEFAULT_FROM_X = 0.18;
const DEFAULT_TO_X = 0.55;
const DEFAULT_VEHICLE_FROM = 0.22;
const DEFAULT_VEHICLE_TO = 0.55;

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export const poseProgress = (frame: number, duration: number) => {
  if (!Number.isFinite(frame)) return 0;
  const span = Number.isFinite(duration) && duration > 0 ? duration : 1;
  return clamp(frame / span, 0, 1);
};

const unit = (value: number | undefined, fallback: number) => {
  if (!Number.isFinite(value as number)) return fallback;
  return clamp(value as number, 0, 1);
};

export const resolveWorld = (world?: WorldInput): WorldState => ({
  room: world?.room === 'interior' ? 'interior' : 'exterior',
  weather: world?.weather === 'dust' || world?.weather === 'rain' ? world.weather : 'clear',
  light: world?.light === 'night' || world?.light === 'lamp' ? world.light : 'day',
});

/** Enter is exterior before the door and interior once the actor has passed it. */
export const roomAt = (action: PoseAction, progress: number): Room => {
  if (action !== 'enter') return 'exterior';
  const t = Number.isFinite(progress) ? clamp(progress, 0, 1) : 0;
  return t < DOOR_PROGRESS ? 'exterior' : 'interior';
};

/** Shot room: an `enter` action cuts at the door; otherwise cartoon.world.room is used. */
export const frameRoom = (action: PoseAction | PoseAction[], progress: number, world?: WorldInput): Room => {
  const actions = Array.isArray(action) ? action : [action];
  if (actions.includes('enter')) return roomAt('enter', progress);
  return resolveWorld(world).room;
};

const driveEnds = (fromX?: number, toX?: number) => {
  const start = unit(fromX, DEFAULT_VEHICLE_FROM) * WORLD_W;
  let end = unit(toX, DEFAULT_VEHICLE_TO) * WORLD_W;
  if (Math.abs(end - start) < 12) end = clamp(start + 96, 16, WORLD_W - 24);
  return {start, end};
};

/** Rover center and the seated pelvis. The rider pelvis is the seat. */
export const vehicleMotion = (progress: number, fromX?: number, toX?: number): VehicleMotion => {
  const t = Number.isFinite(progress) ? clamp(progress, 0, 1) : 0;
  const {start, end} = driveEnds(fromX, toX);
  const x = start + (end - start) * t;
  const seat = {x: x + SEAT_OFFSET.x, y: GROUND_Y + SEAT_OFFSET.y};
  return {x, y: GROUND_Y, seat, riderPelvis: {x: seat.x, y: seat.y}};
};

export type Particle = {x: number; y: number};

/** Falling drops. Empty unless weather is rain, including when weather is clear or dust. */
export const rainDrops = (weather: Weather, frame: number): Particle[] => {
  if (weather !== 'rain') return [];
  const tick = Number.isFinite(frame) ? Math.max(0, Math.floor(frame)) : 0;
  return Array.from({length: 36}, (_, i) => {
    const speed = 4 + (i % 5);
    return {
      x: (i * 53 + 7) % WORLD_W,
      y: ((i * 37 + tick * speed) % (WORLD_H + 10)) - 6,
    };
  });
};

export const dustMotes = (weather: Weather, frame: number): Particle[] => {
  if (weather !== 'dust') return [];
  const tick = Number.isFinite(frame) ? Math.max(0, Math.floor(frame)) : 0;
  return Array.from({length: 18}, (_, i) => ({
    x: (i * 41 + tick * (1 + (i % 3))) % WORLD_W,
    y: 18 + ((i * 19 + tick) % 96),
  }));
};

type Chain = {joint: Vec2; end: Vec2; angle1: number; angle2: number};

const solve2Bone = (root: Vec2, target: Vec2, len1: number, len2: number, bend: 1 | -1): Chain => {
  let dx = target.x - root.x;
  let dy = target.y - root.y;
  const max = len1 + len2 - 0.05;
  const min = Math.abs(len1 - len2) + 0.05;
  let dist = Math.hypot(dx, dy);
  if (dist < 1e-4) {
    dx = 0;
    dy = min;
    dist = min;
  }
  const scale = clamp(dist, min, max) / dist;
  const tx = root.x + dx * scale;
  const ty = root.y + dy * scale;
  const d = Math.max(0.05, dist * scale);
  const ux = (tx - root.x) / d;
  const uy = (ty - root.y) / d;
  const cos = clamp((len1 * len1 + d * d - len2 * len2) / (2 * len1 * d), -1, 1);
  const sin = Math.sqrt(Math.max(0, 1 - cos * cos));
  const px = bend * uy;
  const py = bend * -ux;
  const joint = {x: root.x + ux * len1 * cos + px * len1 * sin, y: root.y + uy * len1 * cos + py * len1 * sin};
  return {
    joint,
    end: {x: tx, y: ty},
    angle1: Math.atan2(joint.x - root.x, joint.y - root.y),
    angle2: Math.atan2(tx - joint.x, ty - joint.y),
  };
};

const plant = (hip: Vec2, wantX: number) => {
  const dy = GROUND_Y - hip.y;
  const reach = BONE.thigh + BONE.shin - 0.4;
  const maxDx = Math.sqrt(Math.max(0, reach * reach - dy * dy));
  const dx = clamp(wantX - hip.x, -maxDx, maxDx);
  return {x: hip.x + dx, y: GROUND_Y};
};

const swingOf = (progress: number, fps: number, duration: number) => {
  const safeFps = Number.isFinite(fps) && fps > 0 ? fps : 24;
  const seconds = (Number.isFinite(duration) && duration > 0 ? duration : safeFps) / safeFps;
  const steps = 2 * Math.max(1, Math.round(seconds / 2));
  return Math.sin(progress * Math.PI * 2 * steps + Math.PI / 2);
};

/**
 * Full-body pose for one frame. Grounded actions keep both soles on GROUND_Y.
 * Joints are in world space for a right-facing skeleton. `facing === -1` means the
 * renderer mirrors that skeleton around `pelvis.x`; pelvis.x itself stays on the path.
 */
export const pose = (input: PoseFrame): Pose => {
  const action = input.action;
  const fps = Number.isFinite(input.fps) && (input.fps as number) > 0 ? input.fps as number : 24;
  const progress = poseProgress(input.frame, input.duration);
  const fromX = unit(input.fromX, DEFAULT_FROM_X);
  const toX = unit(input.toX, action === 'walk' || action === 'enter' ? DEFAULT_TO_X : fromX);
  const vehicle = action === 'drive' ? vehicleMotion(progress, input.fromX, input.toX) : null;
  const swing = action === 'walk' || action === 'enter' ? swingOf(progress, fps, input.duration) : 0;
  const entering = action === 'enter' && progress < DOOR_PROGRESS;

  let pelvisX = fromX * WORLD_W + (toX - fromX) * WORLD_W * progress;
  let facing: 1 | -1 = toX >= fromX ? 1 : -1;
  if (action === 'drive' && vehicle) {
    pelvisX = vehicle.riderPelvis.x;
    const {start, end} = driveEnds(input.fromX, input.toX);
    facing = end >= start ? 1 : -1;
  } else if (action === 'enter') {
    if (entering) {
      const start = fromX * WORLD_W;
      const goal = EXTERIOR_DOOR_X - 12;
      pelvisX = start + (goal - start) * (progress / DOOR_PROGRESS);
      facing = goal >= start ? 1 : -1;
    } else {
      pelvisX = INTERIOR_STAND_X;
      facing = 1;
    }
  } else if (action === 'console') {
    pelvisX = CONSOLE_STAND_X;
    facing = 1;
  }

  const bob = action === 'walk' || entering ? Math.abs(swing) * 0.6 : action === 'react'
    ? Math.sin((Number.isFinite(input.frame) ? input.frame : 0) / fps * 10) * 0.4 : 0;
  const pelvisY = action === 'drive' && vehicle ? vehicle.riderPelvis.y : GROUND_Y - STAND_DROP + bob;
  const pelvis = {x: pelvisX, y: pelvisY};
  const lean = action === 'react' ? -0.22 : action === 'walk' || entering ? swing * 0.05 : action === 'drive' ? 0.08 : 0;
  const torsoLen = BONE.torso;
  const neck = {x: pelvis.x + Math.sin(lean) * torsoLen, y: pelvis.y - Math.cos(lean) * torsoLen};
  const spine = {x: (pelvis.x + neck.x) / 2, y: (pelvis.y + neck.y) / 2};
  const head = {x: neck.x + Math.sin(lean) * 4, y: neck.y - Math.cos(lean) * 5};
  const shoulderY = neck.y + 2;
  const leftShoulder = {x: neck.x - BONE.shoulder, y: shoulderY};
  const rightShoulder = {x: neck.x + BONE.shoulder, y: shoulderY};
  const leftHip = {x: pelvis.x - BONE.hip, y: pelvis.y};
  const rightHip = {x: pelvis.x + BONE.hip, y: pelvis.y};

  const stride = 7;
  let leftTarget: Vec2;
  let rightTarget: Vec2;
  if (action === 'drive') {
    leftTarget = {x: pelvis.x + 6, y: GROUND_Y - 9};
    rightTarget = {x: pelvis.x + 14, y: GROUND_Y - 9};
  } else {
    const step = action === 'walk' || entering ? swing : 0;
    leftTarget = plant(leftHip, leftHip.x - 3 - step * stride);
    rightTarget = plant(rightHip, rightHip.x + 3 + step * stride);
  }
  const leftLeg = solve2Bone(leftHip, leftTarget, BONE.thigh, BONE.shin, leftTarget.x >= leftHip.x ? 1 : -1);
  const rightLeg = solve2Bone(rightHip, rightTarget, BONE.thigh, BONE.shin, rightTarget.x >= rightHip.x ? 1 : -1);
  const leftFoot = action === 'drive' ? leftLeg.end : {x: leftTarget.x, y: GROUND_Y};
  const rightFoot = action === 'drive' ? rightLeg.end : {x: rightTarget.x, y: GROUND_Y};

  const talk = Math.sin((Number.isFinite(input.frame) ? input.frame : 0) / fps * 3);
  const armTarget = (side: 'left' | 'right'): Vec2 => {
    const shoulder = side === 'left' ? leftShoulder : rightShoulder;
    const dir = side === 'left' ? -1 : 1;
    if (action === 'console' && side === 'right') return {x: CONSOLE_ANCHOR.x, y: CONSOLE_ANCHOR.y};
    if (action === 'work' && side === 'right') return {x: pelvis.x + 18, y: pelvis.y - 2};
    if (action === 'point' && side === 'right') return {x: pelvis.x + 26, y: pelvis.y - 12};
    if (action === 'carry') return {x: pelvis.x + 11, y: pelvis.y - 8};
    if (action === 'react') return {x: shoulder.x + dir * 6, y: shoulder.y - 8};
    if (action === 'talk' && side === 'right') return {x: shoulder.x + 8, y: shoulder.y + 8 + talk * 2};
    if (action === 'drive') return {x: pelvis.x + 12 + dir * 3, y: pelvis.y - 2};
    if (action === 'walk' || entering) {
      const oppose = side === 'left' ? swing : -swing;
      return {x: shoulder.x + dir * 2 + oppose * 8, y: shoulder.y + 16};
    }
    return {x: shoulder.x + dir * 3, y: shoulder.y + 18};
  };
  const leftArmChain = solve2Bone(leftShoulder, armTarget('left'), BONE.upperArm, BONE.forearm, -1);
  const rightArmChain = solve2Bone(rightShoulder, armTarget('right'), BONE.upperArm, BONE.forearm, 1);
  const rightHand = action === 'console' ? {x: CONSOLE_ANCHOR.x, y: CONSOLE_ANCHOR.y} : rightArmChain.end;
  const leftHand = leftArmChain.end;

  const built: Pose = {
    action,
    progress,
    facing,
    groundY: GROUND_Y,
    pelvis,
    spine,
    neck,
    head,
    leftShoulder,
    rightShoulder,
    leftElbow: leftArmChain.joint,
    rightElbow: rightArmChain.joint,
    leftHand,
    rightHand,
    hand: action === 'carry' ? {x: (leftHand.x + rightHand.x) / 2, y: (leftHand.y + rightHand.y) / 2} : rightHand,
    leftHip,
    rightHip,
    leftKnee: leftLeg.joint,
    rightKnee: rightLeg.joint,
    leftAnkle: leftFoot,
    rightAnkle: rightFoot,
    leftFoot,
    rightFoot,
    leftThigh: leftLeg.angle1,
    rightThigh: rightLeg.angle1,
    leftShin: leftLeg.angle2,
    rightShin: rightLeg.angle2,
    leftArm: leftArmChain.angle1,
    rightArm: rightArmChain.angle1,
    leftForearm: leftArmChain.angle2,
    rightForearm: rightArmChain.angle2,
    lean,
    consoleAnchor: {x: CONSOLE_ANCHOR.x, y: CONSOLE_ANCHOR.y},
    vehicle,
  };
  return built;
};
