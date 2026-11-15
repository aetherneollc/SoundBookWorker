import assert from 'node:assert/strict';
import test from 'node:test';
import {
  SIDE_SHOE_LIFT_MAX_PX,
  SIDE_STRIDE_DEG,
  TURN_EDGE_SCALE_X,
  aimDegrees,
  contactShadowBackground,
  faceClipsAfterWalk,
  facingAfterTravel,
  feetPlantedForTurn,
  gazeAddress,
  handModesForClip,
  handSizeForForearm,
  insetScreenGlass,
  pointTarget,
  stageRectForPlate,
  resolveSideToFrontTurn,
  shoulderLockScale,
  sideShoeLiftPx,
  sideStrideDegrees,
  sideTogetherDegrees,
} from '../remotion/puppet_motion.ts';

test('side-to-front turn is planted side, one thin edge, then the front', () => {
  const boundary = 30;
  assert.equal(resolveSideToFrontTurn(boundary - 6, boundary, true, true), 'none');
  for (const frame of [25, 26, 27, 28, 29]) {
    const stage = resolveSideToFrontTurn(frame, boundary, true, true);
    assert.equal(stage, 'planted-side');
    assert.equal(feetPlantedForTurn(stage), true);
  }
  assert.equal(resolveSideToFrontTurn(boundary, boundary, true, true), 'edge');
  assert.equal(resolveSideToFrontTurn(boundary + 1, boundary, true, true), 'front');
  assert.equal(resolveSideToFrontTurn(boundary + 2, boundary, true, true), 'front');
  assert.equal(resolveSideToFrontTurn(boundary + 3, boundary, true, true), 'none');
  assert.equal(resolveSideToFrontTurn(boundary, boundary, false, true), 'none');
  assert.ok(TURN_EDGE_SCALE_X < 0.25);
});

test('side stride starts together and keeps the shoe lift at or under 6px', () => {
  assert.ok(SIDE_STRIDE_DEG < 12.5);
  assert.equal(sideStrideDegrees(0), 0);
  assert.equal(sideStrideDegrees(1), SIDE_STRIDE_DEG);
  assert.equal(sideShoeLiftPx(0), 0);
  assert.ok(sideShoeLiftPx(480) <= SIDE_SHOE_LIFT_MAX_PX);
  assert.ok(sideShoeLiftPx(800, 1.2) <= SIDE_SHOE_LIFT_MAX_PX * 1.2);
});

test('rest, talk, and walk draw half-fists on both hands; point and desk stay distinct', () => {
  for (const type of ['idle', 'listen', 'settle', 'talk', 'walk']) {
    assert.deepEqual(handModesForClip(type), {lead: 'fist', back: 'fist'});
  }
  assert.deepEqual(handModesForClip('point'), {lead: 'point', back: 'fist'});
  assert.deepEqual(handModesForClip('console'), {lead: 'desk', back: 'fist'});
  assert.deepEqual(handModesForClip('work'), {lead: 'desk', back: 'fist'});
  const forearm = 80;
  assert.ok(handSizeForForearm(forearm, 'point') > handSizeForForearm(forearm, 'fist'));
  assert.ok(handSizeForForearm(forearm, 'point') <= forearm);
});

test('a pointing finger aims at the gesture target, not straight down', () => {
  const down = aimDegrees(100, 100, 100, 180);
  const towardScreen = aimDegrees(100, 200, 400, 80);
  assert.ok(Math.abs(down) < 1);
  assert.ok(towardScreen < -20);
});

test('after a walk the actor faces the partner or rover', () => {
  const clips = faceClipsAfterWalk([
    {type: 'walk', fromX: 0.2, toX: 0.55, facing: 'right' as const, pose: 'side' as const},
    {type: 'settle', fromX: 0.55, toX: 0.55, facing: 'right' as const},
    {type: 'talk', fromX: 0.55, toX: 0.55, facing: 'right' as const},
    {type: 'point', fromX: 0.55, toX: 0.55, facing: 'right' as const, targetX: 0.7},
  ], 0.32);
  assert.equal(clips[0].facing, 'right');
  assert.equal(clips[1].facing, 'left');
  assert.equal(clips[1].pose, 'three_quarter');
  assert.equal(clips[1].targetX, 0.32);
  assert.equal(clips[2].facing, 'left');
  assert.equal(clips[3].facing, 'right');
  assert.equal(clips[3].targetX, 0.7);
});

test('shoulder lock never stretches a narrower profile', () => {
  assert.equal(shoulderLockScale(0.11, 0.22), 1);
  assert.ok(shoulderLockScale(0.3, 0.2) < 1);
});

test('contact shadow uses the plate floor color when it is provided', () => {
  const tinted = contactShadowBackground([180, 170, 150]);
  assert.match(tinted, /rgba\(68, 65, 57/);
  const plain = contactShadowBackground(undefined);
  assert.match(plain, /rgba\(6, 12, 22/);
});

test('pointing y comes from the screen rectangle', () => {
  const detected = {x: 0.7508, y: 0.3194, w: 0.0961, h: 0.1167};
  const glass = insetScreenGlass(detected);
  const aim = pointTarget(0.42, 0.58, 1280, 720, glass);
  assert.equal(aim.x, (glass.x + glass.w / 2) * 1280);
  assert.equal(aim.y, (glass.y + glass.h / 2) * 720);
  assert.ok(Math.abs(aim.y - 720 * 0.42) > 20);
  assert.ok(glass.x + glass.w < detected.x + detected.w - detected.w * 0.15);
});

test('stage glass stays on the plate when the puppet layer is zoomed', () => {
  const glass = insetScreenGlass({x: 0.7508, y: 0.3194, w: 0.0961, h: 0.1167});
  const stage = stageRectForPlate(glass, 0.58, 0.42, 1.06, 1.34, 0, 0, 1280, 720);
  const appear = (x: number, scale: number, origin: number) => origin + (x - origin) * scale;
  const plateX = appear((glass.x + glass.w / 2) * 1280, 1.06, 0.58 * 1280);
  const stageX = appear((stage.x + stage.w / 2) * 1280, 1.34, 0.58 * 1280);
  assert.ok(Math.abs(plateX - stageX) < 0.5);
  assert.ok(stage.w < glass.w);
});

test('a rover in front is not swallowed by the 0.04 deadzone', () => {
  const missed = gazeAddress(0.02, 1, false);
  assert.equal(missed.headDeg, 0);
  assert.equal(missed.nodDeg, 0);
  const looked = gazeAddress(0.02, 1, true);
  assert.ok(looked.nodDeg < -8);
  assert.equal(facingAfterTravel(0.4, 0.43, 'left'), 'right');
});

test('post-stop facing is not locked by the entrance facing', () => {
  const clips = faceClipsAfterWalk(
    [
      {type: 'walk', fromX: 0.14, toX: 0.46, facing: 'right' as const, pose: 'side' as const},
      {type: 'idle', fromX: 0.46, toX: 0.46, facing: 'right' as const, pose: 'side' as const},
      {type: 'settle', fromX: 0.46, toX: 0.46, facing: 'right' as const},
      {type: 'talk', fromX: 0.46, toX: 0.46, facing: 'right' as const},
    ],
    0.25,
  );
  assert.equal(clips[0].facing, 'right');
  assert.equal(clips[0].pose, 'side');
  assert.equal(clips[1].pose, 'side');
  assert.equal(clips[1].facing, 'right');
  assert.equal(clips[2].facing, 'left');
  assert.equal(clips[2].pose, 'three_quarter');
  assert.equal(clips[3].facing, 'left');
  const close = faceClipsAfterWalk(
    [
      {type: 'walk', fromX: 0.55, toX: 0.4, facing: 'left' as const, pose: 'side' as const},
      {type: 'talk', fromX: 0.4, toX: 0.4, facing: 'left' as const},
    ],
    0.43,
  );
  assert.equal(close[1].facing, 'right');
  assert.equal(close[1].targetX, 0.43);
});

test('side plant angles bring a baked stride together', () => {
  const together = 0.492;
  const hipL = {x: 0.47616, y: 0.48575};
  const footL = {x: 0.43737, y: 0.87609};
  const hipR = {x: 0.56991, y: 0.48817};
  const footR = {x: 0.50849, y: 0.87124};
  const left = sideTogetherDegrees(hipL.x, hipL.y, footL.x, footL.y, together);
  const right = sideTogetherDegrees(hipR.x, hipR.y, footR.x, footR.y, together);
  const land = (hipX: number, hipY: number, ankleX: number, ankleY: number, deg: number) => {
    const rad = (deg * Math.PI) / 180;
    const dx = ankleX - hipX;
    const dy = ankleY - hipY;
    return hipX + dx * Math.cos(rad) - dy * Math.sin(rad);
  };
  const landedL = land(hipL.x, hipL.y, footL.x, footL.y, left);
  const landedR = land(hipR.x, hipR.y, footR.x, footR.y, right);
  assert.ok(Math.abs(landedL - together) < 0.01, `left ankle landed at ${landedL}`);
  assert.ok(Math.abs(landedR - together) < 0.01, `right ankle landed at ${landedR}`);
  assert.ok(Math.abs(landedL - landedR) < 0.015);
  assert.equal(sideStrideDegrees(0), 0);
  const wideL = sideTogetherDegrees(0.45048, 0.48972, 0.27893, 0.8563, 0.49256);
  const wideR = sideTogetherDegrees(0.53464, 0.48972, 0.63175, 0.88786, 0.49256);
  const wideLandL = land(0.45048, 0.48972, 0.27893, 0.8563, wideL);
  const wideLandR = land(0.53464, 0.48972, 0.63175, 0.88786, wideR);
  assert.ok(Math.abs(wideLandL - 0.49256) < 0.01);
  assert.ok(Math.abs(wideLandR - 0.49256) < 0.01);
});
