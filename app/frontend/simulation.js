'use strict';
const fields=[['Compression_Ratio','Compression ratio',':1',11.5,10.5,12.5,.5],['Hydrogen_Injection_Timing_deg_BTDC','Hydrogen injection timing','deg BTDC',160,60,160,5],['Engine_Load_pct','Engine fuel-load setting','%',75,0,100,25],['Engine_Speed_rpm','Engine speed','rpm',3000,2500,4500,500]];
for(const [i,f] of fields.entries()){
 const [key,label,unit,value,min,max,step]=f;
 document.getElementById('fields').insertAdjacentHTML('beforeend',`<div class="field"><label for="${key}">${label}<span>${unit}</span></label><input required type="number" id="${key}" name="${key}" value="${value}" min="${min}" max="${max}" step="${step}"></div>`);
}
let result=null,chartType='pressure',visual=null,playing=true,angle=0,last=performance.now();
const metrics=[['peak_pressure_bar','Peak cylinder pressure','bar'],['peak_temperature_k','Peak gas temperature','K'],['gross_imep_bar','Gross indicated MEP','bar'],['gross_indicated_power_kw','Gross indicated power · 4 cyl','kW']];
document.getElementById('metrics').innerHTML=metrics.map(([k,label,unit])=>`<div class="metric"><small>${label}</small><strong id="m-${k}">—</strong><em>${unit}</em></div>`).join('');
try{visual=window.initEngine3D();visual.isPlaying=false;visual.scene.background=new THREE.Color(0xedf4f8);visual.scene.traverse(object=>{if(object.type==='GridHelper'){for(const material of (Array.isArray(object.material)?object.material:[object.material])){material.vertexColors=false;material.color.set(0xc3d2df);material.needsUpdate=true;}}});document.getElementById('visual-fallback').hidden=true;}catch(e){document.getElementById('visual-fallback').textContent='3D preview unavailable. Numerical simulation and charts remain available.';}
function animate(now){if(playing)angle=(angle+(now-last)*.045)%720;last=now;if(visual)visual.setCrankAngle(angle);document.getElementById('angle').value=angle;document.getElementById('angle-label').textContent=`${Math.round(angle)}°`;requestAnimationFrame(animate);}requestAnimationFrame(animate);
document.getElementById('play').onclick=()=>{playing=!playing;document.getElementById('play').textContent=playing?'Pause':'Play';};
document.getElementById('angle').oninput=e=>{angle=Number(e.target.value);playing=false;document.getElementById('play').textContent='Play';};
document.getElementById('setup').addEventListener('input',()=>{document.getElementById('status').textContent='Settings changed. Run again to update results.';});
document.getElementById('reset').onclick=()=>{document.getElementById('setup').reset();document.getElementById('status').textContent='Defaults restored. Run again to update results.';};
document.getElementById('setup').onsubmit=async e=>{
 e.preventDefault();const button=document.getElementById('run');button.disabled=true;document.getElementById('status').textContent='Running Cantera with timed hydrogen injection...';
 try{
  const inputs=Object.fromEntries(fields.map(f=>[f[0],Number(document.getElementById(f[0]).value)]));
  const response=await fetch('/api/simulation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(inputs)});
  const data=await response.json();if(!response.ok)throw Error(data.error||'Simulation failed');result=data;
  for(const [key] of metrics)document.getElementById(`m-${key}`).textContent=data.summary[key].toFixed(key==='peak_temperature_k'?0:2);
  document.getElementById('balance').textContent=`Energy residual: ${data.summary.energy_residual_j.toExponential(2)} J / injection mass error: ${data.numerical_checks.injection_mass_relative_error.toExponential(2)}`;
  document.getElementById('csv').disabled=false;document.getElementById('json').disabled=false;
  document.getElementById('status').textContent=`Cantera ${data.cantera_version} / ${data.combustion_status.replaceAll('_',' ')} / ${(data.summary.final_h2_consumed_fraction*100).toFixed(1)}% scheduled H2 consumed`;
  draw();
 }catch(error){document.getElementById('status').textContent=`Could not run: ${error.message}`;}finally{button.disabled=false;}
};
const chart=document.getElementById('chart');
function draw(){const box=chart.getBoundingClientRect(),dpr=window.devicePixelRatio||1;chart.width=box.width*dpr;chart.height=box.height*dpr;const ctx=chart.getContext('2d');ctx.scale(dpr,dpr);const w=box.width,h=box.height;ctx.clearRect(0,0,w,h);if(!result){ctx.fillStyle='#52647a';ctx.font='12px system-ui';ctx.fillText('Run the model to generate a cycle trace.',55,h/2);return;}
 const config={pressure:['crank_angle_deg','pressure_bar','Crank angle (deg ATDC)','Pressure (bar)'],pv:['volume_cm3','pressure_bar','Cylinder volume (cm3)','Pressure (bar)'],temperature:['crank_angle_deg','temperature_k','Crank angle (deg ATDC)','Temperature (K)'],burn:['crank_angle_deg','mass_fraction_burned','Crank angle (deg ATDC)','Fraction of scheduled H2 consumed'],hydrogen:['crank_angle_deg','h2_mole_fraction','Crank angle (deg ATDC)','H2 mole fraction'],water:['crank_angle_deg','h2o_mole_fraction','Crank angle (deg ATDC)','H2O mole fraction'],injection:['crank_angle_deg','hydrogen_injection_rate_kg_s','Crank angle (deg ATDC)','H2 injection rate (kg/s)']}[chartType];
 const xs=result.trace.map(p=>p[config[0]]),ys=result.trace.map(p=>p[config[1]]);let xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=0,ymax=Math.max(...ys)*1.12||1;const pad={l:57,r:18,t:25,b:43},cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;const X=x=>pad.l+(x-xmin)/(xmax-xmin)*cw,Y=y=>pad.t+ch-(y-ymin)/(ymax-ymin)*ch;
 ctx.font='10px monospace';ctx.textAlign='right';for(let i=0;i<=4;i++){const y=ymax*i/4;ctx.strokeStyle='#dce4ed';ctx.beginPath();ctx.moveTo(pad.l,Y(y));ctx.lineTo(w-pad.r,Y(y));ctx.stroke();ctx.fillStyle='#52647a';ctx.fillText(y.toFixed(ymax>100?0:1),pad.l-9,Y(y)+4);}ctx.textAlign='center';for(let i=0;i<=4;i++){const x=xmin+(xmax-xmin)*i/4;ctx.fillText(x.toFixed(0),X(x),h-pad.b+19);}ctx.fillText(config[2],w/2,h-3);ctx.textAlign='left';ctx.fillText(config[3],pad.l,12);ctx.beginPath();result.trace.forEach((p,i)=>{if(i===0)ctx.moveTo(X(p[config[0]]),Y(p[config[1]]));else ctx.lineTo(X(p[config[0]]),Y(p[config[1]]));});ctx.strokeStyle='#087f6a';ctx.lineWidth=2;ctx.stroke();
 document.getElementById('chart-note').textContent=`Cantera / ${result.inputs.Engine_Speed_rpm} rpm / CR ${result.inputs.Compression_Ratio}:1 / SOI ${result.inputs.Hydrogen_Injection_Timing_deg_BTDC} deg BTDC / ${result.inputs.Engine_Load_pct}% fuel setting. Zero crank angle is firing TDC.`;
}
document.querySelectorAll('[data-chart]').forEach(button=>button.onclick=()=>{chartType=button.dataset.chart;document.querySelectorAll('[data-chart]').forEach(b=>b.classList.toggle('selected',b===button));draw();});new ResizeObserver(draw).observe(chart);
function download(text,type,name){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
document.getElementById('csv').onclick=()=>{if(!result)return;const keys=Object.keys(result.trace[0]);const meta=[`# ${result.model}`,`# inputs: ${JSON.stringify(result.inputs)}`,`# summary: ${JSON.stringify(result.summary)}`,...result.assumptions.map(a=>`# ${a}`)];download([...meta,keys.join(','),...result.trace.map(p=>keys.map(k=>p[k]).join(','))].join('\n'),'text/csv','cantera-hydrogen-cycle.csv');};
document.getElementById('json').onclick=()=>{if(result)download(JSON.stringify(result,null,2),'application/json','cantera-hydrogen-run.json');};
draw();
