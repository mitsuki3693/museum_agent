const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const context=vm.createContext({exports:{},require:id=>id.endsWith('.css')?new Proxy({},{get:(_,k)=>String(k)}):require(id)});
vm.runInContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/app/review/EvaluationPanel.tsx'),'utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}
}).outputText,context);
const {default:Panel,metricValue}=context.exports;
const data={total:1,shown:1,truncated:false,tracks:[{id:'photo',label:'局部识图',count:1},{id:'route',label:'路线规划',count:0}],runs:[{
 _id:'crop',created_at:1,track:'photo',status:'completed',dataset_version:'frozen-v1',dataset_hash:'a'.repeat(64),corpus_hash:'b'.repeat(64),
 model:'fixture',prompt_version:'no-vlm',scope:'Offline retrieval only',decision:'reject',human_reviewed:false,human_grades_completed:0,result_count:42,
 metrics:[{group:'20张库内图',variant:'baseline',label:'Top5命中',value:.7,unit:'ratio',numerator:14,denominator:20}]
}]};
test('evaluation separates rejected experiment, review coverage, and missing tracks',()=>{
 const html=renderToStaticMarkup(React.createElement(Panel,{data}));
 for(const text of ['不采用该实验方案','待人工复核','0/42','14 / 20','路线规划','不代表线上 A/B','版本与复现标识'])assert(html.includes(text),text);
 assert(!html.includes('已记录采用决定'));
});
test('missing measurement does not render zero or perfect accuracy',()=>{
 assert.equal(metricValue({value:null,unit:'ratio'}),'未测／未评分');
 assert.equal(metricValue({value:0,unit:'ratio',numerator:0,denominator:4}),'0 / 4（0.0%）');
});
test('empty and bounded-history states are explicit',()=>{
 const html=renderToStaticMarkup(React.createElement(Panel,{data:{...data,total:201,shown:0,truncated:true,runs:[]}}));
 assert(html.includes('最近 200 批')&&html.includes('尚无符合条件的评测批次'));
});
