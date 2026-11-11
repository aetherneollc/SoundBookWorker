import React from 'react';
import {AbsoluteFill, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {CartoonPlan, CartoonAction} from './cartoon';

type Cue = {from: number; to: number; value: string; speaker: string; scene_number: number};
type PixelShot = {src?: string; from: number; duration: number; index: number; scene_number?: number; location?: string;
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

function GameRover({action,frame,fps}: {action:string;frame:number;fps:number}) {
  const moving=action==='drive', fall=action==='fall', recover=action==='recover';
  const x=234+(moving?Math.sin(frame/fps*1.5)*13:0), angle=fall?Math.min(1,frame/(fps*1.4))*18:recover?18-Math.min(1,frame/(fps*1.4))*18:0;
  return <g transform={`translate(${x} 137) rotate(${angle})`}>
    <R x={-31} y={-8} w={61} h={13} fill={ink}/><R x={-28} y={-6} w={55} h={9} fill="#cbd2c9"/>
    <R x={-15} y={-18} w={32} h={10} fill={ink}/><R x={-13} y={-16} w={28} h={7} fill="#90b8b5"/>
    <R x={-6} y={-27} w={14} h={9} fill={ink}/><R x={-4} y={-25} w={10} h={5} fill={frame%20<10?teal:amber}/>
    <R x={-24} y={3} w={11} h={10} fill={ink}/><R x={15} y={3} w={11} h={10} fill={ink}/>
    <R x={-22} y={5} w={7} h={6} fill={steel}/><R x={17} y={5} w={7} h={6} fill={steel}/>
    <R x={17} y={-21} w={4} h={15} fill={ink}/><R x={20} y={-24} w={14} h={4} fill={ink}/>
    <R x={20} y={-22} w={11} h={2} fill={amber}/>
  </g>;
}

export const PixelStoryShot = ({shot,width,height,speech}: {shot:PixelShot;width:number;height:number;speech:Cue[]}) => {
  const frame=useCurrentFrame(),{fps}=useVideoConfig(), phase=Number(shot.cartoon_phase||0);
  const location=String(shot.location||'')+' '+String(shot.description||'');
  const kind=/月球|月面|moon|lunar/i.test(location)?'moon':/发射|航天港|launch/i.test(location)?'launch'
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
    {shot.src ? <><image href={staticFile(shot.src)} x={0} y={0} width={320} height={180}
      preserveAspectRatio="xMidYMid slice" style={{imageRendering:'pixelated'}}/>
      <R x={0} y={0} w={320} h={180} fill="#24374f" opacity={.16}/></>
      : <GameBackdrop kind={kind} variant={variant} frame={frame} fps={fps}/>}
    {shot.cartoon.actors.filter(actor=>!/阿拓|AT-1/i.test(actor.name)).map((actor,i)=>{
      const progress=Math.max(0,Math.min(1,frame/Math.max(1,shot.duration)));
      const x=40+320*(actor.from_x+(actor.to_x-actor.from_x)*progress);
      if (actor.body) {
        const stride=actor.action==='walk'?Math.sin((frame+phase*shot.duration)/fps*9):0;
        return <g key={`${actor.name}-${i}`} transform={`translate(${Math.round(x)} ${Math.round(145-Math.abs(stride)*1.4)})`}>
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
        </g>;
      }
      return <GamePerson key={`${actor.name}-${i}`} name={actor.name} x={x} action={actor.action}
        frame={frame+phase*shot.duration} fps={fps} speaking={active(actor.name)}/>;
    })}
    {shot.rover&&<GameRover action={shot.rover.action} frame={frame+phase*shot.duration} fps={fps}/>}
    {shot.cartoon.prop&&shot.cartoon.prop!=='none'&&<g><R x={83} y={110} w={19} h={8} fill={ink}/><R x={86} y={112} w={13} h={4} fill={shot.cartoon.prop==='battery'?amber:teal}/></g>}
    <R x={0} y={156} w={320} h={3} fill="#1b2d42" opacity={.75}/>
  </svg></AbsoluteFill>;
};
