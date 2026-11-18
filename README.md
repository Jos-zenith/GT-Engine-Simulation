PERFORMANCE PREDICTION AND OPTIMIZATION OF A DIRECT-INJECTION HYDROGEN ENGINE
USING ENGINE SIMULATION AND MACHINE LEARNING

WHAT THE PROJECT DOES
The project finds the best-performing operating setup of a 4-cylinder spark-ignited
direct-injection (DI) hydrogen engine from a full-factorial design of experiments, and
shows that a machine-learning surrogate can predict and optimise it in milliseconds.

  Design space (workbook "2635 boundary conditions.xlsx", Full_Factorial_DOE sheet)
    Compression ratio          10.5 - 12.5        (5 levels)
    H2 injection timing (SOI)  60 - 160 deg BTDC  (21 levels)
    Engine load                0 - 100 %          (5 levels; quality control, phi 0.2 - 1.0)
    Engine speed               2500 - 4500 rpm    (5 levels)
    = 2,625 setups

  Outputs per setup (the workbook's nine target columns)
    Brake power, torque, brake thermal efficiency, fuel consumption, peak cylinder
    pressure, peak cylinder temperature, combustion duration (10-90 %),
    knock/pre-ignition tendency (0-5) and NOx.

  Graphs per setup (13)
    Cylinder pressure (firing and motored), log P-log V, mean gas temperature, burned
    and unburned zone temperatures, heat release rate, mass fraction burned, H2
    injection rate, mixture homogeneity, wall heat transfer, cumulative work, thermal
    NO formation, knock integral and energy balance. They are generated on demand in
    the app and can be exported as PNG sheets.

PIPELINE
  1. Chemistry (Cantera 3.2): laminar flame speeds (h2o2 FreeFlame), ignition delays
     (h2o2 constant-pressure reactor), adiabatic flame temperature, burned-gas
     equilibrium and dissociation energy (gri30), and species energies. These are
     tabulated once in data/chemistry/h2_tables.json.
  2. Cycle model (si_engine.py): a 0D spark-ignited DI hydrogen cycle.
       - Engine: the review-deck specification, 86 x 86 mm, 1996 cc.
       - Injection: choked gaseous injection at 100 bar. Any fuel injected before
         intake valve closing displaces air.
       - Mixing: parcel-based mixing sets mixture homogeneity, combustion efficiency
         and the local-phi spread.
       - Spark: placed for CA50 = 8 deg ATDC (MBT rule).
       - Combustion: a Wiebe burn whose duration comes from the turbulent flame speed.
       - Heat transfer and losses: Woschni wall heat transfer and Chen-Flynn friction.
       - NOx: extended-Zeldovich thermal NO in 24 burned-gas elements.
       - Knock: a Livengood-Wu end-gas knock integral.
     One setup takes about 0.15 s.
  3. Full DOE (doe_study.py): all 2,625 setups are simulated in parallel. The original
     workbook is returned with its nine output columns filled (doe_filled.xlsx).
     1,200 extra Latin-hypercube points between the grid lines are simulated for ML
     training only, because the workbook's 25 % load step is too coarse where NOx
     rises steeply.
  4. Machine learning: Random Forest, Gradient Boosting and an ANN (MLP 64-64-64) are
     compared by 5-fold cross-validation. The best model is chosen separately for each
     output, and accuracy is reported on a held-out test set for both on-grid and
     off-grid points. The study also produces a learning curve (how many simulations
     the surrogate really needs) and permutation feature importance.
  5. Ranking: each setup gets a Derringer-Suich desirability score D. It is high when
     brake thermal efficiency and brake power are high and NOx and knock are low.
     Setups above a knock limit or with no positive brake power score 0. The study
     also finds the Pareto front (efficiency vs NOx vs power) and the best injection
     timing for every speed and load.
  6. Optimisation: differential evolution (a genetic algorithm) searches the
     continuous input space on the ML surrogate. Every optimum is then re-simulated
     with the physics model, and the app reports the ML-vs-physics error and compares
     the result with the best grid setup.

RESULTS (saved study, default weights, knock limit 2.5)
  Simulation  2,625 setups + 1,200 infill points in about 160 s on 12 cores; worst
              relative energy residual 9e-7.
  Feasible    2,473 of 2,625 setups. The remaining 152 exceed the knock limit or give
              no positive brake power.
  Best setup  #259: CR 10.5, SOI 110 deg BTDC, load 25 % (phi 0.4), 4,000 rpm.
              29.2 kW, 69.8 N.m, BTE 28.8 %, NOx 3.5 ppm, knock 0.06 of 5, D = 0.639.
              The top 15 are all lean (phi 0.4) at 4,000 rpm with SOI 100-120 deg BTDC.
  Peak BTE    37.1 % (CR 12.5, SOI 60 deg, load 75 % / phi 0.8, 2,500 rpm), but with
              8,650 ppm NOx. The Pareto front holds 285 setups; the efficiency-NOx
              trade-off is the central result.
  SOI trend   The best SOI moves later as load rises (105-130 deg BTDC at phi 0.2,
              60-65 deg at phi 1.0) and, at light load, earlier as speed rises. This
              matches the dependence reported by Rahman et al. (2011).
  ML          The ANN is selected for all nine outputs. Held-out test R2 is
              0.998-0.99995 on grid points and 0.996-0.9999 off grid. Trained on only
              100 simulations (about 4 % of the DOE), the ANN still reaches a mean test
              R2 of 0.991.
  GA optimum  CR 10.5, SOI 112 deg, load 27.4 %, 4,078 rpm. D predicted 0.6406,
              re-simulated 0.6401. BTE 29.16 vs 29.15 %, power 31.5 vs 31.5 kW and NOx
              6.7 vs 6.8 ppm (ML vs physics). It edges out the best grid setup
              (D 0.639).
  Model check Selecting a tree model for NOx made the GA exploit a step at a grid
              midpoint: it predicted 3.5 ppm where the physics gave 90 ppm. Off-grid
              infill and log-scale model selection fixed this. Always re-simulate an
              ML optimum.

USING THE APPLICATION (http://127.0.0.1:8000)
  Single cycle            The original Cantera timed-injection autoignition cylinder,
                          with the 3D engine view and the first ML lab.
  DOE study & optimisation  Section by section:
                            01 run the full study and download the CSV, the filled
                               XLSX and the ML report;
                            02 adjust the objective weights, knock limit and filters
                               to re-rank the setups;
                            03 open the 13-graph pack for any setup;
                            04 Pareto front, injection-timing map and main effects;
                            05 model comparison, learning curve, parity plot and
                               feature importance;
                            06 GA optimisation with physics validation.

  Command line
    python doe_study.py run               full DOE + ML training (about 5 min, 12 cores)
    python doe_study.py optimize          GA optimum + physics check
    python doe_study.py graphs --top 25   13-graph PNG sheets for the top-ranked setups
                                          (--all for every setup: 2,625 sheets)
    python si_engine.py --inputs case.json one setup's nine outputs
    python chemistry_tables.py            rebuild the Cantera tables (about 30 s)

FILES
  si_engine.py          spark-ignited DI cycle model (nine workbook outputs + 13 traces)
  chemistry_tables.py   Cantera table builder
  doe_study.py          DOE runner, workbook filler, ML comparison, ranking, GA optimiser
  graph_pack.py         13-graph definitions and PNG export
  cantera_injection.py  layer 1: four-input Cantera injection/autoignition cylinder
  surrogate_service.py  layer 1: ML lab for the Cantera cylinder
  phase1_server.py      local web server and API
  workbook_cases.py     reads the four input columns from the workbook
  data/raw/             the supplied DOE workbook
  data/chemistry/       Cantera tables
  data/study/           DOE results, filled workbook, surrogate and ML report
  frontend/             web pages (simulation.html, study.html)

