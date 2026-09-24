const r = await fetch('https://api.github.com/users/fanbuz/repos?per_page=100', { headers: { 'User-Agent': 'Mozilla/5.0' } });
const j = await r.json();
console.log(JSON.stringify(j.map(x => ({ name: x.name, full: x.full_name, fork: x.fork, pushed: x.pushed_at, stars: x.stargazers_count })), null, 1));