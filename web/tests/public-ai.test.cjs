const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
function client(fetch){const c={exports:{},process:{env:{NEXT_PUBLIC_MUSE_AI_URL:'https://muse.example/chat'}},fetch};vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/lib/public-ai.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,c);return c.exports;}
const body={workId:'artic-16568',question:'为什么？',style:'brief',history:[]};
test('public client sends only question context, omits browser credentials and propagates cancellation',async()=>{
 const controller=new AbortController();let sent;
 const c=client(async(url,options)=>{sent={url,options};return {ok:true,json:async()=>({kind:'ai',workId:body.workId,answer:'回答'})};});
 await c.askPublicAi(body,controller.signal);
 assert.equal(sent.options.credentials,'omit');assert.equal(sent.options.signal,controller.signal);assert.equal(sent.options.headers.Authorization,undefined);assert.deepEqual(JSON.parse(sent.options.body),body);
});
test('quota error is shown rather than a fake AI success',async()=>{
 const c=client(async()=>({ok:false,json:async()=>({error:'今日额度已用完'})}));await assert.rejects(c.askPublicAi(body,new AbortController().signal),/今日额度已用完/);
});
test('malformed or wrong-work answers are rejected',async()=>{
 for(const answer of [{kind:'ai',workId:'other',answer:'错误作品'},{kind:'preset',workId:body.workId,answer:'模板'}]){
  const c=client(async()=>({ok:true,json:async()=>answer}));await assert.rejects(c.askPublicAi(body,new AbortController().signal),/格式异常/);
 }
});
