import type {CartoonPlan} from '../cartoon';
import type {SpriteCell} from './atlas';
import {limbAtlas, paletteFor} from './atlas';
import {
  CONSOLE_ANCHOR, DOOR_PROGRESS, EXTERIOR_DOOR_X, GROUND_Y, INTERIOR_DOOR_X, ROVER_PARK_X,
  dustMotes, rainDrops, type Light, type Pose, type Room, type VehicleMotion, type WorldState,
} from './pose';

export type CastMember = {name: string; pose: Pose; speaking: boolean; body?: string};

const Px = ({x, y, w, h, fill, opacity = 1}: {x: number; y: number; w: number; h: number; fill: string; opacity?: number}) =>
  <rect x={x} y={y} width={w} height={h} fill={fill} opacity={opacity} />;

const Glyph = ({cell, x, y, rotation = 0, anchor}: {cell: SpriteCell; x: number; y: number; rotation?: number; anchor: 'top' | 'bottom'}) => {
  const deg = rotation * 180 / Math.PI * (anchor === 'top' ? -1 : 1);
  const originY = anchor === 'bottom' ? -cell.h : -cell.pivotY;
  return <g transform={`translate(${Math.round(x)} ${Math.round(y)}) rotate(${deg}) translate(${-cell.pivotX} ${originY})`}>
    {cell.pixels.map((color, index) => color
      ? <rect key={index} x={index % cell.w} y={Math.floor(index / cell.w)} width={1} height={1} fill={color} />
      : null)}
  </g>;
};

const Exterior = ({world, doorOpen}: {world: WorldState; doorOpen: boolean}) => {
  const night = world.light === 'night';
  const dust = world.weather === 'dust';
  const rain = world.weather === 'rain';
  const sky = night ? '#141a2e' : rain ? '#60788a' : dust ? '#c4a36c' : '#7eacd0';
  const hill = night ? '#243044' : dust ? '#a68458' : '#8ea4b4';
  const yard = night ? '#2c3324' : rain ? '#44503a' : dust ? '#a98450' : '#7c8c48';
  const dirt = night ? '#241c16' : rain ? '#3e342c' : dust ? '#8d6840' : '#6d583c';
  return <g>
    <Px x={0} y={0} w={320} h={180} fill={sky} />
    {night ? <>
      <Px x={40} y={18} w={2} h={2} fill="#d9e3d5" />
      <Px x={96} y={28} w={1} h={1} fill="#d9e3d5" />
      <Px x={150} y={14} w={2} h={2} fill="#d9e3d5" />
      <Px x={188} y={24} w={1} h={1} fill="#d9e3d5" />
      <Px x={300} y={16} w={8} h={8} fill="#e4e6cb" />
      <Px x={302} y={18} w={3} h={3} fill="#c5cbb8" />
    </> : <>
      <Px x={26} y={14} w={12} h={12} fill="#efb867" />
      <Px x={30} y={12} w={4} h={2} fill="#efb867" />
      <Px x={30} y={26} w={4} h={2} fill="#efb867" />
      <Px x={22} y={18} w={2} h={4} fill="#efb867" />
      <Px x={40} y={18} w={2} h={4} fill="#efb867" />
    </>}
    <Px x={0} y={96} w={320} h={22} fill={hill} />
    <Px x={0} y={118} w={320} h={GROUND_Y - 118} fill={yard} />
    <Px x={0} y={GROUND_Y} w={320} h={30} fill={dirt} />
    <Px x={0} y={GROUND_Y} w={320} h={1} fill="#172236" />
    <Px x={214} y={70} w={106} h={GROUND_Y - 70} fill={night ? '#3a332c' : '#8d7058'} />
    <Px x={214} y={70} w={106} h={6} fill={night ? '#2a241e' : '#6e5844'} />
    <Px x={226} y={84} w={16} h={12} fill={night ? '#efb867' : '#d7e4ea'} />
    <Px x={280} y={84} w={16} h={12} fill={night ? '#1a2430' : '#d7e4ea'} />
    <Px x={EXTERIOR_DOOR_X - 11} y={GROUND_Y - 36} w={22} h={36} fill="#1a2430" />
    <Px x={EXTERIOR_DOOR_X - 9 + (doorOpen ? -14 : 0)} y={GROUND_Y - 34} w={18} h={34} fill={doorOpen ? '#314238' : '#6a4a38'} />
    <Px x={EXTERIOR_DOOR_X - 3} y={GROUND_Y - 16} w={2} h={2} fill="#efb867" />
    <Px x={36} y={134} w={18} h={10} fill="#6a5338" />
    <Px x={38} y={130} w={14} h={4} fill="#c48a52" />
  </g>;
};

const outsideColor = (world: WorldState) => world.light === 'night' ? '#141a2e'
  : world.weather === 'rain' ? '#60788a' : world.weather === 'dust' ? '#c4a36c' : '#7eacd0';

const Interior = ({world}: {world: WorldState}) => {
  const night = world.light === 'night';
  const wall = night ? '#1c2630' : '#4a5d6e';
  const wallDark = night ? '#141b22' : '#31414e';
  const floor = night ? '#1a2118' : '#3e4a40';
  const outside = outsideColor(world);
  return <g>
    <Px x={0} y={0} w={320} h={180} fill={wall} />
    <Px x={0} y={0} w={320} h={10} fill={wallDark} />
    <Px x={0} y={10} w={320} h={3} fill="#85c9c2" />
    {[18, 78, 138, 250, 300].map(x => <Px key={x} x={x} y={14} w={4} h={GROUND_Y - 14} fill={wallDark} />)}
    <Px x={200} y={28} w={78} h={42} fill="#172236" />
    <Px x={204} y={32} w={70} h={34} fill={outside} />
    <Px x={238} y={32} w={2} h={34} fill="#172236" />
    <Px x={204} y={48} w={70} h={2} fill="#172236" />
    <Px x={INTERIOR_DOOR_X - 4} y={GROUND_Y - 40} w={26} h={40} fill="#172236" />
    <Px x={INTERIOR_DOOR_X} y={GROUND_Y - 36} w={16} h={36} fill={outside} />
    <Px x={INTERIOR_DOOR_X + 14} y={GROUND_Y - 34} w={10} h={34} fill="#6a4a38" />
    <Px x={0} y={GROUND_Y} w={320} h={30} fill={floor} />
    <Px x={0} y={GROUND_Y} w={320} h={1} fill="#d9e3d5" />
    {[0, 1, 2, 3, 4, 5, 6].map(i => <Px key={i} x={i * 46 + 8} y={164} w={28} h={2} fill="#2c3a34" />)}
    <Px x={148} y={16} w={10} h={4} fill="#efb867" />
    <Px x={151} y={20} w={4} h={8} fill="#172236" />
  </g>;
};

const ConsoleDesk = () => {
  const x = CONSOLE_ANCHOR.x;
  const y = CONSOLE_ANCHOR.y;
  return <g>
    <Px x={x - 18} y={y - 10} w={36} h={22} fill="#172236" />
    <Px x={x - 16} y={y - 8} w={32} h={16} fill="#1b3c4c" />
    <Px x={x - 12} y={y - 4} w={16} h={3} fill="#85c9c2" />
    <Px x={x + 6} y={y - 4} w={5} h={3} fill="#efb867" />
    <Px x={x - 14} y={y + 12} w={4} h={GROUND_Y - (y + 12)} fill="#172236" />
    <Px x={x + 10} y={y + 12} w={4} h={GROUND_Y - (y + 12)} fill="#172236" />
  </g>;
};

type Setpiece = 'generic' | 'vacuum' | 'vibration' | 'fairing' | 'assembly';

const EngineeringSetpiece = ({kind, frame}: {kind: Setpiece; frame: number}) => {
  if (kind === 'vacuum') return <g>
    <Px x={178} y={46} w={112} h={100} fill="#182633" />
    <Px x={184} y={52} w={100} h={88} fill="#a4bac3" />
    <Px x={193} y={61} w={82} h={70} fill="#263f50" />
    <Px x={200} y={68} w={68} h={54} fill="#628495" />
    <Px x={206} y={75} w={56} h={40} fill="#172a3a" />
    {[61, 85, 109, 133].map(y => <g key={y}><Px x={187} y={y} w={4} h={4} fill="#efb867" /><Px x={277} y={y} w={4} h={4} fill="#efb867" /></g>)}
    <Px x={202} y={143} w={64} h={4} fill="#8bb7ba" />
  </g>;
  if (kind === 'vibration') return <g>
    <Px x={182} y={100} w={102} h={10} fill="#162938" />
    <Px x={190} y={110} w={86} h={22} fill="#66889b" />
    {[194, 216, 238, 260].map(x => <Px key={x} x={x} y={132} w={8} h={16} fill="#dfac58" />)}
    <Px x={177} y={146} w={112} h={5} fill="#243747" />
    <Px x={180} y={40} w={5} h={60} fill="#93abb2" /><Px x={281} y={40} w={5} h={60} fill="#93abb2" />
    <Px x={180} y={40} w={106} h={6} fill="#93abb2" />
    <Px x={217} y={49} w={32} h={10} fill={Math.floor(frame / 12) % 2 ? '#efb867' : '#8bc8c0'} />
  </g>;
  if (kind === 'assembly') return <g>
    <Px x={181} y={24} w={6} h={123} fill="#76909c" /><Px x={283} y={24} w={6} h={123} fill="#76909c" />
    <Px x={181} y={24} w={108} h={8} fill="#a5bac0" />
    <Px x={223} y={32} w={3} h={42} fill="#e1ae5d" /><Px x={217} y={72} w={15} h={8} fill="#e1ae5d" />
    <Px x={193} y={124} w={82} h={7} fill="#344b59" />
    <Px x={199} y={131} w={6} h={17} fill="#67818d" /><Px x={263} y={131} w={6} h={17} fill="#67818d" />
    <Px x={195} y={40} w={30} h={18} fill="#1c3444" /><Px x={199} y={44} w={22} h={10} fill="#75b5ba" />
  </g>;
  if (kind === 'fairing') return <g>
    <Px x={227} y={44} w={44} h={104} fill="#d4dcd8" />
    <Px x={232} y={47} w={34} h={98} fill="#f0eee3" />
    <Px x={248} y={47} w={2} h={98} fill="#7596a4" />
    <path d="M227 44 Q229 20 249 10 Q269 20 271 44" fill="#f0eee3" stroke="#7596a4" strokeWidth="2" />
    <Px x={222} y={145} w={54} h={5} fill="#334855" />
    <Px x={205} y={85} w={18} h={4} fill="#e6ae54" /><Px x={273} y={85} w={18} h={4} fill="#e6ae54" />
  </g>;
  return null;
};

const RoverSprite = ({x, y, frame, fps, action, moving}: {x: number; y: number; frame: number; fps: number; action: string; moving: boolean}) => {
  const spin = moving ? frame * 22 : 0;
  const reach = action === 'reach' || action === 'sample';
  const wave = Math.sin(frame / Math.max(1, fps) * (reach ? 3 : 1.5));
  const arm = reach ? -34 + wave * 14 : -14 + wave * 8;
  const elbow = reach ? 28 + Math.sin(frame / Math.max(1, fps) * 2.1) * 8 : 10;
  const blink = action === 'signal' && frame % 16 < 8;
  const tilt = !moving && action === 'fall' ? Math.min(14, frame / Math.max(1, fps) * 9)
    : !moving && action === 'recover' ? Math.max(0, 14 - frame / Math.max(1, fps) * 7) : 0;
  return <g transform={`translate(${Math.round(x)} ${Math.round(y)}) rotate(${tilt})`}>
    <ellipse cx={0} cy={0} rx={26} ry={2.2} fill="#10202d" opacity={.35} />
    {[-17, 17].map(wx => <g key={wx} transform={`translate(${wx} -7)`}>
      <circle r={7.4} fill="#182633" stroke="#7f9dad" strokeWidth={1.4} />
      <circle r={4.8} fill="#394e61" stroke="#cadbdc" strokeWidth={1} />
      <g transform={`rotate(${spin})`}><path d="M0 -3.5V3.5M-3.5 0H3.5" stroke="#e8ece6" strokeWidth={1.3} /></g>
      <circle r={1.7} fill="#162b38" />
    </g>)}
    <path d="M-25 -20L-18 -26H18L25 -20L23 -12H-23Z" fill="#edf2ed" stroke="#233b4a" strokeWidth={1.7} />
    <path d="M-21 -20H21L18 -15H-18Z" fill="#d4e2e6" />
    <path d="M-17 -13H17" stroke="#ee9e48" strokeWidth={1.5} />
    <path d="M-12 -26L-9 -34H8L12 -26" fill="#2e5065" stroke="#233b4a" strokeWidth={1.2} />
    <path d="M-10 -34H10L8 -39H-8Z" fill="#cddfe2" stroke="#203c4b" strokeWidth={1.2} />
    <path d="M-5 -38H5" stroke={blink ? '#f3b45d' : '#78d4e0'} strokeWidth={2} strokeLinecap="round" />
    <path d="M-22 -22L-28 -24M22 -22L28 -24" stroke="#a9c8ce" strokeWidth={1.8} strokeLinecap="round" />
    <g transform={`translate(22 -23) rotate(${arm})`}>
      <path d="M0 0L3 13" stroke="#203d4c" strokeWidth={5} strokeLinecap="round" />
      <path d="M0 0L3 13" stroke="#e1e9e5" strokeWidth={2.4} strokeLinecap="round" />
      <g transform={`translate(3 13) rotate(${elbow})`}>
        <circle r={2.5} fill="#eda65c" stroke="#203d4c" strokeWidth={1} />
        <path d="M0 0L-2 11" stroke="#d8e5e6" strokeWidth={3.7} strokeLinecap="round" />
        <path d="M-2 11L-6 13M-2 11L2 14" stroke="#38576b" strokeWidth={1.5} strokeLinecap="round" />
      </g>
    </g>
    {moving && [0, 1, 2, 3].map(i => <Px key={i} x={-30 - ((frame * 3 + i * 6) % 16)} y={-5 - (i % 3)} w={2} h={1} fill="#c2b090" />)}
  </g>;
};

const Held = ({body, prop}: {body: Pose; prop?: CartoonPlan['prop']}) => {
  if (body.action === 'carry') {
    const fill = prop === 'battery' ? '#efb867' : prop === 'sensor' ? '#85c9c2' : prop === 'tool' ? '#d9e3d5' : '#c48a52';
    return <g>
      <Px x={body.hand.x - 7} y={body.hand.y - 6} w={14} h={12} fill="#172236" />
      <Px x={body.hand.x - 6} y={body.hand.y - 5} w={12} h={10} fill={fill} />
    </g>;
  }
  if (body.action === 'work') return <g>
    <Px x={body.hand.x - 1} y={body.hand.y} w={10} h={2} fill="#d9e3d5" />
    <Px x={body.hand.x + 7} y={body.hand.y - 3} w={2} h={6} fill="#526b83" />
  </g>;
  if (body.action === 'point') return <Px x={body.hand.x + 1} y={body.hand.y - 1} w={7} h={2} fill="#efb867" />;
  return null;
};

const IllustratedCharacter = ({member, frame, fps, index}: {member: CastMember; frame: number; fps: number; index: number}) => {
  const {pose: body} = member;
  const walk = body.action === 'walk' || body.action === 'enter';
  const expressive = member.speaking || body.action === 'talk' || body.action === 'point';
  const stride = walk ? Math.sin(frame / Math.max(1, fps) * 9 + index) : 0;
  const bob = walk ? Math.abs(stride) * .9 : Math.sin(frame / Math.max(1, fps) * (expressive ? 3.1 : 1.7) + index) * (expressive ? 1.1 : .35);
  const sway = body.action === 'react' ? Math.sin(frame / Math.max(1, fps) * 8) * 3
    : expressive ? Math.sin(frame / Math.max(1, fps) * 3.1 + index) * 2.5 : stride * 1.2;
  const id = `body-${index}`;
  const sheet = <image href={member.body} x={-22} y={-76} width={44} height={76} preserveAspectRatio="xMidYMax meet" />;
  return <g transform={`translate(${Math.round(body.pelvis.x)} ${Math.round(GROUND_Y + bob)}) scale(${body.facing} 1) rotate(${sway})`}>
    <defs>
      <clipPath id={`${id}-top`}><rect x={-22} y={-76} width={44} height={50} /></clipPath>
      <clipPath id={`${id}-left`}><rect x={-22} y={-28} width={22} height={30} /></clipPath>
      <clipPath id={`${id}-right`}><rect x={0} y={-28} width={22} height={30} /></clipPath>
    </defs>
    <ellipse cx={0} cy={1} rx={15} ry={2} fill="#10202d" opacity={.35} />
    <g clipPath={`url(#${id}-left)`} transform={`rotate(${stride * 8} -8 -27)`}>{sheet}</g>
    <g clipPath={`url(#${id}-right)`} transform={`rotate(${-stride * 8} 8 -27)`}>{sheet}</g>
    <g clipPath={`url(#${id}-top)`} transform={expressive ? `rotate(${Math.sin(frame / Math.max(1, fps) * 3.1 + index) * 2} 0 -27)` : undefined}>{sheet}</g>
    {member.speaking && frame % 9 < 5 && <ellipse cx={0} cy={-63} rx={1.5} ry={1} fill="#803d49" opacity={.7} />}
  </g>;
};

const Character = ({member, prop, frame, fps, index}: {member: CastMember; prop?: CartoonPlan['prop']; frame: number; fps: number; index: number}) => {
  if (member.body) return <IllustratedCharacter member={member} frame={frame} fps={fps} index={index} />;
  const {pose: body} = member;
  const atlas = limbAtlas(member.name);
  const limbs = atlas.limbs;
  const colors = paletteFor(member.name);
  const nod = Math.sin(frame / Math.max(1, fps) * 1.6) * 0.06;
  const blink = frame % 96 < 4;
  const talk = member.speaking && frame % 8 < 5;
  const limb = (part: SpriteCell, x: number, y: number, rotation: number) =>
    <Glyph cell={part} x={x} y={y} rotation={rotation} anchor="top" />;
  const boot = (x: number, y: number) => <Glyph cell={limbs.boot} x={x} y={y} anchor="bottom" />;
  const draw = <g>
    <Px x={body.pelvis.x - 8} y={body.leftFoot.y - 1} w={16} h={2} fill="#172236" opacity={.35} />
    {limb(limbs.upperArm, body.leftShoulder.x, body.leftShoulder.y, body.leftArm)}
    {limb(limbs.forearm, body.leftElbow.x, body.leftElbow.y, body.leftForearm)}
    {limb(limbs.hand, body.leftHand.x, body.leftHand.y, body.leftForearm)}
    {limb(limbs.thigh, body.leftHip.x, body.leftHip.y, body.leftThigh)}
    {limb(limbs.shin, body.leftKnee.x, body.leftKnee.y, body.leftShin)}
    {boot(body.leftFoot.x, body.leftFoot.y)}
    <Glyph cell={limbs.torso} x={body.pelvis.x} y={body.pelvis.y} rotation={body.lean} anchor="bottom" />
    <g transform={`translate(${Math.round(body.neck.x)} ${Math.round(body.neck.y)}) rotate(${nod * 180 / Math.PI}) translate(${-limbs.head.pivotX} ${-limbs.head.h})`}>
      {limbs.head.pixels.map((color, index) => color
        ? <rect key={index} x={index % limbs.head.w} y={Math.floor(index / limbs.head.w)} width={1} height={1} fill={color} />
        : null)}
      {blink && <rect x={5} y={3} width={2} height={1} fill={colors.skin} />}
      {talk && <rect x={5} y={6} width={2} height={2} fill={colors.mouth} />}
    </g>
    {limb(limbs.thigh, body.rightHip.x, body.rightHip.y, body.rightThigh)}
    {limb(limbs.shin, body.rightKnee.x, body.rightKnee.y, body.rightShin)}
    {boot(body.rightFoot.x, body.rightFoot.y)}
    {limb(limbs.upperArm, body.rightShoulder.x, body.rightShoulder.y, body.rightArm)}
    {limb(limbs.forearm, body.rightElbow.x, body.rightElbow.y, body.rightForearm)}
    {limb(limbs.hand, body.rightHand.x, body.rightHand.y, body.rightForearm)}
    <Held body={body} prop={prop} />
  </g>;
  if (body.facing > 0) return draw;
  const origin = body.pelvis.x;
  return <g transform={`translate(${origin} 0) scale(-1 1) translate(${-origin} 0)`}>{draw}</g>;
};

const Wash = ({light}: {light: Light}) => {
  if (light === 'day') return null;
  if (light === 'night') return <Px x={0} y={0} w={320} h={180} fill="#050814" opacity={.38} />;
  return <Px x={0} y={0} w={320} h={180} fill="#120e0a" opacity={.10} />;
};

export const PixelWorld = ({
  room, world, frame, fps, progress, vehicle, roverAction, prop, cast, entering, setpiece, roverVisible, background,
}: {
  room: Room;
  world: WorldState;
  frame: number;
  fps: number;
  progress: number;
  vehicle: VehicleMotion | null;
  roverAction: string;
  prop?: CartoonPlan['prop'];
  cast: CastMember[];
  entering: boolean;
  setpiece: Setpiece;
  roverVisible: boolean;
  background?: string;
}) => {
  const doorOpen = entering && progress >= 0.32 && progress < DOOR_PROGRESS;
  const showConsole = room === 'interior' || prop === 'console' || cast.some(member => member.pose.action === 'console');
  const roverX = vehicle ? vehicle.x : setpiece === 'generic' ? ROVER_PARK_X : setpiece === 'fairing' ? 226 : 233;
  const roverY = background || vehicle ? GROUND_Y : setpiece === 'vacuum' ? 123 : setpiece === 'vibration' ? 101 : setpiece === 'assembly' ? 124 : GROUND_Y;
  const showRover = roverVisible || Boolean(vehicle);
  return <g>
    {background ? <g>
      <image href={background} x={0} y={0} width={320} height={180} preserveAspectRatio="xMidYMid slice" />
      <Px x={0} y={0} w={320} h={180} fill="#14263a" opacity={.18} />
      <Px x={0} y={149} w={320} h={31} fill="#142332" opacity={.32} />
    </g> : room === 'interior' ? <Interior world={world} /> : <Exterior world={world} doorOpen={doorOpen} />}
    {!background && <EngineeringSetpiece kind={setpiece} frame={frame} />}
    {!background && showConsole && setpiece === 'generic' && <ConsoleDesk />}
    {showRover && <RoverSprite x={roverX} y={roverY} frame={frame} fps={fps} action={vehicle ? 'drive' : roverAction} moving={Boolean(vehicle)} />}
    {cast.map((member, index) => <Character key={`${member.name}-${index}`} member={member} prop={prop} frame={frame} fps={fps} index={index} />)}
    <Wash light={world.light} />
    {rainDrops(world.weather, frame).map((drop, index) => <Px key={`rain-${index}`} x={drop.x} y={drop.y} w={1} h={3} fill="#d5e7f4" />)}
    {dustMotes(world.weather, frame).map((mote, index) => <Px key={`dust-${index}`} x={mote.x} y={mote.y} w={1} h={1} fill="#e6c98a" />)}
  </g>;
};
