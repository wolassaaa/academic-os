(function(root){
  'use strict';
  const M=root.AcademicMailReviewModel,A=root.AcademicMailAdapter,drafts=new Map(),intents=new Map();
  let owner='',selected=null,trigger=null,triggerItemId='';
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
  const button=(text,fn)=>{const n=el('button','button tonal',text);n.type='button';n.addEventListener('click',fn);return n;};
  const feedback=el('p','mail-review-feedback');feedback.role='status';feedback.setAttribute('aria-live','polite');feedback.hidden=true;document.querySelector('.service-status').after(feedback);
  const dialog=el('dialog','mail-review-dialog');dialog.setAttribute('aria-labelledby','mail-review-heading');
  const heading=el('h2','','逐条核对');heading.id='mail-review-heading';const summary=el('p'),problem=el('p','mail-conflict'),actions=el('div','mail-review-buttons');problem.role='alert';
  const cancel=button('取消',()=>close()),submit=button('确认操作',()=>send());submit.className='button primary';actions.append(cancel,submit);dialog.append(heading,summary,problem,actions);document.body.append(dialog);
  dialog.addEventListener('cancel',e=>{e.preventDefault();if(!root.AcademicMailReviewBusy)close();});
  dialog.addEventListener('keydown',e=>{if(e.key==='Escape'&&root.AcademicMailReviewBusy)e.preventDefault();});
  const messages={title:'请填写 1–500 字的事项标题。','invalid-date':'日期格式不正确，请使用有效日期和明确时区。','time-gap':'此时间因夏令时跳转而不存在，请重新选择。','time-ambiguous':'此时间对应两个时刻，请在精确时间中填写时区偏移。',conflict:'事项已变化或截止存在冲突。请刷新后重新核对；原草稿仍保留。',permission:'当前会话没有确认权限。请重新登录后核对。',auth:'登录已过期，请重新登录。',remote:'远程看板仅供阅读。',uncertain:'尚未收到可靠回执。请核实未决操作，重复请求将沿用同一个操作编号。',invalid:'核对内容未通过校验，请检查后重新操作。'};
  function announce(text){feedback.textContent=text;feedback.hidden=false;}
  function close(){if(root.AcademicMailReviewBusy)return;dialog.close();selected=null;if(trigger?.isConnected)trigger.focus({preventScroll:true});else{const title=[...document.querySelectorAll('.mail-candidate')].find(n=>n.dataset.itemId===triggerItemId)?.querySelector('h3');title?.setAttribute('tabindex','-1');title?.focus({preventScroll:true});}}
  function busy(value){root.AcademicMailReviewBusy=value;cancel.disabled=submit.disabled=value;document.querySelector('.mail-refresh').disabled=value;setEnabled();}
  function setEnabled(){const disabled=root.AcademicMailReviewBusy||document.body.dataset.serviceState!=='ready';document.querySelectorAll('.mail-review-controls input,.mail-review-controls button').forEach(n=>n.disabled=disabled||n.dataset.locked==='true');}
  function begin(item,action,changes,origin){
    if(root.AcademicMailReviewBusy||document.body.dataset.serviceState!=='ready')return;
    const data=root.AcademicMailCurrent();if(!data?.canReview)return;
    const existing=intents.get(item.id);
    selected=existing||{item:structuredClone(item),action,changes,username:data.username,key:crypto.randomUUID()};
    trigger=origin;triggerItemId=item.id;heading.textContent=existing?'核实未决操作':{confirm:'确认为邮件待办',ignore:'忽略这条候选',restore:'恢复到待核对'}[action];
    summary.textContent=(selected.changes?.title||selected.item.title)+'。'+(selected.action==='confirm'?'确认后进入正式待办；截止未知则留空，不写入日历。':selected.action==='ignore'?'仅隐藏这条候选，原邮件保留，可以恢复。':'仅恢复这条候选，仍需核对后才产生截止提醒。');
    problem.textContent='';submit.textContent=existing?'核实并重试':'确认操作';dialog.showModal();cancel.focus();root.AcademicMotion?.panel(dialog);
  }
  async function send(){
    if(root.AcademicMailReviewBusy||!selected)return;
    const intent=selected;intents.set(intent.item.id,intent);problem.textContent='正在提交，请等待回执…';busy(true);
    try{
      const receipt=await A.review(intent.item,intent.action,intent.changes,intent.key,intent.username);
      intents.delete(intent.item.id);drafts.delete(intent.item.id);busy(false);dialog.close();selected=null;
      const applied=root.AcademicMailApplyReview(receipt);announce('已收到回执：'+{confirm:'已确认为邮件待办',ignore:'已忽略候选',restore:'已恢复待核对'}[intent.action]+'。');
      const data=await root.AcademicMailRefresh();if(!data)announce('操作已确认；刷新失败，当前显示可能过期，请稍后刷新。');
      if(!applied)announce('操作已确认，但事项已有后续更新；已重新读取当前状态。');
      const title=[...document.querySelectorAll('.mail-candidate')].find(n=>n.dataset.itemId===receipt.id)?.querySelector('h3');title?.setAttribute('tabindex','-1');title?.focus({preventScroll:true});
    }catch(e){
      if(e.code!=='uncertain')intents.delete(intent.item.id);
      busy(false);problem.textContent=messages[e.code]||'核对失败，请检查连接后重试。';announce(problem.textContent);
      if(e.code==='auth'||e.code==='remote'){dialog.close();selected=null;root.AcademicMailExpire();}
      else if(e.code!=='uncertain'){submit.disabled=true;selected=null;}
      else submit.textContent='核实并重试';
      // The dialog owns no draft: rerendering preserves each candidate independently.
      root.AcademicMailRender();
    }
  }
  function decorate(data){
    if(owner!==data.username){drafts.clear();intents.clear();owner=data.username;feedback.hidden=true;}
    for(const item of data.linked_items){
      const card=[...document.querySelectorAll('.mail-candidate')].find(n=>n.dataset.itemId===item.id);if(!card)continue;
      const state=M.reviewState(item),pendingIntent=intents.get(item.id);if(!data.canReview||(!pendingIntent&&!['pending','ignored'].includes(state)))continue;
      const controls=el('div','mail-review-controls');card.append(controls);
      if(pendingIntent){controls.append(el('p','mail-conflict','这条候选有未决操作，核实回执前暂停其他修改。'),button('核实未决操作',e=>begin(item,pendingIntent.action,pendingIntent.changes,e.currentTarget)));continue;}
      if(state==='ignored'){controls.append(button('恢复待核对',e=>begin(item,'restore',{},e.currentTarget)));continue;}
      if(state!=='pending')continue;
      let draft=drafts.get(item.id);if(!draft||(!draft.dirty&&draft.version!==item.version)){draft={version:item.version,title:item.title,local:M.localInput(item.source_due_at),exact:'',dirty:false};drafts.set(item.id,draft);}
      const stale=draft.version!==item.version,form=el('form','mail-review-form');controls.append(form);
      function field(label,type,value,key){const wrap=el('label','mail-review-field'),input=el('input');input.type=type;input.value=value;input.setAttribute('aria-label',label);if(key==='title')input.maxLength=500;input.dataset.locked=String(stale);input.addEventListener('input',()=>{draft[key]=input.value;draft.dirty=true;});wrap.append(el('span','',label),input);return {wrap,input};}
      const title=field('事项标题','text',draft.title,'title'),local=field('截止时间','datetime-local',draft.local,'local');form.append(title.wrap,local.wrap);
      const tz=Intl.DateTimeFormat().resolvedOptions().timeZone;form.append(el('p','muted','编辑时区：'+tz+' · 未知时间留空；当前来源显示时区：'+data.timezone));
      const clear=button('清空截止',()=>{draft.local=draft.exact='';draft.dirty=true;local.input.value=exact.input.value='';});clear.dataset.locked=String(stale);form.append(clear);
      const advanced=el('details','mail-review-exact'),exact=field('精确时间（含时区）','text',draft.exact,'exact');exact.input.placeholder='2026-10-17T18:00:00+08:00';advanced.append(el('summary','','精确时间 / 夏令时'),exact.wrap,el('p','muted','填写后优先使用精确时间，需包含 Z 或 +08:00 等时区偏移。'));form.append(advanced);
      const inline=el('p','mail-conflict');inline.role='alert';form.append(inline);
      const confirm=el('button','button primary','确认为待办');confirm.type='submit';confirm.dataset.locked=String(stale||item.review_state==='conflict');
      const ignore=button('忽略候选',e=>begin(item,'ignore',{},e.currentTarget));ignore.dataset.locked=String(stale);
      const buttons=el('div','mail-review-buttons');buttons.append(confirm,ignore);form.append(buttons);
      if(stale){inline.textContent='服务器版本已变化。草稿保留，请重新读取并核对，未自动覆盖。';controls.append(button('放弃草稿并重新核对',()=>{drafts.delete(item.id);root.AcademicMailRender();}));}
      form.addEventListener('submit',e=>{e.preventDefault();inline.textContent='';if(stale||confirm.disabled)return;try{begin(item,'confirm',M.changes(item,draft),confirm);}catch(err){inline.textContent=messages[err.code]||'请检查核对内容。';}});
    }
    setEnabled();
  }
  function expire(){drafts.clear();intents.clear();owner='';feedback.hidden=true;}
  root.AcademicMailReview={decorate,setEnabled,expire};
})(globalThis);
