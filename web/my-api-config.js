import { api, app, can, checked, empty, esc, loading, renderError, setMeta, state, toast } from './core.js';

const labels={llm:'LLM 写作',search:'搜索服务',image:'文生图'};
const value=id=>document.querySelector(id)?.value?.trim()||'';
const imageSourceOptions=(items,current)=>(items||[]).map(x=>`<option value="${esc(x.id)}" ${x.id===current?'selected':''} ${x.supported===false?'disabled':''}>${esc(x.label)}</option>`).join('');

export async function renderMyApiConfig(){
  setMeta('my-api-config');
  if(!can('article.create')){app.innerHTML=`<div class="card">${empty('当前角色无需配置 API','只有管理员、编辑和创作者可以配置写作、搜索和文生图服务')}</div>`;return;}
  if(!can('config.edit')&&state.features?.user_api_config===false){app.innerHTML=`<div class="card">${empty('功能已关闭','管理员已对用户关闭「我的 API 配置」，请使用平台生成或联系管理员')}</div>`;return;}
  loading('正在安全读取个人 API 配置…');
  try{paint(await api('/api/me/api-config'));}catch(e){renderError(e);}
}

function sourceBadge(service){
  const source=service.source;
  const map={personal:['个人配置','green'],system:['系统默认','gold'],missing:['未配置','red']};
  const [text,color]=map[source]||['未配置','red'];
  return `<span class="badge ${color}">${text}</span>`;
}

function keyField(id,service){const key=service.api_key||{};return `<div class="field"><label>API Key <small>${esc(key.masked||'未配置')}</small></label><input class="input" id="${id}" type="password" autocomplete="new-password" placeholder="${key.configured?'留空保持原值':'请输入个人 API Key'}"></div>`;}

function actions(service){return `<div class="personal-config-actions"><button class="btn primary" data-save="${service}">保存${labels[service]}</button><button class="btn" data-test-personal="${service}">测试连接</button><button class="btn danger-outline" data-clear-personal="${service}">清除个人配置</button></div>`;}

function paint(data){
  const s=data.services,d=data.defaults;
  app.innerHTML=`<div class="page-intro"><div><h2>我的 API 配置</h2><p>个人密钥加密保存且不会返回明文。使用个人 API 不扣平台额度；创作者也可选择免费试用或付费额度调用系统服务。</p></div></div><div class="personal-config-note"><strong>费用归属说明</strong><span>使用个人配置产生的模型、搜索和图片费用由对应个人 API 账号承担。微信公众号发布凭证仍由系统统一管理。</span></div><div class="config-grid">
  ${card('llm',s.llm,`<div class="form-grid"><div class="field full"><label>API Base URL</label><input class="input" id="myLlmBase" value="${esc(s.llm.base_url||d.llm.base_url||'')}"></div><div class="field"><label>模型名称</label><input class="input" id="myLlmModel" value="${esc(s.llm.model||d.llm.model||'')}"></div>${keyField('myLlmKey',s.llm)}<div class="field"><label>Temperature</label><input class="input" id="myLlmTemp" type="number" min="0" max="2" step="0.1" value="${s.llm.temperature??d.llm.temperature??0.8}"></div><div class="field"><label>最大 Token</label><input class="input" id="myLlmTokens" type="number" min="256" max="32768" value="${s.llm.max_tokens??d.llm.max_tokens??4096}"></div></div>`)}
  ${card('search',s.search,`<div class="form-grid"><div class="field full"><label>API Base URL</label><input class="input" id="mySearchBase" value="${esc(s.search.base_url||d.search.base_url||'https://api.tavily.com')}"></div>${keyField('mySearchKey',s.search)}<div class="field"><label>搜索深度</label><select class="select" id="mySearchDepth"><option value="advanced" ${(s.search.search_depth||d.search.search_depth)==='advanced'?'selected':''}>Advanced</option><option value="basic" ${(s.search.search_depth||d.search.search_depth)==='basic'?'selected':''}>Basic</option></select></div></div>`)}
${card('image',s.image,`<div class="form-grid"><div class="field full"><label>图片服务源</label><select class="select" id="myImageSource">${imageSourceOptions(d.image.source_catalog,s.image.image_source||d.image.source)}</select><p class="field-hint" id="myImageSourceHint"></p></div><div class="field"><label>模型</label><input class="input" id="myImageModel" value="${esc(s.image.model||d.image.model||'')}"></div><div class="field full"><label>API Base URL</label><input class="input" id="myImageBase" value="${esc(s.image.base_url||d.image.base_url||'')}" placeholder="例如 https://api.example.com/v1"><p class="field-hint">compatible-mode/v1 如支持 OpenAI Images 可以直接填写，系统会自动追加 /images/generations；Coding Plan 不支持文生图。</p></div><div class="field"><label>生成尺寸 <small>宽x高</small></label><input class="input" id="myImageApiSize" value="${esc(s.image.api_size||d.image.api_size||'')}" placeholder="例如 2560x1440，留空使用服务商默认"><p class="field-hint">原样传给服务商；火山 Seedream 等模型有最低总像素要求（如 2560x1440），请按模型支持范围填写。</p></div>${keyField('myImageKey',s.image)}<div class="field"><label>正文配图数</label><input class="input" id="myInlineImages" type="number" min="0" max="5" value="${s.image.inline_images??d.image.inline_images??1}"></div></div>`)}
  </div>`;
  document.querySelectorAll('[data-save]').forEach(b=>b.addEventListener('click',()=>saveService(b.dataset.save)));
  document.querySelectorAll('[data-test-personal]').forEach(b=>b.addEventListener('click',()=>testService(b)));
  document.querySelectorAll('[data-clear-personal]').forEach(b=>b.addEventListener('click',()=>clearService(b.dataset.clearPersonal)));
  const sourceSelect=document.querySelector('#myImageSource');
  const syncImageSource=()=>{const profile=(d.image.source_catalog||[]).find(x=>x.id===sourceSelect?.value);if(!profile)return;const custom=profile.id==='custom',hint=document.querySelector('#myImageSourceHint');['#myImageBase','#myImageModel','#myImageKey','#myImageApiSize'].forEach(id=>{const input=document.querySelector(id);if(input){input.closest('.field').hidden=!custom;input.disabled=!custom;}});if(hint)hint.textContent=`${profile.description} ${profile.key_hint}`;};
  sourceSelect?.addEventListener('change',syncImageSource);syncImageSource();
}

function card(name,service,body){return `<section class="card config-card"><header class="card-header"><div><h3>${labels[name]}</h3><p>${service.configured?'已保存个人配置':'尚未保存个人配置'}</p></div>${sourceBadge(service)}</header><div class="card-body">${body}</div><footer class="config-card-footer">${actions(name)}</footer></section>`;}

async function saveService(service){
  let payload;
  if(service==='llm')payload={llm:{base_url:value('#myLlmBase'),model:value('#myLlmModel'),api_key:value('#myLlmKey')||undefined,temperature:Number(value('#myLlmTemp')),max_tokens:Number(value('#myLlmTokens'))}};
  if(service==='search')payload={search:{provider:'tavily',base_url:value('#mySearchBase'),api_key:value('#mySearchKey')||undefined,search_depth:value('#mySearchDepth')}};
  if(service==='image')payload={image:{source:value('#myImageSource'),model:value('#myImageModel'),base_url:value('#myImageBase'),api_size:value('#myImageApiSize'),api_key:value('#myImageKey')||undefined,inline_images:Number(value('#myInlineImages'))}};
  try{await api('/api/me/api-config',{method:'PUT',body:JSON.stringify(payload)});toast(`${labels[service]}已加密保存`);renderMyApiConfig();}catch(e){toast(e.message,'error');}
}

async function testService(button){const old=button.textContent;button.disabled=true;button.textContent='测试中…';try{const result=await api(`/api/me/api-config/test/${button.dataset.testPersonal}`,{method:'POST'});toast(result.reply?`连接成功：${result.reply}`:'连接测试成功');}catch(e){toast(e.message,'error');}finally{button.disabled=false;button.textContent=old;}}
async function clearService(service){if(!confirm(`确定清除个人${labels[service]}配置吗？`))return;try{await api(`/api/me/api-config/${service}`,{method:'DELETE'});toast('个人配置已清除');renderMyApiConfig();}catch(e){toast(e.message,'error');}}
