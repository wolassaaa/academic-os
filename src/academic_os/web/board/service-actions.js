/* An explicit owner decision, never optimistic success or an automatic retry. */
(function(root){
  'use strict';
  const intents=new Map();let pending=null,sending=false,trigger=null;
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text)n.textContent=text;return n;};
  const action=(text,cls,fn)=>{const b=el('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;};
  const dialog=el('dialog','completion-dialog');dialog.setAttribute('aria-labelledby','completion-title');dialog.setAttribute('aria-describedby','completion-description');
  const title=el('h2','','标记为完成？');title.id='completion-title';
  const task=el('p','completion-task'),description=el('p','completion-description');description.id='completion-description';
  const error=el('p','completion-error');error.setAttribute('role','alert');error.hidden=true;
  const cancel=action('取消','button',()=>dialog.close());
  const reload=action('刷新后重新核对','button',async()=>{dialog.close();await root.AcademicServiceRefresh();});reload.hidden=true;
  const confirm=action('确认完成','button primary',send);
  const buttons=el('div','completion-buttons');buttons.append(cancel,reload,confirm);dialog.append(title,task,description,error,buttons);document.body.append(dialog);
  const feedback=el('section','completion-feedback');feedback.hidden=true;feedback.setAttribute('aria-label','完成结果');
  const message=el('span');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
  const detail=el('a','button quiet','查看已完成事项'),dismiss=action('关闭','button quiet',()=>{feedback.hidden=true;});
  feedback.append(message,detail,dismiss);document.querySelector('.service-status').after(feedback);
  function announce(text,item){
    message.textContent=text;feedback.hidden=false;detail.hidden=!item;
    if(item){detail.textContent=item.status==='pending'?'查看待完成事项':'查看已完成事项';const url=new URL('task.html',location.href);url.search=location.search;url.searchParams.set('item',item.id);detail.href=url.pathname+url.search;}
  }
  function request(item,button,kind='complete'){
    const current=root.AcademicServiceCurrent?.();
    const isClosed=['completed','submitted','cancelled'].includes(item.status);
    if(sending||!current?.canComplete||(kind==='reopen'?!isClosed:isClosed))return;
    if(root.AcademicServiceView?.state!=='ready'){announce('数据可能过期，请先刷新后核对。');return;}
    trigger=button;const intentId=item.id+':'+kind;pending=intents.get(intentId)||{item:{...item},key:crypto.randomUUID(),kind,intentId};
    title.textContent=kind==='reopen'?'重新打开这条事项？':'标记为完成？';
    task.textContent=pending.item.title;
    description.textContent=kind==='reopen'?'只将个人状态改回待完成，不会撤销学校的提交记录或改动截止时间。已有安排沿用现有同步规则，这里不代表日历已同步。':'只标记本事项完成，不会替你向学校提交作业。若存在系统学习日历事件，将沿用既有同步规则处理；这里不代表日历已同步。'+(item.review_state==='conflict'?' 日期冲突仍需单独核对。':'');
    error.hidden=true;reload.hidden=true;confirm.disabled=false;confirm.textContent=pending.uncertain?'核实并重试':kind==='reopen'?'确认重新打开':'确认完成';
    dialog.returnValue='';dialog.showModal();root.AcademicMotion?.panel(dialog);cancel.focus();
  }
  async function send(){
    if(!pending||sending)return;
    const intent=pending,label=intent.kind==='reopen'?'重新打开':'完成',expected=intent.kind==='reopen'?'pending':'completed';intents.set(intent.intentId,intent);sending=true;confirm.disabled=cancel.disabled=reload.disabled=true;confirm.textContent='正在确认…';error.hidden=true;
    root.AcademicServiceMutationBusy=true;
    let receipt=null;
    try{
      receipt=await root.AcademicServiceAdapter[intent.kind](intent.item,intent.key);
      intents.delete(intent.intentId);dialog.close('confirmed');
      root.AcademicServiceApplyReceipt(receipt);
      announce(label+'已确认，正在更新列表。',receipt);
      root.AcademicServiceMutationBusy=false;
      const fresh=await root.AcademicServiceRefresh();
      const current=fresh?.items.find(i=>i.id===receipt.id);
      announce(!fresh?label+'已确认；列表刷新失败，可重试刷新。':current&&current.status!==expected?'本次'+label+'已确认，但事项随后又被更新，请查看当前状态。':intent.kind==='reopen'?'已重新打开。来源提交事实、原截止与历史保留。':'已完成。原截止、事项关联与历史保留。',receipt);
      if(!document.querySelector('.detail-panel'))detail.focus({preventScroll:true});
    }catch(e){
      if(receipt){dialog.close('confirmed');announce('服务已确认'+label+'，界面更新未完成，请刷新核对。',receipt);return;}
      error.hidden=false;reload.hidden=false;
      if(e.code==='uncertain'){
        intent.uncertain=true;confirm.textContent='核实并重试';
        error.textContent='结果暂未确认。可使用同一请求核实并重试，不会重复执行。';
      }else{
        intent.uncertain=false;
        intents.delete(intent.intentId);confirm.disabled=true;confirm.textContent=intent.kind==='reopen'?'确认重新打开':'确认完成';
        error.textContent=e.code==='conflict'?'事项已被其他操作更新。请刷新并重新核对，不会自动覆盖。':e.code==='readonly'?'当前会话没有'+label+'权限，请在本地工作台核对。':e.code==='auth'?'登录已过期，请重新登录后核对。':'服务未接受本次操作，请刷新后核对。';
        if(e.code==='auth'){dialog.close();root.AcademicServiceExpire();}
      }
    }finally{
      sending=false;root.AcademicServiceMutationBusy=false;cancel.disabled=reload.disabled=false;
      if(pending===intent&&intent.uncertain)confirm.disabled=false;
    }
  }
  dialog.addEventListener('cancel',event=>{if(sending)event.preventDefault();});
  dialog.addEventListener('close',()=>{if(dialog.returnValue!=='confirmed')trigger?.isConnected&&trigger.focus({preventScroll:true});pending=null;});
  root.AcademicCompletion={request,reopen:(item,button)=>request(item,button,'reopen'),get busy(){return sending;}};
})(globalThis);
