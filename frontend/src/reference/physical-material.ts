import * as THREE from 'three';

/** Preserve glTF PBR inputs while creating a real physical shader material. */
export function physicalMaterial(source: THREE.Material): THREE.MeshPhysicalMaterial {
  if (source instanceof THREE.MeshPhysicalMaterial) return source.clone();
  if (!(source instanceof THREE.MeshStandardMaterial)) {
    throw new TypeError(`Archive surface requires a PBR material: ${source.type}`);
  }
  const physical = new THREE.MeshPhysicalMaterial();
  // Physical.copy expects physical-only fields, which Standard does not have.
  // Copy the common PBR base explicitly; retain Physical's valid optical defaults.
  THREE.MeshStandardMaterial.prototype.copy.call(physical, source);
  return physical;
}

/** Read-only evidence used by the opt-in fixture diagnostic panel. */
export function materialSnapshot(material: THREE.Material) {
  const physical = material instanceof THREE.MeshPhysicalMaterial;
  return {
    name: material.name,
    type: material.type,
    physical,
    ...(physical ? {
      transmission: material.transmission,
      thickness: material.thickness,
      roughness: material.roughness,
      ior: material.ior,
      attenuationDistance: Number.isFinite(material.attenuationDistance)
        ? material.attenuationDistance : 'infinite',
      clearcoat: material.clearcoat,
    } : {}),
  };
}
