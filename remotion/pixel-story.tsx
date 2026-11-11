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
  return <AbsoluteFill style={{background: '#172236'}}>
    <svg width={width} height={height} viewBox="0 0 320 180" preserveAspectRatio="xMidYMid slice" shapeRendering="crispEdges">
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
      />
    </svg>
  </AbsoluteFill>;
};
