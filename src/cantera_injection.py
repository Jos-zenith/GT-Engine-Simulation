"""Four-input Cantera cylinder with timed, homogeneous gaseous hydrogen injection.

Run with .venv\Scripts\python cantera_injection.py --output results/cantera_injection
Ignition is provisional autoignition; this is not a calibrated spark/jet engine model.
"""
import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from cantera_engine import geometry,mechanism_hash,export_run

DEFAULTS={'Compression_Ratio':11.5,'Hydrogen_Injection_Timing_deg_BTDC':160,
          'Engine_Load_pct':75,'Engine_Speed_rpm':3000}
BOUNDS={'Compression_Ratio':(10.5,12.5),'Hydrogen_Injection_Timing_deg_BTDC':(60,160),
        'Engine_Load_pct':(0,100),'Engine_Speed_rpm':(2500,4500)}
FIXED_ENGINE=dict(bore_mm=100,stroke_mm=100,conrod_mm=220,intake_temperature_k=450,
    intake_pressure_bar=1,heat_transfer_coefficient=150,wall_temperature_k=450,
    injection_duration_deg=20,hydrogen_temperature_k=300,hydrogen_supply_pressure_bar=100)

def validate(inputs):
    if not isinstance(inputs,dict) or set(inputs)-set(DEFAULTS):raise ValueError('Use only the four workbook input columns.')
    c=DEFAULTS|inputs
    for key,(lo,hi) in BOUNDS.items():
        v=c[key]
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(f'{key} must be between {lo} and {hi}.')
    return c

def fingerprint():
    import cantera as ct
    return hashlib.sha256((Path(__file__).read_text(encoding='utf-8')+
        Path(__file__).with_name('cantera_engine.py').read_text(encoding='utf-8')+
        ct.__version__+mechanism_hash()).encode()).hexdigest()

def simulate(inputs,step_deg=.25):
    import cantera as ct
    import numpy as np
    c=validate(inputs)
    if step_deg not in (.125,.25,.5,1):raise ValueError('Unsupported output resolution.')
    begin=time.perf_counter();f=FIXED_ENGINE.copy()
    geo=f|{'compression_ratio':c['Compression_Ratio']}
    rpm=c['Engine_Speed_rpm'];omega=2*math.pi*rpm/60
    def angle(t):return -180+6*rpm*t
    def heat_rate_for(reactor,t):
        return f['heat_transfer_coefficient']*geometry(geo,angle(t))[2]*(reactor.T-f['wall_temperature_k'])
    air=ct.Solution('h2o2.yaml');air.TPX=f['intake_temperature_k'],f['intake_pressure_bar']*1e5,'O2:1,N2:3.76'
    volume0=geometry(geo,-180)[0]
    air_mass=air.density*volume0
    total_fuel=air_mass*(.9*c['Engine_Load_pct']/100)/34.3
    injection_start=(180-c['Hydrogen_Injection_Timing_deg_BTDC'])/(6*rpm)
    injection_time=f['injection_duration_deg']/(6*rpm)
    def mass_rate(t):
        x=(t-injection_start)/injection_time
        return total_fuel/injection_time*(1-math.cos(2*math.pi*x)) if 0<x<1 else 0.
    supply_gas=ct.Solution('h2o2.yaml');supply_gas.TPX=f['hydrogen_temperature_k'],f['hydrogen_supply_pressure_bar']*1e5,'H2:1'
    incoming_enthalpy=supply_gas.enthalpy_mass

    class InjectedCylinder(ct.ExtensibleIdealGasReactor):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.integrals=[0.]*5
        def after_initialize(self,t0):self.qstart=self.n_vars;self.n_vars+=5
        def after_get_state(self,y):
            for j,v in enumerate(self.integrals):y[self.qstart+j]=v
        def after_update_state(self,y):self.integrals=[float(y[self.qstart+j]) for j in range(5)]
        def after_eval(self,t,lhs,rhs):
            rates=[self.phase.P*geometry(geo,angle(t))[1]*omega,heat_rate_for(self,t),
                   self.phase.heat_release_rate*self.volume,mass_rate(t),mass_rate(t)*incoming_enthalpy]
            for j,v in enumerate(rates):lhs[self.qstart+j]=1.;rhs[self.qstart+j]=v
        def before_component_name(self,index):
            if self.qstart<=index<self.n_vars:return ['work','wall_heat','chemical_heat','injected_fuel','inlet_enthalpy'][index-self.qstart]

    cyl=InjectedCylinder(air,clone=True,energy='on',name='hydrogen_injected_cylinder');cyl.volume=volume0
    supply=ct.Reservoir(supply_gas,clone=True,name='hydrogen_supply')
    injector=ct.MassFlowController(supply,cyl,mdot=mass_rate)
    wallgas=ct.Solution('h2o2.yaml');wallgas.TPX=f['wall_temperature_k'],ct.one_atm,'O2:1,N2:3.76'
    bath=ct.Reservoir(wallgas,clone=True,name='wall_boundary')
    area=math.pi*(f['bore_mm']/1000)**2/4
    piston=ct.Wall(cyl,bath,A=area,velocity=lambda t:geometry(geo,angle(t))[1]*omega/area)
    thermal=ct.Wall(cyl,bath,A=1,Q=lambda t:heat_rate_for(cyl,t))
    net=ct.ReactorNet([cyl]);net.rtol=1e-9;net.atol=1e-15;net.max_time_step=.1/(6*rpm);net.max_steps=50000
    energy0=cyl.mass*cyl.phase.int_energy_mass
    trace=[];mass_error=volume_error=0.
    try:
        for i in range(round(360/step_deg)+1):
            a=-180+i*step_deg;t=(a+180)/(6*rpm)
            if i:net.advance(t)
            gas=cyl.phase
            consumed=cyl.integrals[3]-cyl.mass*float(gas['H2'].Y[0])
            fraction=max(0,min(1,consumed/total_fuel)) if total_fuel else 0.
            trace.append(dict(crank_angle_deg=a,time_s=t,volume_cm3=cyl.volume*1e6,pressure_bar=gas.P/1e5,
                temperature_k=cyl.T,mass_fraction_burned=fraction,
                h2_mole_fraction=float(gas['H2'].X[0]),o2_mole_fraction=float(gas['O2'].X[0]),
                h2o_mole_fraction=float(gas['H2O'].X[0]),oh_mole_fraction=float(gas['OH'].X[0]),
                hydrogen_injection_rate_kg_s=mass_rate(t),injected_hydrogen_mg=cyl.integrals[3]*1e6,
                chemical_heat_release_w=gas.heat_release_rate*cyl.volume,
                cumulative_work_j=cyl.integrals[0],cumulative_wall_heat_loss_j=cyl.integrals[1],
                cumulative_chemical_heat_release_j=cyl.integrals[2]))
            mass_error=max(mass_error,abs(cyl.mass-air_mass-cyl.integrals[3])/air_mass)
            volume_error=max(volume_error,abs(cyl.volume-geometry(geo,a)[0])/geometry(geo,a)[0])
    except ct.CanteraError as error:raise ValueError(f'Cantera failed; no successful simulation claimed: {error}') from error
    work,loss,heat,fuel,inlet_energy=cyl.integrals
    residual=cyl.mass*cyl.phase.int_energy_mass-energy0+work+loss-inlet_energy
    peak=max(trace,key=lambda p:p['pressure_bar']);vd=area*f['stroke_mm']/1000
    burned=trace[-1]['mass_fraction_burned']
    status='substantial_reaction' if burned>=.5 else ('partial_reaction' if burned>=.05 else 'no_substantial_reaction')
    return dict(model='Cantera timed hydrogen-injection cylinder; homogeneous mixing, provisional autoignition',solver='Cantera',
        cantera_version=ct.__version__,mechanism='h2o2.yaml',mechanism_sha256=mechanism_hash(),inputs=c,fixed_engine=f,
        combustion_status=status,assumptions=[
        'Four varied inputs match the workbook: compression ratio, hydrogen start of injection BTDC, fuel-load setting, RPM.',
        'Initially air only; a Cantera MassFlowController adds gaseous H2 over a fixed 20-degree raised-cosine pulse.',
        'Injected fuel is mixed homogeneously immediately; injector jets, stratification and flame propagation are not resolved.',
        'Autoignition chemistry only; no spark timing/energy is specified by the workbook. Ignition treatment is provisional.',
        'Fixed geometry, 450 K preheated initial air, 300 K injected hydrogen, 1 bar initial pressure, 450 K wall are assumptions, not calibrated engine data.',
        'Fuel-load setting maps to scheduled H2 mass = initial air mass * (0.9 * load/100) / 34.3; not rated brake load.',
        'Compression/expansion only; no gas exchange, residual-gas cycle, pumping or mechanical friction. Power assumes four cylinders.',
        'h2o2.yaml provides finite-rate H2 chemistry; mechanism/engine accuracy needs validation. NOx and knock are not modelled.'],
        summary=dict(peak_pressure_bar=peak['pressure_bar'],peak_pressure_angle_deg=peak['crank_angle_deg'],
            peak_temperature_k=max(p['temperature_k'] for p in trace),gross_imep_bar=work/vd/1e5,
            gross_indicated_power_kw=work*rpm/120*4/1000,gross_indicated_efficiency_pct=work/(fuel*120e6)*100 if fuel>1e-15 else None,
            fuel_mg_per_cylinder_cycle=fuel*1e6,fuel_consumption_kg_per_hr=fuel*rpm/120*4*3600,
            work_j_per_cylinder=work,wall_heat_loss_j=loss,heat_release_j=heat,energy_residual_j=residual,
            final_h2_consumed_fraction=burned,reaction_5pct_angle_deg=next((p['crank_angle_deg'] for p in trace if p['mass_fraction_burned']>=.05),None)),
        numerical_checks=dict(energy_balance_definition='delta(total internal energy) + work + wall heat - inlet enthalpy; all integrals use adaptive solver steps',
            energy_residual_relative=abs(residual)/max(total_fuel*120e6,abs(work)+abs(loss),1),max_mass_relative_error=mass_error,
            max_volume_relative_error=volume_error,injection_mass_relative_error=abs(fuel-total_fuel)/total_fuel if total_fuel else 0.,
            output_step_deg=step_deg,rtol=net.rtol,atol=net.atol,max_internal_step_deg=.1),duration_seconds=time.perf_counter()-begin,trace=trace)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default='results/cantera_injection')
    parser.add_argument('--inputs',help='JSON file with the four workbook column names')
    args=parser.parse_args();inputs=json.loads(Path(args.inputs).read_text()) if args.inputs else {}
    result=simulate(inputs);export_run(result,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!='trace'},indent=2))
