import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {physicalMaterial, materialSnapshot} from '../src/reference/physical-material.ts';
import {CardAppearance} from '../src/reference/appearance.ts';

test('standard PBR is promoted to a real physical material without losing inputs', () => {
  const texture = new THREE.Texture();
  const source = new THREE.MeshStandardMaterial({color:'#ada195', roughness:.37, metalness:.16, map:texture});
  source.name = 'legacy-standard';
  const material = physicalMaterial(source);
  assert.ok(material instanceof THREE.MeshPhysicalMaterial);
  assert.equal(material.type, 'MeshPhysicalMaterial');
  assert.equal(material.name, source.name);
  assert.equal(material.color.getHex(), source.color.getHex());
  assert.equal(material.roughness, .37);
  assert.equal(material.map, texture);
  assert.equal(material.ior, 1.5);
  material.transmission = .9;
  assert.equal(source.transmission, undefined);
  assert.throws(() => physicalMaterial(new THREE.MeshBasicMaterial()), /PBR material/);
});

test('existing physical glTF extension properties survive cloning', () => {
  const source = new THREE.MeshPhysicalMaterial({transmission:.9, thickness:.12, ior:1.46,
    attenuationColor:'#eee6df', attenuationDistance:2, clearcoat:.3});
  const material = physicalMaterial(source);
  assert.notEqual(material, source);
  for (const key of ['transmission','thickness','ior','attenuationDistance','clearcoat']) {
    assert.equal(material[key], source[key]);
  }
  assert.notEqual(material.attenuationColor, source.attenuationColor);
  assert.equal(material.attenuationColor.getHex(), source.attenuationColor.getHex());
});

test('actual public GLB loads physical layers and supports the frosted-to-clear shader', async () => {
  const bytes = await readFile(new URL('../public/assets/archive-cassette.glb', import.meta.url));
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const gltf = await new GLTFLoader().parseAsync(buffer, '');
  const appearance = new CardAppearance();
  const group = new THREE.Group();
  const names = [];
  gltf.scene.traverse(mesh => {
    if (!(mesh instanceof THREE.Mesh)) return;
    names.push(mesh.material.name);
    assert.ok(mesh.material instanceof THREE.MeshPhysicalMaterial, mesh.material.name);
    const material = physicalMaterial(mesh.material);
    appearance.register(material.name, material);
    const copy = new THREE.Mesh(mesh.geometry, material);
    copy.userData.surface = material.name;
    group.add(copy);
    const normals = mesh.geometry.attributes.normal;
    for (let i=0;i<normals.count;i++) {
      const n = new THREE.Vector3().fromBufferAttribute(normals, i);
      assert.ok(Math.abs(n.length()-1)<.001, 'unit normals on rolled edges');
    }
  });
  for (const name of ['Frosted_Polymer','Ivory_Edges','Internal_Ceramic','Optical_Diffuser',
    'Optical_Glass_Body','Optical_Glass_Edge','Optical_Bridge_Glass','Titanium_Fasteners']) {
    assert.ok(names.includes(name), name);
  }
  assert.ok(names.length >= 14, 'independent optical/mechanical layers');
  appearance.prepare(group);
  appearance.apply(group, 1);
  const cover = group.children.find(o=>o.userData.surface==='Frosted_Polymer');
  appearance.setClarity(group, 0);
  assert.equal(cover.material.transmission, .9);
  assert.equal(cover.material.thickness, .12);
  const shader = {uniforms:{},vertexShader:THREE.ShaderLib.physical.vertexShader,
    fragmentShader:THREE.ShaderLib.physical.fragmentShader};
  cover.material.onBeforeCompile(shader);
  assert.ok(shader.fragmentShader.includes('archiveTransmissionLod'));
  assert.ok(shader.fragmentShader.includes('glassRevealAtHeight'));
  assert.equal(shader.uniforms.archiveClarity.value, 0);
  appearance.setClarity(group, .5);
  assert.equal(shader.uniforms.archiveClarity.value, .5);
  assert.ok(cover.material.transmission > .9 && cover.material.transmission < .985);
  appearance.setClarity(group, 1);
  assert.equal(shader.uniforms.archiveClarity.value, 1);
  assert.equal(cover.material.transmission, .985);
  assert.ok(Math.abs(cover.material.thickness-.018)<1e-9);
  assert.equal(cover.material.attenuationDistance, 8);
  assert.equal(materialSnapshot(cover.material).physical, true);
});
