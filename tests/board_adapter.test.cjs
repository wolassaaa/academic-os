const assert=require('node:assert/strict');
const path=require('node:path');
const {create}=require(path.resolve(process.argv[2],'src/academic_os/web/board/service-adapter.js'));
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const M=require(path.resolve(process.argv[2],'src/academic_os/web/board/board-model.js'));
const items=[{id:'mixed',title:'Mixed',status:'pending',mail_review:{state:'pending'},evidence:[{source:'gmail:test'},{source:'moodle:test'}]},{id:'pure',title:'Candidate',status:'pending',mail_review:{state:'pending'},evidence:[{source:'gmail:test'}]},{id:'closed',title:'Submitted',status:'submitted'}];
assert.deepEqual(M.partition(items,{now:new Date('2026-10-15T04:00:00Z'),timezone:'America/New_York'}).active.map(i=>i.id),['mixed']);
assert.equal(M.filter([{id:'b',title:'Boundary',source_due_at:'2026-10-16T03:30:00Z'}],{startDate:'2026-10-15',endDate:'2026-10-15',timezone:'America/New_York'}).length,1);
assert.equal(M.timeProgress(new Date('2026-10-15T04:00:00Z'),'America/New_York').semester,null);
const calls=[];
let remote=false,authenticated=true,healthFail=false,itemsFail=false;
const transport=async(url,options)=>{
 calls.push([url,options]);
 if(url.endsWith('/session'))return response({authenticated,initialized:true,remote});
 if(url.endsWith('/board-context'))return response({now:'2026-10-15T04:00:00+00:00',timezone:'America/New_York',semester:null});
 if(url.endsWith('/dashboard'))return response({items:[{id:'remote',title:'Remote',status:'pending'}],health:{sources:[]}});
 if(url.includes('/items'))return itemsFail?response({},503):response([{id:'a',title:'Formal',status:'pending',source_due_at:null}]);
 if(url.endsWith('/health'))return healthFail?response({},503):response({sources:[],worker_online:false,data_stale:true});
 throw Error('Unexpected endpoint '+url);
};
(async()=>{
 const a=create(transport);let s=await a.load();assert.equal(s.mode,'service');assert.equal(s.items[0].id,'a');assert.equal(s.semester,null);assert.equal(s.timezone,'America/New_York');assert.equal(s.items[0].source_due_at,null);
 for(const [url,o] of calls){assert.equal(o.method,'GET');assert.equal(o.credentials,'same-origin');assert.equal(o.cache,'no-store');assert.ok(url.startsWith('/api/v1/'));assert.ok(!o.headers&&!o.body);}
 healthFail=true;s=await a.load();assert.equal(s.health,null);assert.equal(s.healthUnavailable,true);assert.equal(s.items.length,1);
 itemsFail=true;await assert.rejects(a.load(),e=>e.code==='http');itemsFail=false;healthFail=false;
 authenticated=false;await assert.rejects(a.load(),e=>e.code==='auth');authenticated=true;
 calls.length=0;remote=true;s=await a.load();assert.equal(s.items[0].id,'remote');assert.equal(s.remote,true);assert.ok(!calls.some(([u])=>u.includes('/items')||u.endsWith('/health')||u.includes('/config')));
 const bad=create(async u=>u.endsWith('/session')?response({authenticated:true}):u.endsWith('/board-context')?response({timezone:'Invalid/Zone',now:'bad'}):response([]));await assert.rejects(bad.load(),e=>e.code==='schema');
 const offline=create(async()=>{throw Error('network')});await assert.rejects(offline.load(),e=>e.code==='offline');
 console.log('PASS: local/remote GET-only adapters; no config/credentials persistence; clock/timezone validation; unknown dates; auth/offline/HTTP errors; partial health failure without fictional fallback.');
})().catch(e=>{console.error(e);process.exitCode=1});
