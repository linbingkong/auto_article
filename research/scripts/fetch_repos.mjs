// Fetch GitHub READMEs and repo metadata through local proxy, save to files.
import { writeFileSync, mkdirSync } from 'node:fs';
import { ProxyAgent } from 'undici';

const PROXY = 'http://127.0.0.1:7897';
const OUT = 'F:/harness_enginnering/auto_article/research_out/';
mkdirSync(OUT, { recursive: true });
const dispatcher = new ProxyAgent(PROXY);
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36';

async function get(url) {
  const resp = await fetch(url, {
    dispatcher,
    headers: { 'User-Agent': UA, 'Accept': '*/*' },
    redirect: 'follow',
    signal: AbortSignal.timeout(60000),
  });
  const text = await resp.text();
  return { status: resp.status, text };
}

const repos = [
  ['ccblog', 'Mor-Li/ccblog', 'main'],
  ['wechat-flow', 'bingyue/wechat-flow', 'master'],
  ['wechat-auto-publishing', '16Miku/wechat-auto-publishing', 'main'],
  ['fengyun-publish', 'duliangkuan/fengyun-publish', 'main'],
  ['wxgzh-cli', 'fanbuz/wxgzh-cli', 'main'],
  ['wechat-publisher', 'jiji262/wechat-publisher', 'main'],
  ['baoyu-skills', 'JimLiu/baoyu-skills', 'main'],
];

const log = [];
for (const [name, repo, branch] of repos) {
  const [owner, proj] = repo.split('/');
  try {
    const meta = await get(`https://api.github.com/repos/${owner}/${proj}`);
    writeFileSync(`${OUT}${name}__meta.json`, meta.text);
    let info = '';
    try { const j = JSON.parse(meta.text); info = ` stars=${j.stargazers_count} pushed=${j.pushed_at} updated=${j.updated_at} lang=${j.language} desc=${j.description}`; } catch {}
    log.push(`${name} meta: HTTP ${meta.status}${info}`);
  } catch (e) { log.push(`${name} meta ERR: ${e.message}`); }
  for (const b of [branch, 'main', 'master']) {
    try {
      const raw = await get(`https://raw.githubusercontent.com/${owner}/${proj}/${b}/README.md`);
      if (raw.status === 200) {
        writeFileSync(`${OUT}${name}__README.md`, raw.text);
        log.push(`${name} README(${b}): HTTP ${raw.status} len=${raw.text.length}`);
        break;
      } else { log.push(`${name} README(${b}): HTTP ${raw.status}`); }
    } catch (e) { log.push(`${name} README(${b}) ERR: ${e.message}`); }
  }
}

const extras = [
  ['fengyun-publish__PHASE1_FACTS', 'https://raw.githubusercontent.com/duliangkuan/fengyun-publish/main/PHASE1_FACTS.md'],
  ['fengyun-publish__PROJECT_GAPS_AUDIT', 'https://raw.githubusercontent.com/duliangkuan/fengyun-publish/main/PROJECT_GAPS_AUDIT.md'],
  ['wechat-publisher__SKILL', 'https://raw.githubusercontent.com/jiji262/wechat-publisher/main/SKILL.md'],
  ['wechat-auto-publishing__SKILL', 'https://raw.githubusercontent.com/16Miku/wechat-auto-publishing/main/SKILL.md'],
  ['wechat-flow__wechat', 'https://raw.githubusercontent.com/bingyue/wechat-flow/master/wechat/__init__.py'],
  ['openclaw-readme', 'https://raw.githubusercontent.com/openclaw/openclaw/main/README.md'],
];
for (const [name, url] of extras) {
  try {
    const r = await get(url);
    writeFileSync(`${OUT}${name}.md`, r.text);
    log.push(`${name}: HTTP ${r.status} len=${r.text.length}`);
  } catch (e) { log.push(`${name} ERR: ${e.message}`); }
}

writeFileSync(`${OUT}__log.txt`, log.join('\n'));
console.log(log.join('\n'));