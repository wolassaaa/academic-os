const assert=require('node:assert/strict'),path=require('node:path');
const {create}=require(path.resolve(process.argv[2],'src/academic_os/web/board/service-adapter.js'));
let remote=false,patchStatus=200,networkFail=false,malformed=false,csrf='fixture-csrf';const calls=[];
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const M=require(path.resolve(process.argv[2],'src/academic_os/web/board/board-model.js'));
assert.equal(M.deadlineState({review_state:'conflict',source_due_at:'2026-10-20T04:00:00Z'}).label,'截止有冲突');
assert.equal(M.deadlineState({review_state:'ready',source_due_at:null}).label,'截止待确认');
const item={id:'a/b',title:'Fixture',status:'pending',version:7};
const transport=async(url,options)=>{
 calls.push([url,options]);
 if(options.method==='PATCH'){
  if(networkFail)throw Error('lost response');
  return response(malformed?null:{...item,status:'completed',version:8},patchStatus);
 }
 if(url.endsWith('/session'))return response({authenticated:true,initialized:true,remote,csrf_token:csrf});
 if(url.endsWith('/board-context'))return response({timezone:'Asia/Taipei',now:'2026-10-16T04:00:00Z'});
 if(url.endsWith('/dashboard'))return response({items:[item],health:{sources:[]}});
 if(url.endsWith('/health'))return response({sources:[null]});
 return response([item]);
};
(async()=>{
 const a=create(transport);await a.load();const key='same-intent';
 const done=await a.complete(item,key);assert.equal(done.status,'completed');
 const [url,o]=calls.at(-1);assert.equal(url,'/api/v1/items/a%2Fb');assert.equal(o.method,'PATCH');assert.equal(o.headers['X-CSRF-Token'],csrf);assert.equal(o.headers['Idempotency-Key'],key);
 assert.deepEqual(JSON.parse(o.body),{expected_version:7,changes:{status:'completed'}});
 networkFail=true;await assert.rejects(a.complete(item,key),e=>e.code==='uncertain');networkFail=false;
 malformed=true;await assert.rejects(a.complete(item,key),e=>e.code==='uncertain');malformed=false;
 patchStatus=409;await assert.rejects(a.complete(item,key),e=>e.code==='conflict');
 patchStatus=401;await assert.rejects(a.complete(item,key),e=>e.code==='auth');patchStatus=200;
 remote=true;await a.load();const count=calls.length;await assert.rejects(a.complete(item,key),e=>e.code==='readonly');assert.equal(calls.length,count);
 console.log('PASS: completion sends only status/version/CSRF/idempotency; lost response and conflict remain unconfirmed; remote writes blocked.');
})().catch(e=>{console.error(e);process.exitCode=1});
