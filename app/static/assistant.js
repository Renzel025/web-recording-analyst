/* The assistant beside a recording. Ported from ai-avatar-app/public/app.js: same
 * panel, robot moods, mic input and spoken replies. Replies about this run cite
 * times like [0:42]; those become buttons that jump the video there. */
(() => {
  const panel = document.getElementById("chat-panel");
  if (!panel) return;
  const robot = document.getElementById("robot");
  const statusEl = document.getElementById("avatar-status");
  const log = document.getElementById("log");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const micBtn = document.getElementById("mic-btn");
  const voiceBtn = document.getElementById("voice-btn");
  const framesNote = document.getElementById("frames-note");
  const player = document.getElementById("player");

  const CHAT_URL = panel.dataset.chatUrl;
  const STATUS_URL = panel.dataset.statusUrl;
  const VOICE_KEY = "rec-assistant-voice";
  // Kept in memory only: refreshing the page starts a new conversation.
  let history = [];

  // The model is told not to use markdown, but it reaches for bold anyway when
  // it lists things; through textContent those become literal asterisks.
  function stripMarkup(text) {
    return String(text || "")
      .replace(/\*\*(.+?)\*\*/g, "$1")
      .replace(/(^|[\s(])\*(\S.*?)\*(?=[\s).,!?]|$)/g, "$1$2")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/`([^`]+)`/g, "$1");
  }

  // --- Timestamps -> seek buttons -------------------------------------------------

  const TIME_RE = /\[(\d{1,2}):(\d{2})\]/g;

  function seek(seconds) {
    if (!player) return;
    player.currentTime = seconds;
    player.play().catch(() => {});
  }

  function renderWithTimes(el, text) {
    let last = 0;
    for (const m of text.matchAll(TIME_RE)) {
      el.appendChild(document.createTextNode(text.slice(last, m.index)));
      const seconds = Number(m[1]) * 60 + Number(m[2]);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ts";
      btn.textContent = `▶ ${m[1]}:${m[2]}`;
      btn.title = "Jump the video here";
      btn.addEventListener("click", () => seek(seconds));
      el.appendChild(btn);
      last = m.index + m[0].length;
    }
    el.appendChild(document.createTextNode(text.slice(last)));
  }

  function addMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    const clean = stripMarkup(text);
    if (role === "bot") renderWithTimes(div, clean);
    else div.textContent = clean;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  addMessage("bot", "Hi, I've watched this recording. Ask me what happened, where it went wrong, or whether the bet went through.");

  // --- Frames: the AI can only see the video once they're cut ------------------------

  function showFrames(state) {
    if (state === "ready") { framesNote.hidden = true; return; }
    framesNote.hidden = false;
    framesNote.textContent = state === "failed"
      ? "The video couldn't be prepared for the AI, so it can only answer from the run's report."
      : "Preparing the video for the AI… answers use only the report until it's ready.";
  }
  function pollFrames() {
    fetch(STATUS_URL).then((r) => r.json()).then((d) => {
      showFrames(d.frames_status);
      if (d.frames_status === "pending") setTimeout(pollFrames, 3000);
    }).catch(() => setTimeout(pollFrames, 6000));
  }
  showFrames(panel.dataset.frames);
  if (panel.dataset.frames === "pending") setTimeout(pollFrames, 3000);

  // --- Avatar state -----------------------------------------------------------

  const MOODS = { idle: "idle", listening: "listening", thinking: "thinking", speaking: "speaking", error: "thinking" };
  let statusState = "idle";

  function setStatus(state, text) {
    statusState = state;
    if (window.Avatar) window.Avatar.setMood(MOODS[state] || "idle");
    statusEl.textContent = state === "idle" ? "" : text;
    statusEl.classList.toggle("busy", state !== "idle");
  }

  function setTalking(isTalking) {
    robot.classList.toggle("talking", isTalking);
  }

  // --- Voice out (same voice ranking as the avatar) -----------------------------

  const GOOD_VOICES = [
    "google us english", "google uk english male", "google uk english female",
    "microsoft aria", "microsoft guy", "microsoft jenny", "microsoft david", "microsoft mark",
    "alex", "samantha", "daniel", "karen", "tessa", "moira", "rishi",
  ];
  const AVOID_VOICES = [
    "fred", "junior", "ralph", "albert", "zarvox", "trinoids", "boing", "bells",
    "bubbles", "cellos", "bahh", "wobble", "whisper", "organ", "superstar",
    "jester", "good news", "bad news", "novelty", "eddy", "flo", "grandma",
    "grandpa", "reed", "rocko", "sandy", "shelley",
  ];

  function pickVoice() {
    if (!("speechSynthesis" in window)) return null;
    const voices = window.speechSynthesis.getVoices();
    const english = voices.filter((v) => v.lang && v.lang.toLowerCase().startsWith("en"));
    const pool = (english.length ? english : voices)
      .filter((v) => !AVOID_VOICES.some((bad) => v.name.toLowerCase().includes(bad)));
    if (!pool.length) return null;
    for (const want of GOOD_VOICES) {
      const hit = pool.find((v) => v.name.toLowerCase().includes(want));
      if (hit) return hit;
    }
    return pool.find((v) => v.localService === false) || pool[0];
  }

  let selectedVoice = null;
  if ("speechSynthesis" in window) {
    const refreshVoice = () => { const v = pickVoice(); if (v) selectedVoice = v; };
    refreshVoice();
    window.speechSynthesis.addEventListener("voiceschanged", refreshVoice);
  }

  let voiceOn = true;
  try { voiceOn = localStorage.getItem(VOICE_KEY) !== "0"; } catch (e) {}
  function renderVoice() {
    voiceBtn.setAttribute("aria-pressed", String(voiceOn));
    voiceBtn.title = voiceOn ? "Replies are read aloud (click to mute)" : "Replies are muted (click to read aloud)";
    voiceBtn.classList.toggle("muted", !voiceOn);
  }
  renderVoice();
  voiceBtn.addEventListener("click", () => {
    voiceOn = !voiceOn;
    try { localStorage.setItem(VOICE_KEY, voiceOn ? "1" : "0"); } catch (e) {}
    if (!voiceOn && "speechSynthesis" in window) { window.speechSynthesis.cancel(); setTalking(false); setStatus("idle"); }
    renderVoice();
  });

  function speak(text) {
    if (!voiceOn || !("speechSynthesis" in window) || !text) { setStatus("idle"); return; }
    window.speechSynthesis.cancel();
    // "[0:42]" -> "0:42", which voices read as a time.
    const utterance = new SpeechSynthesisUtterance(text.replace(TIME_RE, "$1:$2"));
    const voice = selectedVoice || pickVoice();
    if (voice) utterance.voice = voice;
    utterance.pitch = 1;
    utterance.rate = 0.95;
    utterance.onstart = () => { setTalking(true); setStatus("speaking", "Speaking..."); };
    utterance.onend = () => { setTalking(false); setStatus("idle"); };
    utterance.onerror = () => { setTalking(false); setStatus("idle"); };
    window.speechSynthesis.speak(utterance);
  }

  // --- Chat ---------------------------------------------------------------------

  let busy = false;
  async function sendMessage(message) {
    if (!message.trim() || busy) return;
    busy = true;
    addMessage("user", message);
    chatInput.value = "";
    setStatus("thinking", "Watching the recording...");
    const pending = addMessage("bot pending", "…");

    try {
      const res = await fetch(CHAT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Request failed");

      history.push({ role: "user", content: message });
      history.push({ role: "assistant", content: data.reply });
      history = history.slice(-16);

      pending.remove();
      addMessage("bot", data.reply);
      speak(stripMarkup(data.reply));
    } catch (err) {
      pending.remove();
      addMessage("error", err.message && err.message !== "Request failed"
        ? `Assistant unavailable: ${err.message}`
        : "Something went wrong talking to the AI. Is the server running?");
      setStatus("idle");
      console.error(err);
    } finally {
      busy = false;
    }
  }

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(chatInput.value);
  });

  document.querySelectorAll(".suggestions [data-ask]").forEach((btn) => {
    btn.addEventListener("click", () => sendMessage(btn.dataset.ask));
  });

  // --- Open / close ---------------------------------------------------------------
  // The panel starts open on a recording page; closing it parks the robot under the
  // "Ask the assistant" button and gives the video the full width.

  function openChat() {
    panel.classList.add("open");
    document.body.classList.add("chat-open");
    if (window.Avatar) window.Avatar.hop();
    chatInput.focus();
  }
  function closeChat() {
    panel.classList.remove("open");
    document.body.classList.remove("chat-open");
  }

  robot.addEventListener("click", () => (panel.classList.contains("open") ? closeChat() : openChat()));
  robot.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openChat(); }
  });
  document.getElementById("open-chat-btn")?.addEventListener("click", openChat);
  document.getElementById("close-chat-btn")?.addEventListener("click", closeChat);

  // --- Voice in (Web Speech API) ------------------------------------------------------

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    const recognizer = new SpeechRecognition();
    recognizer.lang = "en-US";
    recognizer.interimResults = false;
    recognizer.maxAlternatives = 1;
    let listening = false;

    recognizer.onstart = () => {
      listening = true;
      micBtn.classList.add("listening");
      robot.classList.add("listening");
      setStatus("listening", "Listening...");
    };
    recognizer.onend = () => {
      listening = false;
      micBtn.classList.remove("listening");
      robot.classList.remove("listening");
      if (statusState === "listening") setStatus("idle");
    };
    recognizer.onerror = (e) => {
      console.error("Speech recognition error:", e.error);
      setStatus("error", "Didn't catch that, try again");
    };
    recognizer.onresult = (e) => sendMessage(e.results[0][0].transcript);
    micBtn.addEventListener("click", () => (listening ? recognizer.stop() : recognizer.start()));
  } else {
    micBtn.disabled = true;
    micBtn.title = "Voice input isn't supported in this browser";
  }
})();
