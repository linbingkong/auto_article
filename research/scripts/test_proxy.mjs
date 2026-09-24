const resp = await fetch('https://api.github.com/repos/Mor-Li/ccblog', { headers: { 'User-Agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(30000) });
const text = await resp.text();
console.log('STATUS', resp.status, 'LEN', text.length);
console.log(text.substring(0, 400));