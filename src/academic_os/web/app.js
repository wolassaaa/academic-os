'use strict';

const state = {session: null, page: 'config', data: {}, offline: false, lastUpdated: null, loading: false, filter: '', status: 'active'};
const app = document.getElementById('app');
const dialog = document.getElementById('dialog');
const closedStatuses = new Set(['completed', 'submitted', 'cancelled']);
const names = {pending:'待开始',in_progress:'进行中',submitted:'已提交',completed:'已完成',cancelled:'已取消',unknown:'待确认',ready:'已核对',needs_date:'日期待确认',needs_analysis:'待人工分析',conflict:'存在冲突',draft:'待确认',confirmed:'已确认',queued:'排队中',running:'执行中',failed:'失败',delivered:'已送达',acknowledged:'已收到',snoozed:'稍后提醒',preview:'影子预览',sending:'投递中',ok:'正常',error:'异常',task:'任务',event:'活动',exam:'考试',assignment:'作业',quiz:'测验',workbench:'工作台',email:'邮件',feishu:'飞书',missing_estimate:'缺少用时估计',needs_review:'需要人工核对',insufficient_time:'可用时间不足',source_date:'来源日期冲突',calendar_edit:'日历编辑冲突',calendar_write:'日历写入冲突'};
const navs = [['config','⚙','账号与同步','CONNECTIONS'],['items','▤','作业与截止','ASSIGNMENTS'],['sources','⇄','运行记录','OPERATIONS'],['overview','◈','总览','OVERVIEW'],['mail','✉','邮件收件箱','MAIL INBOX'],['plans','▦','计划草稿','PLAN DRAFTS'],['notices','♧','提醒中心','NOTIFICATIONS']];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'text') node.textContent = value;
    else if (value !== false && value != null) {
      if (['value','checked','disabled','selected','required'].includes(key)) node[key] = value;
      else node.setAttribute(key, String(value));
    }
  }
  for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
const label = value => names[value] || value || '—';
const btn = (text, action, cls = '', disabled = false) => el('button',{type:'button',class:cls,onclick:action,disabled},text);
const badge = (text, tone = '') => el('span',{class:'badge '+tone},text);
const empty = (title, detail = '') => el('div',{class:'empty'},el('div',{class:'empty-symbol','aria-hidden':'true'},'◇'),el('strong',{},title),detail);
function time(value, options = {}) {
  if (!value) return '尚无记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN',{timeZone:state.data.config?.timezone || undefined,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,...options});
}
function day(value) { return new Intl.DateTimeFormat('en-CA',{timeZone:state.data.config?.timezone || undefined,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value)); }
function localInput(value) {
  if (!value) return '';
  const date = new Date(value); return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
}
function isoInput(value) { return value ? new Date(value).toISOString() : null; }
function toast(text, error = false) {
  const node = el('div',{class:'toast'+(error?' error':''),role:error?'alert':'status'},text);
  const messages=document.getElementById('messages');
  while(messages.children.length>=2) messages.firstElementChild.remove();
  messages.append(node); setTimeout(()=>node.remove(),error?12000:6000);
}
const errors = {item_version_changed:'事项已被其他操作更新。请刷新并重新核对。',draft_already_confirmed:'草稿已确认，请刷新列表。',schedule_after_deadline:'安排结束时间晚于来源截止时间，请调整草稿。',item_already_closed:'事项已完成或关闭，不能确认此安排。','Login required':'登录已过期，请重新登录。','Invalid credentials':'用户名或密码不正确。','Too many attempts; try again in 15 minutes':'登录尝试过多，请在 15 分钟后重试。',already_initialized:'账号已初始化，请使用已有账号登录。',not_found:'记录不存在，请刷新后重试。'};
async function api(path, method = 'GET', body, keyed = false) {
  const headers = {};
  if (method !== 'GET') { headers['Content-Type']='application/json'; if(state.session?.csrf_token) headers['X-CSRF-Token']=state.session.csrf_token; if(keyed) headers['Idempotency-Key']=crypto.randomUUID(); }
  let response;
  try { response = await fetch('/api/v1'+path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)}); }
  catch { throw new Error('连接失败。请检查本地服务和网络，再点击刷新。'); }
  const result = await response.json().catch(()=>({}));
  if (!response.ok) {
    if(response.status===401 && path!=='/login') { state.session={...state.session,authenticated:false}; dialog.close(); loginScreen(); }
    const detail=result.error || result.detail;
    const text = typeof detail === 'string' ? (errors[detail]||detail) : Array.isArray(detail) ? detail.map(x=>`${x.loc?.slice(1).join('.')}: ${x.msg}`).join('；') : '请求失败，请稍后重试。';
    throw new Error(text);
  }
  return result;
}
async function refresh(silent = false, force = false) {
  if(state.loading || !state.session?.authenticated) return;
  state.loading=true; updateConnection();
  try {
    const dashboard = await api('/dashboard');
    let data={dashboard};
    if(!state.session.remote) {
      const endpoints=['items','drafts','notifications','artifacts','jobs','config','conflicts','calendar-events','connections'];
      const values=await Promise.all(endpoints.map(x=>api('/'+x+(x==='items'?'?view=formal':''))));
      endpoints.forEach((key,index)=>data[key]=values[index]);
      const missing=[...new Set(data.conflicts.map(c=>c.item_id))].filter(id=>!data.items.some(i=>i.id===id));
      data.conflictItems=await Promise.all(missing.map(id=>api('/items/'+encodeURIComponent(id))));
    }
    state.data=data; state.offline=false; state.lastUpdated=new Date();
    if(force || (!dialog.open && !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName))) renderPage();
  } catch(error) {
    state.offline=true;
    if(state.session.authenticated) { renderPage(); if(!silent) toast(error.message,true); }
  } finally { state.loading=false; updateConnection(); }
}
async function mutate(path, method, body, keyed, message) {
  if(state.offline) throw new Error('当前离线，恢复连接后才能保存。');
  const result=await api(path,method,body,keyed);
  while(state.loading) await new Promise(resolve=>setTimeout(resolve,50));
  await refresh(true, true);
  if(message) toast(message);
  return result;
}
function brand() { return el('div',{class:'brand'},el('div',{class:'mark','aria-hidden':'true'},'A'),el('div',{},el('div',{class:'brand-name'},'Academic OS'),el('div',{class:'brand-sub'},'YOUR ACADEMIC COMPASS'))); }
function shell() {
  const remote=state.session.remote;
  state.mailSearchRequest=null;state.mailPageData=null;state.mailError=null;state.mailLoading=false;
  if(!remote) state.mailFilters=readMailFilters();
  const nav=el('nav',{class:'nav','aria-label':'主导航'});
  for(const [id,icon,title] of navs.filter(n=>remote?n[0]==='overview':['config','items','mail','sources'].includes(n[0]))) nav.append(btn([el('span',{class:'nav-icon','aria-hidden':'true'},icon),title],()=>navigate(id),'nav-'+id));
  if(!remote) nav.append(el('details',{},el('summary',{},'高级核对工具'),navs.filter(n=>!['config','items','mail','sources'].includes(n[0])).map(([id,icon,title])=>btn([icon+' ',title],()=>navigate(id),'nav-'+id))));
  app.replaceChildren(el('div',{class:'shell'},el('aside',{class:'sidebar'},brand(),el('p',{class:'nav-label'},remote?'REMOTE DASHBOARD':'WORKSPACE'),nav,el('div',{class:'sidebar-foot'},el('strong',{},remote?'远程只读看板':'本地维护后台'),remote?'仅展示脱敏状态与时间安排':'账号授权、同步设置与异常核对。',btn('退出登录',logout,'quiet small'))),el('div',{class:'workspace'},el('header',{class:'topbar'},el('div',{class:'breadcrumb'},'工作台 / ',el('strong',{id:'breadcrumb'},'总览')),el('div',{class:'toolbar'},el('div',{id:'connection',class:'connection','aria-live':'polite'}),btn('↻ 刷新',()=>refresh(), 'small'),btn('退出',logout,'small'))),el('main',{id:'main',tabindex:'-1'}))));
  renderPage();
}
function navigate(page) { state.page=page; state.filter=''; renderPage(); document.getElementById('main')?.focus({preventScroll:true}); }
function updateConnection() {
  const node=document.getElementById('connection'); if(!node)return;
  node.className='connection'+(state.offline?' offline':'');
  node.replaceChildren(el('strong',{},state.loading?'正在刷新…':state.offline?'● 连接中断 · 数据可能过期':'● '+(state.session.remote?'远程只读':'本地连接')),state.lastUpdated?'数据时间 '+time(state.lastUpdated):'尚未取得数据');
}
function heading() {
  const nav=navs.find(n=>n[0]===state.page);
  const subtitles={overview:state.session.remote?'远程查看学业进展；修改请回到本地工作台。':'从来源到行动，为今天留出清晰的节奏。',items:'截止时间来自来源；学习安排通过草稿核对后确认。',mail:'以纯文本查看原始邮件与规则提取日期，人工核对后再采取行动。',plans:'确定性排程器根据优先级、截止时间和可用时间生成草稿。',notices:'确认收到、稍后提醒，或完成关联事项。',sources:'查看同步健康、日历方向与本地资料，追溯每一份输入。',config:'在这里登录账号、选择日历并检查后台运行。'};
  return el('div',{class:'page-heading'},el('div',{},el('div',{class:'eyebrow'},nav[3]),el('h1',{},state.page==='overview'?'把学业，安排得从容。':nav[2]),el('p',{class:'subtitle'},subtitles[state.page])),state.page==='overview'?el('div',{class:'date-label'},new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'})):null);
}
function renderPage() {
  if(!state.session?.authenticated)return;
  const main=document.getElementById('main'); if(!main)return;
  const nav=navs.find(n=>n[0]===state.page);
  document.getElementById('breadcrumb').textContent=nav[2];
  for(const [id] of navs) { const button=document.querySelector('.nav-'+id); if(button){button.classList.toggle('active',id===state.page);button.setAttribute('aria-current',id===state.page?'page':'false');} }
  main.replaceChildren(heading());
  if(state.offline)main.append(el('div',{class:'banner error',role:'alert'},'连接中断。显示的是 '+(state.lastUpdated?time(state.lastUpdated):'尚未加载')+' 的数据；写入操作已暂停。点击“刷新”恢复连接。'));
  if(!state.data.dashboard){main.append(empty('尚未取得工作台数据','服务连接恢复后，请点击刷新。'));updateConnection();return;}
  const renders={overview:overview,items:itemsPage,mail:mailPage,plans:plansPage,notices:noticesPage,sources:sourcesPage,config:configPage};
  main.append(renders[state.page]());
  main.append(el('footer',{class:'page-foot'},el('span',{},'ACADEMIC OS  /  学业事务，由你掌握'),el('span',{},'模型分析未启用 · 每 30 秒刷新 · '+(state.data.config?.timezone||'浏览器本地时区'))));
  updateConnection();
}
function panel(title, subtitle, body, action) {return el('section',{class:'panel'},el('div',{class:'panel-head'},el('div',{},el('h2',{},title),subtitle?el('p',{class:'subtitle'},subtitle):null),action),body);}
function itemTitle(id) { return (state.data.items||state.data.dashboard.items).find(i=>i.id===id)?.title || id; }
function overview() {
  const d=state.data.dashboard,h=d.health,items=d.items,active=items.filter(i=>!closedStatuses.has(i.status));
  const today=active.filter(i=>i.scheduled_start&&day(i.scheduled_start)===day(new Date())).sort((a,b)=>a.scheduled_start.localeCompare(b.scheduled_start));
  const due=active.filter(i=>i.source_due_at).sort((a,b)=>a.source_due_at.localeCompare(b.source_due_at)).slice(0,6);
  const remote=state.session.remote;
  const root=el('div');
  root.append(el('div',{class:'banner'+(h.data_stale?' warn':'')},el('span',{class:'symbol','aria-hidden':'true'},h.data_stale?'◷':'◈'),el('div',{},el('strong',{},h.mode==='shadow'?'影子模式运行中':'实时模式运行中'),' · '+(h.mode==='shadow'?'外部日历写入与外部提醒仅作预览。':'已启用外部同步与投递。')+' 模型分析未启用；邮件日期由规则提取，请人工核对。',h.data_stale?el('div',{},'后台工作进程尚未在线或心跳过期，来源数据可能不是最新。'):null)));
  const dueWeek=active.filter(i=>i.source_due_at&&new Date(i.source_due_at)<=new Date(Date.now()+7*86400000)).length;
  const stats=[['待处理事项',d.counts.active,'已完成事项不计入','highlight'],['近期截止',dueWeek,'含逾期与未来 7 天',''],['待核对事项',d.counts.review,'日期、分析或冲突待处理',''],['今日安排',today.length,'已确认的学习时间块','']];
  root.append(el('div',{class:'stats'},stats.map(([name,value,foot,cls])=>el('div',{class:'stat '+cls},el('div',{class:'stat-label'},name,el('span',{'aria-hidden':'true'},'↗')),el('div',{class:'stat-number'},String(value).padStart(2,'0')),el('div',{class:'stat-foot'},foot)))));
  const todayBody=el('div',{class:'list'},today.length?today.map(i=>el('div',{class:'row'},el('div',{class:'timeline-clock'},time(i.scheduled_start,{month:undefined,day:undefined}),el('div',{class:'muted'},time(i.scheduled_end,{month:undefined,day:undefined}))),el('span',{class:'line-dot','aria-hidden':'true'}),el('div',{class:'row-main'},remote?el('div',{class:'row-title'},i.title):btn(i.title,()=>editItem(i.id),'text-button'),el('div',{class:'row-meta'},i.course||label(i.kind))),badge(label(i.status)))):empty('今天还没有学习安排',remote?'在本地工作台生成并确认计划后，会显示在这里。':'填写事项用时，生成草稿并确认后，今天的安排会显示在这里。'));
  const dueBody=el('div',{class:'list'},due.length?due.map(i=>el('div',{class:'row'},el('span',{class:'line-dot','aria-hidden':'true'}),el('div',{class:'row-main'},remote?el('div',{class:'row-title'},i.title):btn(i.title,()=>editItem(i.id),'text-button'),el('div',{class:'row-meta'},i.course||label(i.kind))),el('div',{class:'row-side'},el('strong',{},time(i.source_due_at)),badge(new Date(i.source_due_at)<new Date()?'已逾期':label(i.review_state),new Date(i.source_due_at)<new Date()?'error':i.review_state==='ready'?'neutral':'warn')))):empty('暂无有截止日期的事项','同步来源或手动补充事项后，按截止时间显示。'));
  const healthBody=el('div',{class:'list'},el('div',{class:'row'},el('div',{class:'row-main'},el('div',{class:'row-title'},'后台工作进程'),el('div',{class:'row-meta'},'最近心跳 '+time(h.heartbeat))),badge(h.worker_online?'在线':'离线',h.worker_online?'':'warn')),h.sources.length?h.sources.slice(0,4).map(s=>el('div',{class:'row'},el('div',{class:'row-main'},el('div',{class:'row-title'},s.id),el('div',{class:'row-meta'},'成功同步 '+time(s.last_success))),badge(label(s.status),s.status==='ok'?'':'error'))):empty('尚无同步记录','配置来源并启动工作进程后显示。'));
  const next=el('div',{class:'panel-body'},el('p',{class:'subtitle'},d.conflicts.length?`${d.conflicts.length} 项冲突等待人工核对。`:d.counts.review?`${d.counts.review} 个事项需要核对日期或内容。`:'待核对事项会集中显示在这里。'),!remote?el('div',{class:'actions section-gap'},btn('查看事项',()=>navigate('items'),'small'),btn('生成学习计划',()=>navigate('plans'),'primary small')):null);
  root.append(el('div',{class:'grid'},el('div',{class:'stack'},panel('今日学习安排','已确认的安排 · 来源截止保持独立',todayBody,!remote?btn('计划草稿 ↗',()=>navigate('plans'),'quiet small'):null),panel('接下来要关注','按来源截止时间排序',dueBody)),el('div',{class:'stack'},panel('系统与来源健康','最新状态以工作进程心跳为准',healthBody),panel('留给你的核对',null,next))));
  return root;
}
function inputField(text, type, value, attrs={}) {const input=el('input',{type,value:value??'',...attrs});return {input,node:el('label',{class:'field'},text,input)};}
function selectField(text, options, value, attrs={}) {const input=el('select',attrs,options.map(([v,t])=>el('option',{value:v,selected:v===value},t)));return {input,node:el('label',{class:'field'},text,input)};}
function openDialog(title, content) { dialog.replaceChildren(el('div',{class:'dialog-head'},el('h2',{id:'dialog-title'},title),btn('✕',()=>dialog.close(),'quiet small')),el('div',{class:'dialog-content'},content));dialog.showModal(); }
function formError(form,error) {let node=form.querySelector('.dialog-error');if(!node){node=el('div',{class:'dialog-error',role:'alert'});form.append(node);}node.textContent=error.message;}
function itemsPage() {
  const root=el('div'),search=el('input',{type:'search',placeholder:'搜索标题或课程','aria-label':'搜索事项',value:state.filter,oninput:e=>{state.filter=e.target.value;renderItems();}});
  const select=el('select',{'aria-label':'事项状态筛选',onchange:e=>{state.status=e.target.value;renderItems();}},[['active','进行中的事项'],['all','全部事项'],...['pending','in_progress','submitted','completed','cancelled','unknown'].map(x=>[x,label(x)])].map(([v,t])=>el('option',{value:v,selected:state.status===v},t)));
  root.append(el('div',{class:'filterbar'},el('div',{class:'filters'},search,select),btn('＋ 新建事项',()=>editItem(),'primary',state.offline)),el('section',{class:'panel',id:'item-table'}));
  queueMicrotask(renderItems);return root;
}
function renderItems() {
  const container=document.getElementById('item-table');if(!container)return;
  const items=state.data.items.filter(i=>(state.status==='all'||state.status==='active'&&!closedStatuses.has(i.status)||i.status===state.status)&&(!state.filter||`${i.title} ${i.course}`.toLocaleLowerCase().includes(state.filter.toLocaleLowerCase())));
  if(!items.length){container.replaceChildren(empty(state.filter?'没有匹配的事项':'暂无事项','你可以新建事项，或在“来源与日历”中请求同步。'));return;}
  const table=el('table',{},el('thead',{},el('tr',{},['事项 / 课程','来源截止','学习安排','用时 / 优先级','状态','操作'].map(t=>el('th',{scope:'col'},t)))),el('tbody',{},items.map(i=>el('tr',{},el('td',{class:'title-cell'},btn(i.title,()=>editItem(i.id),'text-button'),el('div',{class:'row-meta'},i.course||label(i.kind),' · ',badge(label(i.review_state),i.review_state==='ready'?'neutral':'warn'),isMailTodo(i)?badge('邮件待办'):null)),el('td',{},i.source_due_at?time(i.source_due_at):'待公布'),el('td',{},i.scheduled_start?time(i.scheduled_start):'尚未安排',i.calendar_deleted?el('div',{class:'row-meta'},'日历中已删除'):null),el('td',{},i.estimate_minutes?`${i.estimate_minutes} 分钟`:'待估计',el('div',{class:'row-meta'},'优先级 '+i.priority+' / 5')),el('td',{},badge(label(i.status),closedStatuses.has(i.status)?'neutral':'')),el('td',{},el('div',{class:'actions'},...mailLinks(i),btn('编辑',()=>editItem(i.id),'small',state.offline),btn('安排',()=>scheduleItem(i.id),'small',state.offline||closedStatuses.has(i.status))))))));
  container.replaceChildren(el('div',{class:'table-wrap'},table));
}
function editItem(id) {
  const item=id?state.data.items.find(i=>i.id===id):null;if(id&&!item)return;
  const title=inputField('事项标题','text',item?.title,{required:true,maxlength:500});
  const course=inputField('课程','text',item?.course,{maxlength:300,disabled:!!item});
  const kind=selectField('类型',['task','assignment','quiz','exam','event'].map(v=>[v,label(v)]),item?.kind||'task',{disabled:!!item});
  const estimate=inputField('预计用时（分钟）','number',item?.estimate_minutes,{min:1,max:10080,step:1});
  const priority=selectField('优先级（5 最高）',[0,1,2,3,4,5].map(v=>[String(v),String(v)]),String(item?.priority??2));
  const due=inputField('来源截止 · 输入使用浏览器本地时区','datetime-local',localInput(item?.source_due_at));
  const status=selectField('事项状态',['pending','in_progress','submitted','completed','cancelled','unknown'].map(v=>[v,label(v)]),item?.status||'pending');
  const review=selectField('人工核对状态',['ready','needs_date','needs_analysis','conflict'].map(v=>[v,label(v)]),item?.review_state||'needs_date');
  const form=el('form',{},el('div',{class:'field-grid'},title.node,course.node,kind.node,estimate.node,priority.node,due.node,item?status.node:null,item?review.node:null),el('p',{class:'subtitle section-gap'},'保存截止时间不会移动学习安排。安排时间需要创建草稿并单独确认。'),el('div',{class:'form-actions'},btn('取消',()=>dialog.close()),el('button',{type:'submit',class:'primary',disabled:state.offline},'保存事项')));
  form.addEventListener('submit',async e=>{e.preventDefault();const save=form.querySelector('[type=submit]');save.disabled=true;try{
    if(item)await mutate('/items/'+id,'PATCH',{expected_version:item.version,changes:{title:title.input.value.trim(),estimate_minutes:estimate.input.value?Number(estimate.input.value):null,priority:Number(priority.input.value),source_due_at:isoInput(due.input.value),status:status.input.value,review_state:review.input.value}},false,'事项已保存');
    else await mutate('/items','POST',{title:title.input.value.trim(),course:course.input.value.trim(),kind:kind.input.value,estimate_minutes:estimate.input.value?Number(estimate.input.value):null,priority:Number(priority.input.value),due_at:isoInput(due.input.value)},true,'事项已创建');
    dialog.close();
  }catch(error){formError(form,error);}finally{save.disabled=state.offline;}});
  if(item?.evidence?.length)form.append(el('details',{class:'section-gap'},el('summary',{},'查看来源证据（'+item.evidence.length+'）'),el('pre',{class:'code'},JSON.stringify(item.evidence,null,2))));
  openDialog(item?'编辑事项':'新建学业事项',form);
}
function scheduleItem(id) {
  const item=state.data.items.find(i=>i.id===id);
  const start=inputField('开始时间 · 浏览器本地时区','datetime-local',localInput(item.scheduled_start),{required:true});
  const end=inputField('结束时间 · 浏览器本地时区','datetime-local',localInput(item.scheduled_end),{required:true});
  const form=el('form',{},el('p',{class:'subtitle'},item.title+' · 来源截止 '+(item.source_due_at?time(item.source_due_at):'待公布')),el('div',{class:'field-grid section-gap'},start.node,end.node),el('p',{class:'subtitle section-gap'},'这会创建新的安排草稿。请在“计划草稿”核对后确认生效。'),el('div',{class:'form-actions'},btn('取消',()=>dialog.close()),el('button',{type:'submit',class:'primary'},'创建安排草稿')));
  form.addEventListener('submit',async e=>{e.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{const a=isoInput(start.input.value),b=isoInput(end.input.value);if(b<=a)throw new Error('结束时间必须晚于开始时间。');await mutate('/drafts','POST',{changes:[{item_id:id,expected_version:item.version,start:a,end:b}]},true,'安排草稿已创建，请核对后确认');dialog.close();navigate('plans');}catch(error){formError(form,error);}finally{button.disabled=false;}});
  openDialog('调整学习安排',form);
}
const defaultMailFilters=()=>({q:'',account:'',review:'all',date_mode:'all',start:'',end:'',offset:0,limit:50});
function mailStorageKey() { return 'academic-os:mail-filters:'+state.session.username; }
function readMailFilters() {
  const filters=defaultMailFilters();
  try {
    const saved=JSON.parse(sessionStorage.getItem(mailStorageKey())||'{}');
    for(const key of ['q','account','start','end']) if(typeof saved[key]==='string')filters[key]=saved[key];
    if(['all','today','next7','custom','undated'].includes(saved.date_mode))filters.date_mode=saved.date_mode;
    if(['all','pending','ignored'].includes(saved.review))filters.review=saved.review;
    if(Number.isSafeInteger(saved.offset)&&saved.offset>=0)filters.offset=saved.offset;
  } catch { /* Storage may be disabled; in-memory filters still work. */ }
  return filters;
}
function saveMailFilters() {
  try { sessionStorage.setItem(mailStorageKey(),JSON.stringify(state.mailFilters)); } catch { /* Optional persistence. */ }
}
function mailPage() {
  const f=state.mailFilters||defaultMailFilters();state.mailFilters=f;
  const root=el('div',{class:'stack'}),form=el('form',{class:'panel mail-filters'});
  const search=inputField('搜索全部邮件','search',f.q,{'aria-label':'搜索邮件',placeholder:'主题、发件人或正文关键词',maxlength:500});
  const account=selectField('账号',[['','全部账号']],f.account,{'aria-label':'邮件账号',id:'mail-account'});
  const review=selectField('候选核对',[['all','全部'],['pending','待核对'],['ignored','已忽略']],f.review,{'aria-label':'邮件核对状态'});
  const modes=[['all','全部日期'],['today','今天'],['next7','未来七天'],['custom','自定义范围'],['undated','无日期']];
  const mode=selectField('收件日期',modes,f.date_mode,{'aria-label':'邮件收件日期'});
  const start=inputField('开始日期','date',f.start,{'aria-label':'邮件开始日期'});
  const end=inputField('结束日期（含当天）','date',f.end,{'aria-label':'邮件结束日期'});
  const dates=el('div',{class:'field-grid',hidden:f.date_mode!=='custom'},start.node,end.node);
  const toggleDates=()=>{dates.hidden=mode.input.value!=='custom';start.input.required=end.input.required=!dates.hidden;};
  mode.input.addEventListener('change',toggleDates);toggleDates();
  form.append(el('div',{class:'field-grid'},search.node,account.node,review.node,mode.node),dates,el('div',{class:'form-actions'},el('button',{type:'submit',class:'primary'},'查询邮件'),btn('清空筛选',()=>{state.mailFilters=defaultMailFilters();saveMailFilters();state.mailPageData=null;renderPage();})),el('p',{class:'subtitle'},'未确认候选不产生截止提醒 · 新邮件到达通知保留 · 正文按需加载'));
  form.addEventListener('submit',event=>{
    event.preventDefault();
    if(mode.input.value==='custom'&&end.input.value<start.input.value){formError(form,new Error('结束日期必须不早于开始日期。'));return;}
    form.querySelector('.dialog-error')?.remove();
    state.mailFilters={q:search.input.value.trim(),account:account.input.value,review:review.input.value,date_mode:mode.input.value,start:start.input.value,end:end.input.value,offset:0,limit:50};
    saveMailFilters();state.mailPageData=null;loadMail();
  });
  root.append(form,el('section',{id:'mail-results','aria-live':'polite','aria-label':'邮件查询结果'}));
  queueMicrotask(()=>{renderMailResults();loadMail();});return root;
}
async function loadMail() {
  if(state.session?.remote||!state.session?.authenticated)return;
  const request=Symbol();state.mailSearchRequest=request;
  state.mailLoading=true;state.mailError=null;renderMailResults();
  try {
    if(state.offline)throw new Error('当前离线，请先刷新连接，再查询邮件。');
    const f=state.mailFilters,params=new URLSearchParams();
    for(const [key,value] of Object.entries(f))if(value!==''&&(f.date_mode==='custom'||!['start','end'].includes(key)))params.set(key,String(value));
    const result=await api('/mail/search?'+params);
    if(state.mailSearchRequest!==request)return;
    if(f.offset>=result.total&&f.offset>0){f.offset=Math.max(0,Math.ceil(result.total/f.limit)-1)*f.limit;saveMailFilters();return loadMail();}
    state.mailPageData=result;
  } catch(error) {
    if(state.mailSearchRequest!==request)return;
    state.mailError=error.message;
  } finally {
    if(state.mailSearchRequest===request){state.mailLoading=false;renderMailResults();}
  }
}
function renderMailResults() {
  const root=document.getElementById('mail-results');if(!root||state.page!=='mail'||!state.session?.authenticated)return;
  if(state.mailLoading){root.replaceChildren(empty('正在查询邮件…'));return;}
  if(state.mailError){root.replaceChildren(el('div',{class:'banner error',role:'alert'},state.mailError,btn('重试查询',()=>loadMail(),'small')));return;}
  const data=state.mailPageData;if(!data){root.replaceChildren(empty('正在准备查询…'));return;}
  const f=state.mailFilters,account=document.getElementById('mail-account');
  if(account){const value=account.value||f.account,accounts=[...new Set([...data.accounts,...(value?[value]:[])])];account.replaceChildren(el('option',{value:'',selected:!value},'全部账号'),...accounts.map(id=>el('option',{value:id,selected:id===value},id)));}
  const modes={all:'全部日期',today:'今天',next7:'未来七天',custom:f.start+' 至 '+f.end,undated:'无日期'};
  const summary=[f.account||'全部账号',{all:'全部候选',pending:'待核对',ignored:'已忽略'}[f.review],modes[f.date_mode],...(f.q?['关键词：'+f.q]:[])].join(' · ');
  const changePage=offset=>{f.offset=offset;saveMailFilters();loadMail();};
  root.replaceChildren(el('div',{class:'filterbar'},el('div',{},el('strong',{},`共 ${data.total} 封 · ${data.total?data.offset+1:0}–${Math.min(data.offset+data.limit,data.total)}`),el('p',{class:'subtitle'},summary+' · '+data.timezone)),el('div',{class:'actions'},btn('上一页',()=>changePage(Math.max(0,f.offset-f.limit)),'small',f.offset===0),btn('下一页',()=>changePage(f.offset+f.limit),'small',f.offset+f.limit>=data.total))),el('div',{class:'mail-layout'},data.items.length?data.items.map(m=>el('article',{class:'mail-card'},el('div',{class:'mail-head'},el('div',{},btn(m.subject||'(无主题)',()=>openMail(m),'text-button'),el('div',{class:'row-meta'},m.from+' · '+m.account+' · '+(m.received_at?time(m.received_at):'无收件日期'))),badge('✉ 邮件','neutral')),el('div',{class:'actions section-gap'},btn('查看原文与日期',()=>openMail(m),'small'),badge((m.date_nodes?.length||0)+' 个日期节点','neutral'),...Object.entries(m.review_counts||{}).filter(([,n])=>n>0).map(([key,n])=>badge({pending:'待核对',confirmed:'邮件待办',ignored:'已忽略'}[key]+' '+n+' 项',key==='pending'?'warn':'neutral'))))):empty('没有匹配的邮件','调整筛选条件或清空筛选；查询覆盖全部已采集邮件。')));
}
function isMailTodo(item) {
  return !item.manual_origin&&item.mail_review?.state==='confirmed'&&item.evidence?.length&&item.evidence.every(e=>e.source.startsWith('gmail:'));
}
function mailLinks(item) {
  return (item.mail_keys||[]).map((key,index)=>btn(index?'查看关联邮件 '+(index+1):'查看关联邮件',()=>openMail({key,subject:item.title}),'small',state.offline));
}
async function openMail(mail) {
  const content=el('div',{},empty('正在加载邮件原文…','正文按需读取，不包含在定期刷新中。'));
  openDialog(mail.subject||'(无主题)',content);
  const request=Symbol();state.mailRequest=request;
  const current=()=>state.mailRequest===request&&dialog.open&&content.isConnected;
  const load=async()=>{
    try {
      if(state.offline)throw new Error('当前离线，请刷新连接后重试。');
      const detail=await api('/mail/'+encodeURIComponent(mail.key));
      if(!current())return;
      content.replaceChildren(el('p',{class:'subtitle'},detail.from+' · '+detail.account+' · '+time(detail.received_at)),el('div',{class:'banner warn section-gap'},'逐条核对日期证据。未知截止时间请留空；确认前不产生截止提醒。模型分析未启用。'),el('div',{class:'mail-body'},detail.body||'（邮件没有正文）'),el('h3',{class:'section-gap'},'关联候选与邮件待办'),el('div',{class:'stack section-gap'},detail.linked_items?.length?detail.linked_items.map(item=>candidateCard(item,load,current)):empty('没有关联候选','原邮件保留；未提取到明确事项时，不补造任务或截止时刻。')),el('details',{class:'section-gap'},el('summary',{},el('h3',{},'提取的日期节点')),detail.date_nodes?.length?el('pre',{class:'code'},JSON.stringify(detail.date_nodes,null,2)):empty('未提取到明确日期','未知截止时间可以留空。')));
    } catch(error) {
      if(!current())return;
      content.replaceChildren(el('div',{class:'dialog-error',role:'alert'},error.message),el('div',{class:'form-actions'},btn('重试加载原文',load,'primary')));
    }
  };
  await load();
}
function candidateCard(item,reload,current) {
  const pure=!item.manual_origin&&item.evidence?.every(e=>e.source.startsWith('gmail:'));
  const status=pure?item.mail_review?.state||'confirmed':'confirmed';
  const card=el('form',{class:'mail-candidate'},el('div',{class:'actions'},el('strong',{},item.title),badge({pending:'待核对',confirmed:pure?'邮件待办':'正式事项',ignored:'已忽略'}[status],status==='pending'?'warn':'neutral')),el('p',{class:'subtitle'},'事项版本 '+item.version+' · 核对版本 '+(item.mail_review?.revision||0)),el('details',{open:true},el('summary',{},'日期与来源证据'),el('pre',{class:'code'},JSON.stringify(item.evidence,null,2))));
  const actions=el('div',{class:'form-actions'});
  let title,due,estimate,priority;
  if(status==='pending') {
    title=inputField('候选标题','text',item.title,{required:true,maxlength:500});
    due=inputField('核对截止时间 · 浏览器本地时区（未知留空）','datetime-local',localInput(item.source_due_at));
    estimate=inputField('预计用时（分钟）','number',item.estimate_minutes,{min:1,max:10080,step:1});
    priority=selectField('优先级（5 最高）',[0,1,2,3,4,5].map(v=>[String(v),String(v)]),String(item.priority));
    card.append(el('div',{class:'field-grid section-gap'},title.node,due.node,estimate.node,priority.node));
    actions.append(el('button',{type:'submit',class:'primary',disabled:state.offline},'确认成待办'),btn('忽略候选',()=>save('ignore'),'small',state.offline));
  } else if(status==='ignored') actions.append(btn('恢复待核对',()=>save('restore'),'primary',state.offline));
  else actions.append(el('p',{class:'subtitle'},'截止：'+(item.source_due_at?time(item.source_due_at):'未知，未设置截止提醒')),btn('查看正式事项',()=>{dialog.close();navigate('items');editItem(item.id);},'primary',state.offline));
  card.append(actions);
  let busy=false;
  const save=async(action)=>{
    if(busy||!current())return;
    busy=true;card.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      const changes=action==='confirm'?{title:title.input.value.trim(),source_due_at:due.input.value===localInput(item.source_due_at)?item.source_due_at:isoInput(due.input.value),estimate_minutes:estimate.input.value?Number(estimate.input.value):null,priority:Number(priority.input.value)}:{};
      await mutate('/items/'+encodeURIComponent(item.id)+'/mail-review','POST',{action,expected_version:item.version,changes},true,{confirm:'已确认为邮件待办',ignore:'候选已忽略，原邮件保留',restore:'已恢复待核对'}[action]);
      if(current())await reload();
    } catch(error) {
      if(current()) {formError(card,error);if(!card.querySelector('.review-reload'))card.append(btn('刷新核对内容',reload,'review-reload small section-gap'));}
    } finally {busy=false;card.querySelectorAll('button').forEach(b=>b.disabled=state.offline);}
  };
  card.addEventListener('submit',event=>{event.preventDefault();save('confirm');});
  return card;
}
function plansPage() {
  const root=el('div'),days=el('select',{'aria-label':'计划范围'},[3,7,14,30].map(n=>el('option',{value:n,selected:n===7},'未来 '+n+' 天')));
  const generate=btn('生成计划草稿',async()=>{generate.disabled=true;try{const result=await mutate('/plans/generate','POST',{days:Number(days.value)},true);toast(result.changes.length?`已生成 ${result.changes.length} 项安排，请核对后确认。`:'没有生成新的安排。请检查用时、截止时间和可用时段。');if(result.unplaced?.length)openUnplaced(result.unplaced);}catch(error){toast(error.message,true);}finally{generate.disabled=state.offline;}},'primary',state.offline);
  root.append(el('div',{class:'filterbar'},el('div',{class:'filters'},days,generate),el('span',{class:'search-count'},'先生成 → 核对 / 调整 → 确认生效')));
  if(!state.data.drafts.length){root.append(panel('待确认的学习计划',null,empty('还没有计划草稿','事项需要预计用时。排程器避开课程和已同步日历，安排结束不晚于截止时间。')));return root;}
  for(const draft of state.data.drafts) {
    const form=el('form'),body=el('div',{class:'panel-body'}),fields=[];
    for(const change of draft.changes) {
      const start=inputField('开始 · 浏览器本地时区','datetime-local',localInput(change.start),{required:true,disabled:draft.state!=='draft'});
      const end=inputField('结束 · 浏览器本地时区','datetime-local',localInput(change.end),{required:true,disabled:draft.state!=='draft'});
      const current=state.data.items.find(i=>i.id===change.item_id);
      body.append(el('div',{class:'draft-change'},el('div',{class:'row-title'},itemTitle(change.item_id)),start.node,end.node,el('div',{class:'field'},'来源截止',el('span',{},current?.source_due_at?time(current.source_due_at):'待公布'),el('span',{},'草稿版本 '+change.expected_version+' · 当前版本 '+(current?.version||'—')))));
      fields.push({change,start:start.input,end:end.input});
    }
    form.append(body);
    if(draft.metadata?.unplaced?.length)body.append(btn('查看未安排事项（'+draft.metadata.unplaced.length+'）',()=>openUnplaced(draft.metadata.unplaced),'small section-gap'));
    if(draft.state==='draft') {
      const save=el('button',{type:'submit',disabled:state.offline},'将调整保存为新草稿');
      const confirm=btn('核对并确认',()=>confirmDraft(draft),'primary',state.offline);
      form.append(el('div',{class:'draft-footer'},el('span',{class:'subtitle'},'修改时间后请先保存为新草稿，再确认新草稿。'),el('div',{class:'actions'},save,confirm)));
      form.addEventListener('input',()=>{confirm.disabled=true;confirm.title='时间已修改，请先保存为新草稿';});
      form.addEventListener('submit',async e=>{e.preventDefault();save.disabled=true;try{const changes=fields.map(f=>({item_id:f.change.item_id,expected_version:f.change.expected_version,start:isoInput(f.start.value),end:isoInput(f.end.value)}));if(changes.some(c=>c.end<=c.start))throw new Error('每个安排的结束时间必须晚于开始时间。');await mutate('/drafts','POST',{changes},true,'调整已保存为新草稿，请核对顶部新草稿');}catch(error){formError(form,error);}finally{save.disabled=state.offline;}});
    }
    const section=panel('计划 · '+draft.changes.length+' 项安排','创建于 '+time(draft.created_at),form,badge(label(draft.state),draft.state==='draft'?'warn':'neutral'));section.classList.add('draft');root.append(section);
  }
  return root;
}
function openUnplaced(items) {openDialog('未能安排的事项',el('div',{},el('p',{class:'subtitle'},'以下事项没有写入安排，请补充信息或调整可用时间。'),el('div',{class:'list'},items.map(i=>el('div',{class:'row'},el('div',{class:'row-main'},el('div',{class:'row-title'},itemTitle(i.item_id)),el('div',{class:'row-meta'},label(i.reason)+(i.minutes?' · '+i.minutes+' 分钟':''))))))));}
function confirmDraft(draft) {
  const content=el('div',{},el('p',{class:'subtitle'},'确认后，以下安排将写入本地事项，并提交日历同步任务。'+(state.data.dashboard.health.mode==='shadow'?'当前为影子模式，外部写入仅预览。':'')),el('div',{class:'list'},draft.changes.map(c=>el('div',{class:'row'},el('div',{class:'row-main'},el('div',{class:'row-title'},itemTitle(c.item_id)),el('div',{class:'row-meta'},time(c.start)+' — '+time(c.end)))))));
  const button=btn('确认这 '+draft.changes.length+' 项安排',async()=>{button.disabled=true;try{await mutate('/drafts/'+draft.id+'/confirm','POST',undefined,true,'计划已确认，日历同步任务已入队');dialog.close();}catch(error){formError(content,error);}finally{button.disabled=state.offline;}},'primary',state.offline);
  content.append(el('div',{class:'form-actions'},btn('返回核对',()=>dialog.close()),button));openDialog('确认学习计划',content);
}
function noticesPage() {
  const notices=state.data.notifications;if(!notices.length)return panel('学业提醒',null,empty('目前没有提醒','后台进程根据来源截止时间生成提醒；未收到记录前不显示演示内容。'));
  return el('div',{class:'stack'},notices.map(n=>el('article',{class:'panel'},el('div',{class:'panel-body notice-card'},el('div',{class:'row-main'},el('div',{class:'actions'},badge(label(n.channel),'neutral'),badge(label(n.state),n.state==='failed'?'error':n.state==='preview'?'warn':'')),el('h3',{class:'section-gap'},n.document?.title||'学业提醒'),el('div',{class:'notice-body'},n.document?.body||''),el('div',{class:'row-meta'},time(n.created_at)+' · 投递尝试 '+n.attempts+(n.last_error?' · 错误：'+n.last_error:''))),!['completed','acknowledged'].includes(n.state)?el('div',{class:'actions'},btn('确认收到',()=>noticeAction(n,'acknowledge'),'small',state.offline),btn('稍后 1 小时',()=>noticeAction(n,'snooze'),'small',state.offline),btn(n.item_id?'完成关联事项':'完成提醒',()=>noticeAction(n,'complete'),'primary small',state.offline)):null))));
}
async function noticeAction(notice,action) {
  const run=async()=>{try{await mutate('/notifications/'+notice.id+'/'+action,'POST',{minutes:60},false,action==='complete'?'提醒与关联事项已完成':'提醒状态已更新');dialog.close();}catch(error){toast(error.message,true);}};
  if(action==='complete'&&notice.item_id){openDialog('完成关联事项',el('div',{},el('p',{class:'subtitle'},'将“'+itemTitle(notice.item_id)+'”标记为已完成，并结束其关联提醒。'),el('div',{class:'form-actions'},btn('取消',()=>dialog.close()),btn('完成事项',run,'primary'))));}else await run();
}
function sourcesPage() {
  const root=el('div'),h=state.data.dashboard.health;
  const syncButtons=el('div',{class:'actions'},[['sources_sync','同步来源'],['calendar_sync','同步日历'],['export','更新摘要导出']].map(([kind,title])=>{const button=btn(title,async()=>{button.disabled=true;try{await mutate('/sync','POST',{kind},true,'任务已入队；后台工作进程执行后显示结果');}catch(error){toast(error.message,true);}finally{button.disabled=state.offline;}},'small',state.offline);return button;}));
  root.append(el('div',{class:'filterbar'},el('span',{class:'subtitle'},'同步请求进入持久化队列；失败详情保留在下方。'),syncButtons));
  const sources=h.sources.length?el('div',{class:'list'},h.sources.map(s=>el('div',{class:'row'},el('div',{class:'row-main'},el('div',{class:'row-title'},s.id),el('div',{class:'row-meta'},'最近尝试 '+time(s.last_attempt)+' · 成功 '+time(s.last_success)),(s.error||s.last_error)?el('div',{class:'dialog-error'},s.error||s.last_error):null),badge(label(s.status),s.status==='ok'?'':'error')))):empty('尚无来源记录','使用 CLI 配置 Google、Moodle 与本地文件来源，然后请求同步。');
  const calendars=state.data.dashboard.calendars;
  const calendarBody=el('div',{class:'panel-body'},el('div',{class:'banner'},'Google / Apple 编辑通过 Google 日历回流。飞书日历是单向镜像，请在 Google、Apple 或本地工作台调整。'),calendars.length?el('div',{class:'list'},calendars.map(c=>el('div',{class:'row'},el('div',{class:'row-main'},el('div',{class:'row-title'},c.name||c.calendar_id),el('div',{class:'row-meta'},c.provider+' · 成功 '+time(c.last_success))),badge(c.write?'可写':'只读',c.write?'':'neutral')))):empty('暂无已同步日历','配置日历并完成同步后显示。'),el('details',{},el('summary',{},'查看日历事件快照（'+state.data['calendar-events'].length+'）'),el('pre',{class:'code'},JSON.stringify(state.data['calendar-events'],null,2))));
  root.append(el('div',{class:'grid'},panel('来源健康','每个连接独立记录成功时间与错误',sources),panel('日历连接','编辑方向与最近同步',calendarBody)));
  root.append(el('div',{class:'section-gap'},conflictPanel()));
  const artifacts=state.data.artifacts;
  root.append(el('div',{class:'section-gap'},panel('课程资料与成绩档案','按原始结构展示，不推算缺失成绩',el('div',{class:'panel-body'},artifacts.length?artifacts.map(a=>el('details',{},el('summary',{},(a.kind||a.id)+' · 更新 '+time(a.updated_at)),el('pre',{class:'code'},JSON.stringify(a.data??a,null,2)))):empty('暂无本地资料','配置成绩、课表、假期或复习资料路径后，同步结果显示在这里。')))));
  const jobs=state.data.jobs.slice(0,20);
  root.append(el('div',{class:'section-gap'},panel('后台任务','最近 20 条持久化执行记录',jobs.length?el('div',{class:'table-wrap'},el('table',{},el('thead',{},el('tr',{},['任务','状态','尝试','创建时间','最近错误 / 结果'].map(t=>el('th',{scope:'col'},t)))),el('tbody',{},jobs.map(j=>el('tr',{},el('td',{},j.kind),el('td',{},badge(label(j.status),j.status==='failed'?'error':'neutral')),el('td',{},j.attempts),el('td',{},time(j.created_at)),el('td',{},j.error||j.last_error||(j.result?JSON.stringify(j.result):'—'))))))):empty('暂无后台任务','请求同步或确认安排后，任务会显示在这里。'))));
  return root;
}
function conflictPanel() {
  const conflicts=state.data.conflicts;
  const body=el('div',{class:'panel-body'});
  if(!conflicts.length)body.append(empty('没有待处理冲突','来源日期或日历编辑发生冲突时，在这里逐项核对。'));
  for(const c of conflicts) {
    const item=[...state.data.items,...(state.data.conflictItems||[])].find(i=>i.id===c.item_id);
    body.append(el('div',{class:'row'},el('div',{class:'row-main'},el('div',{class:'row-title'},itemTitle(c.item_id)+' · '+label(c.kind)),el('details',{},el('summary',{},'核对冲突内容'),el('pre',{class:'code'},JSON.stringify(c.document,null,2))),el('div',{class:'actions'},btn('保留本地',()=>resolveConflict(c,item,'local'),'small',state.offline||!item),btn('采用来源 / 远端',()=>resolveConflict(c,item,'remote'),'small',state.offline||!item)))));
  }
  return panel('待处理冲突','确认保留哪一份事实；使用当前事项版本避免覆盖新改动',body);
}
function resolveConflict(conflict,item,choice) {
  const content=el('div',{},el('p',{class:'subtitle'},`为“${item.title}”${choice==='local'?'保留本地记录':'采用来源或远端记录'}。请先核对以下内容。`),el('pre',{class:'code'},JSON.stringify(conflict.document,null,2)));
  const button=btn('确认处理',async()=>{button.disabled=true;try{await mutate('/conflicts/'+conflict.id+'/resolve','POST',{choice,expected_version:item.version},false,'冲突已处理');dialog.close();}catch(error){formError(content,error);}finally{button.disabled=state.offline;}},'primary');content.append(el('div',{class:'form-actions'},btn('取消',()=>dialog.close()),button));openDialog('核对冲突解决方式',content);
}
function configPage() {
  const c=state.data.config, connections=state.data.connections;
  const run=async(action)=>{try{await action();await refresh(true,true);}catch(error){toast(error.message,true);}};
  const google=el('div',{class:'panel-body stack'});
  google.append(el('p',{class:'subtitle'},'登录 Google 后保留离线授权。选择需要读取的日历，以及唯一一个用于写入安排的日历。'));
  for(const account of connections.google_accounts) {
    google.append(el('div',{class:'banner'},el('strong',{},account.email||account.id),el('p',{},'已保存授权 · 自动续期 · 最近验证 '+time(account.last_checked)),el('p',{},(account.calendars||[]).map(x=>(x.name||x.id)+(x.write?'（写入）':'（读取）')).join('、')||'尚未选择日历'),el('div',{class:'form-actions'},btn('检查连接 / 选择日历',()=>calendarDialog(account)),btn('重新登录',()=>run(()=>authorize('/connections/google/authorize',{account_id:account.id}))),btn('修复授权',()=>run(()=>authorize('/connections/google/authorize',{account_id:account.id,force_consent:true}))),btn('断开账号',()=>{if(confirm('断开此账号？历史记录保留，停止后续同步。'))run(()=>mutate('/connections/google/'+account.id,'DELETE',undefined,false,'已断开账号'));}))));
  }
  if(!connections.google_accounts.length) google.append(empty('尚未连接 Google','点击登录，使用你自己的账号完成授权。'));
  google.append(btn('＋ 登录 Google 账号',()=>run(()=>authorize('/connections/google/authorize',{})),'primary',!connections.google_client_configured));
  const clientFile=el('input',{type:'file',accept:'.json,application/json','aria-label':'Google 桌面客户端 JSON'});
  clientFile.addEventListener('change',()=>run(async()=>{const file=clientFile.files[0];if(!file)return;if(file.size>20000)throw new Error('客户端文件过大。');await mutate('/connections/google/client','POST',{client_json:await file.text()},false,'Google 客户端已保存，可以登录账号。');}));
  if(!connections.google_client_configured)google.append(el('div',{class:'banner error'},'安装包缺少应用登录组件，请使用完整的新版安装包。'));
  const proxy=inputField('Google 代理（留空为直连）','url',connections.google_proxy||'',{placeholder:'http://127.0.0.1:7897'});
  google.append(el('p',{class:'subtitle'},connections.google_network_mode==='auto'?'网络由程序自动检测，无需填写代理。':'已使用维护入口指定的网络设置。'),el('details',{},el('summary',{},'高级维护：应用组件与网络'),clientFile,proxy.node,btn('自动检测网络',()=>run(()=>mutate('/connections/proxy','POST',{mode:'auto',proxy:null},false,'已恢复自动网络检测'))),btn('使用指定代理',()=>run(()=>mutate('/connections/proxy','POST',{mode:'manual',proxy:proxy.input.value.trim()},false,'网络设置已生效'))),btn('仅直连',()=>run(()=>mutate('/connections/proxy','POST',{mode:'direct',proxy:null},false,'已切换为直连')))));
  const moodle=connections.moodle||{};
  const site=inputField('Moodle 学校网址','url',moodle.site_url||'https://buelearning.hkbu.edu.hk',{required:true});
  const school=el('div',{class:'panel-body stack'},site.node,el('p',{class:'subtitle'},moodle.enabled?`${moodle.site_name||'Moodle'} · ${moodle.username||''} · 最近验证 ${time(moodle.last_checked)}`:'尚未连接学校账号。学校的 SSO 和多因素验证在系统浏览器中完成。'),btn('学校网页登录',()=>run(()=>authorize('/connections/moodle/authorize',{site_url:site.input.value.trim()})),'primary'));
  const schoolHealth=state.data.dashboard.health.sources.find(x=>x.id==='moodle');
  if(schoolHealth?.status==='error')school.append(el('div',{class:'banner error'},'学校采集存在未读到的数据，已保留成功读取的作业。部分课程或测验可能限制权限；请在运行记录核对，提交状态未知时不会自动视为已提交。',btn('查看运行记录',()=>navigate('sources'))));
  if(moodle.enabled)school.append(el('div',{class:'form-actions'},btn('检查学校连接',()=>run(async()=>{const r=await api('/connections/moodle/check','POST',{});toast(`连接成功：${r.site_name}，${r.courses} 门课程`);})),btn('断开学校账号',()=>{if(confirm('停止 Moodle 同步？历史作业记录保留。'))run(()=>mutate('/connections/moodle','DELETE',undefined,false,'学校账号已断开'));})));
  const token=inputField('Moodle 移动服务 token','password','',{autocomplete:'off'});
  school.append(el('details',{},el('summary',{},'学校禁用网页登录时：使用已有移动授权'),el('p',{class:'subtitle'},'学校必须开放 Moodle 移动服务。若学校强制自己的客户端或禁用服务，请使用本机已有授权，或从学校支持的方式取得移动服务 token。'),token.node,btn('验证并保存学校授权',()=>run(async()=>{await mutate('/connections/moodle/token','POST',{site_url:site.input.value.trim(),token:token.input.value},false,'学校连接验证成功');token.input.value='';}))));
  return el('div',{class:'stack'},el('div',{class:'banner'},c.mode==='shadow'?'影子模式运行中：真实读取邮件、作业与日历，写入只生成预览。模型分析未启用。':'实时同步运行中 · 模型分析未启用'),panel('Google 账号与日历','连接、授权和同步范围',google),panel('Moodle 学校账号','只读采集课程、作业、截止与提交状态',school),panel('本机已有授权','接入旧系统保存的账号，先真实验证再保存',el('div',{class:'panel-body'},btn('检查并接入本机已有授权',()=>run(async()=>{const r=await mutate('/connections/import-existing','POST',{},false);toast(r.results.length?r.results.map(x=>x.ok?`${x.kind} 已验证并连接 ${x.email||''}`:`${x.kind}：${x.error}`).join('；'):'没有找到可接入的旧授权。');})))),el('details',{},el('summary',{},'当前生效配置与维护工具'),el('pre',{class:'code'},JSON.stringify(c,null,2)),btn('邮件核对',()=>navigate('mail')),btn('计划草稿',()=>navigate('plans')),btn('提醒记录',()=>navigate('notices'))));
}

async function authorize(path,body) {
  const result=await api(path,'POST',{...body,open_browser:true});
  const school=path==='/connections/moodle/authorize';
  const link=school?btn('重新打开学校登录窗口',async()=>{dialog.close();try{await authorize(path,body);}catch(error){toast(error.message,true);}}):el('a',{href:result.url,target:'_blank',rel:'noopener noreferrer'},'未打开浏览器？点击继续登录');
  const progress=el('p',{},school?'请在独立的学校直连窗口完成登录及多因素验证，完成后这里会自动更新。此窗口不使用系统代理。':'等待你在系统浏览器登录，完成后这里会自动更新。');
  const content=el('div',{},progress,link,btn('关闭',()=>dialog.close()));
  openDialog(school?'在学校直连窗口完成登录':'在系统浏览器完成登录',content);
  const deadline=Date.now()+600000;
  let polling=false;
  const timer=setInterval(async()=>{
    if(!dialog.open||!content.isConnected||Date.now()>deadline){clearInterval(timer);if(Date.now()>deadline)progress.textContent='本次登录已超时，请关闭后重新登录。';return;}
    if(polling)return;
    polling=true;
    try{
      const status=await api('/connections/flows');
      const flow=result.flow_id?status.flows.find(x=>x.id===result.flow_id):status.flows.filter(x=>x.kind==='moodle').slice(-1)[0];
      if(!flow)return;
      if(flow.status==='done'){clearInterval(timer);dialog.close();await refresh(true,true);toast('账号连接成功');}
      else if(['failed','expired'].includes(flow.status)){clearInterval(timer);progress.textContent=flow.message||'本次登录未完成，请关闭后重新登录。';content.append(btn('重新登录',async()=>{dialog.close();try{await authorize(path,body);}catch(error){toast(error.message,true);}},'primary'));}
      else progress.textContent=flow.status==='processing'?'正在验证账号连接，请稍候……':'等待你在系统浏览器完成登录……';
    }catch(error){progress.textContent=error.message;}finally{polling=false;}
  },2000);
}

async function calendarDialog(account) {
  try {
    const result=await api('/connections/google/'+account.id+'/check','POST',{});
    const rows=result.calendars.map(calendar=>{
      const old=(account.calendars||[]).find(x=>x.id===calendar.id);
      const read=el('input',{type:'checkbox',checked:old?.read??false,'aria-label':'读取 '+(calendar.summary||calendar.id)});
      const write=el('input',{type:'radio',name:'write-calendar',checked:old?.write??false,disabled:!['owner','writer'].includes(calendar.accessRole),'aria-label':'写入 '+(calendar.summary||calendar.id)});
      return {id:calendar.id,read,write,node:el('div',{class:'banner'},el('strong',{},calendar.summary||calendar.id),el('p',{},calendar.id),el('label',{},read,' 读取占用时间'),el('label',{},write,' 写入安排'))};
    });
    const none=el('input',{type:'radio',name:'write-calendar',checked:!rows.some(x=>x.write.checked)});
    const content=el('div',{class:'stack'},el('p',{},'只能有一个写入日历。若另一账号已选择写入，请先取消它的写入选择。'),el('label',{},none,' 暂不写入日历'),rows.map(x=>x.node));
    content.append(btn('保存同步范围',async()=>{try{await mutate('/connections/google/'+account.id+'/calendars','PUT',{calendars:rows.filter(x=>x.read.checked||x.write.checked).map(x=>({id:x.id,read:x.read.checked,write:x.write.checked}))},false,'日历范围已保存并排队同步');dialog.close();await refresh(true,true);}catch(error){formError(content,error);}},'primary'));
    openDialog('选择 '+result.email+' 的日历',content);
  } catch(error){toast(error.message,true);}
}
function loginScreen() {
  const setup=!state.session.initialized&&!state.session.remote;
  const username=inputField('用户名','text','',{required:true,autocomplete:'username',maxlength:100});
  const password=inputField('密码','password','',{required:true,autocomplete:setup?'new-password':'current-password',minlength:setup?8:1,maxlength:500});
  const repeat=setup?inputField('再次输入密码','password','',{required:true,autocomplete:'new-password',minlength:8,maxlength:500}):null;
  const submit=el('button',{type:'submit',class:'primary'},setup?'创建本地账号':'登录工作台');
  const form=el('form',{},username.node,password.node,repeat?.node,submit);
  form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;try{if(setup){if(password.input.value!==repeat.input.value)throw new Error('两次密码输入不一致。');await api('/setup','POST',{username:username.input.value.trim(),password:password.input.value});state.session.initialized=true;}const result=await api('/login','POST',{username:username.input.value.trim(),password:password.input.value});state.session={...state.session,...result,authenticated:true};state.data={};state.lastUpdated=null;state.offline=false;state.page=state.session.remote?'overview':'config';shell();await refresh();}catch(error){formError(form,error);if(setup&&state.session.initialized){loginScreen();toast('账号已创建，请登录。'+error.message,true);}}finally{submit.disabled=false;}});
  app.replaceChildren(el('div',{class:'login-screen'},el('section',{class:'login-art'},brand(),el('h1',{},'把学业，',el('br'),'安排得从容。'),el('p',{},'课程、邮件、截止日期与学习计划，汇聚在一个安静而可靠的工作台。'),el('div',{class:'login-line'}),el('div',{class:'login-foot'},'CLARITY BEFORE ACTION')),el('div',{class:'login-area'},el('div',{class:'login-box'},el('div',{class:'eyebrow'},state.session.remote?'READ-ONLY DASHBOARD':'LOCAL WORKSPACE'),el('h1',{},setup?'建立你的工作台':'欢迎回来'),el('p',{class:'subtitle'},setup?'设置本地管理账号，密码至少 8 个字符。':state.session.remote?'使用本地账号登录远程只读看板。':'登录后查看真实的来源与学业进展。'),form,el('p',{class:'login-caption'},!state.session.initialized&&state.session.remote?'工作台尚未初始化，请先在本地 8765 端口创建账号。':'忘记密码？请在后台主机运行 reset-password 重置；重置后所有浏览器需重新登录。')))));
}
async function logout() {try{await api('/logout','POST');state.session.authenticated=false;state.data={};dialog.close();loginScreen();toast('已退出登录');}catch(error){toast(error.message,true);}}
async function boot() {try{state.session=await api('/session');if(state.session.authenticated){state.page=state.session.remote?'overview':'config';shell();await refresh();}else loginScreen();}catch(error){app.replaceChildren(el('div',{class:'boot'},'Academic OS',el('span',{},error.message),btn('重新连接',boot,'primary section-gap')));}}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true);});
setInterval(()=>{if(!document.hidden)refresh(true);},30000);
window.addEventListener('online',()=>refresh(true));
window.addEventListener('offline',()=>{state.offline=true;renderPage();});
boot();



