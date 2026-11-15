/* Local single-user workbench. Remote text is rendered only as text, never as HTML. */
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = {
  tab: 'companion', goals: [], goalId: null, status: null, jobs: [], videos: new Map(),
  sessions: readLocal('cdv.sessions', {}), online: true, selectedJob: null, chatting: false,
  pollBusy: false, toastTimer: null,
};

function readLocal(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function saveLocal(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} }
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function append(parent, ...children) { children.filter(Boolean).forEach(child => parent.append(child)); return parent; }
function setHidden(node, value) { node.classList.toggle('hidden', value); }
function shortText(value, length = 120) {
  const text = String(value ?? '');
  return text.length > length ? `${text.slice(0, length)}…` : text;
}
function formatTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}
function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function currentGoal() { return state.goals.find(goal => goal.id === state.goalId); }
function safeURL(value, { image = false } = {}) {
  if (!value || typeof value !== 'string') return null;
  if (image && /^data:image\/(png|jpeg|webp);base64,[a-z0-9+/=\r\n]+$/i.test(value)) return value;
  try {
    const url = new URL(value, window.location.origin);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}
function link(text, href, className = 'text-link') {
  const url = safeURL(href);
  if (!url) return null;
  const node = el('a', className, text);
  node.href = url; node.target = '_blank'; node.rel = 'noopener noreferrer';
  return node;
}
function action(text, className, callback) {
  const button = el('button', className, text); button.type = 'button';
  button.addEventListener('click', callback); return button;
}
function toast(message, error = false) {
  const node = $('#toast'); node.textContent = message; node.className = `toast${error ? ' error' : ''}`;
  clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => setHidden(node, true), error ? 6500 : 3800);
}
function showError(error) { toast(error?.message || '操作暂时未能完成，请重试。', true); }
function errorText(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(item => `${(item.loc || []).slice(1).join('.')}: ${item.msg || ''}`).join('；');
  return detail?.message || detail?.error || '请求失败，请检查服务状态。';
}
async function api(path, options = {}) {
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 180000);
  try {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      ...options, signal: controller.signal,
      ...(options.body !== undefined ? {body: JSON.stringify(options.body)} : {}),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(errorText(body?.detail || body || `HTTP ${response.status}`));
    return body;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求等待时间较长。你可以在探索记录中检查执行状态，再决定是否重试。');
    if (error instanceof TypeError) throw new Error('暂时无法连接本机服务，请确认 CoDiscover Video 仍在运行。');
    throw error;
  } finally { clearTimeout(timeout); }
}
async function busy(button, task, label = '处理中…') {
  if (button?.disabled) return;
  const old = button?.textContent;
  if (button) { button.disabled = true; button.textContent = label; }
  try { return await task(); } catch (error) { showError(error); }
  finally { if (button) { button.disabled = false; button.textContent = old; } }
}
function empty(container, title, copy, button) {
  const box = el('div', 'empty-state');
  append(box, el('h3', '', title), el('p', '', copy));
  if (button) { button.style.marginTop = '15px'; box.append(button); }
  container.replaceChildren(box);
}
function rememberVideos(videos) { (videos || []).forEach(video => { if (video?.id) state.videos.set(video.id, video); }); }
function tagList(values, negative = false) {
  const wrap = el('div', 'tags');
  (values || []).forEach(value => wrap.append(el('span', `tag${negative ? ' negative' : ''}`, value)));
  return wrap;
}

function switchTab(tab) {
  state.tab = tab;
  $$('.tab-panel').forEach(panel => setHidden(panel, panel.id !== `tab-${tab}`));
  $$('.nav-item').forEach(button => {
    const selected = button.dataset.tab === tab; button.classList.toggle('active', selected);
    if (selected) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  $('#page-name').textContent = {companion:'与 Agent 共行',library:'本地知识库',explore:'探索与设置'}[tab];
  if (tab === 'library') loadLibrary().catch(showError);
  if (tab === 'explore') { loadJobs().catch(showError); loadStatus().catch(showError); }
}
function renderGoals() {
  const list = $('#goal-list'); list.replaceChildren();
  if (!state.goals.length) list.append(el('p', 'sidebar-empty', '从一个小小的愿望开始。'));
  state.goals.forEach(goal => {
    const button = action(goal.text, `goal-item${goal.id === state.goalId ? ' selected' : ''}`, () => selectGoal(goal.id));
    button.title = goal.text; button.setAttribute('aria-pressed', String(goal.id === state.goalId)); list.append(button);
  });
  const select = $('#explore-goal'); select.replaceChildren();
  state.goals.forEach(goal => { const option = el('option', '', shortText(goal.text, 44)); option.value = goal.id; select.append(option); });
  if (state.goalId) select.value = state.goalId;
  $('#explore-submit').disabled = !state.goals.length;
  setHidden($('#empty-goals'), state.goals.length > 0); setHidden($('#companion-content'), !state.goals.length);
  const goal = currentGoal();
  if (goal) {
    $('#current-goal-title').textContent = goal.text;
    const schedule = goal.interval_minutes ? `离线自动探索 · 每 ${goal.interval_minutes >= 60 ? `${goal.interval_minutes / 60} 小时` : `${goal.interval_minutes} 分钟`}` : '按需探索';
    $('#current-goal-context').textContent = [goal.context, schedule].filter(Boolean).join('  /  ');
    renderIntent(goal.intent || {});
  }
}
async function loadGoals() {
  state.goals = (await api('/api/goals')) || [];
  if (!state.goals.some(goal => goal.id === state.goalId)) state.goalId = state.goals[0]?.id || null;
  renderGoals();
}
async function selectGoal(id) {
  if (state.chatting) { toast('这轮对话正在处理，请等回复后再切换目标。'); return; }
  state.goalId = id; saveLocal('cdv.goalId', id); renderGoals();
  await Promise.allSettled([loadMessages(), loadRecommendations()]);
}
function renderIntent(intent) {
  const root = $('#intent-content'); root.replaceChildren();
  const group = (title, content) => append(root, append(el('div', 'intent-group'), el('span', 'label', title), content));
  group('期待的体验', el('p', '', intent.goal || currentGoal()?.text || '尚未开始澄清'));
  if (intent.liked_tags?.length) group('希望多一些', tagList(intent.liked_tags));
  if (intent.disliked_tags?.length) group('希望少一些', tagList(intent.disliked_tags, true));
  if (intent.hypotheses?.length) {
    const list = el('ul', 'hypotheses'); intent.hypotheses.forEach(value => list.append(el('li', '', value)));
    group('待验证的方向', list);
  } else if (!intent.liked_tags?.length) {
    group('尚待发现', el('p', '', '聊聊当下的期待，或者先体验几种不同方向。'));
  }
  if (intent.question) root.append(el('div', 'intent-question', `接下来可以想想：${intent.question}`));
}
function openGoalDialog(edit = false) {
  const goal = edit ? currentGoal() : null;
  $('#goal-form').reset(); $('#goal-id').value = goal?.id || '';
  $('#goal-dialog-title').textContent = goal ? '调整体验目标' : '建立体验目标';
  $('#goal-text').value = goal?.text || ''; $('#goal-context').value = goal?.context || '';
  const interval = String(goal?.interval_minutes || 0);
  if (![...$('#goal-interval').options].some(option => option.value === interval)) {
    const option = el('option', '', `每 ${interval} 分钟`); option.value = interval; $('#goal-interval').append(option);
  }
  $('#goal-interval').value = interval; $('#goal-dialog').showModal();
}
async function saveGoal(event) {
  event.preventDefault();
  await busy($('button[type=submit]', event.currentTarget), async () => {
    const id = $('#goal-id').value;
    const payload = {text: $('#goal-text').value.trim(), context: $('#goal-context').value.trim(), interval_minutes: Number($('#goal-interval').value)};
    if (!payload.text) throw new Error('请先写下希望获得的体验。');
    const goal = await api(id ? `/api/goals/${encodeURIComponent(id)}` : '/api/goals', {method: id ? 'PATCH' : 'POST', body:payload});
    $('#goal-dialog').close(); await loadGoals(); await selectGoal(goal.id); switchTab('companion');
    toast(id ? '目标已更新，后续探索会沿着新的方向进行。' : '目标已建立。可以先聊一聊，或让 Agent 探索一轮。');
  }, '保存中…');
}
function openIntentDialog() {
  const goal = currentGoal(); if (!goal) return;
  const intent = goal.intent || {}; $('#intent-goal').value = intent.goal || goal.text;
  $('#intent-liked').value = (intent.liked_tags || []).join('\n'); $('#intent-disliked').value = (intent.disliked_tags || []).join('\n');
  $('#intent-hypotheses').value = (intent.hypotheses || []).join('\n'); $('#intent-dialog').showModal();
}
async function saveIntent(event) {
  event.preventDefault(); const goal = currentGoal(); if (!goal) return;
  const lines = selector => $(selector).value.split(/[\n,，]/).map(value => value.trim()).filter(Boolean);
  await busy($('button[type=submit]', event.currentTarget), async () => {
    const intent = {...goal.intent, goal:$('#intent-goal').value.trim(), context:goal.context || '', liked_tags:lines('#intent-liked'), disliked_tags:lines('#intent-disliked'), hypotheses:lines('#intent-hypotheses')};
    const updated = await api(`/api/goals/${encodeURIComponent(goal.id)}`, {method:'PATCH',body:{intent}});
    state.goals = state.goals.map(value => value.id === updated.id ? updated : value); renderGoals();
    $('#intent-dialog').close(); await loadRecommendations(); toast('已保存。你确认的理解会用于后续推荐。');
  }, '保存中…');
}

function addMessage(role, text, citations = [], className = '') {
  const root = $('#messages'); const row = el('div', `message ${role} ${className}`);
  const body = append(el('div', 'message-body'), el('span', 'message-role', role === 'user' ? '你' : 'CoDiscover Video'), el('p', 'message-text', text));
  if (citations.length) {
    rememberVideos(citations); const links = el('div', 'message-citations');
    citations.forEach(video => links.append(action(`↗ ${shortText(video.title || video.id, 28)}`, '', () => openVideo(video)))); body.append(links);
  }
  append(row, el('div', 'message-avatar', role === 'user' ? '你' : '✳'), body); root.append(row); root.scrollTop = root.scrollHeight;
  return row;
}
function welcomeMessage() {
  addMessage('assistant', '不必马上找到准确的关键词。\n你希望这段观看时间带来什么：安静的陪伴、一点好奇，还是从工作里抽离？也可以直接说说刚才看过的视频。');
}
async function loadMessages() {
  $('#messages').replaceChildren(); const goalId = state.goalId; const session = state.sessions[goalId];
  if (!session) { welcomeMessage(); return; }
  try {
    const messages = await api(`/api/messages?session_id=${encodeURIComponent(session)}`);
    if (goalId !== state.goalId) return;
    if (!messages?.length) welcomeMessage();
    else messages.forEach(message => addMessage(message.role, message.content || message.text || '', message.citations || []));
  } catch (error) { welcomeMessage(); showError(error); }
}
async function sendChat(event) {
  event.preventDefault(); if (state.chatting) return;
  const goal = currentGoal(); const message = $('#chat-input').value.trim(); if (!goal || !message) return;
  state.chatting = true; $('#chat-submit').disabled = true; $('#chat-input').value = '';
  addMessage('user', message); const pending = addMessage('assistant', '正在结合目标、记忆与内容证据整理思路…', [], 'pending');
  try {
    const response = await api('/api/chat', {method:'POST',body:{message,goal_id:goal.id,...(state.sessions[goal.id] ? {session_id:state.sessions[goal.id]} : {})}});
    pending.remove(); state.sessions[goal.id] = response.session_id; saveLocal('cdv.sessions', state.sessions);
    addMessage('assistant', response.reply, response.citations || []);
    if (response.intent) { goal.intent = response.intent; renderIntent(response.intent); }
    if (response.recommendations) renderRecommendations(response.recommendations);
    await loadStatus().catch(()=>{});
  } catch (error) {
    pending.remove(); addMessage('assistant', `这次请求未完成：${error.message}\n你刚才输入的内容已放回输入框，可以检查连接后重试。`, [], 'error');
    $('#chat-input').value = message;
  } finally { state.chatting = false; $('#chat-submit').disabled = false; $('#chat-input').focus(); }
}

function evidenceLabel(segment) {
  const labels = {subtitle: '字幕片段', storyboard: '画面采样', metadata: '标题 / 简介'};
  return `${labels[segment.source] || '内容证据'}${segment.source === 'metadata' ? '' : ' · ' + formatTime(segment.start)}`;
}
function videoCard(video, recommendation = null, index = 0) {
  state.videos.set(video.id, video);
  const card = el('article', 'video-card');
  const visual = el('div', `video-visual color-${index % 5}`); visual.setAttribute('aria-hidden','true');
  append(visual, el('span','video-symbol',['◌','≈','⌁','✳','◈'][index % 5]), el('span','video-duration',video.duration ? formatTime(video.duration) : '时长未知'));
  const body = el('div','video-card-content');
  const badge = video.source === 'demo' ? el('span','badge demo','演示素材') : (video.content_status === 'metadata_only' ? el('span','badge neutral','仅元信息') : el('span','badge live','含内容证据'));
  append(body, append(el('div','video-card-topline'),el('span','video-card-author',video.author || '作者未知'),badge),el('h3','',video.title || video.id));
  body.append(tagList((video.traits?.length ? video.traits : video.tags || []).slice(0,3)));
  body.append(el('p','video-reason',recommendation?.reason || video.summary || video.description || '等待更多可用内容证据。'));
  const evidence = recommendation?.evidence?.[0] || video.segments?.[0];
  if (evidence) body.append(el('blockquote','video-evidence',`${evidenceLabel(evidence)}  ${shortText(evidence.text, 120)}`));
  else body.append(el('p','video-evidence',video.content_status === 'metadata_only' ? '尚未读取字幕或画面，不能据此判断真实观看体验。' : '当前没有可展示的文字证据。'));
  const actions = el('div','video-actions');
  append(actions, link('去观看 ↗',video.url,'video-link'),action('记录体验', 'text-button', () => openFeedback(video)));
  append(body, append(el('div','video-card-footer'),action('查看证据', 'text-button', () => openVideo(video)),actions));
  append(card,visual,body); return card;
}
function renderRecommendations(recommendations) {
  const root = $('#recommendations'); root.replaceChildren();
  if (!recommendations?.length) {
    empty(root,'给探索留一个起点','知识库还没有适合的内容。先探索一轮，或添加你看过的视频。',action('让 Agent 探索一轮 ↗','button outline small',()=>startExploration(state.goalId,4)));
    return;
  }
  recommendations.forEach((item,index)=>root.append(videoCard(item.video || item,item.video ? item : null,index)));
}
async function loadRecommendations() {
  if (!state.goalId) return;
  const id = state.goalId; const items = await api('/api/recommendations',{method:'POST',body:{goal_id:id,limit:6}});
  if (state.goalId === id) renderRecommendations(items);
}
async function loadLibrary() {
  const query = $('#library-query').value.trim(); const root = $('#library-videos'); root.classList.add('refreshing');
  try {
    const videos = await api(`/api/videos?query=${encodeURIComponent(query)}`); rememberVideos(videos); root.replaceChildren();
    $('#library-summary').textContent = query ? `“${query}” · 找到 ${videos.length} 个视频` : `已保存 ${videos.length} 个视频 · 内容证据与推断分别展示`;
    if (!videos.length) empty(root,query ? '暂时没有匹配的内容' : '知识库等待第一份发现',query ? '试试更宽泛的主题，或让 Agent 继续探索。' : '发起一轮探索，或者添加你自行观看的视频。');
    else videos.forEach((video,index)=>root.append(videoCard(video,null,index)));
  } finally { root.classList.remove('refreshing'); }
}
function openVideo(video) {
  $('#detail-title').textContent = video.title || video.id; const root = $('#video-detail'); root.replaceChildren();
  append(root,append(el('div','detail-meta'),el('span','',video.author || '作者未知'),el('span','',video.duration ? `时长 ${formatTime(video.duration)}` : ''),el('span','',video.source === 'demo' ? '合成演示素材' : `来源 ${video.source || 'B 站'}`),el('span','',video.id)));
  append(root,tagList([...(video.tags || []),...(video.traits || [])].filter((value,index,values)=>values.indexOf(value)===index)));
  const summary = append(el('section','detail-section'),el('h3','','Agent 的内容理解'),el('p','',video.summary || video.description || '尚未生成内容理解。'));
  if (video.content_status === 'metadata_only') summary.append(el('p','notice','当前只有标题、简介等元信息；尚未读取视频字幕或画面。不应视为已观看完整视频。'));
  else summary.append(el('p','help-text',`证据状态：${video.content_status || '有内容证据'} · 内容理解置信度 ${Math.round((video.confidence || 0)*100)}%（不是用户满意度概率）`));
  root.append(summary);
  const segments = append(el('section','detail-section'),el('h3','','原始内容证据'));
  if (video.segments?.length) video.segments.forEach(segment=>append(segments,append(el('div','segment-row'),el('span','segment-time',evidenceLabel(segment)),el('div','segment-text',segment.text))));
  else segments.append(el('p','','没有可展示的字幕片段；请以证据状态说明为准。'));
  root.append(segments);
  if (video.content_status === 'frames' && video.image_urls?.length) {
    const gallery = append(el('section','detail-section'),el('h3','','原始采样画面'));
    const grid = el('div','frame-gallery');
    video.image_urls.slice(0,3).forEach((url,index)=>{
      const src = safeURL(url,{image:true}); if (!src) return;
      const figure = el('figure',''); const image = el('img',''); image.src=src; image.alt=`第 ${index+1} 张故事板采样帧`; image.loading='lazy';
      append(figure,image,el('figcaption','help-text',video.segments?.[index] ? evidenceLabel(video.segments[index]) : `采样 ${index+1}`)); grid.append(figure);
    });
    gallery.append(grid); root.append(gallery);
  }
  const actions = append(el('div','detail-actions'),link('在 B 站观看 ↗',video.url,'button primary'),action('记录这次体验','button outline',()=>{ $('#video-dialog').close(); openFeedback(video); }));
  root.append(actions); $('#video-dialog').showModal();
}
function openImport() {
  $('#import-form').reset(); $('#import-hint').textContent = state.status?.mode === 'demo' ? '当前为演示数据模式，可输入 demo-rain、demo-cook 等演示视频 ID。真实 B 站视频需启动真实数据模式。' : '支持 BV 号和包含 BV 号的完整视频链接。';
  $('#import-dialog').showModal();
}
async function importVideo(event) {
  event.preventDefault(); await busy($('button[type=submit]',event.currentTarget),async()=>{
    const raw = $('#import-id').value.trim(); const match = raw.match(/BV[0-9A-Za-z]{10}/);
    const video = await api('/api/videos/import',{method:'POST',body:{video_id:match ? match[0] : raw}});
    state.videos.set(video.id,video); $('#import-dialog').close(); toast('视频已保存，可以记录你的实际体验。');
    await loadStatus(); if (state.tab === 'library') await loadLibrary();
    if (currentGoal()) openFeedback(video); else openVideo(video);
  },'正在读取内容…');
}
function openFeedback(video = null) {
  if (!currentGoal()) { toast('先建立一个体验目标，再记录反馈。'); openGoalDialog(); return; }
  $('#feedback-form').reset(); $('#feedback-video-id').value = video?.id || '';
  $('#feedback-video-title').textContent = video ? video.title || video.id : `自由体验记录 · ${shortText(currentGoal().text,70)}`;
  $('#feedback-context').value = currentGoal().context || ''; $('#feedback-dialog').showModal();
}
async function saveFeedback(event) {
  event.preventDefault(); await busy($('button[type=submit]',event.currentTarget),async()=>{
    const rating = $('input[name=rating]:checked',event.currentTarget); if (!rating) throw new Error('请选择体验满足程度。');
    const videoId = $('#feedback-video-id').value || null; const note = $('#feedback-note').value.trim();
    if (!videoId && !note) throw new Error('自由体验反馈需要一句感受，帮助 Agent 理解评分的含义。');
    await api('/api/feedback',{method:'POST',body:{goal_id:state.goalId,video_id:videoId,rating:Number(rating.value),note,context:$('#feedback-context').value.trim()}});
    $('#feedback-dialog').close(); await loadGoals(); await loadRecommendations(); await loadStatus();
    toast('体验已记住。推荐会结合这条反馈继续调整。');
  },'保存中…');
}

const statusLabels = {queued:'等待执行',running:'正在探索',completed:'已完成',failed:'失败',paused:'等待你参与',canceled:'已取消',cancelled:'已取消'};
async function startExploration(goalId,maxVideos = 4) {
  if (!goalId) { openGoalDialog(); return; }
  try {
    const job = await api('/api/jobs',{method:'POST',body:{goal_id:goalId,max_videos:maxVideos}});
    state.selectedJob = job.id; switchTab('explore'); await loadJobs(); toast('探索任务已创建。可以在执行记录中查看每一步。');
  } catch (error) { showError(error); }
}
async function loadJobs() {
  state.jobs = (await api('/api/jobs')) || []; renderJobs();
}
function renderJobs() {
  const root = $('#job-list'); root.replaceChildren();
  if (!state.jobs.length) { empty(root,'还没有探索记录','每次搜索、理解和入库都会留下可追溯的执行记录。'); return; }
  state.jobs.forEach(job=>{
    const goal = state.goals.find(value=>value.id===job.goal_id);
    const copy = append(el('div','job-copy'),el('div','job-title',goal?.text || job.goal_text || '内容探索'),el('div','job-meta',`${formatDate(job.created_at)} · ${String(job.id).slice(0,8)}`));
    const stats = job.result || job.stats || {};
    const count = job.processed ?? stats.processed ?? stats.ingested ?? stats.indexed ?? stats.video_ids?.length ?? stats.videos;
    const progress = job.error || job.message || (count !== undefined ? `已处理 ${typeof count === 'number' ? count : count?.length || 0} 个视频` : `本轮预算：最多 ${job.max_videos || 4} 个视频`);
    copy.append(el('div','job-progress',shortText(progress,110)));
    const actions = append(el('div','job-actions'),el('span',`badge ${job.status === 'cancelled' ? 'canceled' : job.status}`,statusLabels[job.status] || job.status),action('执行详情 →','text-button',()=>openJob(job.id)));
    if (['queued','running'].includes(job.status)) actions.append(action('取消','text-button',event=>jobAction(job.id,'cancel',event.currentTarget)));
    if (['paused','failed','cancelled','canceled'].includes(job.status)) actions.append(action('恢复探索','text-button',event=>jobAction(job.id,'resume',event.currentTarget)));
    append(root,append(el('div','job-row'),copy,actions));
  });
}
async function jobAction(id,name,button) {
  await busy(button,async()=>{ await api(`/api/jobs/${encodeURIComponent(id)}/${name}`,{method:'POST',body:{}}); await loadJobs(); if ($('#job-dialog').open) await refreshJobDetail(id); toast(name==='cancel' ? '已请求取消，正在执行的步骤结束后会停止。' : '已恢复探索任务。'); },'处理中…');
}
async function openJob(id) {
  state.selectedJob = id;
  try { await refreshJobDetail(id); if (!$('#job-dialog').open) $('#job-dialog').showModal(); } catch (error) { showError(error); }
}
async function refreshJobDetail(id) {
  const job = await api(`/api/jobs/${encodeURIComponent(id)}`); const root = $('#job-detail'); root.replaceChildren();
  const goal = state.goals.find(value=>value.id===job.goal_id);
  root.append(el('p','muted',goal?.text || '内容探索'));
  append(root,append(el('div','detail-meta'),el('span',`badge ${job.status === 'cancelled' ? 'canceled' : job.status}`,statusLabels[job.status] || job.status),el('span','',`创建于 ${formatDate(job.created_at)}`),el('span','',`任务 ${job.id}`)));
  if (job.error) root.append(el('p','notice error',job.error));
  const events = el('ol','event-list');
  (job.events || []).forEach(event=>{
    const row = el('li');
    const labels = {started:'开始探索',plan:'制定探索计划',planned:'制定探索计划',login_required:'需要扫码登录',search:'搜索候选视频',inspect:'读取视频内容',analyze:'分析内容证据',ingested:'内容已入库',indexed:'内容已入库',skipped:'跳过当前视频',completed:'探索完成',failed:'探索未完成',paused:'等待你参与',cancelled:'探索已取消',llm_usage:'模型调用记录',checkpoint:'保存执行进度'};
    const kind = event.kind || event.event || event.type || event.stage || '状态更新';
    append(row,el('time','event-time',formatDate(event.created_at || event.timestamp || event.at)),el('div','',event.message || labels[kind] || kind));
    const detail = event.data || event.detail;
    if (detail && (typeof detail === 'string' || Object.keys(detail).length)) row.append(el('pre','event-data',typeof detail === 'string' ? detail : JSON.stringify(detail,null,2)));
    events.append(row);
  });
  if (!(job.events || []).length) events.append(el('li','','任务已创建，等待执行记录。'));
  root.append(events);
  if (job.result) root.append(append(el('section','detail-section'),el('h3','','本轮结果'),el('pre','event-data',JSON.stringify(job.result,null,2))));
  const actions = el('div','detail-actions');
  if (['queued','running'].includes(job.status)) actions.append(action('取消此轮探索','button outline',event=>jobAction(id,'cancel',event.currentTarget)));
  if (['paused','failed','cancelled','canceled'].includes(job.status)) actions.append(action('恢复探索','button primary',event=>jobAction(id,'resume',event.currentTarget)));
  root.append(actions);
}
async function pollJobs() {
  if (state.pollBusy || document.hidden) return;
  const active = state.jobs.some(job=>['queued','running'].includes(job.status));
  if (!active && !$('#job-dialog').open) return;
  state.pollBusy = true;
  try {
    const before = state.jobs.filter(job=>['queued','running'].includes(job.status)).map(job=>job.id);
    await loadJobs();
    if ($('#job-dialog').open && state.selectedJob) await refreshJobDetail(state.selectedJob);
    const finished = state.jobs.filter(job=>before.includes(job.id) && !['queued','running'].includes(job.status));
    if (finished.length) { await loadStatus(); await loadRecommendations(); if (state.tab==='library') await loadLibrary(); toast(finished[0].status==='completed' ? '探索已完成，新的内容证据已进入知识库。' : `探索状态更新：${statusLabels[finished[0].status] || finished[0].status}`); }
  } catch {} finally { state.pollBusy = false; }
}

async function loadStatus() {
  const [status, health] = await Promise.all([api('/api/status'),api('/api/health').catch(()=>null)]);
  state.status = {...status,provider:health || status.provider}; renderStatus();
}
function renderStatus() {
  const status = state.status; if (!status) return;
  const demo = status.mode === 'demo'; const badge = $('#mode-badge');
  badge.textContent = demo ? '演示数据模式' : '真实 B 站数据'; badge.className = `badge ${demo ? 'demo' : 'live'}`;
  const notice = $('#global-notice');
  if (demo) { notice.textContent = `当前使用合成演示视频${status.llm_mode === 'deepseek' ? '，对话与内容分析使用真实 DeepSeek API' : '与本地规则演示智能体'}。这些内容用于验证闭环，不是真实 B 站推荐。`; notice.className='notice'; }
  else if (status.llm_mode !== 'deepseek') { notice.textContent='当前连接真实 B 站，内容分析与对话使用本地规则模式。配置 DeepSeek API 后可启用大模型理解。'; notice.className='notice'; }
  else setHidden(notice,true);
  $('#library-count').textContent = status.counts?.videos || 0; $('#stat-videos').textContent=status.counts?.videos || 0; $('#stat-feedback').textContent=status.counts?.feedback || 0;
  const details = $('#connection-details'); details.replaceChildren();
  const provider = status.provider || {};
  const rows = [ ['内容来源',demo ? '合成演示素材' : 'B 站只读接入'], ['推理引擎',status.llm_mode === 'deepseek' ? 'DeepSeek Flash' : '本地规则演示'], ['离线调度',status.scheduler_enabled ? '已启用' : '未启用'], ['平台登录',demo ? '无需登录' : (provider.authenticated || provider.logged_in ? (provider.credential_verified === false ? '本地凭证已保存' : '已登录') : provider.status === 'ok' ? '连接可用' : '待登录 / 匿名')] ];
  rows.forEach(([name,value])=>append(details,append(el('div'),el('dt','',name),el('dd','',value))));
  if (provider.message || provider.detail) details.append(append(el('div'),el('dt','','服务提示'),el('dd','',shortText(provider.message || provider.detail,120))));
  setHidden($('#auth-panel'),demo);
}
function renderPresence() {
  const button=$('#presence-toggle'); button.classList.toggle('offline',!state.online); button.setAttribute('aria-pressed',String(state.online));
  $('#presence-label').textContent=state.online ? '我在线' : '我已离线';
}
async function setPresence(value, notify = false) {
  const result = await api('/api/presence',{method:'POST',body:{online:value}});
  state.online = result.online ?? value; renderPresence();
  if (notify) toast(state.online ? '欢迎回来。新的定时探索会等待你离线后再启动。' : '已切换为离线；已开启定时探索的目标将在到期后执行。');
}
async function startLogin() {
  await busy($('#auth-start'),async()=>{
    const response = await api('/api/auth/start',{method:'POST',body:{}});
    const image = response.qr_image || response.qr_image_url || response.qrcode_image || response.image || response.image_url || response.qr_data_url;
    const url = response.qr_url || response.url || response.login_url;
    const imageURL = safeURL(image,{image:true});
    const fallbackImage = safeURL(url,{image:true});
    if (imageURL) { $('#qr-image').src=imageURL; setHidden($('#qr-image'),false); }
    else if (fallbackImage?.startsWith('data:image/')) { $('#qr-image').src=fallbackImage; setHidden($('#qr-image'),false); }
    else setHidden($('#qr-image'),true);
    const loginURL=safeURL(url); setHidden($('#qr-link'),!loginURL || loginURL.startsWith('data:'));
    if (loginURL) $('#qr-link').href=loginURL;
    $('#qr-status').textContent=response.message || '用 B 站 App 扫码并确认，再检查登录状态。';
    setHidden($('#qr-container'),false);
    if (!imageURL && !fallbackImage) toast('登录请求已创建，但未返回可显示的二维码。请查看服务日志。',true);
  },'生成二维码…');
}
async function pollLogin() {
  await busy($('#auth-poll'),async()=>{
    const response=await api('/api/auth/poll',{method:'POST',body:{}});
    const loginState=response.state || response.status;
    const success=response.authenticated || response.logged_in || ['ok','success','authenticated','logged_in'].includes(loginState);
    const labels={waiting:'等待扫码',scanned:'已扫码，请在手机上确认',expired:'二维码已过期，请重新生成',no_session:'请先重新生成二维码',ok:'登录成功',success:'登录成功',authenticated:'登录成功'};
    $('#qr-status').textContent=response.message || labels[loginState] || loginState || '请完成手机确认后再次检查。';
    if (success) { toast('B 站登录成功，凭据已保存在本机。'); setHidden($('#qr-container'),true); await loadStatus(); }
  },'检查中…');
}
async function exportMemory() {
  await busy($('#export-memory'),async()=>{
    const data=await api('/api/export'); const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob); const anchor=document.createElement('a'); anchor.href=url; anchor.download=`codiscover-video-memory-${new Date().toISOString().slice(0,10)}.json`; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); toast('记忆已导出。导出文件不包含平台登录凭据。');
  },'导出中…');
}
async function clearMemory() {
  if (!window.confirm('清除全部体验目标、视频、对话、反馈与探索记录？此操作无法撤销。建议先导出记忆。')) return;
  await busy($('#clear-memory'),async()=>{
    await api('/api/memory',{method:'DELETE'}); state.sessions={};state.videos.clear();state.goalId=null;state.selectedJob=null;saveLocal('cdv.sessions',{});saveLocal('cdv.goalId',null);
    await Promise.all([loadGoals(),loadStatus(),loadJobs()]); switchTab('companion');toast('本地记忆已清除。');
  },'清除中…');
}

$$('.nav-item').forEach(button=>button.addEventListener('click',()=>switchTab(button.dataset.tab)));
$$('[data-close-dialog]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$$('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if (event.target===dialog) {const rect=dialog.getBoundingClientRect(); if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom) dialog.close();}}));
$('#new-goal').addEventListener('click',()=>openGoalDialog());
$$('[data-action=new-goal]').forEach(button=>button.addEventListener('click',()=>openGoalDialog()));
$('#edit-goal').addEventListener('click',()=>openGoalDialog(true));
$('#goal-form').addEventListener('submit',saveGoal); $('#edit-intent').addEventListener('click',openIntentDialog); $('#intent-form').addEventListener('submit',saveIntent);
$('#chat-form').addEventListener('submit',sendChat);
$('#chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('#chat-form').requestSubmit();}});
$$('[data-prompt]').forEach(button=>button.addEventListener('click',()=>{$('#chat-input').value=button.dataset.prompt;$('#chat-input').focus();}));
$('#new-session').addEventListener('click',()=>{if(state.chatting)return;delete state.sessions[state.goalId];saveLocal('cdv.sessions',state.sessions);$('#messages').replaceChildren();welcomeMessage();toast('已开始新对话，目标与反馈记忆仍然保留。');});
$('#refresh-recommendations').addEventListener('click',event=>busy(event.currentTarget,loadRecommendations,'刷新中…'));
$('#start-from-companion').addEventListener('click',event=>busy(event.currentTarget,()=>startExploration(state.goalId,4),'创建中…'));
$('#import-from-companion').addEventListener('click',openImport);$('#import-from-library').addEventListener('click',openImport);$('#import-form').addEventListener('submit',importVideo);
$('#free-feedback').addEventListener('click',()=>openFeedback());$('#feedback-form').addEventListener('submit',saveFeedback);
$('#library-search-form').addEventListener('submit',event=>{event.preventDefault();busy($('button',event.currentTarget),loadLibrary,'搜索中…');});
$('#explore-form').addEventListener('submit',event=>{event.preventDefault();busy($('#explore-submit'),()=>startExploration($('#explore-goal').value,Number($('#max-videos').value)),'创建中…');});
$('#refresh-jobs').addEventListener('click',event=>busy(event.currentTarget,loadJobs,'刷新中…'));
$('#refresh-status').addEventListener('click',event=>busy(event.currentTarget,loadStatus,'检查中…'));
$('#presence-toggle').addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;try{await setPresence(!state.online,true);}catch(error){showError(error);}finally{button.disabled=false;renderPresence();}});
$('#auth-start').addEventListener('click',startLogin);$('#auth-poll').addEventListener('click',pollLogin);
$('#export-memory').addEventListener('click',exportMemory);$('#clear-memory').addEventListener('click',clearMemory);
window.addEventListener('pagehide',()=>{try{fetch('/api/presence',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({online:false}),keepalive:true});}catch{}});
setInterval(()=>{if(state.online) api('/api/presence',{method:'POST',body:{online:true}}).catch(()=>{});},20000);
setInterval(pollJobs,2000);

(async function init(){
  state.goalId=readLocal('cdv.goalId',null);
  try{
    await Promise.all([loadGoals(),loadStatus(),loadJobs()]);
    await setPresence(true); if(state.goalId)await Promise.all([loadMessages(),loadRecommendations()]);
  }catch(error){const notice=$('#global-notice');notice.textContent=error.message;notice.className='notice error';$('#mode-badge').textContent='连接未就绪';showError(error);}
})();
