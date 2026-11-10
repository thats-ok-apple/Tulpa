'use strict';
window.TULPA_MCP_ONLY=true;
const $=id=>document.getElementById(id);
const state={ready:true,busy:false,loading:false,watchBusy:false};
const READ_FIELDS={qq_per_chat:'read-qq-per-chat',wechat_per_chat:'read-wechat-per-chat'};
let jobStarting=null,toastTimer,homeBusy=false;
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5000);}
async function watchApi(path,body,method){const r=await fetch(path,{method:method||(body===undefined?'GET':'POST'),cache:'no-store',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(30000)});const d=await r.json();if(!r.ok)throw Error(d.detail||'请求未完成');return d;}
function syncControls(){syncDataControls(Boolean(state.loading||state.watchBusy||jobStarting));}
function readLimits(){return Object.fromEntries(Object.entries(READ_FIELDS).map(([key,id])=>[key,$(id).valueAsNumber]));}
function showReadLimits(){const valid=Object.values(READ_FIELDS).every(id=>!$('read-enable-'+id.split('-')[1]).checked||$('read-all-'+id.split('-')[1]).checked||$(id).validity.valid);$('read-window-summary').hidden=valid;$('read-window-summary').textContent=valid?'':'每会话条数上限须为 1–10000 之间的整数。';}
async function loadReadOptions(){const rules=await watchApi('/api/read-options');for(const [key,id]of Object.entries(READ_FIELDS)){const r=rules[key];$(id).min=r.min;$(id).max=r.max;$(id).value=r.default;}showReadLimits();}
async function monitorWatchJob(id){state.watchBusy=true;syncControls();try{while(true){const job=await watchApi('/api/watch-jobs/'+id);renderImportProgress(job);$('read-progress').hidden=false;$('read-progress').textContent=job.events.join('\n');if(job.status!=='running'){if(job.error)throw Error(job.error);$('read-progress').textContent+='\n'+(job.result?.summary||'处理完成');break;}await new Promise(r=>setTimeout(r,800));}}finally{state.watchBusy=false;syncControls();await refreshHome();}}
async function openData(){if(state.loading)return;if(!$('data-dialog').open)$('data-dialog').showModal();state.loading=true;syncControls();try{await loadDataSettings();await loadReadOptions();await loadSync();}catch(e){toast(e.message);}finally{state.loading=false;syncControls();}}
async function loadSync(){const s=await watchApi('/api/refresh-status');$('sync-summary').textContent=s.platforms.map(p=>(p.platform==='qq'?'QQ':'微信')+'：'+p.status+' '+(p.detail||'')).join('；');$('auto-refresh').value=s.auto_refresh.minutes;$('auto-refresh-stickers').checked=s.auto_refresh.load_stickers;const job=s.running_jobs?.[0];if(job&&!state.watchBusy)void monitorWatchJob(job.id).catch(e=>toast(e.message));}
async function refreshHome(){if(homeBusy)return;homeBusy=true;try{const m=await watchApi('/api/mcp');$('data-status').textContent=Object.entries(m.data_platforms||{}).map(([p,n])=>(p==='qq'?'QQ':'微信')+' '+n.toLocaleString()+' 条消息').join(' · ')||'尚未导入聊天记录';window.dispatchEvent(new CustomEvent('tulpa-connections',{detail:m}));$('lite-error').textContent='';}catch(e){$('lite-error').textContent=e.message;}finally{homeBusy=false;}}
function fillOnebot(s){$('lite-onebot-url').value=s.sender_url;$('lite-events-url').value=s.events_url||'';$('lite-events-token').value='';$('lite-events-token').placeholder=s.has_events_token?'已保存 · 留空保留':'WebSocket 节点 Token';$('lite-onebot-token').value='';$('lite-onebot-token').placeholder=s.has_sender_token?'已保存 · 留空保留':'访问 Token（如接口需要）';$('lite-onebot-current').textContent=s.sender_url?'当前保存的 HTTP：'+s.sender_url+(s.events_url?'；事件：'+s.events_url:'；未配置事件连接。'):'尚未配置 OneBot。';}
const snow=window.TulpaSnowLuma.mount($('lite-snowluma'),{prefix:'lite',onSaved:fillOnebot});
async function openOnebot(){snow.reset();$('lite-onebot-manual').open=false;try{const [s,p]=await Promise.all([watchApi('/api/desktop/status'),watchApi('/api/desktop/preferences')]);fillOnebot(s.settings);$('lite-background').checked=p.background;$('lite-background-label').hidden=!s.owned;if(!$('lite-onebot').open)$('lite-onebot').showModal();}catch(e){toast(e.message);}}
async function testOnebot(){const r=await watchApi('/api/desktop/onebot/test',{});$('lite-onebot-result').textContent=r.message;}
$('open-data').onclick=openData;$('open-onebot').onclick=openOnebot;
window.addEventListener('tulpa-open-settings',openOnebot);
$('lite-onebot-form').onsubmit=async e=>{e.preventDefault();const b=$('lite-onebot-form').querySelector('[type="submit"]');if(b.disabled)return;b.disabled=true;try{fillOnebot(await watchApi('/api/desktop/settings',{sender_url:$('lite-onebot-url').value,sender_token:$('lite-onebot-token').value,events_url:$('lite-events-url').value,events_token:$('lite-events-token').value},'PUT'));await testOnebot();}catch(e){$('lite-onebot-result').textContent=e.message;}finally{b.disabled=false;}};
$('lite-onebot-test').onclick=()=>testOnebot().catch(e=>toast(e.message));
$('lite-background').onchange=()=>watchApi('/api/desktop/preferences',{background:$('lite-background').checked,mode:'mcp'},'PUT').catch(e=>toast(e.message));
$('lite-onebot').addEventListener('close',()=>{snow.reset();$('lite-onebot-token').value='';$('lite-events-token').value='';});
for(const id of ['auto-refresh','auto-refresh-stickers'])$(id).onchange=()=>watchApi('/api/auto-refresh',{minutes:Number($('auto-refresh').value),load_stickers:$('auto-refresh-stickers').checked}).catch(e=>toast(e.message));
for(const p of ['qq','wechat'])$('reconnect-'+p).onclick=()=>watchApi('/api/sync/reconnect',{platform:p}).then(()=>toast('进度已重建，等待下一次读取')).catch(e=>toast(e.message));
document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
// The MCP edition imports directly from clients; legacy file imports belong to the full app.
$('import-form').closest('details').hidden=true;
$('voice-settings').hidden=true;
Object.values(READ_FIELDS).forEach(id=>$(id).oninput=showReadLimits);
setInterval(()=>{if(!document.hidden)void refreshHome();},4000);
Promise.resolve().then(async()=>{await loadReadOptions();await refreshHome();const d=await watchApi('/api/desktop/status');$('lite-version').textContent='Tulpa MCP '+d.version;const params=new URLSearchParams(location.search),anchor=params.get('anchor');if(anchor&&/^\d+$/.test(anchor))await openChat(Number(anchor));}).catch(e=>toast(e.message));
