for (const repo of ['lucianaib0318/china-hot-ranks','lucianaib0318/wechat-topic-selector','lucianaib0318/wechat-publisher','LucianaiB2004/LucianaiB2004']) {
  const r = await fetch(`https://api.github.com/repos/${repo}`, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  const j = await r.json();
  console.log(`${repo}: status=${r.status} stars=${j.stargazers_count} pushed=${j.pushed_at} desc=${j.description} full=${j.full_name}`);
}