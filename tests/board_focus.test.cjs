const assert=require('node:assert/strict'),path=require('node:path');
const M=require(path.resolve(process.argv[2],'src/academic_os/web/board/board-model.js'));
const items=[{id:'urgent',title:'Urgent',status:'pending',source_due_at:'2026-10-13T12:00:00Z'},{id:'later',title:'Later',status:'pending'},{id:'closed',title:'Closed',status:'completed'}];
const options={now:'2026-10-16T12:00:00Z',timezone:'Asia/Taipei',choices:{urgent:'pool',later:'focus',closed:'focus'}};
const p=M.partition(items,options);
assert.deepEqual(p.important.map(i=>i.id),['later']);assert.deepEqual(p.pool.map(i=>i.id),['urgent']);
assert.equal(M.due(p.pool[0].source_due_at,options.now).state,'overdue');
assert.deepEqual(M.partition(items,{...options,choices:{}}).important.map(i=>i.id),['urgent']);

assert.deepEqual(M.partition(items,{...options,choices:{later:'focus'}}).important.map(i=>i.id),['later','urgent']);

console.log('PASS: personal focus/pool overrides automatic selection; personal pins rank first; overdue and closed semantics retained.');
