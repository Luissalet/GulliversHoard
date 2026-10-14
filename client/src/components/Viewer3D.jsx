import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

/**
 * The 3D preview of a build (preview.glb). Nodes are named "<group>__<part>": the group is a tile id (or "model"), the part
 * terrain / buildings / water / roads / green / frame). `hidden` is a set of part names to hide; `explode` (0..1) pulls the
 * tiles apart; `view` is "iso" | "top" | "front" (changing it moves the camera).
 */
export default function Viewer3D({ url, hidden, explode = 0, view = "iso", onInfo, labels }) {
  const holder = useRef(null);
  const s = useRef({});
  const [state, setState] = useState("loading");

  useEffect(() => {
    const el = holder.current;
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.92;
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x10140f);
    const camera = new THREE.PerspectiveCamera(35, 1, 0.5, 8000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    scene.add(new THREE.HemisphereLight(0xf4efe4, 0x2c2c24, 0.75));
    const sun = new THREE.DirectionalLight(0xfff4e2, 1.9);
    sun.position.set(-1, 1.6, 1.1);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xbfd4ff, 0.45);
    fill.position.set(1.2, 0.6, -0.8);
    scene.add(fill);
    const root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;              // the model is Z-up, three is Y-up
    scene.add(root);
    s.current = { renderer, scene, camera, controls, root, groups: [], centre: new THREE.Vector3(), size: 100 };

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    resize();
    let frame = 0;
    const loop = () => {
      frame = requestAnimationFrame(loop);
      controls.update();
      renderer.render(scene, camera);
    };
    loop();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      root.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose?.(); });
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  useEffect(() => {
    if (!url) return undefined;
    let cancelled = false;
    setState("loading");
    const loader = new GLTFLoader();
    loader.load(url, (gltf) => {
      if (cancelled) return;
      const { root } = s.current;
      while (root.children.length) root.remove(root.children[0]);
      const groups = new Map();
      const content = gltf.scene;
      content.updateMatrixWorld(true);
      const meshes = [];
      content.traverse((o) => { if (o.isMesh) meshes.push(o); });
      let triangles = 0;
      for (const mesh of meshes) {
        const name = [mesh.name, mesh.parent?.name, mesh.geometry?.name].find((n) => n && n.includes("__")) || "model__terrain";
        const [group, part] = name.split("__");
        const geo = mesh.geometry.clone().applyMatrix4(mesh.matrixWorld);
        geo.computeVertexNormals();
        triangles += (geo.index ? geo.index.count : geo.attributes.position.count) / 3;
        const color = mesh.material?.color ? mesh.material.color.clone() : new THREE.Color(0xd8ccb4);
        const material = new THREE.MeshStandardMaterial({ color, roughness: part === "water" ? 0.35 : 0.85, metalness: 0.0, flatShading: true });
        const m = new THREE.Mesh(geo, material);
        m.userData.part = part;
        if (!groups.has(group)) groups.set(group, new THREE.Group());
        groups.get(group).add(m);
      }
      const box = new THREE.Box3();
      for (const g of groups.values()) { root.add(g); }
      root.updateMatrixWorld(true);
      box.setFromObject(root);
      const centre = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      // per-group centre (in model coordinates) for the exploded view
      const groupInfo = [...groups.entries()].map(([id, g]) => {
        const b = new THREE.Box3().setFromObject(g);
        const c = b.getCenter(new THREE.Vector3());
        return { id, g, c };
      });
      s.current.groups = groupInfo;
      s.current.centre = centre;
      s.current.size = Math.max(size.x, size.z, 1);
      s.current.modelCentre = new THREE.Box3().setFromObject(root).getCenter(new THREE.Vector3());
      setCamera(view);
      applyHidden(hidden);
      applyExplode(explode);
      setState("ready");
      onInfo?.({ triangles: Math.round(triangles), groups: groupInfo.map((x) => x.id) });
    }, undefined, () => { if (!cancelled) setState("error"); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  useEffect(() => { applyHidden(hidden); }, [hidden]);
  useEffect(() => { applyExplode(explode); }, [explode]);
  useEffect(() => { if (state === "ready") setCamera(view); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [view]);

  function applyHidden(set) {
    const { root } = s.current;
    if (!root) return;
    root.traverse((o) => { if (o.isMesh) o.visible = !(set && set.has(o.userData.part)); });
  }

  function applyExplode(amount) {
    const { groups, modelCentre, size } = s.current;
    if (!groups || groups.length < 2 || !modelCentre) {
      groups?.forEach(({ g }) => g.position.set(0, 0, 0));
      return;
    }
    // world-space offsets converted to the root's (Z-up) frame: world x -> root x, world z -> root -y
    for (const { g, c } of groups) {
      const dx = c.x - modelCentre.x;
      const dz = c.z - modelCentre.z;
      const len = Math.hypot(dx, dz) || 1;
      const k = amount * size * 0.12;
      g.position.set((dx / len) * k, -(dz / len) * k, 0);
    }
  }

  function setCamera(v) {
    const { camera, controls, centre, size } = s.current;
    if (!camera) return;
    const d = size * 1.25;
    if (v === "top") camera.position.set(centre.x, centre.y + d * 1.45, centre.z + 0.001);
    else if (v === "front") camera.position.set(centre.x, centre.y + d * 0.25, centre.z + d * 1.35);
    else camera.position.set(centre.x - d * 0.55, centre.y + d * 0.9, centre.z + d * 1.0);
    controls.target.copy(centre);
    controls.update();
  }

  return (
    <div className="viewer">
      <div ref={holder} className="viewer-canvas" role="img" aria-label={labels?.viewer || "3D"} />
      {state === "loading" && <div className="viewer-overlay"><span className="spinner" /> {labels?.loading}</div>}
      {state === "error" && <div className="viewer-overlay">{labels?.error}</div>}
    </div>
  );
}
