"""Local thermodynamic simulation and ML application. Run: python phase1_server.py"""
import json
import math
import os
import socket
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = dict(rpm=3000, compression_ratio=10.5, load=75, bore_mm=100,
                stroke_mm=100, conrod_mm=220, intake_temperature_k=330,
                intake_pressure_bar=1.0, combustion_start_deg=-10,
                burn_duration_deg=45, heat_transfer_coefficient=150)
BOUNDS = dict(rpm=(500,6000), compression_ratio=(6,16), load=(0,100),
              bore_mm=(50,150), stroke_mm=(50,150), conrod_mm=(100,400),
              intake_temperature_k=(280,400), intake_pressure_bar=(0.5,2),
              combustion_start_deg=(-40,10), burn_duration_deg=(15,100),
              heat_transfer_coefficient=(0,500))

def simulate(inputs):
    if not isinstance(inputs, dict) or set(inputs)-set(DEFAULTS):
        raise ValueError('Unknown parameters or invalid input object.')
    c = DEFAULTS | inputs
    for k,(lo,hi) in BOUNDS.items():
        if isinstance(c[k],bool) or not isinstance(c[k],(float,int)) or not math.isfinite(c[k]) or not lo <= c[k] <= hi:
            raise ValueError(f'{k} must be between {lo} and {hi}.')
    b,s,l = c['bore_mm']/1000,c['stroke_mm']/1000,c['conrod_mm']/1000
    r=s/2
    if l <= r:
        raise ValueError('Connecting rod must exceed crank radius.')
    area=math.pi*b*b/4
    vd=area*s
    vc=vd/(c['compression_ratio']-1)
    gamma,R=1.35,287.05
    cv=R/(gamma-1)
    mass=c['intake_pressure_bar']*1e5*(vd+vc)/(R*c['intake_temperature_k'])
    phi=0.9*c['load']/100
    fuel=mass*phi/34.3
    heat=fuel*120e6*0.98
    wall=450
    def volume(a):
        t=math.radians(a)
        return vc+area*(r*(1-math.cos(t))+l-math.sqrt(l*l-r*r*math.sin(t)**2))
    def burn(a):
        x=max(0,min(1,(a-c['combustion_start_deg'])/c['burn_duration_deg']))
        return (1-math.exp(-6.9*x**3))/(1-math.exp(-6.9))
    u=mass*cv*c['intake_temperature_k']
    work=loss=0.0
    trace=[]
    step=0.25
    for i in range(1441):
        a=-180+i*step
        v=volume(a)
        temp=u/(mass*cv)
        pressure=mass*R*temp/v
        trace.append(dict(crank_angle_deg=a,volume_cm3=v*1e6,pressure_bar=pressure/1e5,
                          temperature_k=temp,mass_fraction_burned=burn(a)))
        if i==1440: break
        v1=volume(a+step)
        dv=v1-v
        dq=heat*(burn(a+step)-burn(a))
        dt=step/(6*c['rpm'])
        surface=2*area+math.pi*b*max(0,(v-vc)/area)
        hdt=c['heat_transfer_coefficient']*surface*dt/(mass*cv)
        # Implicit midpoint energy balance: stable, and preserves energy bookkeeping.
        k=(gamma-1)*dv/((v+v1)/2)
        u1=(u*(1-k/2-hdt/2)+dq+hdt*mass*cv*wall)/(1+k/2+hdt/2)
        dw=k*(u+u1)/2
        qwall=hdt*((u+u1)/2-mass*cv*wall)
        work+=dw
        loss+=qwall
        u=u1
    peak=max(trace,key=lambda p:p['pressure_bar'])
    return dict(model='Approximate 0D closed-cylinder thermodynamic model',inputs=c,
        assumptions=['Constant gas properties: gamma=1.35, R=287.05 J/(kg K).',
        'Fixed trapped air mass; load maps to equivalence ratio 0–0.9.',
        'Prescribed Wiebe heat release, 98% combustion efficiency, wall at 450 K.',
        'Compression/expansion only. No gas exchange, friction, injection mixing, NOx or knock model.',
        'Four identical cylinders; gross indicated power excludes pumping and friction.'],
        summary=dict(peak_pressure_bar=peak['pressure_bar'],peak_pressure_angle_deg=peak['crank_angle_deg'],
            peak_temperature_k=max(p['temperature_k'] for p in trace),gross_imep_bar=work/vd/1e5,
            gross_indicated_power_kw=work*c['rpm']/120*4/1000,
            gross_indicated_efficiency_pct=work/(fuel*120e6)*100 if fuel else None,
            fuel_mg_per_cylinder_cycle=fuel*1e6,work_j_per_cylinder=work,
            wall_heat_loss_j=loss,heat_release_j=heat,
            energy_residual_j=heat-loss-work-(u-mass*cv*c['intake_temperature_k'])),trace=trace)

class Handler(SimpleHTTPRequestHandler):
    def send_json(self, result, status=200):
        body=json.dumps(result,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(body)

    def send_file(self,path,content_type,name):
        if not path.exists():self.send_error(404,'Run the DOE study first.');return
        body=path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Disposition',f'attachment; filename={name}')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

    STUDY_FILES={'/api/study/doe_results.csv':('doe_results.csv','text/csv; charset=utf-8','h2-di-doe-results.csv'),
        '/api/study/doe_filled.xlsx':('doe_filled.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','2625-boundary-conditions-filled.xlsx'),
        '/api/study/ml_report.json':('ml_report.json','application/json','h2-di-ml-report.json')}

    def do_GET(self):
        if self.path in self.STUDY_FILES:
            from doe_study import DIRECTORY
            name,kind,download=self.STUDY_FILES[self.path]
            self.send_file(DIRECTORY/name,kind,download);return
        if self.path in ('/api/study/status','/api/study/results'):
            try:
                import doe_study
                self.send_json(doe_study.status() if self.path.endswith('status') else doe_study.results_json())
            except ValueError as e:self.send_json({'error':str(e)},400)
            return
        if self.path=='/api/ml/status':
            from surrogate_service import status
            self.send_json(status()); return
        if self.path=='/api/ml/dataset.csv':
            from surrogate_service import DIRECTORY
            path=DIRECTORY/'dataset.csv'
            if not path.exists():self.send_error(404,'Generate a training dataset first.'); return
            body=path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/csv; charset=utf-8')
            self.send_header('Content-Disposition','attachment; filename=thermodynamic-training-data.csv')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body);return
        if self.path=='/': self.path='/simulation.html'
        if self.path=='/study': self.path='/study.html'
        super().do_GET()
    def do_POST(self):
        if self.path not in ('/api/simulation','/api/ml/train','/api/ml/predict','/api/study/run','/api/study/case','/api/study/optimize'):
            self.send_error(404); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=10000: raise ValueError('Invalid request size.')
            payload=json.loads(self.rfile.read(length))
            if self.path=='/api/study/run':
                from doe_study import start
                result=start()
            elif self.path=='/api/study/case':
                from si_engine import simulate as simulate_si
                result=simulate_si(payload,traces=True)
            elif self.path=='/api/study/optimize':
                from doe_study import optimize
                if not isinstance(payload,dict) or set(payload)-{'weights','knock_limit','free','fixed'}:raise ValueError('Invalid optimisation request.')
                weights=payload.get('weights') or {}
                if not isinstance(weights,dict) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=5 for v in weights.values()):raise ValueError('Weights must be numbers from 0 to 5.')
                result=optimize(weights=weights,knock_limit=payload.get('knock_limit',2.5),free=payload.get('free'),fixed=payload.get('fixed') or {})
            elif self.path=='/api/simulation':
                from cantera_injection import simulate as simulate_cantera
                result=simulate_cantera(payload)
            elif self.path=='/api/ml/train':
                from surrogate_service import start
                if not isinstance(payload,dict) or set(payload)-{'inputs','samples'}:raise ValueError('Invalid training request.')
                result=start(payload.get('inputs',{}),payload.get('samples',1800))
            else:
                from surrogate_service import predict
                result=predict(payload)
            status=200
        except (ValueError,TypeError) as e:
            result={'error':str(e)}; status=400
        except ImportError:
            result={'error':'Cantera/ML dependencies are missing. Launch with START_SIMULATION.bat or install requirements-ml.txt in your Python environment.'};status=503
        self.send_json(result,status)

class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can allow two app instances to answer on the same port.
    allow_reuse_address=False
    def server_bind(self):
        if os.name=='nt':self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

if __name__=='__main__':
    server=LocalServer(('127.0.0.1',8000),partial(Handler,directory=str(ROOT/'app'/'frontend')))
    print('Hydrogen simulation: http://127.0.0.1:8000',flush=True)
    server.serve_forever()
