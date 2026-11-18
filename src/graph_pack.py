"""The 13-graph set produced for every engine setup (same definitions as the web study page)."""
from pathlib import Path

# (title, x key, [(y key, label)], x label, y label)
GRAPHS = [
    ('Cylinder pressure','theta',[('pressure_bar','Firing'),('motored_pressure_bar','Motored')],'Crank angle (deg ATDC)','Pressure (bar)'),
    ('Log P-log V diagram','volume_cm3',[('pressure_bar','Cycle')],'Volume (cm3, log)','Pressure (bar, log)'),
    ('Mean gas temperature','theta',[('temperature_k','Mean gas')],'Crank angle (deg ATDC)','Temperature (K)'),
    ('Zone temperatures','theta',[('burned_core_temperature_k','First-burned core'),('unburned_temperature_k','Unburned end gas')],'Crank angle (deg ATDC)','Temperature (K)'),
    ('Heat release rate','theta',[('heat_release_j_per_deg','Chemical')],'Crank angle (deg ATDC)','Heat release (J/deg)'),
    ('Mass fraction burned','theta',[('mass_fraction_burned','Wiebe')],'Crank angle (deg ATDC)','Fraction burned (-)'),
    ('Hydrogen injection rate','theta',[('injection_rate_g_s','Injector')],'Crank angle (deg ATDC)','H2 mass flow (g/s)'),
    ('Mixture homogeneity','mixedness_theta',[('mixedness','Injected fuel')],'Crank angle (deg ATDC)','Mixedness (0-1)'),
    ('Wall heat transfer','theta',[('wall_heat_kw','Woschni')],'Crank angle (deg ATDC)','Heat loss rate (kW)'),
    ('Cumulative indicated work','theta',[('cumulative_work_j','Work')],'Crank angle (deg ATDC)','Work (J/cylinder)'),
    ('Thermal NO formation','theta',[('nox_ppm','Zeldovich NO')],'Crank angle (deg ATDC)','NO (ppm)'),
    ('Knock integral (Livengood-Wu)','theta',[('knock_integral','End gas')],'Crank angle (deg ATDC)','Integral (1 = autoignition)'),
    ('Energy balance','bar',[],'','Share of fuel energy (%)'),
]

def figure(result,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    tr = result['trace'];c = result['inputs'];s = result['summary']
    fig,axes = plt.subplots(4,4,figsize=(18,15),layout='constrained')
    colors = ['#2a78d6','#eb6834']
    for ax,(title,xk,series,xl,yl) in zip(axes.flat,GRAPHS):
        if xk=='bar':
            eb = result['energy_balance_j'];fuel = eb['Fuel energy']
            parts = {k:v/fuel*100 for k,v in eb.items() if k!='Fuel energy'}
            ax.barh(list(parts),list(parts.values()),color='#2a78d6');ax.invert_yaxis()
            for y,v in enumerate(parts.values()):ax.text(v,y,f' {v:.1f}',va='center',fontsize=8)
        else:
            for (key,label),color in zip(series,colors):
                pts = [(x,y) for x,y in zip(tr[xk],tr[key]) if y is not None]
                ax.plot([p[0] for p in pts],[p[1] for p in pts],color=color,lw=1.6,label=label)
            if len(series)>1:ax.legend(fontsize=8,frameon=False)
            if title.startswith('Log'):ax.set_xscale('log');ax.set_yscale('log')
        ax.set_title(title,fontsize=10,loc='left');ax.set_xlabel(xl,fontsize=8);ax.set_ylabel(yl,fontsize=8)
        ax.grid(alpha=.25);ax.tick_params(labelsize=7)
    table = axes.flat[13];table.axis('off')
    lines = [f'{k.replace("_"," ")}: {s[k]:.4g}' for k in ('Brake_Power_kW','Torque_Nm','Brake_Thermal_Efficiency_pct',
             'Fuel_Consumption_kg_per_hr','Peak_Cylinder_Pressure_bar','Peak_Cylinder_Temperature_K',
             'Combustion_Duration_deg_CA','Knock_PreIgnition_Tendency','NOx_Emissions_ppm',
             'spark_timing_deg_atdc','ca50_deg_atdc','mixture_homogeneity')]
    table.text(0,1,'\n'.join(lines),va='top',fontsize=9,family='monospace')
    for ax in list(axes.flat)[14:]:ax.axis('off')
    fig.suptitle(f"CR {c['Compression_Ratio']:g} · SOI {c['Hydrogen_Injection_Timing_deg_BTDC']:g} deg BTDC · "
                 f"load {c['Engine_Load_pct']:g} % (phi {s['equivalence_ratio']:.2f}) · {c['Engine_Speed_rpm']:g} rpm",fontsize=13)
    fig.savefig(path,dpi=110);plt.close(fig)

def export(rows,folder,progress=lambda p,m:None):
    from si_engine import simulate,INPUTS
    folder = Path(folder);folder.mkdir(parents=True,exist_ok=True)
    for i,row in enumerate(rows):
        result = simulate({k:row[k] for k in INPUTS},traces=True)
        rank = f'rank{i+1:04d}_' if 'desirability' in row else ''
        figure(result,folder/f"{rank}case{int(row['S.NO']):04d}.png")
        progress(round((i+1)/len(rows)*100),f'{i+1}/{len(rows)} graph sheets')
    return folder.resolve()
