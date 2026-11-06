const assert = require('node:assert/strict');
const { rewriteMarkdownHref, headingSlug } = require('../static/repository.js');

const base = '/repo/FlightPlan.git';
const ref = 'refs/heads/master';
assert.equal(rewriteMarkdownHref('SendMail.py', '', base, ref),
  '/repo/FlightPlan.git/open/SendMail.py?ref=refs%2Fheads%2Fmaster');
assert.equal(rewriteMarkdownHref('deploy/', '', base, ref),
  '/repo/FlightPlan.git/open/deploy?ref=refs%2Fheads%2Fmaster');
assert.equal(rewriteMarkdownHref('../SendMail.py#L4', 'docs', base, ref),
  '/repo/FlightPlan.git/open/SendMail.py?ref=refs%2Fheads%2Fmaster#L4');
assert.equal(rewriteMarkdownHref('中文 文件.md', 'docs', base, ref),
  '/repo/FlightPlan.git/open/docs/%E4%B8%AD%E6%96%87%20%E6%96%87%E4%BB%B6.md?ref=refs%2Fheads%2Fmaster');
assert.equal(rewriteMarkdownHref('#使用说明', '', base, ref), '#使用说明');
assert.equal(rewriteMarkdownHref('https://example.com', '', base, ref), 'https://example.com');
assert.equal(rewriteMarkdownHref('mailto:a@example.com', '', base, ref), 'mailto:a@example.com');
assert.equal(rewriteMarkdownHref('../../outside.py', 'docs', base, ref), null);
assert.equal(rewriteMarkdownHref('javascript:alert(1)', '', base, ref), null);
assert.equal(headingSlug('使用说明'), '使用说明');
assert.equal(headingSlug('Install & Run!'), 'install-run');
