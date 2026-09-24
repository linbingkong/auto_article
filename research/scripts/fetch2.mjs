import { writeFileSync, mkdirSync } from 'node:fs';
const OUT = 'F:/harness_enginnering/auto_article/research_out/';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36';
async function get(url) {
  const resp = await fetch(url, { headers: { 'User-Agent': UA, 'Accept': '*/*' }, redirect: 'follow', signal: AbortSignal.timeout(60000) });
  const text = await resp.text();
  return { status: resp.status, text };
}
const log = [];
// wechat-flow canonical check
for (const repo of ['oaker-io/wechat-flow','bingyue/wechat-flow']) {
  try {
    const r = await get(`https://api.github.com/repos/${repo}`);
    const j = JSON.parse(r.text);
    log.push(`${repo}: stars=${j.stargazers_count} fork=${j.fork} parent=${j.parent ? j.parent.full_name : '-'} pushed=${j.pushed_at} created=${j.created_at} desc=${j.description}`);
  } catch (e) { log.push(`${repo} ERR ${e.message}`); }
}
// search LucianaiB
try {
  const r = await get('https://api.github.com/search/repositories?q=LucianaiB&per_page=10');
  const j = JSON.parse(r.text);
  log.push('LucianaiB search total=' + j.total_count);
  for (const it of (j.items || [])) log.push(`  ${it.full_name} stars=${it.stargazers_count} desc=${it.description}`);
} catch (e) { log.push('LucianaiB search ERR ' + e.message); }
// search other wechat publish repos
for (const q of ['wechat-auto-publish skill', 'wechat-publisher skill', 'wxgzh-cli']) {
  try {
    const r = await get(`https://api.github.com/search/repositories?q=${encodeURIComponent(q)}&per_page=5`);
    const j = JSON.parse(r.text);
    log.push(`search "${q}" total=${j.total_count}`);
    for (const it of (j.items || [])) log.push(`  ${it.full_name} stars=${it.stargazers_count} pushed=${it.pushed_at} lang=${it.language}`);
  } catch (e) { log.push(`search "${q}" ERR ${e.message}`); }
}
// pages
const pages = [
  ['juejin__7610616824568954920', 'https://juejin.cn/post/7610616824568954920'],
  ['tencent__lucianai_2640353', 'https://cloud.tencent.com.cn/developer/article/2640353'],
  ['zhihu__skills-vs-claude-code', 'https://zhuanlan.zhihu.com/p/2018606221498823087'],
];
for (const [name, url] of pages) {
  try {
    const r = await get(url);
    writeFileSync(`${OUT}${name}.html`, r.text);
    log.push(`${name}: HTTP ${r.status} len=${r.text.length}`);
  } catch (e) { log.push(`${name} ERR ${e.message}`); }
}
writeFileSync(`${OUT}__log2.txt`, log.join('\n'));
console.log(log.join('\n'));