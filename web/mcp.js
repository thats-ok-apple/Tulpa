(()=>{'use strict';
  const $=id=>document.getElementById(id),node=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
  const dialog=node('dialog');dialog.id='mcp-settings';dialog.className='mcp-dialog mcp-create-dialog';dialog.setAttribute('aria-labelledby','mcp-title');
  dialog.innerHTML=`<div class="dialog-head"><h2 id="mcp-title">创建 MCP 链接</h2><button id="mcp-close" type="button" class="icon-button" aria-label="关闭创建链接">×</button></div>
  <div class="mcp-body"><form id="mcp-create-form" autocomplete="off">
    <section class="permission-card" data-module="qq"><label class="module-heading"><input id="mcp-module-qq" type="checkbox"><span class="module-icon">Q</span><strong>QQ</strong></label>
      <fieldset id="mcp-module-qq-fields" class="module-fields" disabled hidden>
        <details class="qq-setup"><summary>QQ 连接 <span id="mcp-onebot-status" class="mcp-muted"></span></summary><div id="mcp-snowluma-managed"></div><details><summary>高级连接</summary><button id="mcp-open-onebot" type="button">OneBot 设置</button><button id="mcp-test-onebot" type="button">检测连接</button></details></details>
        <label><input id="mcp-send" type="checkbox">发送消息</label><label><input id="mcp-chat" type="checkbox">持续水群</label>
        <label><input id="mcp-chat_reactions" type="checkbox" disabled>消息表情回应</label>
        <label><input id="mcp-chat_images" type="checkbox" disabled>看图与读取收藏表情</label>
        <label><input id="mcp-chat_sticker_send" type="checkbox" disabled>发送表情包</label>
        <label><input id="mcp-chat_sticker_collect" type="checkbox" disabled>收藏表情包</label>
        <label><input id="mcp-qq" type="checkbox">读取 QQ 聊天记录</label>
        <p id="mcp-qq-import-warning" class="mcp-warning import-warning" hidden>还没有 QQ 聊天资料。<button type="button" data-open-data>去导入</button></p>
        <details><summary>高级权限</summary><label><input id="mcp-manage" type="checkbox">群管理</label><label><input id="mcp-onebot" type="checkbox" disabled>补充历史群公告、成员与文件目录</label></details>
      </fieldset>
    </section>
    <section class="permission-card" data-module="wechat"><label class="module-heading"><input id="mcp-module-wechat" type="checkbox"><span class="module-icon">微</span><strong>微信</strong></label>
      <fieldset id="mcp-module-wechat-fields" class="module-fields" disabled hidden><label><input id="mcp-wechat" type="checkbox">读取微信聊天记录</label><p id="mcp-wechat-import-warning" class="mcp-warning import-warning" hidden>还没有微信聊天资料。<button type="button" data-open-data>去导入</button></p></fieldset>
    </section>
    <section class="permission-card" data-module="bilibili"><label class="module-heading"><input id="mcp-module-bilibili" type="checkbox"><span class="module-icon">哔</span><strong>哔哩哔哩</strong></label>
      <fieldset id="mcp-module-bilibili-fields" class="module-fields" disabled hidden><p>搜索、推荐、视频内容、画面与评论</p><button id="mcp-bilibili-login" type="button">扫码登录</button></fieldset>
    </section>
    <fieldset id="mcp-scope-picker" hidden><legend>会话范围</legend><label><input id="mcp-all" type="checkbox">允许访问所选平台的全部会话</label>
      <div id="mcp-picker"><select id="mcp-source" aria-label="会话来源"><option value="history">聊天记录</option><option value="groups">QQ 群聊</option><option value="friends">QQ 好友</option></select><input id="mcp-search" type="search" placeholder="搜索名称或号码" aria-label="搜索会话"><div id="mcp-chats" class="mcp-chats"></div><button id="mcp-more" type="button" hidden>更多</button><p id="mcp-selection" class="mcp-muted"></p></div>
    </fieldset>
    <details id="mcp-create-advanced" class="mcp-advanced"><summary>高级设置</summary>
      <label for="mcp-name">链接名称（可留空）</label><input id="mcp-name" maxlength="60" placeholder="留空自动生成 ID">
      <fieldset id="mcp-history-extras" hidden><legend>所选聊天记录的附件</legend><label><input id="mcp-media" type="checkbox">读取图片</label><label><input id="mcp-prepare" type="checkbox">下载文件与提取文字</label><label><input id="mcp-voice" type="checkbox">转写语音</label></fieldset>
      <div class="mcp-dates" id="mcp-date-scope"><label>开始日期<input id="mcp-start" type="date"></label><label>截止日期<input id="mcp-end" type="date"></label></div>
    </details>
    <p id="mcp-create-status" class="mcp-warning" role="alert"></p><button id="mcp-create" type="submit">一键开启 MCP</button>
  </form>
  <section id="mcp-secret" hidden><h3>链接已创建</h3><p id="mcp-created-name"></p><label>地址<input id="mcp-url" readonly></label><label>访问令牌<input id="mcp-token" type="password" readonly autocomplete="off"></label>
    <button id="mcp-reveal" type="button">显示令牌</button> <button id="mcp-copy-connection" type="button">复制连接配置</button><p id="mcp-copy-status" role="status"></p>
    <details><summary>高级连接设置</summary><p>关闭后不再显示令牌，请先保存连接配置。</p><button id="mcp-test" type="button">检测连接</button><p id="mcp-test-result" role="status"></p>
    <label for="mcp-server-name">Codex MCP 名称</label><input id="mcp-server-name" value="tulpa" maxlength="64" pattern="[A-Za-z0-9_-]{1,64}" autocomplete="off"><textarea id="mcp-config" readonly rows="5" aria-label="Codex MCP 连接配置"></textarea><button id="mcp-install-codex" type="button">写入本机 Codex</button> <button id="mcp-copy" type="button">复制 Codex 配置</button><p id="mcp-install-result" role="status"></p></details>
  </section><p id="mcp-error" role="alert"></p></div>`;
  document.body.append(dialog);
  const settings=node('dialog');settings.id='mcp-service-settings';settings.className='mcp-dialog';
  settings.innerHTML=`<div class="dialog-head"><h2>服务设置</h2><button id="mcp-service-close" type="button" class="icon-button" aria-label="关闭服务设置">×</button></div><div class="mcp-body"><p id="mcp-service-status" role="status"></p><details id="mcp-advanced"><summary>高级服务设置</summary><div class="mcp-service"><label><input id="mcp-enabled" type="checkbox">启用 MCP 服务</label><label>端口<input id="mcp-port" type="number" min="1024" max="65535" value="18777"></label><button id="mcp-save-service" type="button">保存</button></div></details><details id="mcp-audit-details"><summary>访问记录</summary><div id="mcp-audit"></div></details><details id="mcp-operation-history"><summary>QQ 操作记录</summary><div id="mcp-operations"></div></details><p id="mcp-service-error" class="mcp-warning" role="alert"></p></div>`;
  document.body.append(settings);
  const managedQQ=window.TulpaSnowLuma?.mount($('mcp-snowluma-managed'),{prefix:'mcp',onSaved:async()=>refresh()});
  let current=null,credential=null,busy=false,selected=new Map(),page=0,searchVersion=0,searchTimer,liveConversations=null,listSignature='';
  async function api(path,method='GET',body){let response,data;try{response=await fetch('/api/mcp'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(30000)});data=await response.json();}catch{throw Error('无法连接 Tulpa，请重试。');}if(!response.ok)throw Error(data.detail||'操作未完成。');return data;}
  const modules=()=>Object.fromEntries(['qq','wechat','bilibili'].map(p=>[p,$('mcp-module-'+p).checked]));
  const historyPlatforms=()=>['qq','wechat'].filter(p=>modules()[p]&&$('mcp-'+p).checked);
  const hasLive=()=>modules().qq&&['send','chat','manage'].some(f=>$('mcp-'+f).checked);
  const scopePlatforms=()=>[...new Set([...historyPlatforms(),...(hasLive()?['qq']:[])])];
  function state(data){current=data;$('mcp-enabled').checked=data.enabled;$('mcp-port').value=data.port;$('mcp-service-status').textContent=data.error||(data.running?'MCP 运行中':'MCP 已停止');$('mcp-onebot-status').textContent=data.onebot_configured?'已配置':'待连接';renderConnections();warnImport();}
  async function refresh(){state(await api(''));}
  function warnImport(){for(const p of ['qq','wechat'])$('mcp-'+p+'-import-warning').hidden=!(modules()[p]&&$('mcp-'+p).checked&&!current?.data_platforms?.[p]);}
  function renderConnections(){
    const host=$('mcp-connections');if(!host||!current)return;
    const rows=current.connections||[],signature=JSON.stringify(rows.map(r=>[r.id,r.name,r.scope,r.paused,r.revoked,r.valid,r.revision]));
    if($('connection-count'))$('connection-count').textContent=String(rows.length);
    if(signature===listSignature)return;listSignature=signature;host.replaceChildren();
    for(const row of rows){const line=node('div','','connection-row'),text=node('div','','connection-label'),title=node('strong',row.name),small=node('span',[(row.scope.platforms||[]).includes('qq')?'QQ':'',(row.scope.platforms||[]).includes('wechat')?'微信':'',row.scope.bilibili_read?'哔哩哔哩':'',row.revoked?'已撤销':row.valid===false?'资料来源已变更':row.paused?'已关闭':''].filter(Boolean).join(' · '),'mcp-muted');text.append(title,small);
      const toggle=node('button','','connection-toggle');toggle.type='button';toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(!row.paused&&!row.revoked));toggle.setAttribute('aria-label','开启或关闭 '+row.name);toggle.disabled=!!row.revoked;
      toggle.onclick=async()=>{toggle.disabled=true;try{await api('/connections/'+encodeURIComponent(row.id)+'/state','PUT',{enabled:!!row.paused});await refresh();}catch(e){showHomeError(e.message);}finally{toggle.disabled=!!row.revoked;}};
      const remove=node('button','','connection-delete');remove.type='button';remove.setAttribute('aria-label','删除 '+row.name);remove.title='删除链接';remove.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7m4-7v7"/></svg>';
      remove.onclick=()=>confirmDelete(row);line.append(text,toggle,remove);host.append(line);
    }
    if(!rows.length)host.append(node('p','还没有链接。','mcp-empty'));
  }
  function showHomeError(text){($('lite-error')||$('mcp-error')).textContent=text;}
  function confirmDelete(row){const p=node('dialog','','mcp-dialog mcp-confirm'),h=node('h2','删除此链接？'),name=node('p',row.name),yes=node('button','删除','danger-button'),no=node('button','取消'),error=node('p','','mcp-warning');p.append(h,name,yes,no,error);document.body.append(p);no.onclick=()=>p.close();p.addEventListener('close',()=>p.remove());yes.onclick=async()=>{yes.disabled=true;try{await api('/connections/'+encodeURIComponent(row.id),'DELETE');if(credential?.id===row.id)clearSecret();await refresh();p.close();}catch(e){error.textContent=e.message;yes.disabled=false;}};p.showModal();no.focus();}
  function selection(){$('mcp-selection').textContent='已选 '+selected.size+' 个会话';}
  function permissions(){
    const m=modules();for(const p of Object.keys(m)){const fields=$('mcp-module-'+p+'-fields');fields.disabled=!m[p];fields.hidden=!m[p];}
    const active=m.qq&&$('mcp-chat').checked&&$('mcp-send').checked;
    for(const f of ['chat_reactions','chat_images'])$('mcp-'+f).disabled=!active;
    for(const f of ['chat_sticker_send','chat_sticker_collect'])$('mcp-'+f).disabled=!active||!$('mcp-chat_images').checked;
    const historical=historyPlatforms().length>0;
    $('mcp-history-extras').hidden=!historical;for(const f of ['media','prepare','voice'])$('mcp-'+f).disabled=!historical;
    $('mcp-onebot').disabled=!m.qq||!$('mcp-qq').checked;$('mcp-date-scope').hidden=!scopePlatforms().length;$('mcp-scope-picker').hidden=!scopePlatforms().length;
    warnImport();
  }
  async function scopeChanged(){permissions();liveConversations=null;const platforms=scopePlatforms();for(const [key,pair]of selected)if(!platforms.includes(pair[0]))selected.delete(key);await chats();}
  async function chats(reset=true){
    const version=++searchVersion;if(reset){page=0;$('mcp-chats').replaceChildren();}
    const platforms=scopePlatforms(),history=historyPlatforms();for(const option of $('mcp-source').options)option.disabled=option.value==='history'?!history.length:!hasLive();
    if(!history.length&&hasLive()&&$('mcp-source').value==='history')$('mcp-source').value='groups';else if(!hasLive())$('mcp-source').value='history';
    if(!platforms.length){$('mcp-more').hidden=true;selection();return;}
    if($('mcp-all').checked){selection();return;}
    const source=$('mcp-source').value,query=$('mcp-search').value.toLowerCase();let data;
    if(source==='history')data=await api('/conversations?'+new URLSearchParams({query,offset:page,platform:history.length===1?history[0]:''}));
    else{const items=liveConversations||(await api('/live-'+source)).items;if(version!==searchVersion)return;liveConversations=items;data={items:items.filter(r=>(r.name+' '+r.conversation_id).toLowerCase().includes(query)),has_more:false,next_offset:0};}
    if(version!==searchVersion)return;
    for(const item of data.items.filter(r=>platforms.includes(r.platform))){const pair=[item.platform,item.conversation_id],key=JSON.stringify(pair),label=node('label'),input=node('input');input.type='checkbox';input.checked=selected.has(key);input.onchange=()=>{if(input.checked)selected.set(key,pair);else selected.delete(key);selection();};label.append(input,node('span',item.name+' · '+(item.platform==='qq'?'QQ':'微信')));$('mcp-chats').append(label);}
    if(!$('mcp-chats').children.length)$('mcp-chats').append(node('p','没有可选会话。','mcp-muted'));page=data.next_offset;$('mcp-more').hidden=!data.has_more;selection();
  }
  async function action(fn){$('mcp-error').textContent='';try{await fn();}catch(e){$('mcp-error').textContent=e.message;}}
  function clearSecret(){credential=null;for(const id of ['mcp-token','mcp-config'])$(id).value='';$('mcp-token').type='password';$('mcp-secret').hidden=true;}
  async function open(){if(!dialog.open){clearSecret();$('mcp-create-form').hidden=false;dialog.showModal();}await action(async()=>{await refresh();await scopeChanged();});}
  ($('home-connect')||$('open-mcp'))?.addEventListener('click',open);$('mcp-close').onclick=()=>{clearSecret();dialog.close();};dialog.addEventListener('close',clearSecret);
  window.addEventListener('tulpa-connections',e=>state(e.detail));
  for(const p of ['qq','wechat','bilibili'])$('mcp-module-'+p).onchange=()=>action(scopeChanged);
  for(const f of ['qq','wechat','manage'])$('mcp-'+f).onchange=()=>action(scopeChanged);
  $('mcp-chat').onchange=()=>{if($('mcp-chat').checked)$('mcp-send').checked=true;action(scopeChanged);};
  $('mcp-send').onchange=()=>{if(!$('mcp-send').checked)$('mcp-chat').checked=false;action(scopeChanged);};
  $('mcp-chat_images').onchange=permissions;
  $('mcp-source').onchange=()=>{liveConversations=null;action(()=>chats());};$('mcp-more').onclick=()=>action(()=>chats(false));
  $('mcp-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>action(()=>chats()),200);};
  $('mcp-all').onchange=()=>{$('mcp-picker').hidden=$('mcp-all').checked;action(()=>chats());};
  dialog.querySelectorAll('[data-open-data]').forEach(b=>b.onclick=()=>{$('open-data')?.click();});
  $('mcp-open-onebot').onclick=()=>{dialog.close();window.dispatchEvent(new CustomEvent('tulpa-open-settings'));};
  $('mcp-test-onebot').onclick=()=>action(async()=>{const r=await fetch('/api/desktop/onebot/test',{method:'POST',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:'{}'});const d=await r.json();$('mcp-onebot-status').textContent=d.message||d.detail||'检测完成';});
  $('mcp-bilibili-login').onclick=()=>window.dispatchEvent(new CustomEvent('tulpa-open-bilibili'));
  $('data-dialog')?.addEventListener('close',()=>action(refresh));
  $('mcp-create-form').onsubmit=e=>{e.preventDefault();action(async()=>{
    if(busy)return;busy=true;$('mcp-create').disabled=true;$('mcp-create-status').textContent='';$('mcp-create').textContent='正在创建…';
    try{const m=modules(),body={modules:m,name:$('mcp-name').value,platforms:scopePlatforms(),history_platforms:historyPlatforms(),conversations:[...selected.values()],all_conversations:$('mcp-all').checked,start:scopePlatforms().length?$('mcp-start').value:'',end:scopePlatforms().length?$('mcp-end').value:'',bilibili_read:m.bilibili};
      for(const f of ['media','prepare','voice'])body[f]=body.history_platforms.length>0&&$('mcp-'+f).checked;
      for(const f of ['onebot','send','manage','chat','chat_reactions','chat_images','chat_sticker_send','chat_sticker_collect'])body[f]=m.qq&&!$('mcp-'+f).disabled&&$('mcp-'+f).checked;
      if(!body.platforms.length&&!body.bilibili_read)throw Error('请勾选至少一项功能。');
      if(body.platforms.length&&!body.all_conversations&&!body.conversations.length){$('mcp-scope-picker').scrollIntoView({block:'center'});throw Error('请选择会话范围。');}
      if(body.start&&body.end&&body.start>body.end)throw Error('截止日期不能早于开始日期。');
      await refresh();
      const missing=body.history_platforms.filter(p=>!current.data_platforms?.[p]);if(missing.length)throw Error('请先导入 '+missing.map(p=>p==='qq'?'QQ':'微信').join('、')+' 聊天资料。');
      if(!current.running){const s=await api('','PUT',{enabled:true,port:current.port});state({...current,...s});if(!s.running)throw Error(s.error||'MCP 启动失败。');}
      const result=await api('/connections','POST',body);credential={...result,scope:body,url:current.url};
      $('mcp-url').value=current.url;$('mcp-token').value=result.token;$('mcp-created-name').textContent=result.name||body.name||result.id;
      $('mcp-create-form').hidden=true;$('mcp-secret').hidden=false;$('mcp-copy-status').textContent=$('mcp-test-result').textContent=$('mcp-install-result').textContent='';renderCodexConfig();
      try{await refresh();}catch{showHomeError('链接已创建，列表暂未刷新。');}
    }catch(e){$('mcp-create-status').textContent=e.message;}finally{busy=false;$('mcp-create').disabled=false;$('mcp-create').textContent='一键开启 MCP';}
  });};
  function renderCodexConfig(){
    if(!credential)return false;const name=$('mcp-server-name').value,valid=/^[A-Za-z0-9_-]{1,64}$/.test(name);$('mcp-install-codex').disabled=$('mcp-copy').disabled=!valid;if(!valid){$('mcp-config').value='';return false;}
    $('mcp-config').value='[mcp_servers.'+name+']\nurl = '+JSON.stringify(credential.url)+'\nhttp_headers = { Authorization = '+JSON.stringify('Bearer '+credential.token)+' }\ntool_timeout_sec = 240\n';
    const body=credential.scope;
    for(const [flag,names]of [['send',['send_qq_message']],['manage',['manage_qq_group']],['chat',['start_chat_session','get_chat_session','list_chat_sessions','wait_chat_messages','plan_chat_reply','send_chat_reply','send_chat_message','stop_chat_session','list_chat_groups','list_chat_personas','get_chat_learning','claim_chat_learning','submit_chat_learning','select_chat_expressions']],['chat_reactions',['react_to_chat_message']],['chat_images',['read_chat_image','list_chat_stickers','read_chat_sticker','note_chat_sticker']],['chat_sticker_send',['send_chat_sticker']],['chat_sticker_collect',['collect_chat_sticker']]])if(body[flag])for(const tool of names)$('mcp-config').value+='\n[mcp_servers.'+name+'.tools.'+tool+']\napproval_mode = "approve"\n';
    return true;
  }
  $('mcp-server-name').oninput=renderCodexConfig;
  $('mcp-reveal').onclick=()=>{const input=$('mcp-token');input.type=input.type==='password'?'text':'password';$('mcp-reveal').textContent=input.type==='password'?'显示令牌':'隐藏令牌';};
  $('mcp-copy-connection').onclick=()=>action(async()=>{if(!credential)return;await navigator.clipboard.writeText(JSON.stringify({mcpServers:{[credential.name||'tulpa']:{type:'http',url:credential.url,headers:{Authorization:'Bearer '+credential.token}}}},null,2));$('mcp-copy-status').textContent='已复制';});
  $('mcp-copy').onclick=()=>action(async()=>{if(renderCodexConfig()){await navigator.clipboard.writeText($('mcp-config').value);$('mcp-copy').textContent='已复制';}});
  $('mcp-test').onclick=()=>action(async()=>{if(!credential)return;const r=await api('/test','POST',{token:credential.token});$('mcp-test-result').textContent='检测通过 · '+r.tools+' 个工具';});
  $('mcp-install-codex').onclick=()=>action(async()=>{if(!renderCodexConfig())return;const b=$('mcp-install-codex');b.disabled=true;try{await api('/connections/'+encodeURIComponent(credential.id)+'/codex','POST',{token:credential.token,server_name:$('mcp-server-name').value});$('mcp-install-result').textContent='已写入，请在 Codex 中重新连接 MCP。';}finally{b.disabled=false;}});
  $('open-service-settings')?.addEventListener('click',()=>{settings.showModal();refresh().catch(e=>$('mcp-service-error').textContent=e.message);});
  $('mcp-service-close').onclick=()=>settings.close();
  $('mcp-save-service').onclick=async()=>{try{await api('','PUT',{enabled:$('mcp-enabled').checked,port:Number($('mcp-port').value)});await refresh();$('mcp-service-error').textContent='';}catch(e){$('mcp-service-error').textContent=e.message;}};
  $('mcp-audit-details').addEventListener('toggle',()=>{if($('mcp-audit-details').open)$('mcp-audit').replaceChildren(...(current?.recent||[]).map(r=>node('p',r.name+' · '+r.tool+' · '+r.status)));});
  $('mcp-operation-history').addEventListener('toggle',async()=>{if(!$('mcp-operation-history').open)return;try{const d=await api('/operations');$('mcp-operations').replaceChildren(...d.operations.map(r=>node('p',r.connection_name+' · '+r.state+' · '+r.summary)));}catch(e){$('mcp-service-error').textContent=e.message;}});
  permissions();void refresh().catch(e=>showHomeError(e.message));
})();
