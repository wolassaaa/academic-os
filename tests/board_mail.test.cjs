const assert=require('node:assert/strict'),path=require('node:path');
const {create}=require(path.resolve(process.argv[2],'src/academic_os/web/board/mail-adapter.js'));
let remote=false,auth=true,status=200,invalid=false;const calls=[];
const transport=async(url,o)=>{calls.push([url,o]);const body=url.endsWith('/session')?{authenticated:auth,initialized:true,remote,username:'fixture'}:url.endsWith('/board-context')?{timezone:'Asia/Taipei'}:url.includes('/digest?')?invalid?{}:{items:[{key:'school:m1',subject:'Fixture',body_excerpt:'<script>literal</script>'}],total:42,offset:20,limit:20,accounts:['school'],timezone:'Asia/Taipei'}:{key:'school:m1',subject:'Fixture',body:'literal',linked_items:[]};return{ok:status<400,status,json:async()=>body};};
(async()=>{
 const a=create(transport);const p=await a.list({q:'a & b',offset:20,review:'pending'});assert.equal(p.total,42);
 const query=new URL('https://fixture'+calls.at(-1)[0]).searchParams;assert.equal(query.get('q'),'a & b');assert.equal(query.get('offset'),'20');assert.equal(query.get('review'),'pending');
 assert.equal((await a.detail('school:m1')).body,'literal');assert.ok(calls.some(([u])=>u.endsWith('school%3Am1')));
 for(const [url,o] of calls){assert.ok(url.startsWith('/api/v1/'));assert.equal(o.method,'GET');assert.equal(o.cache,'no-store');assert.equal(o.credentials,'same-origin');assert.ok(!o.body&&!o.headers);}
 await assert.rejects(a.list({offset:-1}),e=>e.code==='invalid');await assert.rejects(a.list({review:'confirmed'}),e=>e.code==='invalid');
 invalid=true;await assert.rejects(a.list({}),e=>e.code==='schema');invalid=false;
 calls.length=0;remote=true;await assert.rejects(a.list({}),e=>e.code==='remote');assert.equal(calls.length,1);remote=false;
 auth=false;await assert.rejects(a.list({}),e=>e.code==='auth');auth=true;
 status=401;await assert.rejects(a.detail('school:m1'),e=>e.code==='auth');status=200;
 const offline=create(async()=>{throw Error('offline')});await assert.rejects(offline.list({}),e=>e.code==='offline');
 console.log('PASS: mail GET-only adapter; encoded query/key, pagination, validation, malformed data, auth/offline/remote guards; no credentials persistence or writes.');
})().catch(e=>{console.error(e);process.exitCode=1});
