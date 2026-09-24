const r = await fetch('https://api.github.com/users/lucianaib0318/repos?per_page=50', { headers: { 'User-Agent': 'Mozilla/5.0' } });
const j = await r.json();
console.log(JSON.stringify((j||[]).map(x => ({ name: x.name, stars: x.stargazers_count, pushed: x.pushed_at, lang: x.language })), null, 1));