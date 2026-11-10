import JSZip from 'jszip';
import {XMLParser} from 'fast-xml-parser';
import {ensure} from './core.mjs';

function contents(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(contents).join('');
  if (!node || typeof node !== 'object') return '';
  return Object.entries(node).filter(([key]) => key !== ':@').map(([key, value]) => key === 'w:br' || key === 'w:tab' ? ' ' : contents(value)).join('');
}
function nodes(tree, key) {
  if (!Array.isArray(tree)) return [];
  return tree.flatMap((node) => Object.entries(node).flatMap(([name, value]) => name === key ? [value] : nodes(value, key)));
}
export async function parseDocx(buffer, promptDocument = false) {
  ensure(buffer.length <= 20 * 1024 * 1024, 'DOCX больше 20 МБ');
  let zip;
  try { zip = await JSZip.loadAsync(buffer); } catch { ensure(false, 'Повреждённый DOCX/ZIP'); }
  const entries = Object.values(zip.files);
  ensure(entries.length <= 5000, 'Слишком много файлов в DOCX');
  const xml = zip.file('word/document.xml'); ensure(xml, 'В DOCX нет word/document.xml');
  // JSZip exposes central-directory sizes before inflation. Enforce both before and after decoding.
  ensure(xml._data?.uncompressedSize <= 8 * 1024 * 1024, 'XML документа больше 8 МБ');
  const source = await xml.async('string');
  ensure(Buffer.byteLength(source) <= 8 * 1024 * 1024 && !/<!DOCTYPE|<!ENTITY/i.test(source), 'Небезопасный XML');
  const tree = new XMLParser({preserveOrder: true, ignoreAttributes: true, trimValues: false, parseTagValue: false, processEntities: true}).parse(source);
  const rows = [], warnings = [], seen = new Set(); let line = 0;
  for (const table of nodes(tree, 'w:tbl')) for (const row of nodes(table, 'w:tr')) {
    line++;
    const cells = nodes(row, 'w:tc').map((cell) => nodes(cell, 'w:p').map(contents).join('\n').trim());
    const match = cells[0]?.match(/\bS(\d{1,4})\.(\d{1,4})\b/i);
    if (!match) { if (cells.some(Boolean)) warnings.push(`Строка ${line}: нет shot ID, строка пропущена`); continue; }
    const shotId = `S${match[1].padStart(2, '0')}.${match[2].padStart(2, '0')}`;
    ensure(!seen.has(shotId), `Строка ${line}: повтор ${shotId}`); seen.add(shotId);
    rows.push({shotId, sceneKey: String(Number(match[1])), narration: promptDocument ? '' : cells[1] || '', visual: promptDocument ? '' : cells[2] || '', prompt: promptDocument ? cells.slice(1).filter(Boolean).join('\n') : ''});
  }
  ensure(rows.length > 0 && rows.length <= 1000, 'Не найдены кадры S01.01… либо более 1000 кадров');
  return {rows, warnings};
}
export function mergePrompts(script, prompts) {
  const result = structuredClone(script), lookup = new Map(prompts.rows.map((row) => [row.shotId, row.prompt]));
  for (const row of result.rows) {
    if (lookup.has(row.shotId)) { row.prompt = lookup.get(row.shotId); lookup.delete(row.shotId); }
    else result.warnings.push(`Для ${row.shotId} нет промпта`);
  }
  for (const shotId of lookup.keys()) result.warnings.push(`Неизвестный ID в промптах: ${shotId}`);
  result.warnings.push(...prompts.warnings); return result;
}
