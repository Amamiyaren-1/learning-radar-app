import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { createArchiveLighting, type LightingLook } from "./archive-lighting";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { SSAOPass } from "three/addons/postprocessing/SSAOPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { BokehPass } from "three/addons/postprocessing/BokehPass.js";
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
import { normalizeQuality, type RenderQuality } from "./render-quality";
import { applyTextureQuality, resizeQuality } from "./quality-renderer";
import { CardAppearance } from "./appearance";
import { configureInternalOptics } from "./internal-optics";
import { DecryptionController } from "./decryption";
import { fileLocation, rebaseRows, archiveColumns } from "./data";
import {
  cellKey,
  sameCell,
  selectionCell,
  fileAtCell,
  poolCell,
  visibleCell,
  LOOP_COLUMNS,
  LOOP_ROWS,
  COLUMN_SPACING,
  ROW_SPACING,
  type ArchiveCell,
  type ArchiveNavigation,
} from "./archive-loop";

import {
  archiveWave,
  extraction,
  baselineSelectionWave,
  rippleEnvelope,
  settlingWave,
  damp,
  columnStrength,
  idleWave,
  cinematicField,
  cinematicSelectedField,
  INSPECTION_LIFT,
  returnStep,
  introReturn,
  introFraming,
} from "./motion";

const ease = (t: number) => {
  t = THREE.MathUtils.clamp(t, 0, 1);
  return t * t * t * (t * (t * 6 - 15) + 10);
};
export class ArchiveScene {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  // The reference uses a long lens 72–140 units from the cassette. A 0.1 near
  // plane quantizes adjacent optical layers to the same depth (visible shimmer).
  // All visible foreground geometry is beyond 5; retain the framing and lens.
  readonly camera = new THREE.PerspectiveCamera(34, 16 / 9, 5, 300);
  private composer: EffectComposer;
  private ao: SSAOPass;
  private bokeh: BokehPass;
  private instances: THREE.InstancedMesh[] = [];
  private model = new THREE.Group();
  private appearance = new CardAppearance();
  private decryption = new DecryptionController();
  private cursor = new THREE.Vector2();
  private raycaster = new THREE.Raycaster();
  private dummy = new THREE.Object3D();
  private positions: THREE.Vector3[] = [];
  private cells: ArchiveCell[] = [];
  private renderCells:ArchiveCell[]=[];
  private renderFrustum=new THREE.Frustum();
  private renderProjection=new THREE.Matrix4();
  private selectedCell: ArchiveCell = { lane: 0, row: 12 };
  private looping = false;
  private coordinateOrigin: ArchiveCell = { lane: 0, row: 0 };
  private lift = { value: 0, velocity: 0 };
  private rail = { value: 0, velocity: 0 };
  private shoulder = { value: 12, velocity: 0 };
  private laneFocus = { value: 2, velocity: 0 };
  private columnCamera = { value: 0, velocity: 0 };
  private returnY: number | null = null;
  private canInspect = false;
  private clearance = 0;
  private pulseGain = 1;
  private idleGain = 0;
  private lastInteraction = 0;
  private scanTime = 29.1;
  private scanBlend = 0;
  private cameraAim = new THREE.Vector3();
  private openingHeight:{value:number;velocity:number}|null=null;
  private worldVelocity=0;
  private pointerGain={value:1,velocity:0};
  endIntro(mode:"archive"|"detail"){
    this.setMode(mode);
    // The cinematic has already reached the ordinary selection coordinates.
    // No pose lock, second extraction, new selection, or reading request.
    this.lift={value:.4,velocity:0};this.detail=0;this.reveal=1;
    this.rotation=this.targetRotation=0;this.returnY=null;this.openingHeight=null;
    this.pendingPulse=null;this.pulses=[];this.scanBlend=0;
    this.idleGain=0;this.worldVelocity=0;this.decryption.select();
  }
  private outgoing: {
    group: THREE.Group;
    slot: number;
    cell: ArchiveCell;
    lift: { value: number; velocity: number };
    returnY: number | null;
    clarity: number;
  }[] = [];
  private pulses: { row: number; lane: number; time: number }[] = [];
  private pendingPulse: ArchiveCell | null = null;
  private selectedSlot = 12;
  private selectedIndex = 0;
  private detail = 0;
  private targetDetail = 0;
  private reveal = 0;
  private targetReveal = 0;
  private last = 0;
  private pointer = new THREE.Vector2();
  private dragging = false;
  private rotation = 0;
  private targetRotation = 0;
  private light: THREE.DirectionalLight;
  private clock = 0;
  private loaded = false;
  private labelCanvas = document.createElement("canvas");
  private labelTexture?: THREE.CanvasTexture;
  private labelMark = new Image();
  private reduced = false;
  private quality = normalizeQuality(undefined);
  private appliedQuality = "";
  // Hardware antialiasing avoids SMAA's embedded data-image lookup under v1 CSP.
  private smaa = { enabled: false };
  private aoKernelSize = 32;
  private matrixMS=0;
  private renderMS=0;
  private freezeMatrices=false;
  private footprint={minX:0,maxX:0,minZ:0,maxZ:0};
  private recycleVisible=0;
  private recycled=0;
  private gpuTimer:any;
  private gpuQuery:WebGLQuery|null=null;
  private gpuMS:number|null=null;
  setMatrixProbe(value:boolean){this.freezeMatrices=value;}
  onSelect?: (index: number | undefined, cell?: ArchiveCell) => void;
  onHover?: (index: number | null | undefined) => void;
  constructor(
    private container: HTMLElement,
    private readonly selectionPulse = baselineSelectionWave,
    private readonly deferSelectionPulse = false,
    private readonly lightingLook: LightingLook = "baseline",
  ) {
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.renderer.info.autoReset = false;
    this.gpuTimer=this.renderer.getContext().getExtension('EXT_disjoint_timer_query_webgl2');
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.domElement.setAttribute(
      "aria-label",
      "三维研究档案阵列，可点击选择档案",
    );
    container.appendChild(this.renderer.domElement);
    this.scene.background = new THREE.Color("#ece9e3");
    this.scene.fog = new THREE.Fog("#ece9e3", 22, 47);
    this.light = createArchiveLighting(this.renderer, this.scene, lightingLook);
    this.light.castShadow = true;
    Object.assign(this.light.shadow.camera, {
      left: -16,
      right: 16,
      top: 15,
      bottom: -15,
      near: 0.1,
      far: 45,
    });
    this.light.shadow.mapSize.set(2048, 2048);
    this.light.shadow.normalBias = lightingLook === "refined" ? 0.018 : 0.035;
    this.light.shadow.bias = lightingLook === "refined" ? -0.00012 : -0.0003;
    this.light.shadow.radius = 4;
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(200, 200),
      new THREE.MeshStandardMaterial({ color: "#d8c9b9", roughness: 0.95 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -4.63;
    floor.receiveShadow = true;
    this.scene.add(floor);
    this.camera.position.set(-62.26, 35.98, 43.28);
    this.cameraAim.set(-0.5, 1.1, 0.4);
    this.camera.fov = 6.15;
    this.camera.lookAt(this.cameraAim);
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.ao = new SSAOPass(
      this.scene,
      this.camera,
      container.clientWidth,
      container.clientHeight,
    );
    this.ao.kernelRadius = lightingLook === "refined" ? 0.44 : 0.38;
    this.ao.minDistance = 0.001;
    this.ao.maxDistance = 0.09;
    this.composer.addPass(this.ao);
    this.bokeh = new BokehPass(this.scene, this.camera, {
      focus: 25,
      aperture: 0.0018,
      maxblur: 0.011,
    });
    this.composer.addPass(this.bokeh);
    this.smaa.enabled = false;
    this.composer.addPass(new OutputPass());
    this.bindPointer();
  }
  async load(assetUrl = "/static/ui/assets/archive-cassette.glb") {
    this.labelMark.src = "/static/ui/assets/radar-mark.svg";
    await this.labelMark.decode();
    const gltf = await new GLTFLoader().loadAsync(
      assetUrl,
    );
    gltf.scene.updateMatrixWorld(true);
    const meshes: THREE.Mesh[] = [];
    gltf.scene.traverse((o) => {
      if (o instanceof THREE.Mesh) meshes.push(o);
    });
    const count = LOOP_COLUMNS * LOOP_ROWS;
    for (let index = 0; index < count; index++) {
      const cell = poolCell(index);
      this.cells.push(cell);
      this.positions.push(this.cellPosition(cell));
    }
    for (const mesh of meshes) {
      const geom = mesh.geometry
        .clone()
        .applyMatrix4(mesh.matrixWorld)
        .scale(1, 1, 1);
      const source = mesh.material as THREE.MeshStandardMaterial;
      const name = source.name.replace(/\.\d+$/, "");
      const mat = source.clone() as THREE.MeshPhysicalMaterial;
      mat.envMapIntensity = 0.6;
      if (name === "Frosted_Polymer") {
        mat.color.set("#fffdfa");
        mat.transmission = 0.9;
        mat.thickness = 0.12;
        mat.roughness = 0.21;
        mat.ior = 1.46;
        mat.attenuationColor = new THREE.Color("#eee6df");
        mat.attenuationDistance = 2;
      }
      if (name === "Internal_Ceramic") {
        mat.color.set(this.lightingLook === "refined" ? "#b9ae9e" : "#c7beb6");
        mat.roughness = 0.6;
      }
      if(this.lightingLook==='refined'&&name==='Titanium_Fasteners'){
        mat.color.set('#696b65');mat.metalness=.78;mat.roughness=.33;
      }
      if(this.lightingLook==='refined'&&name==='Index_Inlay'){
        mat.color.set('#a38b6a');mat.roughness=.65;mat.metalness=.08;
      }
      if (name === "Printed_Label") mat.color.set("#eae5dc");
      if (name === "Ivory_Edges") {
        mat.color.set(this.lightingLook==='refined'?'#e1d6c5':"#f0e7df");
        mat.roughness = this.lightingLook==='refined'?.38:0.31;
        mat.transmission = this.lightingLook==='refined'?.35:0.65;
        mat.thickness = 0.04;
      }
      if (name === "Optical_Diffuser") {
        mat.color.set("#e2dad4");
        mat.transmission = 0;
        mat.roughness = 0.7;
      }
      if (name === "Subsurface_Optics") {
        mat.color.set(this.lightingLook === "refined" ? "#a98b6c" : "#b9aba1");
        mat.roughness = 0.48;
        mat.metalness = 0.05;
      }
      if (name === "Optical_Edges") {
        // Internal refractive shoulders must be in the opaque capture: WebGL's
        // screen-space transmission cannot recursively sample another glass mesh.
        mat.transmission = 0;
        mat.color.set(this.lightingLook === "refined" ? "#c5ad90" : "#d4c7be");
        mat.roughness = 0.26;
        mat.metalness = 0.08;
      }
      configureInternalOptics(name, mat);
      if (name === "Carbon_Ink") continue;
      const selectedMesh = new THREE.Mesh(geom, mat);
      selectedMesh.userData.surface = name;
      selectedMesh.castShadow = name === "Optical_Diffuser";
      selectedMesh.receiveShadow = true;
      this.model.add(selectedMesh);
      // Only the shell, edge and fasteners remain visible within tightly packed rows.
      // Keep sub-millimetre optical/typographic geometry on the extracted cassette.
      if (
        ![
          "Frosted_Polymer",
          "Ivory_Edges",
          "Titanium_Fasteners",
          "Index_Inlay",
          "Optical_Diffuser",
        ].includes(name)
      ) {
        this.appearance.register(name, mat);
        continue;
      }
      const arrayMat = mat.clone();
      if (name === "Frosted_Polymer") {
        arrayMat.transmission = 0.78;
        if (this.lightingLook === "refined") {
          // Longer oblique paths pick up the warm body tint, while the thin
          // edges and the extracted clear cover retain a brighter response.
          arrayMat.thickness = 0.28;
          arrayMat.attenuationColor.set("#d4c7b4");
          arrayMat.attenuationDistance = 1.2;
        }
        arrayMat.transparent = false;
        arrayMat.color.set("#fff7ed");
        arrayMat.onBeforeCompile = (shader) => {
          shader.vertexShader =
            "varying float vPanelHeight;\n" + shader.vertexShader;
          shader.vertexShader = shader.vertexShader.replace(
            "#include <begin_vertex>",
            "#include <begin_vertex>\nvPanelHeight = position.y / 3.7;",
          );
          shader.fragmentShader =
            "varying float vPanelHeight;\n" + shader.fragmentShader;
          shader.fragmentShader = shader.fragmentShader.replace(
            "#include <color_fragment>",
            "#include <color_fragment>\ndiffuseColor.rgb *= mix(vec3(0.40, 0.30, 0.20), vec3(1.0, 0.98, 0.94), smoothstep(0.1, 1.0, vPanelHeight));",
          );
        };
        arrayMat.roughness = 0.28;
        arrayMat.clearcoat = 0.3;
        arrayMat.clearcoatRoughness = 0.25;
      }
      if (name === "Optical_Diffuser") arrayMat.color.set("#806447");
      if (name === "Ivory_Edges") {
        arrayMat.transmission = 0;
        arrayMat.color.set(
          this.lightingLook === "refined" ? "#dcc9b0" : "#fff5e9",
        );
        arrayMat.roughness = 0.38;
      }
      if (name === "Index_Inlay") {
        arrayMat.color.set("#e4d6c5");
        arrayMat.metalness = 0.05;
      }
      this.appearance.register(name, mat, arrayMat);
      // Distant fasteners use four six-sided caps. The extracted model retains
      // all original 2136 triangles; only densely occluded pool hardware is LOD.
      let arrayGeometry=geom;
      if(name==='Titanium_Fasteners'){
        geom.computeBoundingBox();const box=geom.boundingBox!;const mid=box.getCenter(new THREE.Vector3());const caps:THREE.BufferGeometry[]=[];
        for(const sx of [-1,1])for(const sy of [-1,1]){
          const bounds=new THREE.Box3();const a=geom.attributes.position;
          for(let k=0;k<a.count;k++){const p=new THREE.Vector3().fromBufferAttribute(a,k);if((p.x>mid.x?1:-1)===sx&&(p.y>mid.y?1:-1)===sy)bounds.expandByPoint(p);}
          if(bounds.isEmpty())continue;const size=bounds.getSize(new THREE.Vector3()),center=bounds.getCenter(new THREE.Vector3());
          caps.push(new THREE.CylinderGeometry(Math.min(size.x,size.y)/2,Math.min(size.x,size.y)/2,size.z,6,1).rotateX(Math.PI/2).translate(center.x,center.y,center.z));
        }
        arrayGeometry=mergeGeometries(caps)!;caps.forEach(g=>g.dispose());
      }
      const inst = new THREE.InstancedMesh(arrayGeometry, arrayMat, count);
      // Every material uses identical transforms: share one GPU attribute.
      if(this.instances.length)inst.instanceMatrix=this.instances[0].instanceMatrix;
      inst.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      inst.castShadow = name === "Optical_Diffuser";
      inst.receiveShadow = true;
      inst.frustumCulled = false;
      // Raycasting also consults a sphere even when renderer culling is off.
      // Matrices move inside a bounded world window; use a conservative bound.
      inst.boundingSphere=new THREE.Sphere(new THREE.Vector3(),120);
      this.instances.push(inst);
      this.scene.add(inst);
    }
    if(this.lightingLook==='refined'){
      // One opaque etched frame, only on the selected/returning full model.
      // No extra geometry, transparent layers or materials in the 576-slot pool.
      const parts:THREE.BufferGeometry[]=[];
      for(const x of [-2.28,2.28])for(const y of [.18,3.48]){
        parts.push(new THREE.BoxGeometry(.32,.012,.008).translate(x+(x<0?.16:-.16),y,.268));
        parts.push(new THREE.BoxGeometry(.012,.25,.008).translate(x,y+(y<1?.125:-.125),.268));
      }
      const geometry=mergeGeometries(parts)!;parts.forEach(p=>p.dispose());
      const mat=new THREE.MeshPhysicalMaterial({color:'#a4947d',roughness:.78,metalness:.1});
      this.appearance.register('Archive_Frame',mat);const frame=new THREE.Mesh(geometry,mat);frame.userData.surface='Archive_Frame';frame.receiveShadow=true;this.model.add(frame);
    }
    this.labelCanvas.width = 1024;
    this.labelCanvas.height = 440;
    this.labelTexture = new THREE.CanvasTexture(this.labelCanvas);
    this.labelTexture.colorSpace = THREE.SRGBColorSpace;
    this.labelTexture.anisotropy =
      this.renderer.capabilities.getMaxAnisotropy();
    const label = new THREE.Mesh(
      new THREE.PlaneGeometry(0.99, 0.46),
      new THREE.MeshBasicMaterial({
        map: this.labelTexture,
        toneMapped: false,
        transparent: true,
        depthWrite: false,
      }),
    );
    label.position.set(-1.36, 3.04, 0.255);
    this.model.add(label);
    this.appearance.prepare(this.model);
    this.appearance.apply(this.model, 0);
    this.drawLabel(0);
    this.scene.add(this.model);
    this.model.position.copy(this.positions[this.selectedSlot]);
    this.loaded = true;
    void document.fonts.load('700 81px "LR Novecento"','LEARNING RADAR').then(()=>{if(this.loaded)this.drawLabel(this.selectedIndex);}).catch(()=>{});
    // The supplied Normal Webfont can arrive after Bold. Refresh its label
    // text too, without rebuilding the scene or replaying the introduction.
    void document.fonts.load('400 32px "LR Novecento"','PERSONAL ARCHIVE').then(()=>{if(this.loaded)this.drawLabel(this.selectedIndex);}).catch(()=>{});
  }

  setMode(mode: "hidden" | "archive" | "detail") {
    if(mode==='hidden'){this.pulses=[];this.pendingPulse=null;this.openingHeight=null;}
    if(mode==='detail'&&!this.targetDetail)this.openingHeight={value:this.model.position.y,velocity:this.worldVelocity};
    if(mode!=='detail')this.openingHeight=null;
    if (mode === "detail") this.decryption.enter(this.scanBlend > .9 && this.decryption.clarity > .999);
    else this.decryption.leave();
    if (mode === "hidden") this.decryption.select();
    if (mode !== "archive") this.pendingPulse = null;
    this.looping = mode !== "hidden";
    if (!this.looping) {
      const canonical = fileLocation(this.selectedIndex);
      this.selectedCell = { lane: canonical.lane, row: canonical.row };
      this.coordinateOrigin = { lane: 0, row: 0 };
      for (const old of this.outgoing) {
        this.scene.remove(old.group);
        this.appearance.dispose(old.group);
      }
      this.outgoing = [];
    }
    this.lastInteraction = this.clock;
    this.targetReveal = mode === "hidden" ? 0 : 1;
    this.targetDetail = mode === "detail" ? 1 : 0;
    this.dragging = false;
    if (mode !== "detail") {
      this.targetRotation = 0;
      if (this.rotation !== 0) this.returnY = this.model.position.y;
    } else this.returnY = null;
  }
  setReduced(value: boolean) {
    this.reduced = value;
  }
  setQuality(value: RenderQuality | boolean) {
    const quality =
      typeof value === "boolean"
        ? normalizeQuality(undefined, value)
        : normalizeQuality(value);
    const key = JSON.stringify(quality);
    if (this.appliedQuality === key) return;
    this.appliedQuality = key;
    this.quality = quality;
    if (quality.aoSamples && quality.aoSamples !== this.aoKernelSize) {
      const old = this.ao;
      this.ao = new SSAOPass(this.scene, this.camera, 1, 1, quality.aoSamples);
      this.ao.kernelRadius = old.kernelRadius;
      this.ao.minDistance = old.minDistance;
      this.ao.maxDistance = old.maxDistance;
      const index = this.composer.passes.indexOf(old);
      this.composer.removePass(old);
      this.composer.insertPass(this.ao, index);
      // Three r183 SSAOPass.dispose omits its noise map and SSAO material.
      old.noiseTexture?.dispose();old.ssaoMaterial.dispose();
      old.dispose();
      this.aoKernelSize = quality.aoSamples;
    }
    this.ao.enabled = quality.aoSamples > 0;
    this.bokeh.enabled = quality.depthOfField > 0;
    this.smaa.enabled = quality.antialias === "smaa";
    this.renderer.shadowMap.enabled = quality.shadows > 0;
    const size = Math.min(
      quality.shadows || 1024,
      this.renderer.capabilities.maxTextureSize,
    );
    if (this.light.shadow.mapSize.x !== size) {
      this.light.shadow.map?.dispose();
      this.light.shadow.map = null;
      this.light.shadow.mapSize.set(size, size);
    }
    this.light.shadow.needsUpdate = true;
    applyTextureQuality(this.scene, this.renderer, quality);
    this.resize();
  }
  private cellPosition(cell: ArchiveCell) {
    return new THREE.Vector3(
      (cell.lane - 2) * COLUMN_SPACING,
      -4.6,
      (cell.row - 15.5) * ROW_SPACING,
    );
  }
  private rebaseCoordinates() {
    // Periodically reduce the logical coordinates while preserving every
    // relative position, spring velocity, ripple and idle phase.
    const shift = {
      lane:
        Math.abs(this.selectedCell.lane) > 2048
          ? Math.round(this.selectedCell.lane / Math.max(1,archiveColumns.length)) * Math.max(1,archiveColumns.length)
          : 0,
      row:
        Math.abs(this.selectedCell.row) > 2048
          ? Math.trunc(this.selectedCell.row)
          : 0,
    };
    if (!shift.lane && !shift.row) return;
    this.selectedCell.lane -= shift.lane;
    this.selectedCell.row -= shift.row;
    this.coordinateOrigin.lane += shift.lane;
    this.coordinateOrigin.row += shift.row;
    rebaseRows(shift.row);
    this.laneFocus.value -= shift.lane;
    this.shoulder.value -= shift.row;
    this.columnCamera.value -= shift.lane * COLUMN_SPACING;
    this.rail.value += shift.row * ROW_SPACING;
    for (const old of this.outgoing) {
      old.cell.lane -= shift.lane;
      old.cell.row -= shift.row;
    }
    for (const pulse of this.pulses) {
      pulse.lane -= shift.lane;
      pulse.row -= shift.row;
    }
    if (this.pendingPulse) {
      this.pendingPulse.lane -= shift.lane;
      this.pendingPulse.row -= shift.row;
    }
  }
  select(index: number, navigation?: ArchiveNavigation) {
    this.lastInteraction = this.clock;
    this.selectedIndex = index;
    const next = fileLocation(index).slot;
    const canonical = fileLocation(index);
    const cell = this.looping
      ? selectionCell(index, this.selectedCell, navigation)
      : { lane: canonical.lane, row: canonical.row };
    const changed = !sameCell(cell, this.selectedCell);
    if (this.looping && changed && this.loaded && this.lift.value > 0.0001) {
      const group = this.model.clone(true);
      this.appearance.prepare(group);
      const label = group.children[group.children.length - 1] as THREE.Mesh;
      const canvas = document.createElement("canvas");
      canvas.width = 1024;
      canvas.height = 440;
      canvas.getContext("2d")!.drawImage(this.labelCanvas, 0, 0);
      const map = new THREE.CanvasTexture(canvas);
      map.colorSpace = THREE.SRGBColorSpace;
      label.material = new THREE.MeshBasicMaterial({
        map,
        toneMapped: false,
        transparent: true,
        depthWrite: false,
      });
      this.appearance.apply(group, ease(this.lift.value / 0.4));
      this.appearance.setClarity(group, this.decryption.clarity);
      this.scene.add(group);
      this.outgoing.push({
        group,
        slot: this.selectedSlot,
        cell: { ...this.selectedCell },
        lift: { ...this.lift },
        returnY: group.rotation.y !== 0 ? group.position.y : null,
        clarity: this.decryption.clarity,
      });
      while (this.outgoing.length > 3) {
        const old = this.outgoing.shift()!;
        this.scene.remove(old.group);
        this.appearance.dispose(old.group);
      }
      this.lift.value = 0;
      this.lift.velocity = 0;
    }
    this.selectedSlot = next;
    this.selectedCell = cell;
    if (changed) {
      this.openingHeight=null;
      this.decryption.select();
      this.rotation = 0;
      this.returnY = null;
    }
    const returning = this.outgoing.findIndex((o) => sameCell(o.cell, cell));
    if (returning >= 0) {
      const o = this.outgoing[returning];
      this.lift = { ...o.lift };
      this.rotation = o.group.rotation.y;
      this.returnY = o.returnY;
      this.decryption.select(o.clarity);
      this.scene.remove(o.group);
      this.appearance.dispose(o.group);
      this.outgoing.splice(returning, 1);
    }
    if (this.deferSelectionPulse) {
      this.pendingPulse = this.looping ? { ...cell } : null;
    } else if(changed) this.emitPulse(cell);
    this.targetRotation = 0;
    this.drawLabel(index);
  }
  private emitPulse(cell: ArchiveCell) {
    this.pulses.push({ ...cell, time: this.clock });
    this.pulses = this.pulses.slice(-6);
  }
  private drawLabel(index: number) {
    if (!this.labelTexture) return;
    const c = this.labelCanvas.getContext("2d")!;
    c.fillStyle = "#e6e2d9";
    c.fillRect(0, 0, 1024, 440);
    c.fillStyle = "#171713";
    c.fillRect(12, 12, 1000, 6);
    c.fillRect(12, 419, 1000, 3);
    c.font = '700 81px "LR Novecento", MiSans, sans-serif';
    const brandWidth=c.measureText('LEARNING RADAR').width;
    if(brandWidth>735)c.font=`700 ${Math.floor(81*735/brandWidth)}px "LR Novecento", MiSans, sans-serif`;
    c.fillText("LEARNING RADAR", 22, 116);
    c.font = '400 32px "LR Novecento", sans-serif';
    c.fillStyle = "#878476";
    c.fillText("PERSONAL ARCHIVE", 25, 174);
    c.fillStyle = "#171713";
    c.font = '400 130px "LR Novecento", sans-serif';
    c.fillText("NO." + String(index + 1).padStart(3, "0"), 22, 360);
    c.fillRect(782, 32, 221, 39);
    c.fillStyle = "#eee9de";
    c.font = '400 24px "LR Novecento", sans-serif';
    c.fillText("L R / O S", 809, 61);
    c.fillStyle = "#171713";
    c.font = '400 64px "LR Novecento", sans-serif';
    c.fillText("INFO", 830, 143);
    c.drawImage(this.labelMark, 790, 242, 210, 98);
    this.labelTexture.needsUpdate = true;
  }
  resize() {
    const w = this.container.clientWidth,
      h = this.container.clientHeight;
    const dimensions = resizeQuality(
      this.renderer,
      this.composer,
      this.container,
      this.quality,
    );
    this.ao.setSize(
      Math.max(1, Math.floor(dimensions.width * this.quality.aoResolution)),
      Math.max(1, Math.floor(dimensions.height * this.quality.aoResolution)),
    );
    this.container.dataset.renderQuality = JSON.stringify({
      ...JSON.parse(this.container.dataset.renderQuality!),
      aoSamples: this.ao.enabled ? this.aoKernelSize : 0,
      aoWidth: this.ao.width,
      aoHeight: this.ao.height,
      shadows: this.renderer.shadowMap.enabled
        ? this.light.shadow.mapSize.x
        : 0,
      depthOfField: this.bokeh.enabled ? this.quality.depthOfField : 0,
    });
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
  private bindPointer() {
    const canvas = this.renderer.domElement;
    let startX = 0,
      startY = 0;
    canvas.addEventListener("pointerdown", (e) => {
      startX = e.clientX;
      startY = e.clientY;
      if (this.canInspect) {
        this.dragging = true;
        canvas.setPointerCapture(e.pointerId);
      }
    });
    canvas.addEventListener("pointermove", (e) => {
      const r = canvas.getBoundingClientRect();
      this.pointer.set(
        (e.clientX - r.left) / r.width - 0.5,
        (e.clientY - r.top) / r.height - 0.5,
      );
      if (this.dragging) {
        if (!this.canInspect) {
          this.dragging = false;
          return;
        }
        this.targetRotation = THREE.MathUtils.clamp(
          this.targetRotation + e.movementX * 0.004,
          -0.8,
          0.8,
        );
        return;
      }
      if (this.reveal < 0.8 || this.detail > 0.2 || !this.loaded) return;
      this.cursor.set(
        ((e.clientX - r.left) / r.width) * 2 - 1,
        (-(e.clientY - r.top) / r.height) * 2 + 1,
      );
      this.raycaster.setFromCamera(this.cursor, this.camera);
      const hit = this.raycaster.intersectObjects(
        [this.instances[0], this.model],
        true,
      )[0];
      canvas.style.cursor = hit ? "pointer" : "default";
      this.onHover?.(
        hit
          ? hit.instanceId !== undefined
            ? fileAtCell(this.renderCells[hit.instanceId])
            : this.selectedIndex
          : null,
      );
    });
    canvas.addEventListener("pointerup", (e) => {
      this.dragging = false;
      if (
        Math.hypot(e.clientX - startX, e.clientY - startY) > 6 ||
        this.detail > 0.2 ||
        this.reveal < 0.8 ||
        !this.loaded
      )
        return;
      const r = canvas.getBoundingClientRect();
      this.cursor.set(
        ((e.clientX - r.left) / r.width) * 2 - 1,
        (-(e.clientY - r.top) / r.height) * 2 + 1,
      );
      this.raycaster.setFromCamera(this.cursor, this.camera);
      const hit = this.raycaster.intersectObjects(
        [this.instances[0], this.model],
        true,
      )[0];
      if (hit)
        this.onSelect?.(
          hit.instanceId !== undefined
            ? fileAtCell(this.renderCells[hit.instanceId])
            : this.selectedIndex,
          hit.instanceId !== undefined
            ? { ...this.renderCells[hit.instanceId] }
            : { ...this.selectedCell },
        );
    });
    canvas.addEventListener("pointercancel", () => (this.dragging = false));
    canvas.addEventListener("pointerleave", () => {
      this.pointer.set(0, 0);
      this.onHover?.(null);
    });
  }
  update(
    time: number,
    cinematic?: { reveal: number; lift: number; zoom: number; time: number },
  ) {
    const dt = Math.min(time - this.last || 0.016, 0.05);
    this.last = time;
    this.clock = time;
    if (!this.loaded) return;
    const blend = 1 - Math.exp(-dt * (this.reduced ? 35 : 2.8));
    this.reveal = cinematic
      ? cinematic.reveal
      : THREE.MathUtils.lerp(this.reveal, this.targetReveal, blend);
    this.rotation = this.targetDetail
      ? THREE.MathUtils.lerp(this.rotation, this.targetRotation, blend)
      : returnStep(this.rotation, dt, this.reduced);
    const returnProgress = cinematic ? introReturn(cinematic.time) : 0;
    const shot = cinematic ? Math.min(34.8,cinematic.time) : 29.1;
    if (cinematic) {
      this.scanTime = shot;
      this.scanBlend = 1;
    } else {
      this.scanTime += dt;
      this.scanBlend *= Math.exp(-dt * 3);
    }
    if (this.looping && !cinematic) this.rebaseCoordinates();
    const chosen = this.cellPosition(this.selectedCell);
    const selectedRow = this.selectedCell.row;
    const selectedLane = this.selectedCell.lane;
    damp(this.shoulder, selectedRow, this.reduced ? 35 : 5, dt);
    damp(this.laneFocus, selectedLane, this.reduced ? 35 : 4, dt);
    damp(this.columnCamera, chosen.x, this.reduced ? 35 : 3.7, dt);
    damp(
      this.rail,
      cinematic ? 0 : -2.17 - chosen.z,
      this.reduced ? 35 : 3.7,
      dt,
    );
    if (cinematic) {
      this.rail.value = (-2.17-chosen.z)*returnProgress;
      this.rail.velocity = 0;
      this.lift.value = THREE.MathUtils.lerp(extraction(shot),.4,returnProgress);
      this.lift.velocity = 0;
      this.shoulder.value = selectedRow;
      this.shoulder.velocity = 0;
      this.laneFocus.value = selectedLane;
      this.laneFocus.velocity = 0;
      this.columnCamera.value = chosen.x;
      this.columnCamera.velocity = 0;
    }
    // Keep the illuminated set near the origin. Lateral navigation is a track
    // movement of the whole array, just like the existing front/back rail.
    const trackX = cinematic ? chosen.x*returnProgress : this.columnCamera.value;
    // Project both cassette base and top planes. Recycle around the rendered
    // frustum, not the selected row. This includes the damped camera in transit.
    const points:THREE.Vector3[]=[];
    for(const y of [-4.6,.6])for(const x of [-1,1])for(const z of [-1,1]){
      const ray=new THREE.Vector3(x,z,.5).unproject(this.camera).sub(this.camera.position).normalize();
      if(ray.y<-.001)points.push(this.camera.position.clone().addScaledVector(ray,(y-this.camera.position.y)/ray.y));
    }
    if(points.length)this.footprint={minX:Math.min(...points.map(p=>p.x)),maxX:Math.max(...points.map(p=>p.x)),minZ:Math.min(...points.map(p=>p.z)),maxZ:Math.max(...points.map(p=>p.z))};
    const center = {lane:(trackX+(this.footprint.minX+this.footprint.maxX)/2)/COLUMN_SPACING+2,row:((this.footprint.minZ+this.footprint.maxZ)/2-this.rail.value)/ROW_SPACING+15.5};
    const matrixStart=performance.now();
    for (let i = 0; i < this.positions.length; i++) {
      // Use the same bounded pool on both sides of the return. The reference
      // 5×32 pool would expose a hard array edge when the camera pulls back.
      const next=visibleCell(i,center);
      if(this.looping&&!cinematic&&!sameCell(next,this.cells[i])){
        const old=this.cellPosition(this.cells[i]);old.x-=trackX;old.z+=this.rail.value;
        // Count any relocation whose full bounds touch the actual view. This
        // remains a fixture diagnostic; visual recording is still required.
        if(old.x+2.5>=this.footprint.minX&&old.x-2.5<=this.footprint.maxX&&old.z+.35>=this.footprint.minZ&&old.z-.35<=this.footprint.maxZ&&this.reveal>.99)this.recycleVisible++;
        this.recycled++;
      }
      this.cells[i]=next;
      this.positions[i].set(
        (this.cells[i].lane - 2) * COLUMN_SPACING,
        -4.6,
        (this.cells[i].row - 15.5) * ROW_SPACING,
      );
    }
    this.pulses = this.pulses.filter((p) => time - p.time < 3.2);
    const aligningCopy = this.outgoing.some((o) => o.returnY !== null);
    const idle =
      !cinematic &&
      !this.reduced &&
      this.targetReveal > 0 &&
      !this.targetDetail &&
      this.detail < 0.01 &&
      this.returnY === null &&
      !aligningCopy &&
      time - this.lastInteraction > 2.5;
    this.idleGain = cinematic
      ? 0
      : THREE.MathUtils.lerp(
          this.idleGain,
          idle ? 1 : 0,
          1 - Math.exp(-dt * (idle ? 0.8 : 4)),
        );
    this.pulseGain = THREE.MathUtils.lerp(
      this.pulseGain,
      this.targetDetail || this.returnY !== null || aligningCopy ? 0 : 1,
      1 - Math.exp(-dt * 8),
    );
    const rawField = (row: number, lane: number) => {
      if (cinematic)
        return THREE.MathUtils.lerp(row===selectedRow&&lane===selectedLane?cinematicSelectedField(shot,selectedRow,selectedLane):cinematicField(
          row,
          lane,
          shot,
          this.shoulder.value,
          this.laneFocus.value,
        ),settlingWave(row-this.shoulder.value,26.56)*columnStrength(lane,this.laneFocus.value),returnProgress);
      let height =
        archiveWave(
          row + this.coordinateOrigin.row,
          lane + this.coordinateOrigin.lane,
          this.scanTime,
        ) *
          this.scanBlend +
        idleWave(
          row + this.coordinateOrigin.row,
          lane + this.coordinateOrigin.lane,
          time,
        ) *
          this.idleGain;
      if (!cinematic && !this.reduced) {
        let ripple = 0;
        for (const p of this.pulses) {
          const distance = Math.hypot(row - p.row, (lane - p.lane) * 2.2);
          const age = time - p.time;
          ripple +=
            this.selectionPulse(distance, age) *
            (this.deferSelectionPulse ? rippleEnvelope(distance, age) : 1);
        }
        height += THREE.MathUtils.clamp(ripple, -0.6, 0.6) * this.pulseGain;
      }
      const distance = row - this.shoulder.value;
      return (
        height +
        settlingWave(distance, 26.56) *
          columnStrength(lane, this.laneFocus.value)
      );
    };
    const heights=new Map<number,number>();
    const field=(row:number,lane:number)=>{const key=(lane+4096)*32768+(row+4096)*2;let v=heights.get(key);if(v===undefined){v=rawField(row,lane);heights.set(key,v);}return v;};
    const selectedBase = chosen.y + field(selectedRow, selectedLane);
    if (!cinematic) {
      if(this.targetDetail&&this.openingHeight){
        // Spring the ACTUAL altitude, so array waves cannot make an opening
        // cassette dip while its relative lift is increasing.
        const base=chosen.y+settlingWave(selectedRow-this.shoulder.value,26.56)*columnStrength(selectedLane,this.laneFocus.value);
        damp(this.openingHeight,base+INSPECTION_LIFT,this.reduced?35:4.2,dt);
        this.lift.value=this.openingHeight.value-selectedBase;
        this.lift.velocity=this.openingHeight.velocity;
      } else if (this.returnY !== null && this.rotation !== 0) {
        this.lift.value = this.returnY - selectedBase;
        this.lift.velocity = 0;
      } else {
        this.returnY = null;
        damp(
          this.lift,
          this.targetDetail
            ? INSPECTION_LIFT
            : this.outgoing.some(
                  (o) =>
                    o.returnY !== null &&
                    o.cell.lane === selectedLane &&
                    Math.abs(o.cell.row - selectedRow) < 5,
                )
              ? 0
              : 0.4 * this.targetReveal,
          this.reduced
            ? 35
            : this.deferSelectionPulse &&
                !this.targetDetail &&
                this.lift.value < 0.4
              ? 7.6
              : 4.2,
          dt,
        );
      }
    }
    const cameraTarget = this.targetDetail
      ? ease((this.lift.value - 0.8) / 2.4)
      : this.returnY !== null
        ? this.detail
        : ease((this.lift.value - 0.4) / (INSPECTION_LIFT - 0.4));
    this.detail = cinematic
      ? cinematic.zoom
      : THREE.MathUtils.lerp(this.detail, cameraTarget, blend);
    const detail = this.detail;
    this.decryption.update(dt, detail > .78 && this.lift.value > 3.3, this.reduced,
      cinematic ? shot + 5 : undefined);
    if(cinematic)this.decryption.clarity*=1-returnProgress;
    this.appearance.apply(this.model, ease(this.lift.value / 0.4));
    this.appearance.setClarity(this.model, this.decryption.clarity);
    // Reference 26.92–27.76: the array travels horizontally into a white field.
    const entry = cinematic ? ease((shot - 21.9) / 0.86) : this.reveal;
    const entranceTime = THREE.MathUtils.clamp((shot - 21.92) / 0.75, 0, 1);
    const entryZ = cinematic
      ? -23 * (1 - entranceTime) ** 2
      : -28 * (1 - entry);
    for (let i = this.outgoing.length - 1; i >= 0; i--) {
      const o = this.outgoing[i];
      const p = this.cellPosition(o.cell);
      const baseY = p.y + field(o.cell.row, o.cell.lane);
      o.group.rotation.y = returnStep(o.group.rotation.y, dt, this.reduced);
      if (o.returnY !== null) {
        o.lift.value = o.returnY - baseY;
        o.lift.velocity = 0;
        if (o.group.rotation.y === 0) o.returnY = null;
      } else damp(o.lift, 0, this.reduced ? 35 : 4.5, dt);
      o.group.position.set(
        p.x - trackX,
        baseY + o.lift.value,
        p.z + entryZ + this.rail.value,
      );
      const quality = ease(o.lift.value / 0.4);
      this.appearance.apply(o.group, quality);
      o.clarity = this.reduced ? 0 : o.clarity * Math.exp(-dt * 9);
      this.appearance.setClarity(o.group, o.clarity);
      const { row, lane } = o.cell;
      o.group.rotation.x =
        (field(row + 0.5, lane) - field(row - 0.5, lane)) *
        0.024 *
        (1 - detail) *
        (1 - quality);
      if (o.lift.value < 0.0001 && Math.abs(o.group.rotation.y) < 0.0001) {
        this.scene.remove(o.group);
        this.appearance.dispose(o.group);
        this.outgoing.splice(i, 1);
      }
    }
    if (
      this.pendingPulse &&
      !cinematic &&
      !this.targetDetail &&
      this.targetReveal
    ) {
      const selectedY = selectedBase + this.lift.value;
      const oldCardsLower = this.outgoing.every(
        (old) =>
          old.cell.lane !== selectedLane ||
          Math.abs(old.cell.row - selectedRow) > 4 ||
          old.group.position.y + 0.015 < selectedY,
      );
      // The new file causes the wave: finish most of its rise and let nearby
      // outgoing files get below it before starting the outward pulse.
      if (this.lift.value >= 0.35 && this.returnY === null && oldCardsLower) {
        if (!this.reduced) this.emitPulse(this.pendingPulse);
        this.pendingPulse = null;
      }
    }
    // Resolve returning copies before restoring their array instances, avoiding
    // a missing file for one frame at the ownership handoff.
    const hidden = new Set(this.outgoing.map((o) => cellKey(o.cell)));
    hidden.add(cellKey(this.selectedCell));
    this.renderProjection.copy(this.camera.projectionMatrix);
    this.renderProjection.elements[0]/=1.12;this.renderProjection.elements[5]/=1.12;
    this.renderFrustum.setFromProjectionMatrix(this.renderProjection.multiply(this.camera.matrixWorldInverse));
    let rendered=0;const renderedCells:ArchiveCell[]=[];
    for (let i = 0; i < this.positions.length; i++) {
      const p = this.positions[i];
      const { row, lane } = this.cells[i];
      const slope = field(row + 0.5, lane) - field(row - 0.5, lane);
      this.dummy.position.set(
        p.x - trackX,
        p.y + field(row, lane),
        p.z + entryZ + this.rail.value,
      );
      this.dummy.rotation.set(slope * 0.024 * (1 - detail), 0, 0);
      if(fileAtCell(this.cells[i])===undefined||hidden.has(cellKey(this.cells[i])))continue;
      // Compact the fixed pool into one shared instance buffer. Include a 12%
      // camera guard plus shadow/cassette bounds; do not submit all hidden rows.
      const pos=this.dummy.position;
      if(this.renderFrustum.planes.some(plane=>plane.normal.x*pos.x+plane.normal.y*(pos.y+1.85)+plane.normal.z*pos.z+plane.constant+Math.abs(plane.normal.x)*3+Math.abs(plane.normal.y)*2.8+Math.abs(plane.normal.z)*1.6<0))continue;
      this.dummy.scale.setScalar(1);
      this.dummy.updateMatrix();
      if(!this.freezeMatrices)this.instances[0]?.setMatrixAt(rendered,this.dummy.matrix);
      renderedCells.push(this.cells[i]);rendered++;
    }
    if(!this.freezeMatrices){this.renderCells=renderedCells;for(const inst of this.instances)inst.count=rendered;}
    if(!this.freezeMatrices&&this.instances[0])this.instances[0].instanceMatrix.needsUpdate = true;
    this.matrixMS=performance.now()-matrixStart;
    const previousY=this.model.position.y;
    this.model.position.set(
      chosen.x - trackX,
      chosen.y + field(selectedRow, selectedLane) + this.lift.value,
      chosen.z + entryZ + this.rail.value,
    );
    this.worldVelocity=(this.model.position.y-previousY)/dt;
    // Extraction only changes elevation. Reframing belongs to the camera.
    this.model.rotation.set(
      (field(selectedRow + 0.5, selectedLane) -
        field(selectedRow - 0.5, selectedLane)) *
        0.024 *
        (1 - detail) *
        (1 - ease(this.lift.value / 0.4)),
      cinematic ? 0 : this.rotation,
      0,
    );
    // Measured from frame 787: X edge (382,-204), adjacent row (78,38).
    // The label vertical edge constrains height; the file base is occluded.
    // Do not calibrate field of view from the visible fragment of a file.
    const orbit = ease((shot - 22.6) / 1.6);
    const settle = ease((shot - 24.25) / 2.25);
    const yaw = THREE.MathUtils.degToRad(89 - 22 * orbit - 8 * settle);
    const elevation = THREE.MathUtils.degToRad(
      3 + 40 * ease((shot - 21.96) / 0.22) - 8 * orbit - 16 * settle,
    );
    const span = THREE.MathUtils.lerp(
      THREE.MathUtils.lerp(10.8, 10.3, orbit),
      7.33,
      settle,
    );
    const distance = THREE.MathUtils.lerp(
      THREE.MathUtils.lerp(28 + 7 * orbit, 140, settle),
      72,
      detail,
    );
    const arrayAim = new THREE.Vector3(
      -1.091,
      THREE.MathUtils.lerp(-2.55 + 0.4 * orbit, -0.045, settle),
      THREE.MathUtils.lerp(2.48, 0.481, settle),
    );
    const cameraAim = arrayAim.clone();
    const viewDirection = new THREE.Vector3(
      -Math.sin(yaw) * Math.cos(elevation),
      Math.sin(elevation),
      Math.cos(yaw) * Math.cos(elevation),
    );
    const selectionDirection=viewDirection.clone();
    if (cinematic) {
      const earlyTurn = ease((shot - 27.3) / 1.3);
      const finalTurn = ease((shot - 28.6) / 5.4);
      const shotYaw =
        yaw - THREE.MathUtils.degToRad(9 * earlyTurn + 32 * finalTurn);
      const shotElevation =
        elevation - THREE.MathUtils.degToRad(1.5 * earlyTurn + 3.7 * finalTurn);
      viewDirection.set(
        -Math.sin(shotYaw) * Math.cos(shotElevation),
        Math.sin(shotElevation),
        Math.cos(shotYaw) * Math.cos(shotElevation),
      );
      viewDirection.lerp(selectionDirection,returnProgress).normalize();
    } else {
      viewDirection
        .lerp(new THREE.Vector3(-0.277, 0.238, 0.931), detail)
        .normalize();
    }
    if (cinematic) {
      const pan = ease((shot - 25.4) / 0.95);
      const right = new THREE.Vector3()
        .crossVectors(new THREE.Vector3(0, 1, 0), viewDirection)
        .normalize();
      cameraAim.addScaledVector(
        right,
        -2.05 * (1 - pan) * ease((shot - 24.2) / 0.8),
      );
    }
    if (cinematic && shot >= 25.05) {
      const framing=introFraming(shot);
      const right = new THREE.Vector3()
        .crossVectors(new THREE.Vector3(0, 1, 0), viewDirection)
        .normalize();
      const up = new THREE.Vector3()
        .crossVectors(viewDirection, right)
        .normalize();
      const pixelScale = 1080 / THREE.MathUtils.lerp(span,5.9,detail);
      const anchorAim = this.model.position
        .clone()
        .add(new THREE.Vector3(-2.5, 3.7, 0));
      anchorAim.addScaledVector(
        right,
        -(framing.x - 960) / pixelScale,
      );
      anchorAim.addScaledVector(
        up,
        -(540 - framing.y) / pixelScale,
      );
      cameraAim.lerp(anchorAim,framing.anchor);
      cameraAim.lerp(arrayAim,returnProgress);
    }
    if (!cinematic) {
      const right = new THREE.Vector3()
        .crossVectors(new THREE.Vector3(0, 1, 0), viewDirection)
        .normalize();
      const up = new THREE.Vector3()
        .crossVectors(viewDirection, right)
        .normalize();
      const pixelScale = 1080 / THREE.MathUtils.lerp(span, 5.9, detail);
      const detailAim = this.model.position
        .clone()
        .add(new THREE.Vector3(0, 1.85, 0));
      // The scene now has its own column; center the extracted cassette there.
      detailAim.addScaledVector(up, (560 - 540) / pixelScale);
      cameraAim.lerp(detailAim, detail);
    }
    const cameraPosition = cameraAim
      .clone()
      .addScaledVector(viewDirection, distance);
    if(cinematic){this.pointerGain.value=returnProgress;this.pointerGain.velocity=0;}
    else damp(this.pointerGain,this.targetDetail?0:1,5,dt);
    if (!this.reduced) {
      cameraPosition.x += this.pointer.x * 0.12*this.pointerGain.value;
      cameraPosition.y -= this.pointer.y * 0.12*this.pointerGain.value;
    }
    const cameraBlend = cinematic ? 1 : 1 - Math.exp(-dt * 5);
    this.camera.position.lerp(cameraPosition, cameraBlend);
    this.cameraAim.lerp(cameraAim, cameraBlend);
    this.camera.lookAt(this.cameraAim);
    this.camera.fov = THREE.MathUtils.lerp(
      this.camera.fov,
      THREE.MathUtils.radToDeg(2 * Math.atan(THREE.MathUtils.lerp(span,5.9,detail) / (2 * distance))),
      cameraBlend,
    );
    const fog = this.scene.fog as THREE.Fog;
    // The camera position is damped after its target distance changes. Anchor
    // fog to the rendered camera, or entry puts the array behind the far plane
    // until the camera catches up (a brief white wash that exit never showed).
    const renderedDistance = this.camera.position.distanceTo(this.cameraAim);
    fog.near = renderedDistance + THREE.MathUtils.lerp(5, -1, detail);
    fog.far = renderedDistance + THREE.MathUtils.lerp(25, 12, detail);

    this.camera.updateProjectionMatrix();
    this.camera.updateMatrixWorld();
    let neighborTop = -Infinity;
    const lane = selectedLane,
      row = selectedRow;
    for (let r = row - 5; r <= row + 5; r++) {
      if (r !== row)
        neighborTop = Math.max(neighborTop, -4.6 + field(r, lane) + 3.76);
    }
    for (const o of this.outgoing) {
      if (o.cell.lane === lane && Math.abs(o.cell.row - row) <= 5) {
        neighborTop = Math.max(neighborTop, o.group.position.y + 3.76);
      }
    }
    this.clearance = this.model.position.y - neighborTop;
    this.canInspect =
      !cinematic &&
      Boolean(this.targetDetail) &&
      detail > 0.9 &&
      this.pulseGain < 0.01 &&
      this.clearance > 0.3;
    this.container.dataset.inspection =
      this.returnY !== null
        ? "aligning"
        : this.canInspect
          ? "ready"
          : this.targetDetail
            ? "lifting"
            : "preview";
    const focalPoint = this.model.position
      .clone()
      .add(new THREE.Vector3(0, 2, 0))
      .applyMatrix4(this.camera.matrixWorldInverse);
    const bokehUniforms = this.bokeh.uniforms as Record<
      string,
      { value: number }
    >;
    bokehUniforms.focus.value = -focalPoint.z;
    bokehUniforms.aperture.value =
      (THREE.MathUtils.lerp(0.0003, 0.0008, detail) *
        this.quality.depthOfField) /
      100;
    this.renderer.info.reset();
    const renderStart=performance.now();
    const gl=this.renderer.getContext() as WebGL2RenderingContext;
    if(this.gpuTimer&&this.gpuQuery&&gl.getQueryParameter(this.gpuQuery,gl.QUERY_RESULT_AVAILABLE)){
      if(!gl.getParameter(this.gpuTimer.GPU_DISJOINT_EXT))this.gpuMS=gl.getQueryParameter(this.gpuQuery,gl.QUERY_RESULT)/1e6;
      gl.deleteQuery(this.gpuQuery);this.gpuQuery=null;
    }
    const measuring=!!this.gpuTimer&&!this.gpuQuery;
    if(measuring){this.gpuQuery=gl.createQuery();gl.beginQuery(this.gpuTimer.TIME_ELAPSED_EXT,this.gpuQuery);}
    this.composer.render();
    if(measuring)gl.endQuery(this.gpuTimer.TIME_ELAPSED_EXT);
    this.renderMS=performance.now()-renderStart;
  }
  dispose() {
    this.loaded=false;
    this.ao.noiseTexture?.dispose();this.ao.ssaoMaterial.dispose();
    if(this.gpuQuery){(this.renderer.getContext() as WebGL2RenderingContext).deleteQuery(this.gpuQuery);this.gpuQuery=null;}
    this.composer.dispose();
    for(const pass of this.composer.passes)pass.dispose();
    const geometries=new Set<THREE.BufferGeometry>(),materials=new Set<THREE.Material>(),textures=new Set<THREE.Texture>();
    this.scene.traverse(object => {
      if (!(object instanceof THREE.Mesh)) return;
      geometries.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {materials.add(material);for(const value of Object.values(material))if(value instanceof THREE.Texture)textures.add(value);}
    });
    geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());
    this.labelTexture?.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }
  projectCard(x: number, y: number) {
    this.model.updateMatrixWorld(true);
    const p = this.model
      .localToWorld(new THREE.Vector3(x, y, 0.255))
      .project(this.camera);
    return [(p.x + 1) * 960, (1 - p.y) * 540];
  }
  get decryptionFrame() { return this.decryption.frame; }
  finishDecryption() { this.decryption.finish(); }
  get detailVisibility() {
    return ease((this.detail - 0.25) / 0.55);
  }
  getStats() {
    this.model.updateMatrixWorld(true);
    const project = (x: number, y: number, z: number) => {
      const p = this.model
        .localToWorld(new THREE.Vector3(x, y, z))
        .project(this.camera);
      return [Math.round((p.x + 1) * 960), Math.round((1 - p.y) * 540)];
    };
    return {
      decryption: { ...this.decryption.frame, clarity: this.decryption.clarity },
      topLeft: project(-2.5, 3.7, 0),
      topRight: project(2.5, 3.7, 0),
      labelTopLeft: project(-1.855, 3.27, 0.255),
      labelBottomLeft: project(-1.855, 2.81, 0.255),
      modelPosition: this.model.position
        .toArray()
        .map((v) => Math.round(v * 10000) / 10000),
      cameraPosition: this.camera.position
        .toArray()
        .map((v) => Math.round(v * 10000) / 10000),
      cameraAim:this.cameraAim.toArray(),
      modelRotation:this.model.rotation.toArray().slice(0,3),
      worldVelocity:this.worldVelocity,
      targetDetail:this.targetDetail,
      fieldOfView: this.camera.fov,
      loaded: this.loaded,
      lightingLook:this.lightingLook,
      renderFrame: this.renderer.info.render.frame,
      drawCalls: this.renderer.info.render.calls,
      triangles: this.renderer.info.render.triangles,
      memory:{...this.renderer.info.memory,programs:this.renderer.info.programs?.length},
      matrixMS:this.matrixMS,renderMS:this.renderMS,
      gpuTimerAvailable:!!this.gpuTimer,gpuMS:this.gpuMS,
      footprint:{...this.footprint},recycled:this.recycled,recycleVisible:this.recycleVisible,
      meshes:this.instances.map(i=>({name:(i.material as THREE.Material).name,triangles:(i.geometry.index?.count??i.geometry.attributes.position.count)/3,count:i.count})),
      archiveCount: this.positions.length,
      renderedInstances:this.instances[0]?.count,
      returningFiles: this.outgoing.length,
      selectionPhase: this.pendingPulse
        ? "lifting"
        : this.pulses.length
          ? "wave"
          : "settled",
      pendingPulse: this.pendingPulse ? { ...this.pendingPulse } : null,
      pulses: this.pulses.map((pulse) => ({ ...pulse })),
      referenceTime: Math.round((this.scanTime + 5) * 100) / 100,
      selectedSlot: this.selectedSlot,
      selectedLane: Math.floor(this.selectedSlot / LOOP_ROWS),
      selectedCell: { ...this.selectedCell },
      coordinateOrigin: { ...this.coordinateOrigin },
      poolBounds: {
        minLane: Math.min(...this.cells.map((c) => c.lane)),
        maxLane: Math.max(...this.cells.map((c) => c.lane)),
        minRow: Math.min(...this.cells.map((c) => c.row)),
        maxRow: Math.max(...this.cells.map((c) => c.row)),
      },
      laneFocus: this.laneFocus.value,
      columnCamera: this.columnCamera.value,
      rotation: this.rotation,
      clearance: this.clearance,
      canInspect: this.canInspect,
      returnPhase: this.returnY !== null ? "aligning" : "lowering",
      extraction: Math.round(this.lift.value * 1000) / 1000,
      appearance: Math.round(ease(this.lift.value / 0.4) * 1000) / 1000,
      cameraDetail: Math.round(this.detail * 1000) / 1000,
      idleGain: this.idleGain,
      cameraDistance: this.camera.position.distanceTo(this.cameraAim),
      cameraNear: this.camera.near,
      cameraFar: this.camera.far,
      fogNear: (this.scene.fog as THREE.Fog).near,
      fogFar: (this.scene.fog as THREE.Fog).far,
      returningAppearance: this.outgoing.map((o) => ({
        slot: o.slot,
        cell: { ...o.cell },
        lift: o.lift.value,
        quality: ease(o.lift.value / 0.4),
        rotation: o.group.rotation.y,
        worldY: o.group.position.y,
        phase: o.returnY !== null ? "aligning" : "lowering",
      })),
      rail: Math.round(this.rail.value * 1000) / 1000,
    };
  }
}
