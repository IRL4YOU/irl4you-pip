/* Geschwindigkeits- und Verbindungstest je Leitung (Karte "Verbindungen"): Knopf je Leitung, "Alle Leitungen testen", Ergebnis und Fehlermeldung darunter. */
let utData={tests:{},busy:false,health:null,sending:false},utTimer=0;
async function utLoad(){try{utData=await srtlaCall("GET","/api/uplinktest");}catch(e){return;}
utRender();clearTimeout(utTimer);if(utData.busy)utTimer=setTimeout(utLoad,1500);}
function utText(t){
if(!t)return{cls:"",html:""};
if(t.state==="wartet")return{cls:"run",html:esc("Wartet …")};
if(t.state==="läuft")return{cls:"run",html:esc("Test läuft: "+(t.step||"Start"))};
if(t.state==="fehler")return{cls:"err",html:"⚠ "+esc(t.text||"Test fehlgeschlagen")+(t.hint?' <span class="muted">'+esc(t.hint)+"</span>":"")};
if(t.state==="fertig"){const p=[];
if(t.down_mbit!=null)p.push("↓ "+fmt(t.down_mbit,1)+" Mbit/s");
if(t.up_mbit!=null)p.push("↑ "+fmt(t.up_mbit,1)+" Mbit/s");
if(t.rtt_ms!=null)p.push(esc("Antwortzeit "+t.rtt_ms+" ms"));
return{cls:"",html:p.join(" · ")+(t.during_send?' <span class="muted">'+esc("während der Sendung gemessen")+"</span>":"")};}
return{cls:"",html:""};}
function utRender(){
const tests=utData.tests||{},busy=!!utData.busy;
document.querySelectorAll("[data-utres]").forEach(el=>{const r=utText(tests[el.dataset.utres]);el.className="utres"+(r.cls?" "+r.cls:"");if(el.dataset.h!==r.html){el.dataset.h=r.html;el.innerHTML=r.html;}});
document.querySelectorAll("button[data-ut]").forEach(b=>{b.disabled=busy;});
const all=$("ut_all");if(all)all.disabled=busy;}
async function utStart(body){
if(utData.sending&&!confirm("Während der Sendung belegt der Test die Leitung für einige Sekunden, das Bild kann kurz schlechter werden. Trotzdem testen?"))return;
$("neterr").textContent="";
try{utData=await srtlaCall("POST","/api/uplinktest",body);}catch(x){$("neterr").textContent=x.message;return;}
utRender();clearTimeout(utTimer);utTimer=setTimeout(utLoad,1200);}
$("b_ups").addEventListener("click",e=>{const b=e.target.closest&&e.target.closest("button[data-ut]");if(b&&!b.disabled)utStart({iface:b.dataset.ut});});
$("ut_all").addEventListener("click",()=>utStart({all:true}));
utLoad();
setInterval(()=>{if(document.hidden||utData.busy)return;const c=$("netcard");if(c&&c.open&&typeof srtlaLoad==="function")srtlaLoad();},10000);
