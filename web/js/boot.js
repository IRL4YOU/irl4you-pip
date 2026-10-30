(async function tickLoop(){try{if(!document.hidden)await tick();}catch(e){}
setTimeout(tickLoop,2000);})();document.addEventListener("visibilitychange",()=>{if(!document.hidden){tick();sendLoad();}});
