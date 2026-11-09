// Founder Studio - live 3D arena of buyer bots (three.js r160).
import * as THREE from "../vendor/three.module.min.js";

// Every scene colour comes from the CSS design tokens (styles.css :root), so one token set themes page and arena.
const rootStyle = getComputedStyle(document.documentElement);
const token = (name, fallback) => rootStyle.getPropertyValue(name).trim() || fallback;
const tokenHex = (name, fallback) => parseInt(token(name, fallback).replace("#", ""), 16);
const THEME = {
  sceneBg: tokenHex("--scene-bg", "#eef1f5"),
  floor: tokenHex("--scene-floor", "#e4e8ee"),
  grid: tokenHex("--scene-grid", "#c9d0da"),
  gridMinor: tokenHex("--scene-grid-minor", "#d9dee5"),
  stall: tokenHex("--scene-stall", "#cbd3de"),
  stallTop: tokenHex("--scene-stall-top", "#9aa6b6"),
  accent: tokenHex("--accent", "#1d4ed8"),
  sky: tokenHex("--scene-sky", "#ffffff"),
  plaza: tokenHex("--scene-plaza", "#f6f1e6"),
  path: tokenHex("--scene-path", "#ddd2bf"),
  awning: tokenHex("--scene-awning", "#fffdf8"),
  leaf: tokenHex("--scene-leaf", "#5f8a4a"),
  trunk: tokenHex("--scene-trunk", "#8a5a2b"),
  stone: tokenHex("--scene-stone", "#d9cdb8"),
  ink: tokenHex("--ink", "#1b1a17"),
  ground: tokenHex("--scene-ground", "#b9c1cc"),
  labelBg: token("--scene-label-bg", "rgba(255,255,255,0.96)"),
  labelFg: token("--scene-label-fg", "#14181f"),
  labelSub: token("--scene-label-sub", "#4b5563"),
  font: token("--font-ui", "Segoe UI, Arial, sans-serif"),
};
const SEGMENT_COLORS = [1, 2, 3, 4, 5, 6].map((i) => tokenHex(`--seg-${i}`, "#475569"));
const BUY_COLOR = tokenHex("--buy", "#15803d");
const PASS_COLOR = tokenHex("--pass", "#c0262d");
const THINK_COLOR = tokenHex("--think", "#b45309");
// Waiting line behind the stall (between the stall and the far edge of the frame).
const QUEUE_Z = -6.2;
// Depth of the BUY / PASS zone centres; kept shallow so overlays at the bottom never cover a pile.
const ZONE_Z = 3.6;
const ZONE_R = 4.4;
const QUEUE_WIDTH = 15;

function labelTexture(lines, { width = 512, height = 256, bg = THEME.labelBg, fg = THEME.labelFg, accent = "#" + THEME.accent.toString(16).padStart(6, "0"), big = false } = {}) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = bg;
  const r = 28;
  ctx.beginPath();
  ctx.roundRect(4, 4, width - 8, height - 8, r);
  ctx.fill();
  ctx.strokeStyle = accent;
  ctx.lineWidth = 6;
  ctx.stroke();
  ctx.fillStyle = fg;
  ctx.textAlign = "center";
  if (big) {
    ctx.textBaseline = "middle";
    ctx.font = `600 120px ${THEME.font}`;
    ctx.fillStyle = accent;
    ctx.fillText(lines[0], width / 2, height / 2 + 6, width - 40);
    const bigTexture = new THREE.CanvasTexture(canvas);
    bigTexture.colorSpace = THREE.SRGBColorSpace;
    bigTexture.anisotropy = 4;
    return bigTexture;
  }
  lines.forEach((line, i) => {
    ctx.font = i === 0 ? `600 52px ${THEME.font}` : `32px ${THEME.font}`;
    ctx.fillStyle = i === 0 ? fg : THEME.labelSub;
    ctx.fillText(line, width / 2, 90 + i * 64, width - 40);
  });
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

const hex = (value) => "#" + value.toString(16).padStart(6, "0");

function zoneDisc(color, label, x, z, radius) {
  const accent = "#" + color.toString(16).padStart(6, "0");
  const group = new THREE.Group();
  const disc = new THREE.Mesh(
    new THREE.CircleGeometry(radius, 64),
    new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.16, roughness: 0.95 })
  );
  disc.rotation.x = -Math.PI / 2;
  disc.position.y = 0.03;
  disc.receiveShadow = true;
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(radius - 0.14, radius, 96),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.75 })
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.04;
  const signMaterial = new THREE.MeshBasicMaterial({
    map: labelTexture([label], { accent, big: true }),
    transparent: true,
  });
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(3.6, 1.8), signMaterial);
  sign.position.set(0, 2.6, -radius - 0.2);
  sign.userData.billboard = true;
  group.userData.sign = sign;
  group.add(disc, ring, sign);
  group.position.set(x, 0, z);
  // Redraws the sign with a live count, e.g. "BUY · 4".
  group.userData.setCount = (count) => {
    signMaterial.map?.dispose();
    signMaterial.map = labelTexture([count ? `${label} · ${count}` : label], { accent, big: true });
    signMaterial.needsUpdate = true;
  };
  return group;
}

// The waiting line behind the stall: a soft floor strip with a "QUEUE" sign, so the idle
// scene already reads left to right as queue -> stall -> BUY / PASS.
// A closed neighbour stall: platform, shuttered counter and a striped awning in a muted segment colour.
function neighbourStall(x, z, turn, color) {
  const group = new THREE.Group();
  const wood = new THREE.MeshStandardMaterial({ color: THEME.stall, roughness: 0.8 });
  const shutter = new THREE.MeshStandardMaterial({ color: THEME.stallTop, roughness: 0.7 });
  const muted = new THREE.Color(color).lerp(new THREE.Color(THEME.awning), 0.45);
  const counter = new THREE.Mesh(new THREE.BoxGeometry(2.6, 1.0, 1.0), wood);
  counter.position.y = 0.5;
  const front = new THREE.Mesh(new THREE.BoxGeometry(2.4, 0.9, 0.04), shutter);
  front.position.set(0, 1.45, 0.48);
  group.add(counter, front);
  for (const side of [-1.2, 1.2]) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 2.3, 10), shutter);
    post.position.set(side, 1.15, -0.4);
    group.add(post);
  }
  for (let i = 0; i < 6; i++) {
    const slat = new THREE.Mesh(
      new THREE.BoxGeometry(2.9 / 6, 0.05, 1.4),
      new THREE.MeshStandardMaterial({ color: i % 2 ? THEME.awning : muted, roughness: 0.85 })
    );
    slat.position.set(-1.45 + (i + 0.5) * (2.9 / 6), 2.32, 0.2);
    slat.rotation.x = 0.26;
    group.add(slat);
  }
  group.traverse((mesh) => {
    mesh.castShadow = true;
    mesh.receiveShadow = true;
  });
  group.position.set(x, 0, z);
  group.rotation.y = turn;
  return group;
}

function queueArea(width, z) {
  const group = new THREE.Group();
  const strip = new THREE.Mesh(
    new THREE.PlaneGeometry(width, 3.4),
    new THREE.MeshStandardMaterial({ color: THEME.path, roughness: 0.95 })
  );
  strip.rotation.x = -Math.PI / 2;
  strip.position.y = 0.025;
  strip.receiveShadow = true;
  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.PlaneGeometry(width, 3.4)),
    new THREE.LineBasicMaterial({ color: THEME.stallTop, transparent: true, opacity: 0.35 })
  );
  edge.rotation.x = -Math.PI / 2;
  edge.position.y = 0.03;
  const signMaterial = new THREE.MeshBasicMaterial({
    map: labelTexture(["QUEUE"], { accent: hex(THEME.stallTop), big: true }),
    transparent: true,
  });
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(3.0, 1.5), signMaterial);
  sign.position.set(width / 2 + 1.9, 1.2, 0);
  sign.userData.billboard = true;
  group.userData.sign = sign;
  // Rope-line posts at the four corners.
  const brass = new THREE.MeshStandardMaterial({ color: THEME.stallTop, roughness: 0.4, metalness: 0.3 });
  for (const [px, pz] of [[-width / 2, -1.7], [width / 2, -1.7], [-width / 2, 1.7], [width / 2, 1.7]]) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.09, 0.9, 12), brass);
    post.position.set(px, 0.45, pz);
    post.castShadow = true;
    group.add(post);
  }
  const rope = new THREE.MeshStandardMaterial({ color: THEME.accent, roughness: 0.7 });
  for (const pz of [-1.7, 1.7]) {
    const line = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, width, 8), rope);
    line.rotation.z = Math.PI / 2;
    line.position.set(0, 0.82, pz);
    group.add(line);
  }
  group.add(strip, edge, sign);
  group.position.set(0, 0, z);
  group.userData.setCount = (count) => {
    signMaterial.map?.dispose();
    signMaterial.map = labelTexture([count ? `QUEUE · ${count}` : "QUEUE"], { accent: hex(THEME.stallTop), big: true });
    signMaterial.needsUpdate = true;
  };
  return group;
}

class Bot {
  constructor(persona, color, spawn) {
    this.persona = persona;
    this.state = "queued";
    this.group = new THREE.Group();
    this.group.userData.botId = persona.id;
    const bodyMaterial = new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.15 });
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.34, 0.55, 6, 16), bodyMaterial);
    body.position.y = 0.72;
    body.castShadow = true;
    // Head: a light tint of the segment colour, so a buyer reads as one coloured figure.
    const headColor = new THREE.Color(color).lerp(new THREE.Color(0xfffdf8), 0.62);
    const head = new THREE.Mesh(
      new THREE.SphereGeometry(0.3, 24, 16),
      new THREE.MeshStandardMaterial({ color: headColor, roughness: 0.45, metalness: 0.05 })
    );
    head.castShadow = true;
    head.position.y = 1.45;
    const visor = new THREE.Mesh(
      new THREE.BoxGeometry(0.42, 0.14, 0.12),
      new THREE.MeshStandardMaterial({ color: THEME.ink, roughness: 0.25 })
    );
    visor.position.set(0, 1.47, 0.24);
    this.eyeMaterial = new THREE.MeshBasicMaterial({ color: 0xfff1d6 });
    const eyeLeft = new THREE.Mesh(new THREE.SphereGeometry(0.035, 8, 8), this.eyeMaterial);
    const eyeRight = eyeLeft.clone();
    eyeLeft.position.set(-0.09, 1.47, 0.31);
    eyeRight.position.set(0.09, 1.47, 0.31);
    const antenna = new THREE.Mesh(
      new THREE.CylinderGeometry(0.015, 0.015, 0.25),
      new THREE.MeshStandardMaterial({ color: THEME.stallTop })
    );
    antenna.position.y = 1.85;
    this.bulbMaterial = new THREE.MeshBasicMaterial({ color: THEME.accent });
    this.bulb = new THREE.Mesh(new THREE.SphereGeometry(0.07, 12, 12), this.bulbMaterial);
    this.bulb.position.y = 2.0;
    this.ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.66, 0.09, 10, 48, Math.PI * 1.5),
      new THREE.MeshBasicMaterial({ color: THINK_COLOR, transparent: true, opacity: 0.95 })
    );
    this.ring.position.y = 2.35;
    this.ring.visible = false;
    const shadow = new THREE.Mesh(
      new THREE.CircleGeometry(0.42, 24),
      new THREE.MeshBasicMaterial({ color: THEME.ink, transparent: true, opacity: 0.12 })
    );
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.y = 0.015;
    this.decisionMaterial = new THREE.MeshBasicMaterial({ color: BUY_COLOR, transparent: true, opacity: 0.7 });
    this.decisionDisc = new THREE.Mesh(new THREE.RingGeometry(0.38, 0.46, 32), this.decisionMaterial);
    this.decisionDisc.rotation.x = -Math.PI / 2;
    this.decisionDisc.position.y = 0.03;
    this.decisionDisc.visible = false;
    this.rig = new THREE.Group();
    this.rig.add(body, head, visor, eyeLeft, eyeRight, antenna, this.bulb, this.ring);
    // Small per-buyer variety (stable per id): a cap, a tote or a scarf, and a slight height
    // difference, so a crowd reads as people rather than clones. Purely visual.
    let hash = 0;
    for (const ch of String(persona.id)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    body.scale.y = 0.92 + (hash % 5) * 0.04;
    const trim = new THREE.MeshStandardMaterial({ color: new THREE.Color(color).multiplyScalar(0.55), roughness: 0.7 });
    const variant = hash % 4;
    if (variant === 1) {
      const brim = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.36, 0.04, 20), trim);
      brim.position.y = 1.66;
      const crown = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.25, 0.16, 20), trim);
      crown.position.y = 1.75;
      this.rig.add(brim, crown);
    } else if (variant === 2) {
      const tote = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.36, 0.3), trim);
      tote.position.set(0.4, 0.62, 0);
      tote.castShadow = true;
      this.rig.add(tote);
    } else if (variant === 3) {
      const scarf = new THREE.Mesh(new THREE.TorusGeometry(0.24, 0.07, 8, 20), trim);
      scarf.rotation.x = Math.PI / 2;
      scarf.position.y = 1.16;
      this.rig.add(scarf);
    }
    this.group.add(this.rig, shadow, this.decisionDisc);
    this.group.position.copy(spawn);
    this.target = spawn.clone();
    this.speed = 3.2;
    this.phase = Math.random() * Math.PI * 2;
    this.progress = 0;
  }

  walkTo(point) {
    this.target = point.clone();
    this.state = this.state === "queued" ? "walking" : this.state;
  }

  setThinking() {
    this.state = "thinking";
    this.ring.visible = true;
    this.decisionMaterial.color.setHex(THINK_COLOR);
    this.decisionDisc.visible = true;
    this.bulbMaterial.color.setHex(THINK_COLOR);
    this.eyeMaterial.color.setHex(THINK_COLOR);
  }

  setDecision(buys) {
    this.state = buys ? "buy" : "pass";
    this.ring.visible = false;
    const color = buys ? BUY_COLOR : PASS_COLOR;
    this.bulbMaterial.color.setHex(color);
    this.eyeMaterial.color.setHex(color);
    this.decisionMaterial.color.setHex(color);
    this.decisionMaterial.opacity = 0.7;
    this.decisionDisc.visible = true;
    this.jump = buys ? 1 : 0;
  }

  update(dt, time) {
    const position = this.group.position;
    const delta = new THREE.Vector3().subVectors(this.target, position);
    delta.y = 0;
    const distance = delta.length();
    const moving = distance > 0.05;
    if (moving) {
      const step = Math.min(distance, this.speed * dt);
      position.addScaledVector(delta.normalize(), step);
      const angle = Math.atan2(delta.x, delta.z);
      this.group.rotation.y += ((angle - this.group.rotation.y + Math.PI * 3) % (Math.PI * 2) - Math.PI) * Math.min(1, dt * 10);
      this.rig.position.y = Math.abs(Math.sin(time * 10 + this.phase)) * 0.12;
      this.rig.rotation.z = Math.sin(time * 10 + this.phase) * 0.06;
    } else {
      this.rig.position.y = Math.sin(time * 2 + this.phase) * 0.03;
      this.rig.rotation.z *= 0.9;
    }
    if (this.state === "thinking") {
      this.decisionMaterial.opacity = 0.55 + Math.sin(time * 6) * 0.35;
      this.ring.rotation.z -= dt * 5;
      this.ring.rotation.y = Math.sin(time * 2) * 0.3;
      this.bulb.scale.setScalar(1 + Math.sin(time * 12) * 0.3);
    } else {
      this.bulb.scale.setScalar(1);
    }
    if (this.jump > 0) {
      this.jump = Math.max(0, this.jump - dt * 1.4);
      this.rig.position.y += Math.sin((1 - this.jump) * Math.PI * 3) * 0.35 * this.jump;
    }
    return moving;
  }
}

// Reviewer bots apply one board lens each. They look different from buyers on purpose:
// light body, accent cap and a clipboard, and an accent ring on the floor.
const REVIEW_COLORS = { fix: THEME.accent, ok: BUY_COLOR, you: THEME.stallTop };

class ReviewerBot extends Bot {
  constructor(reviewer, spawn) {
    super({ id: reviewer.id, name: reviewer.name }, THEME.awning, spawn);
    this.reviewer = reviewer;
    const accent = new THREE.MeshStandardMaterial({ color: THEME.accent, roughness: 0.4, metalness: 0.1 });
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.31, 0.33, 0.1, 24), accent);
    cap.position.y = 1.7;
    const brim = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.03, 0.18), accent);
    brim.position.set(0, 1.66, 0.3);
    const board = new THREE.Mesh(
      new THREE.BoxGeometry(0.38, 0.48, 0.04),
      new THREE.MeshStandardMaterial({ color: THEME.panel ?? 0xfffdf8, roughness: 0.6 })
    );
    board.position.set(0.18, 0.85, 0.36);
    board.rotation.x = -0.35;
    const clip = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.05, 0.05), accent);
    clip.position.set(0.18, 1.08, 0.3);
    this.rig.add(cap, brim, board, clip);
    this.homeRing = new THREE.Mesh(
      new THREE.RingGeometry(0.4, 0.5, 32),
      new THREE.MeshBasicMaterial({ color: THEME.accent, transparent: true, opacity: 0.8 })
    );
    this.homeRing.rotation.x = -Math.PI / 2;
    this.homeRing.position.y = 0.03;
    this.group.add(this.homeRing);
    this.state = "idle";
  }

  setVerdict(verdict) {
    this.state = "reviewed";
    this.ring.visible = false;
    const color = REVIEW_COLORS[verdict] ?? REVIEW_COLORS.ok;
    this.bulbMaterial.color.setHex(color);
    this.eyeMaterial.color.setHex(color);
    this.homeRing.material.color.setHex(color);
    this.jump = verdict === "fix" ? 1 : 0;
  }

  clearVerdict() {
    this.state = "idle";
    this.bulbMaterial.color.setHex(THEME.accent);
    this.eyeMaterial.color.setHex(0xfff1d6);
    this.homeRing.material.color.setHex(THEME.accent);
  }
}

export class Arena {
  constructor(container, { onSelect } = {}) {
    this.container = container;
    this.onSelect = onSelect;
    this.bots = new Map();
    this.labels = new Map();
    this.reviewers = new Map();
    this.buySlots = 0;
    this.passSlots = 0;
    this.stallSlots = 0;
    this.speedFactor = 1;
    this.dragging = false;
    this.clock = new THREE.Clock();
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(this.renderer.domElement);
    this.labelLayer = document.createElement("div");
    this.labelLayer.className = "bot-labels";
    container.appendChild(this.labelLayer);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(THEME.sceneBg);
    this.scene.fog = new THREE.Fog(THEME.sceneBg, 34, 70);
    this.camera = new THREE.PerspectiveCamera(48, 1, 0.1, 200);
    this.orbit = { azimuth: 0.0, elevation: 0.86, radius: 24, target: new THREE.Vector3(0, 1.4, 1.5) };
    this.updateCamera();

    // Warm afternoon light: sky fill, one sun with soft shadows, a cool bounce from the other side.
    this.scene.add(new THREE.HemisphereLight(THEME.sky, THEME.ground, 1.6));
    const sun = new THREE.DirectionalLight(0xffe4bf, 1.25);
    sun.position.set(9, 18, 11);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = -24;
    sun.shadow.camera.right = 24;
    sun.shadow.camera.top = 24;
    sun.shadow.camera.bottom = -24;
    sun.shadow.camera.far = 60;
    sun.shadow.bias = -0.0006;
    sun.shadow.radius = 4;
    this.scene.add(sun);
    const bounce = new THREE.DirectionalLight(0xf0e2d0, 0.4);
    bounce.position.set(-10, 8, -6);
    this.scene.add(bounce);

    // Ground: paper floor, a lighter market square, and worn paths queue -> stall -> zones.
    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(60, 96),
      new THREE.MeshStandardMaterial({ color: THEME.floor, roughness: 1 })
    );
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    this.scene.add(ground);
    // The square and its worn paths are painted into one texture: separate coplanar path planes
    // shaded unevenly and read as dark slabs, while one textured disc stays flat and calm.
    const PLAZA_R = 17.5;
    const plazaCanvas = document.createElement("canvas");
    plazaCanvas.width = plazaCanvas.height = 1024;
    const pc = plazaCanvas.getContext("2d");
    const toPx = (x, z) => [((x / PLAZA_R + 1) / 2) * 1024, ((z / PLAZA_R + 1) / 2) * 1024];
    const pxPerUnit = 1024 / (2 * PLAZA_R);
    pc.fillStyle = hex(THEME.plaza);
    pc.fillRect(0, 0, 1024, 1024);
    pc.lineCap = "round";
    const paintPath = (x1, z1, x2, z2, width) => {
      const [ax, ay] = toPx(x1, z1);
      const [bx, by] = toPx(x2, z2);
      // A thin worn edge in the path tone, then a pale walkway inside it: reads as a footpath,
      // not as a dark slab across the square.
      for (const [grow, alpha, tone] of [[1.12, 0.55, hex(THEME.path)], [1, 1, "#efe6d5"]]) {
        pc.globalAlpha = alpha;
        pc.strokeStyle = tone;
        pc.lineWidth = width * grow * pxPerUnit;
        pc.beginPath();
        pc.moveTo(ax, ay);
        pc.lineTo(bx, by);
        pc.stroke();
      }
      pc.globalAlpha = 1;
    };
    pc.strokeStyle = hex(THEME.path);
    pc.globalAlpha = 0.35;
    pc.lineWidth = 2;
    for (let ring = 3; ring < PLAZA_R; ring += 2.2) {
      pc.beginPath();
      pc.arc(512, 512, ring * pxPerUnit, 0, Math.PI * 2);
      pc.stroke();
    }
    let seed = 7;
    const rand = () => {
      seed = (seed * 16807) % 2147483647;
      return seed / 2147483647;
    };
    pc.fillStyle = hex(THEME.path);
    for (let i = 0; i < 900; i++) {
      pc.globalAlpha = 0.12 + rand() * 0.18;
      pc.fillRect(rand() * 1024, rand() * 1024, 2 + rand() * 3, 2 + rand() * 3);
    }
    pc.globalAlpha = 1;
    // A band of pavers just inside the edge: the square reads as a built place, not a painted disc.
    const paverRing = PLAZA_R - 0.9;
    const pavers = Math.round((2 * Math.PI * paverRing) / 0.9);
    pc.fillStyle = hex(THEME.path);
    for (let row = 0; row < 2; row++) {
      for (let i = 0; i < pavers; i++) {
        const angle = ((i + row * 0.5) / pavers) * Math.PI * 2;
        const radius = (paverRing - row * 0.55) * pxPerUnit;
        pc.save();
        pc.translate(512 + Math.cos(angle) * radius, 512 + Math.sin(angle) * radius);
        pc.rotate(angle);
        pc.globalAlpha = 0.28 + rand() * 0.12;
        pc.fillRect(-0.24 * pxPerUnit, -0.4 * pxPerUnit, 0.48 * pxPerUnit, 0.8 * pxPerUnit);
        pc.restore();
      }
    }
    pc.globalAlpha = 1;
    paintPath(0, QUEUE_Z + 1.7, 0, -1.2, 2.2);
    paintPath(1.8, 1.6, 9, ZONE_Z - 1, 1.6);
    paintPath(-1.8, 1.6, -9, ZONE_Z - 1, 1.6);
    const plazaTexture = new THREE.CanvasTexture(plazaCanvas);
    plazaTexture.colorSpace = THREE.SRGBColorSpace;
    plazaTexture.anisotropy = 4;
    const plaza = new THREE.Mesh(
      new THREE.CircleGeometry(PLAZA_R, 96),
      new THREE.MeshStandardMaterial({ map: plazaTexture, roughness: 0.95 })
    );
    plaza.rotation.x = -Math.PI / 2;
    plaza.position.y = 0.005;
    plaza.receiveShadow = true;
    this.scene.add(plaza);
    const curb = new THREE.Mesh(
      new THREE.CylinderGeometry(PLAZA_R + 0.18, PLAZA_R + 0.18, 0.16, 128, 1, true),
      new THREE.MeshStandardMaterial({ color: THEME.stone, roughness: 0.9, side: THREE.DoubleSide })
    );
    curb.position.y = 0.08;
    curb.receiveShadow = true;
    const curbTop = new THREE.Mesh(
      new THREE.RingGeometry(PLAZA_R, PLAZA_R + 0.36, 128),
      new THREE.MeshStandardMaterial({ color: THEME.stone, roughness: 0.9 })
    );
    curbTop.rotation.x = -Math.PI / 2;
    curbTop.position.y = 0.161;
    curbTop.receiveShadow = true;
    this.scene.add(curb, curbTop);
    this.buildDecor();

    this.buildStall();
    this.buyZone = zoneDisc(BUY_COLOR, "BUY", 9, ZONE_Z, ZONE_R);
    this.passZone = zoneDisc(PASS_COLOR, "PASS", -9, ZONE_Z, ZONE_R);
    this.scene.add(this.buyZone, this.passZone);
    this.queueZone = queueArea(QUEUE_WIDTH, QUEUE_Z);
    this.scene.add(this.queueZone);
    this.previewBots = [];
    this.selectedId = null;
    this.hoveredId = null;
    this.view = "overview";
    this.orbitGoal = null;
    this.lastInteraction = 0;
    this.selectRing = new THREE.Mesh(
      new THREE.RingGeometry(0.55, 0.7, 40),
      new THREE.MeshBasicMaterial({ color: THEME.accent, transparent: true, opacity: 0.9 })
    );
    this.selectRing.rotation.x = -Math.PI / 2;
    this.selectRing.position.y = 0.05;
    this.selectRing.visible = false;
    this.scene.add(this.selectRing);
    // Hover: a quiet ink ring under the buyer the pointer is on.
    this.hoverRing = new THREE.Mesh(
      new THREE.RingGeometry(0.58, 0.66, 40),
      new THREE.MeshBasicMaterial({ color: THEME.ink ?? 0x2b2620, transparent: true, opacity: 0.55 })
    );
    this.hoverRing.rotation.x = -Math.PI / 2;
    this.hoverRing.position.y = 0.045;
    this.hoverRing.visible = false;
    this.scene.add(this.hoverRing);
    // Selection: a floating accent pin above the chosen buyer, readable from any camera angle.
    this.selectPin = new THREE.Mesh(
      new THREE.ConeGeometry(0.22, 0.42, 20),
      new THREE.MeshStandardMaterial({ color: THEME.accent, roughness: 0.5, emissive: THEME.accent, emissiveIntensity: 0.25 })
    );
    this.selectPin.rotation.x = Math.PI;
    this.selectPin.visible = false;
    this.scene.add(this.selectPin);
    this.lastDecidedId = null;

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.bindControls();
    this.resize();
    new ResizeObserver(() => this.resize()).observe(container);
    this.renderer.setAnimationLoop(() => this.tick());
  }

  buildStall() {
    // A small market stall: wooden platform and counter, two posts, striped awning, the pitch sign above.
    this.stall = new THREE.Group();
    const wood = new THREE.MeshStandardMaterial({ color: THEME.stall, roughness: 0.75 });
    const darkWood = new THREE.MeshStandardMaterial({ color: THEME.stallTop, roughness: 0.6 });
    const base = new THREE.Mesh(new THREE.CylinderGeometry(2.4, 2.6, 0.22, 48), darkWood);
    base.position.y = 0.11;
    base.receiveShadow = true;
    base.castShadow = true;
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(2.62, 2.78, 96),
      new THREE.MeshBasicMaterial({ color: THEME.accent, transparent: true, opacity: 0.85 })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.02;
    const counter = new THREE.Mesh(new THREE.BoxGeometry(3.0, 1.0, 1.1), wood);
    counter.position.set(0, 0.72, 0.2);
    counter.castShadow = true;
    counter.receiveShadow = true;
    const top = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.1, 1.3), darkWood);
    top.position.set(0, 1.27, 0.2);
    top.castShadow = true;
    // Goods on the counter: three jars in the accent and segment colours.
    const goods = [THEME.accent, SEGMENT_COLORS[0], SEGMENT_COLORS[2]];
    goods.forEach((color, i) => {
      const jar = new THREE.Mesh(
        new THREE.CylinderGeometry(0.16, 0.16, 0.34, 16),
        new THREE.MeshStandardMaterial({ color, roughness: 0.4 })
      );
      jar.position.set(-0.7 + i * 0.7, 1.49, 0.35);
      jar.castShadow = true;
      this.stall.add(jar);
    });
    for (const x of [-1.45, 1.45]) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 2.7, 12), darkWood);
      post.position.set(x, 1.45, -0.35);
      post.castShadow = true;
      this.stall.add(post);
    }
    const stripes = 7;
    for (let i = 0; i < stripes; i++) {
      const slat = new THREE.Mesh(
        new THREE.BoxGeometry(3.4 / stripes, 0.06, 1.7),
        new THREE.MeshStandardMaterial({ color: i % 2 ? THEME.awning : THEME.accent, roughness: 0.8 })
      );
      slat.position.set(-1.7 + (i + 0.5) * (3.4 / stripes), 2.78, 0.35);
      slat.rotation.x = 0.28;
      slat.castShadow = true;
      this.stall.add(slat);
    }
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 1.2), darkWood);
    pole.position.set(0, 3.3, -0.45);
    this.signMaterial = new THREE.MeshBasicMaterial({ map: labelTexture(["Your product", "pitch goes here"]), transparent: true });
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(5.0, 2.5), this.signMaterial);
    sign.position.set(0, 4.15, -0.3);
    sign.userData.billboard = true;
    this.stall.add(base, ring, counter, top, pole, sign);
    // String lights along the front of the awning: warm bulbs on a sagging cable.
    const bulbMaterial = new THREE.MeshBasicMaterial({ color: 0xffd68a });
    const cablePoints = [];
    for (let i = 0; i <= 20; i++) {
      const t = i / 20;
      cablePoints.push(new THREE.Vector3(-1.6 + 3.2 * t, 2.42 - Math.sin(Math.PI * t) * 0.22, 1.08));
    }
    this.stall.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(cablePoints),
      new THREE.LineBasicMaterial({ color: THEME.ink, transparent: true, opacity: 0.55 })
    ));
    for (let i = 1; i < 10; i++) {
      const t = i / 10;
      const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.06, 10, 8), bulbMaterial);
      bulb.position.set(-1.6 + 3.2 * t, 2.34 - Math.sin(Math.PI * t) * 0.22, 1.08);
      this.stall.add(bulb);
    }
    // Stock beside the stall: stacked crates on one side, sacks on the other. Clear of the queue path.
    const crateWood = new THREE.MeshStandardMaterial({ color: THEME.trunk, roughness: 0.85 });
    [[2.95, 0.3, -0.9, 0.2], [2.95, 0.9, -0.9, -0.15], [3.65, 0.3, -0.5, 0.5]].forEach(([x, y, z, turn]) => {
      const crate = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.6, 0.62), crateWood);
      crate.position.set(x, y, z);
      crate.rotation.y = turn;
      crate.castShadow = true;
      crate.receiveShadow = true;
      this.stall.add(crate);
    });
    const burlap = new THREE.MeshStandardMaterial({ color: 0xc9b48c, roughness: 1, flatShading: true });
    [[-2.95, -0.8], [-3.5, -0.4], [-3.15, 0.05]].forEach(([x, z], i) => {
      const sack = new THREE.Mesh(new THREE.DodecahedronGeometry(0.34, 0), burlap);
      sack.scale.set(1, 1.25, 1);
      sack.position.set(x, 0.36, z);
      sack.rotation.y = i * 0.9;
      sack.castShadow = true;
      this.stall.add(sack);
    });
    this.scene.add(this.stall);
  }

  // Trees and lamps around the square: they give the scene depth and scale without crowding the paths.
  buildDecor() {
    const leaf = new THREE.MeshStandardMaterial({ color: THEME.leaf, roughness: 0.85, flatShading: true });
    const leafLight = new THREE.MeshStandardMaterial({
      color: new THREE.Color(THEME.leaf).lerp(new THREE.Color(0xf4e3a1), 0.35), roughness: 0.85, flatShading: true,
    });
    const trunk = new THREE.MeshStandardMaterial({ color: THEME.trunk, roughness: 0.9 });
    const spots = [[-16, -8, 1.2], [16, -8, 1.1], [-19, 3, 1.35], [19, 3, 1.25], [-13, 12, 1.0], [13, 12, 1.05], [-6, -13, 0.9], [7, -13, 1.0]];
    spots.forEach(([x, z, s], i) => {
      const tree = new THREE.Group();
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.16 * s, 0.22 * s, 1.6 * s, 8), trunk);
      stem.position.y = 0.8 * s;
      const crown = new THREE.Mesh(new THREE.IcosahedronGeometry(1.25 * s, 0), i % 2 ? leaf : leafLight);
      crown.position.y = 2.2 * s;
      const crown2 = new THREE.Mesh(new THREE.IcosahedronGeometry(0.85 * s, 0), i % 2 ? leafLight : leaf);
      crown2.position.set(0.5 * s, 2.9 * s, 0.2 * s);
      for (const mesh of [stem, crown, crown2]) {
        mesh.castShadow = true;
        tree.add(mesh);
      }
      tree.position.set(x, 0, z);
      tree.rotation.y = i * 1.3;
      this.scene.add(tree);
    });
    const lampPost = new THREE.MeshStandardMaterial({ color: THEME.ink, roughness: 0.5, metalness: 0.4 });
    const lampGlow = new THREE.MeshBasicMaterial({ color: 0xffe2a8 });
    for (const [x, z] of [[-5.5, -4.5], [5.5, -4.5], [-14, 9], [14, 9]]) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.08, 3.2, 10), lampPost);
      post.position.set(x, 1.6, z);
      post.castShadow = true;
      const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.22, 16, 12), lampGlow);
      bulb.position.set(x, 3.3, z);
      this.scene.add(post, bulb);
    }
    const wood = new THREE.MeshStandardMaterial({ color: THEME.trunk, roughness: 0.8 });
    const iron = new THREE.MeshStandardMaterial({ color: THEME.ink, roughness: 0.6, metalness: 0.3 });
    for (const [x, z, turn] of [[-12.5, -2.5, 0.35], [12.5, -2.5, -0.35], [-5.5, 8.6, 0], [5.5, 8.6, 0]]) {
      const bench = new THREE.Group();
      const seat = new THREE.Mesh(new THREE.BoxGeometry(2.0, 0.1, 0.5), wood);
      seat.position.y = 0.5;
      const back = new THREE.Mesh(new THREE.BoxGeometry(2.0, 0.42, 0.08), wood);
      back.position.set(0, 0.8, -0.24);
      bench.add(seat, back);
      for (const side of [-0.85, 0.85]) {
        const leg = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.5, 0.45), iron);
        leg.position.set(side, 0.25, 0);
        bench.add(leg);
      }
      bench.traverse((mesh) => {
        mesh.castShadow = true;
      });
      bench.position.set(x, 0, z);
      bench.rotation.y = turn;
      this.scene.add(bench);
    }
    const clay = new THREE.MeshStandardMaterial({ color: 0xb8714a, roughness: 0.85 });
    const bloom = [0xd9481f, 0xb8862b, 0xc85a7a];
    // Kept off the walking lines (queue -> stall -> zones).
    [[-3.0, -3.4], [3.0, -3.4], [-1.3, 4.7], [1.3, 4.7]].forEach(([x, z], i) => {
      const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.32, 0.6, 14), clay);
      pot.position.set(x, 0.3, z);
      const shrub = new THREE.Mesh(new THREE.IcosahedronGeometry(0.5, 0), i % 2 ? leaf : leafLight);
      shrub.position.set(x, 0.85, z);
      const flowers = new THREE.Mesh(
        new THREE.IcosahedronGeometry(0.16, 0),
        new THREE.MeshStandardMaterial({ color: bloom[i % bloom.length], roughness: 0.7, flatShading: true })
      );
      flowers.position.set(x + 0.2, 1.2, z + 0.15);
      for (const mesh of [pot, shrub, flowers]) {
        mesh.castShadow = true;
        this.scene.add(mesh);
      }
    });
    // Bunting between the two queue lamps: small paper flags in the page palette, hung on a sagging line.
    const flagColors = [THEME.accent, 0xb8862b, ...SEGMENT_COLORS.slice(0, 3)];
    const flagShape = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-0.22, 0, 0),
      new THREE.Vector3(0.22, 0, 0),
      new THREE.Vector3(0, -0.42, 0),
    ]);
    flagShape.computeVertexNormals();
    const flags = 15;
    for (let i = 0; i <= flags; i++) {
      const t = i / flags;
      const x = -5.5 + 11 * t;
      const y = 3.15 - Math.sin(Math.PI * t) * 0.55;
      if (i > 0 && i < flags) {
        const flag = new THREE.Mesh(
          flagShape,
          new THREE.MeshStandardMaterial({ color: flagColors[i % flagColors.length], side: THREE.DoubleSide, roughness: 0.9 })
        );
        flag.position.set(x, y, -4.5);
        this.scene.add(flag);
      }
    }
    const linePoints = [];
    for (let i = 0; i <= 24; i++) {
      const t = i / 24;
      linePoints.push(new THREE.Vector3(-5.5 + 11 * t, 3.15 - Math.sin(Math.PI * t) * 0.55, -4.5));
    }
    this.scene.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(linePoints),
      new THREE.LineBasicMaterial({ color: THEME.ink, transparent: true, opacity: 0.5 })
    ));
    this.buildFountain(0, 12.4);
    // Two shuttered neighbour stalls behind the queue: the square is a market, yours is the open one.
    [[-11.2, -11.4, 0.35, SEGMENT_COLORS[0]], [11.2, -11.4, -0.35, SEGMENT_COLORS[2]]].forEach(([x, z, turn, color]) => {
      this.scene.add(neighbourStall(x, z, turn, color));
    });
  }

  // A low stone fountain in the open foreground: a focal point that never blocks the stall.
  buildFountain(x, z) {
    const stone = new THREE.MeshStandardMaterial({ color: THEME.stone, roughness: 0.85 });
    const water = new THREE.MeshStandardMaterial({ color: 0x8fbcc4, roughness: 0.15, metalness: 0.1, transparent: true, opacity: 0.85 });
    const fountain = new THREE.Group();
    const basin = new THREE.Mesh(new THREE.CylinderGeometry(1.7, 1.8, 0.5, 40, 1, true), stone);
    basin.position.y = 0.25;
    const rim = new THREE.Mesh(new THREE.TorusGeometry(1.72, 0.1, 8, 48), stone);
    rim.rotation.x = Math.PI / 2;
    rim.position.y = 0.5;
    const pool = new THREE.Mesh(new THREE.CircleGeometry(1.68, 40), water);
    pool.rotation.x = -Math.PI / 2;
    pool.position.y = 0.4;
    const column = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.24, 1.1, 16), stone);
    column.position.y = 0.95;
    const bowl = new THREE.Mesh(new THREE.CylinderGeometry(0.62, 0.3, 0.22, 24), stone);
    bowl.position.y = 1.5;
    const spout = new THREE.Mesh(new THREE.CircleGeometry(0.55, 24), water);
    spout.rotation.x = -Math.PI / 2;
    spout.position.y = 1.62;
    for (const mesh of [basin, rim, column, bowl]) {
      mesh.castShadow = true;
      mesh.receiveShadow = true;
    }
    fountain.add(basin, rim, pool, column, bowl, spout);
    fountain.position.set(x, 0, z);
    this.scene.add(fountain);
  }

  setStall(title, subtitle) {
    const clip = (text, max) => (text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text);
    const shortTitle = title.replace(/\s*\(.*$/, "").split(/ - | – /)[0].trim() || title;
    this.signMaterial.map?.dispose();
    this.signMaterial.map = labelTexture([clip(shortTitle, 22), clip(subtitle, 36)]);
    this.signMaterial.needsUpdate = true;
  }

  bindControls() {
    const canvas = this.renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "3D market square. Drag to orbit, scroll or pinch to zoom, click a buyer for details. Arrow keys orbit, plus and minus zoom.");
    const pointers = new Map();
    let moved = 0;
    let last = null;
    let pinch = 0;
    const touched = () => {
      this.lastInteraction = performance.now();
      this.orbitGoal = null;
    };
    canvas.addEventListener("pointerdown", (event) => {
      pointers.set(event.pointerId, [event.clientX, event.clientY]);
      canvas.setPointerCapture(event.pointerId);
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = Math.hypot(a[0] - b[0], a[1] - b[1]);
        return;
      }
      this.dragging = true;
      moved = 0;
      last = [event.clientX, event.clientY];
    });
    canvas.addEventListener("pointermove", (event) => {
      if (pointers.has(event.pointerId)) pointers.set(event.pointerId, [event.clientX, event.clientY]);
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) this.zoomBy(pinch / distance);
        pinch = distance;
        moved = 99;
        touched();
        return;
      }
      if (!this.dragging) {
        this.hoverAt(event);
        return;
      }
      const dx = event.clientX - last[0];
      const dy = event.clientY - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      last = [event.clientX, event.clientY];
      if (moved > 4) {
        touched();
        this.orbit.azimuth -= dx * 0.006;
        this.orbit.elevation = Math.min(1.4, Math.max(0.15, this.orbit.elevation + dy * 0.005));
        this.updateCamera();
      }
    });
    const release = (event) => {
      pointers.delete(event.pointerId);
      if (pointers.size < 2) pinch = 0;
      if (!this.dragging) return;
      this.dragging = false;
      if (moved < 6 && event.type === "pointerup") this.pick(event);
    };
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", release);
    canvas.addEventListener("pointerleave", () => this.setHover(null));
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      touched();
      this.zoomBy(1 + Math.sign(event.deltaY) * 0.08);
    }, { passive: false });
    canvas.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: [0.12, 0], ArrowRight: [-0.12, 0], ArrowUp: [0, -0.08], ArrowDown: [0, 0.08] }[event.key];
      if (step) {
        event.preventDefault();
        touched();
        this.orbit.azimuth += step[0];
        this.orbit.elevation = Math.min(1.4, Math.max(0.15, this.orbit.elevation + step[1]));
        this.updateCamera();
      } else if (event.key === "+" || event.key === "=" || event.key === "-") {
        event.preventDefault();
        touched();
        this.zoomBy(event.key === "-" ? 1.1 : 0.9);
      }
    });
  }

  zoomBy(factor) {
    this.orbit.radius = Math.min(52, Math.max(7, this.orbit.radius * factor));
    this.updateCamera();
  }

  // Bot id under the pointer (buyers and reviewers), or null.
  botAt(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.bots.values(), ...this.reviewers.values()].map((b) => b.group), true);
    for (const hit of hits) {
      let node = hit.object;
      while (node && !node.userData.botId) node = node.parent;
      if (node) return node.userData.botId;
    }
    return null;
  }

  hoverAt(event) {
    const now = performance.now();
    if (now - (this.lastHoverCheck || 0) < 50) return;
    this.lastHoverCheck = now;
    this.setHover(this.botAt(event));
  }

  setHover(id) {
    if (id === this.hoveredId) return;
    this.hoveredId = id;
    this.renderer.domElement.classList.toggle("hovering", Boolean(id));
  }

  // Camera presets with a smooth move: overview (whole square), stall (close), top (from above).
  setView(name) {
    const base = this.baseRadius || 27;
    const views = {
      overview: { azimuth: 0, elevation: 0.86, radius: base, target: this.baseTarget.clone() },
      stall: { azimuth: 0.35, elevation: 0.42, radius: Math.max(11, base * 0.48), target: new THREE.Vector3(0, 1.6, 0.6) },
      top: { azimuth: 0, elevation: 1.38, radius: base * 1.02, target: new THREE.Vector3(0, 0, -0.6) },
    };
    const goal = views[name] || views.overview;
    this.view = name in views ? name : "overview";
    // Take the short way round from the current azimuth.
    const twoPi = Math.PI * 2;
    goal.azimuth = this.orbit.azimuth + ((((goal.azimuth - this.orbit.azimuth) % twoPi) + twoPi + Math.PI) % twoPi - Math.PI);
    this.orbitGoal = goal;
    this.lastInteraction = performance.now();
  }

  // Signs turn about their vertical axis to face the camera, so they stay readable at any orbit angle.
  faceSignsToCamera() {
    if (!this.billboards) {
      this.billboards = [];
      this.scene.traverse((object) => {
        if (object.userData.billboard) this.billboards.push(object);
      });
    }
    const worldPosition = new THREE.Vector3();
    for (const sign of this.billboards) {
      sign.getWorldPosition(worldPosition);
      sign.rotation.y = Math.atan2(this.camera.position.x - worldPosition.x, this.camera.position.z - worldPosition.z);
    }
  }

  // Page coordinates of a bot's body (used by automated checks and for anchoring UI).
  screenPointOf(id) {
    const bot = this.bots.get(id) || this.reviewers.get(id);
    if (!bot) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const point = new THREE.Vector3(bot.group.position.x, 0.95, bot.group.position.z).project(this.camera);
    const x = rect.left + ((point.x + 1) / 2) * rect.width;
    const y = rect.top + ((1 - point.y) / 2) * rect.height;
    const inFrame = point.z < 1 && x > rect.left + 4 && x < rect.right - 4 && y > rect.top + 4 && y < rect.bottom - 4;
    // Visible means a click there reaches the canvas, not an overlay card on top of it.
    const visible = inFrame && document.elementFromPoint(x, y) === this.renderer.domElement;
    return { x, y, visible };
  }

  pick(event) {
    const id = this.botAt(event);
    if (id) this.onSelect?.(id);
  }

  updateCamera() {
    const { azimuth, elevation, radius, target } = this.orbit;
    this.camera.position.set(
      target.x + radius * Math.sin(azimuth) * Math.cos(elevation),
      target.y + radius * Math.sin(elevation),
      target.z + radius * Math.cos(azimuth) * Math.cos(elevation)
    );
    this.camera.lookAt(target);
  }

  resize() {
    // Measure the canvas itself: on phones the HUD sits above it in the arena's flow.
    const canvas = this.renderer.domElement;
    const clientWidth = canvas.clientWidth || this.container.clientWidth;
    const clientHeight = canvas.clientHeight || this.container.clientHeight;
    if (!clientWidth || !clientHeight) return;
    this.renderer.setSize(clientWidth, clientHeight, false);
    this.camera.aspect = clientWidth / clientHeight;
    // Keep both decision zones (x = +/-12.6) in frame on narrow screens.
    this.baseRadius = Math.min(42, Math.max(27, 27 * (1.45 / this.camera.aspect)));
    // The HUD covers the top of the canvas, so aim a little behind the stall: the queue
    // clears the HUD and both zones stay in frame. Phones have the HUD above the canvas.
    this.baseTarget = new THREE.Vector3(0, clientWidth < 600 ? 1.4 : 3.2, clientWidth < 600 ? 1.5 : -1.2);
    // Small canvases render the signs at a few pixels high; enlarge them there.
    const signScale = clientWidth < 600 ? 1.45 : 1;
    for (const zone of [this.buyZone, this.passZone, this.queueZone]) zone?.userData.sign?.scale.setScalar(signScale);
    if (this.view === "overview" && !this.orbitGoal) {
      this.orbit.radius = this.baseRadius;
      this.orbit.target.copy(this.baseTarget);
    } else {
      this.setView(this.view);
    }
    this.updateCamera();
    this.camera.updateProjectionMatrix();
  }

  reset() {
    for (const bot of this.bots.values()) this.scene.remove(bot.group);
    this.bots.clear();
    // Buyer labels go; reviewer labels stay with their bots.
    for (const label of this.labels.values()) label.remove();
    this.labels.clear();
    this.buySlots = 0;
    this.passSlots = 0;
    this.stallSlots = 0;
    this.selectedId = null;
    this.lastDecidedId = null;
    this.buyZone.userData.setCount(0);
    this.passZone.userData.setCount(0);
  }

  spawn(persona, segmentIndex, index, total) {
    if (this.previewBots.length) this.clearPreview();
    this.panelSize = total;
    const spawn = this.queueSlot(index, total);
    const bot = new Bot(persona, SEGMENT_COLORS[segmentIndex % SEGMENT_COLORS.length], spawn);
    bot.speed = 3.2 * this.speedFactor;
    this.scene.add(bot.group);
    this.bots.set(persona.id, bot);
    const label = document.createElement("div");
    label.className = "bot-label";
    const name = persona.name || persona.id;
    // Decided bots in a pile show a 2-letter chip; hover, select or a feed hover expands it to the name.
    label.innerHTML = `<span class="ini"></span><span class="nm"></span>`;
    label.querySelector(".ini").textContent = name.slice(0, 2);
    label.querySelector(".nm").textContent = name;
    label.setAttribute("aria-label", name);
    label.addEventListener("click", () => this.onSelect?.(persona.id));
    this.labelLayer.appendChild(label);
    this.labels.set(persona.id, label);
    return bot;
  }

  // Two staggered rows inside the queue strip.
  queueSlot(index, total) {
    const perRow = Math.max(1, Math.ceil(total / 2));
    const row = index % 2;
    const col = Math.floor(index / 2);
    const gap = Math.min(1.3, (QUEUE_WIDTH - 1.2) / Math.max(1, perRow - 1));
    const x = (col - (perRow - 1) / 2) * gap + (row ? gap / 2 : 0);
    return new THREE.Vector3(x, 0, QUEUE_Z - 0.75 + row * 1.5);
  }

  // Idle preview: anonymous buyers waiting in the queue, coloured by segment share.
  preview(total, shares) {
    this.clearPreview();
    if (this.bots.size || !total) return;
    const weights = shares.length ? shares : [1];
    const sum = weights.reduce((a, b) => a + (b || 0), 0) || 1;
    let segment = 0;
    let used = 0;
    for (let i = 0; i < total; i++) {
      while (segment < weights.length - 1 && i >= Math.round(((used + (weights[segment] || 0)) / sum) * total)) {
        used += weights[segment] || 0;
        segment++;
      }
      const bot = new Bot({ id: `preview-${i}` }, SEGMENT_COLORS[segment % SEGMENT_COLORS.length], this.queueSlot(i, total));
      bot.group.rotation.y = 0;
      this.scene.add(bot.group);
      this.previewBots.push(bot);
    }
    this.queueZone.userData.setCount(total);
  }

  clearPreview() {
    for (const bot of this.previewBots) this.scene.remove(bot.group);
    this.previewBots = [];
  }

  // Number of buyers still waiting in the queue (shown on the queue sign).
  updateQueueCount() {
    let waiting = 0;
    for (const bot of this.bots.values()) if (bot.state === "queued") waiting++;
    this.queueZone.userData.setCount(waiting);
  }

  setSpeed(factor) {
    this.speedFactor = factor;
    for (const bot of [...this.bots.values(), ...this.reviewers.values()]) bot.speed = 3.2 * factor;
  }

  // Milliseconds until the bot reaches its current target.
  travelMs(bot) {
    const dx = bot.target.x - bot.group.position.x;
    const dz = bot.target.z - bot.group.position.z;
    return (Math.hypot(dx, dz) / bot.speed) * 1000 + 120;
  }

  approach(id) {
    const bot = this.bots.get(id);
    if (!bot) return 0;
    const slot = this.stallSlots++;
    const angle = Math.PI * (0.15 + ((slot * 0.37) % 0.7));
    const radius = 3.3 + (slot % 2) * 0.9;
    bot.walkTo(new THREE.Vector3(Math.cos(angle) * radius * (slot % 2 ? -1 : 1), 0, -Math.sin(angle) * radius * 0.4 + 2.6));
    bot.state = "walking";
    this.updateQueueCount();
    return this.travelMs(bot);
  }

  think(id) {
    this.bots.get(id)?.setThinking();
    const label = this.labels.get(id);
    if (label) label.dataset.state = "thinking";
  }

  // Spot for the n-th buyer in a zone: a sunflower (golden-angle) layout that fills the disc from the
  // centre out, so every buyer keeps its own visible place instead of a grid spilling past the ring.
  pileSpot(cx, index) {
    const capacity = Math.max(8, this.panelSize || 20);
    const radius = (ZONE_R - 0.55) * Math.sqrt((index + 0.5) / capacity);
    const angle = index * 2.399963;
    return new THREE.Vector3(cx + Math.cos(angle) * radius, 0, ZONE_Z + Math.sin(angle) * radius * 0.92);
  }

  decide(id, buys, shortReason) {
    const bot = this.bots.get(id);
    if (!bot) return;
    bot.setDecision(buys);
    this.lastDecidedId = id;
    const index = buys ? this.buySlots++ : this.passSlots++;
    (buys ? this.buyZone : this.passZone).userData.setCount(buys ? this.buySlots : this.passSlots);
    bot.walkTo(this.pileSpot(buys ? 9 : -9, index));
    const label = this.labels.get(id);
    if (label) {
      label.dataset.state = buys ? "buy" : "pass";
      label.title = shortReason || "";
    }
  }

  // ---------- reviewer bots ----------
  reviewerHome(index, total) {
    const gap = 1.6;
    return new THREE.Vector3((index - (total - 1) / 2) * gap, 0, 5.4);
  }

  spawnReviewers(reviewers) {
    reviewers.forEach((reviewer, index) => {
      if (this.reviewers.has(reviewer.id)) {
        this.reviewers.get(reviewer.id).clearVerdict();
        return;
      }
      const home = this.reviewerHome(index, reviewers.length);
      const bot = new ReviewerBot(reviewer, new THREE.Vector3(home.x, 0, 16));
      bot.home = home;
      bot.speed = 3.2 * this.speedFactor;
      bot.walkTo(home);
      this.scene.add(bot.group);
      this.reviewers.set(reviewer.id, bot);
      const label = document.createElement("div");
      label.className = "bot-label reviewer";
      label.innerHTML = '<span class="nm"></span>';
      label.querySelector(".nm").textContent = reviewer.name;
      label.setAttribute("aria-label", `${reviewer.name} (reviewer)`);
      label.addEventListener("click", () => this.onSelect?.(reviewer.id));
      this.labelLayer.appendChild(label);
      this.reviewerLabels ||= new Map();
      this.reviewerLabels.set(reviewer.id, label);
    });
  }

  // Walk up to the stall to read the pitch. Returns the walk time in ms.
  reviewerInspect(id) {
    const bot = this.reviewers.get(id);
    if (!bot) return 0;
    bot.clearVerdict();
    bot.state = "walking";
    bot.walkTo(new THREE.Vector3(bot.home.x * 0.55, 0, 3.4));
    return this.travelMs(bot);
  }

  reviewerThink(id) {
    const bot = this.reviewers.get(id);
    if (!bot) return;
    bot.setThinking();
    const label = this.reviewerLabels?.get(id);
    if (label) label.dataset.state = "thinking";
  }

  // Post the verdict (fix / ok / you) and walk back to the reviewers' row.
  reviewerVerdict(id, verdict) {
    const bot = this.reviewers.get(id);
    if (!bot) return 0;
    bot.setVerdict(verdict);
    bot.walkTo(bot.home);
    const label = this.reviewerLabels?.get(id);
    if (label) label.dataset.state = verdict;
    return this.travelMs(bot);
  }

  clearReviewers() {
    for (const bot of this.reviewers.values()) this.scene.remove(bot.group);
    this.reviewers.clear();
    for (const label of this.reviewerLabels?.values() || []) label.remove();
    this.reviewerLabels?.clear();
  }

  highlight(id) {
    this.selectedId = id;
    this.selectRing.visible = Boolean(id && (this.bots.has(id) || this.reviewers.has(id)));
    this.selectPin.visible = this.selectRing.visible;
    for (const [botId, label] of this.labels) label.classList.toggle("selected", botId === id);
    for (const [botId, label] of this.reviewerLabels || []) label.classList.toggle("selected", botId === id);
  }

  // Snap every bot to its target - used at the end of a run, because browsers
  // throttle animation frames in background tabs and the walk can lag behind.
  settle() {
    for (const bot of [...this.bots.values(), ...this.reviewers.values()]) {
      bot.group.position.x = bot.target.x;
      bot.group.position.z = bot.target.z;
    }
  }

  tick() {
    const dt = Math.min(0.05, this.clock.getDelta());
    const time = this.clock.elapsedTime;
    let anyMoving = false;
    for (const bot of [...this.bots.values(), ...this.reviewers.values()]) anyMoving = bot.update(dt, time) || anyMoving;
    for (const bot of this.previewBots) bot.update(dt, time);
    if (this.orbitGoal) {
      const k = Math.min(1, dt * 4);
      const goal = this.orbitGoal;
      this.orbit.azimuth += (goal.azimuth - this.orbit.azimuth) * k;
      this.orbit.elevation += (goal.elevation - this.orbit.elevation) * k;
      this.orbit.radius += (goal.radius - this.orbit.radius) * k;
      this.orbit.target.lerp(goal.target, k);
      if (Math.abs(goal.radius - this.orbit.radius) < 0.02 && Math.abs(goal.azimuth - this.orbit.azimuth) < 0.002) this.orbitGoal = null;
      this.updateCamera();
    } else if (!anyMoving && !this.dragging && this.view === "overview" && performance.now() - this.lastInteraction > 8000) {
      this.orbit.azimuth += dt * 0.025;
      this.updateCamera();
    }
    for (const [id, bot] of [...this.bots, ...this.reviewers]) {
      const lift = id === this.hoveredId || id === this.selectedId ? 1.14 : 1;
      const scale = bot.group.scale.x + (lift - bot.group.scale.x) * Math.min(1, dt * 12);
      bot.group.scale.setScalar(scale);
    }
    const chosen = this.bots.get(this.selectedId) || this.reviewers.get(this.selectedId);
    if (chosen) {
      this.selectRing.position.x = chosen.group.position.x;
      this.selectRing.position.z = chosen.group.position.z;
      this.selectRing.scale.setScalar(1 + Math.sin(time * 4) * 0.06);
      this.selectPin.position.set(chosen.group.position.x, 2.05 * chosen.group.scale.x + Math.sin(time * 3) * 0.08, chosen.group.position.z);
      this.selectPin.rotation.y = time * 1.5;
    }
    const hovered = this.hoveredId !== this.selectedId && (this.bots.get(this.hoveredId) || this.reviewers.get(this.hoveredId));
    this.hoverRing.visible = Boolean(hovered);
    if (hovered) {
      this.hoverRing.position.x = hovered.group.position.x;
      this.hoverRing.position.z = hovered.group.position.z;
    }
    this.faceSignsToCamera();
    this.renderer.render(this.scene, this.camera);
    const width = this.renderer.domElement.clientWidth;
    const height = this.renderer.domElement.clientHeight;
    const { offsetLeft, offsetTop } = this.renderer.domElement;
    const projected = new THREE.Vector3();
    const rank = { thinking: 1, walking: 2, buy: 3, pass: 4 };
    const candidates = [];
    for (const [id, bot] of this.bots) {
      const label = this.labels.get(id);
      const selected = id === this.selectedId || id === this.hoveredId;
      // Queued bots stay unlabelled. Settled bots in a pile drop their tag (the zone sign
      // carries the count); only the latest decision and the selected bot keep a name.
      const settled = bot.state === "buy" || bot.state === "pass";
      // Settled bots drop their tag (the roster and the zone sign carry identity and count);
      // only the latest decision and the selected bot keep a name.
      const compact = false;
      label.classList.toggle("compact", compact);
      if (!selected && (!(bot.state in rank) || (settled && id !== this.lastDecidedId))) {
        label.style.display = "none";
        continue;
      }
      projected.set(bot.group.position.x, compact ? 2.3 : 2.75, bot.group.position.z).project(this.camera);
      if (projected.z >= 1) {
        label.style.display = "none";
        continue;
      }
      candidates.push({
        label,
        bot,
        compact,
        w: compact ? 24 : 64,
        h: compact ? 18 : 26,
        priority: selected ? 0 : rank[bot.state],
        x: offsetLeft + ((projected.x + 1) / 2) * width,
        y: offsetTop + ((1 - projected.y) / 2) * height,
      });
    }
    // Reviewer labels always show (5 at most) and win over buyer labels.
    for (const [id, bot] of this.reviewers) {
      const label = this.reviewerLabels.get(id);
      projected.set(bot.group.position.x, 2.75, bot.group.position.z).project(this.camera);
      if (projected.z >= 1) {
        label.style.display = "none";
        continue;
      }
      candidates.push({
        label,
        bot,
        compact: false,
        w: 92,
        h: 24,
        priority: id === this.selectedId ? 0 : bot.state === "thinking" ? 0.5 : 0.8,
        x: offsetLeft + ((projected.x + 1) / 2) * width,
        y: offsetTop + ((1 - projected.y) / 2) * height,
      });
    }
    // The stall sign is reserved space; labels never sit on top of it.
    const toScreen = (x, y, z) => {
      projected.set(x, y, z).project(this.camera);
      return [offsetLeft + ((projected.x + 1) / 2) * width, offsetTop + ((1 - projected.y) / 2) * height];
    };
    // Reserved screen boxes: the stall sign and both zone count signs ("PASS · 16").
    const signBox = (left, top, right, bottom, z) => {
      const [x1, y1] = toScreen(left, top, z);
      const [x2, y2] = toScreen(right, bottom, z);
      return { left: Math.min(x1, x2), right: Math.max(x1, x2), top: Math.min(y1, y2), bottom: Math.max(y1, y2) };
    };
    const reserved = [
      signBox(-2.5, 5.4, 2.5, 2.9, -0.3),
      signBox(9 - 1.9, 3.6, 9 + 1.9, 1.6, ZONE_Z - ZONE_R - 0.2),
      signBox(-9 - 1.9, 3.6, -9 + 1.9, 1.6, ZONE_Z - ZONE_R - 0.2),
    ];
    const onSign = (x, y, w = 80, h = 22) => reserved.some((box) =>
      x + w / 2 > box.left && x - w / 2 < box.right && y > box.top && y - h < box.bottom);
    // Hide a label when a higher-priority one already sits on the same spot.
    candidates.sort((a, b) => a.priority - b.priority);
    const placed = [];
    const clashes = (item) => onSign(item.x, item.y, item.w, item.h) || placed.some((p) =>
      Math.abs(p.x - item.x) < (p.w + item.w) / 2 && Math.abs(p.y - item.y) < (p.h + item.h) / 2);
    for (const item of candidates) {
      if (!item.compact && clashes(item)) {
        const [footX, footY] = toScreen(item.bot.group.position.x, -0.1, item.bot.group.position.z);
        item.x = footX;
        item.y = footY + 26;
      }
      const clash = clashes(item);
      item.label.style.display = clash ? "none" : "block";
      if (clash) continue;
      placed.push(item);
      item.x = Math.min(offsetLeft + width - item.w / 2 - 4, Math.max(offsetLeft + item.w / 2 + 4, item.x));
      item.label.style.transform = `translate(-50%, -100%) translate(${item.x}px, ${item.y}px)`;
    }
  }
}
