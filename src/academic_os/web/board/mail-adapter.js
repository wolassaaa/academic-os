(function(root){
  'use strict';
  class MailError extends Error{constructor(code){super(code);this.code=code;}}
  function create(transport){
    let sessionInfo=null;
    // Remote is read-only unless the server grants tailnet full access.
    const readonly=s=>!!s&&!!s.remote&&s.access!=='full';
    async function get(path){
      let r;try{r=await transport('/api/v1'+path,{method:'GET',credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000)});}catch{throw new MailError('offline');}
      if(r.status===401)throw new MailError('auth');if(r.status===403)throw new MailError('remote');if(r.status===404)throw new MailError('missing');if(!r.ok)throw new MailError('http');
      try{return await r.json();}catch{throw new MailError('schema');}
    }
    async function session(){const s=await get('/session');sessionInfo=s;if(!s.authenticated)throw new MailError('auth');if(readonly(s))throw new MailError('remote');return {username:typeof s.username==='string'?s.username:'',canReview:!!s.csrf_token&&!readonly(s)};}
    function validDay(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return false;const d=new Date(value+'T00:00:00Z');return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===value;}
    function params(query){
      const q={q:'',account:'',review:'all',date_mode:'all',start:'',end:'',offset:0,...query};
      if(!Number.isInteger(q.offset)||q.offset<0||typeof q.q!=='string'||q.q.length>500||!['all','pending','ignored'].includes(q.review)||!['all','today','custom','undated'].includes(q.date_mode))throw new MailError('invalid');
      if(q.date_mode==='custom'&&(!validDay(q.start)||!validDay(q.end)||q.start>q.end))throw new MailError('invalid');
      const p=new URLSearchParams({limit:'20',offset:String(q.offset),body_chars:'500',review:q.review,date_mode:q.date_mode});
      for(const k of ['q','account','start','end'])if(q[k])p.set(k,q[k]);return p;
    }
    async function list(query){
      const p=params(query);const user=await session(),data=await get('/mail/digest?'+p);
      if(!data||!Array.isArray(data.items)||!Number.isInteger(data.total)||data.total<0||data.limit!==20||!Number.isInteger(data.offset)||data.offset<0||!Array.isArray(data.accounts)||data.accounts.some(a=>typeof a!=='string')||typeof data.timezone!=='string'||data.items.some(m=>!m||typeof m.key!=='string'||typeof m.body_excerpt!=='string'))throw new MailError('schema');
      try{new Intl.DateTimeFormat('zh-CN',{timeZone:data.timezone});}catch{throw new MailError('schema');}
      return {...data,username:user.username,canReview:user.canReview};
    }
    async function detail(key){
      if(typeof key!=='string'||!key||key.length>1000)throw new MailError('invalid');
      const user=await session();const [data,context]=await Promise.all([get('/mail/'+encodeURIComponent(key)),get('/board-context')]);
      try{if(typeof context?.timezone!=='string')throw Error();new Intl.DateTimeFormat('zh-CN',{timeZone:context.timezone});}catch{throw new MailError('schema');}
      if(!data||data.key!==key||!Array.isArray(data.linked_items)||(data.body!=null&&typeof data.body!=='string')||data.linked_items.some(i=>!i||typeof i.id!=='string'||typeof i.title!=='string'||!Number.isInteger(i.version)))throw new MailError('schema');
      return {...data,username:user.username,canReview:user.canReview,timezone:context.timezone};
    }
    async function review(item,action,changes,operationKey,username){
      if(readonly(sessionInfo))throw new MailError('remote');
      if(!sessionInfo?.authenticated)throw new MailError('auth');
      if(!sessionInfo.csrf_token||sessionInfo.username!==username)throw new MailError('permission');
      if(!item||typeof item.id!=='string'||!Number.isInteger(item.version)||!['confirm','ignore','restore'].includes(action)||!operationKey)throw new MailError('invalid');
      let r;try{r=await transport('/api/v1/items/'+encodeURIComponent(item.id)+'/mail-review',{method:'POST',credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json','X-CSRF-Token':sessionInfo.csrf_token,'Idempotency-Key':operationKey},body:JSON.stringify({action,expected_version:item.version,changes})});}catch{throw new MailError('uncertain');}
      if(r.status===401){sessionInfo=null;throw new MailError('auth');}if(r.status===403)throw new MailError('permission');if(r.status===409)throw new MailError('conflict');if(r.status>=500)throw new MailError('uncertain');if(!r.ok)throw new MailError('invalid');
      let value;try{value=await r.json();}catch{throw new MailError('uncertain');}
      const target={confirm:'confirmed',ignore:'ignored',restore:'pending'}[action];
      if(!value||value.id!==item.id||typeof value.title!=='string'||!('source_due_at' in value)||!Number.isInteger(value.version)||value.version<=item.version||value.mail_review?.state!==target||!Number.isInteger(value.mail_review.revision))throw new MailError('uncertain');
      return value;
    }
    return {list,detail,session,review};
  }
  const api={create,MailError};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.AcademicMailAdapter=create(root.fetch.bind(root));
})(globalThis);
