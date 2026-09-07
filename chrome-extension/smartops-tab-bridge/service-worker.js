const BRIDGE_URL = "ws://127.0.0.1:8765/ws/chrome-bridge";
const SNAPSHOT_INTERVAL_MS = 2000;
const KEEPALIVE_INTERVAL_MS = 20000;

let socket = null;
let reconnectTimer = null;
let snapshotTimer = null;
let keepaliveTimer = null;
let sharedTabId = null;
let captureInProgress = false;

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function connect() {
  if (socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(socket.readyState)) return;
  socket = new WebSocket(BRIDGE_URL);
  socket.onopen = () => {
    send({ type: "hello" });
    if (sharedTabId !== null) captureSnapshot();
  };
  socket.onclose = () => {
    socket = null;
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 2000);
  };
  socket.onerror = () => socket?.close();
}

function safeUrl(raw) {
  try {
    const url = new URL(raw);
    return `${url.origin}${url.pathname}`;
  } catch (_) {
    return "";
  }
}

function collectFrameStructure() {
  let frameUrl = "";
  try {
    const current = new URL(location.href);
    frameUrl = `${current.origin}${current.pathname}`;
  } catch (_) { /* unsupported page URL */ }
  const controls = [...document.querySelectorAll(
    "a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=menuitem],[role=tab],[role=checkbox],[role=radio],[id]"
  )];
  const elements = [];
  for (const node of controls) {
    if (elements.length >= 250) break;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    if (style.visibility === "hidden" || style.display === "none" || style.opacity === "0" || rect.width <= 0 || rect.height <= 0) continue;
    if (rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth) continue;
    const centerX = Math.min(innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
    const centerY = Math.min(innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
    const hit = document.elementFromPoint(centerX, centerY);
    if (hit && hit !== node && !node.contains(hit) && !hit.contains(node)) continue;
    const kind = (node.getAttribute("type") || "").toLowerCase();
    const identity = `${node.id || ""} ${node.getAttribute("name") || ""} ${node.getAttribute("aria-label") || ""}`.toLowerCase();
    if (kind === "password" || identity.includes("password") || identity.includes("passwd")) continue;
    elements.push({
      tag: node.tagName.toLowerCase(),
      id: node.id || "",
      name: node.getAttribute("name") || "",
      role: node.getAttribute("role") || "",
      type: kind,
      ariaLabel: node.getAttribute("aria-label") || "",
      text: ["input", "textarea"].includes(node.tagName.toLowerCase())
        ? ""
        : (node.innerText || node.textContent || "").trim().slice(0, 200),
      isDisabled: node.matches(":disabled,[aria-disabled=true]"),
      bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    });
  }
  return {
    url: frameUrl,
    title: document.title.slice(0, 200),
    elements
  };
}

async function captureSnapshot() {
  if (sharedTabId === null || captureInProgress) return;
  captureInProgress = true;
  try {
    connect();
    const tab = await chrome.tabs.get(sharedTabId);
    const results = await chrome.scripting.executeScript({
      target: { tabId: sharedTabId, allFrames: true },
      func: collectFrameStructure
    });
    send({
      type: "snapshot",
      snapshot: {
        tabId: sharedTabId,
        url: safeUrl(tab.url || ""),
        title: (tab.title || "").slice(0, 200),
        capturedAt: new Date().toISOString(),
        frames: results.map(result => ({ frameId: result.frameId, ...(result.result || {}) }))
      }
    });
  } catch (error) {
    send({ type: "error", message: String(error?.message || error).slice(0, 300) });
  } finally {
    captureInProgress = false;
  }
}

async function setSharedTab(tabId) {
  sharedTabId = tabId;
  await chrome.storage.session.set({ sharedTabId });
  await chrome.action.setBadgeText({ tabId, text: "ON" });
  await chrome.action.setBadgeBackgroundColor({ tabId, color: "#ec3013" });
  clearInterval(snapshotTimer);
  snapshotTimer = setInterval(captureSnapshot, SNAPSHOT_INTERVAL_MS);
  connect();
  captureSnapshot();
}

async function stopSharing() {
  const previous = sharedTabId;
  sharedTabId = null;
  await chrome.storage.session.remove("sharedTabId");
  clearInterval(snapshotTimer);
  snapshotTimer = null;
  if (previous !== null) await chrome.action.setBadgeText({ tabId: previous, text: "" }).catch(() => {});
  send({ type: "unshare" });
}

chrome.action.onClicked.addListener(async tab => {
  if (!tab.id) return;
  if (sharedTabId === tab.id) await stopSharing();
  else {
    if (sharedTabId !== null) await stopSharing();
    await setSharedTab(tab.id);
  }
});

chrome.tabs.onRemoved.addListener(tabId => {
  if (tabId === sharedTabId) stopSharing();
});

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

chrome.storage.session.get("sharedTabId").then(value => {
  if (Number.isInteger(value.sharedTabId)) setSharedTab(value.sharedTabId);
  else connect();
});

keepaliveTimer = setInterval(() => {
  connect();
  send({ type: "heartbeat" });
}, KEEPALIVE_INTERVAL_MS);
