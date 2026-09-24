import {app,api,can,empty,esc,fmtDate,loading,renderError,setMeta,sourceName,state,toast,watchTask} from './core.js';

export async function renderHotspots(force=false){
  const webMode=state.hotspotSearchMode==='web';
  setMeta('hotspots');loading(webMode?'正在搜索榜单外网页与新闻…':(state.hotspotKeyword?'正在按关键词查询热点…':'正在聚合全网热点…'));
  try{
    if(!state.hotspots.length||force){
      let data;
      if(webMode){
        if(state.hotspotKeyword.length<2){state.hotspots=[];state.hotspotTotal=0;paintHotspots();return;}
        const params=new URLSearchParams({q:state.hotspotKeyword,topic:state.hotspotSearchTopic,time_range:state.hotspotTimeRange,limit:'20',prefer_chinese:String(state.hotspotPreferChinese)});
        data=await api(`/api/search/topics?${params}`);
        data.updated_at=new Date().toISOString();data.total_before_filter=(data.items||[]).length;
      }else{
        const sources=[...state.sourceSet].join(',');
        const params=new URLSearchParams({sources,refresh:'true',limit:'100',keywords:state.hotspotKeyword});
        data=await api(`/api/hotspots?${params}`);
      }
      state.hotspots=data.items||[];
      state.hotspotUpdatedAt=data.updated_at;
      state.hotspotTotal=Number(data.total_before_filter??state.hotspots.length);
      if(!['manual'].includes(state.selectedTopic?.source))state.selectedTopic=state.hotspots.find(x=>x.id===state.selectedTopic?.id)||null;
    }
    if(can('article.create')&&!state.billing){try{state.billing=await api('/api/me/billing');}catch(_){state.billing=null;}}
    paintHotspots();
  }catch(e){renderError(e,webMode?'请到配置中心填写搜索服务 API Key，并测试搜索服务连接。':'热点接口可能受平台反爬影响，可切换数据源后重试。');}
}

function paintHotspots(){
  const keyword=state.hotspotKeyword,webMode=state.hotspotSearchMode==='web';
  const resultText=webMode?(keyword?`全网搜索“${esc(keyword)}”返回 ${state.hotspots.length} 条结果`:'输入关键词后搜索榜单外内容'):(keyword?`关键词“${esc(keyword)}”匹配 ${state.hotspots.length} / ${state.hotspotTotal} 条热点`:`共聚合 ${state.hotspots.length} 条热点`);
  const modeControls=webMode?`<select class="select compact-select" id="searchTopic"><option value="news" ${state.hotspotSearchTopic==='news'?'selected':''}>中文新闻</option><option value="general" ${state.hotspotSearchTopic==='general'?'selected':''}>全网页</option></select><select class="select compact-select" id="searchTimeRange"><option value="24h" ${state.hotspotTimeRange==='24h'?'selected':''}>24 小时</option><option value="7d" ${state.hotspotTimeRange==='7d'?'selected':''}>7 天</option><option value="30d" ${state.hotspotTimeRange==='30d'?'selected':''}>30 天</option><option value="year" ${state.hotspotTimeRange==='year'?'selected':''}>一年</option><option value="all" ${state.hotspotTimeRange==='all'?'selected':''}>不限时间</option></select><label class="search-zh-toggle"><input id="preferChinese" type="checkbox" ${state.hotspotPreferChinese?'checked':''}><span>中文优先</span></label>`:'';
  app.innerHTML=`<div class="page-intro"><div><h2>选择今天值得写的热点</h2><p>支持热榜筛选和榜单外全网网页/新闻搜索。更新于 ${fmtDate(state.hotspotUpdatedAt)}</p></div></div>
  <div class="search-mode-tabs"><button class="chip ${!webMode?'active':''}" data-search-mode="ranking">榜单内查询</button><button class="chip ${webMode?'active':''}" data-search-mode="web">榜单外全网搜索</button></div>
  <div class="toolbar keyword-toolbar">${!webMode?`<div class="chips">${['weibo','zhihu','toutiao','cls','bili','baidu'].map(s=>`<button class="chip ${state.sourceSet.has(s)?'active':''}" data-source="${s}">${sourceName(s)}</button>`).join('')}</div>`:'<div class="web-search-label"><strong>榜单外全网搜索</strong><small>覆盖新闻与网页 · 结果将抓取原网页作为文章证据</small></div>'}<div class="actions keyword-actions"><div class="search keyword-search"><input id="hotspotKeywords" maxlength="200" placeholder="${webMode?'输入榜单之外的话题关键词':'输入关键词，多个用空格或逗号分隔'}" value="${esc(keyword)}"></div>${modeControls}<button class="btn copy-rich" id="queryHotspots">⌕ ${webMode?'全网搜索':'关键词查询'}</button>${keyword?'<button class="btn" id="clearKeyword">清空</button>':''}<button class="btn" id="refreshHotspots">↻ ${webMode?'重新搜索':'刷新榜单'}</button></div></div>
  <div class="query-status"><span>${resultText}</span><small>${webMode?'按内容中文占比优先排序，中文结果排前；搜索摘要仅用于选题初筛，生成时仍会抓取原网页并执行事实核验。':'多个关键词按“任意一个匹配”查询；每次查询都会重新抓取已选平台榜单。'}</small></div>
  <div class="hotspot-layout"><div class="hotspot-list">${state.hotspots.length?state.hotspots.map(hotspotCard).join(''):noKeywordResults(keyword,webMode)}</div>${can('article.create')?`<aside class="card sticky-card"><header class="card-header"><div><h3>生成设置</h3><p>选定热点或自定义关键词后创建文章任务</p></div></header><div class="card-body"><div class="selected-topic">${state.selectedTopic?`<strong>${esc(state.selectedTopic.title)}</strong><small>${sourceName(state.selectedTopic.source)}${state.selectedTopic.source==='manual'?' · 自定义选题':` · 选题分 ${state.selectedTopic.score}`}</small>`:'<small>从左侧选择一个话题，或将关键词作为自定义选题</small>'}</div><div class="field"><label>生成方式</label><select class="select" id="generationMode"><option value="platform">平台生成（可用 ${state.billing?.account?.total_available??0} 次）</option>${can('config.edit')||state.features?.user_api_config!==false?'<option value="personal">使用我的 API（不扣额度）</option>':''}</select><p class="field-hint">平台生成优先扣免费试用，成功保存后才扣除。</p></div><div class="field"><label>文章风格</label><select class="select" id="generateStyle"><option value="deep">深度分析</option><option value="news">资讯解读</option><option value="story">故事化叙事</option></select></div><div class="field section-space"><label>目标字数 <small>留空使用全局配置</small></label><input class="input" id="generateWords" type="number" min="500" max="8000" placeholder="例如 2500"></div><div class="field section-space"><label>权威参考资料 <small>推荐粘贴官方公告或权威报道</small></label><textarea class="textarea" id="referenceMaterial" maxlength="20000" placeholder="可选。粘贴原文、关键数据和来源链接，Agent 将据此约束正文并执行事实核验。"></textarea></div><label class="checkline"><input id="generateImages" type="checkbox" checked><span>生成封面和正文信息图</span></label><button class="btn primary" id="generateButton" style="width:100%" ${state.selectedTopic?'':'disabled'}>✦ 生成文章预览</button><div class="compliance-box">关键词只用于选题检索，不等于事实证据。发布前仍需补充并核验权威信源。</div></div></aside>`:`<aside class="card sticky-card"><header class="card-header"><div><h3>查看模式</h3><p>当前账号无生成权限</p></div></header><div class="card-body"><p class="hint">如需生成文章，请联系管理员分配“编辑”或以上角色。</p></div></aside>`}</div>`;
  bindHotspotEvents();
}

function noKeywordResults(keyword,webMode=false){
  if(!keyword)return webMode?empty('输入关键词开始全网搜索','支持新闻与网页，中文结果优先排序'):empty('暂未获取到热点','尝试更换数据源后重新刷新');
  if(webMode)return `<div class="empty"><strong>没有找到相关内容</strong><span>可更换关键词、调整时间范围或内容类别后重试；也可以直接把“${esc(keyword)}”作为自定义选题。</span><button class="btn primary section-space" id="useCustomTopic">＋ 作为自定义选题</button></div>`;
  return `<div class="empty"><strong>榜单中没有匹配内容</strong><span>可更换关键词，或直接把“${esc(keyword)}”作为自定义选题。</span><button class="btn primary section-space" id="useCustomTopic">＋ 作为自定义选题</button></div>`;
}

function bindHotspotEvents(){
  document.querySelectorAll('[data-source]').forEach(button=>button.addEventListener('click',()=>{const source=button.dataset.source;state.sourceSet.has(source)?state.sourceSet.delete(source):state.sourceSet.add(source);button.classList.toggle('active');}));
  document.querySelectorAll('[data-search-mode]').forEach(button=>button.addEventListener('click',()=>{state.hotspotSearchMode=button.dataset.searchMode;state.hotspots=[];state.selectedTopic=null;paintHotspots();}));
  const query=()=>{
    state.hotspotKeyword=document.querySelector('#hotspotKeywords').value.trim();
    state.hotspotSearchTopic=document.querySelector('#searchTopic')?.value||state.hotspotSearchTopic;
    state.hotspotTimeRange=document.querySelector('#searchTimeRange')?.value||state.hotspotTimeRange;
    state.hotspotPreferChinese=document.querySelector('#preferChinese')?.checked??state.hotspotPreferChinese;
    if(state.hotspotSearchMode==='web'&&state.hotspotKeyword.length<2){toast('全网搜索关键词至少需要两个字符','error');return;}
    renderHotspots(true);
  };
  document.querySelector('#queryHotspots').addEventListener('click',query);
  document.querySelector('#hotspotKeywords').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();query();}});
  document.querySelector('#refreshHotspots').addEventListener('click',()=>{state.hotspotKeyword=document.querySelector('#hotspotKeywords').value.trim();renderHotspots(true);});
  document.querySelector('#clearKeyword')?.addEventListener('click',()=>{state.hotspotKeyword='';renderHotspots(true);});
  document.querySelector('#useCustomTopic')?.addEventListener('click',useCustomTopic);
  document.querySelectorAll('[data-topic]').forEach(button=>button.addEventListener('click',()=>{state.selectedTopic=state.hotspots.find(x=>x.id===button.dataset.topic);paintHotspots();}));
  document.querySelector('#generateButton')?.addEventListener('click',startGenerate);
}

function useCustomTopic(){
  const title=state.hotspotKeyword.trim().slice(0,300);
  if(title.length<2){toast('请输入至少两个字符的选题关键词','error');return;}
  state.selectedTopic={id:`manual-${Date.now()}`,title,source:'manual',rank:null,heat:null,url:'',summary:`用户自定义选题关键词：${title}`,score:0,matched_keywords:[title]};
  paintHotspots();toast('已设为自定义选题，请补充权威参考资料');
}

function hotspotCard(h){
  const queryTags=(h.matched_query_keywords||[]).map(x=>`<span class="query-hit">匹配：${esc(x)}</span>`).join('');
  const langBadge=h.language==='zh'?'<span class="badge gold">中文</span>':'<span class="badge">其他语言</span>';
  return `<article class="hotspot ${state.selectedTopic?.id===h.id?'selected':''}"><div class="score-ring" style="--score:${Math.min(100,h.score)}"><span>${Math.round(h.score)}</span></div><div class="hotspot-content"><h3>${esc(h.title)}</h3>${h.summary?`<p class="hotspot-summary">${esc(h.summary.slice(0,150))}</p>`:''}<div class="hotspot-meta">${langBadge}<span class="badge green">${esc(h.source_name||sourceName(h.source))}</span>${h.source==='web_search'&&h.published_at?`<span>${esc(h.published_at)}</span>`:(h.rank?`<span>榜单 #${h.rank}</span>`:'')}${h.heat?`<span>热度 ${esc(h.heat)}</span>`:''}${queryTags}${(h.matched_keywords||[]).slice(0,2).map(x=>`<span>#${esc(x)}</span>`).join('')}</div></div><div class="hotspot-actions">${h.url?`<a class="btn small ghost" href="${esc(h.url)}" target="_blank" rel="noopener">来源</a>`:''}<button class="btn small ${state.selectedTopic?.id===h.id?'primary':''}" data-topic="${h.id}">${state.selectedTopic?.id===h.id?'已选择':'选择'}</button></div></article>`;
}

async function startGenerate(){
  if(!state.selectedTopic)return;
  const words=Number(document.querySelector('#generateWords').value)||null;
  try{
    const response=await api('/api/generate',{method:'POST',body:JSON.stringify({topic:state.selectedTopic,options:{target_words:words,generation_mode:document.querySelector('#generationMode').value,with_images:document.querySelector('#generateImages').checked,style:document.querySelector('#generateStyle').value,reference_material:document.querySelector('#referenceMaterial').value.trim()}})});
    watchTask(response.task_id,'正在生成文章',task=>{state.billing=null;if(task.result?.article_id)location.hash=`article/${task.result.article_id}`;});
  }catch(e){toast(e.message,'error');}
}
