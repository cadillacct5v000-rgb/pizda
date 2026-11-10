import {Composition, registerRoot} from 'remotion';
import {FinalVideo, type Timeline} from './Video';

const empty: Timeline = {fps: 30, width: 1920, height: 1080, durationInFrames: 30, visuals: [], audio: [], cues: [], captionStyle: {fontSize: 48, position: 'bottom', maxLines: 2, safeArea: 100}};
const Root = () => <Composition id="FinalVideo" component={FinalVideo} width={1920} height={1080} fps={30} durationInFrames={30} defaultProps={{timeline: empty}} calculateMetadata={({props}) => ({durationInFrames: props.timeline.durationInFrames, fps: props.timeline.fps})}/>;
registerRoot(Root);
