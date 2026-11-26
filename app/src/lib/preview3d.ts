import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export interface Spawn {
  xyz: [number, number, number];
  heading_deg: number;
}

export interface PreviewHandle {
  triangles: number;
  dispose(): void;
}

/** Local map coordinates (x east, y north, z up) to the 3D file's (x right, y up, -z north). */
export function toScene(xyz: [number, number, number]): [number, number, number] {
  return [xyz[0], xyz[2], -xyz[1]];
}

/** Camera position a little behind and above the car's starting point, looking along the road. */
export function cameraStart(spawn: Spawn, back = 60, up = 28): [number, number, number] {
  const [x, y, z] = toScene(spawn.xyz);
  const h = (spawn.heading_deg * Math.PI) / 180;
  // heading 0 = north = -z in the scene, 90 = east = +x
  return [x - Math.sin(h) * back, y + up, z + Math.cos(h) * back];
}

export async function startPreview(
  canvas: HTMLCanvasElement,
  glb: ArrayBuffer,
  spawn: Spawn | null,
): Promise<PreviewHandle> {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x9fc4e8);
  scene.add(new THREE.HemisphereLight(0xdfefff, 0x4a5a3a, 1.6));
  const sun = new THREE.DirectionalLight(0xfff2dd, 2.2);
  sun.position.set(-0.5, 1, 0.35);
  scene.add(sun);

  const gltf = await new GLTFLoader().parseAsync(glb, "");
  let triangles = 0;
  gltf.scene.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    const geometry = mesh.geometry;
    if (!geometry.getAttribute("normal")) geometry.computeVertexNormals();
    const material = mesh.material as THREE.MeshStandardMaterial;
    material.flatShading = false;
    material.needsUpdate = true;
    triangles += (geometry.index ? geometry.index.count : geometry.getAttribute("position").count) / 3;
  });
  scene.add(gltf.scene);

  const box = new THREE.Box3().setFromObject(gltf.scene);
  const size = box.getSize(new THREE.Vector3()).length();
  const camera = new THREE.PerspectiveCamera(55, 1, 0.5, Math.max(size * 3, 2000));
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.495; // never look from below the ground
  if (spawn) {
    controls.target.set(...toScene(spawn.xyz));
    camera.position.set(...cameraStart(spawn));
  } else {
    const c = box.getCenter(new THREE.Vector3());
    controls.target.copy(c);
    camera.position.set(c.x, c.y + size * 0.4, c.z + size * 0.4);
  }

  const resize = () => {
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  let running = true;
  const frame = () => {
    if (!running) return;
    controls.update();
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  };
  frame();

  // lets the automated click-through test see that something was really drawn
  (window as unknown as { __preview?: unknown }).__preview = { drawn: () => renderer.info.render.triangles };

  return {
    triangles,
    dispose() {
      running = false;
      observer.disconnect();
      controls.dispose();
      gltf.scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (!mesh.isMesh) return;
        mesh.geometry.dispose();
        (mesh.material as THREE.Material).dispose();
      });
      renderer.dispose();
    },
  };
}
