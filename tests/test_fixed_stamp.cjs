// Run: node tests/test_fixed_stamp.cjs. A finding box whose fixer says it was fixed gets the big green FIXED stamp;
// the reviewer's own boxes (with a "Fix:" suggestion) and "Not fixed" ones do not. Runs the page's real formatter.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const a = html.indexOf('function esc(s)'), b = html.indexOf('// A plan card\'s live pane', a);
assert(a >= 0 && b > a, 'formatter section exists');
const ctx = vm.createContext({ window: { t: (s) => s } });
vm.runInContext(html.slice(a, b), ctx);
const fmt = (s) => vm.runInContext('fmtMsgRaw', ctx)(s);
const boxes = (h) => h.split('<div class="finding').slice(1).map((x) => x.split('<div class="ftitle">')[0]);

const fixReport = `I agreed with both findings and fixed both.

Finding 1 (major): Cartoon style asks the AI to draw a face on items

- Where: \`app/Services/Ai/PortraitCreator.php:42\`
- Fixed: on an item, Cartoon now uses the same style words without the face.
- Proof: a new test picks Cartoon on an item.

Finding 2 (minor): Clearing "What to draw" still spends AI tokens

- Where: \`app/Livewire/Worlds/EntityPage.php:262\`
- **Fixed in 1e4377d:** saving no longer fills an empty box.
- Proof: a new test empties the box.

Finding 3 (minor): the list is slow

- Where: \`a.php:3\`
- Status: fixed — cached now.

Finding 4 (low): the hint is vague

- Where: \`b.php:9\`
- Not fixed: the owner wants it as is.

All 1465 tests pass.`;
const h = fmt(fixReport), bx = boxes(h);
assert.equal(bx.length, 4, 'four boxes');
for (const k of [0, 1, 2]) {
  assert.match(bx[k], /^ \w+ fixed"/, `box ${k + 1} is marked fixed`);
  assert.match(bx[k], /<span class="fstamp">Fixed<\/span><\/div>$/, `box ${k + 1} has the stamp in its head`);
}
assert.doesNotMatch(bx[3], /fixed|fstamp/, '"Not fixed" gets no stamp');
assert.equal((h.match(/fstamp/g) || []).length, 3, 'exactly three stamps');
assert.equal((h.match(/<div class="finding/g) || []).length, (h.match(/<div class="fhead">/g) || []).length, 'every box keeps its head');
assert.match(h, /All 1465 tests pass\./, 'text after the boxes is kept');
assert.match(h, /<b class="flbl">Fixed:<\/b>/, 'the Fixed row itself still shows');

// The reviewer's first report: a "Fix:" suggestion is not a fix that was made.
const review = `Finding 1 (major): a kind breaks the menu

- Where: \`x.php:3\`
- Fix: use a plain lookup.
- Proof: Reproduced.`;
assert.doesNotMatch(fmt(review), /fstamp|finding major fixed/, 'a suggested Fix gets no stamp');

// A second review's one-line status gets the stamp too; "fixed" is not repeated as plain text.
const status = fmt('Finding 1 (major): fixed\n\nFinding 2 (minor): still open');
assert.match(status, /<div class="fline fixed"><span class="ftag major">major<\/span><span class="fnum">Finding 1<\/span><span class="fstamp">Fixed<\/span><\/div>/);
assert.doesNotMatch(status.split('Finding 2')[1], /fstamp/, 'a still-open line gets none');
const withSha = fmt('Finding 1 (major): fixed in abc1234');
assert.match(withSha, /fstamp.*in <code>abc1234<\/code>/, 'the rest of the status line stays');

// Two fixed boxes in a row, then one more message box: indexes stay right after the first swap.
const two = fmt('Finding 1: a\n\n- Fixed: x\n\nFinding 2: b\n\n- Fixed: y\n\nFinding 3: c\n\n- Fix: z');
assert.equal((two.match(/fstamp/g) || []).length, 2);
assert.equal((two.match(/<div class="finding fixed">/g) || []).length, 2);
assert.equal((two.match(/<div class="finding">/g) || []).length, 1);
// A fixer that says it in the title ("— fixed", as review-fix reports do) with "Now:" rows and no "Fixed:" row.
const tail = fmt(`## Review fixes done

### Finding 1 (major): Weapons and spells can vanish with no warning — fixed

- Where: \`app/Services/GameDataService.php:786\`
- Now: a row with a number in it but nothing picked shows a red message.
- Proof: both tests pass.

### Finding 2 (minor): Spell min range hides (fixed)

- Now: it reads "min range 2 m".

### Finding 3 (minor): A bad value is ignored — not fixed

- Why: the owner wants it as is.

### Finding 4 (minor): Props have no limit -- fixed

- Now: at most 20 props.`);
const tb = boxes(tail);
assert.equal(tb.length, 4, 'four boxes');
for (const k of [0, 1, 3]) assert.match(tb[k], /^ \w+ fixed".*fstamp/, `title-fixed box ${k + 1} gets the stamp`);
assert.doesNotMatch(tb[2], /fixed"|fstamp/, '"— not fixed" gets no stamp');
assert.match(tail, /<div class="ftitle">Weapons and spells can vanish with no warning<\/div>/, 'the words "— fixed" leave the title');
assert.match(tail, /<div class="ftitle">Spell min range hides<\/div>/);
assert.match(tail, /A bad value is ignored — not fixed/, '"not fixed" stays in its title');
const tailLine = fmt('Finding 1 (major): the menu breaks — fixed');
assert.match(tailLine, /<div class="fline fixed">.*fstamp.*<span>the menu breaks<\/span><\/div>/, 'a one-line title-fixed status gets the stamp');
assert.doesNotMatch(fmt('Finding 1 (major): the fix is not done\n\n- Fix: do it'), /fstamp/, 'no fixed word, no stamp');
console.log('ok fixed stamp');
