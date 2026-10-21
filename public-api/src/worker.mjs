import {handleRequest,takeQuota} from "./core.mjs";
import catalogue from "./catalogue.json";

// One global object serializes reservations across isolates; origin checks alone are not authentication.
export class Quota {
  constructor(ctx){this.ctx=ctx;}
  async fetch(request){
    const {client}=await request.json();
    if(typeof client!=="string"||!/^[a-f0-9]{64}$/.test(client))return new Response(null,{status:400});
    const result=await this.ctx.storage.transaction(async tx=>{
      const next=takeQuota(await tx.get("budget"),client);
      if(next.ok)await tx.put("budget",next.state);
      return {ok:next.ok,message:next.message,retry:next.retry};
    });
    return Response.json(result);
  }
}

export default {async fetch(request,env){
  return handleRequest(request,env,{
    catalogue,
    async reserve(ip){
      const key=await crypto.subtle.importKey("raw",new TextEncoder().encode(env.IP_SALT),{name:"HMAC",hash:"SHA-256"},false,["sign"]);
      const value=new TextEncoder().encode(`${new Date().toISOString().slice(0,10)}:${ip}`);
      const hash=await crypto.subtle.sign("HMAC",key,value);
      const client=[...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,"0")).join("");
      const stub=env.QUOTA.get(env.QUOTA.idFromName("public-budget-v1"));
      const response=await stub.fetch("https://quota/reserve",{method:"POST",body:JSON.stringify({client})});
      if(!response.ok)throw new Error("Quota unavailable");
      return response.json();
    },
    provider(body){return fetch("https://api.deepseek.com/chat/completions",{method:"POST",headers:{Authorization:`Bearer ${env.DEEPSEEK_API_KEY}`,"Content-Type":"application/json"},body:JSON.stringify(body),signal:AbortSignal.timeout(25000)});},
  });
}};
