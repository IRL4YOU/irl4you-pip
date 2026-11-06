(async function tickLoop(){try{if(!document.hidden)await tick();}catch(e){}
setTimeout(tickLoop,2000);})();document.addEventListener("visibilitychange",()=>{if(!document.hidden){tick();sendLoad();}});
(function openFromHash(){function go(){const id=decodeURIComponent((location.hash||"").slice(1));if(!/^[A-Za-z0-9_-]{1,40}$/.test(id))return;const t=document.getElementById(id);if(!t)return;for(let p=t;p;p=p.parentElement)if(p.tagName==="DETAILS")p.open=true;setTimeout(()=>t.scrollIntoView({block:"start"}),50);}
window.addEventListener("hashchange",go);setTimeout(go,600);})();
