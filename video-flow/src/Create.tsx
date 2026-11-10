import {useState} from 'react';
import {api, fileBase64} from './lib/api';
import type {Project} from './types';
import {Dialog} from './components';

export default function Create({created, close}: {created: (project: Project, warnings: string[]) => void; close: () => void}) {
  const [title,setTitle] = useState(''), [script,setScript] = useState(''), [language,setLanguage] = useState('ru'), [frames,setFrames] = useState(1), [docx,setDocx] = useState<File>(), [prompts,setPrompts] = useState<File>(), [busy,setBusy] = useState(false), [error,setError] = useState('');
  async function submit() {
    setBusy(true); setError('');
    try {
      if (!title.trim() || (!script.trim() && !docx)) throw new Error('Укажите название и сценарий либо DOCX');
      for (const file of [docx,prompts]) if (file && !file.name.toLowerCase().endsWith('.docx')) throw new Error('Нужен файл DOCX');
      const result = await api.create({title, language, script, framesPerScene: frames, docx: docx ? await fileBase64(docx) : undefined, promptsDocx: prompts ? await fileBase64(prompts) : undefined});
      created(result.project, result.warnings);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось создать проект'); } finally { setBusy(false); }
  }
  return <Dialog title="Новый проект" close={close}><form onSubmit={(event) => {event.preventDefault(); void submit();}}><label className="field">Название<input required value={title} onChange={(event) => setTitle(event.target.value)}/></label><label className="field">Язык<input value={language} onChange={(event) => setLanguage(event.target.value)}/></label><label className="field">Сценарий<textarea value={script} onChange={(event) => setScript(event.target.value)} rows={8} placeholder="Разделите сцены пустой строкой"/></label><div className="columns"><label className="field">Сценарий DOCX<input type="file" accept=".docx" onChange={(event) => setDocx(event.target.files?.[0])}/></label><label className="field">Промпты DOCX<input type="file" accept=".docx" onChange={(event) => setPrompts(event.target.files?.[0])}/></label></div><label className="field">Кадров на сцену для ручного текста<input type="number" min={1} max={20} value={frames} onChange={(event) => setFrames(Number(event.target.value))}/></label><p>DOCX сопоставляются по ID S01.01. Создание проекта не расходует средства API.</p>{error && <p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy ? 'Импорт…' : 'Создать проект'}</button></form></Dialog>;
}
