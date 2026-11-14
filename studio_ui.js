// Native studio controls. No audio playback or remote interface assets.
let studioBusy=false, studioTaskId=null, studioTaskAction=null, studioBoardNeedsRetry=false;
function studioBoardGuard() {
  if(!studioBoardNeedsRetry)return true;
  $('s_prep2').textContent='Улучшенная постановка не создана. Сейчас открыта прежняя версия; повторите создание в «Студии» или явно откройте нужную сохранённую версию.';
  return false;
}
function studioBoardRequested() { studioBoardNeedsRetry=true; }
function studioSyncGuard() { if(studioBoardNeedsRetry){$('btn_go').disabled=true;if($('board_preview'))$('board_preview').disabled=true;} }
function studioBoardReady() { studioBoardNeedsRetry=false;$('btn_go').disabled=false;if($('board_preview'))$('board_preview').disabled=false; }
function studioFields() {
  return {enhancement_level:$('studio_level').value,editing_pace:$('studio_pace').value,
    visual_detail:$('studio_detail').value,render_quality:$('studio_quality').value,
    caption_style:$('studio_captions').value==='off'?'source':$('studio_captions').value,
    caption_position:$('studio_caption_position').value,on_screen_labels:$('studio_labels').checked};
}
function loadStudioSettings(c) {
  channelDesignLoad(c);
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
  studioSyncGuard();
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
    studioTaskId=result.task_id;studioTaskAction=action;
    if(action==='board')studioBoardNeedsRetry=true;
    pollStudio();
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
      studioBoardReady();boardState=value.board;boardDirty=false;boardPage=0;source='script';F.script=boardState.script;
      renderReview(boardState.script);if(cur!==5)go(5);showStoryboard();renderStudioReport(result.report);
      studioStatus('Улучшенная раскадровка сохранена отдельной версией. Следующий шаг — короткий предпросмотр.');
      await refreshStudioVersions();
    } else if(result.action==='packaging') {
      renderStudioPackage(result.packaging);studioStatus('Упаковка готова. Проверьте обещания заголовков и факты перед публикацией.');
    }
  }catch(error){studioStatus(String(error),true);if(studioTaskAction==='board')studioBoardGuard();}
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
      studioBoardReady();boardState=response.board;boardDirty=false;boardPage=0;source='script';F.script=boardState.script;renderReview(F.script);go(5);showStoryboard();closeStudio();
    }));
  }catch(error){studioStatus(String(error),true);}
}
for(const id of ['studio_captions','studio_wallpaper']) $(id).addEventListener('change',previewStudioCaptions);

function showYouTubeSample() {
  if(studioBusy)return;
  youtubeSampleStart=0;closeStudio();go(4);
  setTimeout(()=>{$('youtube_source_url')?.focus();},280);
}

// Channel design selection is an explicit immutable version, including saved projects.
let channelDesignActive=null, channelDesignEditing=null;
function channelDesignChoice() {
  return channelDesignActive===null?{}:{channel_design_id:channelDesignActive.id||'',channel_design_revision:channelDesignActive.revision||null};
}
function channelDesignBadge() {
  const e=$('channel_design_badge');if(e)e.textContent=channelDesignActive?.id?'Закреплён профиль канала · версия '+channelDesignActive.revision:'Дизайн исходника';
}
function channelDesignLoad(c,force=false) {
  if(channelDesignActive!==null&&!force)return;
  if(Object.hasOwn(c,'channel_design_id')){channelDesignActive={id:c.channel_design_id||'',revision:c.channel_design_revision||null};channelDesignBadge();}
}
function channelDesignPut(p) {
  channelDesignEditing=p?.id?p:null;
  $('cd_name').value=p?.name||'Мой канал';$('cd_description').value=p?.description||'';
  $('cd_palette').value=(p?.palette||[]).join(', ');$('cd_rules').value=p?.rules||'';
  $('cd_motion').value=p?.motion_policy||'local';$('cd_faces').value=p?.faces||'reference';$('cd_layout').value=p?.layout||'source';$('cd_font').value=p?.font||'Arial';
  $('cd_assets').innerHTML=(p?.assets||[]).map((a,i)=>`<div>${esc({reference:'Эталон',host:'Ведущий',logo:'Логотип'}[a.role])}: ${esc(a.name)} <button class="ghost small" onclick="channelDesignAction('remove:${i}')">Убрать из новой версии</button></div>`).join('')||'Файлы ещё не добавлены';
  $('cd_import').disabled=!p?.id;
}
async function channelDesignRefresh() {
  const r=await pywebview.api.channel_designs();if(!r.ok)throw new Error(r.error);
  $('cd_profiles').innerHTML='<option value="">Новый профиль</option>'+r.profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · v${p.revision}</option>`).join('');
  if(channelDesignEditing)$('cd_profiles').value=channelDesignEditing.id;
}
async function openChannelDesign() {
  $('channel_design_dialog').showModal();$('cd_status').textContent='';$('cd_source_url').value=typeof F!=='undefined'?F.rwUrl||'':'';
  try {
    await channelDesignRefresh();
    if(channelDesignActive?.id){const r=await pywebview.api.channel_design_get(channelDesignActive.id,channelDesignActive.revision);if(!r.ok)throw new Error(r.error);channelDesignPut(r.profile);$('cd_profiles').value=r.profile.id;}
    else {const r=await pywebview.api.channel_design_seed(typeof boardState!=='undefined'?boardState?.id:null);if(!r.ok)throw new Error(r.error);channelDesignPut(r.profile);}
  } catch(e){$('cd_status').textContent=String(e);}
}
async function channelDesignView() {
  try {const id=$('cd_profiles').value;if(!id){channelDesignPut(null);return;}
    const r=await pywebview.api.channel_design_get(id);if(!r.ok)throw new Error(r.error);channelDesignPut(r.profile);
  }catch(e){$('cd_status').textContent=String(e);}
}
async function channelDesignAction(action) {
  const buttons=$('channel_design_dialog').querySelectorAll('button,select,input,textarea');buttons.forEach(e=>e.disabled=true);
  let designPoll=null;
  try {
    let r;
    if(action==='learn'){
      $('cd_cancel').hidden=false;$('cd_cancel').disabled=false;
      $('cd_status').textContent='Однократно изучаю стиль образца…';
      designPoll=setInterval(async()=>{try{const s=await pywebview.api.get_state();$('cd_status').textContent=s.message||'Изучаю дизайн…';}catch(e){}},1400);
      r=await pywebview.api.channel_design_learn($('cd_name').value||'Мой канал',$('cd_source_url').value.trim());
    }
    else if(action==='source')r=await pywebview.api.channel_design_select('');
    else if(action==='capture')r=await pywebview.api.channel_design_capture($('cd_name').value||'Мой канал',typeof boardState!=='undefined'?boardState?.id:null);
    else if(action==='select'){
      if(!channelDesignEditing)throw new Error('Сначала сохраните профиль.');
      r=await pywebview.api.channel_design_select(channelDesignEditing.id,channelDesignEditing.revision);
    } else if(action.startsWith('remove:')) {
      r=await pywebview.api.channel_design_remove(channelDesignEditing.id,channelDesignEditing.revision,+action.split(':')[1]);
    } else if(action==='import') {
      if(!channelDesignEditing)throw new Error('Сначала сохраните профиль.');
      r=await pywebview.api.channel_design_import(channelDesignEditing.id,channelDesignEditing.revision,$('cd_role').value);
    } else {
      const value={name:$('cd_name').value,description:$('cd_description').value,
        palette:$('cd_palette').value.split(',').map(s=>s.trim()).filter(Boolean),rules:$('cd_rules').value,
        faces:$('cd_faces').value,font:$('cd_font').value,layout:$('cd_layout').value,motion_policy:$('cd_motion').value};
      r=await pywebview.api.channel_design_save(value,channelDesignEditing?.id||null,channelDesignEditing?.revision||null);
    }
    if(r.cancelled){$('cd_status').textContent='Изучение остановлено; готовые ответы сохранены.';return;}if(!r.ok)throw new Error(r.error);
    channelDesignActive=r.profile?{id:r.profile.id,revision:r.profile.revision}:{id:'',revision:null};channelDesignBadge();
    channelDesignPut(r.profile);await channelDesignRefresh();
    $('cd_status').textContent=r.profile?'Профиль закреплён для новых сборок. Генерация не запускалась.':'Выбран дизайн исходника.';
    mark();renderAccess();
  }catch(e){$('cd_status').textContent=String(e);}
  finally {if(designPoll)clearInterval(designPoll);$('cd_cancel').hidden=true;buttons.forEach(e=>e.disabled=false);$('cd_import').disabled=!channelDesignEditing;}
}

// Clip selection uses the same local backend rules for preview and rendering.
let animationPreview = null, animationPreviewRequest = 0, animationPreviewTimer=null;
const animationHintText=$('animation_hint').textContent;
function queueAnimationPreview(){++animationPreviewRequest;clearTimeout(animationPreviewTimer);animationPreviewTimer=setTimeout(refreshAnimationPreview,250);}
function animationOptions(){return {strategy:$('animation_strategy').value,limit:Number($('animation_limit').value)}}
function animationLoad(value){const a=value||{strategy:'legacy',limit:10};$('animation_strategy').value=a.strategy||'legacy';$('animation_limit').value=a.limit??10;animationDraw();}
function animationDraw(){
  const strategy=animationOptions().strategy, selective=['key','hook','manual'].includes(strategy);
  drawVf();$('vf_row').style.display=strategy==='legacy'?'':'none';$('animation_budget').hidden=!selective;
  const label=document.querySelector('#modes [data-i18n="m_mixed"]');if(label)label.textContent=selective?'Выборочные вставки':t('m_mixed');
  $('animation_hint').hidden=strategy==='legacy';
}
async function animationChanged(){
  try {
    const a=animationOptions();
    if(!Number.isInteger(a.limit)||a.limit<0||a.limit>150)throw new Error('Лимит видеовставок: целое число от 0 до 150.');
    $('animation_hint').textContent=animationHintText;
    if(['key','hook','manual'].includes(a.strategy))document.querySelector('input[name=mode][value=mixed]').checked=true;
    if(a.strategy==='local')document.querySelector('input[name=mode][value=images]').checked=true;
    mark();animationDraw();
    const r=await pywebview.api.save_config({animation:a,mode:mode()});if(r?.ok===false)throw new Error(r.error);
    await refreshAnimationPreview();
  } catch(error){$('animation_hint').textContent=String(error);$('animation_hint').hidden=false;}
}

async function refreshAnimationPreview(){
  clearTimeout(animationPreviewTimer);if(!boardState)return;
  const request=++animationPreviewRequest, board=boardState;
  let r;try{r=await pywebview.api.animation_preview(board.rows,opts());}catch(error){r={ok:false,error:String(error)};}
  if(request!==animationPreviewRequest||board!==boardState)return;
  animationPreview=r;
  if(!r.ok){$('board_status').textContent=r.error;return;}
  $('board_count').textContent=$('board_count').textContent.replace(/ · Видеовставок:.*$/,'')+' · Видеовставок: '+r.count;
  for(const item of r.rows){
    const box=Array.from($('board_rows').querySelectorAll('[data-animate]')).find(e=>e.dataset.animate===item.id);
    const note=Array.from($('board_rows').querySelectorAll('[data-animation-reason]')).find(e=>e.dataset.animationReason===item.id);
    if(box)box.checked=item.selected;
    if(note)note.textContent=item.reason+(item.risk&&item.reason!==item.risk?' · '+item.risk:'');
  }
}
async function animationToggle(input){
  const checked=input.checked,id=input.dataset.animate;
  if(animationOptions().strategy!=='manual'){
    if(!animationPreview?.ok){input.checked=!checked;return;}
    const chosen=new Set(animationPreview.rows.filter(r=>r.selected).map(r=>r.id));
    boardState.rows.forEach(r=>r.animate=chosen.has(r.id));
    $('animation_strategy').value='manual';
  }
  const row=boardState.rows.find(r=>r.id===id);if(row)row.animate=checked;
  markBoardDirty();await animationChanged();
}
