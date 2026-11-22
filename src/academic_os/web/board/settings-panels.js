/* Display-only category navigation. No settings persistence or service requests. */
(function(){
  'use strict';
  const names={connections:'连接来源',courses:'外观与课程',hermes:'Hermes 与 AI',maintenance:'数据与维护'};
  const tabs=[...document.querySelectorAll('.settings-tabs a')];
  const panels=[...document.querySelectorAll('[data-settings-panel]')];
  function select(id){
    const selected=Object.hasOwn(names,id)?id:'connections';
    for(const panel of panels)panel.hidden=panel.id!==selected;
    for(const tab of tabs){const active=tab.hash==='#'+selected;tab.classList.toggle('selected',active);if(active)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');}
    globalThis.AcademicMotion?.panel(document.getElementById(selected));
    globalThis.AcademicSettingsPanels={mode:'service',panel:selected};
  }
  for(const tab of tabs){const id=tab.hash.slice(1);tab.setAttribute('aria-label',names[id]);tab.addEventListener('click',event=>{event.preventDefault();select(id);history.replaceState(null,'','#'+id);});}
  select(location.hash.slice(1));
  addEventListener('hashchange',()=>select(location.hash.slice(1)));
})();
