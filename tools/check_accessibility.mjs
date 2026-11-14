// Run automated WCAG 2.1 AA checks on converted HTML pages.
//
//   cd tools && npm install && npx playwright install chromium
//   node check_accessibility.mjs ../accessible-html/*.html
//
// Checks: axe-core rules tagged wcag2a/wcag2aa/wcag21a/wcag21aa, reflow at 320 CSS px
// (WCAG 1.4.10, no horizontal page scrolling), and the text-spacing override (WCAG 1.4.12).
// Automated tools find roughly a third of accessibility problems; the report from the
// converter lists the things a person still has to check.
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('Playwright is not installed. Run: npm install');
  process.exit(1);
}
const axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

const files = process.argv.slice(2);
if (!files.length) {
  console.error('usage: node check_accessibility.mjs page.html [more.html ...]');
  process.exit(1);
}

const launchOpts = process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {};
const browser = await chromium.launch(launchOpts);
let failures = 0;

for (const file of files) {
  const url = pathToFileURL(resolve(file)).href;
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(url);
  await page.addScriptTag({ content: axeSource });
  const results = await page.evaluate(async () =>
    // eslint-disable-next-line no-undef
    axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } }),
  );

  // 1.4.10 Reflow: 320 CSS px wide, no horizontal scrolling of the page.
  await page.setViewportSize({ width: 320, height: 800 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

  // 1.4.12 Text Spacing: apply the WCAG spacing and check text is not clipped.
  const clipped = await page.evaluate(() => {
    const s = document.createElement('style');
    s.textContent = '* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }';
    document.head.appendChild(s);
    return [...document.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, th, td')]
      .filter((el) => getComputedStyle(el).overflow === 'hidden' && el.scrollHeight > el.clientHeight + 1).length;
  });

  const problems = [];
  for (const v of results.violations) {
    problems.push(`  [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} element(s))`);
    for (const n of v.nodes.slice(0, 3)) problems.push(`      ${n.target.join(' ')}`);
  }
  if (overflow > 1) problems.push(`  [serious] reflow: page scrolls horizontally by ${overflow}px at 320px wide (1.4.10)`);
  if (clipped) problems.push(`  [serious] text-spacing: ${clipped} element(s) clip text with increased spacing (1.4.12)`);

  if (problems.length) {
    failures++;
    console.log(`FAIL ${file}`);
    console.log(problems.join('\n'));
  } else {
    console.log(`PASS ${file}  (${results.passes.length} axe rules passed, ${results.incomplete.length} need manual review)`);
  }
  await page.close();
}

await browser.close();
process.exit(failures ? 1 : 0);
