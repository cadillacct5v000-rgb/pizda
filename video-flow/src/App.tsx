import {useCallback, useEffect, useRef, useState} from 'react';
import type {AppState, Project, ProjectPatch, Stage, Job} from './types';
import {api, fileBase64, request} from './lib/api';
import {ErrorBanner} from './components';
import Settings from './Settings';
import Create from './Create';
import ProjectView, {stageNames} from './ProjectView';

function route() {const match = location.pathname.match(/^\/projects\/([\da-f-]+)\/([a-z]+)$/); return match ? {id: match[1], stage: match[2] as Stage} : null;}
export default function App() {
  const [state,setState] = useState<AppState>(), [project,setProject] = useState<Project>(), [jobs,setJobs] = useState<Job[]>([]), [error,setError] = useState(''), [busy,setBusy] = useState(false), [settings,setSettings] = useState(false), [create,setCreate] = useState(false), [current,setCurrent] = useState(route), [monitor,setMonitor] = useState<unknown>();
  const latest = useRef(project), currentRef = useRef(current), serial = useRef(Promise.resolve()), editing = useRef(0); latest.current = project; currentRef.current = current;
  function accept(value: Project) { if (currentRef.current?.id === value.id && (!latest.current || latest.current.id !== value.id || latest.current.revision <= value.revision)) {latest.current = value; setProject(value);} }
  const refresh = useCallback(async () => {
    const value = await api.state(); setState(value);
    const selected = currentRef.current;
    if (selected && value.projects.some((p) => p.id === selected.id) && !editing.current) {
      const [next,nextJobs] = await Promise.all([api.project(selected.id),api.jobs(selected.id)]);
      if (currentRef.current?.id === selected.id) {accept(next); setJobs(nextJobs);}
    }
    if (location.pathname === '/backend-monitor') setMonitor(await request('/health'));
  }, []);
  useEffect(() => {
    let alive = true;
    const tick = async () => {try {await refresh();} catch (failure) {if (alive) setError(failure instanceof Error ? failure.message : 'Ошибка связи');}};
    void tick(); const timer = setInterval(() => {void tick();},2500), pop = () => {
      const next = route(); window.dispatchEvent(new Event('video-flow-flush'));
      void serial.current.then(() => setCurrent(next)).catch((failure: unknown) => { const previous=currentRef.current; if(previous)history.pushState(null,'',`/projects/${previous.id}/${previous.stage}`); setError(failure instanceof Error ? failure.message : 'Сначала сохраните изменения'); });
    }; window.addEventListener('popstate',pop);
    return () => {alive = false; clearInterval(timer); window.removeEventListener('popstate',pop);};
  }, [refresh]);
  useEffect(() => { latest.current = undefined; setProject(undefined); setJobs([]); if (current) void refresh().catch((failure: unknown) => setError(String(failure))); }, [current?.id,refresh]);
  const navigate = (id: string, stage: Stage) => {
    window.dispatchEvent(new Event('video-flow-flush'));
    void serial.current.then(() => {const path = `/projects/${id}/${stage}`; history.pushState(null,'',path); localStorage.setItem(`stage:${id}`,stage); setCurrent({id,stage});}).catch((failure: unknown) => setError(failure instanceof Error ? failure.message : 'Сначала сохраните изменения'));
  };
  useEffect(() => {
    if (!project || !current) return;
    if (!(current.stage in stageNames) || (current.stage === 'animation' && !project.options.animationEnabled)) {history.replaceState(null,'',`/projects/${project.id}/draft`); setCurrent({id: project.id,stage: 'draft'});}
    document.title = `${project.title} · Video Flow`;
  }, [project?.id,project?.title,project?.options.animationEnabled,current?.stage]);
  function save(patch: ProjectPatch): Promise<void> {
    const targetId = project?.id; if (!targetId) return Promise.reject(new Error('Проект не открыт'));
    editing.current++;
    const next = serial.current.catch(() => {}).then(async () => {
      const base = latest.current?.id === targetId ? latest.current : await api.project(targetId);
      const saved = await api.patch(targetId,base.revision,patch); accept(saved);
    });
    serial.current = next.finally(() => {editing.current--;}); serial.current.catch(() => {});
    return next;
  }
  async function run(action: () => Promise<unknown>) {setBusy(true); setError(''); try {await action(); await refresh();} catch (failure) {setError(failure instanceof Error ? failure.message : 'Ошибка операции');} finally {setBusy(false);}}
  async function remove() {if (!project || !confirm(`Удалить проект «${project.title}» вместе с файлами?`)) return; const id = project.id; await run(async () => {await api.delete(id); history.pushState(null,'','/'); currentRef.current = null; setCurrent(null); setProject(undefined); await refresh();});}
  return <div className="app"><aside><a className="brand" href="/">KadroGen <span>VIDEO FLOW</span></a><button className="primary" onClick={() => setCreate(true)}>＋ Новый проект</button><label className="restore">Восстановить ZIP<input type="file" accept=".zip" onChange={(event) => {const file = event.target.files?.[0]; if (file) void run(async () => {const restored = await api.restore(await fileBase64(file,50 * 1024 * 1024)); navigate(restored.id,'draft'); await refresh();}); event.target.value = '';}}/></label><nav aria-label="Проекты">{state?.projects.map((summary) => <button className={current?.id === summary.id ? 'project selected' : 'project'} key={summary.id} onClick={() => navigate(summary.id,(localStorage.getItem(`stage:${summary.id}`) || 'draft') as Stage)}><strong>{summary.favorite ? '★ ' : ''}{summary.title}</strong><small>{summary.frames} кадров · {summary.lifecycle}</small><progress max={100} value={summary.progress}/></button>)}</nav><button onClick={() => setSettings(true)}>Подключения и ключи</button><a href="/backend-monitor">Состояние сервера</a><small>Video Flow {state?.version || '…'}</small></aside><main><header className="topbar"><div><small>Видеопроект</small><h1>{project?.title || (location.pathname === '/backend-monitor' ? 'Состояние сервера' : 'Ваши проекты')}</h1></div>{project && <div className="toolbar"><button disabled={busy} onClick={() => {void run(() => save({favorite: !project.favorite}));}}>{project.favorite ? '★ В избранном' : '☆ В избранное'}</button><button className="danger" disabled={busy} onClick={() => {void remove();}}>Удалить</button></div>}</header>{error && <ErrorBanner message={error} dismiss={() => setError('')}/>}{state && state.version !== '0.1.0' && <p className="error">Версии интерфейса и сервера отличаются. Обновите страницу.</p>}{location.pathname === '/backend-monitor' ? <section className="card"><h2>Backend</h2><pre>{JSON.stringify(monitor,null,2)}</pre><button onClick={() => {void run(async () => setMonitor(await request('/diagnostics')));}}>Диагностика</button></section> : project && current && state ? <><nav className="stepper" aria-label="Этапы">{(Object.keys(stageNames) as Stage[]).filter((stage) => project.options.animationEnabled || stage !== 'animation').map((stage,index) => <button className={stage === current.stage ? 'selected' : ''} key={stage} onClick={() => navigate(project.id,stage)}><span>{index + 1}</span>{stageNames[stage]}</button>)}</nav><ProjectView key={project.id} project={project} stage={current.stage} state={state} jobs={jobs} save={save} run={run} refresh={refresh} busy={busy}/></> : <section className="empty"><span>01 → 07</span><h2>От сценария к готовому ролику</h2><p>Создайте проект, подготовьте голос, отредактируйте кадры и соберите видео. Готовые задачи сохраняются между запусками.</p><button className="primary" onClick={() => setCreate(true)}>Создать первый проект</button></section>}</main>{settings && state && <Settings state={state} refresh={refresh} close={() => setSettings(false)}/>}{create && <Create close={() => setCreate(false)} created={(value,warnings) => {setCreate(false); navigate(value.id,'draft'); if (warnings.length) setError(warnings.join('\n')); void refresh();}}/>}</div>;
}
