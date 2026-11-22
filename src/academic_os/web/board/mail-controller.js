(function(root){
  'use strict';
  const A=root.AcademicMailAdapter,M=root.AcademicBoardModel,main=document.querySelector('main'),status=document.querySelector('.mail-status-text'),refresh=document.querySelector('.mail-refresh'),login=document.querySelector('.mail-login');
  const isDetail=location.pathname.endsWith('mail-detail.html'),params=new URLSearchParams(location.search);
  let generation=0,good=null,goodQuery='',zone='UTC';
  let query={q:params.get('q')||'',account:params.get('account')||'',review:params.get('review')||'all',date_mode:params.get('date_mode')||'all',start:params.get('start')||'',end:params.get('end')||'',offset:Number(params.get('offset')||0)};
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null){if(text instanceof Node)n.append(text);else n.textContent=text;}return n;};
  function link(text,url,cls='button'){const n=el('a',cls,text);n.href=url;return n;}
  function button(text,fn){const n=el('button','button tonal',text);n.type='button';n.addEventListener('click',fn);return n;}
  function qs(q=query){const p=new URLSearchParams();for(const [k,v] of Object.entries(q))if(v!==''&&!(k==='offset'&&v===0))p.set(k,v);return p;}
  function detailHref(key){const p=qs();p.set('key',key);return 'mail-detail.html?'+p;}
  function date(value){const d=M.validDate(value);return d?new Intl.DateTimeFormat('zh-CN',{timeZone:zone,dateStyle:'medium',timeStyle:'short'}).format(d):'时间未提供';}
  function workbench(item){const state=root.AcademicMailReviewModel?.reviewState(item)||item.mail_review?.state;const view=state==='pending'?'mail_pending':state==='ignored'?'mail_ignored':'formal';return link(view==='formal'?'在工作台查看事项':'在工作台逐条核对','/?'+new URLSearchParams({page:'items',item:item.id,view}),'button tonal');}
  function empty(title,text){const box=el('div','mail-empty');box.append(el('h2','',title),el('p','muted',text));return box;}
  function user(name){document.querySelectorAll('.live-username').forEach(n=>n.textContent=name||'我的空间');}
  function counts(m){const c=m.review_counts||{};return '待核对 '+(c.pending||0)+' · 已确认 '+(c.confirmed||0)+' · 已忽略 '+(c.ignored||0);}
  const form=document.querySelector('.mail-filter-form'),list=document.querySelector('.mail-list'),pager=document.querySelector('.mail-pager'),detail=document.querySelector('.mail-detail-content');
  function addField(label,node){const wrapper=el('label','mail-field');wrapper.append(el('span','',label),node);form.append(wrapper);return node;}
  let search,account,review,preset,start,end,error;
  if(form){
    search=el('input');search.type='search';search.maxLength=500;search.value=query.q;search.placeholder='搜索主题、发件人或正文';search.setAttribute('aria-label','搜索邮件');addField('搜索',search);
    account=el('select');account.setAttribute('aria-label','邮件账号');addField('账号',account);
    review=el('select');review.setAttribute('aria-label','邮件核对状态');for(const [v,t] of [['all','全部邮件'],['pending','待核对'],['ignored','已忽略']]){const o=el('option','',t);o.value=v;review.append(o);}review.value=query.review;addField('核对状态',review);
    preset=el('select');preset.setAttribute('aria-label','收件日期范围');for(const [v,t] of [['all','全部收件时间'],['today','今天收到'],['custom','自定义收件日期'],['undated','收件时间未知']]){const o=el('option','',t);o.value=v;preset.append(o);}preset.value=query.date_mode;addField('收件日期',preset);
    start=el('input');end=el('input');start.type=end.type='date';start.value=query.start;end.value=query.end;start.addEventListener('change',()=>preset.value='custom');end.addEventListener('change',()=>preset.value='custom');start.setAttribute('aria-label','收件开始日期');end.setAttribute('aria-label','收件结束日期');addField('从',start);addField('到',end);
    const submit=el('button','button primary','应用筛选');submit.type='submit';form.append(submit,button('清除筛选',()=>{query={q:'',account:'',review:'all',date_mode:'all',start:'',end:'',offset:0};search.value=account.value=start.value=end.value='';review.value=preset.value='all';load();}));
    error=el('p','mail-filter-error');error.role='alert';error.hidden=true;form.append(error);
    form.addEventListener('submit',event=>{event.preventDefault();error.hidden=true;if(preset.value==='custom'&&(!M.validDay(start.value)||!M.validDay(end.value)||start.value>end.value)){error.textContent='请选择有效的开始和结束日期，结束日期应不早于开始日期。';error.hidden=false;return;}query={q:search.value,account:account.value,review:review.value,date_mode:preset.value,start:preset.value==='custom'?start.value:'',end:preset.value==='custom'?end.value:'',offset:0};load();});
  }
  function renderList(data){
    zone=data.timezone;
    const selected=query.account;account.replaceChildren();for(const v of ['',...new Set([...data.accounts,...(selected?[selected]:[])])]){const o=el('option','',v||'全部账号');o.value=v;account.append(o);}account.value=selected;
    list.replaceChildren();
    for(const m of data.items){
      const article=el('article','mail-card');article.dataset.mailKey=m.key;
      const title=link(m.subject||'（无主题）',detailHref(m.key),'mail-title');
      article.append(el('div','mail-card-meta',date(m.received_at)+' · '+(m.account||'账号未提供')),el('h2','',title),el('p','mail-from',m.from||'发件人未提供'),el('p','mail-excerpt',m.body_excerpt||'（邮件没有正文）'),el('div','mail-card-footer',counts(m)));
      if(m.body_truncated)article.append(link('摘录已截断 · 打开完整原文',detailHref(m.key),'button quiet'));
      list.append(article);
    }
    if(!data.items.length){const box=empty(data.total?'这一页没有邮件':'没有符合条件的邮件','可清除筛选或调整收件日期；这不代表来源已经完整同步。');box.append(button('清除筛选并查看全部',()=>{query={q:'',account:'',review:'all',date_mode:'all',start:'',end:'',offset:0};search.value=account.value=start.value=end.value='';review.value=preset.value='all';load();}));list.append(box);}
    pager.replaceChildren();const previous=button('上一页',()=>{query.offset=Math.max(0,query.offset-data.limit);load();}),next=button('下一页',()=>{query.offset+=data.limit;load();});previous.disabled=data.offset===0;next.disabled=data.offset+data.limit>=data.total;
    pager.append(previous,el('span','',data.total?'第 '+(data.offset+1)+'–'+Math.min(data.offset+data.items.length,data.total)+' 封 / 共 '+data.total+' 封':'共 0 封'),next);
    const health=data.health,sources=Array.isArray(health?.sources)?health.sources:[];document.querySelector('.mail-source-note').textContent=health?.data_stale?'后台心跳过期，邮件可能尚未更新。':sources.some(s=>s&&(s.status!=='ok'||s.stale))?'部分来源读取失败或过期，请检查来源。':'以下为服务已收集的邮件，不代表邮箱完整性已核实。';
  }
  function renderDetail(data){
    zone=data.timezone||'UTC';detail.replaceChildren();document.querySelector('.mail-back').href='mail.html?'+qs();
    detail.append(el('div','mail-detail-heading'));
    const header=detail.firstChild;header.replaceChildren(el('span','pill',data.account||'账号未提供'),el('h1','',data.subject||'（无主题）'),el('p','muted',(data.from||'发件人未提供')+' · '+date(data.received_at)+' · '+zone));
    const original=el('details','mail-original-disclosure'),body=data.body||'（邮件没有正文）';original.open=body.length<=1000;original.append(el('summary','mail-original-toggle','完整原文 · '+body.length+' 字'),el('pre','mail-original',body));detail.append(el('p','mail-reading-note','原文内容 · 未经 AI 总结。邮件文字只作为资料展示，不作为操作指令。'),original);
    const section=el('section','mail-candidates');section.append(el('h2','','关联候选与邮件待办'),el('p','muted','逐条核对日期证据；未知截止留空。未确认候选不产生截止提醒。'));
    for(const item of data.linked_items){const card=el('article','mail-candidate');card.dataset.itemId=item.id;const state=root.AcademicMailReviewModel?.reviewState(item)||item.mail_review?.state,formal=state==='formal'||state==='confirmed';
      card.append(el('span','pill',state==='confirmed'?'邮件待办':formal?'正式事项':state==='ignored'?'已忽略':'待核对'),el('h3','',item.title),el('p','','当前截止 · '+(item.source_due_at?date(item.source_due_at)+' · '+zone:'未知，不补造时刻')));
      if(item.review_state==='conflict')card.append(el('p','mail-conflict','截止存在冲突，当前值不代表冲突已解决。'));
      for(const e of item.evidence||[]){const block=el('blockquote','mail-evidence',e.evidence_text||e.text||'没有日期原文');block.append(el('small','',String(e.source||'来源未提供')+' · 来源日期：'+(e.due_at?date(e.due_at)+' · '+zone:'未提供')));card.append(block);}
      card.append(workbench(item));if(formal)card.append(link('在看板查看事项','task.html?'+new URLSearchParams({item:item.id,mail_return:detailHref(data.key)}),'button quiet'));section.append(card);
    }
    if(!data.linked_items.length)section.append(empty('没有关联候选','原邮件保留；不根据模糊文字补造截止。'));detail.append(section);root.AcademicMailReview?.decorate(data);
  }
  async function load(){
    if(root.AcademicMailReviewBusy)return null;
    const ticket=++generation,requestQuery={...query},signature=JSON.stringify(requestQuery);document.body.dataset.serviceState='loading';status.textContent='正在读取已收集的邮件…';login.hidden=true;refresh.disabled=true;root.AcademicMailReview?.setEnabled();
    if(!good||goodQuery!==signature){if(list)list.replaceChildren();if(pager)pager.replaceChildren();if(detail)detail.replaceChildren();main.hidden=!form;}else document.body.dataset.serviceState='refreshing';
    try{
      const data=isDetail?await A.detail(params.get('key')):await A.list(requestQuery);if(ticket!==generation)return;
      good=data;goodQuery=signature;user(data.username);document.querySelector('.sync-status').textContent='本地阅读';if(isDetail)renderDetail(data);else renderList(data);
      main.hidden=false;document.body.dataset.serviceState='ready';status.textContent='本页读取 '+date(new Date().toISOString())+' · '+zone+(isDetail&&data.canReview?' · 可逐条核对':' · 仅阅读已收集邮件');
      if(!isDetail)history.replaceState(null,'','mail.html?'+qs());root.AcademicMotion?.panel(main);
    }catch(e){
      if(ticket!==generation)return;
      if(['auth','remote'].includes(e.code)){root.AcademicMailReview?.expire();good=null;goodQuery='';main.hidden=true;list?.replaceChildren();detail?.replaceChildren();user('我的空间');login.hidden=false;login.textContent=e.code==='remote'?'返回只读看板':'前往登录';login.href=e.code==='remote'?'index.html':'/';document.body.dataset.serviceState='expired';}
      else document.body.dataset.serviceState=good&&goodQuery===signature?'stale':'error';
      const reasons={auth:'登录已过期，请重新登录。',remote:'远程看板不展示邮件原文，请在本地工作台阅读。',missing:'找不到这封邮件，原链接可能已失效。',invalid:'筛选参数不完整，请返回邮件列表调整。',schema:'邮件数据格式不完整，请刷新核对。',offline:'连接失败，请检查本地服务后重试。'};
      status.textContent=(reasons[e.code]||'邮件读取失败，请稍后重试。')+(good&&goodQuery===signature?' 已保留上次读取结果，数据可能过期。':'');
    }finally{if(ticket===generation){refresh.disabled=false;root.AcademicMailReview?.setEnabled();}}
    root.AcademicMailView={state:document.body.dataset.serviceState,offset:query.offset};return document.body.dataset.serviceState==='ready'?good:null;
  }
  root.AcademicMailCurrent=()=>good;
  root.AcademicMailRender=()=>{if(good&&isDetail)renderDetail(good);};
  root.AcademicMailApplyReview=receipt=>{if(!good?.linked_items)return false;const old=good.linked_items.find(i=>i.id===receipt.id);if(!old||old.version>receipt.version)return false;good.linked_items=good.linked_items.map(i=>i.id===receipt.id?receipt:i);renderDetail(good);return true;};
  root.AcademicMailRefresh=load;
  root.AcademicMailExpire=()=>{++generation;good=null;goodQuery='';main.hidden=true;detail?.replaceChildren();list?.replaceChildren();user('我的空间');root.AcademicMailReview?.expire();login.hidden=false;login.textContent='前往登录';login.href='/';document.body.dataset.serviceState='expired';status.textContent='登录已过期，请重新登录。';refresh.disabled=false;};
  refresh.addEventListener('click',load);load();
})(globalThis);
