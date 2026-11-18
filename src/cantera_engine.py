"""Cantera hydrogen/air closed-cylinder autoignition baseline.

Standalone: .venv\Scripts\python cantera_engine.py --output results/cantera_baseline
This is a homogeneous compression/expansion calculation, not a spark/DI model.
"""
import argparse
import csv
import hashlib
import json
import math
import time
from pathlib import Path

DEFAULTS=dict(rpm=3000,compression_ratio=12,load=75,bore_mm=100,stroke_mm=100,
              conrod_mm=220,intake_temperature_k=450,intake_pressure_bar=1,
              heat_transfer_coefficient=150)
BOUNDS=dict(rpm=(500,6000),compression_ratio=(6,16),load=(0,100),bore_mm=(50,150),
            stroke_mm=(50,150),conrod_mm=(100,400),intake_temperature_k=(280,500),
            intake_pressure_bar=(.5,2),heat_transfer_coefficient=(0,500))
MECHANISM='h2o2.yaml'

def validate(inputs):
    if not isinstance(inputs,dict) or set(inputs)-set(DEFAULTS):raise ValueError('Unknown Cantera parameters or invalid input object.')
    c=DEFAULTS|inputs
    for key,(lo,hi) in BOUNDS.items():
        value=c[key]
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError(f'{key} must be between {lo} and {hi}.')
    if c['conrod_mm']<=c['stroke_mm']/2:raise ValueError('Connecting rod must exceed crank radius.')
    return c

def geometry(c,angle_deg):
    b,s,l=c['bore_mm']/1000,c['stroke_mm']/1000,c['conrod_mm']/1000
    r=s/2; area=math.pi*b*b/4; t=math.radians(angle_deg)
    root=math.sqrt(l*l-r*r*math.sin(t)**2)
    distance=r*(1-math.cos(t))+l-root
    volume=area*s/(c['compression_ratio']-1)+area*distance
    derivative=area*(r*math.sin(t)+r*r*math.sin(t)*math.cos(t)/root)
    return volume,derivative,2*area+math.pi*b*distance

def mechanism_hash():
    import cantera as ct
    for folder in ct.get_data_directories():
        path=Path(folder)/MECHANISM
        if path.is_file():return hashlib.sha256(path.read_bytes()).hexdigest()
    raise ValueError('Cannot locate the installed hydrogen reaction mechanism.')

def fingerprint():
    import cantera as ct
    return hashlib.sha256((Path(__file__).read_text(encoding='utf-8')+ct.__version__+mechanism_hash()).encode()).hexdigest()

def simulate(inputs,step_deg=.25):
    import cantera as ct
    import numpy as np
    c=validate(inputs)
    if step_deg not in (.125,.25,.5,1):raise ValueError('Unsupported output resolution.')
    started=time.perf_counter()
    gas=ct.Solution(MECHANISM)
    gas.TP=c['intake_temperature_k'],c['intake_pressure_bar']*1e5
    phi=.9*c['load']/100
    gas.set_equivalence_ratio(phi,'H2:1','O2:1,N2:3.76')
    class CylinderWithIntegrals(ct.ExtensibleIdealGasReactor):
        """Integrate work, wall heat and chemical heat on Cantera's adaptive steps."""
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.integrals=[0.,0.,0.]
        def after_initialize(self,t0):
            self.quadrature_start=self.n_vars
            self.n_vars+=3
        def after_get_state(self,y):
            for j,value in enumerate(self.integrals):y[self.quadrature_start+j]=value
        def after_update_state(self,y):
            self.integrals=[float(y[self.quadrature_start+j]) for j in range(3)]
        def after_eval(self,t,lhs,rhs):
            a=-180+6*c['rpm']*t
            volume,dv_drad,surface=geometry(c,a)
            rates=[self.phase.P*dv_drad*2*math.pi*c['rpm']/60,
                   c['heat_transfer_coefficient']*surface*(self.T-450),
                   self.phase.heat_release_rate*self.volume]
            for j,value in enumerate(rates):
                lhs[self.quadrature_start+j]=1.
                rhs[self.quadrature_start+j]=value
        def before_component_name(self,index):
            if self.quadrature_start<=index<self.n_vars:
                return ['work_j','wall_heat_j','chemical_heat_j'][index-self.quadrature_start]

    cyl=CylinderWithIntegrals(gas,clone=True,energy='on',name='hydrogen_cylinder')
    cyl.volume=geometry(c,-180)[0]
    bathgas=ct.Solution(MECHANISM)
    bathgas.TPX=450,ct.one_atm,'O2:1,N2:3.76'
    bath=ct.Reservoir(bathgas,clone=True,name='wall_450K')
    area=math.pi*(c['bore_mm']/1000)**2/4
    def angle(t):return -180+6*c['rpm']*t
    def speed(t):return geometry(c,angle(t))[1]*2*math.pi*c['rpm']/60/area
    piston=ct.Wall(cyl,bath,A=area,velocity=speed)
    def heat_rate(t):return c['heat_transfer_coefficient']*geometry(c,angle(t))[2]*(cyl.T-450)
    thermal=ct.Wall(cyl,bath,A=1,Q=heat_rate)
    net=ct.ReactorNet([cyl]);net.rtol=1e-9;net.atol=1e-15
    net.max_time_step=.1/(6*c['rpm'])
    net.max_steps=50000
    mass=cyl.mass;fuel=mass*float(cyl.phase['H2'].Y[0])
    energy0=mass*cyl.phase.int_energy_mass
    trace=[];times=[];heat_rates=[];mass_errors=[];volume_errors=[]
    try:
        for i in range(round(360/step_deg)+1):
            a=-180+i*step_deg;t=(a+180)/(6*c['rpm'])
            if i:net.advance(t)
            phase=cyl.phase
            consumed=1-cyl.mass*float(phase['H2'].Y[0])/fuel if fuel>0 else 0
            trace.append(dict(crank_angle_deg=a,time_s=t,volume_cm3=cyl.volume*1e6,
                pressure_bar=phase.P/1e5,temperature_k=cyl.T,
                mass_fraction_burned=max(0,min(1,consumed)),
                h2_mole_fraction=float(phase['H2'].X[0]),o2_mole_fraction=float(phase['O2'].X[0]),
                h2o_mole_fraction=float(phase['H2O'].X[0]),oh_mole_fraction=float(phase['OH'].X[0]),
                chemical_heat_release_w=float(phase.heat_release_rate*cyl.volume),
                cumulative_work_j=cyl.integrals[0],cumulative_wall_heat_loss_j=cyl.integrals[1],
                cumulative_chemical_heat_release_j=cyl.integrals[2]))
            times.append(t);heat_rates.append(heat_rate(t))
            mass_errors.append(abs(cyl.mass-mass)/mass)
            volume_errors.append(abs(cyl.volume-geometry(c,a)[0])/geometry(c,a)[0])
    except ct.CanteraError as error:
        raise ValueError(f'Cantera integration failed; this case was not simulated successfully: {error}') from error
    pressure=np.array([p['pressure_bar']*1e5 for p in trace]);volumes=np.array([p['volume_cm3']*1e-6 for p in trace])
    sampled_work=float(np.trapezoid(pressure,volumes))
    work,loss,chemical_heat=cyl.integrals
    residual=mass*cyl.phase.int_energy_mass-energy0+work+loss
    peak=max(trace,key=lambda p:p['pressure_bar'])
    vd=area*c['stroke_mm']/1000
    final_consumed=trace[-1]['mass_fraction_burned']
    onset=next((p['crank_angle_deg'] for p in trace if p['mass_fraction_burned']>=.05),None)
    status='substantial_reaction' if final_consumed>=.5 else ('partial_reaction' if final_consumed>=.05 else 'no_substantial_reaction')
    return dict(model='Cantera homogeneous hydrogen-air autoignition cylinder',solver='Cantera',
        cantera_version=ct.__version__,mechanism=MECHANISM,mechanism_sha256=mechanism_hash(),
        inputs=c,combustion_status=status,
        assumptions=['Cantera IdealGasReactor with finite-rate hydrogen/oxygen chemistry and variable thermodynamic properties.',
            'Premixed homogeneous charge, autoignition only; no spark, injection jet or flame propagation model.',
            'Closed compression/expansion from -180 to +180 degrees ATDC; no gas-exchange or residual-gas cycle.',
            'Load maps to equivalence ratio 0–0.9, not a measured fraction of rated brake torque.',
            'Analytic slider-crank volume; prescribed piston motion; constant heat-transfer coefficient, 450 K wall.',
            'Four identical cylinders assumed for gross indicated power; pumping and friction are excluded.',
            'Fuel-consumption fraction is based on remaining H2, not a prescribed or endpoint-normalized burn curve.',
            'h2o2.yaml is a demonstration mechanism; engine-condition suitability requires validation. It does not model NOx.'],
        summary=dict(peak_pressure_bar=peak['pressure_bar'],peak_pressure_angle_deg=peak['crank_angle_deg'],
            peak_temperature_k=max(p['temperature_k'] for p in trace),gross_imep_bar=work/vd/1e5,
            gross_indicated_power_kw=work*c['rpm']/120*4/1000,
            gross_indicated_efficiency_pct=work/(fuel*120e6)*100 if fuel>0 else None,
            fuel_mg_per_cylinder_cycle=fuel*1e6,work_j_per_cylinder=work,wall_heat_loss_j=loss,
            heat_release_j=chemical_heat,energy_residual_j=float(residual),
            final_h2_consumed_fraction=final_consumed,reaction_5pct_angle_deg=onset),
        numerical_checks=dict(energy_balance_definition='delta(total internal energy including chemical energy) + adaptively integrated work + wall heat loss',
            energy_residual_relative=abs(float(residual))/max(abs(work)+abs(loss),fuel*120e6,1),
            sampled_work_difference_j=sampled_work-work,
            max_mass_relative_error=max(mass_errors),max_volume_relative_error=max(volume_errors),
            output_step_deg=step_deg,rtol=net.rtol,atol=net.atol,max_internal_step_deg=.1),
        duration_seconds=time.perf_counter()-started,trace=trace)

def export_run(result,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    (folder/'run.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    with (folder/'cycle.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(result['trace'][0]));writer.writeheader();writer.writerows(result['trace'])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    injected='Hydrogen_Injection_Timing_deg_BTDC' in result['inputs']
    fig,axes=plt.subplots(3 if injected else 2,2,figsize=(11,10 if injected else 7),layout='constrained')
    points=result['trace'];angles=[p['crank_angle_deg'] for p in points]
    for axis,key,label in [(axes[0,0],'pressure_bar','Pressure (bar)'),(axes[0,1],'temperature_k','Temperature (K)'),(axes[1,1],'mass_fraction_burned','Fraction of scheduled H2 consumed' if injected else 'Fraction of initial H2 consumed')]:
        axis.plot(angles,[p[key] for p in points],color='#087f6a');axis.set(xlabel='Crank angle (degrees ATDC)',ylabel=label);axis.grid(alpha=.2)
    axes[1,0].plot([p['volume_cm3'] for p in points],[p['pressure_bar'] for p in points],color='#087f6a')
    axes[1,0].set(xlabel='Volume (cm³)',ylabel='Pressure (bar)');axes[1,0].grid(alpha=.2)
    if injected:
        axes[2,0].plot(angles,[p['hydrogen_injection_rate_kg_s'] for p in points],color='#2772aa')
        axes[2,0].set(xlabel='Crank angle (degrees ATDC)',ylabel='H2 injection rate (kg/s)');axes[2,0].grid(alpha=.2)
        for key,label in [('h2_mole_fraction','H2'),('h2o_mole_fraction','H2O')]:
            axes[2,1].plot(angles,[p[key] for p in points],label=label)
        axes[2,1].set(xlabel='Crank angle (degrees ATDC)',ylabel='Mole fraction');axes[2,1].grid(alpha=.2);axes[2,1].legend()
    caption='Timed gaseous H2 injection / homogeneous autoignition' if injected else 'Homogeneous premixed autoignition'
    fig.suptitle(f"Cantera {result['cantera_version']} · {result['mechanism']}\n{caption} · {result['combustion_status']}",fontsize=11)
    fig.savefig(folder/'cycle.png',dpi=180);plt.close(fig)
    (folder/'summary.json').write_text(json.dumps({k:v for k,v in result.items() if k!='trace'},indent=2),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='results/cantera_baseline')
    parser.add_argument('--inputs',help='JSON file containing engine inputs')
    args=parser.parse_args()
    config=json.loads(Path(args.inputs).read_text()) if args.inputs else {}
    result=simulate(config);export_run(result,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!='trace'},indent=2))
    print(f'CSV, JSON and plot saved to {Path(args.output).resolve()}')
