import { writeFileSync } from 'node:fs';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125';
const r = await fetch('https://zh.wikipedia.org/api/rest_v1/page/summary/OpenClaw', { headers: { 'User-Agent': UA }, signal: AbortSignal.timeout(40000) });
const j = await r.json();
writeFileSync('F:/harness_enginnering/auto_article/research_out/wikipedia__openclaw.json', JSON.stringify({ extract: j.extract, title: j.title, description: j.description }, null, 2), 'utf8');
console.log('TITLE:', j.title);
console.log('DESC:', j.description);
console.log('EXTRACT:', j.extract);