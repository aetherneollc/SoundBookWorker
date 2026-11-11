import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import type {CartoonPlan} from './cartoon';
import {PixelWorld} from './game/scene';
import {frameRoom, pose, poseProgress, resolveWorld, vehicleMotion, type PoseAction} from './game/pose';

type Cue = {from: number; to: number; value: string; speaker: string; scene_number: number};
type PixelShot = {
  src?: string;
  from: number;
  duration: number;
  index: number;
  scene_number?: number;
  scene_title?: string;
  location?: string;
  description?: string;
  cartoon: CartoonPlan;
  cartoon_phase?: number;
  rover?: {action: string; speaker: string};
};

// Subtitles stay in video.tsx. actor.body and shot.src are ignored: the cast is
// drawn from the limb atlas, not from a generated portrait.
export const PixelStoryShot = ({shot, width, height, speech}: {shot: PixelShot; width: number; height: number; speech: Cue[]}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = poseProgress(frame, shot.duration);
  const world = resolveWorld(shot.cartoon.world);
  const people = shot.cartoon.actors.filter(actor => !/阿拓|AT-1/i.test(actor.name));
  const cast = people.map(actor => ({
    name: actor.name,
    speaking: speech.some(cue => cue.scene_number === shot.scene_number && cue.speaker === actor.name
      && cue.value !== 'X' && cue.value !== 'A' && shot.from + frame >= cue.from && shot.from + frame < cue.to),
    pose: pose({
      action: actor.action,
      frame,
      fps,
      duration: shot.duration,
      fromX: actor.from_x,
      toX: actor.to_x,
    }),
  }));
  const actions: PoseAction[] = people.map(actor => actor.action);
  const driver = cast.find(member => member.pose.action === 'drive');
  const driving = Boolean(driver) || shot.rover?.action === 'drive';
  const vehicle = driver?.pose.vehicle ?? (driving ? vehicleMotion(progress) : null);
  const place = `${shot.location || ''} ${shot.scene_title || ''} ${shot.description || ''}`;
  const controlCutaway = /整流罩旁/.test(shot.location || '') && /控制室|control room/i.test(shot.description || '');
  const setpiece = controlCutaway ? 'generic' : /热真空|真空试验|thermal.vacuum/i.test(place) ? 'vacuum'
    : /振动|vibration/i.test(place) ? 'vibration'
    : /整流罩|fairing/i.test(place) ? 'fairing'
    : /集成|装配|assembly|integration/i.test(place) ? 'assembly' : 'generic';
  const roverVisible = Boolean(shot.rover || vehicle || /阿拓|AT-1|机器人|月球车|探测车|rover/i.test(place));
  const roverAnchor = vehicle?.x ?? (setpiece === 'generic' ? 100 : setpiece === 'fairing' ? 226 : 233);
  const focus = shot.cartoon.focus;
  const cameraEase = Math.min(1, frame / Math.max(1, fps * 0.8));
  const actorWidths = [320, 275, 210];
  const targetWidth = focus === 'wide' ? 320 : focus === 'actor' ? actorWidths[shot.index % actorWidths.length]
    : focus === 'prop' ? 250 : 260;
  const cameraWidth = 320 - (320 - targetWidth) * cameraEase;
  const cameraHeight = cameraWidth * 9 / 16;
  const targetX = focus === 'actor' ? cast[0]?.pose.pelvis.x ?? 160
    : focus === 'robot' ? roverAnchor : focus === 'prop' ? 220 : 160;
  const cameraX = Math.max(0, Math.min(320 - cameraWidth, targetX - cameraWidth / 2));
  const cameraY = Math.max(0, 180 - cameraHeight - 7);
  return <AbsoluteFill style={{background: '#172236'}}>
    <svg width={width} height={height} viewBox={`${cameraX} ${cameraY} ${cameraWidth} ${cameraHeight}`} preserveAspectRatio="xMidYMid slice" shapeRendering="crispEdges">
      <PixelWorld
        room={frameRoom(actions, progress, shot.cartoon.world)}
        world={world}
        frame={frame}
        fps={fps}
        progress={progress}
        vehicle={vehicle}
        roverAction={shot.rover?.action || 'idle'}
        prop={shot.cartoon.prop}
        cast={cast}
        entering={actions.includes('enter')}
        setpiece={setpiece}
        roverVisible={roverVisible}
      />
    </svg>
  </AbsoluteFill>;
};
