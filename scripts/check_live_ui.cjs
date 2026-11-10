'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(){return {children:[],classList:{remove(){},add(){}},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;},setAttribute(){}};}
const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
let healthy=false,refreshCalls=0;
const ctx=vm.createContext({$,assert,console,Date,setInterval(){},setTimeout(){},
  document:{hidden:true,addEventListener(){},createElement:node,createTextNode:text=>text},
  window:{addEventListener(){}},state:{ready:true,busy:false,loading:false},
  watchApi:async()=>({platforms:[{platform:'qq',status:healthy?'listening':'degraded',detail:'fixture',metrics:{},last_id:20}],settings:{qq:'database',wechat:'database'}}),
  call:()=>{refreshCalls++;return new Promise(()=>{});},setData(){},loadWatches(){}});
vm.runInContext(fs.readFileSync('web/live.js','utf8'),ctx);
(async()=>{
  ctx.document.hidden=false;
  await vm.runInContext('pollLive()',ctx);
  assert.equal(refreshCalls,1);
  assert.match($('live-summary').children[0].children[1],/读取受限/);
  healthy=true;
  await vm.runInContext('pollLive()',ctx);
  assert.match($('live-summary').children[0].children[1],/监听中/);
  assert.equal(refreshCalls,1);
  assert.equal(vm.runInContext('liveBusy',ctx),false);
  console.log('PASS: slow data refresh cannot block live status recovery or duplicate refreshes');
})().catch(e=>{console.error(e);process.exitCode=1;});
