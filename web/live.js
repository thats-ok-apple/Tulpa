'use strict';
let liveLastId=0,liveBusy=false,liveDataBusy=false,liveEditing=false,liveRefreshAt=0;
const liveLabels={unsupported:'Mac 尚未支持',starting:'正在启动',listening:'监听中',receiving:'读取新增',retrying:'正在重试',degraded:'实时读取受限',client_stopped:'客户端未运行',needs_baseline:'需先导入'};
const liveShortLabels={unsupported:'Mac 未适配',starting:'启动中',listening:'监听中',receiving:'更新中',retrying:'重试中',degraded:'读取受限',client_stopped:'未运行',needs_baseline:'待导入'};
function liveLabel(p,short=false) {
  if(p.status==='off')return '监听已关闭';
  return (short?liveShortLabels:liveLabels)[p.status]||'状态未知';
}
function liveDetail(p) {
  if(p.status!=='off')return p.detail;
  return '本工具的实时监听已关闭；可在下方选择“数据库监听”并保存。与历史导入的平台勾选无关，也不代表客户端已退出。';
}
function liveClock(value) {return value?new Date(value*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无';}
async function refreshLiveData(upper) {
  if(liveDataBusy)return;
  liveDataBusy=true;liveRefreshAt=Date.now();
  try {
    if(window.TULPA_MCP_ONLY){await refreshHome();}
    else{const refreshed=await call('refresh',[]);setData(refreshed[0],refreshed[1]);await loadWatches();}
    liveLastId=upper;
    await watchApi('/api/live-visible',{through_id:upper});
  } catch(error) {
    // Data panels may lag without making a successful status read look stale
    // or disconnected. The next eligible refresh retries independently.
  } finally {liveDataBusy=false;}
}
async function pollLive() {
  if(liveBusy||document.hidden)return;
  liveBusy=true;
  try {
    const data=await watchApi('/api/live-status');
    $('live-summary').classList.remove('disconnected');
    $('live-summary').replaceChildren(...data.platforms.map(p=>{
      const line=document.createElement('span');line.className='live-line '+p.status;
      const dot=document.createElement('i');dot.setAttribute('aria-hidden','true');
      line.append(dot,document.createTextNode(`${p.platform==='qq'?'QQ':'微信'} ${liveLabel(p,true)}`));
      line.title=liveDetail(p)+'；最后收到 '+liveClock(p.last_received);return line;
    }));
    $('live-summary').title=data.platforms.map(p=>`${p.platform==='qq'?'QQ':'微信'}：${liveLabel(p)}；${liveDetail(p)}；最后收到 ${liveClock(p.last_received)}`).join('\n')+'\n点击查看实时摄取详情';
    $('live-detail').replaceChildren(...data.platforms.map(p=>{
      const line=document.createElement('p');
      const success=p.last_success_at?new Date(p.last_success_at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无';
      line.textContent=`${p.platform==='qq'?'QQ':'微信'}：${liveDetail(p)} 最后收到 ${liveClock(p.last_received)}；最后成功读取 ${success}；异常会话 ${p.affected_conversations||0} 个。${p.error_types?.length?' 原因：'+p.error_types.join('、')+'。':''}${p.next_retry_at?' 下次重试 '+liveClock(p.next_retry_at)+'。':''}`;return line;
    }));
    if(data.images){
      const line=document.createElement('p');const counts=data.images.ocr||{};
      line.textContent=`本地图片文字索引：已完成 ${counts.ready||0} 张，待处理 ${counts.pending||0} 张，失败 ${counts.failed||0} 张。首次覆盖近 ${data.images.history_days} 天；未上传图片。连接入口：本机直连；插件桥接错误独立诊断。`;
      $('live-detail').append(line);
      if(data.images.remote){
        const remote=data.images.remote;const jobs=remote.downloads||{};
        const download=document.createElement('p');
        download.textContent=`QQ 官方图片补取：${remote.enabled?'已开启':'已暂停'}；滚动保留最近 ${remote.days} 天。已补齐 ${jobs.ready||0} 条，待处理 ${jobs.pending||0} 条，正在补取 ${jobs.downloading||0} 条，等待重试 ${jobs.retry||0} 条。只下载图片到本机，不上传图片；原客户端文件不受缓存清理影响。`;
        if(Object.keys(remote.errors||{}).length)download.textContent+=' 当前失败原因：'+Object.entries(remote.errors).map(([key,n])=>key+' '+n+' 条').join('、')+'。';
        $('live-detail').append(download);
      }
    }
    if(!liveEditing) {
      $('live-qq').value=data.settings.qq;$('live-wechat').value=data.settings.wechat;
      $('live-stickers').checked=data.settings.load_stickers;
      $('wechat-read-images').checked=data.settings.wechat_read_images!==false;
    }
    const upper=Math.max(...data.platforms.map(p=>p.last_id||0));
    if(upper>liveLastId && !liveDataBusy && state.ready && !state.busy && !state.loading && Date.now()-liveRefreshAt>5000) {
      // A slow data refresh must never hold the two-second health poll.
      void refreshLiveData(upper);
    }
  } catch(error) {
    $('live-summary').textContent='实时状态连接中断';
    $('live-summary').classList.add('disconnected');
    $('live-summary').title=error.message;
  } finally {liveBusy=false;}
}
$('live-summary').onclick=()=>{
  $('open-data').click();$('live-settings-panel').open=true;
  setTimeout(()=>$('live-settings-panel').scrollIntoView({block:'center',behavior:'smooth'}),100);
};
for(const id of ['live-qq','live-wechat','live-stickers','wechat-read-images']) $(id).onchange=()=>{liveEditing=true;};
$('live-save').onclick=async()=>{
  try {
    await watchApi('/api/live-settings',{qq:$('live-qq').value,wechat:$('live-wechat').value,
      load_stickers:$('live-stickers').checked,wechat_read_images:$('wechat-read-images').checked},'PUT');
    liveEditing=false;toast('实时设置已保存');await pollLive();
  } catch(error) {toast(error.message);}
};
setInterval(pollLive,2000);void pollLive();
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void pollLive();});
window.addEventListener('focus',()=>{void pollLive();});
