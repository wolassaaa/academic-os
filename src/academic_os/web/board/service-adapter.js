/* Same-origin facts and one explicit completion action. Session data stays in memory. */
(function(root){
  'use strict';
  class BoardError extends Error{constructor(code){super(code);this.code=code;}}
  function create(transport){
    let sessionInfo=null;
    async function get(path){
      let response;
      try{response=await transport('/api/v1'+path,{method:'GET',credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000)});}catch{throw new BoardError('offline');}
      if(response.status===401)throw new BoardError('auth');
      if(!response.ok)throw new BoardError('http');
      try{return await response.json();}catch{throw new BoardError('schema');}
    }
    async function load(){
      const session=await get('/session');
      sessionInfo=session;
      if(!session.authenticated)throw new BoardError(session.initialized?'auth':'setup');
      const healthResult=p=>p.then(value=>({value}),error=>({error}));
      const [context,data,health,focus]=await Promise.all([
        get('/board-context'),get(session.remote?'/dashboard':'/items?view=formal'),
        session.remote?Promise.resolve(null):healthResult(get('/health')),
        session.remote?Promise.resolve(null):healthResult(get('/board-focus'))
      ]);
      if(focus?.error?.code==='auth')throw focus.error;
      if(health?.error?.code==='auth')throw health.error;
      const items=session.remote?data.items:data;
      if(!Array.isArray(items)||items.some(i=>!i||typeof i.id!=='string'||typeof i.title!=='string')||new Set(items.map(i=>i.id)).size!==items.length)throw new BoardError('schema');
      try{if(!Number.isFinite(new Date(context.now).getTime())||typeof context.timezone!=='string')throw Error();new Intl.DateTimeFormat('zh-CN',{timeZone:context.timezone});}catch{throw new BoardError('schema');}
      const rawStatus=session.remote?data.health:health.value;
      const status=rawStatus&&Array.isArray(rawStatus.sources)&&rawStatus.sources.every(s=>s&&typeof s==='object'&&typeof s.id==='string')?rawStatus:null;
      const preferences=focus?.value;
      const focusPrefs=validFocus(preferences)?preferences:null;
      return {focusPrefs,canFocus:!session.remote&&!!session.csrf_token&&!!focusPrefs,mode:'service',now:context.now,timezone:context.timezone,semester:null,items,
        username:session.username||'',remote:!!session.remote,canComplete:!session.remote&&!!session.csrf_token,health:status||null,healthUnavailable:!status};
    }
    async function changeStatus(item,operationKey,status){
      if(!sessionInfo?.authenticated)throw new BoardError('auth');
      if(sessionInfo.remote||!sessionInfo.csrf_token)throw new BoardError('readonly');
      if(!item||!Number.isInteger(item.version)||typeof item.id!=='string'||!operationKey)throw new BoardError('schema');
      let response;
      try{response=await transport('/api/v1/items/'+encodeURIComponent(item.id),{
        method:'PATCH',credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000),
        headers:{'Content-Type':'application/json','X-CSRF-Token':sessionInfo.csrf_token,'Idempotency-Key':operationKey},
        body:JSON.stringify({expected_version:item.version,changes:{status}})
      });}catch{throw new BoardError('uncertain');}
      if(response.status===401){sessionInfo=null;throw new BoardError('auth');}
      if(response.status===403)throw new BoardError('readonly');
      if(response.status===409)throw new BoardError('conflict');
      if(response.status>=500)throw new BoardError('uncertain');
      if(!response.ok)throw new BoardError('invalid');
      let result;try{result=await response.json();}catch{throw new BoardError('uncertain');}
      if(!result||result.id!==item.id||result.status!==status||!Number.isInteger(result.version))throw new BoardError('uncertain');
      return result;
    }
    const complete=(item,key)=>changeStatus(item,key,'completed');
    const reopen=(item,key)=>changeStatus(item,key,'pending');
    function validFocus(value){return !!value&&Number.isInteger(value.revision)&&value.revision>=0&&!!value.choices&&typeof value.choices==='object'&&!Array.isArray(value.choices)&&Object.values(value.choices).every(v=>v==='focus'||v==='pool');}
    async function setFocus(itemId,mode,revision,operationKey){
      if(!sessionInfo?.authenticated)throw new BoardError('auth');
      if(sessionInfo.remote||!sessionInfo.csrf_token)throw new BoardError('readonly');
      let response;
      try{response=await transport('/api/v1/board-focus',{method:'PUT',credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json','X-CSRF-Token':sessionInfo.csrf_token,'Idempotency-Key':operationKey},body:JSON.stringify({item_id:itemId,mode,expected_revision:revision})});}catch{throw new BoardError('uncertain');}
      if(response.status===401){sessionInfo=null;throw new BoardError('auth');}
      if(response.status===403)throw new BoardError('readonly');
      if(response.status===409)throw new BoardError('conflict');
      if(response.status>=500)throw new BoardError('uncertain');
      if(!response.ok)throw new BoardError('invalid');
      let value;try{value=await response.json();}catch{throw new BoardError('uncertain');}
      if(!validFocus(value))throw new BoardError('uncertain');
      return value;
    }
    return {load,complete,reopen,setFocus};
  }
  const api={create,BoardError};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.AcademicServiceAdapter=create(root.fetch.bind(root));
})(globalThis);
