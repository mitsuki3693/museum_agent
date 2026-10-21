import http from "node:http";
import fs from "node:fs";
import path from "node:path";
const root=path.resolve("out"),prefix="/museum_agent/";
const mime={".html":"text/html; charset=utf-8",".css":"text/css",".js":"text/javascript",".jpg":"image/jpeg",".png":"image/png",".svg":"image/svg+xml",".txt":"text/plain"};
http.createServer((req,res)=>{
  let url;
  try{url=decodeURIComponent(new URL(req.url,"http://localhost").pathname);}catch{res.writeHead(400).end();return;}
  if(url==="/museum_agent"){res.writeHead(302,{Location:prefix}).end();return;}
  if(!url.startsWith(prefix)){res.writeHead(404).end();return;}
  const file=path.resolve(root,url.slice(prefix.length)+(url.endsWith("/")?"index.html":""));
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404).end();return;}
  res.writeHead(200,{"Content-Type":mime[path.extname(file)]||"application/octet-stream"});fs.createReadStream(file).pipe(res);
}).listen(3187,"127.0.0.1",()=>console.log("MUSE static preview: http://127.0.0.1:3187/museum_agent/"));
