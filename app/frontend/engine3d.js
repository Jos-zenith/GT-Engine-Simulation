/**
 * Interactive 3D Kinematic Engine Assembly Visualizer
 *
 * Implements a four-cylinder direct-injection hydrogen engine using Three.js.
 * Kinematics scaled to Rahman et al. (2009) specifications:
 * - Bore: 100 mm (radius: 50 mm)
 * - Stroke: 100 mm (crank radius: 50 mm)
 * - Connecting Rod: 220 mm
 * - Firing Order: 1-3-4-2 (Cylinders 1 & 4 in phase, 2 & 3 at 180 deg)
 *
 * Notice: Kinematically scaled illustrative 3D visualization, not verified production CAD.
 */

class Engine3DVisualizer {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;

    // Simulation & Kinematic constants (in scaled units, 1 unit = 100 mm)
    this.bore = 1.0;            // 100 mm
    this.stroke = 1.0;          // 100 mm
    this.crankRadius = 0.5;     // 50 mm
    this.conrodLength = 2.2;    // 220 mm
    this.cylinderPitch = 1.4;   // 140 mm spacing between cylinder centers
    this.numCylinders = 4;

    this.crankAngleDeg = 0.0;
    this.rpm = 2500.0;
    this.isPlaying = true;
    this.isCutaway = true;

    // Three.js scene objects
    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.controls = null;

    // Components
    this.pistons = [];
    this.conrods = [];
    this.crankshaft = null;
    this.cylinders = [];
    this.valves = [];
    this.injectors = [];
    this.flames = [];

    this.lastTime = performance.now();
    this.init();
  }

  init() {
    const width = this.container.clientWidth || 800;
    const height = this.container.clientHeight || 500;

    // Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0a0e17);

    // Camera
    this.camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    this.camera.position.set(4.5, 3.2, 5.0);

    // Renderer
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.appendChild(this.renderer.domElement);

    // Controls
    if (typeof THREE.OrbitControls !== "undefined") {
      this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.05;
      this.controls.target.set(0, 1.2, 0);
    }

    // Lights
    this.setupLighting();

    // Build Engine Assembly
    this.buildEngineAssembly();

    // Handle Window Resize
    window.addEventListener("resize", () => this.onWindowResize());

    // Start Animation Loop
    this.animate();
  }

  setupLighting() {
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    this.scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0x38bdf8, 1.2);
    dirLight1.position.set(5, 10, 7);
    this.scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0x8b5cf6, 0.8);
    dirLight2.position.set(-5, -5, -5);
    this.scene.add(dirLight2);

    const pointLight = new THREE.PointLight(0x38bdf8, 1.5, 10);
    pointLight.position.set(0, 3, 0);
    this.scene.add(pointLight);
  }

  buildEngineAssembly() {
    // Shared Materials
    const metalMaterial = new THREE.MeshStandardMaterial({
      color: 0xcfd8dc,
      metalness: 0.85,
      roughness: 0.25
    });

    const steelDark = new THREE.MeshStandardMaterial({
      color: 0x455a64,
      metalness: 0.9,
      roughness: 0.3
    });

    const bronzeMaterial = new THREE.MeshStandardMaterial({
      color: 0xd97706,
      metalness: 0.8,
      roughness: 0.35
    });

    const glassLinerMaterial = new THREE.MeshPhysicalMaterial({
      color: 0x38bdf8,
      metalness: 0.1,
      roughness: 0.1,
      transmission: 0.75,
      transparent: true,
      opacity: 0.45,
      side: THREE.DoubleSide
    });

    const cylinderHeadMaterial = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      metalness: 0.5,
      roughness: 0.5,
      wireframe: false
    });

    // Engine Block / Bedplate Base
    const baseGeo = new THREE.BoxGeometry(this.numCylinders * this.cylinderPitch + 0.6, 0.4, 2.2);
    const baseMesh = new THREE.Mesh(baseGeo, steelDark);
    baseMesh.position.set(0, -0.6, 0);
    this.scene.add(baseMesh);

    // Grid Floor
    const gridHelper = new THREE.GridHelper(10, 20, 0x38bdf8, 0x1f2937);
    gridHelper.position.y = -0.8;
    this.scene.add(gridHelper);

    // Build Crankshaft Main Axis
    const crankGroup = new THREE.Group();
    const crankShaftGeo = new THREE.CylinderGeometry(0.12, 0.12, this.numCylinders * this.cylinderPitch + 0.4, 32);
    crankShaftGeo.rotateZ(Math.PI / 2);
    const mainShaft = new THREE.Mesh(crankShaftGeo, steelDark);
    crankGroup.add(mainShaft);
    this.crankshaft = crankGroup;
    this.scene.add(this.crankshaft);

    // Build 4 Cylinders
    const startX = -((this.numCylinders - 1) * this.cylinderPitch) / 2;

    for (let i = 0; i < this.numCylinders; i++) {
      const posX = startX + i * this.cylinderPitch;

      // 1. Cylinder Liner (Transparent Glass/Alloy)
      const linerGeo = new THREE.CylinderGeometry(this.bore / 2, this.bore / 2, 2.8, 32, 1, true);
      const linerMesh = new THREE.Mesh(linerGeo, glassLinerMaterial);
      linerMesh.position.set(posX, 2.0, 0);
      this.scene.add(linerMesh);
      this.cylinders.push(linerMesh);

      // 2. Cylinder Head Cap
      const headGeo = new THREE.CylinderGeometry(this.bore / 2 + 0.08, this.bore / 2 + 0.08, 0.3, 32);
      const headMesh = new THREE.Mesh(headGeo, cylinderHeadMaterial);
      headMesh.position.set(posX, 3.4, 0);
      this.scene.add(headMesh);

      // 3. Direct Hydrogen Injector (Center of head)
      const injGeo = new THREE.CylinderGeometry(0.04, 0.02, 0.4, 16);
      const injMesh = new THREE.Mesh(injGeo, bronzeMaterial);
      injMesh.position.set(posX, 3.5, 0);
      this.scene.add(injMesh);

      // Injector Spray Cone (semi-transparent cyan plume)
      const sprayGeo = new THREE.ConeGeometry(0.25, 0.6, 16, 1, true);
      sprayGeo.rotateX(Math.PI);
      const sprayMat = new THREE.MeshBasicMaterial({
        color: 0x38bdf8,
        transparent: true,
        opacity: 0.0,
        side: THREE.DoubleSide
      });
      const sprayMesh = new THREE.Mesh(sprayGeo, sprayMat);
      sprayMesh.position.set(posX, 3.0, 0);
      this.scene.add(sprayMesh);
      this.injectors.push(sprayMesh);

      // Combustion Flame Glow (Sphere expanding during ignition)
      const flameGeo = new THREE.SphereGeometry(0.35, 16, 16);
      const flameMat = new THREE.MeshBasicMaterial({
        color: 0xf59e0b,
        transparent: true,
        opacity: 0.0
      });
      const flameMesh = new THREE.Mesh(flameGeo, flameMat);
      flameMesh.position.set(posX, 3.1, 0);
      this.scene.add(flameMesh);
      this.flames.push(flameMesh);

      // 4. Valves (2 Intake, 2 Exhaust per cylinder)
      const cylValves = [];
      const valveOffsets = [-0.18, 0.18];
      valveOffsets.forEach((zOff, vIdx) => {
        const vStemGeo = new THREE.CylinderGeometry(0.02, 0.02, 0.6, 12);
        const vHeadGeo = new THREE.CylinderGeometry(0.14, 0.04, 0.06, 16);
        const vStem = new THREE.Mesh(vStemGeo, metalMaterial);
        const vHead = new THREE.Mesh(vHeadGeo, vIdx === 0 ? bronzeMaterial : steelDark);
        vHead.position.y = -0.3;

        const valveMesh = new THREE.Group();
        valveMesh.add(vStem);
        valveMesh.add(vHead);
        valveMesh.position.set(posX, 3.2, zOff);
        this.scene.add(valveMesh);
        cylValves.push(valveMesh);
      });
      this.valves.push(cylValves);

      // 5. Piston Assembly
      const pistonGroup = new THREE.Group();
      // Piston Crown & Skirt
      const crownGeo = new THREE.CylinderGeometry(this.bore / 2 - 0.015, this.bore / 2 - 0.015, 0.7, 32);
      const crownMesh = new THREE.Mesh(crownGeo, metalMaterial);
      pistonGroup.add(crownMesh);

      // Piston Rings (Dark groove lines)
      const ringGeo = new THREE.TorusGeometry(this.bore / 2 - 0.01, 0.01, 8, 32);
      ringGeo.rotateX(Math.PI / 2);
      const ring1 = new THREE.Mesh(ringGeo, steelDark);
      ring1.position.y = 0.22;
      const ring2 = new THREE.Mesh(ringGeo, steelDark);
      ring2.position.y = 0.14;
      pistonGroup.add(ring1);
      pistonGroup.add(ring2);

      // Wristpin
      const pinGeo = new THREE.CylinderGeometry(0.06, 0.06, 0.7, 16);
      pinGeo.rotateX(Math.PI / 2);
      const pinMesh = new THREE.Mesh(pinGeo, steelDark);
      pinMesh.position.y = -0.05;
      pistonGroup.add(pinMesh);

      pistonGroup.position.set(posX, 2.0, 0);
      this.scene.add(pistonGroup);
      this.pistons.push(pistonGroup);

      // 6. Connecting Rod Assembly
      const conrodGroup = new THREE.Group();
      // Shank (H-beam look)
      const shankGeo = new THREE.BoxGeometry(0.08, this.conrodLength, 0.12);
      const shankMesh = new THREE.Mesh(shankGeo, steelDark);
      shankMesh.position.y = -this.conrodLength / 2;
      conrodGroup.add(shankMesh);

      // Small end eye
      const smallEndGeo = new THREE.CylinderGeometry(0.10, 0.10, 0.16, 16);
      smallEndGeo.rotateX(Math.PI / 2);
      const smallEnd = new THREE.Mesh(smallEndGeo, bronzeMaterial);
      conrodGroup.add(smallEnd);

      // Big end cap
      const bigEndGeo = new THREE.CylinderGeometry(0.16, 0.16, 0.18, 20);
      bigEndGeo.rotateX(Math.PI / 2);
      const bigEnd = new THREE.Mesh(bigEndGeo, bronzeMaterial);
      bigEnd.position.y = -this.conrodLength;
      conrodGroup.add(bigEnd);

      conrodGroup.position.set(posX, 2.0, 0);
      this.scene.add(conrodGroup);
      this.conrods.push(conrodGroup);

      // 7. Crank Webs and Crankpin for this cylinder
      const webGroup = new THREE.Group();
      const crankPinGeo = new THREE.CylinderGeometry(0.12, 0.12, 0.22, 16);
      crankPinGeo.rotateZ(Math.PI / 2);
      const crankPin = new THREE.Mesh(crankPinGeo, steelDark);
      crankPin.position.y = this.crankRadius;
      webGroup.add(crankPin);

      // Counterweight webs
      const webGeo = new THREE.BoxGeometry(0.12, 0.8, 0.35);
      const web1 = new THREE.Mesh(webGeo, steelDark);
      web1.position.set(-0.14, 0.1, 0);
      const web2 = new THREE.Mesh(webGeo, steelDark);
      web2.position.set(0.14, 0.1, 0);
      webGroup.add(web1);
      webGroup.add(web2);

      webGroup.position.set(posX, 0, 0);
      this.crankshaft.add(webGroup);
    }
  }

  updateKinematics(deltaSec) {
    if (this.isPlaying) {
      // Angular velocity in deg/sec = RPM * 360 / 60 = RPM * 6
      const deltaTheta = this.rpm * 6.0 * deltaSec;
      this.crankAngleDeg = (this.crankAngleDeg + deltaTheta) % 720.0;
    }

    // Crankshaft visual rotation around X-axis
    const thetaRad = (this.crankAngleDeg * Math.PI) / 180.0;
    this.crankshaft.rotation.x = -thetaRad;

    // Firing order 1-3-4-2:
    // Cyl 1: 0 deg offset
    // Cyl 2: 180 deg offset
    // Cyl 3: 180 deg offset
    // Cyl 4: 0 deg offset
    const phaseOffsets = [0.0, Math.PI, Math.PI, 0.0];

    const startX = -((this.numCylinders - 1) * this.cylinderPitch) / 2;

    for (let i = 0; i < this.numCylinders; i++) {
      const cylTheta = thetaRad + phaseOffsets[i];
      const sinT = Math.sin(cylTheta);
      const cosT = Math.cos(cylTheta);

      // Exact slider-crank kinematic displacement from crank axis (y = 0):
      // y = r * cos(theta) + sqrt(L^2 - r^2 * sin^2(theta))
      const r = this.crankRadius;
      const l = this.conrodLength;
      const term = Math.max(0.0, l * l - (r * sinT) ** 2);
      const pistonY = r * cosT + Math.sqrt(term);

      // Conrod swing angle beta: sin(beta) = (r / L) * sin(theta)
      const betaRad = Math.asin((r / l) * sinT);

      // Update Piston Position
      const posX = startX + i * this.cylinderPitch;
      this.pistons[i].position.set(posX, pistonY, 0);

      // Update Conrod Position and Rotation
      // Conrod pivots from piston wristpin at (posX, pistonY, 0)
      this.conrods[i].position.set(posX, pistonY, 0);
      this.conrods[i].rotation.z = betaRad;

      // Cycle phase analysis (0 - 720 deg CA):
      // 0 - 180: Power / Expansion Stroke (Flame glow)
      // 180 - 360: Exhaust Stroke (Exhaust valve open)
      // 360 - 540: Intake Stroke (Intake valve open, injection spray)
      // 540 - 720: Compression Stroke
      const cylCycleDeg = (this.crankAngleDeg + (phaseOffsets[i] * 180.0) / Math.PI) % 720.0;

      // Valve Lift Animation
      const valves = this.valves[i];
      if (valves && valves.length === 2) {
        // Intake valve: opens 350 to 550 deg CA
        let inLift = 0.0;
        if (cylCycleDeg >= 340 && cylCycleDeg <= 550) {
          inLift = Math.sin(((cylCycleDeg - 340) / 210) * Math.PI) * 0.12;
        }
        valves[0].position.y = 3.2 - inLift;

        // Exhaust valve: opens 130 to 390 deg CA
        let exLift = 0.0;
        if (cylCycleDeg >= 120 && cylCycleDeg <= 380) {
          exLift = Math.sin(((cylCycleDeg - 120) / 260) * Math.PI) * 0.12;
        }
        valves[1].position.y = 3.2 - exLift;
      }

      // Direct Hydrogen Injection Spray (active during early intake: 360-440 deg)
      const injPlume = this.injectors[i];
      if (injPlume) {
        if (cylCycleDeg >= 360 && cylCycleDeg <= 440) {
          const fade = Math.sin(((cylCycleDeg - 360) / 80) * Math.PI);
          injPlume.material.opacity = fade * 0.8;
          injPlume.scale.set(1 + fade * 0.4, 1 + fade * 0.6, 1 + fade * 0.4);
        } else {
          injPlume.material.opacity = 0.0;
        }
      }

      // Combustion Flame Glow (active near TDC ignition: 355 to 60 deg)
      const flame = this.flames[i];
      if (flame) {
        let isPower = false;
        let flameOpacity = 0.0;
        if (cylCycleDeg >= 700 || cylCycleDeg <= 70) {
          const norm = cylCycleDeg >= 700 ? (cylCycleDeg - 700) : (cylCycleDeg + 20);
          flameOpacity = Math.max(0.0, 1.0 - norm / 90.0);
          isPower = true;
        }
        flame.material.opacity = flameOpacity * 0.85;
        flame.scale.set(1 + flameOpacity * 0.5, 1 + flameOpacity * 0.5, 1 + flameOpacity * 0.5);
      }
    }

    // Update UI HUD if elements exist
    const caLabel = document.getElementById("hud-crank-angle");
    if (caLabel) {
      caLabel.textContent = `${this.crankAngleDeg.toFixed(1)}° CA`;
    }
  }

  animate() {
    requestAnimationFrame(() => this.animate());

    const now = performance.now();
    const deltaSec = Math.min(0.05, (now - this.lastTime) / 1000.0);
    this.lastTime = now;

    this.updateKinematics(deltaSec);

    if (this.controls) {
      this.controls.update();
    }

    this.renderer.render(this.scene, this.camera);
  }

  onWindowResize() {
    if (!this.container || !this.renderer || !this.camera) return;
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
  }

  setSpeed(rpm) {
    this.rpm = parseFloat(rpm);
  }

  setCrankAngle(caDeg) {
    this.crankAngleDeg = parseFloat(caDeg) % 720.0;
    this.updateKinematics(0.0);
  }

  togglePlay() {
    this.isPlaying = !this.isPlaying;
    return this.isPlaying;
  }

  resetCamera() {
    this.camera.position.set(4.5, 3.2, 5.0);
    if (this.controls) {
      this.controls.target.set(0, 1.2, 0);
      this.controls.update();
    }
  }

  setCameraView(view) {
    if (!this.controls) return;
    if (view === "front") {
      this.camera.position.set(0, 1.5, 6.0);
    } else if (view === "side") {
      this.camera.position.set(6.0, 1.5, 0);
    } else if (view === "top") {
      this.camera.position.set(0, 7.0, 0.1);
    } else { // isometric
      this.camera.position.set(4.5, 3.2, 5.0);
    }
    this.controls.target.set(0, 1.2, 0);
    this.controls.update();
  }
}

// Global visualizer instance
let engine3D = null;
window.initEngine3D = function() {
  if (!engine3D && document.getElementById("engine3d-canvas-container")) {
    engine3D = new Engine3DVisualizer("engine3d-canvas-container");
  }
  return engine3D;
};
