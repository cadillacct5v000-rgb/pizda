export type Stage = 'draft' | 'voiceover' | 'storyboard' | 'images' | 'animation' | 'subtitles' | 'assembly';
export type ItemStatus = 'draft' | 'queued' | 'generating' | 'ready' | 'approved' | 'cancelled' | 'error' | 'uncertain' | 'running' | 'creating' | 'paused';
export type Provider = 'elevenlabs' | 'lumean' | 'fastgen' | 'syntx';
export interface Asset {path: string; duration: number | null; provider?: Provider | 'upload'; timings?: AudioBlockTiming[]; alignmentModel?: string; externalId?: string; inputHash?: string; createdAt?: string;}
export type UploadedVoiceover = Asset;
export interface AudioBlockTiming {blockId: string; start: number; end: number; confidence: number;}
export interface Scene {id: string; title: string; approved: boolean; voiceoverSkipped: boolean;}
export interface TextBlock {id: string; sceneId: string; text: string; status: ItemStatus; audio: Asset | null;}
export interface Frame {id: string; sceneId: string; blockId: string; shotId: string; visualDescription: string; imagePrompt: string; motionPrompt: string; camera: string; durationWeight: number; approved: boolean; image: Asset | null; video: Asset | null;}
export interface CostEstimate {id: string; stage: string; provider: Provider; unit: string | null; value: number | null; refunded?: boolean | null;}
export interface Project {
  id: string; title: string; language: string; createdAt: string; updatedAt: string; revision: number; lifecycle: 'active' | 'completed' | 'archived'; favorite: boolean; stage: Stage;
  options: {animationEnabled: boolean; subtitlesEnabled: boolean; voiceoverSkipped: boolean; silentFrameDuration: number; fps: 30 | 60};
  models: {voiceProvider: Provider; voiceId: string; voiceModel: string; templateId: string; imageProvider: Provider; imageModel: string; videoModel: string; syntxImageModel: string; syntxVideoModel: string; syntxVideoService: string;};
  captionStyle: {fontSize: number; position: string; maxLines: number; safeArea: number};
  scenes: Scene[]; blocks: TextBlock[]; frames: Frame[]; uploads: Record<string, UploadedVoiceover>; captions: {srt: Asset; vtt: Asset; estimated: boolean} | null; render: Asset | null;
  logs: {at: string; level: string; message: string; itemId?: string}[]; cost: CostEstimate[];
}
export interface ProjectSummary {id: string; title: string; language: string; lifecycle: Project['lifecycle']; favorite: boolean; updatedAt: string; revision: number; frames: number; progress: number;}
export interface Integration {status: string; checkedAt?: string; error?: string; details?: {balance?: unknown; voices?: {voice_id: string; name: string}[]; templates?: unknown; models?: unknown};}
export interface Job {key: string; stage: string; itemId: string; status: ItemStatus; error?: string; externalId?: string; cancelUnconfirmed?: boolean;}
export interface AppState {version: string; csrf: string; projects: ProjectSummary[]; integrations: Record<Provider, Integration>; queues: {name: string; active: number; pending: number; limit: number; keys: string[]}[];}
export type ProjectPatch = Partial<Pick<Project, 'title' | 'language' | 'lifecycle' | 'favorite' | 'stage'>> & {options?: Partial<Project['options']>; models?: Partial<Project['models']>; captionStyle?: Partial<Project['captionStyle']>; frames?: (Partial<Frame> & {id: string})[]; blocks?: (Partial<TextBlock> & {id: string})[]; scenes?: (Partial<Scene> & {id: string})[];};
