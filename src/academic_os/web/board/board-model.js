(function(root){
  'use strict';
  const closed=new Set(['completed','submitted','cancelled']);
  function validDate(value){if(!value)return null;const d=new Date(value);return Number.isFinite(d.getTime())?d:null;}
  function localOrdinal(value,timezone){
    const parts=new Intl.DateTimeFormat('en-US',{timeZone:timezone,year:'numeric',month:'numeric',day:'numeric'}).formatToParts(value);
    const get=k=>Number(parts.find(p=>p.type===k).value);
    return Date.UTC(get('year'),get('month')-1,get('day'))/86400000;
  }
  function dayString(ordinal){return new Date(ordinal*86400000).toISOString().slice(0,10);}
  function validDay(value){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return false;
    const d=new Date(value+'T00:00:00Z');return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===value;
  }
  function rangeDates(preset,now,timezone='Asia/Taipei',term={}){
    const ordinal=localOrdinal(new Date(now),timezone),day=new Date(ordinal*86400000),weekday=day.getUTCDay()||7,monday=ordinal-weekday+1;
    if(preset==='week')return {startDate:dayString(monday),endDate:dayString(monday+6)};
    if(preset==='next-week')return {startDate:dayString(monday+7),endDate:dayString(monday+13)};
    if(preset==='month')return {startDate:dayString(Date.UTC(day.getUTCFullYear(),day.getUTCMonth(),1)/86400000),endDate:dayString(Date.UTC(day.getUTCFullYear(),day.getUTCMonth()+1,0)/86400000)};
    if(preset==='semester'&&validDay(term.startDate)&&validDay(term.endDate))return {startDate:term.startDate,endDate:term.endDate};
    return {startDate:'',endDate:''};
  }
  function timeProgress(now,timezone='Asia/Taipei',term={}){
    const date=new Date(now),ordinal=localOrdinal(date,timezone),parts=new Intl.DateTimeFormat('en-GB',{timeZone:timezone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(date);
    const part=k=>Number(parts.find(p=>p.type===k)?.value||0),fraction=(part('hour')*3600+part('minute')*60+part('second'))/86400;
    const current=ordinal+fraction,range=rangeDates('week',now,timezone);
    const percentage=(start,end)=>Math.round(Math.max(0,Math.min(1,(current-Date.parse(start)/86400000)/((Date.parse(end)-Date.parse(start))/86400000+1)))*100);
    const week={...range,percent:percentage(range.startDate,range.endDate)};
    if(!validDay(term.startDate)||!validDay(term.endDate)||term.startDate>term.endDate)return {week,semester:null};
    const days=(Date.parse(term.endDate)-Date.parse(term.startDate))/86400000+1,totalWeeks=Math.ceil(days/7);
    return {week,semester:{...term,percent:percentage(term.startDate,term.endDate),weekNumber:Math.max(0,Math.min(totalWeeks,Math.floor((ordinal-Date.parse(term.startDate)/86400000)/7)+1)),totalWeeks}};
  }
  function courseKey(item){return item.classification?.course_key||('name:'+(item.classification?.course||item.course||'未分类'));}
  function courseName(item){return item.classification?.course||item.course||'未分类';}
  function formal(item){const mixed=item.manual_origin||(item.evidence||[]).some(e=>!String(e.source||'').startsWith('gmail:'));return !!(mixed||!['pending','ignored'].includes(item.mail_review?.state));}
  function active(item){return !closed.has(item.status)&&formal(item);}
  function submissionFollowup(item){
    if(item.status!=='completed'||item.personal_status?.origin!=='explicit'||item.personal_status.state!=='completed')return [];
    return (item.evidence||[]).filter(e=>['moodle','canvas'].includes(String(e.source||'').split(':')[0])&&['not_started','pending'].includes(e.submission_status));
  }
  function history(items,state='all'){
    return items.filter(i=>formal(i)&&closed.has(i.status)&&(state==='all'||state===i.status||state==='pending-submission'&&submissionFollowup(i).length)).slice().sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||''))||String(a.id).localeCompare(String(b.id)));
  }
  function closedLabel(item){return item.status==='submitted'?(item.personal_status?.state==='submitted'?'自行标记已提交':'来源记录已提交'):item.status==='cancelled'?'已取消':'已完成';}
  function due(value,now){
    if(!value)return {state:'unknown',label:'无截止时间',short:'—',unit:'',fill:0};
    const date=validDate(value);if(!date)return {state:'unknown',label:'日期待核对',short:'?',unit:'',fill:0};
    const hours=(date-new Date(now))/3600000;
    if(hours<=0){const days=Math.max(1,Math.ceil(-hours/24));return {state:'overdue',label:'已逾期 '+days+' 天',short:String(days),unit:'天',fill:1};}
    const days=Math.floor(hours/24);
    const short=hours<24?String(Math.max(1,Math.ceil(hours))):String(days);
    const unit=hours<24?'小时':'天';
    return {state:hours<=24?'urgent':hours<=72?'near':'calm',label:'还剩 '+short+' '+unit,short,unit,fill:Math.min(1,Math.max(0,1-hours/(7*24)))};
  }
  function partition(items,{now=new Date(),timezone='Asia/Taipei',choices={}}={}){
    const ordinal=localOrdinal(new Date(now),timezone),weekday=new Date(ordinal*86400000).getUTCDay();
    const end=ordinal+(7-(weekday||7));
    const list=items.filter(active).slice().sort((a,b)=>{
      const ad=validDate(a.source_due_at),bd=validDate(b.source_due_at);
      const ap=Number(a.priority||0)>=4,bp=Number(b.priority||0)>=4;
      return Number(bp)-Number(ap)||(ad?.getTime()??Infinity)-(bd?.getTime()??Infinity)||String(a.created_at||'').localeCompare(String(b.created_at||''))||String(a.id).localeCompare(String(b.id));
    });
    const important=[],pool=[];
    for(const item of list){const date=validDate(item.source_due_at);(choices[item.id]==='focus'||(choices[item.id]!=='pool'&&((Number(item.priority||0)>=4)||(date&&localOrdinal(date,timezone)<=end)))?important:pool).push(item);}
    important.sort((a,b)=>Number(choices[b.id]==='focus')-Number(choices[a.id]==='focus'));
    return {important,pool,active:list};
  }
  function filter(items,{q='',course='all',view='all',startDate='',endDate='',includeUndated=true,timezone='Asia/Taipei'}={}){
    const term=q.trim().toLocaleLowerCase();
    return items.filter(i=>{
      const d=validDate(i.source_due_at),day=d?dayString(localOrdinal(d,timezone)):null;
      const inRange=day?(!validDay(startDate)||day>=startDate)&&(!validDay(endDate)||day<=endDate):includeUndated;
      return inRange&&(course==='all'||courseKey(i)===course)&&(!term||(i.title+' '+courseName(i)).toLocaleLowerCase().includes(term))&&(view==='all'||view==='scheduled'&&i.scheduled_start||view==='unscheduled'&&!i.scheduled_start||view==='undated'&&!d);
    });
  }
  function deadlineState(item){
    if(item.review_state==='conflict')return {label:'截止有冲突',tone:'warn',help:'当前显示保留值；请核对来源，不代表冲突已解决。'};
    if(!validDate(item.source_due_at))return {label:'截止待确认',tone:'warn',help:'未提供可核对的截止时刻，不补造时间。'};
    if(item.review_state==='ready')return {label:'截止已核对',tone:'ok',help:'沿用现有核对状态；后续来源变化仍需核对。'};
    return {label:'来源截止 · 待核对',tone:'warn',help:'存在来源日期，但尚未完成核对。'};
  }
  function statusFacts(item){
    const labels={pending:'待完成',in_progress:'进行中',completed:'已完成',cancelled:'已取消',submitted:'自行标记已提交',unknown:'未能确认'};
    const personal=item.personal_status;
    const value=personal?.origin==='legacy_completed'?'历史完成（来源未区分）':(Object.hasOwn(labels,personal?.state)?labels[personal.state]:'未单独标记');
    const sourceLabels={not_started:'未提交',pending:'未提交',in_progress:'进行中',submitted:'已提交',completed:'来源标记完成',cancelled:'来源标记取消',unknown:'未能确认'};
    const observations=(item.evidence||[]).filter(e=>Object.hasOwn(e,'submission_status')).map(e=>String(e.source||'来源未提供')+(e.source_id?' / '+String(e.source_id):'')+'：'+(Object.hasOwn(sourceLabels,e.submission_status)?sourceLabels[e.submission_status]:'未能确认'));
    return {personal:value,observations};
  }
  const api={partition,filter,due,courseKey,courseName,validDate,validDay,rangeDates,timeProgress,deadlineState,statusFacts,formal,history,submissionFollowup,closedLabel};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.AcademicBoardModel=api;
})(typeof globalThis!=='undefined'?globalThis:this);
