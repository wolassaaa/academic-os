/* GSAP core is the sole motion engine. Content remains readable without JS. */
(function(){
  'use strict';
  const G=globalThis.gsap;if(!G)return;
  let enabled=true,renderContext=null,staticContext=null,panelTween=null,scrollTween=null,streamRoot=null,streamVisible=false;
  let streamTweens=[];const shredding=new Set();
  const media=G.matchMedia();
  G.defaults({duration:.28,ease:'power2.out',overwrite:'auto'});
  function killStream(){for(const {tween} of streamTweens)tween.kill();streamTweens=[];}
  function syncStream(){
    if(!streamRoot)return;
    const focus=streamRoot.matches(':focus-within');
    const paused=!enabled||streamRoot.hidden||!streamVisible||document.hidden||streamRoot.dataset.paused==='true'||streamRoot.matches(':hover')||focus;
    streamRoot.dataset.motionPaused=String(paused);
    for(const {track,tween,reverse} of streamTweens){
      if(focus)tween.progress(reverse?1:0).pause();
      else if(paused)tween.pause();else tween.play();
      track.dataset.gsapPaused=String(paused);
    }
  }
  const observer=new IntersectionObserver(entries=>{for(const e of entries)if(e.target===streamRoot){streamVisible=e.isIntersecting;syncStream();}},{threshold:0});
  function attachStream(root){
    killStream();if(streamRoot&&streamRoot!==root)observer.unobserve(streamRoot);streamRoot=root;observer.observe(root);
    const rect=root.getBoundingClientRect();streamVisible=rect.bottom>0&&rect.top<innerHeight;
    root.dataset.motion='gsap';
    for(const track of root.querySelectorAll('.stream-track')){
      if(!enabled||track.classList.contains('still')){G.set(track,{xPercent:0});continue;}
      const reverse=!!track.closest('.lane-1'),duration=parseFloat(track.style.getPropertyValue('--stream-seconds'))||42;
      const tween=G.fromTo(track,{xPercent:reverse?-50:0},{xPercent:reverse?0:-50,duration,ease:'none',repeat:-1,paused:true,overwrite:true});
      streamTweens.push({track,tween,reverse});
    }
    syncStream();
  }
  function progress(root){
    const fills=[...root.querySelectorAll('[data-progress-fill]')];
    for(const fill of fills){const target=Math.max(0,Math.min(1,Number(fill.dataset.progressFill)/100));G.killTweensOf(fill);if(!enabled)G.set(fill,{scaleX:target});else G.fromTo(fill,{scaleX:0},{scaleX:target,duration:.65,ease:'power2.out'});}
  }
  function render(root,{positions=null}={}){
    if(renderContext)renderContext.revert();
    renderContext=G.context(()=>{
      if(!enabled)return;
      const nodes=[...root.querySelectorAll('.task-card,.task-row')].filter(n=>n.getBoundingClientRect().top<innerHeight+80);
      if(positions){
        for(const node of nodes){const old=positions.get(node.dataset.itemId),rect=node.getBoundingClientRect();if(old)G.fromTo(node,{x:old.left-rect.left,y:old.top-rect.top},{x:0,y:0,duration:.28,clearProps:'transform'});else G.fromTo(node,{opacity:0,y:7},{opacity:1,y:0,duration:.2,clearProps:'opacity,transform'});}
      }else{
        if(nodes.length)G.fromTo(nodes,{opacity:0,y:9},{opacity:1,y:0,duration:.24,stagger:.025,clearProps:'opacity,transform'});
        for(const line of root.querySelectorAll('.ring-line')){const target=line.style.strokeDashoffset;G.fromTo(line,{strokeDashoffset:182.2},{strokeDashoffset:target,duration:.55,ease:'power2.out'});}
      }
    },root);
  }
  // Focusable surfaces stay keyboard-visible throughout their short entrance.
  function panel(root){if(panelTween)panelTween.progress(1).kill();G.killTweensOf(root);G.set(root,{clearProps:'opacity,visibility,transform'});if(!enabled)return;panelTween=G.fromTo(root,{opacity:0,y:7},{opacity:1,y:0,duration:.22,clearProps:'opacity,transform'});}
  function scrollTo(root){
    if(scrollTween)scrollTween.kill();
    const end=Math.max(0,root.getBoundingClientRect().top+scrollY-24);
    if(!enabled){window.scrollTo({top:end,behavior:'instant'});return;}
    const state={y:scrollY};scrollTween=G.to(state,{y:end,duration:.38,ease:'power2.out',onUpdate:()=>window.scrollTo({top:state.y,behavior:'instant'}),onComplete:()=>{scrollTween=null;}});
  }
  function shred(node){
    if(!enabled||!node?.isConnected)return Promise.resolve();
    const rect=node.getBoundingClientRect();if(rect.bottom<=0||rect.top>=innerHeight)return Promise.resolve();
    G.killTweensOf(node);
    const stage=document.createElement('div');stage.className='shred-stage';stage.setAttribute('aria-hidden','true');stage.inert=true;
    Object.assign(stage.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:(rect.height+75)+'px'});
    const window=document.createElement('div');window.className='shred-window';window.style.height=rect.height+'px';
    const slot=document.createElement('div');slot.className='shred-slot';slot.style.top=(rect.height-3)+'px';
    const strips=[],output=[],count=innerWidth<=600?6:8;let color=getComputedStyle(node).backgroundColor;
    if(color==='rgba(0, 0, 0, 0)')color=getComputedStyle(node.parentElement).backgroundColor;
    for(let i=0;i<count;i++){
      const strip=document.createElement('div');strip.className='shred-strip';strip.style.clipPath=`inset(0 ${100-(i+1)*100/count+.35}% 0 ${i*100/count+.35}%)`;
      const copy=node.cloneNode(true);for(const e of [copy,...copy.querySelectorAll('*')]){e.removeAttribute('id');e.removeAttribute('data-item-id');e.removeAttribute('tabindex');}
      Object.assign(copy.style,{margin:'0',width:rect.width+'px',height:rect.height+'px',transform:'none',opacity:'1',visibility:'visible',backgroundColor:color});
      strip.append(copy);window.append(strip);strips.push(strip);
      const paper=document.createElement('i');paper.className='shred-output';Object.assign(paper.style,{left:(i*100/count+.35)+'%',width:(100/count-.7)+'%',top:rect.height+'px',backgroundColor:color});stage.append(paper);output.push(paper);
    }
    stage.append(window,slot);document.body.append(stage);G.set(node,{autoAlpha:0});
    return new Promise(resolve=>{
      let finished=false;const job={timeline:null,finish(){if(finished)return;finished=true;stage.remove();if(node.isConnected)G.set(node,{clearProps:'opacity,visibility'});shredding.delete(job);resolve();}};
      const timeline=G.timeline({defaults:{ease:'power2.in'},onComplete:job.finish,onInterrupt:job.finish});job.timeline=timeline;shredding.add(job);
      timeline.fromTo(slot,{scaleX:.92,autoAlpha:0},{scaleX:1,autoAlpha:1,duration:.08},0)
        .to(strips,{y:rect.height+12,rotation:i=>(i%3-1)*.5,stagger:.015,duration:.43},.04)
        .fromTo(output,{y:-16,scaleY:.1,autoAlpha:0},{y:20,scaleY:1,autoAlpha:1,rotation:i=>(i%3-1)*5,transformOrigin:'top center',stagger:.012,duration:.23,ease:'power1.out'},.23)
        .to(output,{y:65,autoAlpha:0,stagger:.012,duration:.2},.45)
        .to(slot,{autoAlpha:0,duration:.12},.59);
    });
  }
  media.add({desktop:'(min-width: 801px)',mobile:'(max-width: 800px)',reduce:'(prefers-reduced-motion: reduce)'},context=>{
    enabled=!context.conditions.reduce;
    if(!enabled)for(const job of [...shredding]){job.timeline.kill();job.finish();}
    if(renderContext){renderContext.revert();renderContext=null;}
    if(staticContext){staticContext.revert();staticContext=null;}
    if(panelTween){panelTween.progress(1).kill();panelTween=null;}
    if(scrollTween){scrollTween.kill();scrollTween=null;}
    if(streamRoot)attachStream(streamRoot);
    for(const fill of document.querySelectorAll('[data-progress-fill]'))G.set(fill,{scaleX:Number(fill.dataset.progressFill)/100});
  });
  function interactive(target){return target instanceof Element?target.closest('.button:not(:disabled),.task-card,.gallery-tile'):null;}
  document.addEventListener('pointerover',event=>{const node=interactive(event.target);if(!enabled||!node||node.contains(event.relatedTarget))return;G.to(node,{y:node.classList.contains('task-card')?-2:0,scale:node.classList.contains('button')?1.015:1,duration:.16});});
  document.addEventListener('pointerout',event=>{const node=interactive(event.target);if(!node||node.contains(event.relatedTarget))return;G.to(node,{y:0,scale:1,duration:enabled?.18:0,clearProps:'transform'});});
  document.addEventListener('pointerdown',event=>{const node=interactive(event.target);if(enabled&&node?.classList.contains('button'))G.to(node,{scale:.97,duration:.1});});
  document.addEventListener('pointerup',event=>{const node=interactive(event.target);if(node?.classList.contains('button'))G.to(node,{scale:1,duration:enabled?.16:0,clearProps:'transform'});});
  for(const type of ['pointerover','pointerout','focusin','focusout'])document.addEventListener(type,()=>syncStream());
  document.addEventListener('visibilitychange',syncStream);
  for(const type of ['wheel','touchstart','keydown'])addEventListener(type,()=>{if(scrollTween){scrollTween.kill();scrollTween=null;}},{passive:true});
  document.addEventListener('DOMContentLoaded',()=>{
    staticContext=G.context(()=>{
      if(!enabled)return;
      const intro=G.timeline({defaults:{duration:.3,ease:'power2.out'}});
      const title=document.querySelector('.page-heading,.auth-form');
      if(title)intro.from(title,{y:10,autoAlpha:0,clearProps:'opacity,visibility,transform'},0);
      if(!globalThis.AcademicBoardPreview){
        const surfaces=[...document.querySelectorAll('.workflow-nav,.surface,.settings-intro,.gallery-tile')].filter(n=>!n.hidden&&!n.matches('[data-settings-panel]')&&n.getBoundingClientRect().top<innerHeight);
        if(surfaces.length)intro.from(surfaces,{y:12,autoAlpha:0,stagger:.035,clearProps:'opacity,visibility,transform'},.06);
      }
      progress(document);
    });
    document.documentElement.dataset.motionReady='true';
  });
  addEventListener('pagehide',event=>{for(const job of [...shredding]){job.timeline.kill();job.finish();}if(event.persisted)return;killStream();observer.disconnect();renderContext?.revert();staticContext?.revert();media.revert();});
  globalThis.AcademicMotion={render,panel,progress,scrollTo,shred,attachStream,syncStream,version:G.version,get enabled(){return enabled;},get shredCount(){return shredding.size;},get streamActive(){return streamTweens.filter(({tween})=>!tween.paused()).length;}};
})();
