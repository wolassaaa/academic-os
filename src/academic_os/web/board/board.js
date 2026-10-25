globalThis.AcademicBoardStart=async function(provided){
  globalThis.AcademicBoardBootPending=true;
  'use strict';
  const M=globalThis.AcademicBoardModel,adapter=globalThis.AcademicBoardData;
  let snapshot=provided||await adapter.load().catch(()=>null);globalThis.AcademicBoardBootPending=false;if(!snapshot)return;
  let now=new Date(snapshot.now),timezone=snapshot.timezone,semester=snapshot.semester||{};
  const params=new URLSearchParams(location.search);
  const state={history:params.get('history')||'',q:params.get('q')||'',course:params.get('course')||'all',pool:params.get('pool')||'all',expanded:params.get('expanded')==='1',range:params.get('range')||'all',startDate:params.get('from')||'',endDate:params.get('to')||'',includeUndated:params.get('undated')!=='0'};
  if(!['all','week','next-week','month','semester','custom'].includes(state.range))state.range='all';
  if(state.range!=='custom')Object.assign(state,M.rangeDates(state.range,now,timezone,semester));
  if((state.startDate&&!M.validDay(state.startDate))||(state.endDate&&!M.validDay(state.endDate))||(state.startDate&&state.endDate&&state.startDate>state.endDate)){state.range='all';state.startDate='';state.endDate='';}
  let originalPartition=M.partition(snapshot.items,{now,timezone}),automaticFocusIds=new Set(originalPartition.important.map(i=>i.id));
  function readDecisions(){return {partition:options=>M.partition(snapshot.items,{...options,choices:snapshot.focusPrefs?.choices||{}}),item:id=>snapshot.items.find(i=>i.id===id)||null,items:()=>snapshot.items,trashItems:()=>[]};}
  let decisions=readDecisions();
  let partition=decisions.partition({now,timezone}),interactions=null;
  const isTasks=location.pathname.endsWith('/tasks.html'),isDetail=location.pathname.endsWith('/task.html');
  if(!['','all','completed','submitted','cancelled','pending-submission'].includes(state.history)||!isTasks&&!isDetail)state.history='';
  const initial=6;
  const closed=new Set(['completed','submitted','cancelled']);

  const icons={clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',week:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18"/>',down:'<path d="m6 9 6 6 6-6"/>',arrow:'<path d="m9 5 7 7-7 7"/>',check:'<path d="m5 12 4 4L19 6"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>',search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>'};
  function el(tag,cls,...children){const node=document.createElement(tag);if(cls)node.className=cls;for(const child of children.flat())if(child!=null)node.append(child instanceof Node?child:document.createTextNode(String(child)));return node;}
  function ico(key){const node=document.createElementNS('http://www.w3.org/2000/svg','svg');node.classList.add('icon');node.setAttribute('viewBox','0 0 24 24');node.setAttribute('fill','none');node.setAttribute('stroke','currentColor');node.setAttribute('stroke-width','1.65');node.setAttribute('stroke-linecap','round');node.setAttribute('stroke-linejoin','round');node.setAttribute('aria-hidden','true');node.innerHTML=icons[key];return node;}
  function button(text,cls,click){const b=el('button',cls||'button',text);b.type='button';b.addEventListener('click',click);return b;}
  function query(){const p=new URLSearchParams();if(state.history)p.set('history',state.history);if(state.q)p.set('q',state.q);if(state.course!=='all')p.set('course',state.course);if(state.pool!=='all')p.set('pool',state.pool);if(state.expanded)p.set('expanded','1');if(state.range!=='all')p.set('range',state.range);if(state.startDate)p.set('from',state.startDate);if(state.endDate)p.set('to',state.endDate);if(!state.includeUndated)p.set('undated','0');return p;}
  function href(path,item){const p=query();if(item)p.set('item',item.id);return path+(p.size?'?'+p.toString():'');}
  function link(text,path,cls,item,icon){const node=el('a',cls||'button',...(icon?[ico(icon)]:[]),text);node.href=href(path,item);return node;}
  function unavailable(text,cls,icon){const node=el('button',cls||'button',...(icon?[ico(icon)]:[]),text);node.disabled=true;node.title='此操作请在工作台处理';return node;}
  function completionButton(item,text='完成',cls='button'){
    const b=button(text,cls,e=>globalThis.AcademicCompletion?.request(item,e.currentTarget));
    b.dataset.completion=item.id;b.dataset.allowed=String(snapshot.canComplete&&!closed.has(item.status));b.disabled=b.dataset.allowed!=='true';b.setAttribute('aria-label',text+'：'+item.title);
    if(b.disabled)b.title=snapshot.remote?'远程模式只读':closed.has(item.status)?'事项已关闭':'当前会话未提供完成权限';return b;
  }
  function reopenButton(item){
    const b=button('重新打开','button quiet reopen-action',e=>globalThis.AcademicCompletion?.reopen(item,e.currentTarget));
    b.dataset.reopen=item.id;b.dataset.allowed=String(snapshot.canComplete&&closed.has(item.status));b.disabled=b.dataset.allowed!=='true';b.setAttribute('aria-label','重新打开：'+item.title);
    if(b.disabled)b.title=snapshot.remote?'远程模式只读':'当前会话未提供确认权限';return b;
  }
  function followup(item){return !snapshot.remote&&M.submissionFollowup(item).length?el('p','submission-followup','个人已完成 · 有来源仍显示未提交，请核对。'):null;}
  function courseItems(){return isTasks||isDetail?snapshot.items.filter(M.formal):partition.active;}
  function focusButton(item,reset=false){
    const focused=partition.important.some(i=>i.id===item.id),mode=reset?'auto':focused?'pool':'focus';
    const text=reset?'恢复自动推荐':focused?'移出重点':'设为重点';
    const b=button(text,'button quiet focus-action',()=>globalThis.AcademicFocus?.set(item.id,mode));
    b.dataset.focus=item.id;b.dataset.allowed=String(snapshot.canFocus&&!closed.has(item.status));b.disabled=b.dataset.allowed!=='true';b.setAttribute('aria-label',text+'：'+item.title);
    if(b.disabled)b.title=snapshot.remote?'远程只读':'重点偏好暂不可修改';return b;
  }
  function focusLabel(item){return el('span','focus-origin',snapshot.focusPrefs?.choices[item.id]==='focus'?'我的重点':'自动推荐');}
  function trust(item){const state=M.deadlineState(item),label=el('span','deadline-trust '+state.tone,state.label);label.title=state.help;return label;}
  function workbench(item,page='items'){const query=new URLSearchParams({page});if(item)query.set('item',item.id);const a=el('a','workbench-deep-link',page==='sources'?'检查来源与冲突':'在工作台定位此事项');a.href='/?'+query;return a;}
  function evidence(item){
    const section=el('section','detail-section',el('h2','','截止依据'));
    if(snapshot.remote){section.append(el('p','muted small','远程模式不展示原文证据。'));return section;}
    for(const entry of item.evidence||[]){const box=el('div','quote');box.append(el('p','',entry.evidence_text||entry.text||'来源未提供原文片段。'),el('p','small muted',String(entry.source||'来源未标明')));
      if(entry.due_at)box.append(el('p','small','来源声明截止 · '+date(entry.due_at,true)));
      if(entry.observed_at)box.append(el('p','small muted','采集时间 · '+date(entry.observed_at,true)));
      section.append(box);
    }
    if(!item.evidence?.length)section.append(el('p','muted small','没有来源证据，不猜测截止时间。'));
    for(const [index,key] of [...new Set(item.mail_keys||[])].filter(k=>typeof k==='string').entries()){
      const a=el('a','button quiet','查看关联原邮件 '+(index+1));a.href='mail-detail.html?'+new URLSearchParams({key});
      try{const back=new URL(params.get('mail_return'),location.href);if(back.origin===location.origin&&back.pathname===new URL('mail-detail.html',location.href).pathname&&back.searchParams.get('key')===key)a.href=back.pathname+back.search;}catch{}
      section.append(a);
    }
    section.append(workbench(item,'sources'));return section;
  }
  function courseColor(item){return !item.course&&!item.classification?.course?'sand':['sage','blue','lilac'][Array.from(M.courseKey(item)).reduce((n,c)=>n+c.charCodeAt(0),0)%3];}
  function dot(item){return el('span','dot '+courseColor(item));}
  function date(value,withYear=false){const d=M.validDate(value);return d?new Intl.DateTimeFormat('zh-CN',{timeZone:timezone,month:'numeric',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',...(withYear?{year:'numeric'}:{})}).format(d):value?'日期待核对':'待公布';}
  function ring(item){const info=M.due(item.source_due_at,now),node=el('div','ring '+info.state);node.role='img';node.setAttribute('aria-label',info.label+'，按未来七天截止距离显示');
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 72 72');svg.setAttribute('aria-hidden','true');
    for(const cls of ['ring-track','ring-line']){const c=document.createElementNS(svg.namespaceURI,'circle');c.setAttribute('cx','36');c.setAttribute('cy','36');c.setAttribute('r','29');c.classList.add(cls);if(cls==='ring-line'){c.style.strokeDasharray='182.2';c.style.strokeDashoffset=String(182.2*(1-info.fill));}svg.append(c);}
    const center=el('div','ring-center',el('span','ring-status',info.state==='overdue'?'逾期':info.state==='unknown'?'待确认':'还剩'),el('span','ring-number',info.short),el('span','ring-unit',info.unit||'未定'));node.append(svg,center);return node;
  }
  function card(item){const due=M.due(item.source_due_at,now),article=el('article','task-card '+courseColor(item)+' '+(due.state==='overdue'?'overdue-card':''));article.dataset.itemId=item.id;
    const titleLink=link(item.title,'task.html','task-title-link',item);
    const meta=el('div','card-meta',dot(item),el('span','meta-name',M.courseName(item)),el('span','kind-chip',item.kind==='assignment'?'作业':'任务'),focusLabel(item));
    const main=el('div','card-main',el('div','',el('h3','card-title',titleLink),el('div','due-caption','截止时间'),el('div','due-date',date(item.source_due_at))),ring(item));
    const arranged=el('div','arranged',ico('clock'),item.scheduled_start?'已安排 · '+date(item.scheduled_start):'尚未安排学习时间');
    const sub=link(item.subtasks?.length?'查看 '+item.subtasks.length+' 个小任务':'查看任务详情','task.html','card-subtasks',item,'down');
    const status=el('span','due-state '+due.state,due.label);
    main.querySelector('.due-date').after(trust(item));article.append(meta,main,arranged,sub,el('div','card-actions',unavailable(item.scheduled_start?'已安排':'安排时间','button primary','week'),completionButton(item),focusButton(item)),status);return article;
  }
  function historyRow(item){
    const row=el('div','task-row history-row');row.dataset.itemId=item.id;
    row.append(el('span','check-circle',...(item.status==='completed'?[ico('check')]:[])),el('div','',link(item.title,'task.html','task-name',item),el('div','task-course',dot(item),M.courseName(item)),followup(item)),el('div','row-due',date(item.source_due_at)),el('div','row-state',M.closedLabel(item)),el('div','row-controls',reopenButton(item),link('','task.html','row-arrow',item,'arrow')));return row;
  }
  function taskRow(item){const info=M.due(item.source_due_at,now),row=el('div','task-row');row.dataset.itemId=item.id;
    const circle=completionButton(item,'完成','check-circle completion-check');circle.replaceChildren();
    const title=el('div','',link(item.title,'task.html','task-name',item),el('div','task-course',dot(item),M.courseName(item)),trust(item));
    const due=el('div','row-due '+info.state,info.state==='unknown'?info.label:date(item.source_due_at));
    if(info.state!=='unknown'){const fill=el('i');fill.style.width=(info.fill*100)+'%';due.append(el('div','time-track',fill),el('small','remaining-label',info.label));}
    row.append(circle,title,due,el('div','row-state',item.scheduled_start?'已安排':'未安排'),el('div','row-controls',focusButton(item),link('','task.html','row-arrow',item,'arrow')));return row;
  }
  const resultsStatus=el('span','filter-results');resultsStatus.role='status';resultsStatus.setAttribute('aria-live','polite');
  const search=el('input');search.type='search';search.placeholder='搜索任务或课程';search.setAttribute('aria-label','搜索任务或课程');search.value=state.q;
  search.addEventListener('input',()=>{state.q=search.value;state.expanded=false;render();});
  const courseSelect=el('select');courseSelect.setAttribute('aria-label','筛选课程');
  let unique=new Map(courseItems().map(i=>[M.courseKey(i),M.courseName(i)]));
  for(const [key,name] of [['all','全部课程'],...unique]){const option=el('option','',name);option.value=key;courseSelect.append(option);}if(!unique.has(state.course))state.course='all';courseSelect.value=state.course;
  courseSelect.addEventListener('change',()=>{state.course=courseSelect.value;state.expanded=false;render();});
  const clear=button('清除筛选','button quiet',()=>{state.q='';state.course='all';state.pool='all';search.value='';courseSelect.value='all';state.expanded=false;state.range='all';state.startDate='';state.endDate='';state.includeUndated=true;syncDateControls();render();});
  const filterbar=el('div','board-filters',el('label','search',ico('search'),search),el('label','course-picker',courseSelect),clear,resultsStatus);
  let cards=document.querySelector('.cards'),pool=document.querySelector('.pool'),poolList,expand,tabButtons=[];
  if(isTasks){
    document.querySelector('.toolbar')?.remove();document.querySelector('.tabs')?.remove();document.querySelector('.list-label')?.remove();document.querySelectorAll('.task-row').forEach(n=>n.remove());document.querySelector('.pool-foot')?.remove();
    const heading=document.querySelector('.page-heading');heading.after(filterbar);poolList=el('div','all-task-list');filterbar.after(poolList);
  }else{
    document.querySelector('.page-heading').after(filterbar);
    const region=document.querySelector('section[aria-label="本周重点"]');
    const line=region.querySelector('.section-heading');line.querySelector('p').replaceChildren(el('span','material-key','截止距离 · 非完成度'));
    expand=button('','button expand-priorities',()=>{state.expanded=!state.expanded;render();expand.focus();});expand.setAttribute('aria-controls','important-cards');cards.id='important-cards';cards.after(expand);
    pool.querySelectorAll('.task-row').forEach(n=>n.remove());poolList=el('div','pool-list');pool.querySelector('.tabs').after(poolList);
  }
  const tabs=el('div','tabs board-tabs');
  for(const [key,text] of [['all','全部待完成'],['unscheduled','未安排'],['scheduled','已安排'],['undated','无截止']]){const b=button(text,'tab-button',()=>{state.pool=key;render();});b.dataset.view=key;tabButtons.push(b);tabs.append(b);}
  if(isTasks)filterbar.after(tabs);else pool.querySelector('.tabs').replaceWith(tabs);
  const historyControls=el('div','history-controls'),historySelect=el('select'),historySummary=el('p','history-summary');
  historySelect.setAttribute('aria-label','历史状态');
  for(const [value,label] of [['all','全部已关闭'],['completed','已完成'],['submitted','已提交'],['cancelled','已取消'],['pending-submission','个人完成 · 来源待提交']]){const option=el('option','',label);option.value=value;historySelect.append(option);}
  historySelect.value=state.history||'all';historySelect.addEventListener('change',()=>{state.history=historySelect.value;render();});
  historyControls.append(el('label','history-picker','历史状态 ',historySelect),historySummary);
  const viewSwitch=el('div','task-view-switch'),activeView=button('待完成','button',()=>{state.history='';render();}),closedView=button('已关闭/历史','button',()=>{state.history='all';historySelect.value='all';render();});
  viewSwitch.setAttribute('role','group');viewSwitch.setAttribute('aria-label','事项视图');viewSwitch.append(activeView,closedView);
  if(isTasks){filterbar.before(viewSwitch);tabs.after(historyControls);}
  const dates=el('form','date-filter-bar');dates.setAttribute('aria-label','按截止日期筛选');
  const preset=el('select');preset.setAttribute('aria-label','时间范围');
  for(const [value,label] of [['all','全部时间'],['week','本周'],['next-week','下周'],['month','本月'],['semester','本学期'],['custom','自定义时段']]){const option=el('option','',label);option.value=value;if(value==='semester'&&!M.validDay(semester.startDate))option.disabled=true;preset.append(option);}
  const from=el('input'),to=el('input');from.type=to.type='date';from.setAttribute('aria-label','开始日期');to.setAttribute('aria-label','结束日期');
  const undated=el('input');undated.type='checkbox';undated.setAttribute('aria-label','保留无截止任务');
  const apply=el('button','button tonal','应用时段');apply.type='submit';
  const dateError=el('span','date-filter-error');dateError.role='alert';dateError.hidden=true;
  const rangeStatus=el('span','date-filter-status');rangeStatus.role='status';rangeStatus.setAttribute('aria-live','polite');
  dates.append(el('span','date-filter-label',ico('week'),'截止日期'),el('label','date-preset',preset),el('label','date-bound',el('span','','从'),from),el('span','date-separator','—'),el('label','date-bound',el('span','','到'),to),apply,el('label','undated-toggle',undated,'保留无截止'),rangeStatus,dateError);
  function syncDateControls(){preset.value=state.range;from.value=state.startDate;to.value=state.endDate;undated.checked=state.includeUndated;dateError.hidden=true;}
  preset.addEventListener('change',()=>{state.range=preset.value;if(state.range==='custom'){from.focus();return;}Object.assign(state,M.rangeDates(state.range,now,timezone,semester));state.expanded=false;syncDateControls();render();});
  dates.addEventListener('submit',event=>{event.preventDefault();const start=from.value,end=to.value;if((!start&&!end)||(start&&end&&start>end)){dateError.textContent=!start&&!end?'请选择开始或结束日期。':'结束日期应不早于开始日期。';dateError.hidden=false;return;}state.range='custom';state.startDate=start;state.endDate=end;state.expanded=false;syncDateControls();render();});
  undated.addEventListener('change',()=>{state.includeUndated=undated.checked;render();});
  syncDateControls();filterbar.after(dates);
  function progressBar(label,percent,caption,cls=''){
    const fill=el('i');fill.dataset.progressFill=percent;fill.style.transform='scaleX('+(percent/100)+')';
    const track=el('div','context-progress-track',fill);track.role='progressbar';track.setAttribute('aria-label',label+'（时间进程）');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');track.setAttribute('aria-valuenow',String(percent));track.setAttribute('aria-valuetext','已走过 '+percent+'%，不表示任务完成率');
    return el('div','context-progress '+cls,el('div','context-progress-label',el('span','',label),el('b','',percent+'%')),track,el('div','context-progress-caption',caption));
  }
  let time=M.timeProgress(now,timezone,semester);
  const timeStrip=el('section','time-context',progressBar('本周时间',time.week.percent,time.week.startDate.slice(5)+' — '+time.week.endDate.slice(5)+' · '+new Intl.DateTimeFormat('zh-CN',{timeZone:timezone,weekday:'short'}).format(now),'week-progress'),time.semester?progressBar('学期时间',time.semester.percent,'第 '+time.semester.weekNumber+' / '+time.semester.totalWeeks+' 周 · '+semester.startDate.slice(5)+' — '+semester.endDate.slice(5),'semester-progress'):el('div','context-progress muted','学期未设置'));
  timeStrip.setAttribute('aria-label','时间进程，不是任务完成度');filterbar.before(timeStrip);
  const focusNote=el('div','focus-guidance');focusNote.hidden=true;
  if(!isTasks)document.querySelector('section[aria-label="本周重点"]').before(focusNote);
  const mainStatus=document.querySelector('.sync-status');if(mainStatus)mainStatus.textContent=snapshot.remote?'远程只读':'本地看板';
  document.querySelectorAll('.footer>span').forEach(n=>n.textContent='看板 v0.3 · 其他操作在工作台');
  document.querySelector('.pool-foot>span')?.replaceChildren('时间条 / 环表示截止距离');
  function updateNavigation(){
    for(const anchor of document.querySelectorAll('a')){const original=new URL(anchor.href,location.href);if(['index.html','calendar.html','tasks.html'].includes(original.pathname.split('/').pop()))anchor.href=href(original.pathname.split('/').pop());}
    for(const anchor of document.querySelectorAll('.course-nav')){const key=anchor.dataset.courseKey;anchor.classList.toggle('course-selected',state.course===key);anchor.onclick=key?event=>{event.preventDefault();state.course=state.course===key?'all':key;courseSelect.value=state.course;state.expanded=false;render();}:null;}
    const p=query();if(isDetail&&params.get('item'))p.set('item',params.get('item'));history.replaceState(null,'',location.pathname+(p.size?'?'+p.toString():''));
  }

  const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
  let stream=null,streamCount=0,streamPaused=false,streamListRequested=false,scrollFrame=0,previousScrollY=scrollY,streamItems=[];
  if(!isTasks&&!isDetail){
    stream=el('aside','task-stream');stream.setAttribute('aria-label','其他待办流');stream.hidden=true;
    const jump=button('展开待办列表','stream-jump',()=>{streamListRequested=true;updateStreamVisibility();if(globalThis.AcademicMotion)AcademicMotion.scrollTo(pool);else pool.scrollIntoView({behavior:'instant',block:'start'});});
    const pause=button('暂停流动','stream-pause',()=>{streamPaused=!streamPaused;updateStreamMotion();});pause.setAttribute('aria-pressed','false');
    const streamHead=el('div','stream-head',el('div','stream-caption',el('span','stream-live-dot'),'还有这些，慢慢来',el('span','stream-count')),el('div','stream-tools',pause,jump));
    stream.append(streamHead,el('div','stream-lanes'));pool.querySelector('.board-tabs').before(stream);
    const onScroll=()=>{if(scrollFrame)return;scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;if(previousScrollY>=64&&scrollY<64)streamListRequested=false;previousScrollY=scrollY;updateStreamVisibility();});};
    addEventListener('scroll',onScroll,{passive:true});addEventListener('resize',()=>renderStream(streamItems));reducedMotion.addEventListener('change',updateStreamVisibility);document.addEventListener('visibilitychange',updateStreamMotion);
  }
  function updateStreamMotion(){if(!stream)return;stream.dataset.paused=String(streamPaused||document.hidden);const b=stream.querySelector('.stream-pause');b.textContent=streamPaused?'继续流动':'暂停流动';b.setAttribute('aria-pressed',String(streamPaused));globalThis.AcademicMotion?.syncStream();}
  function updateStreamVisibility(){
    if(!stream){document.body.dataset.poolMode='list';return;}
    const mobileCalendar=document.body.classList.contains('calendar-open')&&innerWidth<=900;
    const visible=scrollY<64&&!streamListRequested&&!reducedMotion.matches&&!mobileCalendar&&streamCount>0;
    if(!visible&&stream.contains(document.activeElement)){pool.querySelector('h2').tabIndex=-1;pool.querySelector('h2').focus({preventScroll:true});}
    stream.hidden=!visible;document.body.dataset.poolMode=visible?'flow':'list';updateStreamMotion();
  }
  function streamChip(item,copy){
    const chip=link('','task.html','stream-chip '+courseColor(item),item);if(copy)chip.tabIndex=-1;
    chip.append(dot(item),el('span','stream-chip-title',item.title),el('span','stream-chip-due',M.due(item.source_due_at,now).label));chip.title=item.title;return chip;
  }
  function renderStream(items){
    if(!stream)return;streamListRequested=false;streamItems=items;streamCount=items.length;stream.querySelector('.stream-count').textContent=items.length+' 项';
    const lanes=stream.querySelector('.stream-lanes');lanes.replaceChildren();
    const groups=items.length>3&&innerWidth>600?[items.filter((_,n)=>n%2===0),items.filter((_,n)=>n%2===1)]:[items];
    groups.forEach((group,index)=>{if(!group.length)return;const track=el('div','stream-track '+(group.length<3?'still':''));track.style.setProperty('--stream-seconds',Math.max(36,group.length*14)+'s');
      const original=el('div','stream-group',...group.map(i=>streamChip(i,false))),duplicate=el('div','stream-group',...group.map(i=>streamChip(i,true)));duplicate.setAttribute('aria-hidden','true');track.append(original,duplicate);lanes.append(el('div','stream-lane lane-'+index,track));});
    globalThis.AcademicMotion?.attachStream(stream);updateStreamVisibility();
  }
  function render({positions=null}={}){
    partition=decisions.partition({now,timezone});
    const options={q:state.q,course:state.course,view:'all',startDate:state.startDate,endDate:state.endDate,includeUndated:state.includeUndated,timezone};
    const hasRange=!!(state.startDate||state.endDate),scoped=M.filter(partition.active,options);
    const importantBase=hasRange?scoped.filter(i=>snapshot.focusPrefs?.choices[i.id]==='focus'||snapshot.focusPrefs?.choices[i.id]!=='pool'&&(M.validDate(i.source_due_at)||Number(i.priority)>=4)):partition.important;
    const importantIds=new Set(importantBase.map(i=>i.id));
    const poolBase=hasRange?scoped.filter(i=>!importantIds.has(i.id)):partition.pool;
    const important=M.filter(importantBase,options).sort((a,b)=>Number(snapshot.focusPrefs?.choices[b.id]==='focus')-Number(snapshot.focusPrefs?.choices[a.id]==='focus')),rest=M.filter(poolBase,{...options,view:state.pool});
    const base=isTasks&&state.history?M.history(snapshot.items,state.history):partition.active,count=M.filter(base,options).length;
    resultsStatus.textContent=(state.q||state.course!=='all'||hasRange||!state.includeUndated)?'找到 '+count+' 项任务':'';clear.hidden=!state.q&&state.course==='all'&&state.pool==='all'&&!hasRange&&state.includeUndated;
    rangeStatus.textContent=hasRange?(state.startDate||'不限开始')+' 至 '+(state.endDate||'不限结束')+' · '+count+' 项':'';
    const moved=M.filter(partition.pool,options).filter(i=>snapshot.focusPrefs?.choices[i.id]==='pool'&&(hasRange?M.validDate(i.source_due_at)||Number(i.priority)>=4:automaticFocusIds.has(i.id)));
    focusNote.hidden=!moved.length;focusNote.replaceChildren();
    if(moved.length){const overdue=moved.filter(i=>M.due(i.source_due_at,now).state==='overdue').length;focusNote.append(el('span','',moved.length+' 项已移到待办池'+(overdue?'，其中 '+overdue+' 项逾期':'，截止仍需关注')+'。'),button('查看待办池','button quiet',()=>{state.pool='all';render();streamListRequested=true;updateStreamVisibility();globalThis.AcademicMotion?.scrollTo(pool);}));}
    if(isTasks){
      const inHistory=!!state.history,all=M.filter(base,{...options,view:inHistory?'all':state.pool});
      activeView.setAttribute('aria-pressed',String(!inHistory));closedView.setAttribute('aria-pressed',String(inHistory));
      tabs.hidden=inHistory;historyControls.hidden=!inHistory;historySelect.value=state.history||'all';
      historySummary.textContent=count+' 项 · 保留原事项、截止与来源记录。重新打开只修改个人状态。';
      poolList.replaceChildren(...all.map(inHistory?historyRow:taskRow));
      if(!all.length)poolList.append(empty(inHistory?'暂无符合条件的历史事项':partition.active.length?'没有符合条件的任务':'还没有待办',inHistory?'可调整历史状态、课程或截止日期筛选；事项不会因关闭而删除。':partition.active.length?'试试其他课程、关键词或安排状态。':'正式事项中暂无待完成任务。'));
    }else{
      const show=state.expanded?important:important.slice(0,initial);cards.setAttribute('aria-label','本周重点卡片');cards.replaceChildren(...show.map(card));
      if(!important.length)cards.append(empty(partition.active.length?'这里暂时没有紧急事项':'还没有待办',partition.active.length?'其余任务仍在下方待办池，不会被隐藏。':'正式事项中暂无待完成任务。'));
      const heading=document.querySelector('section[aria-label="本周重点"] h2');heading.replaceChildren(hasRange?'所选时段 ':'本周重点 ',el('span','section-title-count',important.length));
      expand.hidden=important.length<=initial;expand.textContent=state.expanded?'收起重点 · 显示前 '+initial+' 项':'展开其余 '+(important.length-initial)+' 项 · 共 '+important.length+' 项';expand.setAttribute('aria-expanded',String(state.expanded));
      const h=pool.querySelector('h2');h.replaceChildren('待办池 ',el('span','section-title-count',rest.length));poolList.replaceChildren(...rest.map(taskRow));if(!rest.length)poolList.append(empty(partition.active.length?'暂无其他待办':'还没有待办',partition.active.length?'可调整筛选查看其他任务。':'正式事项中暂无待完成任务。'));renderStream(rest);
    }
    tabButtons.forEach(b=>{b.classList.toggle('selected',b.dataset.view===state.pool);b.setAttribute('aria-pressed',String(b.dataset.view===state.pool));});updateNavigation();renderUrgency();globalThis.AcademicMotion?.render(document.querySelector('main'),{positions});interactions?.refresh();
  }
  function empty(title,subtitle){return el('div','empty-state',el('h2','',title),el('p','',subtitle));}
  const urgency=el('div','focus-urgency');urgency.setAttribute('role','status');dates.after(urgency);
  function renderUrgency(){const overdue=partition.active.filter(i=>M.due(i.source_due_at,now).state==='overdue');urgency.hidden=!!state.history||!overdue.length;urgency.replaceChildren(link(overdue.length+' 项已逾期 · 查看全部待办','tasks.html','button quiet'));urgency.querySelector('a').href='tasks.html';}
  render();renderUrgency();
  function renderDetail(){
    if(!isDetail)return;
    const id=params.get('item')||null,item=decisions.item(id),panel=document.querySelector('.detail-panel');
    document.querySelector('main.dashboard').inert=true;
    if(!item){panel.replaceChildren(link('返回本周','index.html','button'),empty('找不到这条任务','它可能已被移除，或不属于正式事项。'));}
    else if(item.preview_trashed){panel.replaceChildren(link('返回本周','index.html','button'),el('div','detail-section',el('span','pill amber','已在回收站 · 预览'),el('h1','',item.title),el('p','muted small','截止、邮件关联和安排保留。')),button('恢复这条任务','button primary',()=>interactions?.restore(item.id)));}
    else{
      const close=link(state.history?'返回历史':'返回本周',state.history?'tasks.html':'index.html','button quiet',null,'close'),facts=el('dl','detail-facts');
      for(const [label,value] of [['截止时间',date(item.source_due_at,true)],['截止状态',M.deadlineState(item).label],['剩余时间',M.due(item.source_due_at,now).label],['任务状态',({completed:'已完成',submitted:'已提交',cancelled:'已取消',in_progress:'进行中'}[item.status]||'待完成')],['学习安排',item.scheduled_start?date(item.scheduled_start):'尚未安排'],['来源',snapshot.remote?'远程脱敏数据':([...new Set((item.evidence||[]).map(e=>String(e.source||'').split(':')[0]))].join(' / ')||'未提供')]])facts.append(el('dt','',label),el('dd','',value));
      if(!snapshot.remote){const states=M.statusFacts(item);facts.append(el('dt','','个人状态'),el('dd','personal-status-fact',states.personal),el('dt','','来源提交记录'));const observations=el('dd','source-submission-fact');if(states.observations.length)for(const text of states.observations)observations.append(el('div','',text));else observations.textContent='未提供，不代表已提交';facts.append(observations);}
      const subs=el('section','detail-section',el('h2','','小任务 · '+(item.subtasks?.length||0)));
      if(item.subtasks?.length)for(const sub of item.subtasks){const check=el('span','check-circle',...(sub.completed?[ico('check')]:[]));subs.append(el('div','subtask '+(sub.completed?'done':''),check,el(sub.completed?'s':'span','',sub.title)));}
      else subs.append(el('p','muted small','小任务未提供。'));
      const done=(item.subtasks||[]).filter(s=>s.completed).length,total=item.subtasks?.length||0;
      const subProgress=total?progressBar('小任务完成',Math.round(done/total*100),done+' / '+total+' 个已完成','subtask-progress'):null;
      if(subProgress){const bar=subProgress.querySelector('[role="progressbar"]');bar.setAttribute('aria-label','小任务完成进度');bar.setAttribute('aria-valuetext',done+' / '+total+' 个小任务已完成');}
      panel.replaceChildren(el('div','detail-head',el('span','pill',M.courseName(item)),close),el('div','detail-hero '+courseColor(item),el('div','eyebrow',M.courseName(item)),el('h1','',item.title),ring(item)),facts,followup(item),...(subProgress?[subProgress]:[]),subs,evidence(item),el('section','detail-section',el('h2','','关联信息'),link('查看信息来源','sources.html','detail-link',item,'arrow'),workbench(item)),el('div','detail-actions',unavailable('查看安排','button primary','week'),completionButton(item,closed.has(item.status)?'已关闭':'标记完成'),...(closed.has(item.status)?[reopenButton(item)]:[]),focusButton(item),...(snapshot.focusPrefs?.choices[item.id]?[focusButton(item,true)]:[])));
      globalThis.AcademicMotion?.progress(panel);globalThis.AcademicMotion?.panel(panel);
      close.focus();
    }
  }
  renderDetail();
  document.addEventListener('keydown',event=>{
    if(document.querySelector('dialog[open],.task-action-menu'))return;
    if(event.key==='Escape'&&isDetail)location.href=href(state.history?'tasks.html':'index.html');
    if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!document.activeElement.isContentEditable&&!isDetail){event.preventDefault();search.focus();}
  });
  globalThis.AcademicBoardPreview={mode:'service',get itemCount(){return partition.active.length;},get importantCount(){return partition.important.length;},get trashCount(){return decisions.trashItems().length;}};

  function refreshChrome(){
    const area=document.querySelector('.side-courses');area?.querySelectorAll('a').forEach(a=>a.remove());
    for(const [key,name] of unique){const item=courseItems().find(i=>M.courseKey(i)===key),a=link(name,'tasks.html','course-nav');a.dataset.courseKey=key;a.prepend(dot(item));area?.append(a);}
    const subtitle=document.querySelector('.page-heading .subtitle');if(subtitle)subtitle.textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:timezone,dateStyle:'full'}).format(now);
    const range=M.rangeDates('week',now,timezone);const dateNav=document.querySelector('.date-nav');if(dateNav)dateNav.replaceChildren(el('span','','本周'),el('span','',range.startDate.slice(5)+' — '+range.endDate.slice(5)));
  }
  globalThis.AcademicBoardRefresh=next=>{
    snapshot=next;now=new Date(next.now);timezone=next.timezone;semester=next.semester||{};
    decisions=readDecisions();partition=decisions.partition({now,timezone});
    originalPartition=M.partition(snapshot.items,{now,timezone});automaticFocusIds=new Set(originalPartition.important.map(i=>i.id));
    unique=new Map(courseItems().map(i=>[M.courseKey(i),M.courseName(i)]));
    courseSelect.replaceChildren();for(const [key,name] of [['all','全部课程'],...unique]){const option=el('option','',name);option.value=key;courseSelect.append(option);}
    if(!unique.has(state.course))state.course='all';courseSelect.value=state.course;
    if(state.range!=='custom')Object.assign(state,M.rangeDates(state.range,now,timezone,semester));syncDateControls();
    time=M.timeProgress(now,timezone,semester);timeStrip.replaceChildren(progressBar('本周时间',time.week.percent,time.week.startDate.slice(5)+' — '+time.week.endDate.slice(5),'week-progress'),el('div','context-progress muted','学期未设置'));
    refreshChrome();render();renderUrgency();renderDetail();globalThis.AcademicMotion?.progress(timeStrip);
  };
  globalThis.AcademicBoardClear=()=>{snapshot={...snapshot,items:[]};decisions=readDecisions();partition=decisions.partition({now,timezone});cards?.replaceChildren();poolList?.replaceChildren();document.querySelector('.side-courses')?.querySelectorAll('a').forEach(a=>a.remove());};
  refreshChrome();updateNavigation();

};
globalThis.AcademicBoardStart();
