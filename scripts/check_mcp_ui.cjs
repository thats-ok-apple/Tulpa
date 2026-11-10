// Actual browser and source assets with isolated synthetic HTTP fixtures.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const root=path.resolve(process.argv.includes('--package')?process.argv[process.argv.indexOf('--package')+1]:path.join(__dirname,'..'));
const main=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const fragments=[main.match(/<svg class="icon-defs"[\s\S]*?<\/svg>/)[0],...['data-dialog','chat-dialog','image-dialog'].map(id=>main.match(new RegExp('<dialog id="'+id+'"[\\s\\S]*?<\\/dialog>'))[0])].join('\n');
const html=fs.readFileSync(path.join(root,'web/mcp-home.html'),'utf8').replace('<!-- shared-forms -->',fragments);
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1180,height:900}}),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));let connections=[],seq=0,history={},enabled=false,account='111',fail=false;
  const snapshot=()=>({enabled,running:enabled,port:18777,url:'http://127.0.0.1:18777/mcp',error:'',data_platforms:history,onebot_configured:true,connections,recent:[]});
  const group=a=>({account:a,group:'222',name:a==='111'?'合成群 A':'合成群 B',conversation_id:a+':group:222',active:[],history:[]});
  const library={enabled:true,hourly_call_budget:20,independence:'degraded',library:{checked_expressions:4,pending_expressions:0,known_jargon:1,observing_jargon:2,last_completed_at:null},run:{completed_batches:0,buffered:0},expression_count:1,jargon_count:0,expressions:[{id:'e',situation:'合成群友在分享好消息时',style:'一串非常长的合成表达'.repeat(35),count:1,enabled:true,evidence_version:2,surface_form:'合成原文'}],jargon:[]};
  await page.route('http://127.0.0.1:39879/**',async route=>{
   const r=route.request(),u=new URL(r.url()),method=r.method(),reply=(d,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(d)});
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname.startsWith('/ui/')){const file=path.join(root,'web',path.basename(u.pathname));return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/png',body:fs.readFileSync(file)});}
   if(u.pathname==='/api/mcp'&&method==='GET')return reply(snapshot());
   if(u.pathname==='/api/desktop/status')return reply({version:'0.5.6',owned:false,settings:{sender_url:'',events_url:''}});
   if(u.pathname==='/api/desktop/preferences')return reply({background:true});
   if(u.pathname==='/api/read-options')return reply({qq_per_chat:{min:1,max:10000,default:300},wechat_per_chat:{min:1,max:10000,default:300}});
   if(u.pathname==='/api/live-status')return reply({platforms:[],settings:{qq:false,wechat:false,load_stickers:false}});
   if(u.pathname==='/api/desktop/snowluma/managed')return reply({available:false,automatic_available:false,phase:'not_started',message:'未配置',choices:[],agreements:[]});
   if(u.pathname==='/api/mcp/learning/accounts')return reply({accounts:[{account:'111',groups:1},{account:'999',groups:1}]});
   if(u.pathname==='/api/mcp/learning/groups'){account=u.searchParams.get('account');return fail?reply({detail:'合成读取失败'},503):reply({groups:[group(account)]});}
   if(u.pathname==='/api/mcp/learning/library'&&method==='GET')return reply({...library,expressions:library.expressions.map(e=>({...e,situation:u.searchParams.get('account')==='111'?e.situation:'另一个账号的群友表达'}))});
   if(u.pathname==='/api/mcp/conversations')return reply({items:['qq','wechat'].filter(p=>!u.searchParams.get('platform')||p===u.searchParams.get('platform')).map(p=>({platform:p,name:p==='qq'?'合成 QQ 记录':'合成微信记录',conversation_id:p==='qq'?'111:group:222':'wx-fixture',count:4})),has_more:false,next_offset:0});
   if(u.pathname==='/api/mcp/live-groups'||u.pathname==='/api/mcp/live-friends')return reply({items:[{platform:'qq',name:'合成实时群',conversation_id:'111:group:222'}]});
   if(u.pathname==='/bilibili/api/status')return reply({bilibili:{state:'logged_out'}});
   let b={};if(r.postData())b=r.postDataJSON();writes.push({url:u.pathname,method,body:b});
   if(u.pathname==='/api/mcp'&&method==='PUT'){enabled=b.enabled;return reply(snapshot());}
   if(u.pathname==='/api/mcp/connections'&&method==='POST'){const id='fixture-'+(++seq),name=b.name||'mcp-fixture-'+seq;connections.push({id,name,scope:b,paused:false,revoked:false,valid:true,revision:0,accounts:{}});return reply({id,name,token:'fixture-not-a-real-token-0000'});}
   if(u.pathname.endsWith('/state')){const g=connections.find(g=>u.pathname.includes(g.id));g.paused=!b.enabled;g.revision++;return reply({enabled:b.enabled,changed:true});}
   if(u.pathname.startsWith('/api/mcp/connections/')&&method==='DELETE'){connections=connections.filter(g=>!u.pathname.endsWith(g.id));return reply({deleted:true});}
   if(u.pathname==='/api/mcp/learning/library'&&method==='POST'){library.expressions[0].enabled=b.change.enabled;return reply(library);}
   if(u.pathname==='/bilibili/api/action')return reply(b.action==='start_login'?{qr_image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='}:{state:'waiting'});
   throw Error('Unexpected fixture request '+method+' '+u.pathname);
  });
  const shot=async name=>{if(process.env.MCP_UI_SCREENSHOTS){fs.mkdirSync(process.env.MCP_UI_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.MCP_UI_SCREENSHOTS,name+'.png')});}};
  await page.goto('http://127.0.0.1:39879/?connect=mcp');await page.locator('#mcp-connections .mcp-empty').waitFor();
  assert.equal(await page.locator('#mcp-settings').isVisible(),false);
  assert.equal(await page.locator('#browse-chats,#open-artifacts,#open-mcp,#voice-component-install').count(),0);
  assert.equal(await page.getByText('从这里开始',{exact:true}).count(),0);await shot('01-home-empty');
  await page.locator('#home-connect').click();assert.equal(await page.locator('.permission-card').count(),3);
  assert.equal(await page.locator('#mcp-bilibili_share,#mcp-chat-open,#mcp-open-data').count(),0);
  assert.equal(await page.locator('#mcp-name').isVisible(),false);assert.equal(await page.locator('#mcp-create-advanced').getAttribute('open'),null);
  await shot('02-three-modules');
  await page.locator('#mcp-module-qq').check();await page.locator('#mcp-chat').check();await page.locator('#mcp-chat_images').check();await page.locator('#mcp-chat_sticker_send').check();await page.locator('#mcp-all').check();
  assert.equal(await page.locator('#mcp-qq-import-warning').isVisible(),false);
  await page.locator('#mcp-create').click();await page.locator('#mcp-secret').waitFor();
  let body=writes.findLast(x=>x.url==='/api/mcp/connections').body;assert.deepEqual(body.history_platforms,[]);assert.equal(body.chat,true);assert.equal(body.name,'');assert.equal(body.bilibili_read,false);
  await page.locator('#mcp-close').click();assert.equal(await page.locator('#mcp-token').inputValue(),'');
  await page.locator('.connection-toggle').click();await page.waitForFunction(()=>document.querySelector('.connection-toggle').getAttribute('aria-checked')==='false');
  await page.reload();await page.waitForFunction(()=>document.querySelector('.connection-toggle')?.getAttribute('aria-checked')==='false');
  await page.locator('.connection-toggle').click();await page.waitForFunction(()=>document.querySelector('.connection-toggle').getAttribute('aria-checked')==='true');await shot('03-home-connection');
  await page.locator('#home-connections>summary').click();assert.equal(await page.locator('.connection-row').isVisible(),false);await page.locator('#home-connections>summary').click();
  await page.locator('#home-connect').click();await page.locator('#mcp-module-qq').check();await page.locator('#mcp-qq').check();assert.equal(await page.locator('#mcp-qq-import-warning').isVisible(),true);
  await page.locator('#mcp-module-wechat').check();await page.locator('#mcp-wechat').check();assert.equal(await page.locator('#mcp-wechat-import-warning').isVisible(),true);
  await page.locator('#mcp-module-qq').uncheck();await page.locator('#mcp-module-wechat').uncheck();await page.locator('#mcp-module-bilibili').check();
  assert.equal(await page.locator('#mcp-scope-picker').isVisible(),false);await page.locator('#mcp-create').click();await page.locator('#mcp-secret').waitFor();
  body=writes.findLast(x=>x.url==='/api/mcp/connections').body;assert.deepEqual(body.platforms,[]);assert.deepEqual(body.conversations,[]);assert.equal(body.bilibili_read,true);for(const f of ['send','chat','manage','onebot','chat_images','media','voice'])assert.equal(body[f],false);
  await page.locator('#mcp-close').click();await page.locator('.connection-delete').first().click();await page.locator('.mcp-confirm').getByRole('button',{name:'取消',exact:true}).click();assert.equal(connections.length,2);
  await page.locator('.connection-delete').first().click();await page.locator('.mcp-confirm').getByRole('button',{name:'删除',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.connection-row').length===1);
  await page.locator('#open-learning').click();await page.getByText('合成群 A',{exact:true}).waitFor();await page.locator('#learning-account').selectOption('999');await page.getByText('合成群 B',{exact:true}).waitFor();
  await page.getByRole('button',{name:'查看记录',exact:true}).click();await page.getByText('另一个账号的群友表达',{exact:true}).waitFor();await shot('04-learning-records');
  await page.getByRole('button',{name:'停用此条',exact:true}).click();await page.getByRole('button',{name:'启用此条',exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});await shot('05-narrow-records');
  for(const d of await page.locator('dialog[open]').all())assert.ok(await d.evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  await page.keyboard.press('Escape');await page.keyboard.press('Escape');await page.locator('#home-connect').click();await page.locator('#mcp-module-qq').check();await shot('06-narrow-QQ');
  assert.ok(await page.locator('#mcp-settings').evaluate(e=>e.scrollWidth<=e.clientWidth+1));await page.keyboard.press('Escape');
  await page.locator('#open-bilibili').click();await page.locator('#bili-login-qr').waitFor();assert.equal(new URL(page.url()).pathname,'/');await page.keyboard.press('Escape');
  assert.deepEqual(errors,[]);assert.equal(writes.some(w=>/chats\/start|send_group|install|managed\/start/.test(w.url)),false);
  console.log('PASS: actual frontend assets; compact/collapsible home; three independent modules; no-history QQ/Bilibili create; random name; pause/resume/reload/delete; QQ-account/group library; edit; QR modal; narrow screens; no external writes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
