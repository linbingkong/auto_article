import { writeFileSync } from 'node:fs';
const OUT = 'F:/harness_enginnering/auto_article/research_out/';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36';
async function get(url) {
  const resp = await fetch(url, { headers: { 'User-Agent': UA }, redirect: 'follow', signal: AbortSignal.timeout(60000) });
  return { status: resp.status, text: await resp.text() };
}
const log = [];
const files = [
  ['wechat-flow__SKILL', 'bingyue/wechat-flow/master/SKILL.md'],
  ['wechat-flow__publisher', 'bingyue/wechat-flow/master/toolkit/publisher.py'],
  ['wechat-flow__wechat_api', 'bingyue/wechat-flow/master/toolkit/wechat_api.py'],
  ['wechat-flow__image_gen', 'bingyue/wechat-flow/master/toolkit/image_gen.py'],
  ['ccblog__wenyan-mcp-readme', 'Mor-Li/ccblog/main/mcp/wenyan-mcp/README.md'],
];
for (const [name, p] of files) {
  const [repo, branch, ...rest] = p.split('/');
  const path = rest.join('/');
  try {
    const r = await get(`https://raw.githubusercontent.com/${repo}/${branch}/${path}`);
    writeFileSync(`${OUT}${name}.md`, r.text);
    log.push(`${name}: HTTP ${r.status} len=${r.text.length}`);
  } catch (e) { log.push(`${name} ERR ${e.message}`); }
}
// tencent articles
const pages = [
  ['tencent__2647721', 'https://cloud.tencent.cn/developer/article/2647721'],
  ['tencent__2694548', 'https://cloud.tencent.com.cn/developer/article/2694548'],
  ['tencent__2662544', 'https://cloud.tencent.cn/developer/article/2662544'],
];
for (const [name, url] of pages) {
  try {
    const r = await get(url);
    writeFileSync(`${OUT}${name}.html`, r.text);
    log.push(`${name}: HTTP ${r.status} len=${r.text.length}`);
  } catch (e) { log.push(`${name} ERR ${e.message}`); }
}
writeFileSync(`${OUT}__log3.txt`, log.join('\n'));
console.log(log.join('\n'));