import {useState} from 'react';
import type {AppState, Provider} from './types';
import {api} from './lib/api';
import {Dialog} from './components';

const names: Record<Provider,string> = {elevenlabs: 'ElevenLabs — прямой ключ', lumean: 'Lumean — шаблоны озвучки', fastgen: 'FastGen — изображения и видео', syntx: 'SYNTX — изображения и видео'};
const statuses: Record<string,string> = {not_configured:'Ключ не задан',unverified:'Ключ сохранён, подключение не проверено',checking:'Проверка…',connected:'Подключено',rejected:'Ключ отклонён',unavailable:'Сервис недоступен'};
export default function Settings({state, refresh, close}: {state: AppState; refresh: () => Promise<void>; close: () => void}) {
  const [keys, setKeys] = useState<Partial<Record<Provider,string>>>({}), [busy, setBusy] = useState<Provider | null>(null), [error,setError] = useState('');
  async function connect(provider: Provider) {
    setBusy(provider); setError(''); const key = keys[provider]; setKeys((previous) => ({...previous,[provider]: ''}));
    try { await api.connect(provider, key || undefined); await refresh(); } catch (failure) { setError(String(failure)); } finally { setBusy(null); }
  }
  return <Dialog title="Подключения" close={close}><p>Ключи сохраняются на этом компьютере. Генерация запускается только по вашей команде.</p>{(Object.keys(names) as Provider[]).map((provider) => <section className="card" key={provider}><h3>{names[provider]}</h3><p role="status">{statuses[state.integrations[provider]?.status || 'not_configured']}</p>{state.integrations[provider]?.details?.balance !== undefined && <p>Баланс: {JSON.stringify(state.integrations[provider].details?.balance)}</p>}<label className="field">Ключ<input type="password" autoComplete="off" value={keys[provider] || ''} onChange={(event) => setKeys({...keys,[provider]: event.target.value})}/></label><button disabled={busy !== null} onClick={() => {void connect(provider);}}>{busy === provider ? 'Проверка…' : keys[provider] ? 'Сохранить и проверить' : 'Проверить подключение'}</button>{state.integrations[provider]?.error && <p className="error">{state.integrations[provider].error}</p>}</section>)}{error && <p className="error" role="alert">{error}</p>}</Dialog>;
}
