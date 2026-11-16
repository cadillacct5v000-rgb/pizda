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
  return {enhancement_level:$('studio_level').value,original_visual:$('studio_original_visual').value,editing_pace:$('studio_pace').value,
    visual_detail:$('studio_detail').value,render_quality:$('studio_quality').value,
    caption_style:$('studio_captions').value==='off'?'source':$('studio_captions').value,
    caption_position:$('studio_caption_position').value,on_screen_labels:$('studio_labels').checked};
}
function loadStudioSettings(c) {
  if(c.voice_languages)workflowVoiceLanguages=c.voice_languages;
  if(c.channel_voices)workflowChannelVoices=c.channel_voices;
  channelDesignLoad(c);
  const s=c.studio||{};
  $('studio_original_visual').value=s.original_visual||'reference';
  $('studio_level').value=c.studio_configured?(s.enhancement_level||'original'):'original';$('studio_pace').value=s.editing_pace||'balanced';
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
  youtubeSampleStart=0;closeStudio();workflowOpen('render');
  setTimeout(()=>{$('youtube_source_url')?.focus();},280);
}

// Channel design selection is an explicit immutable version, including saved projects.
let channelDesignActive=null, channelDesignEditing=null,channelDesignVoices={},channelDesignDelivery=null,channelDesignMotion=null;
let workflowVoiceLanguages={ru:'русском',en:'английском'},workflowChannelVoices={},workflowVoiceLanguage='';
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
  channelDesignEditing=p?.id?p:null;channelDesignVoices=JSON.parse(JSON.stringify(p?.voices||{}));channelDesignDelivery=p?.delivery||null;channelDesignMotion=p?.reference_animation||null;
  $('cd_presentation').value=p?.presentation||'';
  $('cd_voice_language').innerHTML=Object.entries(workflowVoiceLanguages).map(([code,name])=>'<option value="'+code+'">'+esc(name)+'</option>').join('');
  $('cd_voice_language').value=workflowLanguageCode(F.rwLang)||'ru';
  channelDesignVoiceSummary();
  $('cd_name').value=p?.name||'Мой канал';$('cd_description').value=p?.description||'';
  $('cd_palette').value=(p?.palette||[]).join(', ');$('cd_rules').value=p?.rules||'';
  $('cd_motion').value=p?.motion_policy||'local';$('cd_faces').value=p?.faces||'reference';$('cd_layout').value=p?.layout||'source';$('cd_font').value=p?.font||'Arial';
  $('cd_assets').innerHTML=(p?.assets||[]).map((a,i)=>`<div>${esc({reference:'Эталон',host:'Ведущий',logo:'Логотип',character:'Персонаж — референс',character_layer:'Персонаж — готовый слой'}[a.role])}: ${esc(a.name)} ${esc((a.aliases||[]).join(', '))} <button class="ghost small" onclick="channelDesignAction('remove:${i}')">Убрать из новой версии</button></div>`).join('')||'Файлы ещё не добавлены';
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
    if(action==='learn'||action==='learn_motion'){
      $('cd_cancel').hidden=false;$('cd_cancel').disabled=false;
      $('cd_status').textContent='Однократно изучаю стиль образца…';
      designPoll=setInterval(async()=>{try{const s=await pywebview.api.get_state();$('cd_status').textContent=s.message||'Изучаю дизайн…';}catch(e){}},1400);
      r=action==='learn_motion'?await pywebview.api.channel_animation_learn($('cd_source_url').value.trim()):await pywebview.api.channel_design_learn($('cd_name').value||'Мой канал',$('cd_source_url').value.trim(),$('cd_adapt').checked,$('cd_changes').value,$('cd_learn_motion').checked);
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
      r=await pywebview.api.channel_design_import(channelDesignEditing.id,channelDesignEditing.revision,$('cd_role').value,{aliases:$('cd_aliases').value.split(',').map(s=>s.trim()).filter(Boolean),identity:$('cd_identity').value});
    } else {
      const value={name:$('cd_name').value,description:$('cd_description').value,
        palette:$('cd_palette').value.split(',').map(s=>s.trim()).filter(Boolean),rules:$('cd_rules').value,presentation:$('cd_presentation').value,delivery:channelDesignDelivery,voices:channelDesignVoices,reference_animation:channelDesignMotion,
        faces:$('cd_faces').value,font:$('cd_font').value,layout:$('cd_layout').value,motion_policy:$('cd_motion').value};
      r=await pywebview.api.channel_design_save(value,channelDesignEditing?.id||null,channelDesignEditing?.revision||null);
    }
    if(r.cancelled){$('cd_status').textContent='Изучение остановлено; готовые ответы сохранены.';return;}if(!r.ok)throw new Error(r.error);
    if(action==='learn_motion'){channelDesignMotion=r.reference_animation;$('cd_motion').value=channelDesignMotion.confidence!=='low'&&channelDesignMotion.subject_motion_share>.15?'generated':'local';channelDesignVoiceSummary();$('cd_status').textContent='Характер оживления изучен. Проверьте режим движения и сохраните профиль; сценарий и картинки не создавались.';return;}
    channelDesignActive=r.profile?{id:r.profile.id,revision:r.profile.revision}:{id:'',revision:null};channelDesignBadge();
    workflowChannelVoices=r.profile?.voices||{};await workflowUseChannelVoice(F.rwLang,false);
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
  const strategy=animationOptions().strategy, selective=['key','hook','manual','reference'].includes(strategy);
  drawVf();$('vf_row').style.display=strategy==='legacy'?'':'none';$('animation_budget').hidden=!selective;
  const label=document.querySelector('#modes [data-i18n="m_mixed"]');if(label)label.textContent=selective?'Выборочные вставки':t('m_mixed');
  $('animation_hint').hidden=strategy==='legacy';
}
async function animationChanged(){
  try {
    const a=animationOptions();
    if(!Number.isInteger(a.limit)||a.limit<0||a.limit>150)throw new Error('Лимит видеовставок: целое число от 0 до 150.');
    $('animation_hint').textContent=animationHintText;
    if(a.strategy==='reference')$('animation_hint').textContent='Используется сохранённый характер движения: статика, приближения, сдвиги и примерная доля движения объектов. Сначала подготовьте исходник с этим режимом либо изучите оживление в дизайне канала. Выбор сам по себе не запускает модель. Нейроклипы ограничены лимитом; точные движения и склейки не копируются.';
    if(['key','hook','manual','reference'].includes(a.strategy))document.querySelector('input[name=mode][value=mixed]').checked=true;
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

// Replace only FastGen stages; direct providers and saved assets remain selected.
let elysiumKeyEdit=null;
window.elysiumReplacement={elysium_replace_fastgen:false,elysium_model:'gpt-4.1-mini',elysium_image_model:'google/gemini-3.1-flash-image-preview',elysium_stt_model:'google/gemini-3.6-flash',elysium_tts_model:'google/gemini-3-8-flash-tts',elysium_tts_voice:'Charon'};
function replacementLoad(value){
  if(Object.hasOwn(value,'elysium_replace_fastgen'))for(const key of Object.keys(elysiumReplacement))if(Object.hasOwn(value,key))elysiumReplacement[key]=value[key];
  if(value.elysium_model)models.elysium_model=value.elysium_model;replacementBadge();
}
function replacementBadge(){
  const title=elysiumReplacement.elysium_replace_fastgen?'Elysium':'FastGen';
  SVC_TITLES.fastgen=title;const tile=document.querySelector('#providers input[value=fastgen]')||document.querySelector('input[name=prov][value=fastgen]');
  if(tile){tile.closest('label').querySelector('.t').textContent=title;const q=tile.closest('label').querySelector('.q');if(q)q.dataset.help=elysiumReplacement.elysium_replace_fastgen?'Картинки создаются через Elysium с выбранной моделью. Ключ вводится в «Подключениях». Настройки текстов, голоса и оживления находятся на своих шагах.':'';}
}
function replacementFields(){return {...elysiumReplacement,elysium_model:models.elysium_model}}
function syncReplacement(){
  if(elysiumKeyEdit!==null)single.elysium=elysiumKeyEdit;
  for(const key of ['elysium_image_model','elysium_stt_model','elysium_tts_model','elysium_tts_voice'])if($('replace_'+key))elysiumReplacement[key]=$('replace_'+key).value;
  // Text selectors update one shared model through setTextModel; stale controls cannot override it.
}
function replacementPanel(){
  replacementBadge();const c=elysiumReplacement;
  let html=row('Вместо FastGen','',sel('replace_mode',[['off','Обычный режим'],['on','Всё, что делал FastGen → Elysium']],c.elysium_replace_fastgen?'on':'off'));
  html+=row('Ключ Elysium',LINKS.elysium,keyIn('replace_key','elysium',0,single.elysium||'','Ключ Elysium')+eye('replace_key')+chk('elysium',0,'s_replace_key'),'s_replace_key');
  html+='<button class="ghost" id="replace_sonnet" onclick="useSonnetText(\'elysium\')">Claude Sonnet для всех текстов · Elysium</button><button class="ghost" onclick="useSonnetText(\'anthropic\')">Claude Sonnet · напрямую</button>';
  html+=row('Тексты через Elysium','',sel('replace_text_model',modelOptions('elysium',models.elysium_model),models.elysium_model)+modelButton('elysium','replace_text_model','s_replace_models'),'s_replace_models');
  if(c.elysium_replace_fastgen){
    html+=row('Картинки через Elysium','',sel('replace_elysium_image_model',[
      ['google/gemini-3.1-flash-image-preview','Nano Banana 2 · Gemini Flash Image'],['google/gemini-3-pro-image','Nano Banana Pro · Gemini Pro Image']],c.elysium_image_model));
    html+=row('Распознавание и анализ','',`<input id="replace_elysium_stt_model" value="${esc(c.elysium_stt_model)}" placeholder="google/gemini-3.6-flash">`);
    html+=row('Озвучка вместо FastGen','',sel('replace_elysium_tts_model',[
      ['google/gemini-3-8-flash-tts','Gemini 3.8 Flash TTS'],['google/gemini-3-1-flash-tts','Gemini 3.1 Flash TTS'],['google/gemini-2-5-pro-tts','Gemini 2.5 Pro TTS'],
      ['elevenlabs/text-to-speech-v3-provider-4','ElevenLabs v3 · Elysium'],['elevenlabs/text-to-speech-multilingual-v2-provider-4','ElevenLabs Multilingual v2 · Elysium']],c.elysium_tts_model));
    html+=row('Голос модели Elysium','',`<input id="replace_elysium_tts_voice" value="${esc(c.elysium_tts_voice)}" placeholder="Charon"><button class="ghost" onclick="replacementVoices()">Список голосов</button><select id="replace_voices" hidden></select>`,'s_replace_voices');
    html+='<p class="note">Только этапы FastGen переключаются на Elysium: тексты, анализ, распознавание, картинки, озвучка и оживление. Другие выбранные сервисы сохраняются. Готовые картинки, транскрипция и озвучка переиспользуются; новые запросы оплачиваются с баланса Elysium. Для видео используются настройки Elysium ниже.</p>';
  }
  html+='<button class="ghost" onclick="checkReplacementModels()">Каталог картинок и озвучки</button><button class="ghost" id="replace_apply" onclick="switchToElysium()">Применить Elysium / продолжить текущую работу</button><div id="s_replace" class="status" role="status"></div>';
  return html;
}
function bindReplacement(){
  elysiumKeyEdit=null;document.querySelectorAll('#dropzone input.key[data-k=elysium]').forEach(input=>input.addEventListener('input',()=>{elysiumKeyEdit=input.value.trim()}));
  $('replace_mode')?.addEventListener('change',async()=>{
    syncAll();elysiumReplacement.elysium_replace_fastgen=$('replace_mode').value==='on';
    await saveKeys();renderAccess();
  });
  for(const key of ['elysium_image_model','elysium_stt_model','elysium_tts_model','elysium_tts_voice'])$('replace_'+key)?.addEventListener('change',async()=>{syncReplacement();await saveKeys()});
  $('replace_text_model')?.addEventListener('change',async()=>{setTextModel('elysium',$('replace_text_model').value);await saveKeys()});
}
async function replacementVoices(){
  try{syncAll();await saveKeys();const result=await pywebview.api.tts_voices('elysium');if(!result.ok)throw new Error(result.error);
    const box=$('replace_voices');box.hidden=false;box.innerHTML=result.voices.map(v=>`<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
    box.value=elysiumReplacement.elysium_tts_voice;box.onchange=async()=>{$('replace_elysium_tts_voice').value=box.value;syncReplacement();await saveKeys()};
  }catch(error){$('s_replace_voices').textContent=String(error)}
}
async function switchToElysium(){
  const button=$('w_switch'),status=$('s_replace');if(button)button.disabled=true;
  try{
    syncAll();elysiumReplacement.elysium_replace_fastgen=true;await saveKeys();
    const result=await pywebview.api.switch_to_elysium(replacementFields());if(!result.ok)throw new Error(result.error);
    if(status)status.textContent=result.resuming?'Останавливаю текущие запросы; затем продолжу через Elysium.':'Elysium включён. Следующий запуск использует выбранные модели.';
    if(result.resuming){
      let state;do{await new Promise(resolve=>setTimeout(resolve,300));state=await pywebview.api.get_state();$('p_message').textContent=state.message||'Переключаю на Elysium…';}while(state.service_switch);
      if(result.worker==='run_studio'){studioTaskId=state.studio_task?.id;studioBusy=true;pollStudio();}
      else if(result.worker==='run_storyboard')pollStoryboard();
      else if(result.worker==='run_rewrite'||result.worker==='run_prepare')pollRewrite();
      else {if(cur!==6)go(6);poll();}
    }else renderAccess();
  }catch(error){if(status)status.textContent=String(error);$('p_err').textContent=String(error);}
  finally{if(button)button.disabled=false;}
}

async function useSonnetText(provider){
  try{
    syncAll();await saveKeys();const r=await pywebview.api.use_sonnet(provider);if(!r.ok)throw new Error(r.error);
    if(Array.isArray(r.models)){MODEL_OPTS[provider+'_model']=r.models.map(id=>[id,id]);TASK_MODEL_OPTS[provider+':text']=r.models.map(id=>[id,id]);}
    llm=provider;rwEngine=provider;models[provider+'_model']=r.model;elysiumReplacement.elysium_model=models.elysium_model;
    renderAccess();$('s_replace').textContent='Claude Sonnet выбран для сценариев, рерайта, раскадровки и промптов. Генерация не запускалась.';
  }catch(error){if($('s_replace'))$('s_replace').textContent=String(error);}
}

async function checkReplacementModels(){
  try{syncAll();await saveKeys();const r=await pywebview.api.elysium_replacement_models();if(!r.ok)throw new Error(r.error);
    const c=replacementFields();$('s_replace').textContent=(r.images.includes(c.elysium_image_model)&&r.tts.includes(c.elysium_tts_model))?'Выбранные модели есть в каталоге. Генерация и списание не запускались.':'Одна из выбранных моделей отсутствует в каталоге; выберите другую.';
  }catch(error){$('s_replace').textContent=String(error)}
}

function setTextModel(engine, value){
  models[engine+'_model']=value;
  const ids=[engine+'_model'];
  if(engine==='elysium')ids.push('replace_text_model');
  if(rwEngine===engine)ids.push('rw_model');
  for(const id of ids){const input=$(id);if(!input)continue;
    if(!Array.from(input.options).some(option=>option.value===value)){const option=document.createElement('option');option.value=value;option.textContent=value;input.appendChild(option);}
    input.value=value;
  }
}

// Numbered production workflow. Existing native operations remain explicit actions.
let workflowCurrent='source';
const workflowSteps=[['source','1','Источник'],['style','2','Стиль'],['text','3','Сценарий'],['voice','4','Озвучка'],['board','5','Раскадровка и тайминг'],['images','6','Картинки'],['video','7','Оживление'],['render','8','Проба и сборка']];
const workflowStatic=new Map();
const workflowExtraConnections=new Set();
let workflowOwnScriptDraft='';
function workflowRail(){
  if($('workflow_menu'))return;
  const menu=document.createElement('aside');menu.id='workflow_menu';menu.setAttribute('aria-label','Этапы создания видео');
  menu.innerHTML='<div class="wf-brand">Создание ролика</div><div id="wf_activity" hidden><p id="wf_activity_text" role="status"></p><button onclick="workflowStopTask()">Остановить текущую задачу</button></div>'+workflowSteps.map(([id,n,title])=>`<button type="button" data-workflow="${id}" onclick="workflowOpen('${id}')"><span>${n}</span>${title}</button>`).join('')+'<div class="wf-service-links"><button type="button" data-workflow="connections" onclick="workflowOpen(\'connections\')">Подключения</button><button type="button" data-workflow="queue" onclick="workflowOpen(\'queue\')">Очередь роликов</button></div><p>Переход между пунктами не запускает генерацию.</p>';
  document.body.appendChild(menu);document.body.classList.add('workflow-enabled');workflowWatch();
}
function workflowMark(){
  workflowRail();const step=workflowSteps.find(([id])=>id===workflowCurrent);for(const n of [4,5]){const bar=$('screen'+n)?.querySelector('.stepname');if(bar)bar.textContent=step?'Шаг '+step[1]+' · '+step[2]:workflowCurrent==='connections'?'Подключения':'Очередь роликов';}document.querySelectorAll('[data-workflow]').forEach(button=>{button.classList.toggle('wf-current',button.dataset.workflow===workflowCurrent);if(button.dataset.workflow===workflowCurrent)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');});
}
function workflowNotice(text){$('s_prep').textContent=text;}
function workflowOpen(id){
  if(cur===5 && !$('screen5').classList.contains('leaving') && $('rv_script')?.value.trim())F.script=$('rv_script').value;
  workflowCurrent=id;workflowMark();
  if(id==='board'){
    if(source!=='script'&&!F.script&&!boardState){renderReview(null);go(5);$('s_prep2').textContent='Для готового аудио сцены и таймкоды будут рассчитаны автоматически при сборке. Для ручной раскадровки сначала подготовьте текст сценария.';workflowCurrent='render';workflowMark();return;}
    if(cur!==5){renderReview(boardState?.script||F.script||F.rwText||'');go(5);}
    if(boardState)showStoryboard();else{$('board_controls').hidden=false;$('s_prep2').textContent='Создайте раскадровку после проверки сценария. Это платный текстовый запрос. В каждой строке будут фраза, кадр и длительность.';}
    return;
  }
  if(cur!==4)go(4);else workflowShowPane();
}
function workflowShowPane(){
  document.querySelectorAll('#dropzone [data-workflow-pane]').forEach(p=>p.hidden=p.dataset.workflowPane!==workflowCurrent);
  workflowMark();$('dropzone').scrollTop=0;const i=workflowSteps.findIndex(([id])=>id===workflowCurrent);const next=$('btn_prep');if(next)next.textContent=workflowCurrent==='render'?'Проверить перед сборкой':i<0?'К производству ролика':'Следующий пункт';const previous=$('screen4').querySelector('.nav>button');if(previous)previous.disabled=workflowCurrent==='source';
}
function workflowKeep(selector,host){
  if(!workflowStatic.has(selector)){const node=document.querySelector(selector);if(node)workflowStatic.set(selector,node);}
  const node=workflowStatic.get(selector);if(node&&host)host.appendChild(node);
}
function workflowKeyLabel(provider){return ({elysium:'Elysium',fastgen:'FastGen',anthropic:'Anthropic · Claude',openai:'OpenAI',gemini:'Google · Gemini',elevenlabs:'ElevenLabs',royal:'RoyalTechno',nonstop:'VeoNonStop',yougen:'YouGen',slider:'SecretSlider',google:'Google Images',syntx:'SYNTX',assemblyai:'AssemblyAI',openrouter:'OpenRouter',voicegen:'VoiceGen',voicer:'Voicer',lumean:'Lumean'})[provider]||provider;}
async function workflowCheckConnection(provider,index){
  const put=text=>{const status=$('wf_key_status_'+provider+'_'+index);if(status)status.textContent=text;};put('Проверяю подключение…');
  try{await saveKeys();const r=await pywebview.api.check_connection(provider,index);put(r.message||r.error);}catch(error){put(String(error));}
}
function workflowOrganizeAccess(){
  workflowRail();
  const container=$('dropzone').firstElementChild;if(!container)return;
  const raw=[...container.children];const sections={};
  for(const [id,n,title] of [...workflowSteps,['connections','','Подключения'],['queue','','Очередь роликов']]){
    if(id==='board')continue;
    const pane=document.createElement('section');pane.dataset.workflowPane=id;pane.className='wf-pane';
    pane.innerHTML=`<div class="wf-heading"><span>${n?'Шаг '+n:'Настройки'}</span><h2>${title}</h2><p>${({source:'Выберите содержание будущего ролика. На этом шаге ничего не генерируется.',style:'Укажите свой дизайн или способ использования оформления исходника.',text:'Выберите модель текста. Сначала подготовьте сценарий и проверьте его.',voice:'Голос и произношение проверяются до генерации картинок и видео.',images:'Выберите сервис картинок и правила проверки повторяющихся героев и мест.',video:'Определите, какие планы оживлять и какой моделью.',render:'Сначала проверьте короткую пробу, затем собирайте весь ролик.',connections:'Ключ каждого сервиса вводится здесь один раз. Выбор модели находится на её рабочем шаге.',queue:'Несколько роликов выполняются последовательно; проверьте общие настройки до запуска.'})[id]||''}</p></div>`;
    sections[id]=pane;
  }
  for(const provider of workflowExtraConnections){
    if(container.querySelector('input.key[data-k="'+provider+'"]'))continue;
    if(IMG.includes(provider)){
      if(!keyRows[provider].length)keyRows[provider].push({idx:-1,value:''});
      keyRows[provider].forEach((key,index)=>container.insertAdjacentHTML('beforeend',keyIn('wf_key_'+provider+'_'+index,provider,index,key.value,'API-ключ '+workflowKeyLabel(provider))));
    }else container.insertAdjacentHTML('beforeend',keyIn('wf_key_'+provider,provider,0,TTS_SVCS.includes(provider)?ttsKeys[provider]||'':single[provider]||'','API-ключ '+workflowKeyLabel(provider)));
  }
  const keyGroups=new Map();
  for(const input of container.querySelectorAll('input.key')){
    const group=input.dataset.k+':'+(IMG.includes(input.dataset.k)?input.dataset.i:'0');
    if(!keyGroups.has(group))keyGroups.set(group,[]);keyGroups.get(group).push(input);
  }
  for(const [group,inputs] of keyGroups){
    const provider=inputs[0].dataset.k,index=Number(inputs[0].dataset.i||0);
    const canonical=inputs.find(i=>i.id==='replace_key')||inputs.find(i=>i.id.startsWith('k_'+provider+'_'))||inputs[0];
    const keyrow=document.createElement('div');keyrow.className='lrow wf-connection';keyrow.dataset.keyProvider=provider;
    keyrow.innerHTML=`<div class="lab">${esc(workflowKeyLabel(provider))}<small>Ключ хранится локально и используется выбранными задачами этого сервиса.</small></div><div class="ctl"></div><div class="status" id="wf_key_status_${provider}_${index}" role="status"></div>`;
    const controls=keyrow.querySelector('.ctl');controls.appendChild(canonical);
    const eyeButton=document.createElement('button');eyeButton.className='ghost';eyeButton.textContent='Показать';eyeButton.type='button';eyeButton.onclick=async()=>{await toggle(canonical.id);eyeButton.textContent=canonical.type==='password'?'Показать':'Скрыть';};controls.appendChild(eyeButton);
    const check=document.createElement('button');check.className='ghost';check.type='button';check.textContent='Проверить подключение';check.onclick=()=>workflowCheckConnection(provider,index);controls.appendChild(check);
    if(IMG.includes(provider)){const plus=document.createElement('button');plus.className='ghost';plus.textContent='+ Ключ';plus.onclick=()=>addKey(provider);controls.appendChild(plus);if(inputs[0].dataset.i!=='0'){const minus=document.createElement('button');minus.className='ghost';minus.textContent='Удалить';minus.onclick=()=>rmKey(provider,index);controls.appendChild(minus);}}
    sections.connections.appendChild(keyrow);
    for(const input of inputs){if(input===canonical)continue;const sibling=input.nextElementSibling;if(sibling?.classList.contains('eye'))sibling.remove();input.remove();}
  }
  let previous='source';
  for(const node of raw){
    if(node.classList.contains('qbox')){sections.queue.appendChild(node);continue;}
    let target=previous;
    const has=selector=>node.matches(selector)||!!node.querySelector(selector);
    if(has('#replace_text_model'))continue;
    if(node.tagName==='BUTTON'&&node.textContent.includes('Sonnet'))continue;
    if(has('#s_replace')&&!has('#replace_mode'))target='text';
    else if(has('#replace_mode'))target='connections';
    else if(has('input.base'))target='connections';
    else if(has('#replace_sonnet')||has('#llm_sel')||has('#rw_eng')||has('#rw_words'))target='text';
    else if(has('#replace_elysium_tts_model')||has('#replace_elysium_tts_voice')||has('#tts_sel')||has('#tts_voice'))target='voice';
    else if(has('#replace_elysium_image_model')||has('#location_continuity')||has('#qa_eng'))target='images';
    else if(has('#video_engine')||has('#elysium_video_model'))target='video';
    else if(has('#youtube_mode')||has('#analysis_engine'))target='style';
    else if(has('#youtube_sample_start'))target='render';
    else if(has('#youtube_source_url')||has('#rw_src')||has('#youtube_browser')||has('#stt_sel')||has('#replace_elysium_stt_model')||has('#file_name')||has('#script'))target='source';
    else if(node.tagName==='BUTTON'){
      if(node.textContent.includes('Sonnet'))target='text';
      else if(node.textContent.includes('Каталог'))target='connections';
      else if(node.textContent.includes('Применить Elysium')){const d=document.createElement('details');d.innerHTML='<summary>Переключить незавершённую задачу</summary><p>Останавливает прежние запросы и продолжает работу через Elysium. Новые запросы оплачиваются сервисом.</p>';node.textContent='Продолжить задачу через Elysium';d.appendChild(node);sections.connections.appendChild(d);continue;}
    }
    // Credential-only legacy rows have been replaced by one central connection card.
    if(node.classList.contains('lrow')&&!node.querySelector('input,select,textarea')&&!node.querySelector('.note')&&node.querySelector('.ctl'))continue;
    if(node.classList.contains('lrow'))node.querySelectorAll('button[onclick^="checkRow"],button.eye').forEach(b=>b.remove());
    sections[target].appendChild(node);previous=target;
  }
  const addConnection=document.createElement('div');addConnection.className='wf-help';
  const connectionProviders=['elysium','fastgen','anthropic','openai','gemini','elevenlabs','royal','nonstop','yougen','slider','google','syntx','assemblyai','openrouter','voicegen','voicer','lumean'];
  addConnection.innerHTML='<label>Добавить сервис <select id="wf_connection_provider">'+connectionProviders.map(p=>'<option value="'+p+'">'+esc(workflowKeyLabel(p))+'</option>').join('')+'</select></label><button class="ghost" id="wf_add_connection">Добавить подключение</button><p>Один сохранённый ключ используется на всех шагах, где выбран этот сервис.</p>';
  addConnection.querySelector('button').onclick=()=>{syncAll();workflowExtraConnections.add(addConnection.querySelector('select').value);renderAccess();};
  sections.connections.appendChild(addConnection);
  container.replaceChildren(...Object.values(sections));
  // Reuse the real controls and their listeners rather than copying their values.
  workflowKeep('#providers',sections.images);
  workflowKeep('#screen1 .tiles',sections.images);
  workflowKeep('#screen2 .srow',sections.style);workflowKeep('#custom_row',sections.style);
  workflowKeep('#screen2 .qrow:has(#fgq_speed)',sections.images);
  workflowKeep('#modes',sections.video);workflowKeep('#animation_strategy',null);
  const animation=$('animation_strategy')?.closest('.qrow');if(animation&&!workflowStatic.has('animation'))workflowStatic.set('animation',animation);
  if(workflowStatic.has('animation'))sections.video.appendChild(workflowStatic.get('animation'));
  for(const selector of ['#vf_row','#animation_hint'])workflowKeep(selector,sections.video);
  const motionHelp=document.createElement('div');motionHelp.className='wf-help';motionHelp.innerHTML='<strong>Оживление как в образце</strong><p>Изучите короткие временные отрезки один раз, затем сохраните профиль в дизайне канала. Он задаёт характер камеры и примерную долю сцен с движением объектов, сохраняя своё оформление. Локальные сдвиги и приближения не требуют видеомодели; движение объектов генерируется отдельно и может отличаться от исходника.</p><button class="ghost" onclick="openChannelDesign()">Настроить оживление в дизайне канала</button>';sections.video.appendChild(motionHelp);
  for(const selector of ['#hd','#qa'])workflowKeep(selector,sections.images);
  for(const selector of ['#motion','#subs','#speed'])workflowKeep(selector,sections.render);
  const fps=$('render_fps')?.closest('label');if(fps&&!workflowStatic.has('fps'))workflowStatic.set('fps',fps);if(workflowStatic.has('fps'))sections.render.appendChild(workflowStatic.get('fps'));
  const styleHelp=document.createElement('div');styleHelp.className='wf-help';styleHelp.innerHTML='<strong>Дизайн канала</strong><p>Сохранённый профиль позволяет использовать новые ссылки только как источник содержания. Повторное изучение чужого стиля не требуется. После самостоятельной постановки здесь можно сохранить её оформление для следующих роликов.</p><button class="ghost" onclick="openChannelDesign()">Выбрать или сохранить дизайн канала</button>';
  sections.style.querySelector('.wf-heading').after(styleHelp);
  const sourceMode=document.createElement('div');sourceMode.className='wf-help';sourceMode.innerHTML='<label>Содержание ролика <select id="wf_source_kind"><option value="youtube">Видео YouTube</option><option value="text">Мой текст</option><option value="audio">Готовая озвучка</option></select></label><p>Ссылка или текст — содержание. Стиль и голос выбираются отдельно на следующих шагах.</p>';sections.source.querySelector('.wf-heading').after(sourceMode);
  sourceMode.querySelector('select').value=source!=='script'?'audio':rwOn()&&rwSrc==='youtube'?'youtube':'text';
  sourceMode.querySelector('select').onchange=async event=>{syncAll();const value=event.target.value;if(F.script)workflowOwnScriptDraft=F.script;resetStoryboard();reviewProfileId=null;reviewSourceProfile=null;if(value==='audio'||value==='youtube')F.script='';else F.script=F.script||workflowOwnScriptDraft;source=value==='audio'?'mp3':'script';rwSrc=value==='youtube'?'youtube':'text';$('use_rw').checked=value==='youtube';$('use_tts').checked=value!=='audio';await pywebview.api.save_config({source,rewrite:$('use_rw').checked,rewrite_source:rwSrc});renderAccess();};
  const sonnet=document.createElement('div');sonnet.className='wf-help';sonnet.innerHTML=`<strong>Быстрый выбор текстовой модели</strong><p>Выбирает доступный Sonnet из каталога и применяет к сценарию, раскадровке и промптам. Генерация не запускается.</p><button class="ghost" id="replace_sonnet" onclick="useSonnetText(llm==='anthropic'?'anthropic':'elysium')">Выбрать Claude Sonnet · по каталогу</button>`;sections.text.querySelector('.wf-heading').after(sonnet);
  const kind=sourceMode.querySelector('select').value;
  if($('youtube_source_url'))$('youtube_source_url').closest('.lrow').hidden=kind!=='youtube';
  if($('rw_src')){const old=$('rw_src').closest('.lrow');if(!old.querySelector('textarea'))old.hidden=true;else $('rw_src').hidden=true;}
  if(kind==='text'){
    const rewriteToggle=document.createElement('label');rewriteToggle.className='wf-help';rewriteToggle.innerHTML='<input type="checkbox" id="wf_rewrite_text"> Переписать мой текст моделью <small>Выключено — используем текст без рерайта.</small>';
    rewriteToggle.querySelector('input').checked=rwOn();rewriteToggle.querySelector('input').onchange=async event=>{syncAll();$('use_rw').checked=event.target.checked;if(event.target.checked)F.rwText=F.script;else F.script=F.script||F.rwText;await pywebview.api.save_config({rewrite:event.target.checked});renderAccess();};sections.source.appendChild(rewriteToggle);
  }
  const textAction=document.createElement('div');textAction.className='wf-action';textAction.innerHTML='<button class="primary" id="wf_prepare_text" onclick="startAll(false, true)">Создать сценарий заново · платный запрос</button><button class="ghost" onclick="workflowEditScript()">Открыть подготовленный сценарий</button><p>Проверьте факты и формулировки до озвучки. Для YouTube с анализом также оплачивается разбор исходника.</p>';sections.text.appendChild(textAction);
  if(!rwOn())textAction.querySelector('#wf_prepare_text').textContent='Проверить свой сценарий';
  if(boardState){const reuse=document.createElement('button');reuse.className='primary';reuse.id='wf_reuse_board';reuse.textContent='Использовать готовую раскадровку · '+boardState.rows.length+' планов';reuse.onclick=()=>workflowOpen('board');textAction.prepend(reuse);}
  const youtubePrepare=$('youtube_prepare');if(youtubePrepare){youtubePrepare.textContent=boardState?'Создать новый сценарий и планы по ссылке · платно':'Подготовить сценарий и планы по ссылке · платно';if(rwSrc==='youtube'&&rwOn())textAction.querySelector('#wf_prepare_text').hidden=true;textAction.prepend(youtubePrepare);}
  const voiceHelp=document.createElement('div');voiceHelp.className='wf-help';voiceHelp.innerHTML='<strong>Сначала проверьте голос</strong><p>Язык текста, выбранный голос и манера чтения — разные настройки. Анализ исходника не гарантирует перенос акцентов. Подготовьте 30-секундную пробу и послушайте результат перед полным роликом.</p><button class="ghost" onclick="showYouTubeSample()">Настроить пробу 30 секунд</button>';sections.voice.querySelector('.wf-heading').after(voiceHelp);
  const voicePreview=document.createElement('div');voicePreview.className='wf-help';voicePreview.innerHTML='<label>Текст пробы голоса<textarea id="wf_voice_text" maxlength="600" rows="4" placeholder="Введите 2–3 фразы на языке будущего ролика. Для русского проверьте ударения, окончания и отсутствие иностранного акцента."></textarea></label><button class="primary" id="wf_voice_preview" onclick="workflowVoiceSample()">Создать пробу голоса · платный синтез</button><button class="ghost" onclick="workflowStopVoice()">Остановить пробу</button><div id="wf_voice_status" role="status"></div><div id="wf_voice_player"></div><p>Создаётся только короткая озвучка этого текста. Картинки, распознавание и видео не запускаются. Совпадение интонации с исходником нужно оценить на слух.</p>';sections.voice.appendChild(voicePreview);
  const voiceLanguage=document.createElement('label');voiceLanguage.innerHTML='Язык пробы <select id="wf_voice_language"><option value="">Автоопределение по тексту</option>'+Object.entries(workflowVoiceLanguages).map(([code,name])=>'<option value="'+code+'">'+esc(name)+'</option>').join('')+'</select>';voicePreview.prepend(voiceLanguage);$('wf_voice_language').value=workflowVoiceLanguage;
  $('wf_voice_language').onchange=async event=>{workflowVoiceLanguage=event.target.value;await workflowUseChannelVoice(workflowVoiceLanguage,true);renderAccess();};
  voicePreview.querySelector('textarea').value=workflowVoiceDraft||(F.script||F.rwText||'').slice(0,450);voicePreview.querySelector('textarea').oninput=event=>workflowVoiceDraft=event.target.value;
  if(source!=='script'){
    voicePreview.hidden=true;
    voiceHelp.innerHTML='<strong>Используется готовая озвучка</strong><p>Новый синтез не нужен: в сборку поступит выбранный аудиофайл. Проверьте его произношение и подачу в своём плеере до генерации кадров.</p><button class="ghost" onclick="workflowOpen(\'source\')">К выбору аудиофайла</button>';
  }
  const imageTrial=document.createElement('div');imageTrial.className='wf-help';imageTrial.innerHTML='<strong>Проверьте картинки до оживления</strong><p>Проба использует готовую раскадровку и создаёт недостающие картинки без запросов видеомодели. Подготовка озвучки для раскадровки может охватывать весь сценарий; короткая проба голоса находится в пункте 4.</p><button class="primary" id="wf_image_trial" onclick="workflowImageTrial()">Проба картинок без оживления · платно</button>';sections.images.appendChild(imageTrial);
  const renderHelp=document.createElement('div');renderHelp.className='wf-help';renderHelp.innerHTML='<p>Короткая проба запускает платную подготовку выбранного отрезка. Полная сборка создаёт недостающие кадры и клипы; совместимые готовые части используются повторно.</p><button class="primary" onclick="workflowOpen(\'board\')">К раскадровке и предпросмотру</button><button class="ghost" onclick="workflowEditScript()">Проверить сценарий перед сборкой</button>';sections.render.querySelector('.wf-heading').after(renderHelp);
  container.replaceChildren(...Object.values(sections));
  for(const pane of Object.values(sections))pane.querySelectorAll('.lrow .ctl').forEach(ctl=>{if(ctl.querySelector('input.key'))return;});
  const elyVoice=source==='script'&&!!elysiumReplacement.elysium_replace_fastgen&&tts==='fastgen';
  for(const id of ['replace_elysium_tts_model','replace_elysium_tts_voice'])if($(id))$(id).closest('.lrow').hidden=!elyVoice;
  if($('tts_voice'))$('tts_voice').closest('.lrow').hidden=elyVoice;
  if($('replace_elysium_image_model'))$('replace_elysium_image_model').closest('.lrow').hidden=!provs().includes('fastgen');
  if($('replace_elysium_stt_model'))$('replace_elysium_stt_model').closest('.lrow').hidden=stt!=='fastgen'&&analysisEngine!=='fastgen';
  for(const id of ['replace_elysium_tts_model','replace_elysium_tts_voice','replace_elysium_image_model'])if($(id)){
    const label=$(id).closest('.lrow').querySelector('.lab');label.textContent=({'replace_elysium_tts_model':'Модель озвучки Elysium','replace_elysium_tts_voice':'Голос Elysium','replace_elysium_image_model':'Модель картинок Elysium'})[id];
  }
  const originalMode=$('studio_level').value==='original';
  const approach=document.createElement('div');approach.className='wf-help';
  approach.innerHTML='<label for="wf_approach"><strong>Как использовать исходник</strong></label><select id="wf_approach">'+Array.from($('studio_level').options,o=>'<option value="'+esc(o.value)+'"'+(o.selected?' selected':'')+'>'+esc(o.text)+'</option>').join('')+'</select><p>Самостоятельный ролик: тема и сильные объяснительные приёмы исходника → свой угол, новая структура, собственные сцены и визуальная система. Анализ и сценарий оплачиваются и сохраняются по этапам. Выбор действует при следующем явном создании новой версии; готовые планы не меняются. Это не гарантия авторских прав или решения YouTube.</p>';
  if(originalMode&&!channelDesignChoice().channel_design_id){const visual=document.createElement('label');visual.innerHTML='Визуальная основа <select id="wf_original_visual">'+Array.from($('studio_original_visual').options,o=>'<option value="'+o.value+'"'+(o.selected?' selected':'')+'>'+esc(o.text)+'</option>').join('')+'</select>';approach.appendChild(visual);}
  sections.style.querySelector('.wf-heading').after(approach);
  $('wf_approach').addEventListener('change',async event=>{const selected=event.target.value;$('studio_level').value=selected;await pywebview.api.save_config({studio:studioFields()});renderAccess();});
  if($('wf_original_visual'))$('wf_original_visual').addEventListener('change',async event=>{const selected=event.target.value;$('studio_original_visual').value=selected;await pywebview.api.save_config({studio:studioFields()});renderAccess();});
  const designSelected=!!channelDesignChoice().channel_design_id;
  if(originalMode&&$('source_processing_note'))$('source_processing_note').textContent='Самостоятельные сцены создаются по новому сценарию. Исходные планы и последовательность склеек не копируются. '+(designSelected?'Используется сохранённый дизайн канала.':'Видеоряд при включённом анализе изучается выборочно как ориентир, без разбора каждого плана.')+' Подготовка нового замысла и генерация оплачиваются сервисами.';
  const styleFromSource=rwOn()&&rwSrc==='youtube'&&!designSelected&&(originalMode?$('studio_original_visual').value==='reference':analyzeVideo);
  const styleRow=workflowStatic.get('#screen2 .srow');if(styleRow)styleRow.hidden=designSelected||styleFromSource;
  if($('custom_row'))$('custom_row').hidden=designSelected||styleFromSource;
  if($('youtube_mode')){
    $('youtube_mode').closest('.lrow').hidden=originalMode;
    $('youtube_mode').closest('.lrow').querySelector('.lab').textContent='Откуда брать оформление';
    for(const option of $('youtube_mode').options)option.text=({montage:'Стиль исходника + его планы',full:'Стиль исходника + новые планы',text:'Только содержание → мой стиль',channel:'Только содержание → дизайн канала'})[option.value]||option.text;
  }
  if(boardState){const note=document.createElement('p');note.className='note';note.textContent='Стиль текущей раскадровки сохранён в её версии. Для другого оформления создайте новые планы из готового сценария; переписывать сам текст не требуется.';sections.style.appendChild(note);}
  const ttsSelector=$('tts_sel');if(ttsSelector)ttsSelector.closest('.lrow').querySelector('.lab').textContent='Сервис озвучки';
  const speedTip=document.querySelector('#dropzone .qrow:has(#fgq_speed) .q');if(speedTip)speedTip.dataset.help='Скорость использует лёгкие модели у поддерживаемых сервисов. Качество включает референсы, когда они поддерживаются. Модель картинок Elysium выбирается отдельно. Постоянство героев нужно проверять вручную.';
  if($('hd'))$('hd').dataset.help='Повышает разрешение генерируемых изображений у поддерживаемого сервиса. У Elysium запрашивается 2K вместо 1K. Это не меняет разрешение видеоклипов и может увеличить стоимость.';
  if($('wf_voice_preview')&&tts==='edge')$('wf_voice_preview').textContent='Создать пробу голоса · Edge без ключа';
  const replacement=$('replace_mode');if(replacement){replacement.closest('.lrow').querySelector('.lab').textContent='Схема работы';replacement.options[0].text='Сервисы по отдельности';replacement.options[1].text='Задачи FastGen через Elysium';}
  if(window.elysiumReplacement?.elysium_replace_fastgen){for(const select of [$('stt_sel'),$('llm_sel'),$('rw_eng'),$('tts_sel'),$('analysis_engine')])for(const option of select?.options||[])if(option.value==='fastgen')option.text='Elysium · вместо FastGen';}
  const nav=$('screen4').querySelector('.nav');nav.innerHTML='<button class="ghost" onclick="workflowPrevious()">Предыдущий пункт</button><button class="ghost" onclick="workflowOpen(\'board\')">5 · Раскадровка</button><button class="primary" id="btn_prep" onclick="workflowNext()">Следующий пункт</button>';
  workflowShowPane();
}
function workflowEditScript(){if(source!=='script'){if(!audioPath){workflowOpen('source');workflowNotice('Выберите готовый аудиофайл в пункте 1.');return;}renderReview(null);hideStoryboard();go(5);workflowCurrent='render';workflowMark();return;}if(!F.script&&!F.rwText&&!boardState){workflowCurrent='text';workflowShowPane();workflowNotice('Сначала подготовьте сценарий или вставьте свой текст.');return;}renderReview(boardState?.script||F.script||F.rwText);hideStoryboard();go(5);workflowCurrent='text';workflowMark();}
function workflowNext(){if(workflowCurrent==='render'){workflowEditScript();return;}const i=workflowSteps.findIndex(([id])=>id===workflowCurrent);workflowOpen(workflowSteps[Math.min(i+1,workflowSteps.length-1)][0]);}
function workflowPrevious(){const i=workflowSteps.findIndex(([id])=>id===workflowCurrent);workflowOpen(workflowSteps[Math.max(0,i-1)][0]);}

let workflowVoiceDraft='';
async function workflowVoiceSample(){
  const status=$('wf_voice_status'),button=$('wf_voice_preview');if(button.disabled)return;button.disabled=true;
  try{
    syncAll();const sample={...replacementFields(),text:$('wf_voice_text').value,tts,voice:F.voice,language:workflowVoiceLanguage,source_profile:JSON.parse(JSON.stringify(reviewSourceProfile||null))};await saveKeys();status.textContent='Готовлю пробу голоса…';
    const result=await pywebview.api.voice_sample(sample);
    if(!result.ok)throw new Error(result.error);
    if($('wf_voice_player'))$('wf_voice_player').replaceChildren();
    let state;do{await new Promise(resolve=>setTimeout(resolve,700));state=await pywebview.api.get_state();if($('wf_voice_status'))$('wf_voice_status').textContent=state.message||'Готовлю озвучку…';}while(state.preparing);
    if(state.error)throw new Error(state.error);if(state.cancelled)return;
    const audio=await pywebview.api.voice_sample_result();if(!audio.ok)throw new Error(audio.error);
    if($('wf_voice_player')){const player=document.createElement('audio');player.controls=true;player.src=audio.audio;$('wf_voice_player').replaceChildren(player);$('wf_voice_status').textContent='Прослушайте: понятны ли все слова, верны ли ударения и подходит ли подача. Полный ролик пока не запускался.';}
  }catch(error){if($('wf_voice_status'))$('wf_voice_status').textContent=String(error);}
  finally{if($('wf_voice_preview'))$('wf_voice_preview').disabled=false;}
}
const workflowOriginalLoadConfig=loadConfig;
loadConfig=async function(keepScreen){await workflowOriginalLoadConfig(keepScreen);workflowRail();if(!keepScreen&&cur===1)workflowOpen('source');};

const workflowCreateStoryboard=createStoryboard;
createStoryboard=async function(...args){workflowCurrent='board';workflowMark();return workflowCreateStoryboard(...args);};

function workflowOnScreen(n){
  if(n===6)workflowCurrent='render';
  if(n===5)workflowCurrent=$('board_panel').hidden?'text':'board';
  workflowMark();
}

$('use_qa').addEventListener('change',()=>{if(cur===4)renderAccess();});
for(const input of document.querySelectorAll('input[name=prov]'))input.addEventListener('change',()=>{if(cur===4)renderAccess();});

async function workflowImageTrial(){
  if(!boardState){workflowOpen('board');$('s_prep2').textContent='Сначала создайте или откройте раскадровку.';return;}
  if(boardBusy||studioBusy)return;
  const previous={mode:mode(),animation:animationOptions(),video_first:vf()};
  try{
    if(!await saveStoryboard())return;
    const value=opts();value.source='script';value.script=boardState.script;value.rewrite.on=false;value.storyboard_id=boardState.id;value.source_profile_id=null;
    value.mode='images';value.animation={strategy:'local',limit:0};value.video_first=0;value.motion=false;value.preview={start:Number($('preview_start').value)||0,seconds:30};
    await start(value);
  }finally{await pywebview.api.save_config(previous);}
}

function workflowHelp(){
  let dialog=$('workflow_help_dialog');
  if(!dialog){
    dialog=document.createElement('dialog');dialog.id='workflow_help_dialog';dialog.className='wf-help-dialog';
    dialog.innerHTML=`<header><h2>От ссылки до вашего видео</h2><button class="ghost" onclick="document.getElementById('workflow_help_dialog').close()">Закрыть</button></header>
    <p>Переход по меню сохраняет доступ к настройкам и не запускает нейросеть. Генерация начинается только отдельной кнопкой действия.</p>
    <ol>
    <li><strong>Источник.</strong> Выберите YouTube, свой текст или готовую озвучку. Для YouTube вставьте ссылку. Браузер и cookies нужны только если YouTube требует входа. Распознавание переводит речь в текст и таймкоды; это не новая озвучка.</li>
    <li><strong>Стиль.</strong> «Самостоятельный ролик по референсу» создаёт свой замысел, структуру и визуальную систему на основе полезных принципов исходника. Смена шрифтов и палитры сама по себе не делает копию самостоятельной.  Выберите сохранённый дизайн канала, готовый стиль или опишите свой. Анализ исходника изучает его оформление и планы. Дизайн канала позволяет использовать новые ссылки только как содержание. Смена стиля может потребовать новых изображений.</li>
    <li><strong>Сценарий.</strong> Язык выбирается для новой версии: смена поля сама не переводит готовую раскадровку. Модель пишет рерайт, раскадровку и промпты. Sonnet выбирается по каталогу выбранного сервиса. «Создать заново» — платная подготовка нового текста; «Открыть подготовленный сценарий» и «Использовать готовую раскадровку» не переписывают его. Проверьте факты, язык и объём. В полном разборе YouTube объём следует исходнику; поле количества слов там не используется.</li>
    <li><strong>Озвучка.</strong> Выберите язык пробы. В профиле канала можно закрепить разные голоса для разных языков и общие параметры подачи.  Выберите сервис и голос. Голос с иностранной манерой может не подойти для русского даже у многоязычной модели. Введите несколько фраз и нажмите «Создать пробу голоса»: оплачивается только короткий синтез, без картинок и видео. Послушайте ударения и интонацию. Анализ подачи исходника даёт ориентир; точный перенос интонации и ручной редактор голосовых акцентов пока не реализованы.</li>
    <li><strong>Раскадровка и тайминг.</strong> Для каждого плана указаны фраза, описание изображения, движение и длительность. Текст плана связан со сценарием. При сборке планы автоматически выравниваются по словам озвучки. Ручная длительность меняет темп соответствующей фразы в допустимых пределах. «Сохранить» фиксирует правки; «Открыть сохранённую» возвращает существующую версию без нового анализа.</li>
    <li><strong>Картинки.</strong> Выберите сервис, модель и разрешение. «Качество» включает работу с референсами, когда они поддерживаются; модель Elysium выбирается отдельно. HD повышает размер исходных картинок, а не разрешение видеоклипа. Автоотбор использует зрительную чат-модель: Gemini/Claude/подходящий GPT. Seedream рисует, Kling оживляет — они не проверяют картинки через чат API. Проверки и перерисовки оплачиваются. Единые локации связывают планы одной комнаты, но не гарантируют идеального постоянства. Проба картинок без оживления использует готовые планы и не отправляет задания видеомодели.</li>
    <li><strong>Оживление.</strong> Укажите видеомодель и разрешение. Можно оживлять ключевые сцены, начало и повороты, выбрать планы вручную либо оставить локальное движение картинок. Лимит относится к видеовставкам. Автовыбор локальный, без отдельного запроса. «Только видео» оживляет все планы при выборе по режиму; выборочная стратегия имеет приоритет. Смена модели видео требует новых клипов, а не нового сценария.</li>
    <li><strong>Проба и сборка.</strong> Сначала соберите 20–30 секунд и проверьте голос, кадры и движение. Проба по YouTube готовит только указанный исходный отрезок; предпросмотр существующей раскадровки может сначала подготовить озвучку всего сценария. Затем соберите полный ролик. Субтитры накладываются на готовое видео. Моушн-дизайн — локальные переходы; в режиме исходных склеек/раскадровки он может быть отключён. FPS задаёт частоту кадров итогового файла. Готовый MP4 открывается кнопкой результата или через папку видео.</li>
    </ol>
    <h3>Подключения и выбор моделей</h3><p>Ключ сервиса вводится один раз и используется на всех шагах, где выбран этот сервис. «Показать/Скрыть» меняет видимость ключа. «Добавить сервис» создаёт поле подключения, «+ Ключ» — ещё один ключ того же сервиса. «Проверить подключение» читает данные доступа без генерации. «Модели с сервера» загружает каталог, но не подтверждает качество и право на платную генерацию. Списки текста и проверки изображений разделены по назначению.</p>
    <p>Схема «Задачи FastGen через Elysium» заменяет этапы FastGen. Отдельно выбранные ElevenLabs, Anthropic и другие прямые сервисы сохраняются. Свой адрес API нужен для прокси; обычно его оставляют пустым. Ключ должен принадлежать выбранному сервису и адресу.</p>
    <h3>Очередь, проекты, студия</h3><p>«+ В очередь» добавляет текущий источник; затем можно заполнить следующий. Задания выполняются последовательно. «Разный стиль» задаёт оформление отдельно для каждого задания. «Проекты» сохраняют сценарий, настройки и ссылку на раскадровку; ключи остаются общими. «Студия» содержит улучшение сценария, постановку, редакторскую проверку и идеи заголовков/обложек. Редакторская проверка локальная; создание текста и постановки оплачивается.</p>
    <p>«Остановить текущую задачу» появляется в меню во время работы. Остановка дожидается завершения текущего сетевого запроса; сохранённые части остаются. «Остановить пробу» действует только на короткий синтез голоса.</p><h3>Если видео не отправилось</h3><p>HTTP 502 с media_submission_unconfirmed означает, что создание задания не подтверждено. Это не доказательство плохого ключа или нехватки денег. «Повторить отправку» разрешает новую платную попытку и может вызвать повторное списание. Для разбора используйте «Отчёт». Неопределённые задания не повторяются автоматически.</p>`;
    document.body.appendChild(dialog);
  }
  dialog.showModal();
}

let workflowStopping=false;
async function workflowWatch(){
  try{
    const state=await pywebview.api.get_state();const busy=!!(state.running||state.preparing||state.queue_active||state.service_switch);
    const box=$('wf_activity');if(box){box.hidden=!busy;$('wf_activity_text').textContent=workflowStopping?'Останавливаю задачу; ожидаю завершения текущего запроса…':state.message||'Выполняется задача';}
    if(!busy)workflowStopping=false;
  }catch(error){/* Other workflow controls still report bridge failures explicitly. */}
  setTimeout(workflowWatch,1200);
}
async function workflowStopTask(){try{await pywebview.api.cancel();workflowStopping=true;}catch(error){workflowNotice(String(error));}}
async function workflowStopVoice(){
  const result=await pywebview.api.cancel_voice_sample();if($('wf_voice_status'))$('wf_voice_status').textContent=result.message||result.error;
}

function workflowLanguageCode(language){if(Object.hasOwn(workflowVoiceLanguages,language))return language;return Object.keys(workflowVoiceLanguages).find(code=>workflowVoiceLanguages[code]===language)||null;}
async function workflowUseChannelVoice(language,persist=true){
  const voice=workflowChannelVoices[workflowLanguageCode(language)];if(!voice)return false;
  tts=voice.service;F.voice=voice.id;if($('tts_voice'))$('tts_voice').value=F.voice;if($('tts_sel'))$('tts_sel').value=tts;
  const patch={tts_service:tts,tts_voice:F.voice};
  if(tts==='fastgen'&&elysiumReplacement.elysium_replace_fastgen){if(voice.elysium_voice)patch.elysium_tts_voice=voice.elysium_voice;if(voice.elysium_model)patch.elysium_tts_model=voice.elysium_model;Object.assign(elysiumReplacement,patch);}
  if(persist)await pywebview.api.save_config(patch);return true;
}
function channelDesignVoiceSummary(){
 $('cd_voices').textContent=Object.entries(channelDesignVoices).map(([language,voice])=>language+': '+voice.service+' · '+(voice.elysium_voice||voice.id||'голос сервиса по умолчанию')).join('\n')||'Голоса по языкам ещё не закреплены.';
 $('cd_motion_summary').textContent=channelDesignMotion?'Оживление: '+channelDesignMotion.camera_styles.join(', ')+'; доля движения объектов примерно '+Math.round(channelDesignMotion.subject_motion_share*100)+'%; уверенность '+channelDesignMotion.confidence+'. Это оценка выборочных отрезков.':'Профиль оживления не задан: по одиночным картинкам движение не определяется.';
 $('cd_delivery_status').textContent=channelDesignDelivery?'Сохранены общие параметры подачи из звукового анализа. Это не точная копия интонации.':'Звуковая подача не задана. Её нельзя определить по картинкам.';
}
function channelDesignRememberVoice(){
 const language=$('cd_voice_language').value;
 channelDesignVoices[language]={service:tts,id:F.voice||'',elysium_voice:tts==='fastgen'&&elysiumReplacement.elysium_replace_fastgen?elysiumReplacement.elysium_tts_voice||'':'',elysium_model:tts==='fastgen'&&elysiumReplacement.elysium_replace_fastgen?elysiumReplacement.elysium_tts_model||'':''};
 channelDesignVoiceSummary();$('cd_status').textContent='Голос добавлен в черновик профиля. Нажмите «Сохранить и использовать».';
}
async function channelDesignCopyDelivery(){try{const r=await pywebview.api.channel_design_seed(boardState?.id||null);if(!r.ok)throw new Error(r.error);channelDesignDelivery=r.profile.delivery||null;$('cd_presentation').value=r.profile.presentation||$('cd_presentation').value;channelDesignVoiceSummary();}catch(error){$('cd_status').textContent=String(error);}}
function channelDesignRole(){ $('cd_character_fields').hidden=!['character','character_layer'].includes($('cd_role').value); }

async function channelDesignCopyMotion(){try{const r=await pywebview.api.channel_design_seed(boardState?.id||null);if(!r.ok)throw new Error(r.error);if(!r.profile.reference_animation)throw new Error('В текущем анализе ещё нет временных наблюдений. Изучите оживление по ссылке.');channelDesignMotion=r.profile.reference_animation;channelDesignVoiceSummary();$('cd_status').textContent='Профиль движения взят из готового анализа. Сохраните дизайн канала.';}catch(error){$('cd_status').textContent=String(error);}}
