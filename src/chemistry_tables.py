"""Cantera-generated hydrogen chemistry tables used by the spark-ignition cycle model.

Build once (about 1-3 minutes, parallel):  python chemistry_tables.py
Output: data/chemistry/h2_tables.json  (shipped; the cycle model only reads it)

Tables
  laminar flame speed   h2o2.yaml FreeFlame, phi 0.3-1.0 x Tu 300/500/700 K x p 1/5/15 atm,
                        fitted per phi as  S_L = S_L0 (Tu/300)^alpha (p/1 atm)^beta
  ignition delay        h2o2.yaml constant-pressure reactor, phi x T x p   (knock integral)
  adiabatic flame T     gri30.yaml HP equilibrium, phi x Tu at 50 bar      (burned-zone NOx)
  burned-gas equilibrium gri30.yaml TP equilibrium X_O2, X_N2, X_NO and dissociation energy, phi x T at 50 bar
  species energy        molar internal energy (incl. formation) of O2, N2, H2, H2O vs T
"""
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent/'data'/'chemistry'/'h2_tables.json'
FLAME_PHI = [.3,.4,.5,.6,.7,.8,.9,1.]
FLAME_T = [300.,500.,700.]
FLAME_P = [1.,5.,15.]
IGN_PHI = [.2,.4,.6,.8,1.]
IGN_T = [float(t) for t in range(800,1551,50)]
IGN_P = [10.,20.,40.,80.,120.]
TAD_PHI = [round(.1*i,1) for i in range(1,11)]
TAD_TU = [float(t) for t in range(300,1301,100)]
EQ_T = [float(t) for t in range(1400,3301,100)]
SPECIES_T = [float(t) for t in range(250,3601,25)]
SPECIES = ['O2','N2','H2','H2O']

def _flame(args):
    phi,T,p = args
    import cantera as ct
    gas = ct.Solution('h2o2.yaml')
    gas.set_equivalence_ratio(phi,'H2:1','O2:1,N2:3.76'); gas.TP = T,p*ct.one_atm
    flame = ct.FreeFlame(gas,width=.03/p**.6)
    flame.set_refine_criteria(ratio=3,slope=.08,curve=.15)
    try:
        flame.solve(loglevel=0,auto=True)
        speed = float(flame.velocity[0])
        return speed if speed>.01 else None
    except Exception:
        return None

def _ignition(args):
    phi,T,p = args
    import cantera as ct
    gas = ct.Solution('h2o2.yaml')
    gas.set_equivalence_ratio(phi,'H2:1','O2:1,N2:3.76'); gas.TP = T,p*1e5
    reactor = ct.IdealGasConstPressureReactor(gas,clone=True); net = ct.ReactorNet([reactor])
    while reactor.T < T+400 and net.time < 1.:
        net.step()
    return min(net.time,1.)

def build(workers=10):
    import cantera as ct
    import numpy as np
    begin = time.perf_counter()
    flame_jobs = [(phi,T,p) for phi in FLAME_PHI for T in FLAME_T for p in FLAME_P]
    ign_jobs = [(phi,T,p) for phi in IGN_PHI for T in IGN_T for p in IGN_P]
    with ProcessPoolExecutor(workers) as pool:
        flames = list(pool.map(_flame,flame_jobs))
        delays = list(pool.map(_ignition,ign_jobs))
    fit = {'phi':FLAME_PHI,'s_l0_m_s':[],'alpha':[],'beta':[],'points_used':[]}
    for phi in FLAME_PHI:
        rows = [(T,p,s) for (f,T,p),s in zip(flame_jobs,flames) if f==phi and s]
        A = np.array([[1,math.log(T/300),math.log(p)] for T,p,_ in rows])
        b = np.array([math.log(s) for *_,s in rows])
        coef = np.linalg.lstsq(A,b,rcond=None)[0]
        fit['s_l0_m_s'].append(float(math.exp(coef[0]))); fit['alpha'].append(float(coef[1]))
        fit['beta'].append(float(coef[2])); fit['points_used'].append(len(rows))
    gas = ct.Solution('gri30.yaml')
    tad = []
    for phi in TAD_PHI:
        row = []
        for Tu in TAD_TU:
            gas.set_equivalence_ratio(phi,'H2:1','O2:1,N2:3.76'); gas.TP = Tu,50e5
            gas.equilibrate('HP'); row.append(float(gas.T))
        tad.append(row)
    eq = {k:[] for k in ('X_O2','X_N2','X_NO')}
    for phi in TAD_PHI:
        rows = {k:[] for k in eq}
        for T in EQ_T:
            gas.set_equivalence_ratio(phi,'H2:1','O2:1,N2:3.76'); gas.TP = T,50e5
            gas.equilibrate('TP')
            for k in eq: rows[k].append(float(gas[k[2:]].X[0]))
        for k in eq: eq[k].append(rows[k])
    # Fraction of the fuel's heating value still held in dissociated products at (phi, T):
    # [u_equilibrium(T) - u_complete_combustion(T)] / (fuel LHV), per kg of mixture.
    dissociation = []
    for phi in TAD_PHI:
        row = []
        gas.set_equivalence_ratio(phi,'H2:1','O2:1,N2:3.76'); y_fuel = float(gas['H2'].Y[0])
        x = gas.X; n_h2 = float(gas['H2'].X[0]); n_o2 = float(gas['O2'].X[0]); n_n2 = float(gas['N2'].X[0])
        complete = {'H2O':n_h2,'O2':n_o2-n_h2/2,'N2':n_n2}
        for T in EQ_T:
            gas.TPX = T,50e5,complete; u_complete = gas.int_energy_mass
            gas.equilibrate('TP'); row.append(max(0.,(gas.int_energy_mass-u_complete)/(y_fuel*119.96e6)))
        dissociation.append(row)
    thermo = {}
    for name in SPECIES:
        sp = gas.species(name); R = ct.gas_constant
        thermo[name] = {'u_j_mol':[(sp.thermo.h(T)-R*T)/1000 for T in SPECIES_T],
                        'cv_j_mol_k':[(sp.thermo.cp(T)-R)/1000 for T in SPECIES_T]}
    tables = {'generated_by':'chemistry_tables.py','cantera_version':ct.__version__,
        'mechanisms':{'flame_speed':'h2o2.yaml','ignition_delay':'h2o2.yaml','equilibrium':'gri30.yaml'},
        'flame_speed':{'grid':{'phi':FLAME_PHI,'T_K':FLAME_T,'p_atm':FLAME_P},
                       'raw_m_s':[s for s in flames],'fit':fit},
        'ignition_delay':{'phi':IGN_PHI,'T_K':IGN_T,'p_bar':IGN_P,'criterion':'time to +400 K, constant pressure; capped at 1 s',
                          'tau_s':np.array(delays).reshape(len(IGN_PHI),len(IGN_T),len(IGN_P)).tolist()},
        'adiabatic_flame':{'phi':TAD_PHI,'Tu_K':TAD_TU,'p_bar':50,'T_K':tad},
        'burned_equilibrium':{'phi':TAD_PHI,'T_K':EQ_T,'p_bar':50,**eq,'dissociation_energy_fraction':dissociation},
        'species':{'T_K':SPECIES_T,**thermo},
        'build_seconds':time.perf_counter()-begin}
    PATH.parent.mkdir(parents=True,exist_ok=True)
    PATH.write_text(json.dumps(tables),encoding='utf-8')
    return tables

if __name__=='__main__':
    t = build()
    print(json.dumps(t['flame_speed']['fit'],indent=1))
    print('failed flames:',sum(s is None for s in t['flame_speed']['raw_m_s']),'build s:',round(t['build_seconds'],1))
