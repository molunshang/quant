# 前端页面按功能拆分 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `web/index.html` 的四大功能拆成 4 个独立页面（回测 / AI 优化 / 数据预缓存 / LLM 设置）+ 共享 CSS/JS，顶部导航切换。

**Architecture:** 多页面 HTML，无构建工具、无框架。后端 `api/main.py` 仅新增 3 个 GET 页面路由；共享样式抽到 `web/common.css`，共享工具（`$`、导航高亮）抽到 `web/common.js`；各页业务逻辑留在各自内联 `<script>`，页面间零耦合。后端 API 接口零改动。

**Tech Stack:** 原生 HTML/CSS/JS、FastAPI（仅加 3 个 GET 路由）、ECharts（仅回测页）、pytest（TestClient 路由测试）。

## Global Constraints

- 后端 API 接口（`/api/*`）一律不改动，只允许新增 3 个 GET 页面路由。
- 不引入构建工具 / 前端框架 / SPA 路由。
- 页面静态资源必须用绝对路径 `/web/...`（页面由 `FileResponse` 服务，相对路径会解析到 `/chat` 等不存在的目录）。
- 页面路由（含 `/`）注册在 `api/main.py` 的 `if WEB_DIR.exists():` 块内。
- `common.js` 只放真正跨页共享的 `$` 与导航高亮；`loadSymbols`/`loadStrategies` 仅回测页使用，留在回测页（与 spec 的"页面间零耦合"一致）。
- 每页 `<body>` 带 `data-page` 属性，导航高亮据此激活。

---

### Task 1: 创建共享资源 common.css + common.js

**Files:**
- Create: `web/common.css`
- Create: `web/common.js`

**Interfaces:**
- Produces: 全局样式类（`.header`/`.nav`/`.nav-link`/`.panel`/`.btn`/`.status`/`.tab`/`.table` 等）、全局函数 `const $ = id => document.getElementById(id)`、导航高亮逻辑（依据 `body[data-page]`）。
- Consumes: 无。

- [ ] **Step 1: 创建 `web/common.css`**

```css
:root { --bg:#0f1419; --panel:#1a1f27; --border:#2a313b; --text:#e6e9ef; --muted:#8a94a6; --accent:#4c9ffe; --green:#4caf50; --red:#f44336; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); }
.header { display:flex; align-items:center; justify-content:space-between; padding:16px 24px; border-bottom:1px solid var(--border); }
.header h1 { font-size:18px; }
.header .sub { color:var(--muted); font-size:13px; }
.nav { display:flex; gap:8px; }
.nav-link { padding:8px 16px; border-radius:6px; color:var(--muted); text-decoration:none; font-size:13px; }
.nav-link:hover { color:var(--text); background:var(--panel); }
.nav-link.active { background:var(--accent); color:#fff; }
.main { display:flex; gap:16px; padding:16px 24px; }
.panel { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:14px; }
.panel h3 { font-size:14px; margin-bottom:10px; color:var(--muted); font-weight:500; }
label { display:block; font-size:12px; color:var(--muted); margin:8px 0 4px; }
input, select, textarea { width:100%; background:var(--bg); border:1px solid var(--border); color:var(--text); border-radius:6px; padding:8px 10px; font-size:13px; }
textarea { font-family:ui-monospace, Menlo, monospace; font-size:12px; resize:vertical; }
.btn { width:100%; padding:10px; border:none; border-radius:6px; background:var(--accent); color:#fff; font-size:14px; cursor:pointer; font-weight:600; }
.btn:hover { filter:brightness(1.1); }
.btn:disabled { opacity:.5; cursor:not-allowed; }
.content { flex:1; display:flex; flex-direction:column; gap:16px; min-width:0; }
.metrics { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px,1fr)); gap:12px; }
.metric { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:12px; }
.metric .label { font-size:12px; color:var(--muted); }
.metric .value { font-size:20px; font-weight:700; margin-top:4px; }
.metric .value.pos { color:var(--green); } .metric .value.neg { color:var(--red); }
.chart { background:var(--panel); border:1px solid var(--border); border-radius:8px; }
.chart div { width:100%; height:320px; }
.tabs { display:flex; gap:8px; margin-bottom:0; }
.tab { padding:8px 16px; border-radius:6px 6px 0 0; background:var(--panel); border:1px solid var(--border); border-bottom:none; cursor:pointer; font-size:13px; color:var(--muted); }
.tab.active { background:var(--accent); color:#fff; }
.trades { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:14px; max-height:260px; overflow:auto; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th, td { padding:6px 10px; text-align:left; border-bottom:1px solid var(--border); }
th { color:var(--muted); font-weight:500; }
.buy { color:var(--red); } .sell { color:var(--green); }
.status { font-size:12px; color:var(--muted); margin-top:8px; min-height:16px; }
.hidden { display:none; }
```

- [ ] **Step 2: 创建 `web/common.js`**

```js
const $ = id => document.getElementById(id);

// 顶部导航：按 body[data-page] 高亮当前页
document.querySelectorAll('.nav-link').forEach(a => {
  if (a.dataset.page === document.body.dataset.page) a.classList.add('active');
});
```

- [ ] **Step 3: 验证文件存在且包含关键标记**

Run:
```bash
grep -q "nav-link" web/common.css && grep -q "data-page" web/common.js && echo OK
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add web/common.css web/common.js
git commit -m "feat: 抽取共享前端资源 common.css / common.js（主题样式 + 导航高亮 + \$ 工具）"
```

---

### Task 2: 创建 chat.html（AI 目标优化）

**Files:**
- Create: `web/chat.html`

**Interfaces:**
- Consumes: `common.css`、`common.js`（`$`、`.nav`）；后端现有 `/api/providers`、`/api/chat`、`/api/chat/events`。
- Produces: `web/chat.html`，由 Task 5 的 `/chat` 路由服务。

- [ ] **Step 1: 创建 `web/chat.html`**（完整内容）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 目标优化 — A股回测系统</title>
<link rel="stylesheet" href="/web/common.css">
<style>
.wrap { max-width:760px; margin:24px auto; padding:0 24px; }
#chatLog { max-height:320px; overflow:auto; border:1px solid var(--border); border-radius:6px; padding:8px; font-size:12px; background:var(--bg); }
</style>
</head>
<body data-page="chat">
<header class="header">
  <div>
    <h1>📈 A股回测系统</h1>
    <div class="sub">股票 · 基金 · ETF &nbsp;|&nbsp; 自定义策略 · Agent API</div>
  </div>
  <nav class="nav">
    <a href="/" class="nav-link" data-page="backtest">回测</a>
    <a href="/chat" class="nav-link" data-page="chat">AI 优化</a>
    <a href="/data" class="nav-link" data-page="data">数据预缓存</a>
    <a href="/settings" class="nav-link" data-page="settings">LLM 设置</a>
  </nav>
</header>

<div class="wrap">
  <div class="panel">
    <h3>🤖 AI 目标优化</h3>
    <div id="chatLog"></div>
    <label for="chatProvider">模型</label>
    <select id="chatProvider"></select>
    <label for="chatInput">目标 / 指令</label>
    <input id="chatInput" placeholder="例如：在沪深300上做到年化收益10%、回撤小于15%">
    <button id="chatSend" class="btn">发送</button>
    <div id="chatStatus" class="status"></div>
  </div>
</div>

<script src="/web/common.js"></script>
<script>
const chatLog = document.getElementById('chatLog');
const chatInput = document.getElementById('chatInput');
const chatProvider = document.getElementById('chatProvider');
const chatSend = document.getElementById('chatSend');
const chatStatus = document.getElementById('chatStatus');
let chatSession = null;
let chatES = null;

function chatAppend(role, text) {
  const d = document.createElement('div');
  d.style.margin = '4px 0';
  const b = document.createElement('b');
  b.textContent = `${role}:`;
  d.appendChild(b);
  d.appendChild(document.createTextNode(' '));
  d.appendChild(document.createTextNode(text));
  chatLog.appendChild(d);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function loadProviders() {
  const r = await fetch('/api/providers');
  const data = await r.json();
  chatProvider.innerHTML = '';
  data.providers.forEach(n => {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    chatProvider.appendChild(o);
  });
}

function chatConnect(sessionId) {
  if (chatES) chatES.close();
  chatES = new EventSource(`/api/chat/events?session_id=${sessionId}`);
  chatES.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'turn') { chatStatus.textContent = `第 ${ev.turn} 轮…`; }
    else if (ev.type === 'tool') { chatAppend('系统', `调用 ${ev.name}`); }
    else if (ev.type === 'tool_error') { chatAppend('系统', `⚠ ${ev.name}: ${ev.error}`); }
    else if (ev.type === 'backtest_results') { chatAppend('系统', '✅ 回测结果已返回'); }
    else if (ev.type === 'clarify') {
      chatAppend('系统', '🤔 需要澄清：' + (ev.questions || []).join('；'));
      chatStatus.textContent = '请回复以澄清';
      chatSend.disabled = false;
    }
    else if (ev.type === 'confirm') {
      chatAppend('系统', ev.text || '请确认目标');
      chatStatus.textContent = '请确认或修改后回复';
      chatSend.disabled = false;
    }
    else if (ev.type === 'running') {
      chatStatus.textContent = '已确认，开始执行…';
      chatSend.disabled = true;
    }
    else if (ev.type === 'error') { chatAppend('系统', `⚠ 出错: ${ev.error}`); chatStatus.textContent = '出错'; chatES.close(); chatSend.disabled = false; }
    else if (ev.type === 'done') { chatAppend('AI', ev.report || '(完成)'); chatStatus.textContent = '完成'; chatES.close(); chatSend.disabled = false; }
  };
  chatES.onerror = () => {
    chatStatus.textContent = '连接中断，重连中…';
    // 传输失败时恢复输入框：服务端事件流断线不会发 done/error 来恢复按钮
    chatSend.disabled = false;
  };
}

async function sendChat() {
  const msg = chatInput.value.trim();
  if (!msg) return;
  chatInput.value = '';
  chatAppend('用户', msg);
  chatStatus.textContent = '思考中…';
  chatSend.disabled = true;
  const r = await fetch('/api/chat', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ message: msg, provider: chatProvider.value, session_id: chatSession }),
  });
  const data = await r.json();
  chatSession = data.session_id;
  chatConnect(chatSession);
  chatSend.disabled = false;
}
chatSend.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });
loadProviders();
</script>
</body>
</html>
```

- [ ] **Step 2: 验证关键内容**

Run:
```bash
grep -q "chatConnect" web/chat.html && grep -q "EventSource" web/chat.html && grep -q "/web/common.js" web/chat.html && echo OK
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web/chat.html
git commit -m "feat: 拆分出 AI 目标优化页 chat.html（SSE 进度 / 澄清确认 / 断线恢复）"
```

---

### Task 3: 创建 data.html（数据预缓存）

**Files:**
- Create: `web/data.html`

**Interfaces:**
- Consumes: `common.css`、`common.js`（`$`）；后端现有 `/api/data/precache`、`/api/data/precache/jobs`、`/api/data/precache/refresh`。
- Produces: `web/data.html`，由 Task 5 的 `/data` 路由服务。

- [ ] **Step 1: 创建 `web/data.html`**（完整内容；注意原 index 里的 `$p` 助手统一改用 common.js 的 `$`）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>数据预缓存 — A股回测系统</title>
<link rel="stylesheet" href="/web/common.css">
<style>
.wrap { max-width:760px; margin:24px auto; padding:0 24px; }
</style>
</head>
<body data-page="data">
<header class="header">
  <div>
    <h1>📈 A股回测系统</h1>
    <div class="sub">股票 · 基金 · ETF &nbsp;|&nbsp; 自定义策略 · Agent API</div>
  </div>
  <nav class="nav">
    <a href="/" class="nav-link" data-page="backtest">回测</a>
    <a href="/chat" class="nav-link" data-page="chat">AI 优化</a>
    <a href="/data" class="nav-link" data-page="data">数据预缓存</a>
    <a href="/settings" class="nav-link" data-page="settings">LLM 设置</a>
  </nav>
</header>

<div class="wrap">
  <div class="panel">
    <h3>📦 数据预缓存</h3>
    <label for="pcSymbols">标的（逗号分隔，如 600519,510300）</label>
    <input id="pcSymbols" placeholder="600519,510300">
    <label for="pcFreq">频率</label>
    <select id="pcFreq">
      <option value="daily" selected>日线</option>
    </select>
    <label for="pcStart">起始日期</label><input id="pcStart" value="2020-01-01">
    <label for="pcEnd">结束日期</label><input id="pcEnd" value="2024-12-31">
    <label for="pcAdjust">复权</label>
    <select id="pcAdjust"><option value="qfq" selected>qfq</option><option value="none">none</option></select>
    <div style="margin-top:8px;display:flex;gap:8px;">
      <button id="pcSubmit" class="btn" style="flex:1">预缓存</button>
      <button id="pcRefresh" class="btn" style="flex:1">刷新已缓存</button>
    </div>
    <div id="pcMsg" class="status"></div>
    <h3 style="font-size:14px;color:var(--muted);margin-top:12px;">任务列表</h3>
    <table id="pcJobsTable">
      <thead><tr><th>ID</th><th>标的</th><th>状态</th><th>进度</th><th>错误</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
</div>

<script src="/web/common.js"></script>
<script>
$('pcSubmit').addEventListener('click', async () => {
  const symbols = $('pcSymbols').value.split(',').map(s => s.trim()).filter(Boolean);
  if (!symbols.length) { $('pcMsg').textContent = '请填写标的'; return; }
  const res = await fetch('/api/data/precache', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      symbols, freq: $('pcFreq').value,
      start: $('pcStart').value, end: $('pcEnd').value, adjust: $('pcAdjust').value
    })
  });
  const data = await res.json();
  $('pcMsg').textContent = data.job_ids ? `已提交 ${data.job_ids.length} 个任务` : (data.detail || '失败');
  loadPrecacheJobs();
});
$('pcRefresh').addEventListener('click', async () => {
  await fetch('/api/data/precache/refresh', {method: 'POST'});
  $('pcMsg').textContent = '已触发刷新';
});

async function loadPrecacheJobs() {
  const res = await fetch('/api/data/precache/jobs');
  const data = await res.json();
  const tbody = $('pcJobsTable').querySelector('tbody');
  tbody.innerHTML = data.jobs.map(j => `
    <tr><td>${j.id}</td><td>${j.symbol}</td><td>${j.status}</td>
    <td>${j.progress}%</td><td>${j.error || ''}</td></tr>`).join('');
}
setInterval(loadPrecacheJobs, 3000);
loadPrecacheJobs();
</script>
</body>
</html>
```

- [ ] **Step 2: 验证关键内容**

Run:
```bash
grep -q "loadPrecacheJobs" web/data.html && grep -q "pcJobsTable" web/data.html && echo OK
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web/data.html
git commit -m "feat: 拆分出数据预缓存页 data.html（提交 / 刷新 / 任务轮询）"
```

---

### Task 4: 创建 settings.html（LLM 设置）

**Files:**
- Create: `web/settings.html`

**Interfaces:**
- Consumes: `common.css`、`common.js`（`$`）；后端现有 `/api/llm/providers/list|add|update|delete|test`、`/api/llm/default/set`。
- Produces: `web/settings.html`，由 Task 5 的 `/settings` 路由服务。

**说明：** 原 index.html 中 `saveLlmProvider` 成功回调与删除回调里调用了 `loadProviders()`——那是为了同步同页的聊天 provider 下拉。拆分后聊天页独立，settings 页不再需要 `loadProviders()`，删除这两处调用（沿用原逻辑的其余部分）。

- [ ] **Step 1: 创建 `web/settings.html`**（完整内容）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM 设置 — A股回测系统</title>
<link rel="stylesheet" href="/web/common.css">
<style>
.wrap { max-width:760px; margin:24px auto; padding:0 24px; }
</style>
</head>
<body data-page="settings">
<header class="header">
  <div>
    <h1>📈 A股回测系统</h1>
    <div class="sub">股票 · 基金 · ETF &nbsp;|&nbsp; 自定义策略 · Agent API</div>
  </div>
  <nav class="nav">
    <a href="/" class="nav-link" data-page="backtest">回测</a>
    <a href="/chat" class="nav-link" data-page="chat">AI 优化</a>
    <a href="/data" class="nav-link" data-page="data">数据预缓存</a>
    <a href="/settings" class="nav-link" data-page="settings">LLM 设置</a>
  </nav>
</header>

<div class="wrap">
  <div class="panel">
    <h3>⚙️ LLM 设置</h3>
    <div id="llmProviderList" style="border:1px solid var(--border);border-radius:6px;padding:8px;font-size:12px;margin-bottom:8px;"></div>
    <label for="llmName">名称</label>
    <input id="llmName">
    <label for="llmType">类型</label>
    <select id="llmType">
      <option value="anthropic">anthropic</option>
      <option value="openai_compat">openai_compat</option>
    </select>
    <label for="llmBaseUrl">base_url（openai_compat 必填）</label>
    <input id="llmBaseUrl" placeholder="http://127.0.0.1:3456">
    <label for="llmModel">模型</label>
    <input id="llmModel" placeholder="opengo/deepseek-v4-flash">
    <label for="llmApiKey">API Key（env:VAR 或明文）</label>
    <input id="llmApiKey" placeholder="env:ANTHROPIC_API_KEY">
    <div style="margin-top:8px;">
      <button id="llmSave" class="btn">保存</button>
      <button id="llmTestForm" class="btn">测试此配置</button>
      <span id="llmTestResult" class="status"></span>
    </div>
    <div id="llmMsg" class="status"></div>
  </div>
</div>

<script src="/web/common.js"></script>
<script>
let editingLlm = null;

async function loadLlmProviders() {
  const r = await fetch('/api/llm/providers/list');
  if (!r.ok) {
    const err = await r.json();
    $('llmProviderList').textContent = '⚠ ' + (err.detail || '加载失败');
    return;
  }
  const data = await r.json();
  const list = $('llmProviderList');
  list.innerHTML = '';
  if (data.error) { list.textContent = '⚠ ' + data.error; return; }
  if (!data.providers.length) { list.textContent = '（无 provider，请在下表添加）'; return; }
  data.providers.forEach(p => {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);';
    const def = document.createElement('input');
    def.type = 'radio'; def.name = 'llmDefault';
    def.checked = p.name === data.default;
    def.onclick = () => setLlmDefault(p.name);
    const label = document.createElement('span');
    label.style.flex = '1';
    label.textContent = `${p.name}（${p.type} · ${p.model}${p.base_url ? ' · ' + p.base_url : ''}）`;
    const testBtn = document.createElement('button');
    testBtn.className = 'btn'; testBtn.textContent = '测试';
    testBtn.onclick = async () => {
      const res = await fetch('/api/llm/providers/test', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)});
      const j = await res.json();
      label.textContent = `${p.name}（${p.type} · ${p.model}${p.base_url ? ' · ' + p.base_url : ''}） ${j.ok ? '✓ 连接成功' : '✗ ' + j.error}`;
    };
    const editBtn = document.createElement('button');
    editBtn.className = 'btn'; editBtn.textContent = '编辑';
    editBtn.onclick = () => fillLlmForm(p);
    const delBtn = document.createElement('button');
    delBtn.className = 'btn'; delBtn.textContent = '删除';
    delBtn.onclick = async () => {
      if (!confirm(`删除 provider ${p.name}?`)) return;
      const res = await fetch('/api/llm/providers/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:p.name})});
      const j = await res.json();
      if (res.ok) { $('llmMsg').textContent = '已删除'; loadLlmProviders(); }
      else { $('llmMsg').textContent = j.detail || '删除失败'; }
    };
    row.append(def, label, testBtn, editBtn, delBtn);
    list.appendChild(row);
  });
}

function fillLlmForm(p) {
  editingLlm = p.name;
  $('llmName').value = p.name;
  $('llmType').value = p.type;
  $('llmBaseUrl').value = p.base_url || '';
  $('llmModel').value = p.model || '';
  $('llmApiKey').value = p.api_key || '';
}

async function setLlmDefault(name) {
  const res = await fetch('/api/llm/default/set', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
  if (!res.ok) { const j = await res.json(); $('llmMsg').textContent = j.detail || '设置失败'; }
  loadLlmProviders();
}

function llmFormData() {
  return {
    name: $('llmName').value.trim(),
    type: $('llmType').value,
    base_url: $('llmBaseUrl').value.trim(),
    model: $('llmModel').value.trim(),
    api_key: $('llmApiKey').value.trim(),
  };
}

async function saveLlmProvider() {
  const url = editingLlm ? '/api/llm/providers/update' : '/api/llm/providers/add';
  const body = llmFormData();
  if (editingLlm) body.name = editingLlm;
  const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await res.json();
  if (res.ok) {
    editingLlm = null;
    $('llmName').value = ''; $('llmBaseUrl').value = ''; $('llmModel').value = ''; $('llmApiKey').value = '';
    $('llmMsg').textContent = '已保存';
    loadLlmProviders();
  } else {
    $('llmMsg').textContent = j.detail || '保存失败';
  }
}

async function testLlmForm() {
  $('llmTestResult').textContent = '测试中…';
  const res = await fetch('/api/llm/providers/test', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(llmFormData())});
  const j = await res.json();
  $('llmTestResult').textContent = j.ok ? '✓ 连接成功' : '✗ ' + j.error;
}

$('llmSave').addEventListener('click', saveLlmProvider);
$('llmTestForm').addEventListener('click', testLlmForm);
loadLlmProviders();
</script>
</body>
</html>
```

- [ ] **Step 2: 验证关键内容**

Run:
```bash
grep -q "loadLlmProviders" web/settings.html && ! grep -q "loadProviders" web/settings.html && echo OK
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web/settings.html
git commit -m "feat: 拆分出 LLM 设置页 settings.html（provider CRUD / 测试 / 设默认）"
```

---

### Task 5: 后端新增 3 个页面路由 + 路由测试

**Files:**
- Modify: `api/main.py:73-80`（`WEB_DIR` 挂载块）
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `web/index.html`、`web/chat.html`、`web/data.html`、`web/settings.html`（均已存在）。
- Produces: GET `/chat` → `chat.html`，GET `/data` → `data.html`，GET `/settings` → `settings.html`。

- [ ] **Step 1: 写失败测试** — 在 `tests/test_api.py` 末尾追加：

```python
def test_web_pages():
    for path in ("/", "/chat", "/data", "/settings"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"], path
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_api.py::test_web_pages -v`
Expected: `/chat`、`/data`、`/settings` 返回 404（路由不存在），测试 FAIL。`/` 通过。

- [ ] **Step 3: 实现路由** — 修改 `api/main.py` 的 `WEB_DIR` 块为：

```python
# Serve web UI (optional; directory may not exist yet)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(WEB_DIR / "index.html"))

    @app.get("/chat", include_in_schema=False)
    def chat_page():
        return FileResponse(str(WEB_DIR / "chat.html"))

    @app.get("/data", include_in_schema=False)
    def data_page():
        return FileResponse(str(WEB_DIR / "data.html"))

    @app.get("/settings", include_in_schema=False)
    def settings_page():
        return FileResponse(str(WEB_DIR / "settings.html"))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_api.py::test_web_pages -v`
Expected: 4 项 PASS

- [ ] **Step 5: 回归整个 API 测试**

Run: `python -m pytest tests/test_api.py -v`
Expected: 全部 PASS（现有用例不受影响）

- [ ] **Step 6: Commit**

```bash
git add api/main.py tests/test_api.py
git commit -m "feat: 后端新增 /chat /data /settings 页面路由 + test_web_pages 路由测试"
```

---

### Task 6: 重写 index.html 为纯回测页

**Files:**
- Modify: `web/index.html`（整文件重写）

**Interfaces:**
- Consumes: `common.css`、`common.js`（`$`、导航高亮）；保留原有回测逻辑与后端 `/api/backtest` 等接口。
- Produces: 精简后的回测页（无聊天 / LLM / 预缓存面板）。

**改法说明：** 基于当前 `web/index.html` 手术式裁剪（保留部分原样，不重排）：

1. `<head>`：删掉整个 `<style>` 块（共享样式已移入 common.css；回测页独有布局样式保留为本页 `<style>`），加 `<link rel="stylesheet" href="/web/common.css">`。保留 ECharts CDN `<script>`。
2. 顶部 `<div class="header">…</div>` 替换为带导航的版本（见 Step 1）。
3. 删除 `#chatPanel`（131-140 行）、`#llmPanel`（142-164 行）、`#precachePanel`（166-188 行）三个 `<div>`。
4. `<script>` 内删除：`const $ = …`（common.js 已提供）、`chatLog`~`loadProviders` 一整段（219-248 行）、`editingLlm`~`loadLlmProviders();`（250-349 行）、`$p`~`loadPrecacheJobs();`（351-382 行）、`chatConnect`~`loadProviders();`（384-435 行）。
5. `<script src="/web/common.js"></script>` 放在内联 `<script>` 之前。
6. 保留：`let equityChart, klineChart, drawdownChart;`、`loadSymbols`、`loadStrategies`、tabs 逻辑、`runBacktest`、`renderResults`、`chart`、三个 `build*Option`、`loadSymbols(); loadStrategies();`。

- [ ] **Step 1: 写入新 `web/index.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股回测系统</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<link rel="stylesheet" href="/web/common.css">
<style>
/* 回测页独有布局样式（共享样式在 /web/common.css） */
.controls { width:320px; flex-shrink:0; display:flex; flex-direction:column; gap:12px; }
.strat-src { display:none; }
#strategyType { margin-bottom:4px; }
</style>
</head>
<body data-page="backtest">
<header class="header">
  <div>
    <h1>📈 A股回测系统</h1>
    <div class="sub">股票 · 基金 · ETF &nbsp;|&nbsp; 自定义策略 · Agent API</div>
  </div>
  <nav class="nav">
    <a href="/" class="nav-link" data-page="backtest">回测</a>
    <a href="/chat" class="nav-link" data-page="chat">AI 优化</a>
    <a href="/data" class="nav-link" data-page="data">数据预缓存</a>
    <a href="/settings" class="nav-link" data-page="settings">LLM 设置</a>
  </nav>
</header>

<div class="main">
  <!-- ===== 左侧控制面板 ===== -->
  <div class="controls">
    <div class="panel">
      <h3>回测设置</h3>
      <label for="symbol">交易标的</label>
      <input id="symbol" list="symbol-list" value="600519" placeholder="代码或名称">
      <datalist id="symbol-list"></datalist>
      <label for="symbolType">类型</label>
      <select id="symbolType">
        <option value="">全部</option><option value="stock">股票</option>
        <option value="etf">ETF</option><option value="fund">基金</option>
      </select>
      <label for="freq">K线频率</label>
      <select id="freq">
        <option value="daily" selected>日线</option>
        <option value="60">60分钟</option><option value="30">30分钟</option>
        <option value="15">15分钟</option><option value="5">5分钟</option><option value="1">1分钟</option>
      </select>
      <label for="start">起始日期</label><input id="start" value="2022-01-01">
      <label for="end">结束日期</label><input id="end" value="2024-12-31">
      <label for="adjust">复权</label>
      <select id="adjust"><option value="qfq" selected>前复权</option><option value="hfq">后复权</option><option value="none">不复权</option></select>
      <label for="cash">初始资金</label><input id="cash" type="number" value="100000">
    </div>

    <div class="panel">
      <h3>策略</h3>
      <label for="strategyType">策略来源</label>
      <select id="strategyType">
        <option value="builtin" selected>内置策略</option>
        <option value="custom">自定义源码</option>
      </select>
      <div id="builtinWrap">
        <label for="strategyName">选择策略</label>
        <select id="strategyName"></select>
      </div>
      <div id="customWrap" class="strat-src">
        <label for="strategySrc">策略源码（定义 strategy(ctx, params)）</label>
        <textarea id="strategySrc" rows="10" placeholder="def strategy(ctx, params):
    # 示例：跌破20日均线买入
    bars = ctx.bars_upto()
    if len(bars) < 20:
        return
    ma = bars['close'].astype(float).rolling(20).mean().iloc[-1]
    if ctx.price &lt; ma and ctx.shares == 0:
        ctx.buy()
"></textarea>
      </div>
      <label for="paramsJson">策略参数 (JSON)</label>
      <textarea id="paramsJson" rows="3" placeholder='{"short": 20, "long": 60}'></textarea>
    </div>

    <button id="runBtn" class="btn">▶ 运行回测</button>
    <div id="status" class="status"></div>
  </div>

  <!-- ===== 右侧结果区 ===== -->
  <div class="content" id="results" style="display:none">
    <div class="metrics" id="metrics"></div>
    <div class="tabs">
      <div class="tab active" data-tab="equity">权益曲线</div>
      <div class="tab" data-tab="kline">K线买卖点</div>
      <div class="tab" data-tab="drawdown">回撤</div>
    </div>
    <div class="chart" id="equityChart"><div id="chartEquity"></div></div>
    <div class="chart hidden" id="klineChart"><div id="chartKline"></div></div>
    <div class="chart hidden" id="drawdownChart"><div id="chartDrawdown"></div></div>
    <div class="trades">
      <h3 style="font-size:14px;color:var(--muted);margin-bottom:8px;">交易记录</h3>
      <table id="tradesTable">
        <thead><tr><th>日期</th><th>方向</th><th>数量</th><th>价格</th><th>金额</th><th>佣金</th><th>印花税</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>

<script src="/web/common.js"></script>
<script>
let equityChart, klineChart, drawdownChart;

// ---- load symbols & strategies ----
async function loadSymbols() {
  const type = $('symbolType').value;
  const r = await fetch(`/api/symbols?type=${type}&limit=500`);
  const data = await r.json();
  const dl = $('symbol-list');
  dl.innerHTML = '';
  data.symbols.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.code; opt.label = `${s.name} (${s.code})`;
    dl.appendChild(opt);
  });
}
async function loadStrategies() {
  const r = await fetch('/api/strategies');
  const data = await r.json();
  const sel = $('strategyName');
  sel.innerHTML = '';
  data.strategies.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.name; opt.textContent = s.description || s.name;
    sel.appendChild(opt);
  });
}

$('symbolType').addEventListener('change', loadSymbols);
$('strategyType').addEventListener('change', () => {
  const custom = $('strategyType').value === 'custom';
  $('builtinWrap').style.display = custom ? 'none' : '';
  $('customWrap').style.display = custom ? '' : 'none';
});

// ---- tabs ----
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const target = tab.dataset.tab;
    ['equity','kline','drawdown'].forEach(k => {
      $(`${k}Chart`).classList.toggle('hidden', k !== target);
    });
    if (target === 'equity') equityChart?.resize();
    if (target === 'kline') klineChart?.resize();
    if (target === 'drawdown') drawdownChart?.resize();
  });
});

// ---- run backtest ----
async function runBacktest() {
  const status = $('status');
  status.textContent = '回测中...';
  $('runBtn').disabled = true;

  let strategy;
  if ($('strategyType').value === 'custom') {
    strategy = { name: 'user_' + Date.now(), source: $('strategySrc').value };
  } else {
    strategy = $('strategyName').value;
  }
  let params = {};
  try { params = JSON.parse($('paramsJson').value || '{}'); } catch(e) { status.textContent = '⚠ 参数 JSON 格式错误'; $('runBtn').disabled = false; return; }

  const payload = {
    symbol: $('symbol').value.trim(),
    freq: $('freq').value,
    start: $('start').value,
    end: $('end').value,
    adjust: $('adjust').value,
    strategy,
    params,
    initial_cash: parseFloat($('cash').value),
  };

  try {
    const r = await fetch('/api/backtest', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (!r.ok || !data.success) throw new Error(data.detail || '回测失败');
    renderResults(data);
    status.textContent = `✅ 完成：${data.symbol_name} ${data.freq} · ${data.strategy}`;
  } catch(e) {
    status.textContent = `❌ ${e.message}`;
  } finally {
    $('runBtn').disabled = false;
  }
}
$('runBtn').addEventListener('click', runBacktest);

// ---- render ----
function renderResults(data) {
  $('results').style.display = 'flex';
  const m = data.metrics;
  const fmtPct = v => `${(v*100).toFixed(2)}%`;
  const fmtNum = v => Number(v).toFixed(2);
  const items = [
    ['总收益', fmtPct(m.total_return), m.total_return>=0],
    ['年化收益', fmtPct(m.annual_return), m.annual_return>=0],
    ['最大回撤', fmtPct(m.max_drawdown), false],
    ['夏普比率', fmtNum(m.sharpe), m.sharpe>=0],
    ['波动率', fmtPct(m.volatility), false],
    ['胜率', fmtPct(m.win_rate), m.win_rate>=0.5],
    ['交易次数', m.n_trades, true],
    ['最终权益', fmtNum(m.final_equity), m.final_equity>=m.initial_equity],
  ];
  $('metrics').innerHTML = items.map(([l,v,pos]) =>
    `<div class="metric"><div class="label">${l}</div><div class="value ${pos?'pos':'neg'}">${v}</div></div>`
  ).join('');

  // trades
  const tbody = $('tradesTable').querySelector('tbody');
  tbody.innerHTML = data.trades.map(t => {
    const cls = t.side === 'buy' ? 'buy' : 'sell';
    const dir = t.side === 'buy' ? '买入' : '卖出';
    return `<tr><td>${t.date}</td><td class="${cls}">${dir}</td><td>${t.shares}</td><td>${t.price}</td><td>${t.amount.toFixed(2)}</td><td>${t.commission.toFixed(2)}</td><td>${(t.stamp_duty||0).toFixed(2)}</td></tr>`;
  }).join('');

  // charts
  equityChart = chart('chartEquity', buildEquityOption(data));
  klineChart = chart('chartKline', buildKlineOption(data));
  drawdownChart = chart('chartDrawdown', buildDrawdownOption(data));
}

function chart(elId, option) {
  const el = $(elId);
  const c = echarts.getInstanceByDom(el) || echarts.init(el);
  c.setOption(option, true);
  return c;
}
function buildEquityOption(data) {
  const eq = data.equity_curve;
  const dates = eq.map(r => r.date);
  const initial = data.metrics.initial_equity;
  const s = eq.map(r => (r.equity/initial-1)*100);
  const b0 = eq[0].benchmark;
  const b = eq.map(r => (r.benchmark/b0-1)*100);
  return {
    tooltip:{trigger:'axis'}, legend:{data:['策略','基准'],bottom:0},
    grid:{left:50,right:20,top:30,bottom:40},
    xAxis:{type:'category',data:dates}, yAxis:{type:'value',name:'%',axisLabel:{formatter:'{value}%'}},
    series:[
      {name:'策略',type:'line',data:s.map(v=>+v.toFixed(3)),smooth:true,symbol:'none',lineStyle:{width:2}},
      {name:'基准',type:'line',data:b.map(v=>+v.toFixed(3)),smooth:true,symbol:'none',lineStyle:{type:'dashed'}}
    ]
  };
}
function buildDrawdownOption(data) {
  const eq = data.equity_curve;
  const dates = eq.map(r=>r.date);
  let peak = -Infinity;
  const dd = eq.map(r=>{ peak=Math.max(peak,r.equity); return ((r.equity/peak-1)*100); });
  return {
    tooltip:{trigger:'axis'}, grid:{left:50,right:20,top:30,bottom:40},
    xAxis:{type:'category',data:dates}, yAxis:{type:'value',axisLabel:{formatter:'{value}%'}},
    series:[{name:'回撤',type:'line',data:dd.map(v=>+v.toFixed(3)),symbol:'none',
      lineStyle:{color:'#f44336',width:1},areaStyle:{color:'#f44336',opacity:.15}}]
  };
}
function buildKlineOption(data) {
  const eq = data.equity_curve;
  const dates = eq.map(r=>r.date);
  // kline from equity curve only carries close — fetch via data api isn't available; use close as all OHLC
  // Better: reuse trades for markers
  const ohlc = eq.map(r=>[r.close,r.close,r.close,r.close]);
  const buys = data.trades.filter(t=>t.side==='buy').map(t=>({name:'买入',coord:[t.date,t.price],value:t.price}));
  const sells = data.trades.filter(t=>t.side==='sell').map(t=>({name:'卖出',coord:[t.date,t.price],value:t.price}));
  return {
    tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
    grid:{left:50,right:20,top:30,bottom:40}, xAxis:{type:'category',data:dates}, yAxis:{type:'value',scale:true},
    dataZoom:[{type:'inside',start:60,end:100}],
    series:[
      {name:'K线',type:'candlestick',data:ohlc,
       itemStyle:{color:'#f44336',color0:'#4caf50',borderColor:'#f44336',borderColor0:'#4caf50'}},
      {name:'买入',type:'scatter',data:buys,symbol:'triangle',symbolSize:11,itemStyle:{color:'#f44336'}},
      {name:'卖出',type:'scatter',data:sells,symbol:'triangle',symbolRotate:180,symbolSize:11,itemStyle:{color:'#4caf50'}}
    ]
  };
}

// init
loadSymbols(); loadStrategies();
</script>
</body>
</html>
```

- [ ] **Step 2: 验证裁剪干净**

Run:
```bash
test "$(grep -c 'chatPanel\|llmPanel\|precachePanel\|loadProviders\|chatConnect' web/index.html)" -eq 0 && echo "已移除" || echo "仍有残留"
grep -q "runBacktest" web/index.html && grep -q "loadStrategies" web/index.html && grep -q "/web/common.js" web/index.html && echo "回测逻辑保留"
```
Expected: `已移除` + `回测逻辑保留`

- [ ] **Step 3: 回归页面路由测试**

Run: `python -m pytest tests/test_api.py::test_web_pages -v`
Expected: 4 项 PASS（`/` 现在来自新 index.html）

- [ ] **Step 4: Commit**

```bash
git add web/index.html
git commit -m "feat: index.html 精简为纯回测页（移除聊天/LLM/预缓存面板，接入共享导航）"
```

---

### Task 7: 全量验证

**Files:**（无改动）

**Interfaces:**
- Consumes: 全部已完成文件。

- [ ] **Step 1: 运行完整测试套件**

Run: `python -m pytest -q`
Expected: 全部 PASS（含新增 `test_web_pages`）

- [ ] **Step 2: 检查 web 目录结构**

Run:
```bash
ls web/
```
Expected:
```
chat.html      common.css     common.js      data.html      index.html     settings.html
```

- [ ] **Step 3: 可选手工冒烟** — 启动服务，浏览器逐一打开 4 个 URL，确认导航高亮与页面功能正常

Run:
```bash
.venv/bin/uvicorn api.main:app --reload
```
- `http://127.0.0.1:8000/` 回测页可跑一次内置策略回测
- `http://127.0.0.1:8000/chat` 聊天页可发消息
- `http://127.0.0.1:8000/data` 数据页可提交预缓存
- `http://127.0.0.1:8000/settings` 设置页可增删 provider

- [ ] **Step 4: 更新 README 的 API 概览页表（若存在页面 URL 说明）**

Run:
```bash
grep -n "chat.html\|/chat\|/settings" README.md | head
```
Expected: 若 README 提到 Web 页面 URL，补充 `/chat`、`/data`、`/settings`；否则无改动。

---

## Self-Review

**Spec coverage:**
- 页面结构四页 + 导航 → Task 2/3/4（新页）、Task 6（回测页）、Task 1（导航样式与高亮）✓
- 后端 3 个 GET 路由 + `/` 保留 → Task 5 ✓
- 共享 common.css / common.js → Task 1 ✓
- 页面间零耦合：设置页删除 `loadProviders` 调用 → Task 4 ✓
- 路由测试 `GET /、/chat、/data、/settings` 200 + text/html → Task 5 ✓

**Placeholder scan:** 每步均有完整代码或精确的行号裁剪指令，无 TBD/TODO。index.html 重写提供完整目标内容，不依赖"照抄类似任务"。

**Type consistency:** `$`（common.js）在四个页面内联脚本中统一使用；`data-page` 取值与导航链接 `data-page` 一一对应（backtest/chat/data/settings）；后端路由函数名 `index/chat_page/data_page/settings_page` 互不冲突；测试路径与文件路径一致。
