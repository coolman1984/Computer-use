const {getJSON,postJSON,statusBadge,formatDate,el,link,showError}=SmartOps;
const labels={draft:["Draft","gray"],starting:["Launching","blue"],recording:["Recording","red"],paused:["Paused","yellow"],stopping:["Saving","blue"],completed:["Completed","green"],failed:["Failed","red"],interrupted:["Interrupted","orange"]};
async function load(){const body=document.querySelector('#rows');try{const data=await getJSON('/api/recordings?include_deleted='+document.querySelector('#deleted').checked);body.innerHTML='';if(!data.items.length)body.append(el('tr',{},[el('td',{colspan:'7',class:'empty'},['No recordings yet'])]));for(const r of data.items){const action=r.deleted_at?el('button',{},['Restore']):link('Open','recording.html?id='+encodeURIComponent(r.id));if(r.deleted_at)action.onclick=async()=>{await postJSON('/api/recordings/'+r.id+'/restore',{});load()};body.append(el('tr',{},[el('td',{},[r.name]),el('td',{},[r.system_key]),el('td',{},['v'+r.version]),el('td',{},[statusBadge(labels,r.status)]),el('td',{},[String(r.step_count)]),el('td',{},[formatDate(r.created_at)]),el('td',{},[action])]))}}catch(e){showError(document.querySelector('#error'),e)}}
document.querySelector('#create-form').onsubmit=async e=>{e.preventDefault();try{const r=await postJSON('/api/recordings',{name:name.value,system_key:system.value});location.href='recording.html?id='+encodeURIComponent(r.id)}catch(err){showError(document.querySelector('#error'),err)}};
document.querySelector('#deleted').onchange=load;
// The system list is the recording's entry point: manager._system_url() looks
// the key up and silently falls back to about:blank when it is not a real
// defined system, which used to make the hardcoded "local" option produce an
// empty recording. Only offer systems that actually exist, and say so plainly
// when none do instead of leaving an empty required dropdown.
getJSON('/api/systems').then(x=>{
  const note=document.querySelector('#system-note'), submit=document.querySelector('#create-form button[type=submit]'), select=document.querySelector('#system');
  for(const s of x.items)select.append(el('option',{value:s.key},[(s.name||s.key)+' ('+s.key+')']));
  const empty=!x.items.length;
  select.disabled=empty;submit.disabled=empty;
  note.textContent=empty?'No systems are defined yet, so there is nothing to record against. Add a .yaml file to your systems directory and restart the server.':'';
}).catch(e=>showError(document.querySelector('#error'),e)).finally(load);
