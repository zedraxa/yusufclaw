"""Server-rendered HTML, CSS, and JS assets for YusufClaw AG-UI 1.0 web gateway.

Provides:
- Named specialist agents sidebar with session isolation (UX-02).
- Immediate cognitive acknowledgement & background milestone stepper (UX-03).
- Inline question & option decision widgets (UX-04).
- Verifiable action cards with 5-minute countdown and draft edit (UX-05).
- Masked secret-request modal with local credential broker integration (UX-06).
Strictly adheres to CSP: zero innerHTML, zero localStorage/sessionStorage, all textContent.
"""

CHAT_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YusufClaw Workspace · AG-UI 1.0</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
<div class="layout-wrapper">
  <aside id="agents-sidebar" class="sidebar">
    <div class="sidebar-header">
      <h2>Agents</h2>
      <span class="badge-live">AG-UI 1.0</span>
    </div>
    <div id="agents-list" class="agents-list" role="navigation" aria-label="Specialist Agents"></div>
    <div class="sidebar-footer">
      <small>Roles Catalog · Least-Privilege</small>
    </div>
  </aside>

  <main class="main-canvas">
    <header class="workspace-header">
      <div class="header-titles">
        <h1>YusufClaw</h1>
        <span id="current-agent" class="agent-active-badge">Active: YusufClaw (orchestrator)</span>
      </div>
      <p class="header-subtitle">Private Autonomous Assistant · AG-UI 1.0 Streaming</p>
    </header>

    <div id="ack-banner" class="ack-banner" style="display:none;" role="status"></div>
    <div id="milestones" class="milestone-stepper" aria-live="polite"></div>

    <form id="chat" class="chat-form">
      <div class="form-row">
        <label>Access token
          <input id="token" type="password" autocomplete="off" required>
        </label>
        <label>Conversation ID
          <input id="session" value="default" maxlength="128" required>
        </label>
      </div>
      <label>Message
        <textarea id="message" maxlength="16000" placeholder="Type instructions or task for the active specialist agent..." required></textarea>
      </label>
      <div class="form-actions">
        <button id="send" type="submit">Send Task</button>
        <span id="status" class="status-indicator" role="status"></span>
      </div>
    </form>

    <div id="questions" class="questions-container" aria-live="assertive"></div>
    <div id="approvals" class="approvals-container" aria-live="assertive"></div>
    <div id="tools" class="tools-container" aria-live="polite"></div>
    <div id="replies" class="replies-container" aria-live="polite"></div>

    <section class="routines-section">
      <h2>Scheduled replies &amp; Outbox</h2>
      <div id="routines" class="routines-container"></div>
    </section>
  </main>
</div>

<div id="secret-modal" class="modal-overlay" style="display:none;" role="dialog" aria-modal="true" aria-labelledby="modal-title">
  <div class="modal-card">
    <h3 id="modal-title">🔒 Masked Secret Request</h3>
    <p class="modal-notice">A tool requires authentication credentials. Credentials are saved directly to the local broker and never enter conversation history or model context.</p>
    <div class="modal-meta">
      <div><strong>Key:</strong> <span id="modal-key-name"></span></div>
      <div><strong>Target:</strong> <span id="modal-target"></span></div>
      <div><strong>Description:</strong> <span id="modal-desc"></span></div>
      <div class="policy-note"><strong>Storage Policy:</strong> <span id="modal-policy"></span></div>
    </div>
    <label>Credential Value:
      <div class="masked-input-wrapper">
        <input id="modal-secret-value" type="password" autocomplete="off" placeholder="Paste credential (masked)">
        <button type="button" id="toggle-secret-visibility" class="btn-toggle-vis" title="Toggle visibility">👁️</button>
      </div>
    </label>
    <div class="modal-actions">
      <button type="button" id="modal-submit-secret" class="btn-approve">Submit Secret</button>
      <button type="button" id="modal-cancel-secret" class="btn-deny">Cancel</button>
    </div>
  </div>
</div>

<script src="/app.js" defer></script>
</body>
</html>"""

CHAT_STYLE = """body{font:15px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:#f5f5f4;color:#202020;margin:0;line-height:1.5}
.layout-wrapper{display:flex;min-height:100vh}
.sidebar{width:260px;background:#1e1e24;color:#e5e5e5;display:flex;flex-direction:column;border-right:1px solid #333;flex-shrink:0}
.sidebar-header{padding:1.2rem;border-bottom:1px solid #333;display:flex;align-items:center;justify-content:space-between}
.sidebar-header h2{margin:0;font-size:1.1rem;font-weight:600}
.badge-live{background:#007bff;color:#fff;font-size:.7rem;padding:.2rem .4rem;border-radius:4px;font-weight:bold}
.agents-list{flex:1;overflow-y:auto;padding:.5rem}
.agent-item{padding:.75rem .9rem;border-radius:6px;cursor:pointer;margin-bottom:.4rem;transition:background .15s}
.agent-item:hover{background:#2a2a35}
.agent-item.active{background:#3a3a48;border-left:4px solid #28a745}
.agent-name{font-weight:600;font-size:.9rem;color:#fff}
.agent-model{font-size:.75rem;color:#aaa;margin-top:.2rem}
.sidebar-footer{padding:1rem;border-top:1px solid #333;font-size:.75rem;color:#777}
.main-canvas{flex:1;max-width:840px;margin:1.5rem auto;padding:0 1.5rem}
.workspace-header{margin-bottom:1.5rem}
.header-titles{display:flex;align-items:center;gap:1rem}
.header-titles h1{margin:0;font-size:1.8rem}
.agent-active-badge{background:#e0e7ff;color:#3730a3;font-size:.85rem;padding:.3rem .6rem;border-radius:6px;font-weight:600}
.header-subtitle{margin:.4rem 0 0 0;color:#666;font-size:.9rem}
.ack-banner{background:#e6f4ea;color:#137333;border:1px solid #ceead6;padding:.7rem 1rem;border-radius:6px;margin-bottom:1rem;font-size:.9rem;font-weight:500}
.milestone-stepper{margin-bottom:1rem}
.milestone-item{background:#fff;border-left:4px solid #007bff;padding:.6rem 1rem;margin-bottom:.5rem;border-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.05);font-size:.9rem}
.milestone-item.completed{border-left-color:#28a745}
.milestone-item.failed{border-left-color:#dc3545}
.form-row{display:flex;gap:1rem}
.form-row label{flex:1}
label{display:block;margin:.8rem 0;font-weight:500;font-size:.9rem}
input,textarea,button{font:inherit;box-sizing:border-box;padding:.6rem;border-radius:6px}
input,textarea{display:block;width:100%;border:1px solid #ccc;background:#fff}
textarea{min-height:90px}
.form-actions{display:flex;align-items:center;gap:1rem;margin-top:.6rem}
button{cursor:pointer;border:none;background:#202020;color:#fff;font-weight:600;padding:.6rem 1.2rem}
button:hover{opacity:.9}
button:disabled{opacity:.5;cursor:not-allowed}
.status-indicator{font-size:.9rem;color:#555}
.card{background:#fff;border:2px solid #e0a800;border-radius:8px;padding:1.2rem;margin:1rem 0;box-shadow:0 2px 4px rgba(0,0,0,.06)}
.card-title{font-weight:bold;font-size:1.05rem;margin-bottom:.6rem;display:flex;align-items:center;justify-content:space-between}
.countdown-timer{font-size:.85rem;background:#fff3cd;color:#856404;padding:.2rem .5rem;border-radius:4px;border:1px solid #ffeeba}
.card-exact{background:#f8f9fa;padding:.6rem;border:1px solid #eee;font-family:monospace;font-size:.85rem;border-radius:4px;white-space:pre-wrap;overflow-x:auto}
.card-diff{background:#2b2b2b;color:#f8f8f2;padding:.6rem;border-radius:4px;font-family:monospace;font-size:.8rem;margin-top:.5rem;white-space:pre-wrap}
.card-meta{margin:.6rem 0;font-size:.85rem;color:#555}
.card-actions{margin-top:.8rem;display:flex;gap:.8rem;align-items:center;flex-wrap:wrap}
.edit-draft-box{margin-top:.8rem;display:none}
.edit-draft-box textarea{font-family:monospace;font-size:.85rem;min-height:70px}
.btn-approve{background:#28a745;color:#fff}
.btn-deny{background:#dc3545;color:#fff}
.btn-edit{background:#6c757d;color:#fff}
.question-card{background:#fff;border:2px solid #007bff;border-radius:8px;padding:1.2rem;margin:1rem 0}
.question-prompt{font-weight:bold;margin-bottom:.8rem;font-size:1rem}
.options-chips{display:flex;flex-wrap:wrap;gap:.6rem;margin-bottom:.8rem}
.option-chip{background:#f1f3f5;color:#333;border:1px solid #ced4da;border-radius:20px;padding:.5rem 1rem;font-size:.85rem;cursor:pointer}
.option-chip:hover{background:#e9ecef}
.option-chip.recommended{border-color:#28a745;background:#e8f5e9;font-weight:600}
.option-chip.selected{background:#007bff;color:#fff;border-color:#0056b3}
.tool-call{background:#fff;border-left:4px solid #007bff;padding:.6rem 1rem;margin:.5rem 0;border-radius:4px;font-size:.9rem}
.tool-name{font-weight:bold}
.tool-args{font-family:monospace;color:#555;font-size:.85rem}
.tool-result{margin-top:.3rem;color:#333;font-size:.85rem}
.tool-success{border-left-color:#28a745}
.tool-error{border-left-color:#dc3545}
.tool-denied{border-left-color:#ffc107}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:1rem;border-radius:6px;border:1px solid #ddd;font-size:.9rem}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:9999}
.modal-card{background:#fff;border-radius:8px;max-width:540px;width:90%;padding:1.5rem;box-shadow:0 10px 25px rgba(0,0,0,.2)}
.modal-card h3{margin-top:0}
.modal-notice{font-size:.85rem;color:#666;line-height:1.4}
.modal-meta{background:#f8f9fa;padding:.8rem;border-radius:6px;font-size:.85rem;margin:.8rem 0}
.policy-note{margin-top:.4rem;color:#007bff}
.masked-input-wrapper{display:flex;gap:.5rem;align-items:center}
.btn-toggle-vis{background:#eee;color:#333;padding:.6rem .8rem}
.modal-actions{display:flex;justify-content:flex-end;gap:.8rem;margin-top:1.2rem}
.routines-section{margin-top:2rem;border-top:1px solid #ddd;padding-top:1rem}"""

CHAT_SCRIPT = """const el=id=>document.getElementById(id);
const headers=()=>({'Content-Type':'application/json','Authorization':'Bearer '+el('token').value});
let activeAgentId='orchestrator';
let pendingSecretRequestId=null;
function append(parent,label,text){
  const box=document.createElement('pre');
  box.textContent=label+'\\n'+text;
  el(parent).append(box);
  return box;
}
function appendTool(parent,toolName,id){
  const box=document.createElement('div');
  box.className='tool-call';
  box.id='tool-'+id;
  const title=document.createElement('div');
  title.className='tool-name';
  title.textContent='⚙️ Tool: '+toolName;
  box.append(title);
  el(parent).append(box);
  return box;
}
async function loadAgents(){
  if(!el('token').value)return;
  try{
    const res=await fetch('/api/agents',{headers:headers()});
    if(!res.ok)return;
    const agents=await res.json();
    const list=el('agents-list');
    list.replaceChildren();
    for(const a of agents){
      const item=document.createElement('div');
      item.className='agent-item'+(a.agentId===activeAgentId?' active':'');
      item.id='agent-'+a.agentId;
      const name=document.createElement('div');
      name.className='agent-name';
      name.textContent=(a.name||a.agentId);
      const model=document.createElement('div');
      model.className='agent-model';
      model.textContent=(a.model||a.role||'')+' · '+(a.status||'online');
      item.append(name,model);
      item.addEventListener('click',()=>selectAgent(a.agentId,a.name));
      list.append(item);
    }
  }catch(err){}
}
function selectAgent(id,name){
  activeAgentId=id;
  if(el('current-agent'))el('current-agent').textContent='Active: '+(name||id);
  const items=document.querySelectorAll('.agent-item');
  items.forEach(it=>it.classList.remove('active'));
  const cur=el('agent-'+id);
  if(cur)cur.classList.add('active');
  append('replies','System','Switched to agent context: '+(name||id)+' (session: web:local:'+activeAgentId+':'+el('session').value+')');
}
function updateMilestone(data){
  const list=el('milestones');
  let m=el('milestone-step-'+data.step);
  if(!m){
    m=document.createElement('div');
    m.className='milestone-item';
    m.id='milestone-step-'+data.step;
    list.append(m);
  }
  m.classList.remove('completed','failed');
  if(data.status==='completed')m.classList.add('completed');
  if(data.status==='failed')m.classList.add('failed');
  const icon=data.status==='completed'?'✅ ':(data.status==='failed'?'❌ ':'⏳ ');
  m.textContent=icon+'Step '+data.step+': '+(data.title||'Progress')+(data.detail?' — '+data.detail:'');
}
function renderCard(data){
  const card=document.createElement('div');
  card.className='card';
  card.id='card-'+data.approvalId;
  const title=document.createElement('div');
  title.className='card-title';
  const titleSpan=document.createElement('span');
  titleSpan.textContent='⚠️ Approval: '+(data.rendered?.title||data.toolId||'Action');
  const timerSpan=document.createElement('span');
  timerSpan.className='countdown-timer';
  let remainingSeconds=300;
  timerSpan.textContent='TTL: 05:00';
  title.append(titleSpan,timerSpan);
  const exact=document.createElement('div');
  exact.className='card-exact';
  exact.textContent=data.rendered?.exact||'';
  card.append(title,exact);
  if(data.rendered?.diff){
    const diff=document.createElement('pre');
    diff.className='card-diff';
    diff.textContent=data.rendered.diff;
    card.append(diff);
  }
  const meta=document.createElement('div');
  meta.className='card-meta';
  const taint=data.rendered?.taint==='contaminated'?'🔴 Contaminated':'🟢 Clean';
  const rev=data.rendered?.reversible?'Yes':'No (Irreversible)';
  const eff=(data.rendered?.effects||[]).join(', ')||'none';
  meta.textContent='Taint: '+taint+' · Reversible: '+rev+' · Effects: '+eff;
  const editBox=document.createElement('div');
  editBox.className='edit-draft-box';
  const editLabel=document.createElement('label');
  editLabel.textContent='Edit Arguments (JSON):';
  const editText=document.createElement('textarea');
  editText.value=data.rendered?.exact||'';
  editBox.append(editLabel,editText);
  const actions=document.createElement('div');
  actions.className='card-actions';
  const btnApprove=document.createElement('button');
  btnApprove.className='btn-approve';
  btnApprove.textContent='✅ Approve';
  const btnEdit=document.createElement('button');
  btnEdit.className='btn-edit';
  btnEdit.type='button';
  btnEdit.textContent='✏️ Edit Draft';
  const btnDeny=document.createElement('button');
  btnDeny.className='btn-deny';
  btnDeny.textContent='❌ Deny';
  const cardStatus=document.createElement('span');
  actions.append(btnApprove,btnEdit,btnDeny,cardStatus);
  card.append(meta,editBox,actions);
  let timerId=setInterval(()=>{
    remainingSeconds--;
    if(remainingSeconds<=0){
      clearInterval(timerId);
      timerSpan.textContent='Expired';
      btnApprove.disabled=true;
      btnDeny.disabled=true;
      btnEdit.disabled=true;
      cardStatus.textContent='Action Card Expired';
    }else{
      const m=Math.floor(remainingSeconds/60);
      const s=remainingSeconds%60;
      timerSpan.textContent='TTL: '+(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
    }
  },1000);
  btnEdit.addEventListener('click',()=>{
    const cur=editBox.style.display;
    editBox.style.display=(cur==='block'?'none':'block');
  });
  async function submitDecision(action){
    clearInterval(timerId);
    btnApprove.disabled=true;
    btnDeny.disabled=true;
    btnEdit.disabled=true;
    cardStatus.textContent='Submitting…';
    let editedArgs=null;
    if(action==='approve'&&editBox.style.display==='block'&&editText.value.trim()){
      try{editedArgs=JSON.parse(editText.value.trim());}catch(err){}
    }
    const payload={
      approvalId:data.approvalId,
      nonce:data.nonce,
      decision:action,
      action:action
    };
    if(editedArgs)payload.editedArgs=editedArgs;
    try{
      const approvalEndpoint='/api/actions/resolve-approval'; // alias /api/chat/approval
      const res=await fetch(approvalEndpoint,{
        method:'POST',
        headers:headers(),
        body:JSON.stringify(payload)
      });
      const resData=await res.json().catch(()=>({}));
      if(!res.ok)throw new Error(resData.error||'Failed');
      cardStatus.textContent=action==='approve'?'Approved ✅':'Denied ❌';
    }catch(err){
      cardStatus.textContent='Error: '+(err.message||'Failed');
      btnApprove.disabled=false;
      btnDeny.disabled=false;
    }
  }
  btnApprove.addEventListener('click',()=>submitDecision('approve'));
  btnDeny.addEventListener('click',()=>submitDecision('deny'));
  el('approvals').append(card);
}
function renderQuestion(data){
  const card=document.createElement('div');
  card.className='question-card';
  card.id='question-'+data.questionId;
  const prompt=document.createElement('div');
  prompt.className='question-prompt';
  prompt.textContent='❓ '+(data.prompt||'Question:');
  card.append(prompt);
  const chips=document.createElement('div');
  chips.className='options-chips';
  let selected=[];
  for(const opt of data.options||[]){
    const chip=document.createElement('button');
    chip.type='button';
    chip.className='option-chip'+(opt.recommended?' recommended':'');
    chip.textContent=opt.label+(opt.recommended?' (Recommended)':'');
    chip.addEventListener('click',()=>{
      if(data.isMultiSelect){
        if(selected.includes(opt.id)){
          selected=selected.filter(x=>x!==opt.id);
          chip.classList.remove('selected');
        }else{
          selected.push(opt.id);
          chip.classList.add('selected');
        }
      }else{
        selected=[opt.id];
        submitQuestionChoice(data.questionId,selected,null,card);
      }
    });
    chips.append(chip);
  }
  card.append(chips);
  if(data.isMultiSelect){
    const submitBtn=document.createElement('button');
    submitBtn.type='button';
    submitBtn.className='btn-approve';
    submitBtn.textContent='Submit Selection';
    submitBtn.addEventListener('click',()=>submitQuestionChoice(data.questionId,selected,null,card));
    card.append(submitBtn);
  }
  el('questions').append(card);
}
async function submitQuestionChoice(qId,selectedOptionIds,customText,container){
  container.querySelectorAll('button').forEach(b=>b.disabled=true);
  const status=document.createElement('div');
  status.className='status-indicator';
  status.textContent='Submitting choice…';
  container.append(status);
  try{
    const res=await fetch('/api/actions/resolve-widget',{
      method:'POST',
      headers:headers(),
      body:JSON.stringify({questionId:qId,selectedOptionIds:selectedOptionIds,customText:customText})
    });
    const resData=await res.json().catch(()=>({}));
    if(!res.ok)throw new Error(resData.error||'Failed');
    status.textContent='Decision locked: '+(selectedOptionIds.join(', ')||'Submitted');
  }catch(err){
    status.textContent='Error: '+(err.message||'Failed');
    container.querySelectorAll('button').forEach(b=>b.disabled=false);
  }
}
function showSecretModal(data){
  pendingSecretRequestId=data.requestId;
  el('modal-key-name').textContent=data.keyName||'SECRET_KEY';
  el('modal-target').textContent=data.targetHost||data.serviceName||'Local Broker';
  el('modal-desc').textContent=data.description||'Required credential for integration';
  el('modal-policy').textContent=data.storagePolicy||'Encrypted local storage; excluded from LLM context';
  el('modal-secret-value').value='';
  el('modal-secret-value').type='password';
  el('secret-modal').style.display='flex';
}
el('toggle-secret-visibility').addEventListener('click',()=>{
  const inp=el('modal-secret-value');
  inp.type=(inp.type==='password'?'text':'password');
});
el('modal-cancel-secret').addEventListener('click',()=>{
  el('secret-modal').style.display='none';
  el('modal-secret-value').value='';
});
el('modal-submit-secret').addEventListener('click',async()=>{
  const val=el('modal-secret-value').value;
  if(!val)return;
  el('modal-submit-secret').disabled=true;
  try{
    const res=await fetch('/api/actions/secret-submit',{
      method:'POST',
      headers:headers(),
      body:JSON.stringify({
        requestId:pendingSecretRequestId||crypto.randomUUID(),
        keyName:el('modal-key-name').textContent,
        secretValue:val
      })
    });
    const resData=await res.json().catch(()=>({}));
    if(!res.ok)throw new Error(resData.error||'Secret submission failed');
    el('secret-modal').style.display='none';
    el('modal-secret-value').value='';
    append('replies','System','[Key configured: '+(resData.keyName||'SECRET')+' (sha256: '+(resData.fingerprint||'ok')+')]');
  }catch(err){
    alert('Failed to submit secret: '+(err.message||'Error'));
  }finally{
    el('modal-submit-secret').disabled=false;
  }
});
el('chat').addEventListener('submit',async event=>{
  event.preventDefault();
  el('send').disabled=true;
  el('status').textContent='Thinking…';
  el('ack-banner').style.display='none';
  const text=el('message').value;
  append('replies','You',text);
  el('message').value='';
  let assistantBox=null;
  try{
    const response=await fetch('/api/chat/stream',{
      method:'POST',
      headers:headers(),
      body:JSON.stringify({
        text:text,
        conversationId:el('session').value,
        requestId:crypto.randomUUID(),
        agentId:activeAgentId
      })
    });
    if(!response.ok){
      const errData=await response.json().catch(()=>({error:'Request failed'}));
      throw new Error(errData.error||'Request failed');
    }
    const reader=response.body.getReader();
    const decoder=new TextDecoder();
    let buf='';
    while(true){
      const {done,value}=await reader.read();
      if(done)break;
      buf+=decoder.decode(value,{stream:true});
      const parts=buf.split('\\n\\n');
      buf=parts.pop();
      for(const block of parts){
        let evName='';
        let evData='';
        for(const line of block.split('\\n')){
          if(line.startsWith('event: '))evName=line.slice(7).trim();
          else if(line.startsWith('data: '))evData=line.slice(6).trim();
        }
        if(!evName||!evData)continue;
        let data={};
        try{data=JSON.parse(evData);}catch{data={raw:evData};}
        if(evName==='run.acknowledged'){
          el('ack-banner').style.display='block';
          el('ack-banner').textContent='⚡ YusufClaw received task: '+(data.intent||'Analyzing requirements...');
          el('status').textContent='Task acknowledged…';
          el('send').disabled=false;
        }else if(evName==='run.milestone'){
          updateMilestone(data);
          el('status').textContent=(data.title||'Milestone running')+'…';
        }else if(evName==='message.delta'){
          if(!assistantBox)assistantBox=append('replies','YusufClaw','');
          assistantBox.textContent+=data.delta||'';
          el('status').textContent='Streaming response…';
        }else if(evName==='widget.question'){
          renderQuestion(data);
          el('status').textContent='Awaiting user decision…';
        }else if(evName==='widget.approval_card'||evName==='ActionCard'||evName==='approval'){
          renderCard(data);
          el('status').textContent='Action approval required…';
        }else if(evName==='widget.secret_request'){
          showSecretModal(data);
          el('status').textContent='Credential requested…';
        }else if(evName==='RunStarted'){
          el('status').textContent='Run started…';
        }else if(evName==='progress'){
          el('status').textContent=(data.status||'Thinking')+'…';
        }else if(evName==='ToolCallStart'){
          el('status').textContent='Running tool: '+(data.toolName||'')+'…';
          appendTool('tools',data.toolName,data.toolCallId);
        }else if(evName==='ToolCallArgs'){
          const tb=el('tool-'+data.toolCallId);
          if(tb){
            const a=document.createElement('div');
            a.className='tool-args';
            a.textContent='Args: '+JSON.stringify(data.args);
            tb.append(a);
          }
        }else if(evName==='ToolCallEnd'){
          const tb=el('tool-'+data.toolCallId);
          if(tb){
            tb.classList.add('tool-'+(data.status==='success'?'success':(data.status==='denied'?'denied':'error')));
            const r=document.createElement('div');
            r.className='tool-result';
            r.textContent='Result ('+data.status+'): '+(data.result||'');
            tb.append(r);
          }
          el('status').textContent='Tool '+(data.toolName||'')+' ('+(data.status||'')+').';
        }else if(evName==='TextMessageStart'){
          if(!assistantBox)assistantBox=append('replies','YusufClaw','');
        }else if(evName==='TextMessageContent'){
          if(!assistantBox)assistantBox=append('replies','YusufClaw','');
          assistantBox.textContent+=data.delta||'';
          el('status').textContent='Streaming…';
        }else if(evName==='TextMessageEnd'){
          if(data.text&&assistantBox)assistantBox.textContent='YusufClaw\\n'+data.text;
        }else if(evName==='message'){
          if(!assistantBox)assistantBox=append('replies','YusufClaw',data.reply||'');
          else if(assistantBox.textContent==='YusufClaw\\n')assistantBox.textContent='YusufClaw\\n'+(data.reply||'');
        }else if(evName==='run.completed'||evName==='done'||evName==='RunFinished'){
          if(data.status==='error'||data.status==='failed')el('status').textContent=data.error||'Run failed';
          else el('status').textContent='Ready';
          el('send').disabled=false;
        }else if(evName==='error'){
          el('status').textContent=data.error||'Request failed';
          el('send').disabled=false;
        }
      }
    }
  }catch(error){
    el('status').textContent=error.message||'Request failed';
  }finally{
    el('send').disabled=false;
  }
});
async function poll(){
  if(!el('token').value)return;
  try{
    const response=await fetch('/api/routines',{headers:headers()});
    if(!response.ok)return;
    const items=await response.json();
    el('routines').replaceChildren();
    for(const item of items)append('routines',item.conversationId,item.reply);
  }catch(err){}
}
setInterval(poll,5000);
el('token').addEventListener('change',()=>{poll();loadAgents();});
window.addEventListener('DOMContentLoaded',loadAgents);"""

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
