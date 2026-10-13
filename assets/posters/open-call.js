/**
 * AI4S-Benchmark open call poster (1080 × 1920).
 *
 * Run `node assets/posters/open-call.js` to create the editable SVG.
 * If `sharp` is available, the same command also exports a PNG.
 */
const fs = require('node:fs');
const path = require('node:path');

const WIDTH = 1080;
const HEIGHT = 1920;
const OUT_DIR = __dirname;
const BASENAME = 'open-call';

const ink = '#0D1730';
const blue = '#2474FF';
const paper = '#FFFFFF';
const quiet = '#5B6577';
const SANS_FAMILY = 'Arial, sans-serif';
const SANS_TRACKING = 1.5;

function text(x, y, value, attrs = '') {
  return `<text x="${x}" y="${y}" ${attrs}>${value}</text>`;
}

function rule(x1, y1, x2, y2, color = ink, width = 2) {
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${width}"/>`;
}

function sans(size, color = ink, extra = '') {
  return `fill="${color}" font-family="${SANS_FAMILY}" font-size="${size}" font-weight="700" letter-spacing="${SANS_TRACKING}" ${extra}`;
}

const parts = [
  `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-labelledby="title desc">`,
  '<title id="title">AI4S-Benchmark — Open Call for Scientific Tasks</title>',
  '<desc id="desc">An editorial-style poster inviting scientists to contribute challenging, verifiable research tasks.</desc>',
  `<rect width="${WIDTH}" height="${HEIGHT}" fill="${paper}"/>`,

  // Masthead: sharp rules and quiet metadata, as on a printed journal cover.
  text(76, 109, 'AI4S-BENCHMARK', sans(38)),
  text(1003, 108, 'OPEN CALL  /  2026', sans(22, ink, 'text-anchor="end"')),
  rule(76, 145, 1004, 145, ink, 3),
  text(76, 220, 'SCIENTIFIC TASK CONTRIBUTIONS', sans(24, quiet)),

  // A small invitation line and a single-line editorial title.
  text(76, 340, 'CALL FOR', `fill="${ink}" font-family="Georgia, 'Times New Roman', serif" font-size="70" font-weight="700" letter-spacing="-2"`),
  text(70, 476, 'AI4S AGENTIC TASK', `fill="${blue}" font-family="Georgia, 'Times New Roman', serif" font-size="76" font-weight="700" letter-spacing="-3"`),
  rule(76, 550, 1004, 550, ink, 2),

  // Three acceptance cues, set like a newspaper index rather than UI cards.
  text(76, 632, 'WHAT WE ARE LOOKING FOR', sans(24, quiet)),
  text(76, 742, '01', `fill="${blue}" font-family="Georgia, 'Times New Roman', serif" font-size="50" font-style="italic"`),
  text(188, 741, 'HARBOR FORMAT', sans(44)),
  rule(76, 785, 1004, 785, ink, 1.5),
  text(76, 894, '02', `fill="${blue}" font-family="Georgia, 'Times New Roman', serif" font-size="50" font-style="italic"`),
  text(188, 893, 'SCIENTIFICALLY CHALLENGING', sans(44)),
  rule(76, 937, 1004, 937, ink, 1.5),
  text(76, 1046, '03', `fill="${blue}" font-family="Georgia, 'Times New Roman', serif" font-size="50" font-style="italic"`),
  text(188, 1045, 'BOUNDED &amp; EVALUABLE', sans(44)),
  rule(76, 1089, 1004, 1089, ink, 2),

  // The explanatory paragraph remains; the address is printed as information,
  // not styled like a web button or a clickable link.
  text(76, 1220, 'We are collecting challenging,', `fill="${ink}" font-family="Georgia, 'Times New Roman', serif" font-size="50"`),
  text(76, 1287, 'verifiable scientific tasks from', `fill="${ink}" font-family="Georgia, 'Times New Roman', serif" font-size="50"`),
  text(76, 1354, 'researchers across disciplines.', `fill="${ink}" font-family="Georgia, 'Times New Roman', serif" font-size="50"`),
  text(76, 1693, 'TASK PROPOSALS', sans(24, quiet)),
  text(76, 1773, 'ai4sbench.org/submit/', sans(46)),
  rule(76, 1821, 1004, 1821, ink, 3),
  text(76, 1875, 'OPEN  ·  INDEPENDENT  ·  COMMUNITY-BUILT', sans(18, quiet)),
  text(1004, 1875, 'contact@ai4sbench.org', sans(18, quiet, 'text-anchor="end"')),
  '</svg>',
];

const svg = parts.join('\n');
const svgPath = path.join(OUT_DIR, `${BASENAME}.svg`);
fs.writeFileSync(svgPath, svg, 'utf8');
console.log(svgPath);

try {
  const sharp = require('sharp');
  sharp(Buffer.from(svg))
    .png({ compressionLevel: 9 })
    .toFile(path.join(OUT_DIR, `${BASENAME}.png`))
    .then(({ width, height }) => console.log(`PNG: ${width} × ${height}`))
    .catch((error) => {
      console.error(error);
      process.exitCode = 1;
    });
} catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  console.log('SVG created; install sharp for PNG export.');
}
