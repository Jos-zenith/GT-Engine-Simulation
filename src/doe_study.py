"""Full-factorial DOE study: simulate every workbook setup, learn a surrogate, rank and optimise.

  python doe_study.py run                 simulate all 2,625 setups + train/compare ML models
  python doe_study.py optimize            GA (differential evolution) on the ML surrogate + physics check
  python doe_study.py graphs --top 25     13-graph PNG sheet for the best-ranked setups (--all for every setup)

Outputs go to data/study/: doe_results.csv, doe_filled.xlsx (the original workbook with its
nine output columns filled), ml_report.json, surrogate.joblib, study_meta.json.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import re
import threading
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from si_engine import INPUTS, OUTPUTS, ENGINE, simulate, fingerprint, validate

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT/'data'/'study'
EXTRAS = ['equivalence_ratio','spark_timing_deg_atdc','ca50_deg_atdc','mixture_homogeneity','combustion_efficiency_pct',
          'volumetric_efficiency_pct','bmep_bar','gross_imep_bar','bsfc_g_per_kwh','injection_duration_deg','local_phi_spread']
FEATURES = list(INPUTS)
# Desirability objectives (Derringer-Suich). 'log' scales NOx, which spans four decades.
OBJECTIVES = {'Brake_Thermal_Efficiency_pct':'max','Brake_Power_kW':'max','NOx_Emissions_ppm':'min_log',
              'Knock_PreIgnition_Tendency':'min'}
DEFAULT_WEIGHTS = {'Brake_Thermal_Efficiency_pct':1.,'Brake_Power_kW':1.,'NOx_Emissions_ppm':1.,'Knock_PreIgnition_Tendency':.5}
DEFAULT_KNOCK_LIMIT = 2.5

_lock = threading.Lock()
_state = {'status':'idle','progress':0,'message':'Run the full DOE to simulate every workbook setup.'}
_surrogate = None

# ---------------------------------------------------------------- simulation

def _run_case(case):
    out = simulate(case['inputs'])
    return case['case_id'],out['summary'],out['numerical_checks']['max_energy_inversion_residual']

def run_doe(progress=lambda p,m:None,workers=None):
    from workbook_cases import load_cases
    cases,workbook = load_cases()
    infill = infill_cases()
    everything = cases+infill;worst = 0.;done = {}
    begin = time.perf_counter()
    with ProcessPoolExecutor(workers) as pool:
        for k,(case_id,summary,resid) in enumerate(pool.map(_run_case,everything,chunksize=16)):
            worst = max(worst,resid);done[case_id] = summary
            if k%25==0:progress(round(k/len(everything)*60),f'Simulating setups: {k:,}/{len(everything):,} ({len(cases):,} workbook + {len(infill):,} infill)')
    def table(group):
        return [{'S.NO':c['case_id'],**c['inputs'],**{o:done[c['case_id']][o] for o in OUTPUTS},
                 **{x:done[c['case_id']][x] for x in EXTRAS}} for c in group]
    rows,infill_rows = table(cases),table(infill)
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    columns = ['S.NO',*FEATURES,*OUTPUTS,*EXTRAS]
    for name,group in (('doe_results.csv',rows),('infill_results.csv',infill_rows)):
        buffer = io.StringIO(newline='');writer = csv.DictWriter(buffer,fieldnames=columns);writer.writeheader();writer.writerows(group)
        _atomic(DIRECTORY/name,buffer.getvalue())
        if name=='doe_results.csv':digest = hashlib.sha256(buffer.getvalue().encode()).hexdigest()
    fill_workbook(rows)
    meta = {'created_utc':datetime.now(timezone.utc).isoformat(),'cases':len(rows),'infill_cases':len(infill_rows),'workbook':workbook,
            'simulation_seconds':time.perf_counter()-begin,'simulator_sha256':fingerprint(),'engine':ENGINE,
            'max_energy_inversion_residual':worst,'columns':columns,
            'dataset_sha256':digest,'infill_design':'Latin hypercube, 1,200 continuous points, seed 1 (S.NO 10001+)'}
    _atomic(DIRECTORY/'study_meta.json',json.dumps(meta,indent=2))
    return rows,meta

def infill_cases(n=1200,seed=1):
    """Continuous Latin-hypercube points between the workbook grid lines. The workbook steps load by
    25 % (phi 0.2) - too coarse where NOx rises steeply - so the surrogate needs off-grid training data."""
    from scipy.stats import qmc
    sample = qmc.LatinHypercube(d=len(FEATURES),seed=seed).random(n)
    lo = [INPUTS[f][0] for f in FEATURES];hi = [INPUTS[f][1] for f in FEATURES]
    points = qmc.scale(sample,lo,hi)
    return [{'case_id':10001+i,'inputs':dict(zip(FEATURES,map(float,p)))} for i,p in enumerate(points)]

def fill_workbook(rows):
    """Copy the original workbook and write the nine simulated outputs into columns F-N."""
    from workbook_cases import load_cases
    _,workbook = load_cases()
    source = ROOT/'data'/'raw'/workbook['file_name']
    by_row = {r['S.NO']+1:r for r in rows}  # S.NO n sits on spreadsheet row n+1
    column = dict(zip('FGHIJKLMN',OUTPUTS))
    def put(match):
        col,row,style = match.group(1),int(match.group(2)),match.group(3)
        value = by_row.get(row,{}).get(column[col])
        if value is None:return match.group(0)
        return f'<c r="{col}{row}" s="{style}"><v>{float(value):.6g}</v></c>'
    target = DIRECTORY/'doe_filled.xlsx';tmp = target.with_suffix('.tmp')
    with zipfile.ZipFile(source) as zin,zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename=='xl/worksheets/sheet1.xml':
                data = re.sub(r'<c r="([F-N])(\d+)" s="(\d+)"/>',put,data.decode('utf-8')).encode('utf-8')
            zout.writestr(item,data)
    tmp.replace(target)

def load_rows(name='doe_results.csv'):
    path = DIRECTORY/name
    if not path.exists():return None
    with path.open(encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    return [{k:(None if v=='' else (int(v) if k=='S.NO' else float(v))) for k,v in r.items()} for r in rows]

def _atomic(path,text):
    tmp = path.with_suffix('.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(path)

# ---------------------------------------------------------------- desirability & ranking

def ranges(rows):
    out = {}
    for key,kind in OBJECTIVES.items():
        values = [r[key] for r in rows]
        if kind=='min_log':values = [math.log1p(max(v,0)) for v in values]
        out[key] = (min(values),max(values))
    return out

def desirability(row,rng,weights=None,knock_limit=DEFAULT_KNOCK_LIMIT):
    weights = weights or DEFAULT_WEIGHTS
    if row['Knock_PreIgnition_Tendency']>knock_limit or row['Brake_Power_kW']<=0:return 0.
    total = acc = 0.
    for key,kind in OBJECTIVES.items():
        w = weights.get(key,0)
        if w<=0:continue
        lo,hi = rng[key];v = math.log1p(max(row[key],0)) if kind=='min_log' else row[key]
        d = (v-lo)/(hi-lo) if hi>lo else 1.
        if kind!='max':d = 1-d
        acc += w*math.log(min(1.,max(d,1e-6)));total += w
    return math.exp(acc/total) if total else 0.

def rank(rows,weights=None,knock_limit=DEFAULT_KNOCK_LIMIT,rng=None):
    """Sort by desirability. Pass rng (from all 2,625 setups) when ranking a subset so scores stay comparable."""
    rng = rng or ranges(rows)
    scored = sorted(({**r,'desirability':desirability(r,rng,weights,knock_limit)} for r in rows),key=lambda r:-r['desirability'])
    return scored,rng

# ---------------------------------------------------------------- machine learning

class Surrogate:
    """Per-output best model (chosen by cross-validation). predict() returns the nine outputs in workbook units."""
    def __init__(self,models,choice,y_mean,y_std):
        self.models,self.choice,self.y_mean,self.y_std = models,choice,y_mean,y_std
    def _inverse(self,z):
        import numpy as np
        y = z*self.y_std+self.y_mean;y[:,OUTPUTS.index('NOx_Emissions_ppm')] = np.expm1(y[:,OUTPUTS.index('NOx_Emissions_ppm')])
        return y
    def predict_all(self,X):
        return {name:self._inverse(m.predict(X)) for name,m in self.models.items()}
    def predict(self,X):
        import numpy as np
        preds = self.predict_all(np.asarray(X,dtype=float))
        return np.column_stack([preds[self.choice[o]][:,j] for j,o in enumerate(OUTPUTS)])

def _transform(Y):
    import numpy as np
    Z = np.array(Y,dtype=float);j = OUTPUTS.index('NOx_Emissions_ppm');Z[:,j] = np.log1p(np.maximum(Z[:,j],0));return Z

def _model_zoo():
    from sklearn.ensemble import RandomForestRegressor,HistGradientBoostingRegressor
    from sklearn.multioutput import MultiOutputRegressor
    from sklearn.neural_network import MLPRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {'Random forest':RandomForestRegressor(n_estimators=300,min_samples_leaf=1,random_state=42,n_jobs=1),
            'Gradient boosting':MultiOutputRegressor(HistGradientBoostingRegressor(max_iter=300,learning_rate=.1,
                                max_leaf_nodes=31,l2_regularization=1e-3,random_state=42)),
            'ANN (MLP 64-64-64)':make_pipeline(StandardScaler(),MLPRegressor(hidden_layer_sizes=(64,64,64),activation='tanh',solver='lbfgs',
                                max_iter=1500,alpha=1e-4,random_state=42))}

def _fit(model,X,Z,X_eval):
    """Fit one model (run in a worker process, one BLAS thread each); return it and its predictions."""
    import warnings
    from sklearn.base import clone
    from threadpoolctl import threadpool_limits
    with threadpool_limits(1),warnings.catch_warnings():
        warnings.simplefilter('ignore')  # lbfgs iteration-limit notices; accuracy is reported instead
        m = clone(model).fit(X,Z)
    return m,m.predict(X_eval)

def _metrics(y,p):
    import numpy as np
    from sklearn.metrics import r2_score
    out = {o:{'r2':float(r2_score(y[:,j],p[:,j])),'rmse':float(np.sqrt(np.mean((y[:,j]-p[:,j])**2))),
              'mae':float(np.mean(np.abs(y[:,j]-p[:,j])))} for j,o in enumerate(OUTPUTS)}
    j = OUTPUTS.index('NOx_Emissions_ppm')  # NOx is ranked on a log scale, so it is also selected on one
    out['NOx_Emissions_ppm']['rmse_log1p'] = float(np.sqrt(np.mean((np.log1p(np.maximum(p[:,j],0))-np.log1p(y[:,j]))**2)))
    return out

def train(rows,infill,progress=lambda p,m:None):
    import numpy as np
    import joblib,sklearn
    from joblib import Parallel,delayed
    from sklearn.model_selection import KFold,train_test_split
    begin = time.perf_counter()
    data = rows+infill;grid = np.array([r['S.NO']<10001 for r in data])
    X = np.array([[r[f] for f in FEATURES] for r in data]);Y = np.array([[r[o] for o in OUTPUTS] for r in data])
    idx = np.arange(len(data));dev,test = train_test_split(idx,test_size=.2,random_state=42,stratify=grid)
    Z = _transform(Y);mean = Z[dev].mean(0);std = Z[dev].std(0);std[std==0] = 1
    Zs = (Z-mean)/std
    zoo = _model_zoo();probe = Surrogate({},{},mean,std)
    folds = list(KFold(5,shuffle=True,random_state=7).split(dev))
    sizes = (100,250,500,1000,len(dev))
    subsets = [np.random.default_rng(size).permutation(dev)[:size] for size in sizes]  # global target scaling reused
    # One parallel batch: 5 CV folds, the development-set refit and 5 learning-curve sizes, for all three models.
    jobs = [('cv',k,name,dev[a],dev[b]) for k,(a,b) in enumerate(folds) for name in zoo]
    jobs += [('fit',0,name,dev,test) for name in zoo]
    jobs += [('curve',k,name,sub,test) for k,sub in enumerate(subsets) for name in zoo]
    progress(62,f'Training {len(jobs)} models in parallel: 5-fold CV, refit and learning curve')
    done = Parallel(n_jobs=-1,backend='loky')(delayed(_fit)(zoo[name],X[a],Zs[a],X[b]) for _,_,name,a,b in jobs)
    cv = {name:np.zeros((len(dev),len(OUTPUTS))) for name in zoo};fitted = {};curve = [None]*len(sizes)
    position = {i:j for j,i in enumerate(dev)}
    for (kind,k,name,a,b),(model,pred) in zip(jobs,done):
        if kind=='cv':cv[name][[position[i] for i in b]] = probe._inverse(pred)
        elif kind=='fit':fitted[name] = model
        else:
            met = _metrics(Y[test],probe._inverse(pred))
            curve[k] = curve[k] or {'training_cases':int(sizes[k]),'share_of_doe_pct':sizes[k]/len(data)*100}
            curve[k][name] = {'mean_r2':float(np.mean([v['r2'] for v in met.values()])),
                'nox_r2':met['NOx_Emissions_ppm']['r2'],'bte_r2':met['Brake_Thermal_Efficiency_pct']['r2']}
    cv_metrics = {name:_metrics(Y[dev],p) for name,p in cv.items()}
    choice = {o:min(zoo,key=lambda n:cv_metrics[n][o].get('rmse_log1p',cv_metrics[n][o]['rmse'])) for o in OUTPUTS}
    surrogate = Surrogate(fitted,choice,mean,std)
    test_pred = surrogate.predict_all(X[test]);test_metrics = {n:_metrics(Y[test],p) for n,p in test_pred.items()}
    ensemble = surrogate.predict(X[test]);ensemble_metrics = _metrics(Y[test],ensemble)
    on_grid = grid[test]
    split_metrics = {'workbook_grid':_metrics(Y[test][on_grid],ensemble[on_grid]),
                     'off_grid_infill':_metrics(Y[test][~on_grid],ensemble[~on_grid])}
    # Permutation importance of each input on the selected per-output models.
    progress(95,'Permutation feature importance')
    rng = np.random.default_rng(0);importance = {o:{} for o in OUTPUTS}
    for f,feature in enumerate(FEATURES):
        drops = np.zeros(len(OUTPUTS))
        for _ in range(5):
            Xp = X[test].copy();Xp[:,f] = rng.permutation(Xp[:,f]);met = _metrics(Y[test],surrogate.predict(Xp))
            drops += [ensemble_metrics[o]['r2']-met[o]['r2'] for o in OUTPUTS]
        for j,o in enumerate(OUTPUTS):importance[o][feature] = float(drops[j]/5)
    parity = {'actual':Y[test][:300].tolist(),'predicted':ensemble[:300].tolist()}
    report = {'created_utc':datetime.now(timezone.utc).isoformat(),'source':'si_engine.py full-factorial DOE (simulated, not measured)',
        'features':FEATURES,'outputs':OUTPUTS,'split':{'development':len(dev),'test':len(test),'cv_folds':5,'seed':42,'stratified_by':'workbook grid vs infill'},
        'training_data':{'workbook_grid':int(grid.sum()),'off_grid_infill':int((~grid).sum())},
        'selection_rule':'Per output: lowest 5-fold cross-validated RMSE on the development set (NOx: RMSE of log(1+ppm), matching the log-scale ranking). The test set is never used for selection.',
        'choice':choice,'cv_metrics':cv_metrics,'test_metrics':test_metrics,'ensemble_test_metrics':ensemble_metrics,
        'ensemble_test_by_source':split_metrics,'test_counts':{'workbook_grid':int(on_grid.sum()),'off_grid_infill':int((~on_grid).sum())},
        'learning_curve':curve,'permutation_importance':importance,'parity_test':parity,
        'nox_target_transform':'log1p','versions':{'scikit_learn':sklearn.__version__,'numpy':np.__version__},
        'simulator_sha256':fingerprint(),'duration_seconds':time.perf_counter()-begin}
    joblib.dump({'surrogate':surrogate,'report':report},DIRECTORY/'surrogate.tmp');(DIRECTORY/'surrogate.tmp').replace(DIRECTORY/'surrogate.joblib')
    _atomic(DIRECTORY/'ml_report.json',json.dumps(report,indent=1))
    return surrogate,report

def load_surrogate():
    global _surrogate
    import joblib
    with _lock:
        if _surrogate is None:
            path = DIRECTORY/'surrogate.joblib'
            if not path.exists():raise ValueError('Run the DOE study first; no trained surrogate exists.')
            _surrogate = joblib.load(path)  # local artefact written by this application only
        bundle = _surrogate
    if bundle['report']['simulator_sha256']!=fingerprint():raise ValueError('The cycle model changed after training. Re-run the study.')
    return bundle

# ---------------------------------------------------------------- optimisation

def optimize(weights=None,knock_limit=DEFAULT_KNOCK_LIMIT,free=None,fixed=None,seed=0):
    """Maximise desirability on the ML surrogate (differential evolution, a genetic algorithm),
    then re-simulate the optimum with the physics model to validate the prediction."""
    import numpy as np
    from scipy.optimize import differential_evolution
    weights = {**DEFAULT_WEIGHTS,**(weights or {})};free = FEATURES if free is None else list(free);fixed = fixed or {}
    if not set(free)<=set(FEATURES) or not free:raise ValueError('Choose at least one input to optimise.')
    if set(fixed)-set(FEATURES):raise ValueError('Unknown fixed input.')
    if not isinstance(knock_limit,(int,float)) or not 0<knock_limit<=5:raise ValueError('Knock limit must be in (0, 5].')
    base = validate({k:v for k,v in fixed.items() if k not in free})
    rows = load_rows()
    if rows is None:raise ValueError('Run the DOE study first.')
    bundle = load_surrogate();model = bundle['surrogate'];rng = ranges(rows)
    def assemble(z):
        x = np.tile([base[f] for f in FEATURES],(z.shape[1],1)).astype(float)
        for j,f in enumerate(free):x[:,FEATURES.index(f)] = z[j]
        return x
    def score(x,pred):
        return np.array([desirability(dict(zip(OUTPUTS,p)),rng,weights,knock_limit) for p in pred])
    evaluations = [0]
    def objective(z):
        z = z[:,None] if z.ndim==1 else z
        x = assemble(z);evaluations[0] += len(x);return -score(x,model.predict(x))
    begin = time.perf_counter()
    res = differential_evolution(objective,[INPUTS[f] for f in free],seed=seed,popsize=25,maxiter=120,tol=1e-7,
                                 polish=False,vectorized=True,updating='deferred')
    x = assemble(np.array(res.x)[:,None])[0];inputs = dict(zip(FEATURES,map(float,x)))
    predicted = dict(zip(OUTPUTS,map(float,model.predict([x])[0])))
    actual = simulate(inputs)['summary']
    grid = [r for r in rows if all(math.isclose(r[k],base[k]) for k in FEATURES if k not in free)]
    best_grid,_ = rank(grid,weights,knock_limit,rng)
    return {'inputs':inputs,'free':free,'fixed':{k:base[k] for k in FEATURES if k not in free},'weights':weights,
        'knock_limit':knock_limit,'predicted':predicted,'simulated':{o:actual[o] for o in OUTPUTS},
        'desirability_predicted':float(-res.fun),'desirability_simulated':desirability(actual,rng,weights,knock_limit),
        'best_grid_setup':{k:best_grid[0][k] for k in ['S.NO',*FEATURES,*OUTPUTS,'desirability']} if best_grid else None,
        'generations':int(res.nit),'evaluations':evaluations[0],'seconds':time.perf_counter()-begin,
        'method':'Differential evolution (population 25 x free inputs) on the cross-validated ML surrogate; optimum re-simulated with si_engine.'}

# ---------------------------------------------------------------- background job for the web app

def status():
    with _lock:
        value = dict(_state)
    for name in ('study_meta.json','ml_report.json'):
        path = DIRECTORY/name
        if path.exists():value[name.split('.')[0]] = json.loads(path.read_text(encoding='utf-8'))
    if value['status']=='idle' and 'ml_report' in value:
        value.update(status='ready',progress=100,message='Saved study loaded.')
    if 'ml_report' in value:value['ml_report'] = {k:v for k,v in value['ml_report'].items() if k!='parity_test'}
    return value

def start():
    with _lock:
        if _state['status']=='running':raise ValueError('The study is already running.')
        _state.update(status='running',progress=0,message='Starting the full-factorial DOE.')
    threading.Thread(target=_job,daemon=True).start()
    return status()

def _progress(p,m):
    with _lock:_state.update(progress=p,message=m)

def _job():
    global _surrogate
    try:
        rows,meta = run_doe(_progress)
        _,report = train(rows,load_rows('infill_results.csv'),_progress)
        with _lock:
            _surrogate = None
            _state.update(status='ready',progress=100,message=f"Study complete: {meta['cases']:,} setups simulated in {meta['simulation_seconds']:.0f} s; surrogate trained.")
    except Exception as error:
        with _lock:_state.update(status='failed',progress=0,message=f'Study failed: {error}')

def results_json():
    rows = load_rows()
    if rows is None:raise ValueError('Run the DOE study first.')
    columns = list(rows[0])
    return {'columns':columns,'rows':[[r[c] for c in columns] for r in rows],'objectives':OBJECTIVES,
            'default_weights':DEFAULT_WEIGHTS,'default_knock_limit':DEFAULT_KNOCK_LIMIT}

def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command',choices=['run','optimize','graphs'])
    parser.add_argument('--top',type=int,default=25);parser.add_argument('--all',action='store_true')
    parser.add_argument('--knock-limit',type=float,default=DEFAULT_KNOCK_LIMIT)
    args = parser.parse_args()
    show = lambda p,m:print(f'[{p:3d}%] {m}',flush=True)
    if args.command=='run':
        rows,meta = run_doe(show);print(f"{meta['cases']} setups in {meta['simulation_seconds']:.1f} s")
        _,report = train(rows,load_rows('infill_results.csv'),show)
        for o in OUTPUTS:
            g,f = (report['ensemble_test_by_source'][k][o]['r2'] for k in ('workbook_grid','off_grid_infill'))
            print(f"{o:32s} {report['choice'][o]:20s} test R2 grid {g:.4f} | off-grid {f:.4f}")
    elif args.command=='optimize':
        print(json.dumps(optimize(knock_limit=args.knock_limit),indent=2))
    else:
        from graph_pack import export
        rows = load_rows()
        if rows is None:raise SystemExit('Run the study first.')
        ranked,_ = rank(rows,knock_limit=args.knock_limit)
        chosen = ranked if args.all else ranked[:args.top]
        folder = export(chosen,ROOT/'results'/'graphs',show)
        print(f'{len(chosen)} setups x 13 graphs written to {folder}')

if __name__=='__main__':
    import doe_study  # run through the importable module so the saved surrogate unpickles anywhere
    doe_study.main()
