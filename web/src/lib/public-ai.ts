// Public endpoint only. No provider credentials belong in NEXT_PUBLIC variables.
export const publicAiUrl = process.env.NEXT_PUBLIC_MUSE_AI_URL || "";
export type PublicMessage = {role:"user"|"assistant";content:string};
export async function askPublicAi(body:{workId:string;question:string;style:string;history:PublicMessage[]},signal:AbortSignal){
  const response=await fetch(publicAiUrl,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal,credentials:"omit"});
  let data;
  try{data=await response.json();}catch{throw new Error("AI 服务暂不可用，可以查看预置讲解。");}
  if(!response.ok)throw new Error(data.error||"AI 暂未完成回答，请稍后重试。");
  if(typeof data.answer!=="string"||data.kind!=="ai"||data.workId!==body.workId)throw new Error("回答格式异常，请稍后重试。");
  return data as {answer:string;kind:"ai";workId:string;source:string;notice:string};
}
