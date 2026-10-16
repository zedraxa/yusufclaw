"""Minimal web page HTML, CSS, and JS assets for YusufClaw web gateway."""

CHAT_PAGE = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YusufClaw chat</title><link rel="stylesheet" href="/style.css">
<main><h1>YusufClaw</h1><p>Private chat · AG-UI 1.0 streaming · /reset clears conversation</p>
<form id="chat"><label>Access token <input id="token" type="password" autocomplete="off" required></label>
<label>Conversation <input id="session" value="default" maxlength="128" required></label>
<label>Message <textarea id="message" maxlength="16000" required></textarea></label>
<button id="send">Send</button></form><p id="status" role="status"></p>
<div id="approvals" aria-live="assertive"></div>
<div id="tools" aria-live="polite"></div>
<div id="replies" aria-live="polite"></div>
<h2>Scheduled replies</h2><div id="routines"></div></main>
<script src="/app.js" defer></script></html>"""

CHAT_STYLE = """body{font:16px system-ui;background:#f5f5f4;color:#202020;margin:0}
main{max-width:760px;margin:3rem auto;padding:1rem}
label{display:block;margin:1rem 0}
input,textarea,button{font:inherit;box-sizing:border-box;padding:.6rem}
input,textarea{display:block;width:100%}
textarea{min-height:100px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:1rem;border:1px solid #ddd}
button{cursor:pointer}
.card{background:#fff;border:2px solid #e0a800;padding:1rem;margin:1rem 0}
.card-title{font-weight:bold;margin-bottom:.5rem}
.card-exact{background:#f8f9fa;padding:.5rem;border:1px solid #eee;font-family:monospace;font-size:.9rem}
.card-meta{margin:.5rem 0;font-size:.9rem}
.card-actions{margin-top:.8rem;display:flex;gap:.8rem;align-items:center}
.btn-approve{background:#28a745;color:#fff;border:none;padding:.5rem 1rem}
.btn-deny{background:#dc3545;color:#fff;border:none;padding:.5rem 1rem}
.tool-call{background:#fff;border-left:4px solid #007bff;padding:.5rem 1rem;margin:.5rem 0;font-size:.9rem}
.tool-name{font-weight:bold}
.tool-args{font-family:monospace;color:#555}
.tool-result{margin-top:.3rem;color:#333}
.tool-success{border-left-color:#28a745}
.tool-error{border-left-color:#dc3545}
.tool-denied{border-left-color:#ffc107}"""

CHAT_SCRIPT = """const el=id=>document.getElementById(id);
const headers=()=>({'Content-Type':'application/json','Authorization':'Bearer '+el('token').value});
function append(parent,label,text){const box=document.createElement('pre');box.textContent=label+'\\n'+text;el(parent).append(box);return box;}
function appendTool(parent,toolName,id){
  const box=document.createElement('div');box.className='tool-call';box.id='tool-'+id;
  const title=document.createElement('div');title.className='tool-name';title.textContent='⚙️ Tool: '+toolName;
  box.append(title);el(parent).append(box);return box;
}
function renderCard(data){
  const card=document.createElement('div');card.className='card';card.id='card-'+data.approvalId;
  const title=document.createElement('div');title.className='card-title';
  title.textContent='⚠️ Approval Required: '+(data.rendered?.title||data.toolId||'Action');
  const exact=document.createElement('div');exact.className='card-exact';exact.textContent=data.rendered?.exact||'';
  const meta=document.createElement('div');meta.className='card-meta';
  const taint=data.rendered?.taint==='contaminated'?'🔴 Contaminated':'🟢 Clean';
  const rev=data.rendered?.reversible?'Yes':'No (Irreversible)';
  const eff=(data.rendered?.effects||[]).join(', ')||'none';
  meta.textContent='Taint: '+taint+' · Reversible: '+rev+' · Effects: '+eff;
  const actions=document.createElement('div');actions.className='card-actions';
  const btnApprove=document.createElement('button');btnApprove.className='btn-approve';btnApprove.textContent='✅ Approve';
  const btnDeny=document.createElement('button');btnDeny.className='btn-deny';btnDeny.textContent='❌ Deny';
  const cardStatus=document.createElement('span');
  actions.append(btnApprove,btnDeny,cardStatus);
  card.append(title,exact,meta,actions);
  async function submitDecision(action){
    btnApprove.disabled=true;btnDeny.disabled=true;cardStatus.textContent='Submitting…';
    try{
      const res=await fetch('/api/chat/approval',{method:'POST',headers:headers(),body:JSON.stringify({approvalId:data.approvalId,nonce:data.nonce,action:action})});
      const resData=await res.json().catch(()=>({}));
      if(!res.ok)throw new Error(resData.error||'Failed');
      cardStatus.textContent=action==='approve'?'Approved ✅':'Denied ❌';
    }catch(err){
      cardStatus.textContent='Error: '+(err.message||'Failed');
      btnApprove.disabled=false;btnDeny.disabled=false;
    }
  }
  btnApprove.addEventListener('click',()=>submitDecision('approve'));
  btnDeny.addEventListener('click',()=>submitDecision('deny'));
  el('approvals').append(card);
}
el('chat').addEventListener('submit',async event=>{
  event.preventDefault();el('send').disabled=true;el('status').textContent='Thinking…';
  const text=el('message').value;append('replies','You',text);el('message').value='';
  let assistantBox=null;
  try{
    const response=await fetch('/api/chat/stream',{method:'POST',headers:headers(),body:JSON.stringify({text,conversationId:el('session').value,requestId:crypto.randomUUID()})});
    if(!response.ok){
      const errData=await response.json().catch(()=>({error:'Request failed'}));
      throw new Error(errData.error||'Request failed');
    }
    const reader=response.body.getReader();const decoder=new TextDecoder();let buf='';
    while(true){
      const {done,value}=await reader.read();if(done)break;
      buf+=decoder.decode(value,{stream:true});
      const parts=buf.split('\\n\\n');buf=parts.pop();
      for(const block of parts){
        let evName='';let evData='';
        for(const line of block.split('\\n')){
          if(line.startsWith('event: '))evName=line.slice(7).trim();
          else if(line.startsWith('data: '))evData=line.slice(6).trim();
        }
        if(!evName||!evData)continue;
        let data={};try{data=JSON.parse(evData);}catch{data={raw:evData};}
        if(evName==='RunStarted'){el('status').textContent='Run started…';}
        else if(evName==='progress'){el('status').textContent=(data.status||'Thinking')+'…';}
        else if(evName==='ToolCallStart'){
          el('status').textContent='Running tool: '+(data.toolName||'')+'…';
          appendTool('tools',data.toolName,data.toolCallId);
        }else if(evName==='ToolCallArgs'){
          const tb=el('tool-'+data.toolCallId);
          if(tb){const a=document.createElement('div');a.className='tool-args';a.textContent='Args: '+JSON.stringify(data.args);tb.append(a);}
        }else if(evName==='ToolCallEnd'){
          const tb=el('tool-'+data.toolCallId);
          if(tb){
            tb.classList.add('tool-'+(data.status==='success'?'success':(data.status==='denied'?'denied':'error')));
            const r=document.createElement('div');r.className='tool-result';r.textContent='Result ('+data.status+'): '+(data.result||'');
            tb.append(r);
          }
          el('status').textContent='Tool '+(data.toolName||'')+' ('+(data.status||'')+').';
        }else if(evName==='ActionCard'||evName==='approval'){
          renderCard(data);el('status').textContent='Approval required…';
        }else if(evName==='TextMessageStart'){
          if(!assistantBox)assistantBox=append('replies','YusufClaw','');
        }else if(evName==='TextMessageContent'){
          if(!assistantBox)assistantBox=append('replies','YusufClaw','');
          assistantBox.textContent+=data.delta||'';el('status').textContent='Streaming…';
        }else if(evName==='TextMessageEnd'){
          if(data.text&&assistantBox)assistantBox.textContent='YusufClaw\\n'+data.text;
        }else if(evName==='message'){
          if(!assistantBox)assistantBox=append('replies','YusufClaw',data.reply||'');
          else if(assistantBox.textContent==='YusufClaw\\n')assistantBox.textContent='YusufClaw\\n'+(data.reply||'');
        }else if(evName==='done'||evName==='RunFinished'){
          if(data.status==='error')el('status').textContent=data.error||'Run failed';
          else el('status').textContent='';
        }else if(evName==='error'){
          el('status').textContent=data.error||'Request failed';
        }
      }
    }
  }catch(error){el('status').textContent=error.message||'Request failed';}finally{el('send').disabled=false;}
});
async function poll(){
  if(!el('token').value)return;
  try{
    const response=await fetch('/api/routines',{headers:headers()});if(!response.ok)return;
    const items=await response.json();el('routines').replaceChildren();for(const item of items)append('routines',item.conversationId,item.reply);
  }catch{}
}
setInterval(poll,5000);el('token').addEventListener('change',poll);"""

# Aliases matching TS camelCase exports
chatPage = CHAT_PAGE
chatStyle = CHAT_STYLE
chatScript = CHAT_SCRIPT

__all__ = [
    "CHAT_PAGE",
    "CHAT_SCRIPT",
    "CHAT_STYLE",
    "chatPage",
    "chatScript",
    "chatStyle",
]
