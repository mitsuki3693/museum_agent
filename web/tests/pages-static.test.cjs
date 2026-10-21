const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const vm=require('node:vm');
const code=ts.transpileModule(fs.readFileSync('src/lib/pages-catalogue.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const context={exports:{}};vm.runInNewContext(code,context);
const {demoWorks,findDemoWorks,demoAnswer}=context.exports;
test('preset catalogue supports bilingual lookup and full IDs',()=>{
 assert.equal(findDemoWorks('我想看睡莲')[0].id,'artic-16568');
 assert.equal(findDemoWorks('The Bedroom')[0].id,'artic-28560');
 assert.equal(findDemoWorks('1926.224')[0].id,'artic-27992');
 assert.equal(findDemoWorks('C.615-1925').length,0);
});
test('unknown questions never silently become invented answers',()=>{
 assert.match(demoAnswer(demoWorks[0],'画家早餐吃了什么','brief'),/没有连接 AI/);
 assert.match(demoAnswer(demoWorks[0],'是什么材质','brief'),/Oil on canvas/);
 for(const work of demoWorks){assert.notEqual(work.brief,work.children);assert.notEqual(work.brief,work.deep);}
});
test('static visitor and route implementation have no network or storage operations',()=>{
 for(const file of ['PagesVisitor.tsx','PagesRoute.tsx']){
  const source=fs.readFileSync(`src/components/${file}`,'utf8');
  assert.doesNotMatch(source,/\bfetch\s*\(|museumApi\s*\(|localStorage|sessionStorage|XMLHttpRequest/);
 }
});
test('export has base-path assets and only public allowed image files',{skip:!fs.existsSync('out/index.html')},()=>{
 const html=fs.readFileSync('out/index.html','utf8');
 assert.match(html,/交互演示/);assert.match(html,/\/museum_agent\/_next\//);
 for(const route of ['staff-demo','conservation-demo','route-demo'])assert.ok(fs.existsSync(`out/${route}/index.html`));
 const images=fs.readdirSync('out/collection').sort();
 assert.deepEqual(images,['artic-16568.jpg','artic-27992.jpg','artic-28560.jpg']);
 const walk=dir=>fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(`${dir}/${e.name}`):[`${dir}/${e.name}`]);
 const files=walk('out');
 assert.ok(!files.some(f=>/\/(?:\.env|private|\.runtime|backup)(?:\/|\.|$)/i.test(f)));
});
