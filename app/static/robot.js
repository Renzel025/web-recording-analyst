/*
 * The 3D avatar.
 *
 * The face is not geometry — it's a <canvas> painted every frame and used as a
 * texture on a curved patch of the head. That's what makes expressions cheap:
 * changing mood is a different drawing routine, not a different mesh or a rig.
 */
(() => {
  const mount = document.getElementById("robot-3d");
  if (!mount || !window.THREE) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const CYAN = "#5fe3ff";
  const VISOR = "#10141f";

  /* ---------------------------------------------------------------- face -- */

  const face = document.createElement("canvas");
  face.width = 512;
  face.height = 384;
  const fx = face.getContext("2d");

  let mood = "idle";          // idle | happy | thinking | speaking | listening
  let blink = 0;              // 0 open, 1 shut
  let mouthOpen = 0;          // driven while speaking

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function eye(cx, cy, w, h) {
    fx.save();
    fx.shadowColor = CYAN;
    fx.shadowBlur = 26;
    fx.fillStyle = CYAN;
    roundRect(fx, cx - w / 2, cy - h / 2, w, h, Math.min(w, h) / 2);
    fx.fill();
    fx.restore();
  }

  function arcEye(cx, cy, w, up) {
    fx.save();
    fx.strokeStyle = CYAN;
    fx.lineWidth = 16;
    fx.lineCap = "round";
    fx.shadowColor = CYAN;
    fx.shadowBlur = 22;
    fx.beginPath();
    fx.arc(cx, cy + (up ? 10 : -10), w / 2, up ? Math.PI : 0, up ? 0 : Math.PI);
    fx.stroke();
    fx.restore();
  }

  function mouth(shape) {
    fx.save();
    fx.strokeStyle = CYAN;
    fx.fillStyle = CYAN;
    fx.lineWidth = 12;
    fx.lineCap = "round";
    fx.shadowColor = CYAN;
    fx.shadowBlur = 18;
    const cx = 256, cy = 268;
    if (shape === "smile") {
      fx.beginPath();
      fx.arc(cx, cy - 18, 46, 0.25 * Math.PI, 0.75 * Math.PI);
      fx.stroke();
    } else if (shape === "grin") {
      fx.beginPath();
      fx.arc(cx, cy - 26, 60, 0.18 * Math.PI, 0.82 * Math.PI);
      fx.stroke();
    } else if (shape === "dot") {
      fx.beginPath();
      fx.arc(cx, cy, 13, 0, Math.PI * 2);
      fx.fill();
    } else if (shape === "open") {
      const h = 14 + mouthOpen * 34;
      roundRect(fx, cx - 30, cy - h / 2, 60, h, h / 2);
      fx.fill();
    }
    fx.restore();
  }

  function drawFace(show) {
    const mood = show || "idle";
    fx.clearRect(0, 0, face.width, face.height);

    // visor panel
    fx.fillStyle = VISOR;
    roundRect(fx, 26, 22, face.width - 52, face.height - 44, 96);
    fx.fill();

    // a soft sheen so the panel doesn't read as a flat hole
    const g = fx.createLinearGradient(0, 22, 0, face.height - 22);
    g.addColorStop(0, "rgba(255,255,255,0.10)");
    g.addColorStop(0.45, "rgba(255,255,255,0.02)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    fx.fillStyle = g;
    roundRect(fx, 26, 22, face.width - 52, face.height - 44, 96);
    fx.fill();

    const L = 176, R = 336, EY = 168;

    if (blink > 0.55) {
      eye(L, EY, 56, 8);
      eye(R, EY, 56, 8);
      mouth(mood === "speaking" ? "open" : "smile");
      return;
    }

    if (mood === "happy") {
      arcEye(L, EY, 74, true);
      arcEye(R, EY, 74, true);
      mouth("grin");
    } else if (mood === "thinking") {
      eye(L, EY - 12, 46, 46);
      eye(R, EY - 12, 24, 46);
      mouth("dot");
        } else if (mood === "listening") {
      eye(L, EY, 60, 68);
      eye(R, EY, 60, 68);
      mouth("dot");
    } else if (mood === "speaking") {
      eye(L, EY, 52, 58);
      eye(R, EY, 52, 58);
      mouth("open");
    } else {
      eye(L, EY, 52, 62);
      eye(R, EY, 52, 62);
      mouth("smile");
    }
  }

  const faceTex = new THREE.CanvasTexture(face);
  faceTex.anisotropy = 4;

  /* ---------------------------------------------------------------- scene -- */

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  camera.position.set(0, 0.05, 7.9);

  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputEncoding = THREE.sRGBEncoding;
  mount.appendChild(renderer.domElement);

  const shell = new THREE.MeshStandardMaterial({ color: 0xf2f5fb, roughness: 0.42, metalness: 0.04 });
  const trim = new THREE.MeshStandardMaterial({ color: 0xaebdea, roughness: 0.32, metalness: 0.15 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x0d1018, roughness: 0.25, metalness: 0.2 });

  const bot = new THREE.Group();
  scene.add(bot);

  const head = new THREE.Group();
  head.position.y = 0.72;
  bot.add(head);

  const skull = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 48), shell);
  skull.scale.set(1, 0.94, 0.9);
  head.add(skull);

  // Visor: a patch of a slightly larger sphere, so it curves with the head
  // instead of looking like a sticker. Centred on +Z, lifted a little above
  // the equator because the eyes sit high on the face.
  const phiLen = 1.62, thetaLen = 1.2, thetaMid = 1.42;
  const visorGeo = new THREE.SphereGeometry(
    1.008, 64, 48,
    Math.PI / 2 - phiLen / 2, phiLen,
    thetaMid - thetaLen / 2, thetaLen
  );
  const visor = new THREE.Mesh(
    visorGeo,
    new THREE.MeshStandardMaterial({
      map: faceTex, transparent: true, roughness: 0.14, metalness: 0.1,
      emissive: 0xffffff, emissiveMap: faceTex, emissiveIntensity: 0.55,
    })
  );
  visor.scale.copy(skull.scale);
  head.add(visor);

  // ear pods
  [-1, 1].forEach((s) => {
    const pod = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.2, 40), trim);
    pod.rotation.z = Math.PI / 2;
    pod.position.set(s * 0.97, -0.02, 0);
    head.add(pod);
    const cap = new THREE.Mesh(new THREE.SphereGeometry(0.19, 28, 20), shell);
    cap.position.set(s * 1.09, -0.02, 0);
    head.add(cap);
  });

  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.32, 0.22, 32), trim);
  neck.position.y = -0.03;
  bot.add(neck);

  const torso = new THREE.Mesh(new THREE.SphereGeometry(0.82, 48, 36), shell);
  torso.scale.set(1, 0.92, 0.88);
  torso.position.y = -0.72;
  bot.add(torso);

  const arms = [];
  [-1, 1].forEach((s) => {
    const arm = new THREE.Mesh(new THREE.SphereGeometry(0.26, 28, 22), shell);
    arm.scale.set(0.72, 1.12, 0.72);
    arm.position.set(s * 0.82, -0.66, 0.06);
    bot.add(arm);
    arms.push(arm);
  });

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(0.95, 48),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.22 })
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = -1.52;
  bot.add(shadow);

  scene.add(new THREE.HemisphereLight(0xdbe8ff, 0x141a2b, 0.95));
  const key = new THREE.DirectionalLight(0xffffff, 1.25);
  key.position.set(2.4, 3.2, 4);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x38bdf8, 1.5);
  rim.position.set(-3, 0.6, -2.4);
  scene.add(rim);
  const warm = new THREE.PointLight(0xc084fc, 0.7, 14);
  warm.position.set(2.6, -1.6, 2.4);
  scene.add(warm);

  /* ------------------------------------------------------------- behaviour -- */

  // It runs its own life: wanders a little, looks around, waves hello. No
  // cursor tracking — being dragged around by the pointer reads as a puppet,
  // not a character. The one thing that interrupts it is being spoken to:
  // while a conversation is live it returns to centre and pays attention.

  const armBase = arms.map((a) => ({ x: a.position.x, y: a.position.y, z: a.position.z }));

  // Opens like the Pixar lamp: walks in from off-stage, notices it is being
  // watched, double-takes, then says hello. After that it settles into the
  // ambient wander. The intro runs once — chooseNext() never returns to it.
  let phase = "enter";       // enter | notice | greet | look | walk | wave | attend
  let phaseLeft = 6;         // seconds remaining in this phase
  let posX = -3.2, targetX = 0;
  let facing = 0;            // body yaw, follows travel direction
  let headYaw = 0, headYawTarget = 0;
  let headPitch = 0, headPitchTarget = 0;
  let walkAmt = 0;           // eased 0..1, gates the waddle
  let waveAmt = 0;           // eased 0..1, gates the arm raise
  let bounce = 0;

  function chooseNext() {
    const r = Math.random();
    if (phase === "walk") {
      phase = r < 0.4 ? "wave" : "look";
    } else if (phase === "wave") {
      phase = "look";
    } else {
      if (r < 0.45) phase = "walk";
      else if (r < 0.68) phase = "wave";
      else phase = "look";
    }

    if (phase === "walk") {
      phaseLeft = 3 + Math.random() * 2.5;
      targetX = (Math.random() * 2 - 1) * 0.8;
    } else if (phase === "wave") {
      phaseLeft = 2.8;
      targetX = posX;
    } else {
      phaseLeft = 1.8 + Math.random() * 2.2;
      targetX = posX;
      headYawTarget = (Math.random() * 2 - 1) * 0.55;
      headPitchTarget = (Math.random() * 2 - 1) * 0.16;
    }
  }

  let nextBlink = 2 + Math.random() * 3;

  function resize() {
    const w = mount.clientWidth || 300;
    const h = mount.clientHeight || 300;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(mount);
  resize();

  const clock = new THREE.Clock();

  function frame() {
    requestAnimationFrame(frame);
    // getDelta() must come first: getElapsedTime() calls it internally and
    // banks the result, so asking for elapsed first leaves dt at ~0 and every
    // phase timer below stops advancing.
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;

    // A live conversation outranks wandering.
    const attentive = mood !== "idle";
    if (attentive) {
      phase = "attend";
      phaseLeft = 0.4;
      targetX = 0;
      headYawTarget = 0;
      headPitchTarget = 0;
    } else if (phase === "enter") {
      phaseLeft -= dt;
      // arrives when it gets there, not when a timer says so
      if (Math.abs(targetX - posX) < 0.09 || phaseLeft <= 0) {
        phase = "notice";
        phaseLeft = 0.5;
        bounce = 0.17;             // a happy little hop as it spots you
        headYawTarget = 0;
        headPitchTarget = 0;
      }
    } else if (phase === "notice") {
      phaseLeft -= dt;
      if (phaseLeft <= 0) {
        phase = "greet";
        phaseLeft = 3.2;
      }
    } else if (phase === "greet") {
      phaseLeft -= dt;
      if (phaseLeft <= 0) chooseNext();
    } else {
      phaseLeft -= dt;
      if (phaseLeft <= 0) chooseNext();
    }

    const entering = phase === "enter";
    const walking = (phase === "walk" || entering) && !reduceMotion;
    const waving = (phase === "wave" || phase === "greet") && !reduceMotion;

    // While walking on, it watches where it is going rather than the viewer.
    if (entering) {
      headYawTarget = 0.34;
      headPitchTarget = -0.04;
    }

    walkAmt += ((walking ? 1 : 0) - walkAmt) * 0.08;
    waveAmt += ((waving ? 1 : 0) - waveAmt) * 0.12;

    // travel
    const dx = targetX - posX;
    posX += dx * (entering ? 0.026 : walking ? 0.018 : 0.05);
    if (walking && Math.abs(dx) > 0.02) {
      // face the way it's heading, but only leaning — it never turns its back
      facing += (Math.sign(dx) * 0.42 - facing) * 0.05;
    } else {
      facing += (0 - facing) * 0.06;
    }

    // look around
    const scripted = entering || phase === "notice" || phase === "greet";
    if (!attentive && !scripted && phase !== "look" && Math.random() < 0.004) {
      headYawTarget = (Math.random() * 2 - 1) * 0.5;
      headPitchTarget = (Math.random() * 2 - 1) * 0.14;
    }
    const ease = phase === "notice" ? 0.22 : 0.045;   // the double-take snaps
    headYaw += (headYawTarget - headYaw) * ease;
    headPitch += (headPitchTarget - headPitch) * ease;

    head.rotation.y = headYaw;
    head.rotation.x = headPitch;

    bot.rotation.y = facing;
    bot.position.x = posX;

    if (reduceMotion) {
      bot.position.y = 0;
    } else {
      const step = Math.sin(t * 6.4);
      bot.position.y =
        Math.sin(t * 1.5) * 0.05 * (1 - walkAmt) +   // gentle float when still
        Math.abs(step) * 0.085 * walkAmt +            // bob per stride
        bounce;
      bot.rotation.z = step * 0.055 * walkAmt;        // waddle
    }
    bounce *= 0.88;

    // arms: swing while walking, one raised and flapping while waving
    arms.forEach((a, i) => {
      const swing = Math.sin(t * 6.4 + i * Math.PI) * 0.22 * walkAmt;
      const idle = Math.sin(t * 1.5 + i * Math.PI) * 0.1 * (1 - walkAmt);
      a.position.z = armBase[i].z + swing;
      a.position.y = armBase[i].y;
      a.position.x = armBase[i].x;
      a.rotation.z = idle + (i ? -0.12 : 0.12);

      if (i === 1 && waveAmt > 0.01) {
        // The arm is a sphere, so rotating it is invisible — the wave has to
        // be actual travel. Raise it up beside the head, then swing it side to
        // side with a little lift on each pass.
        const swingX = Math.sin(t * 9.5) * 0.26 * waveAmt;
        const lift = Math.abs(Math.cos(t * 9.5)) * 0.07 * waveAmt;
        a.position.y = armBase[i].y + (0.92 + lift) * waveAmt;
        a.position.x = armBase[i].x + (0.16 * waveAmt) + swingX;
        a.position.z = armBase[i].z + 0.18 * waveAmt;
        a.rotation.z = -0.5 * waveAmt;
      }
    });

    // a wave is a greeting, so it grins while doing it
    // Spotting you is a pleased beat, not a startled one — it grins from the
    // moment it turns to camera, right through the wave.
    const shown = attentive
      ? mood
      : (phase === "notice" || phase === "greet" || waveAmt > 0.5) ? "happy"
      : "idle";

    nextBlink -= dt;
    if (nextBlink <= 0) {
      blink = 1;
      nextBlink = 2.4 + Math.random() * 3.4;
    }
    blink = Math.max(0, blink - dt * 7);

    mouthOpen = mood === "speaking" ? Math.sin(t * 17) * 0.5 + 0.5 : 0;

    drawFace(shown);
    faceTex.needsUpdate = true;
    renderer.render(scene, camera);
  }
  frame();

  /* ----------------------------------------------------------------- api -- */

  window.Avatar = {
    setMood(m) { mood = m || "idle"; },
    hop() { bounce = 0.24; },
  };

  mount.addEventListener("pointerdown", () => window.Avatar.hop());
})();
