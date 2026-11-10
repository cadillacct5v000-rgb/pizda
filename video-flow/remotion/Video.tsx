import {AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, useCurrentFrame, interpolate, staticFile} from 'remotion';

export interface Timeline {
  fps: number; width: number; height: number; durationInFrames: number;
  visuals: {id: string; from: number; durationInFrames: number; kind: string; path: string; camera: string}[];
  audio: {from: number; durationInFrames: number; path: string}[];
  cues: {start: number; end: number; text: string}[];
  captionStyle: {fontSize: number; position: string; maxLines: number; safeArea: number};
}
const Still = ({visual}: {visual: Timeline['visuals'][number]}) => {
  const frame = useCurrentFrame();
  const scale = visual.camera === 'zoom-in' ? interpolate(frame, [0, visual.durationInFrames], [1, 1.1]) : visual.camera === 'zoom-out' ? interpolate(frame, [0, visual.durationInFrames], [1.1, 1]) : 1;
  const translate = visual.camera === 'pan-left' ? interpolate(frame, [0, visual.durationInFrames], [3, -3]) : visual.camera === 'pan-right' ? interpolate(frame, [0, visual.durationInFrames], [-3, 3]) : 0;
  return <Img src={staticFile(visual.path)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `translateX(${translate}%) scale(${translate ? 1.1 : scale})`}}/>;
};
export const FinalVideo = ({timeline}: {timeline: Timeline}) => {
  const frame = useCurrentFrame(), cue = timeline.cues.find((c) => frame / timeline.fps >= c.start && frame / timeline.fps < c.end), style = timeline.captionStyle;
  return <AbsoluteFill style={{background: '#000'}}>
    {timeline.visuals.map((visual) => <Sequence key={visual.id} from={visual.from} durationInFrames={visual.durationInFrames}><AbsoluteFill style={{overflow: 'hidden'}}>{visual.kind === 'video' ? <OffthreadVideo src={staticFile(visual.path)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}}/> : <Still visual={visual}/>}</AbsoluteFill></Sequence>)}
    {timeline.audio.map((audio, index) => <Sequence key={index} from={audio.from} durationInFrames={audio.durationInFrames}><Audio src={staticFile(audio.path)}/></Sequence>)}
    {cue && <AbsoluteFill style={{justifyContent: style.position === 'top' ? 'flex-start' : style.position === 'center' ? 'center' : 'flex-end', padding: style.safeArea, pointerEvents: 'none'}}><div style={{alignSelf: 'center', color: 'white', background: '#000b', borderRadius: 12, padding: '14px 26px', fontSize: style.fontSize, textAlign: 'center', fontFamily: 'Arial, sans-serif', lineHeight: 1.3, maxWidth: '88%', display: '-webkit-box', WebkitLineClamp: style.maxLines, WebkitBoxOrient: 'vertical', overflow: 'hidden', whiteSpace: 'pre-wrap'}}>{cue.text}</div></AbsoluteFill>}
  </AbsoluteFill>;
};
