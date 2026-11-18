"""ML surrogate of the local thermodynamic model, never experimental engine data."""
import csv
import hashlib
import inspect
import io
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FEATURES = {'Compression_Ratio':(10.5,12.5),'Hydrogen_Injection_Timing_deg_BTDC':(60,160),
            'Engine_Load_pct':(0,100),'Engine_Speed_rpm':(2500,4500)}
TARGETS = ['peak_pressure_bar','peak_temperature_k','gross_imep_bar',
           'gross_indicated_power_kw','fuel_consumption_kg_per_hr']
DIRECTORY = Path(__file__).resolve().parent.parent/'data'/'cantera_injection_ml'
_lock=threading.Lock()
_state={'status':'idle','progress':0,'message':'Generate simulation data and train a model.'}
_bundle=None

def status():
    with _lock:
        value=dict(_state)
        if (DIRECTORY/'report.json').exists():
            value['report']=json.loads((DIRECTORY/'report.json').read_text())
            if value['status']=='idle':value.update(status='ready',progress=100,message='Saved model loaded. Ready to predict.')
        return value

def start(inputs, samples=1800):
    from cantera_injection import simulate,DEFAULTS,validate
    if not isinstance(samples,int) or isinstance(samples,bool) or not 400<=samples<=2625:
        raise ValueError('Training size must be an integer between 400 and 2625.')
    validate(inputs)  # Validate without running a whole cycle.
    base=DEFAULTS|inputs
    with _lock:
        if _state['status']=='running': raise ValueError('A training job is already running.')
        _state.update(status='running',progress=0,message='Preparing deterministic simulation design.')
    threading.Thread(target=_train,args=(base,samples),daemon=True).start()
    return status()

def _progress(value,message):
    with _lock:_state.update(progress=value,message=message)

def _train(base,samples):
    global _bundle
    try:
        import numpy as np
        import sklearn
        import joblib
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import PolynomialFeatures,StandardScaler
        from sklearn.linear_model import Ridge
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.metrics import mean_absolute_error,mean_squared_error,r2_score
        from cantera_injection import simulate,fingerprint
        begin=time.perf_counter()
        rng=np.random.default_rng(42)
        from workbook_cases import load_cases
        from cantera_injection import FIXED_ENGINE
        cases,workbook=load_cases()
        if samples>len(cases):raise ValueError('Requested more cases than the workbook contains.')
        selected=rng.permutation(len(cases))[:samples]
        selected_cases=[cases[int(i)] for i in selected]
        x=np.array([[case['inputs'][key] for key in FEATURES] for case in selected_cases])
        y=[];combustion_statuses=[]
        for i,case in enumerate(selected_cases):
            result=simulate(case['inputs'])
            summary=result['summary']
            if result['numerical_checks']['energy_residual_relative']>.01:
                raise ValueError(f"Numerical energy check failed for workbook case {case['case_id']}.")
            y.append([summary[k] for k in TARGETS]);combustion_statuses.append(result['combustion_status'])
            if i%5==0:_progress(round(i/samples*65),f'Cantera workbook cases: {i}/{samples}')
        y=np.array(y)
        indices=np.arange(samples)
        development,test=train_test_split(indices,test_size=.2,random_state=42)
        train,validation=train_test_split(development,test_size=.25,random_state=43)
        split={int(i):label for label,group in [('train',train),('validation',validation),('test',test)] for i in group}
        models={'Linear ridge':make_pipeline(StandardScaler(),Ridge(alpha=1)),
                'Polynomial ridge (degree 3)':make_pipeline(StandardScaler(),PolynomialFeatures(3,include_bias=False),StandardScaler(),Ridge(alpha=.001)),
                'Random forest':RandomForestRegressor(n_estimators=160,min_samples_leaf=1,random_state=42,n_jobs=2)}
        records=[]
        scale=np.std(y[train],axis=0)
        for j,(name,model) in enumerate(models.items()):
            _progress(68+j*8,f'Training and validating {name}')
            model.fit(x[train],y[train])
            pred=model.predict(x[validation])
            score=float(np.mean(np.sqrt(np.mean((pred-y[validation])**2,axis=0))/scale))
            records.append({'name':name,'validation_normalized_rmse':score})
        chosen=min(records,key=lambda v:v['validation_normalized_rmse'])['name']
        # Choose using validation only. Test set does not select the model or tune it.
        per_model={}
        predictions={}
        for name,model in models.items():
            pred=model.predict(x[test]);predictions[name]=pred
            per_model[name]={k:{'mae':float(mean_absolute_error(y[test,j],pred[:,j])),
                'rmse':float(np.sqrt(mean_squared_error(y[test,j],pred[:,j]))),
                'r2':float(r2_score(y[test,j],pred[:,j]))} for j,k in enumerate(TARGETS)}
        source_hash=fingerprint()
        out=io.StringIO(newline='');writer=csv.writer(out)
        writer.writerow([*FEATURES,*TARGETS,'S.NO','split','source','combustion_status'])
        for i in range(samples):writer.writerow([*x[i],*y[i],selected_cases[i]['case_id'],split[i],'cantera_timed_hydrogen_injection',combustion_statuses[i]])
        dataset=out.getvalue()
        report={'title':'Performance Simulation and Prediction of a Hydrogen Engine Using Thermodynamic Modelling and Machine Learning',
            'source':'Operating inputs from the supplied workbook; outputs from Cantera timed hydrogen injection with homogeneous mixing and provisional autoignition. Not experimental measurements.',
            'created_utc':datetime.now(timezone.utc).isoformat(),'seed':42,'sample_count':samples,
            'splits':{'train':len(train),'validation':len(validation),'test':len(test)},
            'feature_ranges':FEATURES,'targets':TARGETS,
            'fixed_inputs':{},'fixed_engine':FIXED_ENGINE,'workbook':workbook,
            'reaction_counts':{key:combustion_statuses.count(key) for key in sorted(set(combustion_statuses))},
            'selection_rule':'Lowest validation RMSE normalized by training-target standard deviation; no refit on test data.',
            'selected_model':chosen,'leaderboard':records,'test_metrics':per_model,
            'test_examples':[{'actual':dict(zip(TARGETS,map(float,y[idx]))),
                'predicted':dict(zip(TARGETS,map(float,predictions[chosen][j])))} for j,idx in enumerate(test[:100])],
            'duration_seconds':time.perf_counter()-begin,'simulator_sha256':source_hash,
            'dataset_sha256':hashlib.sha256(dataset.encode()).hexdigest(),
            'versions':{'scikit_learn':sklearn.__version__,'numpy':np.__version__,'cantera':__import__('cantera').__version__},
            'mechanism':'h2o2.yaml','ignition_model':'provisional autoignition; gaseous injection pulse with homogeneous mixing; no spark or resolved jet',
            'limitations':['Metrics measure agreement with the simulator, not physical engine accuracy.',
                'Predictions allowed only within the workbook parameter bounds; engine assumptions are fixed.',
                'Inside-box checks do not guarantee accuracy or dense local coverage.',
                'No brake power, emissions, knock or resolved injection mixing prediction. Fuel setting is not rated brake load.']}
        bundle={'model':models[chosen],'report':report}
        DIRECTORY.mkdir(parents=True,exist_ok=True)
        # Publish a single complete bundle first; status readers never load partial model files.
        joblib.dump(bundle,DIRECTORY/'model.tmp')
        (DIRECTORY/'model.tmp').replace(DIRECTORY/'model.joblib')
        (DIRECTORY/'dataset.tmp').write_text(dataset,encoding='utf-8')
        (DIRECTORY/'dataset.tmp').replace(DIRECTORY/'dataset.csv')
        (DIRECTORY/'report.tmp').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
        with _lock:
            (DIRECTORY/'report.tmp').replace(DIRECTORY/'report.json')
            _bundle=bundle
            _state.update(status='ready',progress=100,message='Training complete. Held-out test results are available.')
    except Exception as error:
        with _lock:_state.update(status='failed',message=f'Training failed: {error}',progress=0)

def predict(inputs):
    global _bundle
    import joblib
    from cantera_injection import simulate,DEFAULTS,BOUNDS,fingerprint
    if not isinstance(inputs,dict) or set(inputs)-set(DEFAULTS):raise ValueError('Invalid prediction inputs.')
    c=DEFAULTS|inputs
    import math
    for k,(lo,hi) in BOUNDS.items():
        v=c[k]
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(f'{k} must be between {lo} and {hi}.')
    with _lock:
        if _bundle is None:
            if not (DIRECTORY/'model.joblib').exists():raise ValueError('Train the model first.')
            _bundle=joblib.load(DIRECTORY/'model.joblib')  # Only this application's fixed local artifact.
        bundle=_bundle
    report=bundle['report']
    if report['simulator_sha256']!=fingerprint():
        raise ValueError('Simulator changed. Retrain before predicting.')
    for k,v in report['fixed_inputs'].items():
        if not math.isclose(c[k],v,rel_tol=0,abs_tol=1e-9):raise ValueError(f'{k} differs from the trained configuration. Restore it or retrain.')
    for k,(lo,hi) in report['feature_ranges'].items():
        if not lo<=c[k]<=hi:raise ValueError(f'Outside training domain: {k} must be {lo}–{hi}.')
    t=time.perf_counter()
    values=bundle['model'].predict([[c[k] for k in FEATURES]])[0]
    prediction_ms=(time.perf_counter()-t)*1000
    t=time.perf_counter();actual=simulate(c)['summary'];simulation_ms=(time.perf_counter()-t)*1000
    return {'model':report['selected_model'],'source':report['source'],'inputs':c,
            'prediction':dict(zip(TARGETS,map(float,values))),
            'simulation':{k:actual[k] for k in TARGETS},'prediction_ms':prediction_ms,'simulation_ms':simulation_ms,
            'domain_status':'Within sampled parameter box; fixed inputs match. Accuracy is not guaranteed.',
            'test_metrics':report['test_metrics'][report['selected_model']]}
