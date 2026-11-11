import {BONE} from './pose';

/** Code-drawn limb atlas. 林岚 / 顾承宇 / 周岳 share these grids and swap colors. */

export type LimbName = 'head' | 'torso' | 'upperArm' | 'forearm' | 'hand' | 'thigh' | 'shin' | 'boot';

export type SpriteCell = {
  name: LimbName;
  sheetX: number;
  sheetY: number;
  w: number;
  h: number;
  pivotX: number;
  pivotY: number;
  /** Row-major color strings. Empty means transparent. */
  pixels: string[];
};

export type LimbAtlas = {
  width: number;
  height: number;
  limbs: Record<LimbName, SpriteCell>;
};

export type Palette = {
  outline: string;
  hair: string;
  hairLight: string;
  skin: string;
  suit: string;
  suitDark: string;
  suitLight: string;
  trim: string;
  boot: string;
  eye: string;
  mouth: string;
};

const shade = (hex: string, delta: number) => {
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift: number) => Math.max(0, Math.min(255, ((value >> shift) & 255) + delta)).toString(16).padStart(2, '0');
  return `#${channel(16)}${channel(8)}${channel(0)}`;
};

export const paletteFor = (name: string): Palette => {
  const cast = /林岚|Lin Lan/i.test(name)
    ? {hair: '#202b4c', suit: '#377dad', skin: '#e6b69a', trim: '#efb867'}
    : /顾承宇|Gu Chengyu/i.test(name)
      ? {hair: '#303a4d', suit: '#64788b', skin: '#d9a68b', trim: '#ecce8b'}
      : /周岳|Zhou Yue/i.test(name)
        ? {hair: '#6f6872', suit: '#3d8c8e', skin: '#d8ab93', trim: '#e9946e'}
        : {hair: '#25364a', suit: '#31678b', skin: '#d8a58c', trim: '#f0ae70'};
  return {
    outline: '#172236',
    hair: cast.hair,
    hairLight: shade(cast.hair, 48),
    skin: cast.skin,
    suit: cast.suit,
    suitDark: shade(cast.suit, -36),
    suitLight: shade(cast.suit, 42),
    trim: cast.trim,
    boot: '#1c2430',
    eye: '#172236',
    mouth: '#8a3d48',
  };
};

const HEAD = [
  '..iiiiii....',
  '.hhhhhhhhh..',
  '.ohhkkkkkk..',
  '.ohhkEekkk..',
  '.ohhkkkkkk..',
  '..hkkkkkkk..',
  '..kkkmmkkk..',
  '...kkkkkk...',
  '....kkk.....',
  '.....k......',
  '.....k......',
];

const TORSO = [
  '.osssssssso.',
  'ossssttsssso',
  'osssssssssso',
  'osssssssssso',
  'ossssllsssso',
  'osssssssssso',
  'osssssssssso',
  'osssssssssso',
  'osssddddssso',
  'osssssssssso',
  '.osssssssso.',
  '.osssssssso.',
  '..osssssso..',
  '...osssso...',
];

const UPPER_ARM = [
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'ooso',
];

const FOREARM = [
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'ooso',
];

const HAND = [
  '.ooo.',
  'okoko',
  'okoko',
  'okoko',
  '.ooo.',
];

const THIGH = [
  'oossoo',
  'osddso',
  'osddso',
  'osddso',
  'osddso',
  'osssso',
  'osssso',
  'osssso',
  'osssso',
  'osddso',
  'osddso',
  'osddso',
  'osddso',
  '.osso.',
];

const SHIN = [
  'osdo',
  'osdo',
  'osdo',
  'osso',
  'osso',
  'osso',
  'osso',
  'osso',
  'osdo',
  'osdo',
  'osdo',
  'osdo',
  'ooso',
];

const BOOT = [
  '.ooooo..',
  'oddddo..',
  'oddddoo.',
  'oooooooo',
  'oooooooo',
];

const paint = (rows: string[], fill: Record<string, string>) => {
  const w = rows[0]?.length ?? 0;
  if (!w || rows.some(row => row.length !== w)) throw new Error('ragged limb sprite');
  const pixels = rows.flatMap(row => [...row].map(ch => {
    const color = fill[ch];
    if (color === undefined) throw new Error(`unknown limb pixel "${ch}"`);
    return color;
  }));
  return {w, h: rows.length, pixels};
};

export const limbAtlas = (name: string): LimbAtlas => {
  const palette = paletteFor(name);
  const suit = {'.': '', o: palette.outline, s: palette.suit, d: palette.suitDark, l: palette.suitLight, t: palette.trim};
  const painted = {
    head: {name: 'head' as const, pivotX: 5, pivotY: 11, ...paint(HEAD, {
      '.': '', o: palette.outline, h: palette.hair, i: palette.hairLight, k: palette.skin,
      E: palette.eye, e: palette.eye, m: palette.mouth,
    })},
    torso: {name: 'torso' as const, pivotX: 5, pivotY: 14, ...paint(TORSO, suit)},
    upperArm: {name: 'upperArm' as const, pivotX: 1, pivotY: 0, ...paint(UPPER_ARM, suit)},
    forearm: {name: 'forearm' as const, pivotX: 1, pivotY: 0, ...paint(FOREARM, suit)},
    hand: {name: 'hand' as const, pivotX: 2, pivotY: 0, ...paint(HAND, {'.': '', o: palette.outline, k: palette.skin})},
    thigh: {name: 'thigh' as const, pivotX: 2, pivotY: 0, ...paint(THIGH, suit)},
    shin: {name: 'shin' as const, pivotX: 1, pivotY: 0, ...paint(SHIN, suit)},
    boot: {name: 'boot' as const, pivotX: 1, pivotY: 0, ...paint(BOOT, {'.': '', o: palette.outline, d: palette.boot})},
  };
  const expected: Record<LimbName, number> = {
    head: painted.head.h,
    torso: BONE.torso,
    upperArm: BONE.upperArm,
    forearm: BONE.forearm,
    hand: painted.hand.h,
    thigh: BONE.thigh,
    shin: BONE.shin,
    boot: painted.boot.h,
  };
  let cursor = 0;
  let height = 0;
  const limbs = {} as Record<LimbName, SpriteCell>;
  (Object.keys(painted) as LimbName[]).forEach(part => {
    const cell = painted[part];
    if (cell.h !== expected[part]) throw new Error(`${part} height ${cell.h} does not match the skeleton`);
    limbs[part] = {...cell, sheetX: cursor, sheetY: 0};
    cursor += cell.w + 1;
    height = Math.max(height, cell.h);
  });
  return {width: Math.max(0, cursor - 1), height, limbs};
};
