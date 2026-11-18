"""Spark-ignited direct-injection hydrogen engine: 0D closed-cycle performance model.

Produces the nine workbook output columns for any of the four workbook inputs, plus
crank-resolved traces for the 13-graph set. Chemistry (laminar flame speed, ignition
delay, adiabatic flame temperature, burned-gas equilibrium, species energies) comes
from Cantera tables built by chemistry_tables.py; the cycle itself is plain Python so a
full 2,625-case DOE runs in about a minute.

Run one case:  python si_engine.py --inputs case.json
"""
import argparse
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUTS = {'Compression_Ratio':(10.5,12.5),'Hydrogen_Injection_Timing_deg_BTDC':(60,160),
          'Engine_Load_pct':(0,100),'Engine_Speed_rpm':(2500,4500)}
DEFAULTS = {'Compression_Ratio':12.0,'Hydrogen_Injection_Timing_deg_BTDC':120,
            'Engine_Load_pct':50,'Engine_Speed_rpm':3500}
OUTPUTS = ['Brake_Power_kW','Torque_Nm','Brake_Thermal_Efficiency_pct','Fuel_Consumption_kg_per_hr',
           'Peak_Cylinder_Pressure_bar','Peak_Cylinder_Temperature_K','Combustion_Duration_deg_CA',
           'Knock_PreIgnition_Tendency','NOx_Emissions_ppm']
# Finalized specification from the review deck (Rahman, Kamil & Bakar 2011 architecture).
ENGINE = dict(cylinders=4,bore_mm=86.,stroke_mm=86.,conrod_mm=143.5,ivc_deg_atdc=-130.,evo_deg_atdc=130.,
    manifold_pressure_bar=1.013,manifold_temperature_k=298.,ivc_temperature_k=330.,wall_temperature_k=450.,
    injector_supply_bar=100.,injector_gas_temperature_k=300.,injector_effective_area_mm2=1.1,
    phi_at_zero_load=.2,phi_at_full_load=1.,ca50_target_deg_atdc=8.,wiebe_a=6.908,wiebe_m=2.,
    mixing_time_constant_ms_at_3000rpm=1.,residual_fraction=.05,flame_path_bore_fraction=.75,turbulence_constant=6.,
    woschni_multiplier=1.,lhv_mj_kg=120.,step_deg=.25)
ASSUMPTIONS = [
    '4-cylinder, 86 x 86 mm, 1996 cc spark-ignited DI hydrogen engine (review-deck specification); 143.5 mm rod assumed.',
    'Engine load is quality-controlled at wide-open throttle: load 0-100 % maps linearly to equivalence ratio 0.2-1.0.',
    'Closed cycle IVC (-130 deg) to EVO (+130 deg) integrated with Cantera species energies, 5 % residual gas and equilibrium dissociation; gas exchange uses a speed-dependent volumetric-efficiency curve.',
    'Gaseous H2 injected through a choked nozzle (100 bar, 1.1 mm2 effective area); fuel injected before IVC displaces air.',
    'Mixing: each fuel parcel mixes with a first-order time constant; incomplete mixing widens the local equivalence-ratio spread, lowers combustion efficiency and raises NOx.',
    'Spark is placed so CA50 = 8 deg ATDC (MBT rule). Wiebe burn duration from Cantera laminar flame speed and a turbulent flame-speed correlation.',
    'Woschni wall heat transfer, 450 K wall. Chen-Flynn friction and a speed-dependent pumping loss give brake quantities.',
    'NOx: extended-Zeldovich thermal NO in 24 burned-gas elements (Heywood rate, gri30 equilibrium); prompt and N2O routes neglected.',
    'Knock/pre-ignition tendency 0-5: 5 x Livengood-Wu end-gas integral (Cantera h2o2 ignition delay) plus a pre-IVC fuel exposure term.',
    'Phenomenological constants are literature-typical, not calibrated to a measured engine; validate before quantitative claims.']

def validate(inputs):
    if not isinstance(inputs,dict) or set(inputs)-set(INPUTS):raise ValueError('Use only the four workbook input columns.')
    c = DEFAULTS|inputs
    for key,(lo,hi) in INPUTS.items():
        v = c[key]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(f'{key} must be between {lo} and {hi}.')
    return {k:float(c[k]) for k in INPUTS}

@lru_cache(1)
def tables():
    import numpy as np
    from scipy.interpolate import RegularGridInterpolator as RGI
    path = ROOT/'data'/'chemistry'/'h2_tables.json'
    if not path.exists():raise ValueError('Chemistry tables missing: run python chemistry_tables.py once.')
    t = json.loads(path.read_text(encoding='utf-8'))
    s = t['species'];T = np.array(s['T_K'])/1000
    poly = {k:np.polyfit(T,np.array(s[k]['u_j_mol']),6).tolist() for k in ('O2','N2','H2','H2O')}
    ign = t['ignition_delay']
    tau = RGI((np.array(ign['phi']),1000/np.array(ign['T_K'])[::-1],np.log(ign['p_bar'])),
              np.log(np.array(ign['tau_s']))[:,::-1,:],bounds_error=False,fill_value=None)
    ad = t['adiabatic_flame'];eq = t['burned_equilibrium']
    tad = RGI((np.array(ad['phi']),np.array(ad['Tu_K'])),np.array(ad['T_K']),bounds_error=False,fill_value=None)
    grid = (np.array(eq['phi']),np.array(eq['T_K']))
    xeq = {k:RGI(grid,np.log(np.maximum(np.array(eq[k]),1e-30)),bounds_error=False,fill_value=None) for k in ('X_O2','X_N2','X_NO')}
    fit = t['flame_speed']['fit']
    return dict(poly=poly,tau=tau,tad=tad,xeq=xeq,fit=fit,raw=t)

def fingerprint():
    text = Path(__file__).read_text(encoding='utf-8')+(ROOT/'data'/'chemistry'/'h2_tables.json').read_text(encoding='utf-8')
    return hashlib.sha256(text.encode()).hexdigest()

def laminar_speed(phi,Tu,p_bar):
    """Cantera FreeFlame fit. The 1-D phi 0.3 flames sit at extinction (fit unreliable), so
    below phi 0.4 S_L0 is extrapolated log-linearly from phi 0.4-0.5 with phi 0.4 exponents."""
    f = tables()['fit'];P = f['phi']
    def at(i):return f['s_l0_m_s'][i],f['alpha'][i],f['beta'][i]
    if phi<=P[1]:
        (s0,a,b),(s1,_,_) = at(1),at(2)
        s = s0*math.exp(math.log(s1/s0)/(P[2]-P[1])*(phi-P[1]))
    else:
        i = min(int((phi-P[0])/(P[1]-P[0])+1e-9),len(P)-2);w = (phi-P[i])/(P[i+1]-P[i])
        (s0,a0,b0),(s1,a1,b1) = at(i),at(i+1)
        s = math.exp((1-w)*math.log(s0)+w*math.log(s1));a = (1-w)*a0+w*a1;b = (1-w)*b0+w*b1
    return s*(Tu/300)**a*(p_bar/1.01325)**b

def simulate(inputs,traces=False):
    import numpy as np
    c = validate(inputs);e = ENGINE;tb = tables()
    Ru = 8.314462618;W = {'O2':.031998,'N2':.028014,'H2':.002016,'H2O':.018015}
    B,S,L = e['bore_mm']/1e3,e['stroke_mm']/1e3,e['conrod_mm']/1e3;r = S/2;Ap = math.pi*B*B/4
    Vd = Ap*S;Vc = Vd/(c['Compression_Ratio']-1);rpm = c['Engine_Speed_rpm'];Sp = 2*S*rpm/60
    def volume(th):
        t = math.radians(th);return Vc+Ap*(r*(1-math.cos(t))+L-math.sqrt(L*L-(r*math.sin(t))**2))
    phi = e['phi_at_zero_load']+(e['phi_at_full_load']-e['phi_at_zero_load'])*c['Engine_Load_pct']/100
    # Gas exchange: speed-dependent volumetric efficiency; pre-IVC hydrogen displaces air.
    ve0 = .92-.06*((rpm-3800)/1500)**2
    rho = e['manifold_pressure_bar']*1e5/(287.05*e['manifold_temperature_k'])
    g = 1.405;Rh = Ru/W['H2'];T0 = e['injector_gas_temperature_k']
    mdot = e['injector_effective_area_mm2']*1e-6*e['injector_supply_bar']*1e5*math.sqrt(g/(Rh*T0))*(2/(g+1))**((g+1)/(2*(g-1)))
    x_h2 = 2*phi/(4.76+2*phi);soi = -c['Hydrogen_Injection_Timing_deg_BTDC'];ivc = e['ivc_deg_atdc']
    f_pre = 0.;m_air = ve0*rho*Vd
    for _ in range(4):
        m_air = ve0*rho*Vd*(1-f_pre*x_h2);m_fuel = phi*m_air/34.3
        dur = 6*rpm*m_fuel/mdot;f_pre = min(1.,max(0.,(ivc-soi)/dur))
    eoi = soi+dur;ve = m_air/(rho*Vd)
    # Spark timing, mixing and burn duration (fixed point: CA50 target).
    w50 = (math.log(2)/e['wiebe_a'])**(1/(e['wiebe_m']+1))
    Vivc = volume(ivc);n_air = m_air/.028966;n_fuel = m_fuel/W['H2']
    Pivc = (n_air+f_pre*n_fuel)*Ru*e['ivc_temperature_k']/Vivc
    tau_mix = e['mixing_time_constant_ms_at_3000rpm']*1e-3*(3000/rpm)**.5
    def mixedness(theta):
        """Mass-averaged mixedness and unmixed fraction of fuel at crank angle theta."""
        if theta<=soi:return 1.,0.
        k = 40;total = 0.;inj = 0
        for j in range(k):
            th = max(soi+(j+.5)*dur/k,ivc)
            if th>=theta:continue
            total += 1-math.exp(-(theta-th)/(6*rpm)/tau_mix);inj += 1
        return (total/inj if inj else 1.),1-inj/k
    spark = -20.
    for _ in range(6):
        pm = Pivc*(Vivc/volume(spark))**1.33;tu = e['ivc_temperature_k']*(Vivc/volume(spark))**.33
        H,f_late = mixedness(spark)
        sl = laminar_speed(phi,tu,pm/1e5)
        u_turb = .55*Sp+(2.5*math.exp(-max(0.,spark-eoi)/30) if eoi>soi else 0)*min(1.,m_fuel/.015)
        td = 1+2.*max(0.,.6-phi)  # thermo-diffusive enhancement of lean H2 turbulent flames
        st = sl*td*(1+e['turbulence_constant']*(u_turb/sl)**.7)
        burn = 6*rpm*e['flame_path_bore_fraction']*B/st*(1+.6*(1-H))
        burn = min(max(burn,12.),140.)
        new = max(-60.,min(-2.,e['ca50_target_deg_atdc']-w50*burn))
        if abs(new-spark)<.05:spark = new;break
        spark = new
    H,f_late = mixedness(spark)
    eta_c = .99-.25*(1-H)**1.5-.35*f_late-(.5*(.3-phi) if phi<.3 else 0)
    eta_c = max(.6,min(.99,eta_c))
    sigma = .03+.6*(1-H)+.5*f_late
    a,m = e['wiebe_a'],e['wiebe_m']
    def xb(th):
        if th<=spark:return 0.
        return 1-math.exp(-a*((th-spark)/burn)**(m+1))
    d1090 = burn*((math.log(10)/a)**(1/(m+1))-(math.log(1/.9)/a)**(1/(m+1)))
    # Closed-cycle integration with Cantera-derived species internal energies (formation included).
    poly = tb['poly']
    def u_coeffs(n):return [sum(poly[k][j]*n[k] for k in n) for j in range(7)]
    def horner(cf,x):
        v = 0.
        for q in cf:v = v*x+q
        return v
    def temperature(U,n,guess):
        cf = u_coeffs(n);d = [q*(6-j) for j,q in enumerate(cf[:-1])];T = guess
        for _ in range(8):
            f = horner(cf,T/1000)-U
            T -= f/(horner(d,T/1000)/1000)
            if abs(f)<1e-7*abs(U)+1e-9:break
        return T
    # Residual burned gas (complete-combustion products of the previous cycle) dilutes the charge.
    xr = e['residual_fraction'];res = {'O2':xr*.21*n_air*(1-phi),'N2':xr*.79*n_air,'H2O':xr*.42*phi*n_air}
    base = {'O2':.21*n_air+res['O2'],'N2':.79*n_air+res['N2'],'H2O':res['H2O']}
    eq = tb['raw']['burned_equilibrium'];pg = eq['phi'];k = min(int(phi/.1+1e-9)-1,len(pg)-2);k = max(k,0)
    wphi = min(max((phi-pg[k])/(pg[k+1]-pg[k]),0.),1.)
    drow = [(1-wphi)*a1+wphi*a2 for a1,a2 in zip(eq['dissociation_energy_fraction'][k],eq['dissociation_energy_fraction'][k+1])]
    t0,tstep = eq['T_K'][0],eq['T_K'][1]-eq['T_K'][0]
    def dissociated(T):
        if T<=t0:return drow[0]*max(0.,(T-1000.)/(t0-1000.))  # taper to zero; no step at the table edge
        f = min((T-t0)/tstep,len(drow)-1.001);j = int(f);return drow[j]+(f-j)*(drow[j+1]-drow[j])
    def mixture(inside,committed,T):
        formed = committed*(1-dissociated(T))
        return {'O2':base['O2']-.5*formed,'N2':base['N2'],'H2':inside-formed,'H2O':base['H2O']+formed},formed
    def solve_state(Ut,inside,committed,T):
        """Newton on T with the dissociated composition evaluated at each T (energy-consistent state)."""
        if T<950:  # no dissociation below 1000 K: composition is fixed, so the plain solve is exact
            Tf = temperature(Ut,mixture(inside,committed,T)[0],T)
            if Tf<1000:return Tf
        for _ in range(15):
            f = horner(u_coeffs(mixture(inside,committed,T)[0]),T/1000)-Ut
            if abs(f)<1e-10*abs(Ut)+1e-9:break
            d = horner(u_coeffs(mixture(inside,committed,T+.5)[0]),(T+.5)/1000)-Ut-f
            T -= f/d*.5
        return T
    n,_ = mixture(f_pre*n_fuel,0.,e['ivc_temperature_k'])
    T = e['ivc_temperature_k'];U = horner(u_coeffs(n),T/1000)
    h_inj = horner(poly['H2'],T0/1000)+Ru*T0  # molar enthalpy of injected H2
    step = e['step_deg'];steps = round((e['evo_deg_atdc']-ivc)/step);omega = 6*rpm
    inj_rate = n_fuel/dur;injected = f_pre*n_fuel;burned_n = 0.;formed = 0.;fuel_target = eta_c*n_fuel
    work = qwall = 0.;Tr = T;Pr = Pivc;Vr = Vivc
    rec = {k:[] for k in ('theta','V','P','T','x','hrr','qw','inj','work','Pmot')}
    V = Vivc;P = Pivc;th = ivc;max_resid = 0.
    for i in range(steps+1):
        if i:
            th1 = ivc+i*step;V1 = volume(th1);dt = step/omega
            dn_inj = 0.
            lo,hi = max(th,soi,ivc),min(th1,eoi)
            if hi>lo:dn_inj = inj_rate*(hi-lo)
            dn_inj = min(dn_inj,n_fuel-injected)
            dburn = min(fuel_target*(xb(th1)-xb(th)),injected+dn_inj-burned_n)
            Pmot = Pivc*(Vivc/V)**1.33
            wv = 2.28*Sp+(3.24e-3*Vd*Tr/(Pr*Vr)*max(0.,P-Pmot) if th>spark else 0)
            h = e['woschni_multiplier']*3.26*B**-.2*(P/1e3)**.8*T**-.55*wv**.8
            area = 2.2*Ap+math.pi*B*V/Ap
            dq = h*area*(T-e['wall_temperature_k'])*dt
            Utr = U-dq+dn_inj*h_inj
            Pg = P;T1 = T
            for _ in range(3):  # trapezoidal p dV: fixed point on the end pressure
                dW = .5*(P+Pg)*(V1-V)
                T1 = solve_state(Utr-dW,injected+dn_inj,burned_n+dburn,T1)
                n2,formed2 = mixture(injected+dn_inj,burned_n+dburn,T1)
                Pg = sum(n2.values())*Ru*T1/V1
            U = Utr-dW;work += dW;qwall += dq;dformed = formed2-formed;formed = formed2  # U matches the solved state exactly
            injected += dn_inj;burned_n += dburn;n = n2;T = T1;P = Pg;V = V1;th = th1
            max_resid = max(max_resid,abs(horner(u_coeffs(n),T/1000)-U)/abs(U))
            if th<=spark+step:Tr,Pr,Vr = T,P,V
            rec['hrr'].append(dformed*241.8e3/step);rec['qw'].append(dq/dt/1e3)
            rec['inj'].append(dn_inj*W['H2']/dt*1e3)
        else:
            Pmot = Pivc;rec['hrr'].append(0.);rec['qw'].append(0.);rec['inj'].append(0.)
        rec['theta'].append(th);rec['V'].append(V);rec['P'].append(P);rec['T'].append(T)
        rec['x'].append(xb(th));rec['work'].append(work);rec['Pmot'].append(Pmot)
    th_a = np.array(rec['theta']);P_a = np.array(rec['P']);x_a = np.array(rec['x']);T_a = np.array(rec['T'])
    # Unburned end gas: isentropic from spark; before spark it is the mean charge.
    i_s = int(np.searchsorted(th_a,spark));gam_u = 1.37
    Tu = T_a.copy();Tu[i_s:] = T_a[i_s]*(P_a[i_s:]/P_a[i_s])**((gam_u-1)/gam_u)
    # Livengood-Wu knock integral over unburned gas until 95 % burned.
    phi_k = min(max(phi*(1+sigma),.2),1.)
    tau = np.exp(tb['tau'](np.column_stack([np.full_like(Tu,phi_k),1000/np.clip(Tu,800,1550),np.log(np.clip(P_a/1e5,10,120))])))
    tau = np.where(Tu<800,np.inf,tau)
    dt = step/omega;lw = np.cumsum(np.where(x_a<.95,dt/tau,0.))
    knock = min(5.,5*min(1.,float(lw[-1]))+1.5*f_pre*phi)
    # Thermal NO in burned-gas elements (3-point local-phi quadrature for stratification).
    N = 24;zq = np.array([-1.2247449,0.,1.2247449]);wq = np.array([1/6,2/3,1/6])
    phis = np.clip(phi*(1+sigma*zq),.1,1.)
    xk = (np.arange(N)+.5)/N*min(1.,x_a[-1])
    ik = np.searchsorted(x_a,xk);ik = np.clip(ik,0,len(x_a)-1)
    Pk = P_a[ik];Tuk = Tu[ik]
    Tb0 = tb['tad'](np.column_stack([np.repeat(phis[None,:],N,0).ravel(),np.repeat(Tuk,3)])).reshape(N,3)
    gam_b = 1.26;Xno = np.zeros((N,3));no_trace = [];stride = 4;dt_no = stride*step/omega
    for j in range(len(th_a)):
        alive = ik<=j
        if alive.any() and j%stride==0:
            Tb = Tb0*((P_a[j]/Pk)**((gam_b-1)/gam_b))[:,None]
            pts = np.column_stack([np.repeat(phis[None,:],N,0).ravel(),np.clip(Tb,1400,3300).ravel()])
            xo2,xn2,xno = (np.exp(tb['xeq'][k](pts)).reshape(N,3) for k in ('X_O2','X_N2','X_NO'))
            conc = P_a[j]/(Ru*Tb)/1e6  # mol/cm3
            rate = 6e16/np.sqrt(Tb)*np.exp(-69090/Tb)*np.sqrt(xo2*conc)*(xn2*conc)  # Heywood eq. 11.8
            alpha = np.clip(Xno/np.maximum(xno,1e-30),0,1)
            dX = rate*(1-alpha**2)/conc*dt_no
            dX = np.where(Tb<1400,0.,dX)
            Xno = np.where(alive[:,None],np.minimum(Xno+dX,np.maximum(xno,Xno)),0.)
        no_trace.append(float((Xno@wq).sum()/N*1e6))
    nox = no_trace[-1]
    # Brake quantities.
    imep = work/Vd/1e5;pmax = float(P_a.max())/1e5
    pmep = .08+.003*Sp**2;fmep = .25+.005*pmax+.09*Sp+.0009*Sp**2
    bmep = imep-pmep-fmep;ncyl = e['cylinders']
    power = bmep*1e5*Vd*ncyl*rpm/120/1e3;torque = bmep*1e5*Vd*ncyl/(4*math.pi)
    fuel_kgh = m_fuel*ncyl*rpm/120*3600;fuel_w = m_fuel*ncyl*rpm/120*e['lhv_mj_kg']*1e6
    bte = power*1e3/fuel_w*100 if fuel_w else 0.
    q_fuel = m_fuel*e['lhv_mj_kg']*1e6
    unburned = q_fuel-burned_n*W['H2']*e['lhv_mj_kg']*1e6
    sampled = float(np.trapezoid(P_a,np.array(rec['V'])))
    i_peak = int(P_a.argmax())
    summary = {'Brake_Power_kW':power,'Torque_Nm':torque,'Brake_Thermal_Efficiency_pct':bte,
        'Fuel_Consumption_kg_per_hr':fuel_kgh,'Peak_Cylinder_Pressure_bar':pmax,
        'Peak_Cylinder_Temperature_K':float(T_a.max()),'Combustion_Duration_deg_CA':d1090,
        'Knock_PreIgnition_Tendency':knock,'NOx_Emissions_ppm':nox,
        'equivalence_ratio':phi,'gross_imep_bar':imep,'bmep_bar':bmep,'fmep_bar':fmep,'pmep_bar':pmep,
        'bsfc_g_per_kwh':fuel_kgh*1e3/power if power>0 else None,'volumetric_efficiency_pct':ve*100,
        'spark_timing_deg_atdc':spark,'ca50_deg_atdc':spark+w50*burn,'burn_duration_0_100_deg':burn,
        'injection_duration_deg':dur,'end_of_injection_deg_atdc':eoi,'fuel_before_ivc_fraction':f_pre,
        'fuel_after_spark_fraction':f_late,'mixture_homogeneity':H,'local_phi_spread':sigma,
        'combustion_efficiency_pct':eta_c*100,'livengood_wu_integral':float(lw[-1]),
        'peak_pressure_angle_deg':float(th_a[i_peak]),'fuel_mg_per_cylinder_cycle':m_fuel*1e6}
    result = {'model':'0D spark-ignited DI hydrogen cycle (Cantera chemistry tables)','inputs':c,'engine':ENGINE,
        'assumptions':ASSUMPTIONS,'summary':summary,
        'numerical_checks':{'max_energy_inversion_residual':max_resid,'sampled_work_difference_j':sampled-work,
                            'output_step_deg':step}}
    if traces:
        lhv = e['lhv_mj_kg']*1e6
        mixed = [mixedness(t)[0] if t>soi else None for t in th_a[::4]]
        result['energy_balance_j'] = {'Fuel energy':q_fuel,'Brake work':bmep*1e5*Vd,'Friction':fmep*1e5*Vd,
            'Pumping':pmep*1e5*Vd,'Wall heat':qwall,'Unburned fuel':unburned,
            'Exhaust (remainder)':q_fuel-imep*1e5*Vd+pmep*1e5*Vd-qwall-unburned-pmep*1e5*Vd}
        sl = slice(None,None,2)
        result['trace'] = {'theta':th_a[sl].tolist(),'volume_cm3':(np.array(rec['V'])*1e6)[sl].tolist(),
            'pressure_bar':(P_a/1e5)[sl].tolist(),'motored_pressure_bar':(np.array(rec['Pmot'])/1e5)[sl].tolist(),
            'temperature_k':T_a[sl].tolist(),'unburned_temperature_k':Tu[sl].tolist(),
            'burned_core_temperature_k':[None if x<=.0 else float(Tb0[0,1]*(p/Pk[0])**((gam_b-1)/gam_b)) for x,p in zip(x_a[sl],P_a[sl])],
            'mass_fraction_burned':x_a[sl].tolist(),'heat_release_j_per_deg':rec['hrr'][::2],
            'wall_heat_kw':rec['qw'][::2],'injection_rate_g_s':rec['inj'][::2],
            'nox_ppm':no_trace[::2],'knock_integral':lw[sl].tolist(),'cumulative_work_j':rec['work'][::2],
            'mixedness_theta':th_a[::4].tolist(),'mixedness':mixed}
    return result

if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs')
    args = parser.parse_args()
    data = json.loads(Path(args.inputs).read_text()) if args.inputs else {}
    out = simulate(data);print(json.dumps(out['summary'],indent=2))
