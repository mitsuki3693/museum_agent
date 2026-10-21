import {spawnSync} from "node:child_process";
import {writeFileSync, existsSync, mkdirSync, mkdtempSync, cpSync, symlinkSync} from "node:fs";
import path from "node:path";
const base=process.env.NEXT_PUBLIC_BASE_PATH || "/museum_agent";
if(!/^\/[a-zA-Z0-9_-]+$/.test(base)) throw new Error("Set a single project base path, e.g. /museum_agent");
const root=process.cwd();
// An allowlisted staging build cannot load local .env files or disturb a live .next server.
mkdirSync(".pages-build",{recursive:true});
const staging=mkdtempSync(path.join(root,".pages-build","export-"));
for(const file of ["src","package.json","tsconfig.json","next.config.mjs"])cpSync(file,path.join(staging,file),{recursive:true});
writeFileSync(path.join(staging,"next-env.d.ts"),'/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n');
mkdirSync(path.join(staging,"public","collection"),{recursive:true});
for(const name of ["artic-16568.jpg","artic-27992.jpg","artic-28560.jpg"])cpSync(path.join("public","collection",name),path.join(staging,"public","collection",name));
symlinkSync(path.join(root,"node_modules"),path.join(staging,"node_modules"),process.platform==="win32"?"junction":"dir");
const result=spawnSync(process.execPath,[path.join(root,"node_modules/next/dist/bin/next"),"build"],{
  cwd:staging,stdio:"inherit",env:{...process.env,NEXT_PUBLIC_STATIC_DEMO:"true",NEXT_PUBLIC_BASE_PATH:base,NEXT_TELEMETRY_DISABLED:"1"},
});
if(result.status!==0) process.exit(result.status || 1);
cpSync(path.join(staging,"out"),path.join(root,"out"),{recursive:true});
for(const route of ["index.html","staff-demo/index.html","conservation-demo/index.html","route-demo/index.html"]){
  if(!existsSync(`out/${route}`))throw new Error(`Missing export: ${route}`);
}
writeFileSync("out/.nojekyll", "");
console.log(`Static MUSE export ready: out (base path ${base}). No backend is required.`);
