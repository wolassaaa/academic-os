/* Loading/auth/partial/offline states remain separate from task decisions. */
(function(root){
  'use strict';
  const body=document.body,main=document.querySelector('main.dashboard');
  const bar=document.createElement('section');bar.className='service-status';bar.setAttribute('aria-label','读取状态');
  const text=document.createElement('span');text.setAttribute('role','status');text.setAttribute('aria-live','polite');
  const refresh=document.createElement('button');refresh.type='button';refresh.className='button tonal';refresh.textContent='刷新';
  const login=document.createElement('a');login.className='button';login.href='/';login.textContent='前往登录';login.hidden=true;
  bar.append(text,refresh,login);document.querySelector('.workarea').before(bar);
  let current=null,busy=false;
  function completionControls(ready){for(const b of document.querySelectorAll('[data-focus]'))b.disabled=!ready||b.dataset.allowed!=='true';for(const button of document.querySelectorAll('[data-completion],[data-reopen]'))button.disabled=!ready||button.dataset.allowed!=='true';}
  function el(tag,cls,value){const n=document.createElement(tag);if(cls)n.className=cls;if(value!=null)n.textContent=value;return n;}
  function date(value,s){const d=root.AcademicBoardModel.validDate(value);return d?new Intl.DateTimeFormat('zh-CN',{timeZone:s.timezone,month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(d):'尚无成功记录';}
  function sources(s){
    for(const container of document.querySelectorAll('.live-sources')){
      container.replaceChildren();
      if(!s.health){container.append(el('p','muted','来源状态暂未读到，事项仍可查看。'));continue;}
      if(!s.health.sources.length)container.append(el('p','muted','尚无来源同步记录。'));
      for(const source of s.health.sources){
        const row=el('div','connection-row'),copy=el('div','connection-main');
        const kind=String(source.id||'').split(':')[0],names={gmail:'邮件',google:'Google 日历',moodle:'学校平台',local:'本地来源'};
        copy.append(el('h3','',names[kind]||kind||'来源'),el('p','',source.id||''),el('p','','最近成功 · '+date(source.last_success,s)));
        const issue=source.status!=='ok',state=issue?'部分读取失败':source.stale?'数据可能过期':'正常';
        copy.append(el('p','','最近尝试 · '+(source.last_attempt?date(source.last_attempt,s):'未报告')));
        copy.append(el('p','',issue?'本次读取未完整成功，已保留上次取得的事项。':source.stale?'显示的是上次同步结果，请检查后台与连接。':'同步成功不代表来源完整性已经核实。'));
        const manage=el('a','button quiet','检查来源');manage.href='/?page=sources';
        row.append(copy,el('span','pill '+(issue||source.stale?'amber':''),state),manage);container.append(row);
      }
    }
    for(const container of document.querySelectorAll('.live-courses')){
      container.replaceChildren();const seen=new Set();
      for(const item of s.items){const key=root.AcademicBoardModel.courseKey(item);if(seen.has(key))continue;seen.add(key);
        container.append(el('div','setting-line',root.AcademicBoardModel.courseName(item)));}
      if(!seen.size)container.append(el('p','muted','尚无课程事项。'));
    }
    for(const n of document.querySelectorAll('.live-worker'))n.textContent=s.health?(s.health.worker_online?'后台运行中':'后台暂无新心跳'):'后台状态未知';
    for(const n of document.querySelectorAll('.live-timezone'))n.textContent=s.timezone;
  }
  function chrome(s){
    for(const n of document.querySelectorAll('.sync-status'))n.textContent=s.remote?'远程只读':s.remoteFull?'远程 · tailnet':'本地看板';
    for(const n of document.querySelectorAll('.live-username'))n.textContent=s.username||'我的空间';
    sources(s);
    for(const n of document.querySelectorAll('.live-capabilities'))n.textContent=s.canComplete?(s.canFocus?'读取 · 重点 · 完成':'读取 · 事项完成'):'只读';
  }
  async function load(){
    if(busy||root.AcademicServiceMutationBusy)return current;busy=true;refresh.disabled=true;login.hidden=true;
    body.dataset.serviceState=current?'refreshing':'loading';text.textContent=current?'正在刷新，保留已读事项…':'正在读取正式事项…';
    completionControls(false);
    try{
      let s=await root.AcademicServiceAdapter.load();
      if(!s.remote&&!s.focusPrefs&&current?.focusPrefs&&current.username===s.username)s={...s,focusPrefs:current.focusPrefs,focusUnavailable:true};
      current=s;
      if(root.AcademicBoardRefresh)root.AcademicBoardRefresh(s);
      else if(root.AcademicBoardStart&&!root.AcademicBoardBootPending)await root.AcademicBoardStart(s);
      chrome(s);main.hidden=false;body.dataset.serviceState='ready';
      const failed=s.health?.sources.filter(x=>x.status!=='ok').length||0;
      const stale=s.health?.sources.filter(x=>x.stale).length||0;
      const issue=s.healthUnavailable?'来源状态暂未读到':failed?failed+' 个来源读取不完整':s.health?.data_stale?'后台心跳过期，数据可能未更新':stale?stale+' 个来源数据过期':'';
      const readAt=new Intl.DateTimeFormat('zh-CN',{timeZone:s.timezone,hour:'2-digit',minute:'2-digit'}).format(new Date(s.now));
      text.textContent=(s.remote?'远程只读 · ':s.remoteFull?'远程 · ':'本地 · ')+s.items.length+' 项正式事项 · 看板读取 '+readAt+(issue?' · 来源状态需关注：'+issue:'')+(!s.remote&&(!s.focusPrefs||s.focusUnavailable)?' · 重点偏好未读到，保留已读选择或自动推荐':'');
      root.AcademicServiceView={state:'ready',count:s.items.length,remote:s.remote};return s;
    }catch(error){
      const auth=['auth','setup'].includes(error.code);
      if(auth){current=null;main.hidden=true;body.dataset.serviceState='expired';login.hidden=false;login.textContent=error.code==='setup'?'初始化工作台':'前往登录';
        root.AcademicBoardClear?.();document.querySelector('.detail-panel')?.replaceChildren();document.querySelectorAll('.live-sources,.live-courses').forEach(n=>n.replaceChildren());
        document.querySelectorAll('.live-username').forEach(n=>n.textContent='我的空间');
        document.querySelector('.completion-feedback')?.setAttribute('hidden','');document.querySelector('.focus-feedback')?.setAttribute('hidden','');
      }else body.dataset.serviceState=current?'stale':'error';
      const reason=auth?(error.code==='setup'?'先在工作台初始化账户。':'登录已过期，请重新登录。'):error.code==='offline'?'连接失败，请检查本地服务后重试。':error.code==='schema'?'数据格式不完整，请刷新重试。':'读取失败，请稍后重试。';
      text.textContent=reason+(current?' 已保留上次读取的事项，数据可能过期。':'');
      root.AcademicServiceView={state:body.dataset.serviceState,count:current?.items.length||0};
      throw error;
    }finally{busy=false;refresh.disabled=false;completionControls(body.dataset.serviceState==='ready');}
  }
  refresh.addEventListener('click',()=>load().catch(()=>{}));
  const initial=load();initial.catch(()=>{});root.AcademicBoardData={load:()=>initial};
  root.AcademicServiceRefresh=()=>load().catch(()=>null);
  root.AcademicServiceCurrent=()=>current;
  root.AcademicServiceApplyFocus=focusPrefs=>{if(!current)return;current={...current,focusPrefs};root.AcademicBoardRefresh?.(current);};
  root.AcademicServiceApplyReceipt=receipt=>{
    if(!current)return;
    current={...current,items:current.items.map(i=>i.id===receipt.id?{...i,...receipt}:i)};
    root.AcademicBoardRefresh?.(current);chrome(current);
  };
  root.AcademicServiceExpire=()=>{
    current=null;main.hidden=true;body.dataset.serviceState='expired';login.hidden=false;login.textContent='前往登录';
    text.textContent='登录已过期，请重新登录。';root.AcademicBoardClear?.();
    document.querySelector('.detail-panel')?.replaceChildren();
    document.querySelector('.completion-feedback')?.setAttribute('hidden','');document.querySelector('.focus-feedback')?.setAttribute('hidden','');
    document.querySelectorAll('.live-sources,.live-courses').forEach(n=>n.replaceChildren());
    document.querySelectorAll('.live-username').forEach(n=>n.textContent='我的空间');
    completionControls(false);
    root.AcademicServiceView={state:'expired',count:0};
  };
})(globalThis);
