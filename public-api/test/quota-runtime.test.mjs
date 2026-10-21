import {test} from "node:test";
import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {Miniflare,convertV4MiniflareOptions} from "miniflare";
test('actual SQLite Durable Object reserves atomically under concurrent requests',{skip:!existsSync('.wrangler/dry-run/worker.js')},async()=>{
 const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.wrangler/dry-run/worker.js',compatibilityDate:'2026-10-01',durableObjects:{QUOTA:{className:'Quota',useSQLite:true}}}));
 try{
  const ns=await mf.getDurableObjectNamespace('QUOTA'),stub=ns.get(ns.idFromName('test'));
  const call=()=>stub.fetch('https://quota/reserve',{method:'POST',body:JSON.stringify({client:'a'.repeat(64)})}).then(r=>r.json());
  const results=await Promise.all(Array.from({length:12},call));
  assert.equal(results.filter(r=>r.ok).length,3);
  assert.equal((await call()).ok,false);
 }finally{await mf.dispose();}
});
