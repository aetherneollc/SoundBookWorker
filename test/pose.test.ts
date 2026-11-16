import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CONSOLE_ANCHOR, GROUND_Y, HAND_REACH_THRESHOLD, WORLD_W,
  frameRoom, pose, rainDrops, roomAt, vehicleMotion,
  type PoseAction,
} from '../remotion/game/pose.ts';

const walk = (frame: number, duration = 48) => pose({
  action: 'walk', frame, duration, fps: 24, fromX: 0.18, toX: 0.55,
});

test('walk mid-shot opposes the thighs, advances the pelvis, and plants both feet', () => {
  const duration = 48;
  const start = walk(0, duration);
  const mid = walk(duration / 2, duration);
  const later = walk(duration - 1, duration);
  assert.ok(mid.leftThigh * mid.rightThigh < 0, `thighs ${mid.leftThigh} and ${mid.rightThigh} should have opposite signs`);
  assert.ok(mid.pelvis.x > start.pelvis.x);
  assert.ok(later.pelvis.x > mid.pelvis.x);
  for (const frame of [0, 6, 12, 24, 36, 47]) {
    const body = walk(frame, duration);
    assert.equal(body.leftFoot.y, GROUND_Y);
    assert.equal(body.rightFoot.y, 150);
  }
  const grounded: PoseAction[] = ['idle', 'walk', 'work', 'point', 'talk', 'react', 'carry', 'console', 'enter'];
  for (const action of grounded) {
    const body = pose({action, frame: 11, duration, fps: 24, fromX: 0.18, toX: 0.55});
    assert.equal(body.leftFoot.y, GROUND_Y, action);
    assert.equal(body.rightFoot.y, GROUND_Y, action);
  }
});

test('enter cuts from exterior to interior at the door, even if the shot already says interior', () => {
  assert.equal(roomAt('enter', 0), 'exterior');
  assert.equal(roomAt('enter', 0.49), 'exterior');
  assert.equal(roomAt('enter', 0.5), 'interior');
  assert.equal(roomAt('enter', 0.51), 'interior');
  assert.equal(roomAt('enter', 1), 'interior');
  const interiorAlready = {room: 'interior' as const, weather: 'clear' as const, light: 'day' as const};
  assert.equal(frameRoom('enter', 0.25, interiorAlready), 'exterior');
  assert.equal(frameRoom('enter', 0.5, interiorAlready), 'interior');
  assert.equal(frameRoom(['walk', 'enter'], 0.1, interiorAlready), 'exterior');
  assert.equal(frameRoom(['enter'], 1, interiorAlready), 'interior');
});

test('drive moves the rover and seats the rider on it', () => {
  const fromX = 0.22;
  const toX = 0.7;
  const parked = vehicleMotion(0, fromX, toX);
  const rolling = vehicleMotion(0.65, fromX, toX);
  assert.notEqual(parked.x, rolling.x);
  assert.ok(rolling.x > parked.x);
  const start = pose({action: 'drive', frame: 0, duration: 48, fps: 24, fromX, toX});
  const mid = pose({action: 'drive', frame: 30, duration: 48, fps: 24, fromX, toX});
  assert.ok(mid.vehicle);
  assert.deepEqual(mid.pelvis, mid.vehicle.riderPelvis);
  assert.deepEqual(mid.vehicle.riderPelvis, mid.vehicle.seat);
  assert.notEqual(mid.pelvis.x, start.pelvis.x);
  assert.notEqual(mid.pelvis.x, fromX * WORLD_W);
  assert.ok(mid.vehicle.x > start.vehicle!.x);
});

test('console plants the hand on the console anchor', () => {
  const body = pose({action: 'console', frame: 12, duration: 48, fps: 24, fromX: 0.18, toX: 0.55});
  const distance = Math.hypot(body.hand.x - CONSOLE_ANCHOR.x, body.hand.y - CONSOLE_ANCHOR.y);
  assert.ok(distance <= HAND_REACH_THRESHOLD);
  assert.equal(distance, 0);
  assert.deepEqual(body.hand, CONSOLE_ANCHOR);
});

test('timed action envelope visibly reaches and retracts the rigged hand', () => {
  const base = {action: 'work' as const, frame: 24, duration: 48, fps: 24, fromX: 0.3, toX: 0.3};
  const ready = pose({...base, interactionProgress: 0});
  const contact = pose({...base, interactionProgress: 1});
  assert.ok(contact.rightHand.x > ready.rightHand.x + 12);
  assert.ok(contact.rightHand.y < ready.rightHand.y - 12);
});

test('rain drops fall and clear weather has none', () => {
  const first = rainDrops('rain', 2);
  const next = rainDrops('rain', 3);
  assert.ok(first.length > 0);
  assert.equal(first.length, next.length);
  assert.ok(first.some((drop, index) => drop.y !== next[index].y));
  assert.deepEqual(rainDrops('clear', 2), []);
  assert.deepEqual(rainDrops('clear', 9), []);
});
