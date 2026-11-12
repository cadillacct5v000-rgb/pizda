// Native studio controls. No audio playback or remote interface assets.
let studioBusy=false, studioTaskId=null;
function studioFields() {
  return {enhancement_level:$('studio_level').value,editing_pace:$('studio_pace').value,
    visual_detail:$('studio_detail').value,render_quality:$('studio_quality').value,
    caption_style:$('studio_captions').value==='off'?'source':$('studio_captions').value,
    caption_position:$('studio_caption_position').value,on_screen_labels:$('studio_labels').checked};
}
function loadStudioSettings(c) {
  const s=c.studio||{};
  $('studio_level').value=s.enhancement_level||'enhanced';$('studio_pace').value=s.editing_pace||'balanced';
  $('studio_detail').value=s.visual_detail||'rich';$('studio_quality').value=s.render_quality||'high';
  $('studio_captions').value=(!c.studio_configured||c.subtitles)?(s.caption_style||'source'):'off';
  $('studio_caption_position').value=s.caption_position||'bottom';$('studio_labels').checked=s.on_screen_labels!==false;
  let wallpaper='horizon';try{wallpaper=localStorage.getItem('studioWallpaper')||wallpaper;}catch(e){}
  $('studio_wallpaper').value=wallpaper;document.documentElement.dataset.wallpaper=wallpaper;
  previewStudioCaptions();
}
function previewStudioCaptions() {
  const preset=$('studio_captions').value;
  $('studio_caption_demo').className='caption-demo '+preset;
  $('studio_caption_demo').style.opacity=preset==='off'?'.35':'1';
  document.documentElement.dataset.wallpaper=$('studio_wallpaper').value;
  try{localStorage.setItem('studioWallpaper',$('studio_wallpaper').value);}catch(e){}
}
async function openStudio() {
  if (!$('studio_dialog').open) $('studio_dialog').showModal();
  await refreshStudioVersions();
}
function closeStudio() { $('studio_dialog').close(); }
function studioStatus(text,error=false) { const e=$('studio_status');e.textContent=text;e.style.color=error?'var(--err)':'var(--ink2)'; }
function studioLock(busy) {
  studioBusy=busy;boardBusy=busy;
  if($('rv_script'))$('rv_script').disabled=busy;
  for(const e of document.querySelectorAll('#studio_dialog button:not([onclick="closeStudio()"]),#studio_dialog select,#studio_dialog input')) e.disabled=busy;
  $('studio_cancel').hidden=!busy;$('studio_cancel').disabled=false;
  if($('board_controls'))storyboardControls();
}
async function applyStudio(quiet=false) {
  try {
    const value=studioFields();
    const high=value.render_quality==='high';
    const radio=document.querySelector(`input[name=fgq][value=${high?'quality':'speed'}]`);if(radio)radio.checked=true;
    $('use_hd').checked=high;$('use_subs').checked=$('studio_captions').value!=='off';
    await pywebview.api.save_config({studio:value,quality:high?'quality':'speed',hd:high,subtitles:$('use_subs').checked});
    mark();previewStudioCaptions();
    if(cur===5 && !studioBusy)renderReview($('rv_script').value);
    if(!quiet)studioStatus('Настройки сохранены. Готовые материалы не перегенерируются до запуска.');
    return true;
  }catch(error){studioStatus(String(error),true);return false;}
}
async function studioOptions() {
  if(boardState && boardDirty && !await saveStoryboard())throw new Error('Сначала сохраните изменения планов.');
  const value=opts();value.script=boardState?boardState.script:($('rv_script').value||F.script);
  value.studio=studioFields();return value;
}
async function studioAction(action) {
  if(studioBusy)return;
  try {
    if(!await applyStudio(true))return;
    const options=await studioOptions();
    if(!options.script.trim())throw new Error('Сначала подготовьте сценарий или откройте сохранённую раскадровку.');
    studioLock(true);studioStatus('Запускаю задачу студии…');
    const result=await pywebview.api.studio_action(action,options);
    if(!result.ok)throw new Error(result.error);
    studioTaskId=result.task_id;pollStudio();
  }catch(error){studioStatus(String(error),true);studioLock(false);}
}
async function pollStudio() {
  let pending=false;
  try {
    const response=await pywebview.api.studio_status(studioTaskId);
    if(!response.ok)throw new Error(response.error);
    studioStatus(response.message||'Задача выполняется…');
    if(response.task.state==='running'){pending=true;setTimeout(pollStudio,1200);return;}
    if(response.task.state==='error')throw new Error(response.task.error);
    if(response.task.state==='cancelled'){studioStatus('Остановлено. Готовые ответы сохранены.');return;}
    const result=response.result||{};
    if(result.action==='script') {
      resetStoryboard();F.script=result.script;source='script';$('use_tts').checked=true;$('use_rw').checked=false;
      reviewSourceProfile=result.source_profile||null;reviewProfileId=result.source_profile_id||null;
      renderReview(result.script);if(cur!==5)go(5);
      renderStudioReport(result.report);studioStatus('Новая версия сценария готова. Проверьте её, затем создайте улучшенную постановку.');
    } else if(result.action==='board') {
      const value=await pywebview.api.get_storyboard(result.board_id);if(!value.ok)throw new Error(value.error);
      boardState=value.board;boardDirty=false;boardPage=0;source='script';F.script=boardState.script;
      renderReview(boardState.script);if(cur!==5)go(5);showStoryboard();renderStudioReport(result.report);
      studioStatus('Улучшенная раскадровка сохранена отдельной версией. Следующий шаг — короткий предпросмотр.');
      await refreshStudioVersions();
    } else if(result.action==='packaging') {
      renderStudioPackage(result.packaging);studioStatus('Упаковка готова. Проверьте обещания заголовков и факты перед публикацией.');
    }
  }catch(error){studioStatus(String(error),true);}
  finally{if(!pending)studioLock(false);}
}
async function cancelStudio() {try{await pywebview.api.cancel();studioStatus('Останавливаю задачу…');}catch(error){studioStatus(String(error),true);}}
async function studioReport() {
  if(studioBusy)return;
  try{const response=await pywebview.api.studio_report(await studioOptions());if(!response.ok)throw new Error(response.error);renderStudioReport(response.report);studioStatus('Проверка готова. Это измерения текста и редакторские замечания, не прогноз просмотров.');}
  catch(error){studioStatus(String(error),true);}
}
function renderStudioReport(report) {
  const warnings=(report.issues||[]).map(value=>`<li>${esc(value)}</li>`).join('');
  $('studio_output').innerHTML=`<h3>${esc(report.label||'Редакторская проверка')}</h3><div class="studio-metrics"><span>Слов: ${report.word_count||0}</span><span>Предложений: ${report.sentence_count||0}</span><span>Озвучка ≈ ${report.estimated_voice_minutes||0} мин</span><span>Слабых привязок: ${(report.weak_plans||[]).length}</span></div><h3>Начало ролика</h3><p>${esc(report.opening||'Сначала подготовьте сценарий.')}</p><ul>${warnings||'<li>Автоматические проверки не нашли перечисленных проблем. Это не заменяет просмотр и проверку фактов.</li>'}</ul><h3>Проверьте вручную</h3><ul>${(report.review_questions||[]).map(value=>`<li>${esc(value)}</li>`).join('')}</ul>`;
}
function renderStudioPackage(value) {
  $('studio_output').innerHTML=`<h3>Варианты заголовка</h3><ol>${value.titles.map(v=>`<li>${esc(v)}</li>`).join('')}</ol><h3>Варианты начала</h3>${value.hooks.map(v=>`<p>${esc(v)}</p>`).join('')}<h3>Обложки: концепции и промпты</h3>${value.thumbnail_concepts.map(v=>`<p><strong>${esc(v.concept)}</strong><br>Надпись отдельно: ${esc(v.overlay_text)}</p><textarea readonly>${esc(v.image_prompt)}</textarea>`).join('')}<h3>Как проверять после публикации</h3><ol>${value.experiment_plan.map(v=>`<li>${esc(v)}</li>`).join('')}</ol>`;
}
async function studioPreflight() {
  try{const response=await pywebview.api.production_check(await studioOptions());const e=response.estimate||{};
    $('studio_output').innerHTML=`<h3>Готовность к генерации</h3><div class="studio-metrics"><span>Планов ≈ ${e.scenes||'—'}</span><span>Клипов ≈ ${e.clips||0}</span><span>Видеодоступ: ${esc(({allowed:'подтверждён',denied:'запрещён',not_needed:'не требуется',unverified:'не подтверждён'})[response.video_access]||'не подтверждён')}</span></div><ul>${[...(response.blockers||[]),...(response.warnings||[])].map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`;
    studioStatus(response.ok?'Блокирующих проблем не найдено. Начните с предпросмотра.':'Есть блокирующая проблема — генерацию пока не запускайте.',!response.ok);
  }catch(error){studioStatus(String(error),true);}
}
async function studioExport() {
  try{const value=await pywebview.api.export_studio(await studioOptions());if(value.cancelled)return;if(!value.ok)throw new Error(value.error);studioStatus('ZIP сохранён: '+value.path);}
  catch(error){studioStatus(String(error),true);}
}
async function refreshStudioVersions() {
  try{const response=await pywebview.api.storyboard_versions();if(!response.ok)return;
    $('studio_versions').innerHTML=response.versions.map(value=>`<div class="studio-version"><span>${esc(({faithful:'По исходнику',enhanced:'Улучшенная',director:'Расширенная'})[value.level]||value.level)} · ${value.plans} планов · ${esc(value.id.slice(0,8))}</span><button class="ghost" data-studio-version="${esc(value.id)}">Открыть</button></div>`).join('')||'<p class="studio-note">Сохранённых версий пока нет.</p>';
    document.querySelectorAll('[data-studio-version]').forEach(button=>button.addEventListener('click',async()=>{
      if(studioBusy)return;const response=await pywebview.api.get_storyboard(button.dataset.studioVersion);if(!response.ok){studioStatus(response.error,true);return;}
      boardState=response.board;boardDirty=false;boardPage=0;source='script';F.script=boardState.script;renderReview(F.script);go(5);showStoryboard();closeStudio();
    }));
  }catch(error){studioStatus(String(error),true);}
}
for(const id of ['studio_captions','studio_wallpaper']) $(id).addEventListener('change',previewStudioCaptions);
