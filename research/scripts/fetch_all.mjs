import { writeFileSync, mkdirSync } from 'node:fs';
const OUT = 'F:/harness_enginnering/auto_article/research_out/';
mkdirSync(OUT, { recursive: true });
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36';
async function get(url) {
  const resp = await fetch(url, { headers: { 'User-Agent': UA, 'Accept': '*/*' }, redirect: 'follow', signal: AbortSignal.timeout(60000) });
  const text = await resp.text();
  return { status: resp.status, text };
}
const log = [];
async function meta(repo, name) {
  try {
    const r = await get(`https://api.github.com/repos/${repo}`);
    writeFileSync(`${OUT}${name}__meta.json`, r.text);
    let j = {}; try { j = JSON.parse(r.text); } catch {}
    log.push(`${name} meta: HTTP ${r.status} stars=${j.stargazers_count} pushed=${j.pushed_at} updated=${j.updated_at} lang=${j.language} archived=${j.archived} fork=${j.fork}`);
  } catch (e) { log.push(`${name} meta ERR: ${e.message}`); }
}
async function readme(repo, name, branches) {
  for (const b of branches) {
    try {
      const r = await get(`https://raw.githubusercontent.com/${repo}/${b}/README.md`);
      if (r.status === 200) { writeFileSync(`${OUT}${name}__README.md`, r.text); log.push(`${name} README(${b}): HTTP ${r.status} len=${r.text.length}`); return b; }
      log.push(`${name} README(${b}): HTTP ${r.status}`);
    } catch (e) { log.push(`${name} README(${b}) ERR: ${e.message}`); }
  }
  return null;
}
async function tree(repo, name, branch) {
  try {
    const r = await get(`https://api.github.com/repos/${repo}/git/trees/${branch}?recursive=1`);
    if (r.status === 200) {
      writeFileSync(`${OUT}${name}__tree.json`, r.text);
      const j = JSON.parse(r.text);
      const paths = (j.tree || []).filter(t => t.type === 'blob').map(t => t.path);
      log.push(`${name} tree: ${paths.length} files`);
      writeFileSync(`${OUT}${name}__files.txt`, paths.join('\n'));
    } else { log.push(`${name} tree: HTTP ${r.status}`); }
  } catch (e) { log.push(`${name} tree ERR: ${e.message}`); }
}
async function raw(repo, branch, path, name) {
  try {
    const r = await get(`https://raw.githubusercontent.com/${repo}/${branch}/${path}`);
    writeFileSync(`${OUT}${name}.md`, r.text);
    log.push(`${name}: HTTP ${r.status} len=${r.text.length}`);
  } catch (e) { log.push(`${name} ERR: ${e.message}`); }
}
async function page(url, name) {
  try {
    const r = await get(url);
    writeFileSync(`${OUT}${name}.html`, r.text);
    log.push(`${name}: HTTP ${r.status} len=${r.text.length}`);
  } catch (e) { log.push(`${name} ERR: ${e.message}`); }
}

const repos = [
  ['Mor-Li/ccblog', 'ccblog', ['main']],
  ['bingyue/wechat-flow', 'wechat-flow', ['master']],
  ['16Miku/wechat-auto-publishing', 'wechat-auto-publishing', ['main']],
  ['duliangkuan/fengyun-publish', 'fengyun-publish', ['main']],
  ['fanbuz/wxgzh-cli', 'wxgzh-cli', ['main']],
  ['jiji262/wechat-publisher', 'wechat-publisher', ['main']],
  ['JimLiu/baoyu-skills', 'baoyu-skills', ['main']],
  ['openclaw/openclaw', 'openclaw', ['main']],
  ['lyhue1991/wxgzh', 'wxgzh-skill', ['main']],
];
for (const [repo, name, branches] of repos) {
  await meta(repo, name);
  const b = await readme(repo, name, branches);
  if (b) await tree(repo, name, b);
}

// extra files
await raw('duliangkuan/fengyun-publish', 'main', 'PHASE1_FACTS.md', 'fengyun-publish__PHASE1_FACTS');
await raw('duliangkuan/fengyun-publish', 'main', 'PROJECT_GAPS_AUDIT.md', 'fengyun-publish__PROJECT_GAPS_AUDIT');
await raw('jiji262/wechat-publisher', 'main', 'SKILL.md', 'wechat-publisher__SKILL');
await raw('16Miku/wechat-auto-publishing', 'main', 'SKILL.md', 'wechat-auto-publishing__SKILL');
await raw('16Miku/wechat-auto-publishing', 'main', 'README.zh-CN.md', 'wechat-auto-publishing__README_zh');

// key web pages
await page('https://docs.clawhub.ai/gdp6539/skills/wechat-auto-publisher', 'clawhub__wechat-auto-publisher');
await page('https://clawhub.ai/16miku/skills/wechat-auto-publishing', 'clawhub__16miku');
await page('https://openclawai.io/skills/skill/wechat-auto-publishing', 'openclawai__16miku');
await page('https://www.cnblogs.com/xuxueli/p/19721838', 'cnblogs__xuxueli');
await page('https://bbs.huaweicloud.com/forum/thread-0212720971562020323-1-1.html', 'huaweicloud__openclaw_case');
await page('https://www.cocoloop.cn/t/topic/311/6', 'cocoloop__t311');

writeFileSync(`${OUT}__log.txt`, log.join('\n'));
console.log(log.join('\n'));