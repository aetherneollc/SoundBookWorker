import {AbsoluteFill, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {CartoonActionEvent, CartoonPlan} from './cartoon';
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
  stage_layout?: CartoonPlan['stage_layout'];
  cartoon_phase?: number;
  rover?: {action: string; speaker: string; body?: string};
};

const actionEnvelope = (event: CartoonActionEvent | undefined, progress: number) => {
  if (!event) return undefined;
  const start = event.start ?? 0.08;
  const contact = event.contact ?? 0.52;
  const end = event.end ?? 0.92;
  if (progress <= start || progress >= end) return 0;
  if (progress < contact) return Math.max(0, Math.min(1, (progress - start) / Math.max(0.01, contact - start)));
  return Math.max(0, Math.min(1, (end - progress) / Math.max(0.01, end - contact)));
};

// Subtitles stay in video.tsx. Illustrated series assets are reused across shots;
// the procedural rig remains a fallback when a free image request fails.
export const PixelStoryShot = ({shot, width, height, speech}: {shot: PixelShot; width: number; height: number; speech: Cue[]}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = poseProgress(frame, shot.duration);
  const world = resolveWorld(shot.cartoon.world);
  const stageLayout = shot.stage_layout || shot.cartoon.stage_layout;
  const people = shot.cartoon.actors.filter(actor => !/阿拓|AT-1/i.test(actor.name));
  const event = shot.cartoon.action_event;
  const actorEvent = event?.subject === 'character' ? event : undefined;
  const interactionProgress = actionEnvelope(actorEvent, progress);
  const eventPoseAction = actorEvent && ['reach', 'press', 'secure', 'inspect'].includes(actorEvent.kind) ? 'work'
    : actorEvent?.kind === 'signal' ? 'point' : undefined;
  const cast = people.map((actor, actorIndex) => ({
    name: actor.name,
    body: actor.body ? staticFile(actor.body) : undefined,
    speaking: speech.some(cue => cue.scene_number === shot.scene_number && cue.speaker === actor.name
      && cue.value !== 'X' && cue.value !== 'A' && shot.from + frame >= cue.from && shot.from + frame < cue.to),
    pose: pose({
      action: actorIndex === 0 && eventPoseAction ? eventPoseAction : actor.action,
      frame,
      fps,
      duration: shot.duration,
      fromX: actor.from_x,
      toX: actor.to_x,
      interactionProgress: actorIndex === 0 ? interactionProgress : undefined,
    }),
    stage: stageLayout?.actors?.[actor.name],
    actorHeight: stageLayout?.dimensions?.actor_height,
    actionEvent: actorIndex === 0 ? actorEvent : undefined,
  }));
  const actions: PoseAction[] = people.map(actor => actor.action);
  const driver = cast.find(member => member.pose.action === 'drive');
  const driving = Boolean(driver) || shot.rover?.action === 'drive';
  const vehicle = driver?.pose.vehicle ?? (driving ? vehicleMotion(progress) : null);
  const actionEvent = event;
  const eventRoverAction = actionEvent?.subject === 'rover'
    ? actionEvent.kind === 'signal' ? 'signal'
      : ['sample', 'reach', 'press', 'secure', 'inspect', 'open', 'close'].includes(actionEvent.kind) ? 'sample'
        : actionEvent.kind === 'move' ? 'drive'
          : actionEvent.kind === 'fall' || actionEvent.kind === 'recover' ? actionEvent.kind : undefined
    : undefined;
  const roverAction = eventRoverAction || (shot.rover?.action && shot.rover.action !== 'idle' ? shot.rover.action : 'idle');
  const place = `${shot.location || ''} ${shot.scene_title || ''} ${shot.description || ''}`;
  const controlCutaway = /整流罩旁/.test(shot.location || '') && /控制室|control room/i.test(shot.description || '');
  const layoutId = stageLayout?.setpiece.id;
  const setpiece = layoutId === 'thermal_vacuum_chamber' ? 'vacuum'
    : layoutId === 'vibration_table' ? 'vibration'
      : layoutId === 'payload_fairing' ? 'fairing'
        : layoutId === 'assembly_station' ? 'assembly'
          : controlCutaway ? 'generic' : /热真空|真空试验|thermal.vacuum/i.test(place) ? 'vacuum'
            : /振动|vibration/i.test(place) ? 'vibration'
              : /整流罩|载荷集成大厅|封舱|fairing/i.test(place) ? 'fairing'
                : /集成|装配|assembly|integration/i.test(place) ? 'assembly' : 'generic';
  const roverVisible = Boolean(shot.rover || vehicle || /阿拓|AT-1|机器人|月球车|探测车|rover/i.test(place));
  const roverAnchor = vehicle?.x ?? (stageLayout?.rover.x ?? (setpiece === 'generic' ? 100 / 320 : setpiece === 'fairing' ? 226 / 320 : 233 / 320)) * 320;
  const focus = shot.cartoon.focus;
  const eventFocus = actionEvent?.subject !== 'environment' && ['reach', 'press', 'secure', 'inspect', 'signal'].includes(actionEvent?.kind || '');
  const cameraEase = Math.min(1, frame / Math.max(1, fps * 0.8));
  const slowDrift = Math.min(1, frame / Math.max(1, shot.duration)) * 10;
  const targetWidth = focus === 'wide' ? eventFocus ? 260 : 320 : focus === 'actor' ? 210
    : focus === 'robot' ? 200 : focus === 'prop' ? 190 : 235;
  const cameraWidth = 320 - (320 - targetWidth) * cameraEase;
  const cameraHeight = cameraWidth * 9 / 16;
  const equipmentX = setpiece === 'vacuum' ? 115 : setpiece === 'fairing' ? 222
    : setpiece === 'vibration' ? 230 : setpiece === 'assembly' ? 205 : 160;
  const targetX = eventFocus && focus === 'wide' ? actionEvent?.subject === 'rover' ? roverAnchor : cast[0]?.pose.pelvis.x ?? 160
    : focus === 'actor' ? cast[0]?.pose.pelvis.x ?? 160
    : focus === 'robot' ? roverAnchor : focus === 'prop' ? equipmentX : 160;
  const cameraX = Math.max(0, Math.min(320 - cameraWidth, targetX - cameraWidth / 2 + (shot.index % 2 ? -slowDrift : slowDrift)));
  const cameraY = Math.max(0, 180 - cameraHeight - 7);
  return <AbsoluteFill style={{background: '#172236'}}>
    <svg width={width} height={height} viewBox={`${cameraX} ${cameraY} ${cameraWidth} ${cameraHeight}`} preserveAspectRatio="xMidYMid slice" shapeRendering={shot.src ? 'geometricPrecision' : 'crispEdges'}>
      <PixelWorld
        room={setpiece !== 'generic' ? 'interior' : frameRoom(actions, progress, shot.cartoon.world)}
        world={world}
        frame={frame}
        fps={fps}
        progress={progress}
        vehicle={vehicle}
        roverAction={roverAction}
        roverBody={shot.rover?.body ? staticFile(shot.rover.body) : undefined}
        prop={shot.cartoon.prop}
        cast={cast}
        entering={actions.includes('enter')}
        setpiece={setpiece}
        roverVisible={roverVisible}
        background={shot.src && setpiece === 'generic' && world.room !== 'interior' ? staticFile(shot.src) : undefined}
        stageLayout={stageLayout}
        actionEvent={shot.cartoon.action_event}
        shotDuration={shot.duration}
      />
    </svg>
  </AbsoluteFill>;
};
