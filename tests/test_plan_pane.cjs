// Run: node tests/test_plan_pane.cjs. Execute the page's real functions with DOM edges stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const section = (start, end) => {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a, 'page section exists');
  return html.slice(a, b);
};
const elements = {};
let created = [], finished = [];
const card = { id: 'p1', title: 'Plan', planMode: true, tags: ['ui'] };
const ctx = vm.createContext({
  window: { t: (s) => s }, openId: card.id, task: () => card,
  $: (id) => elements[id] ||= { hidden: false, dataset: {}, innerHTML: '', scrollTop: 0 },
  mainTab: 'conversation', esc: String, who: () => 'Claude', fmtMsg: String,
  withDelegateChoice: (t) => t, firstOpenCol: () => 'todo',
  addTask: () => { const t = {}; created.push(t); return t; },
  touch() {}, render() {}, openDetail() {}, moveToDone: (id) => finished.push(id),
});
vm.runInContext(section('let cpData =', 'function cpOpen('), ctx);
vm.runInContext(section('function cpDraw()', '$("#d-cplan").addEventListener'), ctx);
const run = (s) => vm.runInContext(s, ctx);
run('cpFor = "p1"; cpData = {phase: "review", docs: [{name: "plan.md", text: "Old draft"}], dir: "/plans"}');
assert.equal(run('cpReady(task())'), false, 'reviewing an old plan is not ready');
run('cpBuild(task(), (label, cls, click) => click)()');
assert.equal(created.length, 0, 'an unfinished plan cannot start work');
assert.equal(finished.length, 0, 'an unfinished plan stays open');
run('cpData.phase = "ready"; cpData.docs[0].text = "  "');
assert.equal(run('cpReady(task())'), false, 'an empty plan is not ready');
run('cpData.docs[0].text = "Approved steps"');
assert.equal(run('cpReady(task())'), true);
run('cpBuild(task(), (label, cls, click) => click)()');
assert.equal(created.length, 1);
assert(created[0].notes.includes('Approved steps'), 'the ready plan reaches the new card');
assert.deepEqual(finished, ['p1'], 'building moves the plan card to done');
run('cpFor = "other"');
assert.equal(run('cpReady(task())'), false, 'another card cannot reuse the plan');
run('cpFor = "p1"');
for (const id of ['constructor', '__proto__', 'toString']) {
  ctx.nodes = [{ id, label: 'Parent', kind: 'goal', status: 'open', at: '1' },
    { id: 'child', parent: id, label: 'Child', kind: 'task', status: 'open', at: '1' }];
  run('cpData.nodes = nodes; cpDraw()');
  assert(elements['#d-cplan'].innerHTML.includes('Parent'));
  assert(elements['#d-cplan'].innerHTML.includes('Child'));
  run('cpData.nodes[0].at = "2"; cpDraw()');
  assert.equal(run(`cpSeen[${JSON.stringify(id)}]`), '2', 'special IDs track changes');
}
ctx.nodes = [{ id: 'orphan', parent: 'constructor', label: 'Orphan', kind: 'task', status: 'open' }];
run('cpData.nodes = nodes; cpDraw()');
assert(elements['#d-cplan'].innerHTML.includes('Orphan'), 'an unknown parent leaves its child visible');
console.log('PASS: plan readiness, build handoff, special tree IDs, and unknown parents');
