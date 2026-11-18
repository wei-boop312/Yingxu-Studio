import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { openResearchPanel } from "./research_panel.js";
import { openKnowledgePanel } from "./knowledge_panel.js";
import { openTryonPanel } from "./tryon_panel.js";
import { openFoundationPanel } from "./foundation_panel.js";
import { openPlannerPanel } from "./planner_panel.js";
import { openCompilePanel } from "./compile_panel.js";
import { openApiSupportPanel } from "./api_support_panel.js";
import { openDetailPanel } from "./detail_panel.js";
import { openPosterPanel } from "./poster_panel.js";
import { openPromptLibraryPanel } from "./prompt_library_panel.js";
import { openOfflineAiPanel } from "./offline_ai_panel.js";

const TASK_NAMES = {
  text_to_image: "文生图",
  image_to_image: "图生图",
  inpaint: "局部重绘",
  image_resize: "调整尺寸",
  image_upscale: "图片放大",
  virtual_tryon: "虚拟换装",
  ecommerce_detail: "电商详情页双分支",
};

const state = {
  parsed: null,
  plan: null,
  compiled: null,
  validation: null,
  capabilities: null,
  managerMatches: [],
};

const $ = (selector, root = document) => root.querySelector(selector);

async function request(path, body = undefined) {
  const options = body === undefined
    ? {}
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
  const response = await api.fetchApi(`/workflow-architect/${path}`, options);
  const envelope = await response.json();
  if (!response.ok || !envelope.ok) {
    throw new Error(envelope?.error?.message || `请求失败 (${response.status})`);
  }
  return envelope.data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  });
  children.filter(Boolean).forEach((child) => node.append(child));
  return node;
}

function buildPanel() {
  const overlay = el("div", { class: "wa-overlay", "aria-hidden": "true" });
  const panel = el("section", {
    class: "wa-panel",
    role: "dialog",
"aria-label": "AI 工作流架构师 2.15.2",
  });
  panel.innerHTML = `
    <header>
<div><h2>AI 工作流架构师</h2><small>V2.15.2 · 任务尺寸与提示词隔离 · 离线AI</small></div>
      <button class="wa-close" title="关闭">×</button>
    </header>
    <main>
      <label>需求描述
        <textarea class="wa-prompt" maxlength="4000" placeholder="例如：参考这张商品图制作电商详情页主视觉，生成奶油白影棚场景，保持商品结构和 Logo，输出 1024×1536。"></textarea>
      </label>
      <div class="wa-grid">
        <label>运行模式
          <select class="wa-profile">
            <option value="stable">稳定模式</option>
            <option value="low_vram">低显存模式</option>
          </select>
        </label>
        <label>商品/参考图
          <select class="wa-source-image">
            <option value="">自动选择 input 第一张</option>
          </select>
        </label>
        <label>输出宽度（可选）<input class="wa-width" type="number" min="64" max="8192" placeholder="自动识别"></label>
        <label>输出高度（可选）<input class="wa-height" type="number" min="64" max="8192" placeholder="自动识别"></label>
        <label class="wa-check"><input class="wa-identity" type="checkbox" checked> 换装时严格保护身份</label>
        <label class="wa-check"><input class="wa-background" type="checkbox" checked> 换装时保持背景</label>
      </div>
      <div class="wa-actions">
        <button data-action="scan">扫描环境</button>
        <button data-action="research">电商生产资料库</button>
        <button data-action="knowledge">本地知识辅助</button>
        <button data-action="prompt-library">AI提示词库</button>
        <button data-action="offline-ai">本地离线AI</button>
        <button data-action="tryon">Klein双图换装（实验）</button>
        <button data-action="foundation">知识与环境验收</button>
        <button data-action="planner">大模型规划交换</button>
        <button data-action="compile-plan">规划方案→真实工作流</button>
        <button data-action="api-support">模型/API支持</button>
        <button data-action="detail-page">六模块详情页</button>
        <button data-action="brand-poster">品牌首屏精排</button>
        <button data-action="parse">解析需求</button>
        <button data-action="plan">生成方案</button>
        <button data-action="validate">验证工作流</button>
        <button data-action="manager" disabled>诊断并优化节点</button>
        <button data-action="open-manager">打开 Manager</button>
        <button data-action="scan-rebuild">刷新并重新构建</button>
        <button data-action="insert" disabled>插入画布</button>
        <button data-action="export" disabled>导出 JSON</button>
        <button data-action="clear">清空结果</button>
      </div>
      <div class="wa-status" role="status">正在读取环境能力…</div>
      <div class="wa-results"></div>
    </main>`;
  overlay.append(panel);
  document.body.append(overlay);
  return overlay;
}

function parseOptions(root) {
  const width = $(".wa-width", root).value.trim();
  const height = $(".wa-height", root).value.trim();
  if (Boolean(width) !== Boolean(height)) {
    throw new Error("手动尺寸必须同时填写宽度和高度");
  }
  const options = {
    strict_identity: $(".wa-identity", root).checked,
    preserve_background: $(".wa-background", root).checked,
    profile: $(".wa-profile", root).value,
  };
  if (width && height) {
    options.width_override = Number(width);
    options.height_override = Number(height);
  }
  return options;
}

function render(root) {
  const target = $(".wa-results", root);
  const { parsed, plan, validation, capabilities, managerMatches } = state;
  const errors = validation?.errors || [];
  const warnings = [
    ...(parsed?.warnings || []),
    ...(plan?.warnings || []),
    ...(validation?.warnings || []).map((item) => item.message),
  ];
  const relevant = new Set(
    (plan?.stages || []).flatMap((stage) =>
      (stage.capabilities || []).map((item) => item.split(".", 1)[0])
    )
  );
  const capabilityRows = capabilities
    ? Object.values(capabilities.capabilities)
        .filter((item) => !relevant.size || relevant.has(item.name))
        .map((item) => `<li class="${item.available ? "ok" : "missing"}">
          ${escapeHtml(item.name)}：${item.available ? escapeHtml(item.matched_node) : "缺失"}
        </li>`).join("")
    : "<li>尚未扫描</li>";
  const stages = (plan?.stages || []).map((stage) => `
    <li class="${stage.missing.length ? "missing" : "ok"}">
      ${escapeHtml(stage.title)}
      <small>${stage.missing.length ? `缺少：${escapeHtml(stage.missing.join("、"))}` : "就绪"}</small>
    </li>`).join("");
  const managerRows = managerMatches.length
    ? managerMatches.map((item) => `<li>
        <strong>${escapeHtml(item.capability)}</strong>：
        ${item.packs.length
          ? item.packs.map((pack) => `
              <span class="wa-manager-pack">
                ${escapeHtml(pack.label)}
                <small>${escapeHtml(pack.status)}</small>
                <button type="button" data-action="manager-mutate"
                  data-pack-id="${escapeHtml(pack.id)}"
                  data-manager-action="${escapeHtml(pack.action)}">
                  ${escapeHtml(pack.actionLabel)}
                </button>
                <button type="button" data-action="copy-pack"
                  data-pack="${escapeHtml(pack.label)}">复制名称</button>
              </span>`).join(" ")
          : "Manager 中未找到精确匹配包，可用候选节点名继续搜索"}
        <small>${escapeHtml(item.recommendation)}</small>
      </li>`).join("")
    : "<li>生成草稿后可查询 Manager；安装、启用、修复或更新均需用户确认。</li>";

  let planStatus = "—";
  let planClass = "";
  if (plan?.execution_ready) {
    planStatus = "可执行";
    planClass = "ok";
  } else if (plan?.creatable && state.compiled) {
    planStatus = "草稿已创建，待修复依赖";
    planClass = "missing";
  } else if (plan) {
    planStatus = "仅生成方案，缺少具体模板";
    planClass = "missing";
  }

  target.innerHTML = `
    <section><h3>识别结果</h3>
      <dl>
        <dt>任务</dt><dd>${escapeHtml(TASK_NAMES[parsed?.spec?.task] || "—")}</dd>
        <dt>运行模式</dt><dd>${parsed?.spec?.execution?.low_vram ? "低显存" : "稳定"}</dd>
        <dt>输出</dt><dd>${parsed?.spec ? `${parsed.spec.output.width}×${parsed.spec.output.height}` : "—"}</dd>
        <dt>置信度</dt><dd>${parsed ? parsed.confidence : "—"}</dd>
        <dt>方案状态</dt><dd class="${planClass}">${planStatus}</dd>
      </dl>
    </section>
    <section><h3>方案阶段</h3><ul>${stages || "<li>尚未生成方案</li>"}</ul></section>
    <section><h3>相关环境能力</h3><ul>${capabilityRows || "<li>未发现相关能力</li>"}</ul></section>
    <section><h3>Manager 建议</h3><ul>${managerRows}</ul></section>
    <section><h3>校验错误</h3><ul>${
      errors.length
        ? errors.map((item) => `<li class="missing">${escapeHtml(item.message)}</li>`).join("")
        : "<li>无</li>"
    }</ul></section>
    <section><h3>警告与说明</h3><ul>${
      warnings.length
        ? warnings.map((item) => `<li>${escapeHtml(item.message || item)}</li>`).join("")
        : "<li>无</li>"
    }</ul></section>`;

  const canUse = Boolean(
    state.compiled &&
    state.compiled.execution_ready &&
    validation?.execution_ready
  );
  $('[data-action="insert"]', root).disabled = !canUse;
  $('[data-action="export"]', root).disabled = !state.compiled;
  const managerSuggestions =
    state.compiled?.dependency_report?.manager_suggestions ||
    plan?.manager_suggestions ||
    [];
  $('[data-action="manager"]', root).disabled = !managerSuggestions.length;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function refreshSourceImages(root) {
  const select = $(".wa-source-image", root);
  if (!select) return;
  const previous = select.value;
  const definition = state.capabilities?.nodes?.LoadImage;
  const input = definition?.inputs?.find((item) => item.name === "image");
  const choices = Array.isArray(input?.choices) ? input.choices : [];
  select.replaceChildren(
    el("option", { value: "", text: "自动选择 input 第一张" }),
    ...choices.map((name) => el("option", { value: name, text: name }))
  );
  if (choices.includes(previous)) select.value = previous;
}

async function parse(root) {
  state.parsed = await request("parse", {
    text: $(".wa-prompt", root).value,
    options: parseOptions(root),
  });
  state.plan = null;
  state.compiled = null;
  state.validation = null;
  state.managerMatches = [];
  if (!state.parsed.recognized) throw new Error(state.parsed.warnings[0]);
}

async function plan(root) {
  // Always reparse the visible form so editing a completed request cannot
  // accidentally reuse the previous TaskSpec.
  await parse(root);
  const result = await request("plan", { spec: state.parsed.spec });
  state.plan = result.plan;
  state.validation = result.validation;
  state.compiled = null;
  state.managerMatches = [];
  if (state.plan.creatable) {
    const compiledResult = await request("compile", {
      spec: state.parsed.spec,
      allow_draft: true,
      input_overrides: {
        source_image: $(".wa-source-image", root).value || undefined,
      },
    });
    state.plan = compiledResult.plan;
    state.validation = compiledResult.validation;
    state.compiled = compiledResult.compiled;
  }
}

async function validate(root) {
  if (state.compiled) {
    state.validation = await request("validate", { compiled: state.compiled });
  } else {
    await plan(root);
  }
}

async function findManagerMatches() {
  const suggestions =
    state.compiled?.dependency_report?.manager_suggestions ||
    state.plan?.manager_suggestions ||
    [];
  if (!suggestions.length) return;
  const response = await api.fetchApi("/customnode/getmappings?mode=cache");
  if (!response.ok) throw new Error("ComfyUI Manager 不可用或尚未加载缓存");
  const raw = await response.json();
  const mappings = raw?.data && typeof raw.data === "object" ? raw.data : raw;
  let managerChannel = "default";
  let nodePacks = {};
  try {
    const listResponse = await api.fetchApi(
      "/customnode/getlist?mode=cache&skip_update=false"
    );
    if (listResponse.ok) {
      const listRaw = await listResponse.json();
      const listData =
        listRaw?.data && typeof listRaw.data === "object" ? listRaw.data : listRaw;
      managerChannel = listData?.channel || managerChannel;
      nodePacks = listData?.node_packs || {};
    }
  } catch {
    // Mapping-only mode still provides useful package search suggestions.
  }
  state.managerMatches = suggestions.map((suggestion) => {
    const needles = [
      ...(suggestion.candidate_nodes || []),
      ...(suggestion.search_terms || []),
    ].map((item) => String(item).toLowerCase());
    const packNames = Object.entries(mappings || {})
      .filter(([pack, value]) => {
        const haystack = `${pack} ${JSON.stringify(value)}`.toLowerCase();
        return needles.some((needle) => haystack.includes(needle));
      })
      .map(([pack]) => pack)
      .slice(0, 8);
    const packs = packNames.map((pack) =>
      managerPackAction(pack, nodePacks, managerChannel)
    );
    return {
      capability: suggestion.capability,
      packs,
      recommendation: packs.length
        ? "操作会交给 Manager 队列，并遵守 Manager 的安全级别；完成后通常需要重启。"
        : `搜索：${(suggestion.search_terms || []).join(" / ")}`,
    };
  });
}

function managerPackAction(pack, nodePacks, channel) {
  const needle = String(pack).toLowerCase();
  const match = Object.entries(nodePacks || {}).find(([id, item]) => {
    const haystack = `${id} ${JSON.stringify(item)}`.toLowerCase();
    return id.toLowerCase() === needle || haystack.includes(needle);
  });
  const [id, item] = match || [pack, {}];
  const activeVersion = item.active_version;
  const installed = Boolean(
    item.installed === true ||
    String(item.installed).toLowerCase() === "true" ||
    activeVersion
  );
  const disabled = Boolean(
    item.enabled === false ||
    item.disabled === true ||
    String(item.state || "").toLowerCase().includes("disabled") ||
    String(item.status || "").toLowerCase().includes("disabled")
  );
  const updatable = Boolean(item.updatable);
  let action = "install";
  let actionLabel = "安装";
  let status = "未安装";
  if (disabled) {
    action = "enable";
    actionLabel = "启用";
    status = "已安装但禁用";
  } else if (installed && updatable) {
    action = "update";
    actionLabel = "更新";
    status = `可更新${activeVersion ? ` · ${activeVersion}` : ""}`;
  } else if (installed) {
    action = "fix";
    actionLabel = "修复依赖";
    status = `已安装但节点不可用${activeVersion ? ` · ${activeVersion}` : ""}`;
  }
  return {
    id: String(id),
    label: String(item.title || item.name || pack),
    status,
    action,
    actionLabel,
    payload: {
      ...item,
      id,
      ui_id: `workflow-architect:${id}`,
      version: item.version || activeVersion || "unknown",
      selected_version: item.selected_version || "latest",
      channel,
      mode: "cache",
    },
  };
}

async function copyManagerPack(pack) {
  await navigator.clipboard.writeText(pack);
}

function findManagerPack(packId) {
  for (const match of state.managerMatches) {
    const pack = match.packs.find((item) => item.id === packId);
    if (pack) return pack;
  }
  return null;
}

async function mutateManagerPack(packId, action) {
  const pack = findManagerPack(packId);
  if (!pack) throw new Error("Manager 候选包已经失效，请重新诊断");
  const labels = {
    install: "安装",
    enable: "启用",
    update: "更新",
    fix: "修复依赖",
  };
  const label = labels[action] || action;
  if (!confirm(
    `${label}“${pack.label}”会修改本地 ComfyUI 环境，并可能需要重启。继续吗？`
  )) return;

  const payload = structuredClone(pack.payload);
  let endpoint = `/manager/queue/${action}`;
  if (action === "enable") {
    endpoint = "/manager/queue/install";
    payload.skip_post_install = true;
  }
  const response = await api.fetchApi(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Manager 拒绝${label}操作：${message || response.status}`
    );
  }
  const start = await api.fetchApi("/manager/queue/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!start.ok && start.status !== 201) {
    throw new Error("动作已加入 Manager，但队列未能启动，请打开 Manager 检查");
  }
}

function openManager() {
  const candidates = [...document.querySelectorAll("button, [role='button']")];
  const button = candidates.find((item) => {
    if (item.closest(".wa-panel")) return false;
    const label = `${item.textContent || ""} ${item.title || ""} ${item.getAttribute("aria-label") || ""}`;
    return /comfyui\s*manager|^\s*manager\s*$/i.test(label);
  });
  if (!button) throw new Error("没有找到 Manager 按钮，请确认已安装 ComfyUI-Manager");
  button.click();
}

function exportJson() {
  if (!state.compiled) return;
  const blob = new Blob([JSON.stringify(state.compiled.ui_workflow, null, 2)], {
    type: "application/json",
  });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `workflow-architect-${state.parsed?.spec?.task || "workflow"}-v2.15.2.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function insertWorkflow(compiled) {
  const workflow = compiled.ui_workflow;
  if (!workflow?.nodes?.length) throw new Error("没有可插入的节点");
  if (!confirm(`将插入 ${workflow.nodes.length} 个节点和 ${workflow.groups.length} 个分组。继续吗？`)) return;

  const graph = app.graph;
  const created = [];
  const groups = [];
  const byOldId = new Map();
  const viewport = app.canvas?.convertEventToCanvasOffset
    ? app.canvas.convertEventToCanvasOffset({
        clientX: window.innerWidth / 2,
        clientY: window.innerHeight / 2,
      })
    : [100, 100];
  const minX = Math.min(...workflow.nodes.map((item) => item.pos[0]));
  const minY = Math.min(...workflow.nodes.map((item) => item.pos[1]));
  graph.beforeChange?.();
  try {
    for (const data of workflow.nodes) {
      const node = LiteGraph.createNode(data.type, data.title);
      if (!node) throw new Error(`当前前端无法创建节点 ${data.type}`);
      const clean = structuredClone(data);
      delete clean.id;
      clean.inputs?.forEach((input) => { input.link = null; });
      clean.outputs?.forEach((output) => { output.links = null; });
      node.configure(clean);
      node.pos = [viewport[0] + data.pos[0] - minX, viewport[1] + data.pos[1] - minY];
      graph.add(node);
      created.push(node);
      byOldId.set(data.id, node);
    }
    for (const link of workflow.links) {
      const source = byOldId.get(link[1]);
      const target = byOldId.get(link[3]);
      if (!source || !target) throw new Error("连线引用无效");
      source.connect(link[2], target, link[4]);
    }
    for (const item of workflow.groups || []) {
      const group = new LiteGraph.LGraphGroup(item.title);
      group.pos = [viewport[0] + item.bounding[0] - minX, viewport[1] + item.bounding[1] - minY];
      group.size = [item.bounding[2], item.bounding[3]];
      group.color = item.color;
      group.font_size = item.font_size;
      graph.add(group);
      groups.push(group);
    }
    app.canvas.setDirty(true, true);
  } catch (error) {
    groups.forEach((group) => graph.remove(group));
    created.reverse().forEach((node) => graph.remove(node));
    throw error;
  } finally {
    graph.afterChange?.();
  }
}

function installButtonAndPanel() {
  if (document.querySelector(".wa-launcher") || document.querySelector(".wa-overlay")) return;
  if (!document.querySelector('link[data-workflow-architect-style="true"]')) {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.dataset.workflowArchitectStyle = "true";
    stylesheet.href = new URL("../css/workflow_architect.css", import.meta.url).href;
    document.head.append(stylesheet);
  }

  const root = buildPanel();
  const launcher = el("button", {
    class: "wa-launcher",
    type: "button",
    "aria-label": "打开 AI 工作流架构师 2.15.2",
    text: "AI 工作流架构师 2.15.2",
    onclick: () => {
      root.classList.add("open");
      root.setAttribute("aria-hidden", "false");
    },
  });
  document.body.append(launcher);
  $(".wa-close", root).addEventListener("click", () => {
    root.classList.remove("open");
    root.setAttribute("aria-hidden", "true");
  });
  root.addEventListener("click", async (event) => {
    const action = event.target?.dataset?.action;
    if (!action) return;
    const status = $(".wa-status", root);
    status.textContent = "处理中…";
    try {
      if (action === "scan") {
        state.capabilities = await request("refresh", {});
        refreshSourceImages(root);
      }
      if (action === "scan-rebuild") {
        state.capabilities = await request("refresh", {});
        refreshSourceImages(root);
        await plan(root);
      }
      if (action === "parse") await parse(root);
      if (action === "research") await openResearchPanel();
      if (action === "knowledge") openKnowledgePanel($(".wa-prompt", root).value);
      if (action === "tryon") openTryonPanel();
      if (action === "foundation") openFoundationPanel();
      if (action === "planner") openPlannerPanel();
      if (action === "compile-plan") openCompilePanel();
      if (action === "api-support") openApiSupportPanel();
      if (action === "detail-page") openDetailPanel();
      if (action === "brand-poster") openPosterPanel();
      if (action === "prompt-library") openPromptLibraryPanel();
      if (action === "offline-ai") openOfflineAiPanel();
      if (action === "plan") await plan(root);
      if (action === "validate") await validate(root);
      if (action === "manager") await findManagerMatches();
      if (action === "open-manager") openManager();
      if (action === "copy-pack") {
        await copyManagerPack(event.target.dataset.pack || "");
        openManager();
      }
      if (action === "manager-mutate") {
        await mutateManagerPack(
          event.target.dataset.packId || "",
          event.target.dataset.managerAction || ""
        );
        status.textContent = "Manager 已接收任务；完成后重启并点击“刷新并重新构建”";
      }
      if (action === "insert") insertWorkflow(state.compiled);
      if (action === "export") exportJson();
      if (action === "clear") {
        state.parsed = null;
        state.plan = null;
        state.compiled = null;
        state.validation = null;
        state.managerMatches = [];
        $(".wa-prompt", root).value = "";
      }
      if (action !== "manager-mutate") status.textContent = "完成";
    } catch (error) {
      status.textContent = `失败：${error.message}`;
    }
    render(root);
  });
  render(root);
}

function ensureUiInstalled() {
  if (document.body) installButtonAndPanel();
  else window.addEventListener("DOMContentLoaded", installButtonAndPanel, { once: true });
}

ensureUiInstalled();

app.registerExtension({
  name: "workflow.architect.v2.12.0", // Stable extension ID: preserve existing registrations/preferences.
  async setup() {
    ensureUiInstalled();
    try {
      state.capabilities = await request("capabilities");
      const root = document.querySelector(".wa-overlay");
      if (root) {
        refreshSourceImages(root);
        $(".wa-status", root).textContent = "环境能力已加载";
        render(root);
      }
    } catch {
      // The panel remains usable and exposes a manual refresh action.
    }
  },
});
