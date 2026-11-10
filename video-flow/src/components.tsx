import {useEffect, useRef, useState, type ReactNode} from 'react';
import type {Asset} from './types';
import {assetURL} from './lib/api';

export function Dialog({title, children, close}: {title: string; children: ReactNode; close: () => void}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const previous = document.activeElement as HTMLElement | null; ref.current?.showModal(); return () => { ref.current?.close(); previous?.focus(); }; }, []);
  return <dialog ref={ref} onCancel={close} aria-label={title}><header><h2>{title}</h2><button onClick={close} aria-label="Закрыть окно">×</button></header>{children}</dialog>;
}
export function Editor({label, value, save, multiline = true}: {label: string; value: string; save: (value: string) => Promise<void>; multiline?: boolean}) {
  const [draft, setDraft] = useState(value), [status, setStatus] = useState(''), [error, setError] = useState('');
  const dirty = useRef(false), latest = useRef(save); latest.current = save;
  const draftRef = useRef(draft), persistRef = useRef<() => Promise<void>>(async () => {}), inFlight = useRef<{value: string; promise: Promise<void>} | null>(null);
  draftRef.current = draft;
  useEffect(() => { if (!dirty.current) setDraft(value); }, [value]);
  async function persist() {
    if (!dirty.current) return;
    const sent = draftRef.current; if (inFlight.current?.value === sent) return inFlight.current.promise;
    setStatus('Сохранение…'); setError('');
    const pending = latest.current(sent); inFlight.current = {value: sent, promise: pending};
    try { await pending; if (draftRef.current === sent) { dirty.current = false; setStatus('Сохранено'); } }
    catch (failure) { setStatus(''); setError(failure instanceof Error ? failure.message : 'Не удалось сохранить'); }
    finally { if (inFlight.current?.promise === pending) inFlight.current = null; }
  }
  persistRef.current = persist;
  useEffect(() => {
    const flush = () => { void persistRef.current(); }, unload = (event: BeforeUnloadEvent) => { if (dirty.current) event.preventDefault(); };
    window.addEventListener('video-flow-flush',flush); window.addEventListener('beforeunload',unload);
    return () => {window.removeEventListener('video-flow-flush',flush); window.removeEventListener('beforeunload',unload);};
  }, []);
  useEffect(() => { if (!dirty.current) return; const timer = setTimeout(() => { void persist(); }, 700); return () => clearTimeout(timer); }, [draft]);
  const props = {value: draft, onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {dirty.current = true; setDraft(event.target.value);}, 'aria-label': label};
  return <label className="field">{label}{multiline ? <textarea {...props} rows={3}/> : <input {...props}/>}<small role="status">{status}</small>{error && <span className="error" role="alert">{error} <button onClick={() => {void persist();}}>Повторить сохранение</button></span>}</label>;
}
export function AudioPlayer({asset}: {asset: Asset | null | undefined}) { return asset ? <div><audio controls preload="none" src={assetURL(asset.path)}/><small>{asset.duration?.toFixed(2)} сек. · {asset.provider}{asset.timings && ` · confidence ${Math.round(Math.min(...asset.timings.map((t) => t.confidence)) * 100)}%`}</small></div> : <small>Озвучка ещё не создана</small>; }
export function ErrorBanner({message, dismiss}: {message: string; dismiss: () => void}) { return <div className="error banner" role="alert">{message}<button onClick={dismiss} aria-label="Закрыть сообщение">×</button></div>; }
