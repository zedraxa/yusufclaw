export const chatPage = `<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YusufClaw chat</title><link rel="stylesheet" href="/style.css">
<main><h1>YusufClaw</h1><p>Private chat · /reset clears this conversation</p>
<form id="chat"><label>Access token <input id="token" type="password" autocomplete="off" required></label>
<label>Conversation <input id="session" value="default" maxlength="128" required></label>
<label>Message <textarea id="message" maxlength="16000" required></textarea></label>
<button id="send">Send</button></form><p id="status" role="status"></p>
<div id="replies" aria-live="polite"></div><h2>Scheduled replies</h2><div id="routines"></div></main>
<script src="/app.js" defer></script></html>`;
export const chatStyle = `body{font:16px system-ui;background:#f5f5f4;color:#202020;margin:0}main{max-width:760px;margin:3rem auto;padding:1rem}label{display:block;margin:1rem 0}input,textarea,button{font:inherit;box-sizing:border-box;padding:.6rem}input,textarea{display:block;width:100%}textarea{min-height:100px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:1rem;border:1px solid #ddd}button{cursor:pointer}`;
export const chatScript = `const el=id=>document.getElementById(id);
const headers=()=>({'Content-Type':'application/json','Authorization':'Bearer '+el('token').value});
function append(parent,label,text){const box=document.createElement('pre');box.textContent=label+'\\n'+text;el(parent).append(box);}
el('chat').addEventListener('submit',async event=>{
 event.preventDefault();el('send').disabled=true;el('status').textContent='Thinking…';
 const text=el('message').value;
 try{const response=await fetch('/api/chat',{method:'POST',headers:headers(),body:JSON.stringify({text,conversationId:el('session').value,requestId:crypto.randomUUID()})});
 const data=await response.json();if(!response.ok)throw new Error(data.error);
 append('replies','You',text);append('replies','YusufClaw',data.reply);el('message').value='';el('status').textContent='';
 }catch(error){el('status').textContent=error.message||'Request failed';}finally{el('send').disabled=false;}
});
async function poll(){if(!el('token').value)return;try{const response=await fetch('/api/routines',{headers:headers()});if(!response.ok)return;
 const items=await response.json();el('routines').replaceChildren();for(const item of items)append('routines',item.conversationId,item.reply);
 }catch{}}
setInterval(poll,5000);el('token').addEventListener('change',poll);`;
