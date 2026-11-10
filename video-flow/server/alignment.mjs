import {ensure} from './core.mjs';
import {pcm} from './media.mjs';

export function tokens(value) { return value.normalize('NFKC').toLocaleLowerCase().match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]|[\p{L}\p{N}]+/gu) || []; }
export function alignBlocks(blocks, chunks, duration) {
  ensure(Number.isFinite(duration) && duration > 0, 'Неверная длительность');
  const spoken = chunks.flatMap((chunk) => {
    const words = tokens(chunk.text), [start, end] = chunk.timestamp || [];
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
    return words.map((word, index) => ({word, start: start + (end - start) * index / words.length, end: start + (end - start) * (index + 1) / words.length}));
  }).filter((entry) => entry.start >= 0 && entry.end <= duration + 0.05);
  let cursor = 0, boundary = 0;
  return blocks.map((block) => {
    const expected = tokens(block.text), matches = [];
    // Limited forward search preserves order and avoids quadratic memory on long scripts.
    for (const word of expected) {
      let position = -1;
      for (let candidate = cursor; candidate < Math.min(spoken.length, cursor + 40); candidate++) if (spoken[candidate].word === word) { position = candidate; break; }
      if (position >= 0) { matches.push(spoken[position]); cursor = position + 1; }
    }
    const start = Math.max(boundary, matches[0]?.start ?? boundary), end = Math.min(duration, Math.max(start, matches.at(-1)?.end ?? start)); boundary = end;
    return {blockId: block.id, start, end, confidence: expected.length ? matches.length / expected.length : 0};
  });
}
export function transcriber(config, implementation) {
  let loading;
  const load = () => loading ||= (implementation ? Promise.resolve(implementation) : import('@huggingface/transformers').then(async ({pipeline, env}) => { env.cacheDir = `${config.root}/models`; return pipeline('automatic-speech-recognition', config.whisperModel, {device: 'cpu', dtype: 'q8'}); })).catch((error) => { loading = null; throw error; });
  return async (file, blocks, duration, signal) => {
    const recognizer = await load(); signal.throwIfAborted();
    const audio = await pcm(file, `${file}.whisper.tmp`, signal); signal.throwIfAborted();
    const result = await recognizer(audio, {return_timestamps: 'word', chunk_length_s: 30, stride_length_s: 5});
    signal.throwIfAborted();
    return {model: config.whisperModel, chunks: result.chunks || [], timings: alignBlocks(blocks, result.chunks || [], duration)};
  };
}
