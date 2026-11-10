(()=>{'use strict';
const $=id=>document.getElementById(id),node=(tag,value='',cls='')=>{const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;};
async function api(path,method='GET',body){const r=await fetch('/api/mcp'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.detail||'暂时无法读取，请重试。');return d;}
const dialog=node('dialog');dialog.id='mcp-chat-manager';dialog.className='mcp-dialog';
dialog.innerHTML='<div class="dialog-head"><h2>QQ 学习管理</h2><button id="mcp-chat-close" class="icon-button" type="button" aria-label="关闭学习管理">×</button></div><div class="mcp-body"><label for="learning-account">QQ 账号</label><select id="learning-account"></select><p id="learning-status" role="status"></p><div id="learning-groups"></div><p id="learning-error" class="mcp-warning" role="alert"></p></div>';
document.body.append(dialog);let timer=null,loading=false,version=0,signature='';
  const dateText=value=>value?new Date(value*1000).toLocaleString():'未记录';
  const thresholdText=lib=>lib.checked_expressions<10?`${lib.checked_expressions}/10，继续积累后才用于聊天`:`${lib.checked_expressions} 条已通过检查，可按语境使用`;
  function popup(title,cls=''){
    const panel=node('dialog','');panel.className='mcp-dialog mcp-body '+cls;
    const head=node('div','', 'mcp-panel-head'),close=node('button','关闭');close.type='button';close.onclick=()=>panel.close();
    head.append(node('h2',title),close);panel.append(head);panel.addEventListener('close',()=>panel.remove(),{once:true});
    document.body.append(panel);panel.showModal();return panel;
  }
  async function openLearning(group){
    const panel=popup(group.name+' · 学习记录','mcp-learning'),summary=node('p','正在加载学习记录…'),error=node('p','', 'mcp-warning'),records=node('div','');
    panel.append(node('p',`机器人 QQ ${group.account} · 群号 ${group.group}`,'mcp-muted'),summary,error,records);
    const advanced=node('details',''),advancedTitle=node('summary','高级设置与检查详情'),label=node('label',''),paused=node('input','');paused.type='checkbox';
    label.append(paused,document.createTextNode(' 暂停这个账号和群的学习（保留已有积累）'));
    const technical=node('pre','');advanced.append(advancedTitle,label,node('p','通过检查的表达默认可用。提取与自检分次调用，但模型可能记得前文，并非独立核验；不保证每条语义判断正确。','mcp-muted'),technical);panel.append(advanced);
    const budgetLabel=node('label','这个账号和群每小时最多学习阶段'),budget=node('input',''),budgetSave=node('button','保存预算');budget.type='number';budget.min='2';budget.max='100';budget.setAttribute('aria-label','每小时学习阶段预算');budgetSave.type='button';budgetLabel.append(budget);advanced.insertBefore(budgetLabel,technical);advanced.insertBefore(budgetSave,technical);
    let loading=false,changing=false;
    const query='/learning/library?'+new URLSearchParams({account:group.account,group:group.group});
    async function change(body){changing=true;error.textContent='';try{await api('/learning/library','POST',{account:group.account,group:group.group,change:body});await load(true);await loadGroups();}catch(e){error.textContent=e.message+' 可稍后重试。';}finally{changing=false;}}
    paused.onchange=()=>change({enabled:!paused.checked});
    budgetSave.onclick=()=>change({hourly_calls:Number(budget.value)});
    async function load(force=false){if(loading)return;loading=true;try{
      const data=await api(query),lib=data.library;
      summary.textContent=`表达：${thresholdText(lib)}。${lib.pending_expressions?`另有 ${lib.pending_expressions} 条旧记录保留待复核，暂不用于聊天。`:''}黑话：已掌握 ${lib.known_jargon}，待观察 ${lib.observing_jargon}。最近完成学习：${dateText(lib.last_completed_at)}。`;
      if(document.activeElement!==paused)paused.checked=!data.enabled;
      if(document.activeElement!==budget)budget.value=data.hourly_call_budget;
      technical.textContent=JSON.stringify({independence:data.independence,run:data.run,calls_last_hour:data.calls_last_hour,hourly_call_budget:data.hourly_call_budget,last_failure:data.last_failure},null,2);
      error.textContent='';
      if(!force&&records.contains(document.activeElement))return;
      records.replaceChildren();
      for(const [kind,title] of [['expressions','表达方式'],['jargon','群内用语']]){
        const section=node('section','');section.append(node('h3',title));
        if((kind==='expressions'?data.expression_count:data.jargon_count)>30)section.append(node('p','显示最近更新的 30 条记录。完整库仍保留，数量统计包含全部条目。','mcp-muted'));
        if(!(data[kind]||[]).length)section.append(node('p','还没有记录，聊天中会逐步积累。','mcp-muted'));
        for(const item of data[kind]||[]){
          const line=node('article','', 'mcp-learning-record');
          if(kind==='expressions'){
            line.append(node('h4',item.situation),node('p',item.style),node('small',!item.enabled?'已停用':item.evidence_version===2?'群友表达 · 已通过自检':'旧记录待复核 · 暂不用于聊天','mcp-muted'));
            if(item.evidence_version===2&&item.surface_form)line.append(node('p','可观察形式：'+item.surface_form,'mcp-muted'));
          }
          else line.append(node('h4',item.term),node('p',item.meaning||'还需观察用法，暂不作为确定释义'),node('small',!item.enabled?'已停用':item.manual?'人工释义':item.is_jargon?'已掌握':'待观察','mcp-muted'));
          const toggle=node('button',item.enabled?'停用此条':'启用此条');toggle.type='button';toggle.onclick=()=>change({kind,id:item.id,enabled:!item.enabled});line.append(toggle);
          const details=node('details','');details.append(node('summary','检查详情'),node('p',`有效批次命中 ${item.count} · ${item.independence==='manual'?'人工确认':'顺序自检，非独立核验'}`));
          if(kind==='jargon'){
            details.append(node('p',item.last_inference_count?`最近释义检查：第 ${item.last_inference_count} 次命中。`:'尚未进行释义检查，首次需要 4 个有效批次命中。','mcp-muted'));
            const meaning=node('textarea',''),save=node('button','保存人工释义');meaning.value=item.meaning||'';meaning.maxLength=600;meaning.setAttribute('aria-label',item.term+' 的人工释义');save.type='button';save.onclick=()=>change({kind,id:item.id,meaning:meaning.value});details.append(meaning,save);
          }
          line.append(details);section.append(line);
        }records.append(section);
      }
    }catch(e){error.textContent=e.message+' 保留已显示记录，将自动重试。';}finally{loading=false;}}
    await load();const timer=setInterval(()=>{if(panel.open&&!document.hidden&&!changing)void load();},3000);
    panel.addEventListener('close',()=>clearInterval(timer),{once:true});
  }

async function loadGroups(){
  const account=$('learning-account').value;if(!account)return;
  const v=++version;
  try{const d=await api('/learning/groups?'+new URLSearchParams({account}));if(v!==version||!dialog.open)return;
    $('learning-error').textContent='';$('learning-status').textContent=d.groups.length+' 个群';const next=JSON.stringify(d);
    if(signature===next)return;signature=next;$('learning-groups').replaceChildren();
    for(const g of d.groups){const row=node('article','','learning-group-row'),title=node('div'),open=node('button','查看记录');open.type='button';title.append(node('strong',g.name),node('small','群号 '+g.group,'mcp-muted'));open.onclick=()=>openLearning(g);row.append(title,open);
      for(const run of g.active){const p=node('div','','learning-active-run'),stop=node('button','停止会话');stop.type='button';p.append(node('span',run.persona_name||'进行中'),stop);stop.onclick=async()=>{stop.disabled=true;try{await api('/chats/'+encodeURIComponent(run.id)+'/stop','POST',{});await loadGroups();}catch(e){$('learning-error').textContent=e.message;stop.disabled=false;}};row.append(p);}
      $('learning-groups').append(row);
    }
  }catch(e){if(v===version)$('learning-error').textContent=e.message;}
}
async function loadAccounts(){
  if(loading)return;loading=true;try{const d=await api('/learning/accounts');if(!dialog.open)return;const select=$('learning-account'),previous=select.value,next=JSON.stringify(d.accounts);
    if(select.dataset.options!==next){select.replaceChildren(...d.accounts.map(a=>new Option(a.account+' · '+a.groups+' 个群',a.account)));select.dataset.options=next;if(d.accounts.some(a=>a.account===previous))select.value=previous;signature='';}
    if(!d.accounts.length){$('learning-status').textContent='还没有学习记录。';$('learning-groups').replaceChildren();}else await loadGroups();
  }catch(e){$('learning-error').textContent=e.message;}finally{loading=false;}
}
$('open-learning')?.addEventListener('click',()=>{dialog.showModal();void loadAccounts();clearInterval(timer);timer=setInterval(()=>{if(!document.hidden)void loadAccounts();},4000);});
$('learning-account').onchange=()=>{signature='';$('learning-groups').replaceChildren();$('learning-status').textContent='正在读取…';void loadGroups();};
$('mcp-chat-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{clearInterval(timer);version++;});
})();
