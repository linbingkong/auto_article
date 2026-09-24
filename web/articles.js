import {app,api,can,closeModal,empty,esc,fmtDate,loading,renderError,setMeta,showModal,state,statusBadge,toast,watchTask} from './core.js';

export async function renderArticles(){
  setMeta('articles');loading('正在读取文章仓库…');
  try{
    const items=await api('/api/articles?limit=100');
    app.innerHTML=`<div class="page-intro"><div><h2>文章内容库</h2><p>管理 Agent 生成的文章、版本与公众号草稿状态。</p></div><a class="btn primary" href="#hotspots">＋ 创建文章</a></div>${items.length?`<section class="article-list-grid">${items.map(articleCard).join('')}</section>`:empty('文章库还是空的','从热点选题页面选择话题并生成第一篇文章')}`;
    document.querySelectorAll('[data-delete-article]').forEach(btn=>btn.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();showDeleteModal(btn.dataset.deleteArticle,false);}));
  }catch(e){renderError(e);}
}

function articleCard(a){return `<article class="card article-card"><a class="article-card-link" href="#article/${a.id}"><div class="article-cover"><img src="${esc(a.cover_url||'')}" alt="${esc(a.title)}" loading="lazy"></div><div class="article-card-content"><div class="actions" style="justify-content:space-between;margin-bottom:9px">${statusBadge(a.status)}<span class="badge">v${a.version||1}</span></div><h3>${esc(a.title)}</h3><p>${esc(a.digest||'暂无摘要')}</p><div class="article-card-foot"><span>${a.word_count||0} 字</span><span>${fmtDate(a.updated_at)}</span></div></div></a>${a.access?.delete?`<button class="article-delete-button" data-delete-article="${a.id}" title="删除文章" aria-label="删除 ${esc(a.title)}">×</button>`:''}</article>`;}

export async function renderArticle(articleId){
  setMeta('article');loading('正在准备公众号预览…');
  try{
    const a=await api(`/api/articles/${articleId}`);state.article=a;
    const fact=a.fact_check||{};
    const factStatusText={completed:'',failed:'（核验未完成，推送已被服务端阻止）',stale:'（已失效，编辑后需重新核验）'}[fact.status]||'（未核验）';
    const factPanel=fact.status?`<div class="fact-panel ${esc(fact.risk_level||'high')}"><strong>事实核验：${fact.risk_level==='low'?'低风险':fact.risk_level==='medium'?'需复核':'高风险'}${esc(factStatusText)}</strong><span>${fact.status==='completed'?'已根据采集信源逐项核对关键断言，仍需人工终审。':'该文章当前不允许推送公众号草稿。'}${fact.information_score!=null?` 信息量评分 ${fact.information_score}/100。`:''}</span>${(fact.issues||[]).length?`<ul>${fact.issues.slice(0,5).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}</div>`:'';
    const ev=a.evidence||{};
    const evPanel=ev.grade?`<div class="fact-panel ${esc(ev.grade==='strong'?'low':ev.grade==='medium'?'medium':'high')}"><strong>研究评估：${ev.grade==='strong'?'充分':ev.grade==='medium'?'一般':'薄弱'}</strong><span>实质信源 ${ev.substantive_source_count??0} 条 · 官方原文 ${ev.official_source_count??0} 条${ev.institution_topic?' · 制度议题':''}${(ev.warnings||[]).length?' · '+esc(ev.warnings[0]):''}</span></div>`:'';
    const q=a.style_quality;
    const factReport=a.fact_check||{};
    const qPanel=q?`<div class="fact-panel ${q.passed?'low':'medium'}"><strong>原创与信息增量体检：${q.passed?'通过':'需优化'}</strong><span>薄弱段落 ${q.thin_paragraph_count??0} · 机制解释段落 ${(Number(q.mechanism_paragraph_ratio??0)*100).toFixed(0)}% · 独立数字 ${q.distinct_number_count??0} 个 · AIGC 套话 ${q.cliche_count??0} 处${factReport.information_score!=null?` · 信息量评分 ${factReport.information_score}/100`:''}${factReport.unsupported_claims!=null?` · 无法溯源断言 ${factReport.unsupported_claims} 项`:''}</span>${(a.style_check?.hints||[]).length?`<ul>${a.style_check.hints.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}${(factReport.low_information_paragraphs||[]).length?`<ul>${factReport.low_information_paragraphs.slice(0,5).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}</div>`:'';
    const editorButtons = a.access?.edit ? `<button class="btn" id="saveArticle">保存新版本</button><button class="btn" id="copyTitle" title="复制到微信公众号标题输入框">复制标题</button><button class="btn" id="copyCover" disabled title="复制封面 PNG；公众号封面与正文是两个独立区域">准备封面…</button><button class="btn copy-rich" id="copyRichBody" disabled title="复制富文本正文，可直接粘贴到微信公众号编辑器">准备富文本…</button>` : `<button class="btn" id="copyTitle" title="复制到微信公众号标题输入框">复制标题</button>`;
    const pushButton = a.access?.push ? `<button class="btn primary" id="pushArticle">↗ 推送公众号草稿</button>` : '';
    const deleteButton = a.access?.delete ? `<button class="btn danger-outline" id="deleteArticle">删除文章</button>` : '';
    const shareButton = can('user.manage') ? `<button class="btn" id="shareArticle">共享设置</button>` : '';
    app.innerHTML=`<div class="page-intro"><div><h2>预览与编辑</h2><p>版本 v${a.version} · ${a.word_count} 字 · 更新于 ${fmtDate(a.updated_at)}</p></div><div class="actions"><a class="btn" href="#articles">返回内容库</a>${shareButton}${deleteButton}${editorButtons}${pushButton}</div></div>${factPanel}${evPanel}${qPanel}
    <div class="editor-shell"><section class="card editor-panel"><div class="editor-fields"><div class="field"><label>文章标题 <small>最多 64 字</small></label><input class="input" id="articleTitle" maxlength="64" value="${esc(a.title)}" ${a.access?.edit?'':'readonly'}></div>${(a.title_candidates||[]).length?`<div class="title-candidates">${a.title_candidates.map(x=>`<button class="title-candidate" data-title="${esc(x)}" ${a.access?.edit?'':'disabled'}>${esc(x)}</button>`).join('')}</div>`:''}<div class="field section-space"><label>公众号摘要</label><textarea class="textarea" id="articleDigest" style="min-height:70px" ${a.access?.edit?'':'readonly'}>${esc(a.digest||'')}</textarea></div></div><textarea class="markdown-editor" id="articleMarkdown" spellcheck="false" ${a.access?.edit?'':'readonly'}>${esc(a.content_md)}</textarea></section>
    <aside class="card preview-panel"><div class="preview-toolbar"><div><strong>公众号预览</strong> ${statusBadge(a.status)}</div>${a.access?.edit?'<button class="btn small" id="refreshPreview">刷新预览</button>':''}</div><div class="preview-canvas"><article class="phone-preview"><div class="phone-head"><h1 id="previewTitle">${esc(a.title)}</h1><p>${esc(a.account_name||'观思辩明')} · ${new Date().toLocaleDateString('zh-CN')}</p></div><img class="phone-cover" src="${a.cover_url}" alt="封面"><div class="wechat-body" id="wechatPreview">${a.preview_html}</div></article></div></aside></div>`;
    document.querySelector('#articleTitle').addEventListener('input',e=>document.querySelector('#previewTitle').textContent=e.target.value);
    document.querySelectorAll('[data-title]').forEach(b=>b.addEventListener('click',()=>{document.querySelector('#articleTitle').value=b.dataset.title;document.querySelector('#previewTitle').textContent=b.dataset.title;}));
    document.querySelector('#saveArticle')?.addEventListener('click',()=>saveArticle(articleId));
    document.querySelector('#refreshPreview')?.addEventListener('click',()=>saveArticle(articleId));
    document.querySelector('#pushArticle')?.addEventListener('click',()=>showPushModal(articleId));
    document.querySelector('#deleteArticle')?.addEventListener('click',()=>showDeleteModal(articleId,true));
    document.querySelector('#shareArticle')?.addEventListener('click',()=>showShareModal(articleId));
    document.querySelector('#copyTitle')?.addEventListener('click',()=>copyPlainText(a.title,'标题已复制，可粘贴到微信公众号标题栏'));
    prepareImageBlob(a.cover_url).then(blob=>{
      const btn=document.querySelector('#copyCover');if(!btn)return;
      btn.disabled=false;btn.textContent='复制封面图';btn.addEventListener('click',()=>copyImageBlob(blob,btn,a.title));
    }).catch(()=>{const btn=document.querySelector('#copyCover');if(btn){btn.disabled=false;btn.textContent='下载封面图';btn.addEventListener('click',()=>downloadImage(a.cover_url,`${a.title}-封面.png`));}});
    prepareRichCopy(document.querySelector('#wechatPreview')).then(payload=>{
      const btn=document.querySelector('#copyRichBody');
      if(!btn)return;
      btn.disabled=false;btn.textContent='⧉ 复制公众号正文';
      btn.addEventListener('click',()=>copyRichBody(payload,btn));
    }).catch(()=>{const btn=document.querySelector('#copyRichBody');if(btn){btn.disabled=false;btn.textContent='⧉ 复制公众号正文';btn.addEventListener('click',()=>copyRichBody({html:document.querySelector('#wechatPreview').innerHTML,text:document.querySelector('#wechatPreview').innerText},btn));}});
  }catch(e){renderError(e);}
}

async function prepareRichCopy(source){
  const clone=source.cloneNode(true);
  clone.removeAttribute('id');
  clone.querySelectorAll('script,button,input,textarea').forEach(x=>x.remove());
  const allowedAlign=new Set(['left','center','right']);
  clone.querySelectorAll('[style]').forEach(el=>{
    const align=(el.style.textAlign||'').trim().toLowerCase();
    if(align&&!allowedAlign.has(align))el.style.removeProperty('text-align');
  });
  clone.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,blockquote').forEach(el=>{
    el.removeAttribute('align');
    el.style.setProperty('text-align','left');
  });
  const images=[...clone.querySelectorAll('img')];
  await Promise.all(images.map(async img=>{
    try{
      const url=new URL(img.getAttribute('src')||'',location.href);
      img.setAttribute('src',url.href);img.removeAttribute('loading');
      if(url.origin!==location.origin)return;
      const headers=state.token?{'X-Admin-Token':state.token}:{};
      const response=await fetch(url.href,{headers,credentials:'same-origin'});
      if(!response.ok)return;
      const blob=await response.blob();
      if(blob.size>6*1024*1024)return;
      img.setAttribute('src',await blobToDataURL(blob));
    }catch(_){/* 保留绝对 URL 作为回退 */}
  }));
  clone.querySelectorAll('a[href]').forEach(a=>{try{a.href=new URL(a.getAttribute('href'),location.href).href;}catch(_){}});
  const wrapper=document.createElement('section');
  wrapper.setAttribute('data-source','观思辩明公众号智能体');
  wrapper.style.cssText='max-width:100%;margin:0;padding:0;color:#3f3f3f;background:#fff;';
  wrapper.innerHTML=clone.innerHTML;
  return {html:wrapper.outerHTML,text:source.innerText.trim()};
}

async function prepareImageBlob(url){
  const headers=state.token?{'X-Admin-Token':state.token}:{};
  const response=await fetch(new URL(url,location.href),{headers,credentials:'same-origin'});
  if(!response.ok)throw new Error(`封面读取失败（${response.status}）`);
  const blob=await response.blob();
  if(!blob.type.startsWith('image/'))throw new Error('封面资源不是图片');
  return blob;
}
async function copyImageBlob(blob,btn,title){
  const old=btn.textContent;btn.disabled=true;btn.textContent='复制中…';
  try{
    if(!window.isSecureContext||!navigator.clipboard?.write||!window.ClipboardItem)throw new Error('浏览器不支持图片剪贴板');
    const png=blob.type==='image/png'?blob:await imageBlobToPng(blob);
    await navigator.clipboard.write([new ClipboardItem({'image/png':png})]);
    toast('封面图已复制；可粘贴到支持图片粘贴的位置，公众号封面也可下载后上传');
  }catch(e){downloadBlob(blob,`${title}-封面.png`);toast(`${e.message}，已自动下载封面图`,'error');}
  finally{btn.disabled=false;btn.textContent=old;}
}
function imageBlobToPng(blob){return new Promise((resolve,reject)=>{const img=new Image(),url=URL.createObjectURL(blob);img.onload=()=>{const canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;canvas.getContext('2d').drawImage(img,0,0);canvas.toBlob(result=>{URL.revokeObjectURL(url);result?resolve(result):reject(new Error('图片转换失败'));},'image/png');};img.onerror=()=>{URL.revokeObjectURL(url);reject(new Error('图片转换失败'));};img.src=url;});}
function downloadBlob(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name.replace(/[\\/:*?"<>|]/g,'-');document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function downloadImage(url,name){try{downloadBlob(await prepareImageBlob(url),name);}catch(e){toast(e.message,'error');}}
function blobToDataURL(blob){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});}

async function copyRichBody(payload,btn){
  const old=btn.textContent;btn.disabled=true;btn.textContent='复制中…';
  try{
    let copied=false;
    if(window.isSecureContext&&navigator.clipboard?.write&&window.ClipboardItem){
      try{
        await navigator.clipboard.write([new ClipboardItem({
          'text/html':new Blob([payload.html],{type:'text/html'}),
          'text/plain':new Blob([payload.text],{type:'text/plain'}),
        })]);copied=true;
      }catch(_){copied=false;}
    }
    if(!copied)copied=legacyCopyHtml(payload.html);
    if(!copied)throw new Error('浏览器拒绝访问剪贴板');
    toast('富文本正文已复制，请在微信公众号正文编辑区直接粘贴');
  }catch(e){toast(`${e.message}；请使用 HTTPS/localhost 并允许剪贴板权限`,'error');}
  finally{btn.disabled=false;btn.textContent=old;}
}

async function copyPlainText(text,message){
  try{
    if(window.isSecureContext&&navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);}
    else if(!legacyCopyText(text)){throw new Error('浏览器拒绝访问剪贴板');}
    toast(message);
  }catch(e){if(legacyCopyText(text))toast(message);else toast(`${e.message}；请允许剪贴板权限`,'error');}
}

function legacyCopyHtml(html){
  const holder=document.createElement('div');holder.contentEditable='true';holder.style.cssText='position:fixed;left:-10000px;top:0;width:600px;background:#fff;';holder.innerHTML=html;document.body.appendChild(holder);
  const range=document.createRange();range.selectNodeContents(holder);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
  let ok=false;try{ok=document.execCommand('copy');}catch(_){}selection.removeAllRanges();holder.remove();return ok;
}
function legacyCopyText(text){const input=document.createElement('textarea');input.value=text;input.style.cssText='position:fixed;left:-10000px;top:0;';document.body.appendChild(input);input.select();let ok=false;try{ok=document.execCommand('copy');}catch(_){}input.remove();return ok;}

async function saveArticle(articleId){const btn=document.querySelector('#saveArticle');if(btn)btn.disabled=true;try{const a=await api(`/api/articles/${articleId}`,{method:'PUT',body:JSON.stringify({title:document.querySelector('#articleTitle').value.trim(),digest:document.querySelector('#articleDigest').value.trim(),content_md:document.querySelector('#articleMarkdown').value})});state.article=a;document.querySelector('#wechatPreview').innerHTML=a.preview_html;document.querySelector('#previewTitle').textContent=a.title;document.querySelector('#articleMarkdown').value=a.content_md;toast(`已保存为版本 v${a.version}`);}catch(e){toast(e.message,'error');}finally{if(btn)btn.disabled=false;}}

function showDeleteModal(articleId,fromDetail){const a=state.article?.id===articleId?state.article:null;const remote=a?.draft_media_id?'<div class="alert">该文章已推送到微信公众号。这里只删除本地副本，微信后台中的远端草稿不会被删除。</div>':'';showModal({title:'确认删除文章',subtitle:'该操作会永久删除正文、版本和本地图片，无法撤销',body:`${remote}<p>确定删除${a?.title?`《${esc(a.title)}》`:'这篇文章'}吗？</p>`,footer:'<button class="btn" data-close>取消</button><button class="btn danger" id="confirmDeleteArticle">永久删除</button>'});document.querySelector('#confirmDeleteArticle').addEventListener('click',async()=>{const btn=document.querySelector('#confirmDeleteArticle');btn.disabled=true;try{const r=await api(`/api/articles/${articleId}?confirm=true`,{method:'DELETE'});closeModal();state.article=null;toast(r?.remote_draft_preserved?'本地文章已删除，微信远端草稿保留':'文章已删除');if(fromDetail){location.hash='articles';}else{renderArticles();}}catch(e){toast(e.message,'error');btn.disabled=false;}});}

async function showShareModal(articleId){try{const [shares,userData]=await Promise.all([api(`/api/articles/${articleId}/shares`),api('/api/users')]);const users=userData.users.filter(u=>u.role!=='admin'&&u.status==='active');showModal({title:'文章共享设置',subtitle:'分别授予查看、编辑和推送权限',body:`<div class="field"><label>共享给用户</label><select class="select" id="shareUser"><option value="">请选择</option>${users.map(u=>`<option value="${u.id}" data-role="${u.role}">${esc(u.username)}（${u.role}）</option>`).join('')}</select></div><div class="share-permissions section-space"><label class="checkline"><input type="checkbox" id="shareView" checked><span>查看</span></label><label class="checkline"><input type="checkbox" id="shareEdit"><span>编辑</span></label><label class="checkline"><input type="checkbox" id="sharePush"><span>推送草稿</span></label></div><button class="btn primary section-space" id="saveShare">保存授权</button><div class="share-list section-space">${shares.length?shares.map(s=>`<div><span>${esc(s.username)} · ${[s.can_view?'查看':'',s.can_edit?'编辑':'',s.can_push?'推送':''].filter(Boolean).join('/')}</span><button class="btn small danger-outline" data-revoke-share="${s.user_id}">撤销</button></div>`).join(''):'<p class="hint">尚未共享给其他用户</p>'}</div>`,footer:'<button class="btn" data-close>关闭</button>'});document.querySelector('#saveShare').addEventListener('click',async()=>{const userId=document.querySelector('#shareUser').value;if(!userId)return toast('请选择用户','error');const permissions=['view','edit','push'].filter(p=>document.querySelector(`#share${p[0].toUpperCase()+p.slice(1)}`).checked);try{await api(`/api/articles/${articleId}/shares`,{method:'PUT',body:JSON.stringify({user_id:userId,permissions})});closeModal();toast('共享权限已保存');showShareModal(articleId);}catch(e){toast(e.message,'error')}});document.querySelectorAll('[data-revoke-share]').forEach(b=>b.addEventListener('click',async()=>{try{await api(`/api/articles/${articleId}/shares/${b.dataset.revokeShare}`,{method:'DELETE'});closeModal();toast('共享已撤销');showShareModal(articleId);}catch(e){toast(e.message,'error')}}));}catch(e){toast(e.message,'error')}}

function showPushModal(articleId){showModal({title:'推送到公众号草稿箱',subtitle:'只创建草稿，不会自动正式发表',body:'<div class="alert">推送后请登录公众号后台再次检查标题、事实、图片版权和排版，再由人工点击发表。</div><label class="checkline"><input type="checkbox" id="confirmReview"><span>我已人工审核文章事实、表述与图片版权</span></label><label class="checkline"><input type="checkbox" id="confirmAI"><span>我确认将按平台要求标注“AI 辅助创作”</span></label>',footer:'<button class="btn" data-close>取消</button><button class="btn primary" id="confirmPush">确认推送草稿</button>'});document.querySelector('#confirmPush').addEventListener('click',async()=>{try{const r=await api(`/api/articles/${articleId}/push`,{method:'POST',body:JSON.stringify({confirm_reviewed:document.querySelector('#confirmReview').checked,confirm_ai_disclosure:document.querySelector('#confirmAI').checked})});closeModal();watchTask(r.task_id,'正在推送公众号草稿',()=>renderArticle(articleId));}catch(e){toast(e.message,'error');}});}
