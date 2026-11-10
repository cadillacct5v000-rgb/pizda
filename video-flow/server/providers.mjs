import {setTimeout as pause} from 'node:timers/promises';
import {lookup} from 'node:dns/promises';
import {isIP, BlockList} from 'node:net';
import https from 'node:https';
import {ensure, AppError, decode64, redact} from './core.mjs';

const denied = new BlockList(), denied6 = new BlockList();
for (const [network, prefix] of [['0.0.0.0',8],['10.0.0.0',8],['100.64.0.0',10],['127.0.0.0',8],['169.254.0.0',16],['172.16.0.0',12],['192.0.0.0',24],['192.168.0.0',16],['198.18.0.0',15],['224.0.0.0',4],['240.0.0.0',4]]) denied.addSubnet(network, prefix);
for (const [network, prefix] of [['::',128],['::1',128],['fc00::',7],['fe80::',10],['ff00::',8],['::ffff:0:0',96]]) denied6.addSubnet(network, prefix, 'ipv6');
export async function checkDownloadURL(input, resolve = lookup) {
  const url = new URL(input);
  ensure(url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443') && !isIP(url.hostname.replace(/[[\]]/g, '')), 'Небезопасная ссылка результата', 502);
  const addresses = await resolve(url.hostname, {all: true, verbatim: true});
  ensure(addresses.length && addresses.every(({address, family}) => family === 6 ? !denied6.check(address, 'ipv6') : !denied.check(address)), 'Ссылка результата ведёт в локальную сеть', 502);
  return url;
}
async function pinnedDownload(url, signal) {
  let addresses;
  const target = await checkDownloadURL(url, async (...args) => { addresses = await lookup(...args); return addresses; });
  const address = addresses[0];
  return new Promise((resolve, reject) => {
    const request = https.get(target, {signal, lookup: (_hostname, options, callback) => options.all ? callback(null, [address]) : callback(null, address.address, address.family)}, (response) => {
      const headers = new Headers(); for (const [key,value] of Object.entries(response.headers)) if (value !== undefined) headers.set(key, Array.isArray(value) ? value.join(',') : value);
      resolve({ok: response.statusCode >= 200 && response.statusCode < 300, status: response.statusCode, headers, body: response});
    }); request.on('error', reject);
  });
}
export function providerClients(config, fetcher = fetch) {
  const secrets = () => Object.values(config.providers).map((p) => p.key);
  async function request(name, route, {method = 'GET', body, signal, binary = false, form} = {}) {
    const provider = config.providers[name]; ensure(provider?.key, `Подключите ${name} в настройках`, 409);
    const headers = name === 'elevenlabs' ? {'xi-api-key': provider.key} : name === 'syntx' ? {Authorization: `Bearer ${provider.key.replace(/^Bearer\s+/i, '').trim()}`} : {'X-API-KEY': provider.key};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let response;
    try { response = await fetcher(provider.base + route, {method, headers, body: form || (body === undefined ? undefined : JSON.stringify(body)), signal: AbortSignal.any([AbortSignal.timeout(config.httpTimeout), ...(signal ? [signal] : [])]), redirect: 'error'}); }
    catch (error) { if (signal?.aborted) throw signal.reason; throw new AppError(502, `Нет ответа от ${name}`, {kind: 'network', cause: redact(error.message, secrets())}); }
    if (!response.ok) {
      // Provider error payloads may echo narration, credentials or data URIs. Never send them to the browser/log.
      const status = response.status;
      throw new AppError(status === 401 || status === 403 ? 401 : 502, `${name}: HTTP ${status}${status === 401 || status === 403 ? ' — проверьте ключ и разрешения' : ''}`, {providerStatus: status});
    }
    return binary ? Buffer.from(await response.arrayBuffer()) : response.json();
  }
  async function download(url, signal, maximum = 512 * 1024 * 1024) {
    let target = url;
    for (let redirects = 0; redirects <= 3; redirects++) {
      const response = await pinnedDownload(target, AbortSignal.any([AbortSignal.timeout(300000), ...(signal ? [signal] : [])]));
      if ([301,302,303,307,308].includes(response.status)) { response.body.destroy(); target = new URL(response.headers.get('location'), target).href; continue; }
      ensure(response.ok && response.body, `Не удалось скачать результат: HTTP ${response.status}`, 502);
      ensure(!Number.isFinite(Number(response.headers.get('content-length'))) || Number(response.headers.get('content-length')) <= maximum, 'Результат превышает лимит', 502);
      const chunks = []; let size = 0;
      try { for await (const chunk of response.body) { size += chunk.byteLength; ensure(size <= maximum, 'Результат превышает лимит', 502); chunks.push(Buffer.from(chunk)); } }
      catch (error) { response.body.destroy(); throw error; }
      ensure(size > 0, 'Пустой результат', 502); return Buffer.concat(chunks);
    }
    throw new AppError(502, 'Слишком много перенаправлений результата');
  }
  const fastgen = {
    async check() { const [balance, models] = await Promise.all([request('fastgen', '/api/v6/usage'), request('fastgen', '/api/v6/capabilities')]); return {balance, models}; },
    async create(payload, signal) { return request('fastgen', '/api/v6/generations', {method: 'POST', body: payload, signal}); },
    async status(externalId, signal) { return request('fastgen', `/api/v6/generations/${encodeURIComponent(externalId)}`, {signal}); },
    async cancel(externalId) { return request('fastgen', '/api/v6/operations/cancel', {method: 'POST', body: {operation_ids: [externalId]}}); },
    async result(status, stage, signal) {
      const result = status.results?.find((item) => item.type === ({voice: 'audio', image: 'image', video: 'video'}[stage]));
      ensure(result, 'Провайдер не вернул ожидаемое медиа', 502);
      return result.download_url ? download(result.download_url, signal) : decode64(result.data?.split(',')[1], 512 * 1024 * 1024);
    },
  };
  const lumean = {
    async check() {
      const [templates,wallets] = await Promise.all([request('lumean', '/templates'),request('lumean', '/wallets')]);
      return {templates: (templates.data?.items || []).map(({id,name,service_key}) => ({id,name,service:service_key})), balance: (wallets.data?.items || []).map(({balance_formatted,locked_balance_formatted,asset}) => ({available:balance_formatted,locked:locked_balance_formatted,unit:asset?.code}))};
    },
    async create(payload, signal) { const result = await request('lumean', '/orders', {method: 'POST', body: payload, signal}); return result.data || result; },
    async status(externalId, signal) { const result = await request('lumean', `/orders/${encodeURIComponent(externalId)}`, {signal}); return result.data || result; },
    async cancel(externalId) { return request('lumean', `/orders/${encodeURIComponent(externalId)}/cancel`, {method: 'POST', signal: AbortSignal.timeout(15000)}); },
    async result(status, _stage, signal) {
      const file = status.result?.files?.[0]; ensure(typeof file === 'string', 'Lumean не вернул итоговый аудиофайл', 502);
      const result = await request('lumean', '/storage/url', {method: 'POST', body: {path: file}, signal});
      ensure(result.data?.url || result.url, 'Lumean не вернул ссылку на файл', 502);
      return download(result.data?.url || result.url, signal);
    },
  };
  const elevenlabs = {
    async check() { const result = await request('elevenlabs', '/v2/voices?page_size=100'); return {voices: result.voices || []}; },
    async speech(payload, signal) { return request('elevenlabs', `/v1/text-to-speech/${encodeURIComponent(payload.voiceId)}?output_format=mp3_44100_128`, {method: 'POST', body: {text: payload.text, model_id: payload.model}, signal, binary: true}); },
  };
  const syntx = {
    async check() {
      const [balance, models] = await Promise.all([request('syntx', '/api/v1/user/balance'), request('syntx', '/api/v1/ai/models')]);
      return {balance, models: (Array.isArray(models) ? models : []).filter((model) => model.active).map(({value, label, ai_name, settings}) => ({value, label, service: ai_name, settings}))};
    },
    async create(payload, signal, checkpoint) {
      const chat = await request('syntx', '/api/v1/chats', {method: 'POST', body: {title: 'Video Flow generation', scope: payload.stage === 'image' ? 'image' : 'video'}, signal});
      ensure(chat.uuid && chat.id, 'SYNTX не вернул chat UUID/ID', 502);
      const descriptor = {chatId: chat.id, chatUUID: chat.uuid, stage: payload.stage};
      // Save the dedicated chat before a billable request. Recovery only polls this chat.
      checkpoint?.(JSON.stringify(descriptor));
      const settings = {model_type: payload.model, aspect_ratio: '16:9'};
      let response;
      if (payload.stage === 'image') {
        Object.assign(settings, {n: 1, image_url: [], quality: 'medium', image_size: '1K'});
        response = await request('syntx', '/api/v1/design/generate?ai_name=sora-images', {method: 'POST', body: {chat_uuid: chat.uuid, prompt: payload.prompt, settings}, signal});
      } else {
        const form = new FormData(); form.append('files', new Blob([decode64(payload.image)], {type: 'image/png'}), 'source.png');
        form.append('destination', 'hidden'); form.append('check_duplicates', 'true'); form.append('model_type', payload.model);
        const uploaded = await request('syntx', '/api/v1/chats/upload-files', {method: 'POST', form, signal});
        const files = uploaded.files || uploaded.data?.files;
        ensure(files?.[0]?.url, 'SYNTX не вернул URL загруженного изображения', 502);
        Object.assign(settings, {file_urls: [files[0].url], resolution: '1080p', video_duration: Math.max(5, Math.min(10, Math.ceil(payload.duration)))});
        response = await request('syntx', `/api/v1/video/generate?ai_name=${encodeURIComponent(payload.service)}`, {method: 'POST', body: {chat_id: chat.id, prompt: payload.prompt, settings}, signal});
      }
      if (response.id) descriptor.messageId = response.id;
      return {id: JSON.stringify(descriptor)};
    },
    async status(externalId, signal) {
      const descriptor = JSON.parse(externalId), chatId = encodeURIComponent(descriptor.chatId);
      const response = await request('syntx', `/api/v1/chats/${chatId}/messages?page=1&page_size=50`, {signal});
      const messages = response.messages || response.data?.messages || [];
      const generated = messages.filter((message) => message.author_id === -1).flatMap((message) => message.message_object || []);
      if (generated.some((item) => item.error)) return {status: 'failed'};
      const result = generated.find((item) => ['image', 'image_url', 'video'].includes(item.object_type) && item.completed && item.object_url);
      return result ? {status: 'succeeded', resultURL: result.object_url} : {status: 'running'};
    },
    async cancel(externalId) {
      const descriptor = JSON.parse(externalId);
      if (!descriptor.messageId) throw new AppError(409, 'SYNTX: неизвестен ID сообщения; отмена оплаты не подтверждена');
      return request('syntx', `/api/v1/chats/${encodeURIComponent(descriptor.chatId)}/messages/${encodeURIComponent(descriptor.messageId)}/cancel`, {method: 'POST'});
    },
    async result(status, _stage, signal) { return download(status.resultURL, signal); },
  };
  return {fastgen, lumean, elevenlabs, syntx, request, download};
}
export async function poll(client, externalId, config, signal, onStatus = () => {}) {
  const deadline = Date.now() + config.jobTimeout;
  let failures = 0;
  while (Date.now() < deadline) {
    signal.throwIfAborted();
    try {
      const status = await client.status(externalId, signal); failures = 0; onStatus(status);
      if (['succeeded', 'completed', 'result_delivered'].includes(status.status)) return status;
      if (['failed', 'error', 'cancelled', 'canceled', 'partial'].includes(status.status)) throw new AppError(422, `Провайдер завершил задачу со статусом ${status.status}`, {terminal: true});
    } catch (error) {
      if (signal.aborted) throw signal.reason;
      if (error.details?.terminal || error.status === 401 || error.details?.providerStatus === 404) throw error;
      failures++; if (failures >= 8) throw error;
    }
    await pause(Math.min(config.poll * 2 ** Math.min(failures, 3), 60000), undefined, {signal});
  }
  throw new AppError(504, 'Время ожидания истекло. Внешний ID сохранён; повтор продолжит проверку существующей задачи.');
}
