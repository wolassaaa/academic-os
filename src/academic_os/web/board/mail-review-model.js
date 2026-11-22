(function(root){
  'use strict';
  class ReviewError extends Error{constructor(code){super(code);this.code=code;}}
  function reviewState(item){const evidence=item.evidence||[];if(item.manual_origin||!evidence.length||evidence.some(e=>!String(e.source||'').startsWith('gmail:')))return 'formal';const state=item.mail_review?.state;if(['pending','ignored','confirmed'].includes(state))return state;return ['completed','submitted','cancelled'].includes(item.status)||item.scheduled_start||item.review_state==='ready'?'confirmed':'pending';}
  const pad=n=>String(n).padStart(2,'0');
  function localInput(value){if(!value)return '';const d=new Date(value);if(!Number.isFinite(d.getTime()))throw new ReviewError('invalid-date');return String(d.getFullYear()).padStart(4,'0')+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+'T'+pad(d.getHours())+':'+pad(d.getMinutes());}
  function calendar(value){const m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?/.exec(value);if(!m||Number(m[1])<1||Number(m[1])>=2090||Number(m[4])>23||Number(m[5])>59||Number(m[6]||0)>59)return false;const date=new Date(m[1]+'-'+m[2]+'-'+m[3]+'T00:00:00Z');return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===value.slice(0,10);}
  function changes(item,{title,local='',exact=''}){
    title=String(title||'').trim();if(!title||title.length>500)throw new ReviewError('title');
    exact=String(exact||'').trim();let due=null;
    if(exact){if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/.test(exact)||!calendar(exact)||!Number.isFinite(new Date(exact).getTime()))throw new ReviewError('invalid-date');due=new Date(exact).toISOString();}
    else if(local===localInput(item.source_due_at))due=item.source_due_at||null;
    else if(local){
      if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(local)||!calendar(local))throw new ReviewError('invalid-date');
      const d=new Date(local);if(!Number.isFinite(d.getTime())||localInput(d)!==local)throw new ReviewError('time-gap');
      for(let minutes=-180;minutes<=180;minutes+=30)if(minutes&&localInput(new Date(d.getTime()+minutes*60000))===local)throw new ReviewError('time-ambiguous');
      due=d.toISOString();
    }
    if(due&&new Date(due).getUTCFullYear()>=2090)throw new ReviewError('invalid-date');
    return {title,source_due_at:due};
  }
  const api={reviewState,localInput,changes,ReviewError};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.AcademicMailReviewModel=api;
})(globalThis);
