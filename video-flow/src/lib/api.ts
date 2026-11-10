import type {AppState, Project, ProjectPatch, Job, Provider, Integration} from '../types';

export class APIError extends Error {constructor(message: string, public status: number, public requestId?: string, public details?: unknown) {super(message);}}
let csrf = '';
export async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(new DOMException('Сервер не ответил за 60 секунд', 'TimeoutError')), 60000);
  try {
    const response = await fetch(`/api${url}`, {...init, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...init.headers}, signal: init.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal});
    if (response.status === 204) return undefined as T;
    const value: unknown = await response.json();
    if (!response.ok) { const error = value as {message?: string; requestId?: string; details?: unknown}; throw new APIError(error.message || `HTTP ${response.status}`, response.status, error.requestId, error.details); }
    return value as T;
  } catch (error) {
    if (error instanceof APIError || (error instanceof DOMException && ['AbortError','TimeoutError'].includes(error.name))) throw error;
    throw new APIError('Нет связи с локальным сервером. Проверьте, запущен ли Backend.', 0);
  } finally { clearTimeout(timer); }
}
const post = <T>(url: string, body: unknown = {}) => request<T>(url, {method: 'POST', body: JSON.stringify(body)});
export const api = {
  async state() { const value = await request<AppState>('/state'); csrf = value.csrf; return value; },
  project: (id: string) => request<Project>(`/projects/${id}`),
  create: (data: unknown) => post<{project: Project; warnings: string[]}>('/projects', data),
  patch: (id: string, revision: number, patch: ProjectPatch) => request<Project>(`/projects/${id}`, {method: 'PATCH', body: JSON.stringify({...patch, revision})}),
  delete: (id: string) => request<void>(`/projects/${id}`, {method: 'DELETE', body: JSON.stringify({confirm: id})}),
  connect: (provider: Provider, key?: string) => post<Integration>(`/integrations/${provider}`, key === undefined ? {} : {key}),
  generate: (id: string, stage: string, itemId: string, newTake = false, acknowledgeUncertain = false) => post<{key: string}>(`/projects/${id}/generate`, {stage, itemId, newTake, acknowledgeUncertain}),
  cancel: (id: string, itemId?: string) => post(`/projects/${id}/cancel`, {itemId}),
  jobs: (id: string) => request<Job[]>(`/projects/${id}/jobs`),
  audio: (id: string, scope: string, data?: string, remove = false) => post<Project>(`/projects/${id}/audio`, {scope, data, remove}),
  alignment: (id: string, scope: string) => post(`/projects/${id}/alignment`, {scope}),
  subtitles: (id: string) => post<Project>(`/projects/${id}/subtitles`),
  render: (id: string) => post(`/projects/${id}/render`),
  studio: () => post<{url: string}>('/studio'),
  restore: (data: string) => post<Project>('/restore', {data}),
};
export function fileBase64(file: File, maximum = 20 * 1024 * 1024): Promise<string> {
  if (file.size > maximum) return Promise.reject(new Error(`Файл больше ${maximum / 1024 / 1024} МБ`));
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error('Не удалось прочитать файл')); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.readAsDataURL(file); });
}
export const assetURL = (path: string) => `/files/${path.split('/').map(encodeURIComponent).join('/')}`;
