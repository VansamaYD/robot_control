import * as THREE from 'three';
import { STLLoader } from '/assets/STLLoader.js';
import { OrbitControls } from '/assets/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const parameters = new URLSearchParams(location.search);
const requestedRevision = parameters.get('snapshot');
if (parameters.has('capture')) document.body.classList.add('capture');
window.__ready = false;
window.__errors = [];

try {
  const bootstrap = await (await fetch('/api/bootstrap')).json();
  const source = bootstrap.code.split('\n');
  source.forEach((line, i) => {
    const row = document.createElement('div');
    row.dataset.line = i + 1;
    const number = document.createElement('span');
    number.className = 'line-no'; number.textContent = i + 1;
    row.append(number, document.createTextNode(line));
    $('source').append(row);
  });
  const model = bootstrap.model;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#101a26');
  const container = $('viewport');
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.append(renderer.domElement);
  const camera = new THREE.PerspectiveCamera(39, 1, .005, 20);
  camera.up.set(0, 0, 1);
  camera.position.set(.70, -.98, .68);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, -.12, .13);
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xd7e7ff, 0x45586b, 3.2));
  const key = new THREE.DirectionalLight(0xfff0db, 4);
  key.position.set(-.4, -.3, 1.5); scene.add(key);
  const fill = new THREE.DirectionalLight(0x9bc5ff, 2);
  fill.position.set(.5, .6, .8); scene.add(fill);
  const material = (color, extras = {}) => new THREE.MeshStandardMaterial({ color, roughness: .45, metalness: .25, ...extras });
  const steel = material(0xb4c5d1, { metalness: .75, roughness: .3, side: THREE.DoubleSide });
  const teal = material(0x369d91);
  function mesh(geometry, mat, parent = scene) {
    const result = new THREE.Mesh(geometry, mat); parent.add(result); return result;
  }
  const floor = mesh(new THREE.PlaneGeometry(2, 2), material(0x182534, { metalness: 0, roughness: .95 }));
  floor.position.z = -.025;
  const grid = new THREE.GridHelper(1.4, 28, 0x345268, 0x233748);
  grid.rotation.x = Math.PI / 2; grid.position.z = -.024; scene.add(grid);
  // Same spherical-cap pot profile as the checked v03 collision approximation.
  const potRadius = (.2 ** 2 + .12 ** 2) / (2 * .12);
  const potProfile = Array.from({ length: 49 }, (_, i) => {
    const z = .12 * i / 48;
    return new THREE.Vector2(Math.sqrt(Math.max(0, potRadius ** 2 - (potRadius - z) ** 2)), z);
  });
  const pot = mesh(new THREE.LatheGeometry(potProfile, 96), material(0x657d8e, { transparent: true, opacity: .42, side: THREE.DoubleSide }));
  pot.rotation.x = Math.PI / 2;
  const rim = mesh(new THREE.TorusGeometry(.2, .004, 10, 100), steel); rim.position.z = .12;
  const juiceRadius = Math.sqrt(potRadius ** 2 - (potRadius - .08) ** 2);
  const juice = mesh(new THREE.CircleGeometry(juiceRadius, 96), material(0x703a1e, { metalness: .1, roughness: .22 })); juice.position.z = .08;
  const chicken = mesh(new THREE.SphereGeometry(1, 48, 24), material(0xb87635, { roughness: .65 }));
  chicken.scale.set(.13, .09, .075); chicken.position.set(0, .05, .075);
  for (const sign of [-1, 1]) {
    const leg = mesh(new THREE.SphereGeometry(1, 24, 16), material(0xa96b31));
    leg.scale.set(.035, .055, .035); leg.position.set(sign * .08, .085, .065);
  }
  const pedestal = mesh(new THREE.BoxGeometry(.16, .15, .08), material(0x243747)); pedestal.position.set(0, -.4, .04);
  const baseGroup = new THREE.Group();
  baseGroup.position.fromArray(model.base.slice(0, 3)); baseGroup.rotation.z = model.base[3]; scene.add(baseGroup);
  const links = new Map(model.links.map(link => [link.name, new THREE.Group()]));
  const pivots = new Map();
  baseGroup.add(links.get('base_link'));
  function applyOrigin(group, origin) {
    group.position.fromArray(origin.xyz);
    group.quaternion.setFromEuler(new THREE.Euler(...origin.rpy, 'ZYX'));
  }
  for (const joint of model.joints) {
    const origin = new THREE.Group(); applyOrigin(origin, joint);
    const rotation = new THREE.Group(); origin.add(rotation);
    links.get(joint.parent).add(origin);
    rotation.add(links.get(joint.child));
    pivots.set(joint.name, { rotation, axis: new THREE.Vector3(...joint.axis).normalize() });
  }
  const loader = new STLLoader();
  const geometryCache = new Map();
  let visualCount = 0;
  await Promise.all(model.links.flatMap(link => link.visuals.map(async visual => {
    if (!geometryCache.has(visual.mesh)) geometryCache.set(visual.mesh, loader.loadAsync('/assets/' + visual.mesh));
    const geometry = await geometryCache.get(visual.mesh);
    geometry.computeVertexNormals();
    const motor = visual.mesh.includes('sts3215');
    const display = mesh(geometry, motor ? material(0x303f4d) : teal, links.get(link.name));
    applyOrigin(display, visual); display.scale.fromArray(visual.scale);
    visualCount++;
  })));
  const toolRoot = links.get('gripper_link');
  function rod(a, b, radius) {
    const start = new THREE.Vector3(...a), end = new THREE.Vector3(...b);
    const delta = end.clone().sub(start);
    const result = mesh(new THREE.CylinderGeometry(radius, radius, delta.length(), 14), steel, toolRoot);
    result.position.copy(start.add(end).multiplyScalar(.5));
    result.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize());
  }
  rod([0, 0, 0], [0, 0, -.09], .004);
  rod([0, 0, -.09], model.tool_offset_m, .004);
  const bowlGeometry = new THREE.SphereGeometry(.025, 32, 20, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2);
  bowlGeometry.rotateZ(-Math.PI / 2);
  const bowl = mesh(bowlGeometry, steel, toolRoot); bowl.position.fromArray(model.tool_offset_m);
  const spoonRim = mesh(new THREE.TorusGeometry(.025, .0015, 8, 48), steel, toolRoot);
  spoonRim.rotation.y = Math.PI / 2; spoonRim.position.fromArray(model.tool_offset_m);
  const tcp = new THREE.Object3D(); tcp.position.fromArray(model.tool_offset_m); toolRoot.add(tcp);
  const tcpMarker = mesh(new THREE.SphereGeometry(.004, 12, 8), material(0x85ffdc));
  const trace = mesh(new THREE.SphereGeometry(.002, 8, 8), material(0xffffff)); trace.visible = false;
  const trajectory = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x6cf0cd, transparent: true, opacity: .65 }));
  scene.add(trajectory);
  const history = [];
  let current = null;
  const labels = { ready: '就绪 · 暂停', planning: '计算并检查轨迹', running: '执行中', succeeded: '流程完成', cancelled: '已取消', failed: '执行失败' };
  function drawState(state) {
    model.joint_order.forEach((name, i) => {
      const pivot = pivots.get(name);
      pivot.rotation.quaternion.setFromAxisAngle(pivot.axis, state.q_rad[i]);
    });
    scene.updateMatrixWorld(true);
    const position = tcp.getWorldPosition(new THREE.Vector3());
    const error = position.distanceTo(new THREE.Vector3(...state.tcp_m));
    const linkErrors = model.joint_order.map((name, i) => {
      const child = model.joints.find(joint => joint.name === name).child;
      const actual = links.get(child).matrixWorld.elements;
      const expected = new THREE.Matrix4().set(...state.link_matrices[i].flat()).elements;
      return Math.max(...actual.map((value, j) => Math.abs(value - expected[j])));
    });
    tcpMarker.position.copy(position);
    if (!current || state.tick !== current.tick) {
      history.push(position.clone()); if (history.length > 4000) history.shift();
      trajectory.geometry.dispose(); trajectory.geometry = new THREE.BufferGeometry().setFromPoints(history);
    }
    current = state;
    $('status').textContent = labels[state.status] ?? state.status;
    $('connection').textContent = `${visualCount} 个正式网格 · 服务在线`;
    $('frame').textContent = `快照 #${state.state_revision} · tick ${state.tick}`;
    $('time').textContent = `${state.simulation_time_s.toFixed(2)} s`;
    $('action').textContent = state.action?.name ?? '待机 · 初始暂停';
    $('hash').textContent = state.code_hash.slice(0, 12);
    $('tcp').textContent = state.tcp_m.map(value => (value * 1000).toFixed(1)).join(', ');
    $('joints').replaceChildren(...state.joint_names.map((name, i) => {
      const row = document.createElement('tr');
      for (const value of [name, (state.q_rad[i] * 180 / Math.PI).toFixed(3) + '°']) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      return row;
    }));
    document.querySelectorAll('#source div').forEach(row => row.classList.toggle('active', Number(row.dataset.line) === state.action?.source_line));
    const busy = ['planning', 'running'].includes(state.job?.status);
    $('run').disabled = busy;
    $('pause').disabled = !busy || state.paused;
    $('resume').disabled = !busy || !state.paused;
    $('step').disabled = !busy || !state.paused;
    $('cancel').disabled = !busy;
    $('error').textContent = state.job?.error ?? '';
    window.__frameState = { ...state, rendered_tcp_m: position.toArray(), tcp_error_m: error,
      maximum_link_matrix_error: Math.max(...linkErrors), visual_mesh_count: visualCount,
      camera: { position: camera.position.toArray(), target: controls.target.toArray(), up: camera.up.toArray(), fov: camera.fov } };
  }
  window.__applySnapshot = drawState; // Test seam: render-only, does not write service state.
  async function update() {
    const response = await fetch('/api/state' + (requestedRevision ? '?revision=' + encodeURIComponent(requestedRevision) : ''));
    const state = await response.json();
    if (!response.ok) throw new Error(state.error);
    drawState(state);
  }
  async function post(path, data) {
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': bootstrap.token }, body: JSON.stringify(data) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    return result;
  }
  const act = (callback) => async () => {
    try { $('error').textContent = ''; await callback(); await update(); }
    catch (error) { $('error').textContent = error.message; window.__errors.push(error.message); }
  };
  $('run').onclick = act(() => post('/api/run', { idempotency_key: crypto.randomUUID() }));
  for (const [id, command] of [['pause', 'pause'], ['resume', 'resume'], ['step', 'step_tick'], ['cancel', 'cancel']]) {
    $(id).onclick = act(() => post('/api/control', { command }));
  }
  $('speed').onchange = act(() => post('/api/speed', { speed: Number($('speed').value) }));
  function resize() {
    const { width, height } = container.getBoundingClientRect();
    renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container); resize();
  function render() { controls.update(); renderer.render(scene, camera); requestAnimationFrame(render); }
  await update(); controls.update(); renderer.render(scene, camera);
  window.__ready = true;
  render();
  if (!requestedRevision) {
    setInterval(() => update().catch(error => {
      $('connection').textContent = '状态连接中断';
      window.__errors.push(error.message);
    }), 50);
  }
} catch (error) {
  $('connection').textContent = '加载失败'; $('error').textContent = error.stack;
  window.__errors.push(String(error));
}
