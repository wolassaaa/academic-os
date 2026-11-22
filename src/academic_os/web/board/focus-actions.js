/* Personal focus is presentation state, never a change to deadlines or priority. */
(function(root){
  'use strict';
  let intent=null,busy=false;
  const panel=document.createElement('section');panel.className='focus-feedback';panel.hidden=true;
  const message=document.createElement('span');message.role='status';message.setAttribute('aria-live','polite');
  const retry=document.createElement('button');retry.className='button tonal';retry.type='button';retry.textContent='核实并重试';retry.hidden=true;
  const refresh=document.createElement('button');refresh.className='button';refresh.type='button';refresh.textContent='刷新后重新选择';refresh.hidden=true;
  refresh.onclick=()=>{intent=null;panel.hidden=true;root.AcademicServiceRefresh();};retry.onclick=()=>send();
  panel.append(message,retry,refresh);document.querySelector('.service-status').after(panel);
  function controls(){for(const b of document.querySelectorAll('[data-focus],[data-completion]'))b.disabled=busy||root.AcademicServiceView?.state!=='ready'||b.dataset.allowed!=='true';}
  async function send(){
    if(!intent||busy||root.AcademicServiceMutationBusy)return;
    if(root.AcademicServiceCurrent()?.username!==intent.username){intent=null;panel.hidden=false;message.textContent='账户已变化，请刷新后重新选择。';retry.hidden=true;refresh.hidden=false;return;}
    busy=true;root.AcademicServiceMutationBusy=true;controls();retry.disabled=refresh.disabled=true;panel.hidden=false;message.textContent='正在保存重点选择…';
    let receipt=null;
    try{
      receipt=await root.AcademicServiceAdapter.setFocus(intent.id,intent.mode,intent.revision,intent.key);
      const mode=intent.mode;intent=null;retry.hidden=refresh.hidden=true;
      root.AcademicServiceApplyFocus(receipt);
      message.textContent=mode==='pool'?'已移回待办池，截止和安排不变。':mode==='focus'?'已设为我的重点。':'已恢复自动推荐。';
      root.AcademicServiceMutationBusy=false;
      if(!await root.AcademicServiceRefresh())message.textContent+=' 列表刷新失败，选择已保存。';
    }catch(e){
      if(receipt){intent=null;message.textContent='选择已保存，界面更新未完成，请刷新核对。';refresh.hidden=false;}
      else if(e.code==='auth'){intent=null;root.AcademicServiceExpire();}
      else if(e.code==='uncertain'){message.textContent='结果暂未确认；请核实并重试，同一请求不会重复保存。';retry.hidden=false;refresh.hidden=false;}
      else{intent=null;retry.hidden=true;refresh.hidden=false;message.textContent=e.code==='conflict'?'重点选择已在其他页面更新，请刷新后重新选择。':'选择未保存，请刷新核对权限或事项状态。';}
    }finally{busy=false;root.AcademicServiceMutationBusy=false;retry.disabled=refresh.disabled=false;controls();}
  }
  root.AcademicFocus={set(id,mode){
    if(busy||root.AcademicServiceMutationBusy)return;
    if(intent){panel.hidden=false;message.textContent='请先核实上一次未确认的选择，或刷新后重新选择。';return;}
    const current=root.AcademicServiceCurrent();
    if(!current?.canFocus||root.AcademicServiceView?.state!=='ready')return;
    intent={id,mode,username:current.username,revision:current.focusPrefs.revision,key:crypto.randomUUID()};send();
  }};
})(globalThis);
