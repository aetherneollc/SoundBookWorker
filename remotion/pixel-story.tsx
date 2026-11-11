import React from 'react';
import {AbsoluteFill, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {CartoonPlan, CartoonAction} from './cartoon';

type Cue = {from: number; to: number; value: string; speaker: string; scene_number: number};
type PixelShot = {src?: string; from: number; duration: number; index: number; scene_number?: number; scene_title?: string; location?: string;
  description?: string; cartoon: CartoonPlan; cartoon_phase?: number; rover?: {action: string; speaker: string}};
const ink = '#172236', steel = '#526b83', pale = '#d9e3d5', amber = '#efb867', teal = '#85c9c2';
const palette = (name: string) => /林岚|Lin Lan/i.test(name)
  ? {hair: '#202b4c', suit: '#377dad', skin: '#e6b69a', trim: amber}
  : /顾承宇|Gu Chengyu/i.test(name)
    ? {hair: '#303a4d', suit: '#64788b', skin: '#d9a68b', trim: '#ecce8b'}
    : {hair: '#6f6872', suit: '#3d8c8e', skin: '#d8ab93', trim: '#e9946e'};
const R = ({x,y,w,h,fill,opacity=1}: {x:number;y:number;w:number;h:number;fill:string;opacity?:number}) =>
  <rect x={x} y={y} width={w} height={h} fill={fill} opacity={opacity}/>;
const P = ({points,fill}: {points:string;fill:string}) => <polygon points={points} fill={fill}/>;

function GameBackdrop({kind, variant, frame, fps}: {kind: string;variant: number;frame:number;fps:number}) {
  const moon = kind === 'moon', exterior = kind === 'launch', control = kind === 'control';
  if (['vacuum','cleanroom','fairing','vibration','cable'].includes(kind)) {
    const bright=kind==='cleanroom', warning=kind==='vibration';
    const wall=bright?'#7899aa':warning?'#34455c':'#39576c';
    const floor=bright?'#8ba8ae':'#3f5668';
    return <g>
      <R x={0} y={0} w={320} h={180} fill={wall}/>
      <R x={0} y={0} w={320} h={10} fill="#192d42"/>
      <R x={0} y={10} w={320} h={3} fill={bright?'#cedbd0':'#78919b'}/>
      {[24,94,164,234,304].map((x,i)=><g key={i}>
        <R x={x} y={14} w={4} h={109} fill={bright?'#55788c':'#243a50'}/>
        <R x={x+4} y={14} w={2} h={109} fill={bright?'#b8c8c5':'#647f90'}/>
        <R x={x+12} y={17} w={37} h={3} fill={i%2?amber:teal} opacity={.65}/>
      </g>)}
      <R x={0} y={122} w={320} h={58} fill={floor}/><R x={0} y={122} w={320} h={3} fill="#c5c5ad"/>
      {[0,1,2,3].map(i=><g key={i}><R x={i*81+22} y={142} w={47} h={2} fill="#526b7c"/>
        <R x={i*81+45} y={142} w={2} h={38} fill="#526b7c"/></g>)}
      {kind==='vacuum'&&<g>
        <R x={60} y={42} w={89} h={77} fill="#182c42"/><R x={65} y={48} w={79} h={64} fill="#8da7ae"/>
        <R x={70} y={54} w={69} h={51} fill="#243e58"/><R x={82} y={67} w={43} h={26} fill="#526d82"/>
        <R x={178} y={51} w={70} h={51} fill="#1c3349"/><R x={184} y={57} w={58} h={37} fill="#45677a"/>
        <R x={191} y={64} w={43} h={4} fill={frame%(fps*2)<fps?teal:amber}/>
        <R x={190} y={74} w={32} h={2} fill="#a7c5c1"/>
      </g>}
      {kind==='cleanroom'&&<g>
        <R x={26} y={34} w={120} h={74} fill="#c5d6d0"/><R x={31} y={39} w={110} h={64} fill="#a8c0c3"/>
        <R x={47} y={46} w={77} h={46} fill="#7797a6"/><R x={171} y={80} w={100} h={8} fill="#d0d6c8"/>
        <R x={181} y={88} w={5} h={34} fill="#4d687d"/><R x={258} y={88} w={5} h={34} fill="#4d687d"/>
      </g>}
      {kind==='fairing'&&<g>
        <R x={30} y={17} w={8} h={106} fill="#8c9da3"/><R x={282} y={17} w={8} h={106} fill="#8c9da3"/>
        <R x={38} y={21} w={244} h={6} fill="#aeb9ae"/><R x={43} y={31} w={4} h={91} fill="#78909a"/>
        <R x={273} y={31} w={4} h={91} fill="#78909a"/>
        <R x={103} y={45} w={115} h={72} fill="#283f54"/><R x={107} y={49} w={107} h={64} fill="#4f6a7c"/>
        <R x={156} y={49} w={4} h={64} fill="#a2b8b8"/>
      </g>}
      {kind==='vibration'&&<g>
        <R x={30} y={34} w={48} h={57} fill="#20364e"/><R x={35} y={39} w={38} h={32} fill="#3d6576"/>
        <R x={41} y={48} w={21} h={4} fill={frame%20<10?'#e6856a':teal}/>
        <R x={99} y={107} w={172} h={12} fill="#263a4c"/><R x={108} y={110} w={153} h={6} fill="#879a9b"/>
        <R x={117} y={119} w={8} h={12} fill={ink}/><R x={246} y={119} w={8} h={12} fill={ink}/>
      </g>}
      {kind==='cable'&&<g>
        <R x={35} y={37} w={94} h={68} fill="#243d52"/><R x={40} y={42} w={84} h={58} fill="#6b8894"/>
        {[0,1,2].map(i=><path key={i} d={`M ${52+i*18} 51 Q ${67+i*15} 82 ${54+i*18} 98`}
          stroke={i===1?'#e7b06c':'#9fc5be'} strokeWidth="3" fill="none"/>)}
        <R x={192} y={45} w={78} h={58} fill="#253c53"/><R x={198} y={51} w={66} h={46} fill="#52738a"/>
      </g>}
    </g>;
  }
  if (moon) return <g>
    <R x={0} y={0} w={320} h={180} fill="#152541"/><R x={0} y={0} w={320} h={86} fill="#243d60"/>
    <circle cx="261" cy="39" r="16" fill="#e4e6cb"/><circle cx="256" cy="35" r="4" fill="#c1c9bd"/>
    <P points="0,112 53,94 91,102 136,85 190,98 242,88 320,108 320,180 0,180" fill="#8491a0"/>
    <P points="0,132 67,121 123,138 197,119 320,133 320,180 0,180" fill="#adb4ae"/>
    <R x={0} y={151} w={320} h={29} fill="#c5c4b6"/>
    {[34,93,180,261].map((x,i)=><g key={i}><R x={x} y={143+i%2*10} w={16} h={2} fill="#7e8b91"/><R x={x+6} y={145+i%2*10} w={10} h={1} fill="#dce0d1"/></g>)}
  </g>;
  if (exterior) return <g>
    <R x={0} y={0} w={320} h={180} fill="#3d6381"/><R x={0} y={80} w={320} h={74} fill="#c7b9a3"/>
    <P points="0,87 50,70 83,84 169,60 229,81 320,66 320,139 0,139" fill="#899aad"/>
    <R x={30} y={87} w={261} h={59} fill="#647a89"/><R x={36} y={94} w={250} h={47} fill="#8497a0"/>
    <R x={210} y={26} w={7} h={117} fill={ink}/><R x={194} y={48} w={41} h={4} fill={pale}/>
    <R x={0} y={145} w={320} h={35} fill="#485d68"/><R x={0} y={154} w={320} h={2} fill="#8fa8a8"/>
    <R x={71} y={113} w={37} h={25} fill="#364d60"/><R x={77} y={117} w={25} h={12} fill="#d0d5c9"/>
  </g>;
  return <g>
    <R x={0} y={0} w={320} h={180} fill={control ? '#24445b' : '#344e66'}/>
    <R x={0} y={0} w={320} h={12} fill="#162d43"/><R x={0} y={12} w={320} h={4} fill="#627f91"/>
    {[20,105,214,299].map((x,i)=><g key={i}><R x={x} y={15} w={5} h={119} fill="#26394e"/><R x={x+5} y={27} w={2} h={94} fill="#8296a1"/></g>)}
    <R x={31} y={25} w={73} h={46} fill="#182a40"/><R x={36} y={30} w={63} h={36} fill={variant===1?'#4f7894':'#7397a5'}/>
    <P points="37,61 55,48 68,55 84,40 99,54 99,66 37,66" fill="#a8bbc0"/>
    <R x={125} y={24} w={70} h={39} fill="#21364b"/><R x={130} y={29} w={60} h={29} fill={control?'#395b69':'#536b7c'}/>
    {[0,1,2,3].map((i)=><R key={i} x={133+i*13} y={34+(i%2)*7} w={9} h={3} fill={i===2?'#db886a':teal}/>)}
    <R x={0} y={123} w={320} h={57} fill="#34475c"/><R x={0} y={123} w={320} h={3} fill="#a0a6a2"/>
    {[0,1,2,3,4,5,6,7].map(i=><R key={i} x={i*43+variant*7} y={147} w={25} h={2} fill="#50697b"/>)}
    {control ? <g><R x={239} y={73} w={66} h={52} fill="#1b3045"/><R x={244} y={79} w={56} h={29} fill="#315a6a"/>
      <R x={250} y={84} w={18} h={3} fill={frame%(fps*2)<fps?teal:amber}/><R x={249} y={112} w={43} h={5} fill="#85929a"/></g>
      : variant===1 ? <g><R x={230} y={86} w={69} h={36} fill="#24384c"/><R x={234} y={90} w={59} h={4} fill="#b9b8ab"/>
        <R x={235} y={94} w={4} h={28} fill="#8a98a0"/><R x={285} y={94} w={4} h={28} fill="#8a98a0"/></g>
      : <g><R x={223} y={74} w={74} h={48} fill="#253a50"/><R x={229} y={80} w={61} h={35} fill="#465f72"/>
        <R x={236} y={87} w={29} h={16} fill="#5e8c92"/><R x={270} y={89} w={10} h={4} fill={amber}/></g>}
    <R x={0} y={110} w={91} h={10} fill="#1e354a"/><R x={6} y={114} w={83} h={5} fill="#8da2a8"/>
    <R x={13} y={120} w={6} h={28} fill="#253c50"/><R x={78} y={120} w={6} h={28} fill="#253c50"/>
    {variant===2 && <g><R x={31} y={95} w={26} h={14} fill="#bc8c59"/><R x={35} y={91} w={18} h={4} fill="#e0b078"/></g>}
  </g>;
}

function GamePerson({name,x,action,frame,fps,speaking,scale=1}: {name:string;x:number;action:CartoonAction;frame:number;fps:number;speaking:boolean;scale?:number}) {
  const c=palette(name), walking=action==='walk';
  const step=walking?Math.sin(frame/fps*10):0;
  const bob=walking?Math.abs(step)*1.4:Math.sin(frame/fps*2)*.3;
  const arm=action==='point'?-5:action==='work'||action==='console'?-2:action==='react'?2:0;
  return <g transform={`translate(${Math.round(x)} ${Math.round(145-bob)}) scale(${scale})`}>
    <R x={-11} y={-1} w={22} h={2} fill="#172236" opacity={.45}/>
    <g transform={`translate(${-step*2} 0)`}><R x={-7} y={-14} w={6} h={13} fill={ink}/><R x={-6} y={-14} w={4} h={11} fill={c.suit}/><R x={-9} y={-3} w={9} h={3} fill={ink}/></g>
    <g transform={`translate(${step*2} 0)`}><R x={2} y={-14} w={6} h={13} fill={ink}/><R x={3} y={-14} w={4} h={11} fill={c.suit}/><R x={1} y={-3} w={10} h={3} fill={ink}/></g>
    <R x={-9} y={-33} w={18} h={20} fill={ink}/><R x={-7} y={-31} w={14} h={15} fill={c.suit}/>
    <R x={-7} y={-31} w={3} h={15} fill="#7bacc0"/><R x={4} y={-31} w={2} h={15} fill="#244c6d"/>
    <R x={-4} y={-28} w={8} h={2} fill={c.trim}/><R x={-9} y={-17} w={18} h={3} fill="#1d3850"/>
    <R x={-13} y={-31} w={5} h={15} fill={ink}/><R x={-12} y={-30} w={3} h={12} fill={c.suit}/>
    <g transform={`rotate(${arm*8} 9 -29)`}><R x={9} y={-31} w={5} h={15} fill={ink}/><R x={10} y={-30} w={3} h={12} fill={c.suit}/><R x={10} y={-16} w={3} h={3} fill={c.skin}/></g>
    <R x={-7} y={-48} w={14} h={15} fill={ink}/><R x={-5} y={-45} w={10} h={11} fill={c.skin}/>
    <R x={-7} y={-49} w={14} h={6} fill={c.hair}/><R x={-7} y={-44} w={3} h={8} fill={c.hair}/>
    <R x={-2} y={-40} w={2} h={2} fill={ink}/><R x={3} y={-40} w={2} h={2} fill={ink}/>
    {speaking&&frame%8<4&&<R x={0} y={-36} w={3} h={2} fill="#954f55"/>}
    {action==='work'&&frame%16<8&&<><R x={15} y={-18} w={2} h={2} fill={amber}/><R x={18} y={-22} w={1} h={1} fill={pale}/></>}
  </g>;
}

function GameRover({action,frame,fps,centerX=234}: {action:string;frame:number;fps:number;centerX?:number}) {
  const moving=action==='drive', fall=action==='fall', recover=action==='recover';
  const x=centerX+(moving?Math.sin(frame/fps*1.5)*13:0), angle=fall?Math.min(1,frame/(fps*1.4))*18:recover?18-Math.min(1,frame/(fps*1.4))*18:0;
  return <g transform={`translate(${x} 137) rotate(${angle})`}>
    <R x={-31} y={-8} w={61} h={13} fill={ink}/><R x={-28} y={-6} w={55} h={9} fill="#cbd2c9"/>
    <R x={-15} y={-18} w={32} h={10} fill={ink}/><R x={-13} y={-16} w={28} h={7} fill="#90b8b5"/>
    <R x={-6} y={-27} w={14} h={9} fill={ink}/><R x={-4} y={-25} w={10} h={5} fill={frame%20<10?teal:amber}/>
    <R x={-24} y={3} w={11} h={10} fill={ink}/><R x={15} y={3} w={11} h={10} fill={ink}/>
    <R x={-22} y={5} w={7} h={6} fill={steel}/><R x={17} y={5} w={7} h={6} fill={steel}/>
    <R x={moving&&frame%12<6?-19:-22} y={7} w={2} h={2} fill={pale}/>
    <R x={moving&&frame%12<6?20:17} y={7} w={2} h={2} fill={pale}/>
    <g transform={`rotate(${action==='reach'||action==='sample'?-28+Math.sin(frame/fps*4)*13:0} 18 -19)`}>
      <R x={17} y={-21} w={4} h={15} fill={ink}/><R x={20} y={-24} w={14} h={4} fill={ink}/>
      <R x={20} y={-22} w={11} h={2} fill={amber}/>
    </g>
    {action==='signal'&&frame%18<9&&<><R x={-3} y={-31} w={6} h={3} fill={amber}/><R x={-6} y={-34} w={12} h={1} fill={amber}/></>}
  </g>;
}

function GameInteraction({x,action,prop,frame}: {x:number;action:CartoonAction;prop:CartoonPlan['prop'];frame:number}) {
  if (action==='work'||action==='console') return <g>
    <R x={x+11} y={111+frame%3} w={12} h={2} fill={ink}/>
    <R x={x+20} y={110+frame%3} w={4} h={3} fill="#c4d2c9"/>
    {frame%12<6&&<><R x={x+24} y={107} w={2} h={2} fill={amber}/><R x={x+27} y={104} w={1} h={1} fill={pale}/></>}
  </g>;
  if (action==='carry') return <g>
    <R x={x+10} y={108} w={16} h={13} fill={ink}/><R x={x+12} y={110} w={12} h={9} fill={prop==='battery'?amber:teal}/>
  </g>;
  if (action==='point') return <g><R x={x+11} y={109} w={16} h={3} fill={ink}/><R x={x+25} y={107} w={4} h={7} fill={pale}/></g>;
  if (action==='react'&&frame%16<8) return <g><R x={x-16} y={91} w={2} h={6} fill={amber}/><R x={x-16} y={99} w={2} h={2} fill={amber}/></g>;
  return null;
}

// The generated image is scenery. Engineering objects that carry the plot are
// deterministic sprites so their state actually changes during each shot.
function EngineeringSetpiece({title,frame,fps,phase}: {title:string;frame:number;fps:number;phase:number}) {
  const t=Math.min(1,frame/(fps*2.5));
  const fairing=/整流罩|封舱|包络/.test(title), vibration=/振动|第二轮/.test(title);
  const hinge=/线|折/.test(title), lock=/锁销|一只眼/.test(title);
  const vacuum=/故障|密封/.test(title);
  const panelAngle=hinge||lock ? (phase ? -65*t : -10-35*t) : -16;
  const shake=vibration&&frame<fps*3?Math.sin(frame*1.9)*2:0;
  return <g transform={`translate(${shake} 0)`}>
    {fairing&&<g opacity={.9}>
      <R x={177} y={40} w={3} h={109} fill={amber}/><R x={300} y={40} w={3} h={109} fill={amber}/>
      <R x={180} y={39} w={120} h={3} fill={amber}/><R x={180} y={145} w={120} h={3} fill={amber}/>
      {phase>0&&<><R x={180} y={36} w={5} h={111} fill="#b7c8cd" opacity={.75}/><R x={298} y={36} w={5} h={111} fill="#b7c8cd" opacity={.75}/></>}
    </g>}
    {vacuum&&<g><R x={108} y={85} w={98} h={55} fill={ink}/><R x={112} y={89} w={90} h={47} fill="#8399a1"/>
      <R x={120} y={96} w={70} h={29} fill="#243d55"/><R x={132} y={131} w={48} h={3} fill={frame%30<15?"#e4806b":teal}/>
      <R x={135} y={140} w={43} h={4} fill={amber}/></g>}
    {vibration&&<g><R x={193} y={141} w={84} h={7} fill={ink}/><R x={198} y={144} w={74} h={3} fill="#92a6a7"/>
      <R x={201} y={148} w={5} h={8} fill={steel}/><R x={265} y={148} w={5} h={8} fill={steel}/></g>}
    {(hinge||lock||fairing||vibration)&&<g>
      <g transform={`rotate(${panelAngle} 234 112)`}><R x={233} y={96} w={56} h={15} fill={ink}/>
        <R x={236} y={99} w={50} h={9} fill="#567c91"/>{[0,1,2].map(i=><R key={i} x={248+i*12} y={100} w={2} h={7} fill="#a7c6c0"/>)}</g>
      {lock&&<><R x={231} y={108} w={7} h={9} fill={amber}/><R x={228} y={112} w={12} h={2} fill={pale}/></>}
      {hinge&&<path d="M253 113 Q265 95 271 113" stroke={phase?'#eaa96e':'#e67c66'} strokeWidth="2" fill="none"/>}
    </g>}
  </g>;
}

export const PixelStoryShot = ({shot,width,height,speech}: {shot:PixelShot;width:number;height:number;speech:Cue[]}) => {
  const frame=useCurrentFrame(),{fps}=useVideoConfig(), phase=Number(shot.cartoon_phase||0);
  const location=String(shot.location||'')+' '+String(shot.description||'');
  const sceneTitle=String(shot.scene_title||'');
  const engineeringObject=/整流罩|封舱|包络|线束|折叠|振动|第二轮|锁销|一只眼/.test(`${shot.scene_title||''} ${shot.description||''}`);
  const kind=/热真空|密封故障|旧故障/.test(sceneTitle)?'vacuum'
    :/换一圈|洁净/.test(sceneTitle+' '+location)?'cleanroom'
    :/振动|第二轮/.test(sceneTitle)?'vibration'
    :/线却|线束/.test(sceneTitle)?'cable'
    :/整流罩|封舱/.test(sceneTitle)?'fairing'
    :/月球|月面|moon|lunar/i.test(location)?'moon'
    :/发射|航天港|launch/i.test(location)?'launch'
    :/控制|通信|监测|control/i.test(location)?'control':'workshop';
  const variant=shot.index%3;
  const focus=shot.cartoon.focus||'wide';
  const focusX=focus==='robot'?235:focus==='prop'?92:shot.cartoon.actors[0]?(shot.cartoon.actors[0].from_x*320+45):160;
  const viewWidth=phase?210:320, viewHeight=phase?118:180;
  const viewX=Math.max(0,Math.min(320-viewWidth,focusX-viewWidth*.5));
  const viewY=phase?36:0;
  const active=(speaker:string)=>speech.some(cue=>cue.scene_number===shot.scene_number&&cue.speaker===speaker
    &&cue.value!=='X'&&cue.value!=='A'&&shot.from+frame>=cue.from&&shot.from+frame<cue.to);
  return <AbsoluteFill style={{background:'#172236'}}><svg width={width} height={height}
    viewBox={`${viewX} ${viewY} ${viewWidth} ${viewHeight}`} preserveAspectRatio="xMidYMid slice" shapeRendering="crispEdges">
    <GameBackdrop kind={kind} variant={variant} frame={frame} fps={fps}/>
    {shot.src&&<image href={staticFile(shot.src)} x={0} y={0} width={320} height={180}
      preserveAspectRatio="xMidYMid slice" opacity={.18} style={{imageRendering:'pixelated'}}/>}
    {(shot.rover||engineeringObject)&&<GameRover action={shot.rover?.action||'idle'} frame={frame+phase*shot.duration} fps={fps}/>}
    <EngineeringSetpiece title={`${shot.scene_title||''} ${shot.description||''}`} frame={frame} fps={fps} phase={phase}/>
    {shot.cartoon.actors.filter(actor=>!/阿拓|AT-1/i.test(actor.name)).map((actor,i)=>{
      const progress=Math.max(0,Math.min(1,frame/Math.max(1,shot.duration)));
      const x=40+320*(actor.from_x+(actor.to_x-actor.from_x)*progress);
      const effect=<GameInteraction x={Math.round(x)} action={actor.action} prop={shot.cartoon.prop} frame={frame}/>;
      if (actor.body) {
        const stride=actor.action==='walk'?Math.sin((frame+phase*shot.duration)/fps*9):0;
        const shake=actor.action==='react'?Math.sin(frame*.8)*1.8:0;
        return <g key={`${actor.name}-${i}`}>{effect}<g transform={`translate(${Math.round(x+shake)} ${Math.round(145-Math.abs(stride)*1.4)})`}>
          <R x={-14} y={-1} w={28} h={2} fill={ink} opacity={.4}/>
          <g transform={`rotate(${stride*1.5} 0 -25)`}>
            <image href={staticFile(actor.body)} x={-18} y={-64} width={36} height={64}
              style={{imageRendering:'pixelated',clipPath:'polygon(0 0,100% 0,100% 65%,0 65%)'}}/>
            <image href={staticFile(actor.body)} x={-18} y={-64} width={36} height={64}
              style={{imageRendering:'pixelated',clipPath:'polygon(0 64%,50% 64%,50% 100%,0 100%)',
                transform:`rotate(${stride*8}deg)`,transformOrigin:'-3px -23px'}}/>
            <image href={staticFile(actor.body)} x={-18} y={-64} width={36} height={64}
              style={{imageRendering:'pixelated',clipPath:'polygon(50% 64%,100% 64%,100% 100%,50% 100%)',
                transform:`rotate(${-stride*8}deg)`,transformOrigin:'4px -23px'}}/>
          </g>
        </g></g>;
      }
      return <g key={`${actor.name}-${i}`}>{effect}<GamePerson name={actor.name} x={x} action={actor.action}
        frame={frame+phase*shot.duration} fps={fps} speaking={active(actor.name)}/></g>;
    })}
    <R x={0} y={156} w={320} h={3} fill="#1b2d42" opacity={.75}/>
  </svg></AbsoluteFill>;
};
