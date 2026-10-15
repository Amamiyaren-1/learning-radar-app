import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {CardAppearance} from '../src/reference/appearance.ts';

test('the accepted archive asset is preserved without a substitute mesh', async () => {
  const bytes = await readFile(new URL('../public/assets/archive-cassette.glb',import.meta.url));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),
    'dda42b9b3b471d11c69820a64084d254a64b27761287ab0e35dd8387ec0a0bed');
});

test('the accepted cover loads as physical glass and retains its reveal behavior', async () => {
  const bytes = await readFile(new URL('../public/assets/archive-cassette.glb',import.meta.url));
  const gltf = await new GLTFLoader().parseAsync(
    bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),'');
  let cover;
  gltf.scene.traverse(o=>{
    if(o instanceof THREE.Mesh && o.material.name.replace(/\.\d+$/,'')==='Frosted_Polymer') cover=o;
  });
  assert.ok(cover);
  // The original glTF extensions create the real class; no cast or promotion.
  assert.ok(cover.material instanceof THREE.MeshPhysicalMaterial);
  const material=cover.material.clone();
  material.transmission=.9;material.thickness=.12;material.attenuationDistance=2;
  const mesh=new THREE.Mesh(cover.geometry,material);
  mesh.userData.surface='Frosted_Polymer';
  const group=new THREE.Group();group.add(mesh);
  const appearance=new CardAppearance();appearance.register('Frosted_Polymer',material);
  appearance.prepare(group);appearance.apply(group,1);appearance.setClarity(group,0);
  assert.equal(mesh.material.transmission,.9);
  const shader={uniforms:{},vertexShader:THREE.ShaderLib.physical.vertexShader,
    fragmentShader:THREE.ShaderLib.physical.fragmentShader};
  mesh.material.onBeforeCompile(shader);
  assert.ok(shader.fragmentShader.includes('glassRevealAtHeight'));
  appearance.setClarity(group,1);
  assert.equal(shader.uniforms.archiveClarity.value,1);
  assert.equal(mesh.material.transmission,.985);
  assert.ok(Math.abs(mesh.material.thickness-.018)<1e-9);
});
