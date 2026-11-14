import React from 'react';
import {Img, interpolate, staticFile} from 'remotion';
import {Rover, type RoverAction} from './rover';

export type {RoverAction};

export type RobotWheel = {
  src: string;
  cx: number;
  cy: number;
  r: number;
  rx?: number;
  ry?: number;
  tilt_deg?: number;
  tire_src?: string;
};

export type RobotPart = {
  src: string;
  pivot: [number, number];
};

export type RobotRig = {
  level: 'wheeled' | 'sprite';
  width: number;
  height: number;
  anchor_x: number;
  foot_y: number;
  /** Normalised y of the highest opaque pixel (sensor/mast top); used for metric height locking. */
  content_top?: number;
  native_facing?: 'left' | 'right';
  body: string;
  body_pivot?: [number, number];
  wheels?: RobotWheel[];
  mast?: RobotPart;
  arm?: RobotPart;
  signal_point?: [number, number];
};

const bound = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

const partStyle: React.CSSProperties = {
  position: 'absolute',
  inset: 0,
  width: '100%',
  height: '100%',
  objectFit: 'fill',
  pointerEvents: 'none',
};

/**
 * Cutout puppet for the AI-designed AT-1 robot master.
 * Spins wheels on `drive` using exact arc-length rolling (dTheta = dX / R),
 * aligns `foot_y` onto `floorY * sceneHeight` with optional metric rover height,
 * projects elliptical hubs onto angled tires, and articulates mast/arm/signal.
 * Falls back to the legacy `<Rover>` SVG only when `rig` is absent.
 */
export const RobotPuppet = ({
  rig,
  frame,
  fps,
  duration,
  action,
  speaking,
  width,
  height,
  left = '43%',
  bottom = '3%',
  facing,
  floorY,
  sceneWidth,
  sceneHeight,
  targetRoverHeightPx,
  travelRangePx,
  ambientTint,
}: {
  rig?: RobotRig | null;
  frame: number;
  fps: number;
  duration: number;
  action: RoverAction;
  speaking: boolean;
  width: number;
  height: number;
  left?: string;
  bottom?: string;
  facing?: 'left' | 'right';
  floorY?: number;
  sceneWidth?: number;
  sceneHeight?: number;
  targetRoverHeightPx?: number;
  travelRangePx?: [number, number];
  ambientTint?: string;
}) => {
  if (!rig || !rig.body) {
    return (
      <Rover
        frame={frame}
        fps={fps}
        duration={duration}
        action={action}
        speaking={speaking}
        width={width}
        height={height}
        left={left}
      />
    );
  }

  const t = frame / Math.max(1, fps);
  const drive = action === 'drive';
  const reachOrSample = action === 'reach' || action === 'sample';
  const signal = action === 'signal';

  const nativeFacing = rig.native_facing === 'left' ? 'left' : 'right';
  const desiredFacing = facing || nativeFacing;
  const flipX = desiredFacing !== nativeFacing ? -1 : 1;
  const dir = nativeFacing === 'right' ? 1 : -1;
  const moveSign = desiredFacing === 'right' ? 1 : -1;

  // Smooth ease-in / cruise / ease-out displacement travelPx(frame) on `drive`
  const maxF = Math.max(1, duration - 1);
  const u = Math.max(0, Math.min(1, frame / maxF));
  const easeRamp = 0.22;
  const cruiseSpan = Math.max(0.05, 1 - easeRamp);
  // C1 continuous ease-in / linear cruise / ease-out integral profile normalized to [0, 1]
  const driveProgress =
    u < easeRamp
      ? (0.5 * u * u) / (easeRamp * cruiseSpan)
      : u <= 1 - easeRamp
      ? (u - 0.5 * easeRamp) / cruiseSpan
      : 1 - (0.5 * (1 - u) * (1 - u)) / (easeRamp * cruiseSpan);

  const rangeStart = travelRangePx ? travelRangePx[0] : -88;
  const rangeEnd = travelRangePx ? travelRangePx[1] : 88;
  const travelPx = drive
    ? (travelRangePx
        ? rangeStart + (rangeEnd - rangeStart) * driveProgress
        : (-88 + 176 * driveProgress) * moveSign)
    : reachOrSample && !rig.arm
    ? interpolate(frame, [0, fps * 1.4, fps * 2.8], [0, 10, 14], bound) * moveSign
    : 0;

  // Damped suspension bob (+-1.4px) and grounded wheel micro-jitter (<= +-0.35px) so wheels never detach from axles
  const suspensionBob = Math.sin(t * (drive ? 12 : 2.1)) * (drive ? 1.4 : 0.75);
  const standLift =
    action === 'stand'
      ? interpolate(frame, [0, fps * 2.2], [4, -12], bound)
      : action === 'fall'
      ? interpolate(frame, [0, fps * 0.7, fps * 1.8], [-4, -4, 14], bound)
      : action === 'recover'
      ? interpolate(frame, [0, fps * 0.8, fps * 2.8], [14, 14, -4], bound)
      : 0;

  const balanceSway = Math.sin(t * 2.1) * (action === 'stand' || action === 'recover' ? 1.2 : 0.4);
  const hullTilt =
    ((action === 'fall'
      ? interpolate(frame, [0, fps * 0.7, fps * 1.8], [0, 0, 15], bound)
      : action === 'recover'
      ? interpolate(frame, [0, fps * 0.8, fps * 2.8], [15, 15, 0], bound)
      : reachOrSample && !rig.arm
      ? interpolate(frame, [0, fps * 1.5], [0, 5.0], bound)
      : 0) +
      balanceSway) *
    dir;

  const mastPan = Math.sin(t * (signal ? 3.0 : 1.25)) * (signal ? 7.5 : 2.6) * dir;
  const armAngle = reachOrSample
    ? (interpolate(frame, [0, fps * 1.4, fps * 2.8], [10, -24, -34], bound) + Math.sin(t * 2.6) * 3.5) * dir
    : (8 + Math.sin(t * 1.3) * 1.4) * dir;

  // Compute rendered canvas box: lock to targetRoverHeightPx (0.53x human height ~0.95m) when provided
  const rigAspect = Math.max(0.5, Math.min(2.0, (rig.width || 1024) / Math.max(1, rig.height || 1024)));
  const footYNorm = Math.min(1, Math.max(0.5, rig.foot_y || 0.92));
  const contentTopNorm =
    typeof rig.content_top === 'number' && rig.content_top >= 0 && rig.content_top < footYNorm - 0.12
      ? rig.content_top
      : 0.16;
  const bodySpanNorm = Math.max(0.12, footYNorm - contentTopNorm);
  let boxW: number;
  let boxH: number;
  if (typeof targetRoverHeightPx === 'number' && targetRoverHeightPx > 12) {
    boxH = targetRoverHeightPx / bodySpanNorm;
    boxW = boxH * rigAspect;
  } else {
    const fitScale = Math.min(width / rigAspect, height);
    boxW = fitScale * rigAspect;
    boxH = fitScale;
  }

  const offsetLeft = (width - boxW) * 0.5;
  const groundOffset = (1 - footYNorm) * boxH;

  // Align rig.foot_y onto floorY * sceneHeight when floorY is supplied
  const resolvedBottomPx =
    typeof floorY === 'number' && typeof sceneHeight === 'number'
      ? sceneHeight * (1 - floorY)
      : undefined;
  const containerBottom = resolvedBottomPx !== undefined ? resolvedBottomPx : bottom;
  const resolvedLeft =
    typeof sceneWidth === 'number' && left.endsWith('%')
      ? `${(parseFloat(left) / 100) * sceneWidth}px`
      : left;

  const bodyPivot = rig.body_pivot || [rig.anchor_x || 0.5, 0.72];
  const sigPt = rig.signal_point || [rig.anchor_x || 0.5, Math.max(0.08, footYNorm - 0.72)];
  const pulse = speaking || signal ? 0.5 + 0.5 * Math.max(0, Math.sin(t * 11)) : 0.18;
  const wheels = rig.level === 'wheeled' && rig.wheels?.length ? rig.wheels : [];
  const roverFilter = ambientTint
    ? `drop-shadow(0 8px 10px rgba(5, 11, 24, 0.46)) drop-shadow(0 0 1px ${ambientTint})`
    : 'drop-shadow(0 8px 10px rgba(5, 11, 24, 0.46))';

  return (
    <div
      style={{
        position: 'absolute',
        left: resolvedLeft,
        bottom: containerBottom,
        width,
        height: resolvedBottomPx !== undefined ? boxH : height,
        pointerEvents: 'none',
        transform: `translateX(${travelPx}px)`,
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: offsetLeft,
          bottom: -groundOffset,
          width: boxW,
          height: boxH,
          transformOrigin: `${(rig.anchor_x || 0.5) * 100}% ${footYNorm * 100}%`,
          transform: `scaleX(${flipX})`,
          filter: roverFilter,
        }}
      >
        {/* Ground contact shadow */}
        <div
          style={{
            position: 'absolute',
            left: `${((rig.anchor_x || 0.5) - 0.36) * 100}%`,
            top: `${(footYNorm - 0.03) * 100}%`,
            width: '72%',
            height: '6.5%',
            borderRadius: '50%',
            background: 'radial-gradient(ellipse, rgba(6, 16, 26, 0.52) 0%, rgba(6, 16, 26, 0) 74%)',
            filter: 'blur(2px)',
          }}
        />

        {/* Dust puffs when driving */}
        {drive && (
          <svg
            width="100%"
            height="100%"
            viewBox="0 0 200 200"
            style={{position: 'absolute', inset: 0, overflow: 'visible'}}
          >
            {Array.from({length: 8}, (_, i) => {
              const drift = (frame * (1.8 + (i % 3) * 0.7) + i * 23) % 56;
              const baseX = nativeFacing === 'right' ? 42 - drift : 158 + drift;
              const baseY = (rig.foot_y || 0.9) * 200 - 6 - ((i * 7) % 16);
              return (
                <circle
                  key={i}
                  cx={baseX}
                  cy={baseY}
                  r={1.8 + (i % 3) * 1.1}
                  fill="#c5d0d3"
                  opacity={0.42 * (1 - drift / 56)}
                />
              );
            })}
          </svg>
        )}

        {/* Chassis group (damped suspension bob & posture tilt) */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            transformOrigin: `${bodyPivot[0] * 100}% ${bodyPivot[1] * 100}%`,
            transform: `translateY(${suspensionBob + standLift}px) rotate(${hullTilt}deg)`,
          }}
        >
          {/* Optional articulated sample/reach arm mounted cleanly behind the front bumper */}
          {rig.arm && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                transformOrigin: `${rig.arm.pivot[0] * 100}% ${rig.arm.pivot[1] * 100}%`,
                transform: `rotate(${armAngle}deg)`,
              }}
            >
              <Img src={staticFile(rig.arm.src)} style={partStyle} />
            </div>
          )}

          <Img src={staticFile(rig.body)} style={partStyle} />

          {/* Optional sensor mast */}
          {rig.mast && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                transformOrigin: `${rig.mast.pivot[0] * 100}% ${rig.mast.pivot[1] * 100}%`,
                transform: `rotate(${mastPan}deg)`,
              }}
            >
              <Img src={staticFile(rig.mast.src)} style={partStyle} />
            </div>
          )}

          {/* Signal / speaking LED & expanding telemetry waves */}
          {(speaking || signal) && (
            <svg
              width="100%"
              height="100%"
              viewBox="0 0 200 200"
              style={{position: 'absolute', inset: 0, overflow: 'visible'}}
            >
              <circle
                cx={sigPt[0] * 200}
                cy={sigPt[1] * 200}
                r={3.2 + pulse * 2.2}
                fill="#ffb648"
                opacity={0.75 + pulse * 0.25}
              />
              {[0, 1].map(ringIdx => {
                const phase = (frame + ringIdx * 10) % 20;
                const progress = phase / 20;
                return (
                  <circle
                    key={ringIdx}
                    cx={sigPt[0] * 200}
                    cy={sigPt[1] * 200}
                    r={6 + progress * 24}
                    fill="none"
                    stroke="#ffd078"
                    strokeWidth="1.8"
                    opacity={0.52 * (1 - progress)}
                  />
                );
              })}
            </svg>
          )}
        </div>

        {/* True no-slip rolling wheels grounded on the floor plane (jitterY <= +-0.35px) */}
        {wheels.map((wheel, idx) => {
          const jitterY = drive ? Math.sin(t * 19 + idx * 1.7) * 0.35 : 0;
          const wheelRadiusPx = Math.max(8, wheel.r * boxH);
          const screenRotDeg = drive ? (travelPx / wheelRadiusPx) * (180 / Math.PI) : 0;
          const localWheelDeg = screenRotDeg * flipX;

          const rxPx = (wheel.rx ?? wheel.r) * boxW;
          const ryPx = (wheel.ry ?? wheel.r) * boxH;
          const projAspect = Math.max(0.45, Math.min(1, Math.min(rxPx, ryPx) / Math.max(1, Math.max(rxPx, ryPx))));
          const isElliptical = Boolean(wheel.tire_src) || projAspect < 0.88;
          const tiltDeg = wheel.tilt_deg ?? 0;

          if (isElliptical) {
            return (
              <React.Fragment key={idx}>
                {wheel.tire_src && (
                  <div
                    style={{
                      position: 'absolute',
                      inset: 0,
                      transformOrigin: `${wheel.cx * 100}% ${wheel.cy * 100}%`,
                      transform: `translateY(${jitterY}px)`,
                    }}
                  >
                    <Img src={staticFile(wheel.tire_src)} style={partStyle} />
                  </div>
                )}
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    transformOrigin: `${wheel.cx * 100}% ${wheel.cy * 100}%`,
                    transform: `translateY(${jitterY}px) rotate(${tiltDeg}deg) scaleX(${projAspect}) rotate(${localWheelDeg}deg)`,
                  }}
                >
                  <Img src={staticFile(wheel.src)} style={partStyle} />
                </div>
              </React.Fragment>
            );
          }

          return (
            <div
              key={idx}
              style={{
                position: 'absolute',
                inset: 0,
                transformOrigin: `${wheel.cx * 100}% ${wheel.cy * 100}%`,
                transform: `translateY(${jitterY}px) rotate(${localWheelDeg}deg)`,
              }}
            >
              <Img src={staticFile(wheel.src)} style={partStyle} />
            </div>
          );
        })}
      </div>
    </div>
  );
};
