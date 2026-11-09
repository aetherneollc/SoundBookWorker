import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {SoundBook, type SoundBookProps} from './video';

const defaults: SoundBookProps = {
  width: 1280, height: 720, fps: 24, durationInFrames: 120,
  introFrames: 0, shots: [], audio: [], captions: [], speech: [], episodeNumber: 1,
  title: '', language: 'zh', cta: null, ctaFrom: 120, ctaFrames: 0,
};

const Root = () => <Composition id="SoundBook" component={SoundBook}
  width={1280} height={720} fps={24} durationInFrames={120}
  defaultProps={defaults}
  calculateMetadata={({props}) => ({
    width: props.width, height: props.height, fps: props.fps,
    durationInFrames: props.durationInFrames,
  })} />;

registerRoot(Root);
