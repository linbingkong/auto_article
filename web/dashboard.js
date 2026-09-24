import {app,api,cardList,loading,metric,renderError,setMeta,statusRow,taskList} from './core.js';

export async function renderDashboard(){
  setMeta('dashboard');loading('正在汇总 Agent 运行状态…');
  try{
    const d=await api('/api/dashboard'),s=d.stats;
    app.innerHTML=`<div class="page-intro"><div><h2>观思辩明 · 内容工作台</h2><p>观其现象，思其本质，辩其逻辑，明其真意。</p></div><div class="actions"><a class="btn" href="#config">检查配置</a><a class="btn primary" href="#hotspots">＋ 开始创作</a></div></div>
    <section class="grid cols-4">${metric('文章总数',s.articles,'▤','本地文章仓库')}${metric('草稿投递',s.drafts,'↗','等待公众号后台审核')}${metric('执行中任务',s.running_tasks,'◴','最大并发 2 个任务')}${metric('热点数据源',s.configured_sources,'⌁','已启用的聚合来源')}</section>
    <section class="grid cols-3 section-space"><div class="card" style="grid-column:span 2"><header class="card-header"><div><h3>最近文章</h3><p>最近生成和推送的内容</p></div><a href="#articles" class="btn small">全部文章</a></header><div class="card-body">${cardList(d.articles)}</div></div><div class="card"><header class="card-header"><div><h3>系统就绪状态</h3><p>正式推送前请完成全部配置</p></div></header><div class="card-body"><div class="status-list">${statusRow('大模型服务',d.configuration.llm,d.configuration.llm_key?'已配置模型与 Key':'模型可用，Key 未配置')}${statusRow('微信公众号',d.configuration.wechat,d.configuration.wechat?'凭证已配置':'等待配置 AppID/Secret')}${statusRow('通知渠道',d.configuration.notify,d.configuration.notify?'Webhook 已启用':'当前未启用')}${statusRow('每日调度',d.schedule.running,d.schedule.configured?(d.schedule.running?`每日 ${d.schedule.daily_time}`:'启动异常'):'当前关闭')}</div>${!d.configuration.wechat?'<div class="alert section-space">正式推送需要认证公众号凭证，并将服务器出网 IP 加入公众号白名单。</div>':''}</div></div></section>
    <section class="grid cols-2 section-space"><div class="card"><header class="card-header"><div><h3>任务动态</h3><p>后台生成与推送进度</p></div><a href="#tasks" class="btn small">任务日志</a></header><div class="card-body">${taskList(d.tasks,4)}</div></div><div class="card"><header class="card-header"><div><h3>发布记录</h3><p>微信公众号草稿与复盘</p></div><a href="#history" class="btn small">查看复盘</a></header><div class="card-body">${cardList(d.history,'history')}</div></div></section>`;
  }catch(e){renderError(e);}
}
