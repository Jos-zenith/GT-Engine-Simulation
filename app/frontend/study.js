'use strict';
const $=id=>document.getElementById(id);
const FEATURES=['Compression_Ratio','Hydrogen_Injection_Timing_deg_BTDC','Engine_Load_pct','Engine_Speed_rpm'];
const IN={Compression_Ratio:['Compression ratio',':1'],Hydrogen_Injection_Timing_deg_BTDC:['Injection timing (SOI)','° BTDC'],Engine_Load_pct:['Load','%'],Engine_Speed_rpm:['Engine speed','rpm']};
const OUT={Brake_Power_kW:['Brake power','kW',1],Torque_Nm:['Torque','N·m',1],Brake_Thermal_Efficiency_pct:['Brake thermal efficiency','%',2],
 Fuel_Consumption_kg_per_hr:['Fuel consumption','kg/h',2],Peak_Cylinder_Pressure_bar:['Peak cylinder pressure','bar',1],
 Peak_Cylinder_Temperature_K:['Peak mean gas temperature','K',0],Combustion_Duration_deg_CA:['Combustion duration 10–90 %','°CA',1],
 Knock_PreIgnition_Tendency:['Knock / pre-ignition tendency','0–5',2],NOx_Emissions_ppm:['NOx','ppm',1]};
const WEIGHT_LABELS={Brake_Thermal_Efficiency_pct:'Weight · efficiency ↑',Brake_Power_kW:'Weight · power ↑',NOx_Emissions_ppm:'Weight · low NOx',Knock_PreIgnition_Tendency:'Weight · low knock'};
const C={s1:'#2a78d6',s2:'#eb6834',s3:'#1baf7a',ink:'#172b43',muted:'#52647a',grid:'#e4eaf1',faint:'#c3cfdc'};
const GRAPHS=[
 ['Cylinder pressure','theta',[['pressure_bar','Firing'],['motored_pressure_bar','Motored']],'Crank angle (° ATDC)','Pressure (bar)'],
 ['Log P – log V diagram','volume_cm3',[['pressure_bar','Cycle']],'Volume (cm³, log)','Pressure (bar, log)',true],
 ['Mean gas temperature','theta',[['temperature_k','Mean gas']],'Crank angle (° ATDC)','Temperature (K)'],
 ['Zone temperatures','theta',[['burned_core_temperature_k','First-burned core'],['unburned_temperature_k','Unburned end gas']],'Crank angle (° ATDC)','Temperature (K)'],
 ['Heat release rate','theta',[['heat_release_j_per_deg','Chemical']],'Crank angle (° ATDC)','J/°'],
 ['Mass fraction burned','theta',[['mass_fraction_burned','Wiebe']],'Crank angle (° ATDC)','Fraction (–)'],
 ['Hydrogen injection rate','theta',[['injection_rate_g_s','Injector']],'Crank angle (° ATDC)','g/s'],
 ['Mixture homogeneity','mixedness_theta',[['mixedness','Injected fuel']],'Crank angle (° ATDC)','Mixedness (0–1)'],
 ['Wall heat transfer','theta',[['wall_heat_kw','Woschni']],'Crank angle (° ATDC)','kW'],
 ['Cumulative indicated work','theta',[['cumulative_work_j','Work']],'Crank angle (° ATDC)','J / cylinder'],
 ['Thermal NO formation','theta',[['nox_ppm','Zeldovich NO']],'Crank angle (° ATDC)','ppm'],
 ['Knock integral (Livengood–Wu)','theta',[['knock_integral','End gas']],'Crank angle (° ATDC)','1 = autoignition'],
 ['Energy balance','bar',[],'','% of fuel energy']];
let rows=[],objectives={},ranges={},weights={},knockLimit=2.5,mlReport=null,mlFull=null,selectedId=null,lastStatus=null;

/* ---------- formatting & tooltip ---------- */
function fmt(v,d){if(v==null||!Number.isFinite(v))return '—';if(d!=null)return v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});const a=Math.abs(v);return a>=1000?Math.round(v).toLocaleString():a>=100?v.toFixed(1):a>=1?v.toFixed(2):a===0?'0':v.toPrecision(3);}
const tip=$('tip');
function showTip(html,x,y){tip.innerHTML=html;tip.hidden=false;const r=tip.getBoundingClientRect();tip.style.left=Math.min(x+14,innerWidth-r.width-8)+'px';tip.style.top=Math.max(8,y-r.height-10)+'px';}
function hideTip(){tip.hidden=true;}
function el(tag,attrs={},text){const n=document.createElement(tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text!=null)n.textContent=text;return n;}
function tile(label,value,unit){return `<div class="tile"><small>${label}</small><strong>${value}</strong>${unit?`<em>${unit}</em>`:''}</div>`;}

/* ---------- minimal canvas charts (line, scatter, horizontal bars) with hover ---------- */
function niceTicks(lo,hi,n=5){if(!(hi>lo)){lo-=1;hi+=1;}const raw=(hi-lo)/n,mag=10**Math.floor(Math.log10(raw)),e=raw/mag,step=(e>=7.5?10:e>=3.5?5:e>=1.5?2:1)*mag;const out=[];for(let v=Math.ceil(lo/step)*step;v<=hi+step*1e-9;v+=step)out.push(+v.toPrecision(12));return out;}
function logTicks(lo,hi){const span=hi-lo,mult=span<1.2?[1,1.5,2,3,4,5,6,8]:span<2.5?[1,2,5]:[1],t=[];for(let k=Math.floor(lo);k<=Math.ceil(hi);k++)for(const m of mult){const v=k+Math.log10(m);if(v>=lo-1e-9&&v<=hi+1e-9)t.push(v);}return t;}
function tickLabel(v,ticks,log){if(log){const x=10**v;return x>=1000?`${+(x/1000).toPrecision(3)}k`:`${+x.toPrecision(3)}`;}const step=ticks.length>1?Math.abs(ticks[1]-ticks[0]):1,d=Math.min(3,Math.max(0,Math.ceil(-Math.log10(step)-1e-9)));return v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});}
function canvasCtx(cv){const b=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=Math.max(1,Math.round(b.width*d));cv.height=Math.max(1,Math.round(b.height*d));const ctx=cv.getContext('2d');ctx.setTransform(d,0,0,d,0,0);return [ctx,b.width,b.height];}
function plot(cv,spec){cv._spec=spec;cv._hover=null;if(!cv._bound){cv._bound=true;
 cv.addEventListener('mousemove',e=>hover(cv,e));cv.addEventListener('mouseleave',()=>{cv._hover=null;render(cv);hideTip();});
 cv.addEventListener('click',()=>{const s=cv._spec;if(s.kind==='scatter'&&s.onClick&&cv._hover!=null)s.onClick(s.points[cv._hover]);});
 new ResizeObserver(()=>render(cv)).observe(cv);}render(cv);}
function frame(cv){const s=cv._spec,[ctx,w,h]=canvasCtx(cv),pad={l:54,r:24,t:22,b:38};
 const pts=s.kind==='scatter'?s.points:s.series.flatMap(q=>q.x.map((x,i)=>({x,y:q.y[i]})));
 const ok=pts.filter(p=>p.y!=null&&Number.isFinite(p.y)&&(!s.logx||p.x>0)&&(!s.logy||p.y>0));
 const fx=v=>s.logx?Math.log10(v):v,fy=v=>s.logy?Math.log10(v):v;
 let [x0,x1]=s.xdomain?s.xdomain.map(fx):[Math.min(...ok.map(p=>fx(p.x))),Math.max(...ok.map(p=>fx(p.x)))];
 let [y0,y1]=s.ydomain?s.ydomain.map(fy):[Math.min(...ok.map(p=>fy(p.y))),Math.max(...ok.map(p=>fy(p.y)))];
 if(!(x1>x0)){x0-=1;x1+=1;}if(!(y1>y0)){y0-=Math.abs(y0)*.1||1;y1+=Math.abs(y1)*.1||1;}
 let yt;if(s.logy){if(s.kind==='scatter'){y0=Math.floor(y0);y1=Math.ceil(y1);}yt=logTicks(y0,y1);}else{yt=niceTicks(y0,y1);y0=Math.min(y0,yt[0]);y1=Math.max(y1,yt[yt.length-1]);}
 let xt;if(s.logx){if(s.kind==='scatter'){x0=Math.floor(x0);x1=Math.ceil(x1);}xt=logTicks(x0,x1);}else xt=niceTicks(x0,x1,s.xticks||5).filter(t=>t>=x0-1e-9&&t<=x1+1e-9);
 const X=v=>pad.l+(fx(v)-x0)/(x1-x0)*(w-pad.l-pad.r),Y=v=>pad.t+(1-(fy(v)-y0)/(y1-y0))*(h-pad.t-pad.b);
 return {ctx,w,h,pad,X,Y,xt,yt,ok,fx,fy,x0,x1};}
function render(cv){const s=cv._spec;if(!s)return;if(s.kind==='barh')return renderBars(cv);
 const f=frame(cv),{ctx,w,h,pad,X,Y}=f;ctx.clearRect(0,0,w,h);
 if(!f.ok.length){ctx.fillStyle=C.muted;ctx.font='12px system-ui';ctx.fillText('No data',pad.l,h/2);return;}
 ctx.font='10px system-ui';ctx.fillStyle=C.muted;ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.textAlign='right';
 for(const t of f.yt){const v=s.logy?10**t:t,y=Y(v);ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();ctx.fillText(tickLabel(t,f.yt,s.logy),pad.l-6,y+3);}
 ctx.textAlign='center';for(const t of f.xt){const v=s.logx?10**t:t,x=X(v);if(x<pad.l-1||x>w-pad.r+1)continue;ctx.fillText(tickLabel(t,f.xt,s.logx),x,h-pad.b+14);}
 ctx.fillText(s.xlabel||'',pad.l+(w-pad.l-pad.r)/2,h-6);ctx.textAlign='left';ctx.fillText(s.ylabel||'',pad.l-46,12);
 ctx.strokeStyle='#9fb1c4';ctx.beginPath();ctx.moveTo(pad.l,pad.t);ctx.lineTo(pad.l,h-pad.b);ctx.lineTo(w-pad.r,h-pad.b);ctx.stroke();
 ctx.save();ctx.beginPath();ctx.rect(pad.l,pad.t-4,w-pad.l-pad.r+4,h-pad.t-pad.b+8);ctx.clip();
 if(s.diagonal){ctx.strokeStyle=C.faint;ctx.setLineDash([4,4]);ctx.beginPath();const lo=s.diagonal[0],hi=s.diagonal[1];ctx.moveTo(X(lo),Y(lo));ctx.lineTo(X(hi),Y(hi));ctx.stroke();ctx.setLineDash([]);}
 if(s.kind==='scatter'){for(const layer of [0,1])for(const p of s.points){if((p.layer||0)!==layer||!(p.y>0||!s.logy))continue;ctx.fillStyle=p.color;ctx.globalAlpha=p.alpha??1;ctx.beginPath();ctx.arc(X(p.x),Y(p.y),p.r||3,0,7);ctx.fill();if(p.ring){ctx.globalAlpha=1;ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();}}ctx.globalAlpha=1;
  if(cv._hover!=null){const p=s.points[cv._hover];ctx.strokeStyle=C.ink;ctx.lineWidth=2;ctx.beginPath();ctx.arc(X(p.x),Y(p.y),(p.r||3)+3,0,7);ctx.stroke();}}
 else for(const q of s.series){ctx.strokeStyle=q.color;ctx.lineWidth=2;ctx.lineJoin='round';ctx.beginPath();let pen=false;
  q.x.forEach((x,i)=>{const y=q.y[i];if(y==null||!Number.isFinite(y)||(s.logx&&x<=0)||(s.logy&&y<=0)){pen=false;return;}pen?ctx.lineTo(X(x),Y(y)):ctx.moveTo(X(x),Y(y));pen=true;});ctx.stroke();
  if(q.dots)q.x.forEach((x,i)=>{ctx.fillStyle=q.color;ctx.beginPath();ctx.arc(X(x),Y(q.y[i]),4,0,7);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();});}
 if(s.kind!=='scatter'&&cv._hover!=null){const hx=cv._hover;ctx.strokeStyle='#8193a8';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(hx,pad.t);ctx.lineTo(hx,h-pad.b);ctx.stroke();
  for(const q of s.series){const i=nearest(q.x,X,hx);if(i<0||q.y[i]==null)continue;ctx.fillStyle=q.color;ctx.beginPath();ctx.arc(X(q.x[i]),Y(q.y[i]),4,0,7);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();}}
 ctx.restore();cv._frame=f;}
function nearest(xs,X,px){let b=-1,d=Infinity;xs.forEach((x,i)=>{const e=Math.abs(X(x)-px);if(e<d){d=e;b=i;}});return b;}
function hover(cv,e){const s=cv._spec,f=cv._frame;if(!s||!f)return;const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
 if(s.kind==='barh'){const i=Math.floor((my-f.top)/f.band);if(i>=0&&i<s.labels.length){showTip(`<b>${s.labels[i]}</b><br>${fmt(s.values[i],1)} ${s.unit}`,e.clientX,e.clientY);}else hideTip();return;}
 if(s.kind==='scatter'){let b=null,d=196;s.points.forEach((p,i)=>{if(s.logy&&!(p.y>0))return;const dx=f.X(p.x)-mx,dy=f.Y(p.y)-my,q=dx*dx+dy*dy;if(q<d){d=q;b=i;}});
  cv.style.cursor=b!=null&&s.onClick?'pointer':'default';if(b!==cv._hover){cv._hover=b;render(cv);}if(b!=null)showTip(s.tooltip(s.points[b]),e.clientX,e.clientY);else hideTip();return;}
 if(mx<f.pad.l||mx>f.w-f.pad.r){cv._hover=null;render(cv);hideTip();return;}cv._hover=mx;render(cv);
 const lines=s.series.map(q=>{const i=nearest(q.x,f.X,mx);return i<0||q.y[i]==null?'':`<span style="color:${q.color}">●</span> ${q.label}: <b>${fmt(q.y[i])}</b>`;}).filter(Boolean);
 const i0=nearest(s.series[0].x,f.X,mx);showTip(`${s.xname||s.xlabel}: <b>${fmt(s.series[0].x[i0])}</b><br>${lines.join('<br>')}`,e.clientX,e.clientY);}
function renderBars(cv){const s=cv._spec,[ctx,w,h]=canvasCtx(cv);ctx.clearRect(0,0,w,h);const left=118,top=6,band=(h-top-8)/s.labels.length,max=Math.max(...s.values.map(Math.abs),1e-9);
 ctx.font='11px system-ui';s.labels.forEach((l,i)=>{const y=top+i*band,bw=Math.max(0,s.values[i])/max*(w-left-60);ctx.fillStyle=C.muted;ctx.textAlign='right';ctx.fillText(l,left-8,y+band/2+4);
  ctx.fillStyle=C.s1;ctx.beginPath();ctx.roundRect(left,y+band*.2,Math.max(bw,1),band*.6,[0,4,4,0]);ctx.fill();ctx.fillStyle=C.ink;ctx.textAlign='left';ctx.fillText(fmt(s.values[i],1),left+bw+6,y+band/2+4);});
 cv._frame={top,band};}

/* ---------- desirability, ranking ---------- */
function computeRanges(){for(const [k,kind] of Object.entries(objectives)){const v=rows.map(r=>kind==='min_log'?Math.log1p(Math.max(r[k],0)):r[k]);ranges[k]=[Math.min(...v),Math.max(...v)];}}
function desirability(r){if(r.Knock_PreIgnition_Tendency>knockLimit||r.Brake_Power_kW<=0)return 0;let acc=0,tot=0;
 for(const [k,kind] of Object.entries(objectives)){const w=weights[k]||0;if(w<=0)continue;const [lo,hi]=ranges[k],v=kind==='min_log'?Math.log1p(Math.max(r[k],0)):r[k];let d=hi>lo?(v-lo)/(hi-lo):1;if(kind!=='max')d=1-d;acc+=w*Math.log(Math.min(1,Math.max(d,1e-6)));tot+=w;}
 return tot?Math.exp(acc/tot):0;}
function filterValue(id){const v=$(id).value;return v==='all'?null:Number(v);}
function filtered(){const cr=filterValue('f-cr'),n=filterValue('f-rpm'),l=filterValue('f-load');return rows.filter(r=>(cr==null||r.Compression_Ratio===cr)&&(n==null||r.Engine_Speed_rpm===n)&&(l==null||r.Engine_Load_pct===l));}
function setupLabel(r){return `CR ${r.Compression_Ratio} · SOI ${r.Hydrogen_Injection_Timing_deg_BTDC}° · load ${fmt(r.Engine_Load_pct,0)} % · ${fmt(r.Engine_Speed_rpm,0)} rpm`;}
function rerank(){for(const r of rows)r.D=desirability(r);renderRanking();renderPareto();renderSoiMap();}
function renderRanking(){const set=filtered().slice().sort((a,b)=>b.D-a.D),feasible=set.filter(r=>r.D>0),body=$('rank-body');body.replaceChildren();
 $('rank-caption').textContent=`Top 15 of ${set.length.toLocaleString()} setups in view · ${feasible.length.toLocaleString()} feasible (knock ≤ ${knockLimit}, positive brake power)`;
 set.slice(0,15).forEach((r,i)=>{const tr=el('tr');if(r['S.NO']===selectedId)tr.className='sel';
  for(const v of [i+1,r['S.NO'],fmt(r.Compression_Ratio,1),fmt(r.Hydrogen_Injection_Timing_deg_BTDC,0),`${fmt(r.Engine_Load_pct,0)} (${fmt(r.equivalence_ratio,2)})`,fmt(r.Engine_Speed_rpm,0),fmt(r.Brake_Power_kW,1),fmt(r.Torque_Nm,1),fmt(r.Brake_Thermal_Efficiency_pct,2),fmt(r.NOx_Emissions_ppm,1),fmt(r.Knock_PreIgnition_Tendency,2),fmt(r.D,3)])tr.append(el('td',{},String(v)));
  tr.tabIndex=0;tr.onclick=tr.onkeydown=e=>{if(e.type==='keydown'&&e.key!=='Enter')return;selectRow(r);};body.append(tr);});
 const b=set[0];$('best-tiles').innerHTML=b&&b.D>0?tile('Best setup',`#${b['S.NO']}`,setupLabel(b))+tile('Desirability D',fmt(b.D,3))+tile('Brake thermal efficiency',fmt(b.Brake_Thermal_Efficiency_pct,2),'%')+tile('Brake power',fmt(b.Brake_Power_kW,1),'kW')+tile('NOx',fmt(b.NOx_Emissions_ppm,1),'ppm')+tile('Knock tendency',fmt(b.Knock_PreIgnition_Tendency,2),'of 5'):tile('Best setup','—','no feasible setup in this view');}
function selectRow(r){selectedId=r['S.NO'];history.replaceState(null,'',`#case=${r['S.NO']}`);renderRanking();renderPareto();loadCase(Object.fromEntries(FEATURES.map(f=>[f,r[f]])),`Setup #${r['S.NO']}`);}

/* ---------- 13-graph pack ---------- */
async function loadCase(inputs,label){$('pack-status').textContent=`Simulating ${label}…`;$('pack-tag').textContent=label.toUpperCase();
 try{const res=await fetch('/api/study/case',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(inputs)});const data=await res.json();if(!res.ok)throw Error(data.error||'Simulation failed');
  const s=data.summary;$('pack-status').textContent=`${label}: CR ${fmt(inputs.Compression_Ratio,2)} · SOI ${fmt(inputs.Hydrogen_Injection_Timing_deg_BTDC,1)}° BTDC · load ${fmt(inputs.Engine_Load_pct,1)} % (φ ${fmt(s.equivalence_ratio,2)}) · ${fmt(inputs.Engine_Speed_rpm,0)} rpm · spark ${fmt(s.spark_timing_deg_atdc,1)}° ATDC · CA50 ${fmt(s.ca50_deg_atdc,1)}°`;
  $('pack-tiles').innerHTML=Object.entries(OUT).map(([k,[l,u,d]])=>tile(l,fmt(s[k],d),u)).join('');
  const grid=$('pack-grid');grid.replaceChildren();const tr=data.trace;
  GRAPHS.forEach(([title,xk,series,xl,yl,log],i)=>{const fig=el('figure'),cap=el('figcaption',{},`${i+1}. ${title}`),cv=el('canvas',{class:'small',role:'img','aria-label':title});fig.append(cap);
   if(series.length>1){const lg=el('div',{class:'legend'});series.forEach(([,name],j)=>{lg.insertAdjacentHTML('beforeend',`<span><i style="background:${[C.s1,C.s2][j]}"></i>${name}</span>`);});fig.append(lg);}
   fig.append(cv);grid.append(fig);
   if(xk==='bar'){const eb=data.energy_balance_j,fuel=eb['Fuel energy'],labels=Object.keys(eb).filter(k=>k!=='Fuel energy');plot(cv,{kind:'barh',labels,values:labels.map(k=>eb[k]/fuel*100),unit:'% of fuel energy'});}
   else plot(cv,{series:series.map(([k,name],j)=>({x:tr[xk],y:tr[k],label:name,color:[C.s1,C.s2][j]})),xlabel:xl,ylabel:yl,logx:!!log,logy:!!log,xname:xk==='volume_cm3'?'Volume (cm³)':'Crank angle (°)'});});
  $('pack-panel').scrollIntoView({behavior:'smooth',block:'start'});
 }catch(error){$('pack-status').textContent=`Could not simulate: ${error.message}`;}}

/* ---------- trade-offs ---------- */
function pareto(set){const out=[];for(const a of set){let dominated=false;for(const b of set){if(b===a)continue;if(b.Brake_Thermal_Efficiency_pct>=a.Brake_Thermal_Efficiency_pct&&b.NOx_Emissions_ppm<=a.NOx_Emissions_ppm&&b.Brake_Power_kW>=a.Brake_Power_kW&&(b.Brake_Thermal_Efficiency_pct>a.Brake_Thermal_Efficiency_pct||b.NOx_Emissions_ppm<a.NOx_Emissions_ppm||b.Brake_Power_kW>a.Brake_Power_kW)){dominated=true;break;}}if(!dominated)out.push(a);}return out;}
function renderPareto(){const set=filtered().filter(r=>r.Knock_PreIgnition_Tendency<=knockLimit&&r.Brake_Power_kW>0),front=new Set(pareto(set));
 const points=set.map(r=>{const sel=r['S.NO']===selectedId,f=front.has(r);return {x:Math.max(r.NOx_Emissions_ppm,.01),y:r.Brake_Thermal_Efficiency_pct,r:sel?7:f?5:3,color:sel?C.s2:f?C.s1:C.faint,alpha:sel||f?1:.55,layer:sel||f?1:0,ring:sel||f,row:r,front:f};});
 plot($('pareto'),{kind:'scatter',points,logx:true,xlabel:'NOx (ppm, log scale)',ylabel:'Brake thermal efficiency (%)',onClick:p=>selectRow(p.row),
  tooltip:p=>`<b>Setup #${p.row['S.NO']}</b>${p.front?' · Pareto-optimal':''}<br>${setupLabel(p.row)}<br>BTE ${fmt(p.row.Brake_Thermal_Efficiency_pct,2)} % · power ${fmt(p.row.Brake_Power_kW,1)} kW<br>NOx ${fmt(p.row.NOx_Emissions_ppm,1)} ppm · knock ${fmt(p.row.Knock_PreIgnition_Tendency,2)} · D ${fmt(p.row.D,3)}`});
 $('pareto-note').innerHTML=`<span style="color:${C.s1}">●</span> ${front.size} Pareto-optimal setups (no other setup has higher efficiency, higher power and lower NOx at once) · <span style="color:${C.faint}">●</span> other feasible setups · <span style="color:${C.s2}">●</span> selected. NOx below 0.01 ppm is drawn at 0.01. Click a point to open its graphs.`;}
function levels(key){return [...new Set(rows.map(r=>r[key]))].sort((a,b)=>a-b);}
function renderSoiMap(){const cr=filterValue('f-cr'),pool=rows.filter(r=>cr==null||r.Compression_Ratio===cr),loads=levels('Engine_Load_pct'),speeds=levels('Engine_Speed_rpm'),t=$('soi-map');t.replaceChildren();
 const head=el('tr');head.append(el('th',{},'rpm \\ load'));loads.forEach(l=>head.append(el('th',{},`${l} % (φ ${fmt(.2+.8*l/100,1)})`)));t.append(head);
 for(const n of speeds){const tr=el('tr');tr.append(el('th',{},String(n)));for(const l of loads){const cell=pool.filter(r=>r.Engine_Speed_rpm===n&&r.Engine_Load_pct===l).sort((a,b)=>b.D-a.D)[0],td=el('td');
  if(cell&&cell.D>0){td.textContent=`${cell.Hydrogen_Injection_Timing_deg_BTDC}° · CR ${cell.Compression_Ratio}`;td.style.background=`rgba(42,120,214,${(.06+cell.D*.5).toFixed(2)})`;td.title=`D ${fmt(cell.D,3)} · BTE ${fmt(cell.Brake_Thermal_Efficiency_pct,2)} % · NOx ${fmt(cell.NOx_Emissions_ppm,1)} ppm`;td.style.cursor='pointer';td.onclick=()=>selectRow(cell);}
  else{td.textContent='knock-limited';td.style.color=C.muted;}tr.append(td);}t.append(tr);}}
function renderEffects(){const key=$('effect-output').value,wrap=$('effects');wrap.replaceChildren();
 for(const f of FEATURES){const lv=levels(f),means=lv.map(v=>{const g=rows.filter(r=>r[f]===v);return g.reduce((s,r)=>s+r[key],0)/g.length;});
  const fig=el('figure'),cv=el('canvas',{class:'small',role:'img','aria-label':`${OUT[key][0]} versus ${IN[f][0]}`});fig.append(el('figcaption',{},IN[f][0]),cv);wrap.append(fig);
  plot(cv,{series:[{x:lv,y:means,label:`Mean ${OUT[key][0]}`,color:C.s1,dots:lv.length<=6}],xlabel:`${IN[f][0]} (${IN[f][1]})`,ylabel:`${OUT[key][1]}`});}}

/* ---------- ML ---------- */
async function renderML(report){mlReport=report;const names=Object.keys(report.cv_metrics),body=$('ml-body');body.replaceChildren();
 $('ml-summary').textContent=`${report.training_data.workbook_grid.toLocaleString()} workbook setups + ${report.training_data.off_grid_infill.toLocaleString()} off-grid infill simulations · ${report.split.development.toLocaleString()} development / ${report.split.test.toLocaleString()} held-out test · ${report.selection_rule} NOx is learned as log(1 + ppm).`;
 for(const o of report.outputs){const tr=el('tr');tr.append(el('td',{},`${OUT[o][0]} (${OUT[o][1]})`));for(const n of names)tr.append(el('td',{class:n===report.choice[o]?'good':''},fmt(report.cv_metrics[n][o].r2,4)));
  tr.append(el('td',{},report.choice[o].replace(' (MLP 64-64-64)','')));tr.append(el('td',{},fmt(report.ensemble_test_by_source.workbook_grid[o].r2,4)));tr.append(el('td',{},fmt(report.ensemble_test_by_source.off_grid_infill[o].r2,4)));tr.append(el('td',{},`${fmt(report.ensemble_test_metrics[o].rmse)} ${OUT[o][1]}`));body.append(tr);}
 const curve=report.learning_curve;
 plot($('curve'),{series:names.map((n,j)=>({x:curve.map(p=>p.training_cases),y:curve.map(p=>p[n].mean_r2),label:n,color:[C.s1,C.s2,C.s3][j],dots:true})),logx:true,xlabel:'Training simulations (log scale)',ylabel:'Mean test R² (9 outputs)',xname:'Training simulations'});
 $('curve-legend')?.remove();const lg=el('div',{class:'legend',id:'curve-legend'});names.forEach((n,j)=>lg.insertAdjacentHTML('beforeend',`<span><i style="background:${[C.s1,C.s2,C.s3][j]}"></i>${n}</span>`));$('curve').before(lg);
 const imp=$('importance');imp.replaceChildren();const head=el('tr');head.append(el('th',{},'Output'));FEATURES.forEach(f=>head.append(el('th',{},IN[f][0])));imp.append(head);
 for(const o of report.outputs){const tr=el('tr'),vals=FEATURES.map(f=>report.permutation_importance[o][f]),max=Math.max(...vals,1e-9);tr.append(el('th',{},OUT[o][0]));
  vals.forEach(v=>{const td=el('td',{},fmt(Math.max(v,0),3));td.style.background=`rgba(42,120,214,${(Math.max(v,0)/max*.5).toFixed(2)})`;tr.append(td);});imp.append(tr);}
 try{mlFull=await (await fetch('/api/study/ml_report.json',{cache:'no-store'})).json();renderParity();}catch(e){}}
function renderParity(){if(!mlFull)return;const o=$('parity-output').value,j=mlFull.outputs.indexOf(o),a=mlFull.parity_test.actual.map(r=>r[j]),p=mlFull.parity_test.predicted.map(r=>r[j]);
 const lo=Math.min(...a,...p),hi=Math.max(...a,...p);
 plot($('parity'),{kind:'scatter',points:a.map((v,i)=>({x:v,y:p[i],color:C.s1,r:3,alpha:.7})),diagonal:[lo,hi],xdomain:[lo,hi],ydomain:[lo,hi],xlabel:`Simulated ${OUT[o][0]} (${OUT[o][1]})`,ylabel:`ML predicted (${OUT[o][1]})`,
  tooltip:q=>`Simulated <b>${fmt(q.x)}</b><br>Predicted <b>${fmt(q.y)}</b><br>Error ${fmt(q.y-q.x)} ${OUT[o][1]}`});}

/* ---------- optimiser ---------- */
function buildOptimiser(){const wrap=$('opt-vars');wrap.replaceChildren();const lim={Compression_Ratio:[10.5,12.5,.1,12],Hydrogen_Injection_Timing_deg_BTDC:[60,160,1,120],Engine_Load_pct:[0,100,1,50],Engine_Speed_rpm:[2500,4500,50,3000]};
 for(const f of FEATURES){const [lo,hi,st,def]=lim[f],label=el('label');label.innerHTML=`<span><input type="checkbox" id="free-${f}" checked> Optimise: ${IN[f][0]}</span>`;
  const input=el('input',{type:'number',id:`fix-${f}`,min:lo,max:hi,step:st,value:def,disabled:'','aria-label':`Fixed ${IN[f][0]}`});label.append(input);wrap.append(label);
  label.querySelector('input[type=checkbox]').onchange=e=>{input.disabled=e.target.checked;};}}
async function runOptimiser(){const free=FEATURES.filter(f=>$(`free-${f}`).checked),fixed=Object.fromEntries(FEATURES.filter(f=>!free.includes(f)).map(f=>[f,Number($(`fix-${f}`).value)]));
 if(!free.length){$('opt-status').textContent='Tick at least one input to optimise.';return;}
 $('opt-run').disabled=true;$('opt-status').textContent='Running differential evolution on the surrogate, then re-simulating the optimum…';$('opt-result').replaceChildren();
 try{const res=await fetch('/api/study/optimize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({weights,knock_limit:knockLimit,free,fixed})});const d=await res.json();if(!res.ok)throw Error(d.error||'Optimisation failed');
  $('opt-status').textContent=`${d.generations} generations · ${d.evaluations.toLocaleString()} surrogate evaluations · ${fmt(d.seconds,1)} s.`;
  const gap=Math.abs(d.desirability_predicted-d.desirability_simulated),g=d.best_grid_setup;
  let html=`<div class="tiles">${tile('Optimum inputs',FEATURES.map(f=>`${fmt(d.inputs[f],f==='Compression_Ratio'?2:0)}`).join(' · '),'CR · SOI · load · rpm')}${tile('D predicted by ML',fmt(d.desirability_predicted,3))}${tile('D re-simulated',fmt(d.desirability_simulated,3),gap<.03?'<span class="good">confirmed</span>':'<span class="warn">surrogate gap</span>')}${g?tile('Best grid setup D',fmt(g.desirability,3),`#${g['S.NO']}`):''}</div>`;
  html+='<div class="table-scroll"><table><thead><tr><th>Output</th><th>ML prediction</th><th>Physics re-simulation</th><th>Error</th>'+(g?'<th>Best grid setup</th>':'')+'</tr></thead><tbody>';
  for(const [k,[l,u,dp]] of Object.entries(OUT)){const p=d.predicted[k],s=d.simulated[k],err=Math.abs(s)>1e-6?(p-s)/Math.abs(s)*100:null;html+=`<tr><td>${l} (${u})</td><td>${fmt(p,dp)}</td><td>${fmt(s,dp)}</td><td>${err==null?'—':fmt(err,1)+' %'}</td>${g?`<td>${fmt(g[k],dp)}</td>`:''}</tr>`;}
  html+='</tbody></table></div><button type="button" id="opt-pack">Open 13 graphs for the optimum ↗</button>';$('opt-result').innerHTML=html;
  $('opt-pack').onclick=()=>loadCase(d.inputs,'GA optimum');
 }catch(error){$('opt-status').textContent=`Could not optimise: ${error.message}`;}finally{$('opt-run').disabled=false;}}

/* ---------- study lifecycle ---------- */
function buildControls(defaults,limit){const wrap=$('weights');wrap.replaceChildren();weights={...defaults};knockLimit=limit;$('knock-limit').value=limit;$('knock-out').textContent=limit;
 for(const k of Object.keys(objectives)){const label=el('label',{},WEIGHT_LABELS[k]),input=el('input',{type:'range',min:0,max:3,step:.25,value:weights[k]}),out=el('output',{},String(weights[k]));
  input.oninput=()=>{weights[k]=Number(input.value);out.textContent=input.value;rerank();};label.append(input,out);wrap.append(label);}
 for(const [id,key,unit] of [['f-cr','Compression_Ratio',':1'],['f-rpm','Engine_Speed_rpm',' rpm'],['f-load','Engine_Load_pct',' %']]){const s=$(id);s.replaceChildren(el('option',{value:'all'},'All'));levels(key).forEach(v=>s.append(el('option',{value:v},`${v}${unit}`)));s.onchange=rerank;}
 $('effect-output').replaceChildren(...Object.entries(OUT).map(([k,[l]])=>el('option',{value:k},l)));$('effect-output').value='Brake_Thermal_Efficiency_pct';
 $('parity-output').replaceChildren(...Object.entries(OUT).map(([k,[l]])=>el('option',{value:k},l)));$('parity-output').value='NOx_Emissions_ppm';}
$('knock-limit').oninput=e=>{knockLimit=Number(e.target.value);$('knock-out').textContent=e.target.value;rerank();};
$('effect-output').onchange=renderEffects;$('parity-output').onchange=renderParity;$('opt-run').onclick=runOptimiser;
async function loadResults(){const res=await fetch('/api/study/results',{cache:'no-store'});const data=await res.json();if(!res.ok)throw Error(data.error);
 rows=data.rows.map(v=>Object.fromEntries(data.columns.map((c,i)=>[c,v[i]])));objectives=data.objectives;computeRanges();buildControls(data.default_weights,data.default_knock_limit);rerank();renderEffects();
 const m=location.hash.match(/case=(\d+)/),r=m&&rows.find(x=>x['S.NO']===Number(m[1]));if(r)selectRow(r);}
async function refresh(){try{const res=await fetch('/api/study/status',{cache:'no-store'}),s=await res.json();$('study-status').textContent=s.message;$('study-progress').value=s.progress;$('study-run').disabled=s.status==='running';
  const m=s.study_meta;if(m)$('study-tiles').innerHTML=tile('Workbook setups simulated',m.cases.toLocaleString())+tile('Off-grid infill points',(m.infill_cases||0).toLocaleString())+tile('Simulation wall time',fmt(m.simulation_seconds,0),'s')+tile('Worst energy residual',m.max_energy_inversion_residual.toExponential(1))+tile('Study created',new Date(m.created_utc).toLocaleString());
  for(const id of ['dl-csv','dl-xlsx','dl-ml'])$(id).hidden=!s.ml_report;
  if(s.status==='running'){setTimeout(refresh,1500);lastStatus='running';return;}
  if(s.ml_report&&(lastStatus!=='ready'||!rows.length)){await loadResults();await renderML(s.ml_report);}lastStatus=s.status;
 }catch(error){$('study-status').textContent=`Unable to load the study: ${error.message}`;}}
$('study-run').onclick=async()=>{$('study-run').disabled=true;try{const r=await fetch('/api/study/run',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();if(!r.ok)throw Error(d.error);lastStatus='running';refresh();}catch(e){$('study-status').textContent=e.message;$('study-run').disabled=false;}};
buildOptimiser();refresh();
