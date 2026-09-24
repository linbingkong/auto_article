import { writeFileSync } from 'node:fs';
const OUT = 'F:/harness_enginnering/auto_article/research_out/';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125';
async function get(url) {
  const resp = await fetch(url, { headers: { 'User-Agent': UA }, redirect: 'follow', signal: AbortSignal.timeout(60000) });
  return { status: resp.status, text: await resp.text() };
}
const log = [];
const files = [
  ['ccblog__CLAUDE', 'Mor-Li/ccblog/main/CLAUDE.md'],
  ['ccblog__PROMPT', 'Mor-Li/ccblog/main/PROMPT.md'],
  ['ccblog__agent-pdf-parser', 'Mor-Li/ccblog/main/.claude/agents/pdf-parser-mineru.md'],
  ['ccblog__agent-writer', 'Mor-Li/ccblog/main/.claude/agents/wechat-blog-writer.md'],
];
for (const [name, p] of files) {
  const [repo, branch, ...rest] = p.split('/');
  try {
    const r = await get(`https://raw.githubusercontent.com/${repo}/${branch}/${rest.join('/')}`);
    writeFileSync(`${OUT}${name}.md`, r.text);
    log.push(`${name}: HTTP ${r.status} len=${r.text.length}`);
  } catch (e) { log.push(`${name} ERR ${e.message}`); }
}
writeFileSync(`${OUT}__log4.txt`, log.join('\n'));
console.log(log.join('\n'));