const root = document.getElementById('stack-preview');
const status = root.querySelector('.stack-status');
try {
  const { THREE, OrbitControls } = globalThis.PhoneStackThree;
  const compressed = Uint8Array.from(atob(root.querySelector('[data-tray-meshes]').textContent), c => c.charCodeAt(0));
  const unpacked = await new Response(new Blob([compressed]).stream()
    .pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  const header = new DataView(unpacked);
  const metadataLength = header.getUint32(0, true);
  const { bearing, pitch, postWidth, postDepth, socketDepth, phone, variants } = JSON.parse(
    new TextDecoder().decode(new Uint8Array(unpacked, 4, metadataLength))
  );
  const meshBuffer = new DataView(unpacked, 4 + metadataLength);
  const canvas = root.querySelector('canvas');
  const stage = root.querySelector('.stack-stage');
  const summary = root.querySelector('#stack-dimensions');
  const sideOptions = root.querySelector('[data-short-options]');
  const showPhones = root.querySelector('[data-phones]');
  const showCables = root.querySelector('[data-cables]');
  const openArea = root.querySelector('#stack-open-area');
  const openValue = root.querySelector('#stack-open-value');
  const save = root.querySelector('[data-save-stl]');
  const sizeForm = root.querySelector('#stack-size-form');
  const sizeFields = [...sizeForm.querySelectorAll('input')];
  const sizeError = root.querySelector('#stack-size-error');
  const fitMessage = root.querySelector('#stack-fit');
  const viewButtons = [...root.querySelectorAll('[data-view]')];
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-150, 150, 90, -90, 0.1, 1800);
  const renderer = new THREE.WebGLRenderer({ canvas, alpha:true, antialias:true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const controls = new OrbitControls(camera, canvas);
  controls.enablePan = false;
  controls.minZoom = 0.65;
  controls.maxZoom = 3.5;
  controls.minPolarAngle = 0.002;
  controls.maxPolarAngle = Math.PI * 0.92;

  function themeColor(token, fallback) {
    const swatch = document.createElement('span');
    swatch.style.color = 'var(' + token + ', ' + fallback + ')';
    root.append(swatch);
    const color = new THREE.Color(getComputedStyle(swatch).color);
    swatch.remove();
    return color;
  }

  const trayMaterial = new THREE.MeshStandardMaterial({ roughness:0.64 });
  const phoneMaterial = new THREE.MeshStandardMaterial({ roughness:0.38 });
  const cableMaterial = new THREE.MeshStandardMaterial({ roughness:0.62 });
  const outline = new THREE.LineBasicMaterial({ transparent:true, opacity:0.23 });
  function applyTheme() {
    trayMaterial.color.copy(themeColor('--blue', '#527c93'))
      .lerp(themeColor('--foreground', '#21303a'), 0.46);
    phoneMaterial.color.copy(themeColor('--foreground', '#21303a'))
      .lerp(themeColor('--orange', '#b36e44'), 0.32);
    cableMaterial.color.copy(themeColor('--foreground', '#21303a'));
    outline.color.copy(themeColor('--foreground', '#21303a'));
    render();
  }

  const key = new THREE.DirectionalLight(0xffffff, 2.25);
  key.position.set(-120, 240, 150);
  const fill = new THREE.DirectionalLight(0xffffff, 0.8);
  fill.position.set(150, 70, -200);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x969eab, 1.65), key, fill);

  function meshGroup(geometry, material, threshold) {
    const group = new THREE.Group();
    group.add(new THREE.Mesh(geometry, material));
    group.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry, threshold), outline));
    return group;
  }

  function readMesh(offset) {
    const vertexCount = meshBuffer.getUint16(offset, true);
    const faceCount = meshBuffer.getUint16(offset + 2, true);
    const percent = meshBuffer.getUint16(offset + 4, true) / 10;
    offset += 6;
    const vertices = new Float32Array(vertexCount * 3);
    const triangles = new Uint16Array(faceCount * 3);
    for (let axis = 0; axis < 3; axis++) {
      let current = 0;
      for (let i = axis; i < vertices.length; i += 3) {
        current += meshBuffer.getInt32(offset, true);
        vertices[i] = current / 100000;
        offset += 4;
      }
    }
    for (let axis = 0; axis < 3; axis++) {
      let current = 0;
      for (let i = axis; i < triangles.length; i += 3) {
        current += meshBuffer.getInt16(offset, true);
        triangles[i] = current;
        offset += 2;
      }
    }
    return { vertices, triangles, percent };
  }

  function resizeMesh(mesh, template, size) {
    const axes = [
      [postWidth, template.across - postWidth, size.across - template.across],
      [postDepth, template.depth - postDepth, size.depth - template.depth],
      [socketDepth, pitch, size.pitch - pitch],
    ];
    if (axes.every(([, , delta]) => Math.abs(delta) < 1e-5)) return mesh;
    const vertices = [];
    const triangles = [];
    const index = new Map();
    function add(point) {
      const coordinates = point.map((value, axis) => {
        const [start, stop, delta] = axes[axis];
        const weight = Math.min(1, Math.max(0, (value - start) / (stop - start)));
        return Math.round((value + weight * delta) * 100000) / 100000;
      });
      const key = coordinates.join(',');
      if (!index.has(key)) {
        index.set(key, vertices.length / 3);
        vertices.push(...coordinates);
      }
      return index.get(key);
    }

    // Subdivide faces crossing a resize boundary so the warp preserves their topology.
    function split(face, axis, plane) {
      const side = point => Math.abs(point[axis] - plane) < 1e-5 ? 0 : Math.sign(point[axis] - plane);
      const sides = face.map(side);
      if (!sides.includes(-1) || !sides.includes(1)) return [face];
      const lower = [], upper = [];
      for (let i = 0; i < face.length; i++) {
        const j = (i + 1) % face.length;
        const a = face[i], b = face[j];
        if (sides[i] <= 0) lower.push(a);
        if (sides[i] >= 0) upper.push(a);
        if (sides[i] * sides[j] >= 0) continue;
        const t = (plane - a[axis]) / (b[axis] - a[axis]);
        const point = a.map((value, k) => k === axis ? plane : value + t * (b[k] - value));
        lower.push(point);
        upper.push(point);
      }
      return [lower, upper];
    }

    for (let i = 0; i < mesh.triangles.length; i += 3) {
      let faces = [[...mesh.triangles.subarray(i, i + 3)].map(n => [...mesh.vertices.subarray(n * 3, n * 3 + 3)])];
      for (const [axis, [start, stop, delta]] of axes.entries()) {
        if (Math.abs(delta) < 1e-5) continue;
        for (const plane of [start, stop]) faces = faces.flatMap(face => split(face, axis, plane));
      }
      for (const face of faces) {
        const corners = face.map(add).filter((value, n, ids) => value !== ids[(n + ids.length - 1) % ids.length]);
        if (corners.length === 3) {
          triangles.push(...corners);
        } else if (corners.length > 3) {
          const center = face[0].map((_, axis) => face.reduce((total, point) => total + point[axis], 0) / face.length);
          const middle = add(center);
          for (let j = 0; j < corners.length; j++) triangles.push(middle, corners[j], corners[(j + 1) % corners.length]);
        }
      }
    }
    return { vertices:new Float32Array(vertices), triangles:new Uint16Array(triangles), percent:mesh.percent };
  }

  function trayGeometry(variant, mesh) {
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(mesh.vertices.length);
    for (let i = 0; i < positions.length; i += 3) {
      positions[i] = mesh.vertices[i] - variant.across / 2;
      positions[i + 1] = mesh.vertices[i + 2];
      positions[i + 2] = -mesh.vertices[i + 1];
    }
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(mesh.triangles, 1));
    const faces = geometry.toNonIndexed();
    faces.computeVertexNormals();
    geometry.dispose();
    return faces;
  }

  function phoneGeometry(across, insertion) {
    const radius = 6;
    const half = across / 2;
    const shape = new THREE.Shape()
      .moveTo(-half + radius, 0).lineTo(half - radius, 0)
      .quadraticCurveTo(half, 0, half, radius)
      .lineTo(half, insertion - radius)
      .quadraticCurveTo(half, insertion, half - radius, insertion)
      .lineTo(-half + radius, insertion)
      .quadraticCurveTo(-half, insertion, -half, insertion - radius)
      .lineTo(-half, radius).quadraticCurveTo(-half, 0, -half + radius, 0);
    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth:phone.thickness, bevelEnabled:false, curveSegments:6
    });
    geometry.rotateX(-Math.PI / 2);
    return geometry;
  }

  const plugGeometry = new THREE.BoxGeometry(14, 9, 19);
  const wireGeometry = new THREE.CylinderGeometry(1.8, 1.8, 35, 16)
    .rotateX(Math.PI / 2);
  function chargingCable(x, y, z, angle) {
    const cable = new THREE.Group();
    const plug = new THREE.Mesh(plugGeometry, cableMaterial);
    plug.position.z = 9.5;
    const wire = new THREE.Mesh(wireGeometry, cableMaterial);
    wire.position.z = 36.5;
    cable.add(plug, wire);
    cable.position.set(x, y, z);
    cable.rotation.y = angle;
    return cable;
  }

  const assemblies = {};
  for (const [name, variant] of Object.entries(variants)) {
    const trays = new THREE.Group();
    const phones = new THREE.Group();
    const cables = new THREE.Group();
    const assembly = new THREE.Group();
    const mobile = meshGroup(phoneGeometry(
      name === 'long' ? phone.length : phone.width,
      name === 'long' ? phone.width : phone.length
    ), phoneMaterial, 50);
    for (let layer = 0; layer < 2; layer++) {
      const handset = mobile.clone();
      handset.position.set(0, bearing + layer * pitch + 0.02,
        (name === 'long' ? phone.width : phone.length) - variant.insertion);
      phones.add(handset);
    }
    const socketHeight = bearing + phone.thickness / 2;
    if (name === 'long') {
      const socketDepth = phone.width / 2 - variant.insertion;
      cables.add(
        chargingCable(-phone.length / 2, socketHeight, socketDepth, -Math.PI / 2),
        chargingCable(phone.length / 2, socketHeight + pitch, socketDepth, Math.PI / 2)
      );
    } else {
      cables.add(
        chargingCable(0, socketHeight, -variant.insertion, Math.PI),
        chargingCable(0, socketHeight + pitch, phone.length - variant.insertion, 0)
      );
    }
    assembly.add(trays, phones, cables);
    scene.add(assembly);
    assemblies[name] = { assembly, phones, trays, cables };
  }

  function setTray(assembly, template, size, offset) {
    const meshKey = [offset, size.across, size.depth, size.pitch].join(':');
    if (assembly.meshKey === meshKey) return;
    if (assembly.trays.children.length) {
      for (const part of assembly.trays.children[0].children) part.geometry.dispose();
      assembly.trays.clear();
    }
    assembly.meshKey = meshKey;
    assembly.mesh = resizeMesh(readMesh(offset), template, size);
    const tray = meshGroup(trayGeometry(size, assembly.mesh), trayMaterial, 28);
    for (let layer = 0; layer < 3; layer++) {
      const shelf = tray.clone();
      shelf.position.y = layer * size.pitch;
      assembly.trays.add(shelf);
    }
    for (const accessories of [assembly.phones, assembly.cables]) {
      accessories.position.z = template.insertion - size.insertion;
      accessories.children[1].position.y += size.pitch - (assembly.accessoryPitch ?? pitch);
    }
    assembly.accessoryPitch = size.pitch;
  }

  function downloadMesh() {
    const { vertices, triangles } = active.mesh;
    const file = new ArrayBuffer(84 + 50 * triangles.length / 3);
    const data = new DataView(file);
    data.setUint32(80, triangles.length / 3, true);
    const [a, b, c, edge, normal] = Array.from({ length:5 }, () => new THREE.Vector3());
    for (let i = 0; i < triangles.length; i += 3) {
      a.fromArray(vertices, triangles[i] * 3);
      b.fromArray(vertices, triangles[i + 1] * 3);
      c.fromArray(vertices, triangles[i + 2] * 3);
      normal.subVectors(b, a).cross(edge.subVectors(c, a)).normalize();
      let offset = 84 + 50 * i / 3;
      for (const vector of [normal, a, b, c]) {
        for (const component of [vector.x, vector.y, vector.z]) {
          data.setFloat32(offset, component, true);
          offset += 4;
        }
      }
    }
    const url = URL.createObjectURL(new Blob([file], { type:'model/stl' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = ['phone-stack', root.dataset.variant, root.dataset.pattern, openArea.value, ...active.external].join('-') + '.stl';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  let active;
  let direction;
  const sizes = new Map();
  let view = 'iso';
  const fmt = number => Number(number.toFixed(3));
  const views = {
    iso: () => direction === 'long' ? [0.58, 0.80, 1] : [1, 0.82, 0.77],
    top: () => [0, 1, 0.002],
    opening: () => [0, 0.085, 1],
  };

  function render() { renderer.render(scene, camera); }

  function resize(fit = false) {
    const width = stage.clientWidth, height = stage.clientHeight;
    if (!width || !height || !active) return;
    renderer.setSize(width, height, false);
    const aspect = width / height;
    if (fit) {
      const bounds = new THREE.Box3().setFromObject(active.trays);
      if (active.phones.visible) bounds.expandByObject(active.phones);
      if (active.cables.visible) bounds.expandByObject(active.cables);
      bounds.getCenter(controls.target);
      camera.position.copy(controls.target).add(new THREE.Vector3(...views[view]()).normalize().multiplyScalar(450));
      camera.zoom = 1;
      controls.update();
      active.bounds = bounds;
    }
    camera.updateMatrixWorld();
    const extent = new THREE.Vector3();
    const { min, max } = active.bounds;
    const center = controls.target.clone().applyMatrix4(camera.matrixWorldInverse);
    for (const x of [min.x,max.x]) for (const y of [min.y,max.y]) for (const z of [min.z,max.z]) {
      const corner = new THREE.Vector3(x,y,z).applyMatrix4(camera.matrixWorldInverse).sub(center);
      extent.x = Math.max(extent.x,Math.abs(corner.x));
      extent.y = Math.max(extent.y,Math.abs(corner.y));
    }
    const heightLimit = Math.max(extent.y, extent.x / aspect, 42) * 1.18;
    camera.left = -heightLimit * aspect;
    camera.right = heightLimit * aspect;
    camera.top = heightLimit;
    camera.bottom = -heightLimit;
    camera.updateProjectionMatrix();
    render();
  }

  function fillSizeFields(variant, values) {
    const original = direction === 'long' ? [variant.across, variant.depth] : [variant.depth, variant.across];
    sizeFields.forEach((field, position) => {
      if (position < 2) {
        field.min = Math.ceil(original[position] * .65);
        field.max = 250;
      }
      field.value = values[position];
      field.removeAttribute('aria-describedby');
      field.removeAttribute('aria-invalid');
    });
    sizeError.hidden = true;
  }

  function update(fit = true, syncSize = false) {
    direction = root.querySelector('[name="stack-direction"]:checked').value;
    const mode = root.querySelector('[name="stack-short-mode"]:checked').value;
    const name = direction === 'long' ? 'long' : mode;
    const pattern = root.querySelector('[name="stack-pattern"]:checked').value;
    sideOptions.hidden = direction === 'long';
    const changedVariant = active !== assemblies[name];
    active = assemblies[name];
    for (const item of Object.values(assemblies)) item.assembly.visible = item === active;
    const variant = variants[name];
    const original = direction === 'long'
      ? [variant.across, variant.depth, variant.height]
      : [variant.depth, variant.across, variant.height];
    const external = sizes.get(name) ?? original;
    if (changedVariant || syncSize) fillSizeFields(variant, external);
    const size = {
      across:external[direction === 'long' ? 0 : 1],
      depth:external[direction === 'long' ? 1 : 0],
      pitch:external[2] - (variant.height - pitch),
    };
    size.insertion = size.depth - (variant.depth - variant.insertion);
    active.external = external;
    const clearance = fmt(size.pitch - bearing);
    const phoneAcross = direction === 'long' ? phone.length : phone.width;
    const phoneTravel = direction === 'long' ? phone.width : phone.length;
    const usableAcross = size.across - 2 * postWidth;
    const overhang = name === 'overhang' ? Math.max(0, phoneTravel - size.insertion) : 0;
    const fits = phoneAcross <= usableAcross && phone.thickness <= clearance &&
      (name === 'overhang' ? overhang < phoneTravel / 2 : phoneTravel <= size.insertion);
    active.phones.visible = showPhones.checked && fits;
    showCables.disabled = !active.phones.visible;
    active.cables.visible = active.phones.visible && showCables.checked;
    setTray(active, variant, size, variant.patterns[pattern][openArea.value]);
    openValue.value = active.mesh.percent + '%';
    openArea.setAttribute('aria-valuetext', openValue.value);
    const inside = direction === 'long' ? [usableAcross, size.insertion] : [size.insertion, usableAcross];
    const innerLabel = name === 'overhang' ? '承托范围' : '内部净尺寸';
    summary.textContent = '单层外尺寸 ' + external.map(fmt).join(' × ') + ' mm · ' + innerLabel + ' ' +
      [...inside, clearance].map(fmt).join(' × ') + ' mm';
    fitMessage.hidden = !showPhones.checked || (fits && name !== 'overhang');
    fitMessage.textContent = fits
      ? '示例手机外悬 ' + fmt(overhang) + ' mm'
      : '当前尺寸放不下示例手机（' + [phone.length, phone.width, phone.thickness].join(' × ') + ' mm）。';
    canvas.setAttribute('aria-label', variant.label +
      '，沿' + (direction === 'long' ? '长' : '短') + '边取机。三层模型，顶层镂空可见。');
    root.dataset.variant = name;
    root.dataset.pattern = pattern;
    resize(fit);
  }

  controls.addEventListener('change', render);
  root.querySelectorAll('input[type=radio], input[type=checkbox]').forEach(input => {
    const geometryOnly = input.name === 'stack-pattern';
    input.addEventListener('change', () => update(!geometryOnly));
  });
  openArea.addEventListener('input', () => update(false));
  function applySize() {
    if (!sizeForm.reportValidity()) return;
    sizes.set(root.dataset.variant, sizeFields.map(field => field.valueAsNumber));
    update(true, true);
  }
  root.querySelector('[data-size-apply]').addEventListener('click', applySize);
  sizeForm.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.isComposing || !sizeFields.includes(event.target)) return;
    event.preventDefault();
    applySize();
  });
  sizeForm.addEventListener('invalid', event => {
    const field = event.target;
    const firstInvalid = sizeFields.find(input => !input.validity.valid);
    if (field !== firstInvalid) return;
    sizeError.textContent = '请输入 ' + field.min + '–' + field.max + ' mm 的' +
      { length:'长度', width:'宽度', height:'高度' }[field.name] + '，最多三位小数。';
    sizeError.hidden = false;
    field.setAttribute('aria-invalid', 'true');
    field.setAttribute('aria-describedby', sizeError.id);
  }, true);
  sizeForm.addEventListener('input', () => {
    sizeError.hidden = true;
    for (const field of sizeFields) {
      field.removeAttribute('aria-describedby');
      field.removeAttribute('aria-invalid');
    }
  });
  root.querySelector('[data-size-reset]').addEventListener('click', () => {
    sizes.delete(root.dataset.variant);
    update(true, true);
  });
  root.querySelector('[data-export]').hidden = !root.hasAttribute('data-standalone');
  save.addEventListener('click', downloadMesh);
  for (const button of viewButtons) {
    button.addEventListener('click', () => {
      view = button.dataset.view;
      for (const option of viewButtons) {
        const selected = option === button;
        option.classList.toggle('btn-primary', selected);
        option.setAttribute('aria-pressed', String(selected));
      }
      resize(true);
    });
  }
  new ResizeObserver(() => resize()).observe(stage);
  new MutationObserver(applyTheme).observe(document.documentElement, {
    attributes:true, attributeFilter:['class','style','data-theme']
  });
  applyTheme();
  update();
  status.hidden = true;
  root.dataset.ready = 'true';
} catch (error) {
  status.textContent = '三维预览加载失败。请确认浏览器已启用 WebGL 2。';
  console.error(error);
}
