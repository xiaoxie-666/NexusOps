const AUTH_KEY = "nexusops.auth";

const els = {
  serviceState: document.querySelector("#serviceState"),
  modelState: document.querySelector("#modelState"),
  loginForm: document.querySelector("#loginForm"),
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  loginState: document.querySelector("#loginState")
};

function setPill(el, text, tone = "ok") {
  if (!el) return;
  el.textContent = text;
  el.className = `pill ${tone === "ok" ? "pill-ok" : tone === "danger" ? "pill-danger" : "pill-warn"}`;
}

async function api(path, token, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: `Basic ${token}` };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) throw new Error((await response.text()) || `${response.status} ${response.statusText}`);
  return response;
}

function saveAuth(token, profile) {
  sessionStorage.setItem(AUTH_KEY, JSON.stringify({
    token,
    username: profile.username,
    displayName: profile.displayName,
    roles: profile.roles || []
  }));
}

function readAuth() {
  try { return JSON.parse(sessionStorage.getItem(AUTH_KEY) || "null"); }
  catch { return null; }
}

async function checkHealth() {
  try {
    const response = await fetch("/actuator/health");
    const body = await response.json();
    setPill(els.serviceState, body.status === "UP" ? "服务正常" : `服务 ${body.status}`, body.status === "UP" ? "ok" : "danger");
  } catch {
    setPill(els.serviceState, "服务不可用", "danger");
  }
}

async function resumeExistingLogin() {
  const auth = readAuth();
  if (!auth?.token) return;
  try {
    const response = await api("/api/profile", auth.token);
    const profile = await response.json();
    saveAuth(auth.token, profile);
    window.location.replace("/ops.html");
  } catch {
    sessionStorage.removeItem(AUTH_KEY);
  }
}

async function login(event) {
  event.preventDefault();
  const username = els.username.value.trim();
  const password = els.password.value;
  const token = btoa(unescape(encodeURIComponent(`${username}:${password}`)));
  els.loginState.textContent = "正在验证账号…";
  try {
    const response = await api("/api/profile", token);
    const profile = await response.json();
    saveAuth(token, profile);
    els.loginState.textContent = "登录成功，正在进入工作台…";
    window.location.assign("/ops.html");
  } catch (error) {
    sessionStorage.removeItem(AUTH_KEY);
    els.loginState.textContent = `登录失败：${error.message}`;
  }
}

els.loginForm?.addEventListener("submit", login);
checkHealth();
resumeExistingLogin();
