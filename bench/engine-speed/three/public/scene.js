import * as THREE from '/vendor/three/build/three.module.js';

const CLEAR = [140, 158, 179];

function material(color, roughness) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness: 0 });
}

function limb(parent, x, y, mat) {
  const pivot = new THREE.Group();
  pivot.position.set(x, y, 0);
  const mesh = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.41, 3, 8), mat);
  mesh.position.y = -0.28;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  pivot.add(mesh);
  parent.add(pivot);
  return pivot;
}

function character(x, z, color) {
  const root = new THREE.Group();
  root.position.set(x, 0, z);
  root.userData.z0 = z;
  const skin = material(color, 0.72);
  const cloth = material(new THREE.Color(color).multiplyScalar(0.75), 0.72);

  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.2, 0.45, 4, 12), skin);
  body.position.y = 1.05;
  body.castShadow = true;
  body.receiveShadow = true;
  root.add(body);

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.18, 16, 8), skin);
  head.position.y = 1.62;
  head.castShadow = true;
  root.add(head);

  root.userData.armL = limb(root, -0.32, 1.35, cloth);
  root.userData.armR = limb(root, 0.32, 1.35, cloth);
  root.userData.legL = limb(root, -0.1, 0.72, cloth);
  root.userData.legR = limb(root, 0.1, 0.72, cloth);
  return root;
}

function coverage(read) {
  const width = read.width;
  const height = read.height;
  const pix = read.pixels;
  const x0 = Math.floor(width * 0.25);
  const x1 = Math.floor(width * 0.75);
  const y0 = Math.floor(height * 0.05);
  const y1 = Math.floor(height * 0.65);
  let hit = 0;
  let n = 0;
  let acc = 0;
  for (let y = y0; y < y1; y += 8) {
    for (let x = x0; x < x1; x += 8) {
      const o = (y * width + x) * 4;
      const d = Math.abs(pix[o] - CLEAR[0]) + Math.abs(pix[o + 1] - CLEAR[1]) + Math.abs(pix[o + 2] - CLEAR[2]);
      acc += pix[o] + pix[o + 1] + pix[o + 2];
      if (d > 46) hit += 1;
      n += 1;
    }
  }
  return {
    coverage: n ? hit / n : 0,
    mean: n ? acc / (n * 3) / 255 : 0,
  };
}

function grab(gl, width, height) {
  const pixels = new Uint8Array(width * height * 4);
  gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
  return { pixels, width, height };
}

let world = null;

function createWorld(width, height) {
  const canvas = document.querySelector('#c');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false });
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;

  const gl = renderer.getContext();
  if (!gl) throw new Error('no WebGL context');
  const debug = gl.getExtension('WEBGL_debug_renderer_info');
  const adapter = debug
    ? `${gl.getParameter(debug.UNMASKED_VENDOR_WEBGL)} ${gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)}`
    : String(gl.getParameter(gl.RENDERER) || '');

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x8c9eb3);
  const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 80);
  camera.position.set(0, 1.5, 5.2);
  camera.lookAt(0, 1, 0);

  scene.add(new THREE.HemisphereLight(0xb7c4d4, 0x303030, 0.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.3);
  sun.position.set(5, 8, 6);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.camera.near = 0.5;
  sun.shadow.camera.far = 24;
  sun.shadow.camera.left = -8;
  sun.shadow.camera.right = 8;
  sun.shadow.camera.top = 8;
  sun.shadow.camera.bottom = -8;
  sun.shadow.bias = 0.0008;
  scene.add(sun);

  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(24, 24),
    material(0x525c4d, 0.9),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const boxMat = material(0x735c47, 0.8);
  for (let i = 0; i < 8; i += 1) {
    const h = 0.8 + (i % 3) * 0.35;
    const box = new THREE.Mesh(new THREE.BoxGeometry(0.6, h, 0.6), boxMat);
    const ang = Math.PI * 2 * i / 8;
    box.position.set(Math.sin(ang) * 4, h / 2, Math.cos(ang) * 4 - 1);
    box.castShadow = true;
    box.receiveShadow = true;
    scene.add(box);
  }

  const cast = [
    character(-0.7, 0, 0xdb7361),
    character(0.8, 0.2, 0x597ac7),
  ];
  for (const actor of cast) scene.add(actor);

  return {
    renderer,
    gl,
    scene,
    camera,
    cast,
    adapter,
    drawW: gl.drawingBufferWidth,
    drawH: gl.drawingBufferHeight,
  };
}

window.runBench = function runBench(opts) {
  const frames = opts.frames;
  const fps = opts.fps;
  const readback = Boolean(opts.readback);
  if (!world) world = createWorld(opts.width, opts.height);
  const { renderer, gl, scene, camera, cast, adapter, drawW, drawH } = world;
  const full = readback ? new Uint8Array(drawW * drawH * 4) : null;
  const mid = Math.max(Math.floor(frames / 2), 1);
  let sample = { coverage: 0, mean: 0 };

  const drawFrame = (frame) => {
    const t = frame / fps;
    const swing = Math.sin(t * Math.PI * 2 * 1.6);
    camera.position.x = Math.sin(t * 0.4) * 0.3;
    camera.lookAt(0, 1, 0);
    for (const actor of cast) {
      actor.userData.armL.rotation.x = swing * 0.8;
      actor.userData.armR.rotation.x = -swing * 0.8;
      actor.userData.legL.rotation.x = -swing * 0.6;
      actor.userData.legR.rotation.x = swing * 0.6;
      actor.position.z = actor.userData.z0 + Math.sin(t * 0.7) * 0.15;
    }
    renderer.render(scene, camera);
  };

  for (let frame = 1; frame <= 5; frame += 1) drawFrame(frame);

  const t0 = performance.now();
  for (let frame = 1; frame <= frames; frame += 1) {
    drawFrame(frame);
    if (readback) {
      gl.readPixels(0, 0, drawW, drawH, gl.RGBA, gl.UNSIGNED_BYTE, full);
    }
    if (frame === mid) {
      const shot = readback
        ? { pixels: full, width: drawW, height: drawH }
        : grab(gl, drawW, drawH);
      sample = coverage(shot);
    }
  }
  const loopS = (performance.now() - t0) / 1000;

  return {
    engine: 'three',
    mode: readback ? 'readback' : 'render',
    frames,
    fps_setting: fps,
    width: drawW,
    height: drawH,
    loop_s: loopS,
    ms_per_frame: (loopS * 1000) / frames,
    loop_fps: frames / loopS,
    adapter,
    pixel_mean: sample.mean,
    pixel_coverage: sample.coverage,
    pixel_ok: sample.coverage >= 0.08 && drawW >= 1200 && drawH >= 680,
    scene: '1280x720, 2 capsule characters, 8 boxes, one 1024 directional shadow',
  };
};
