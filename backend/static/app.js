const state = {
  token: "",
  repo: null,
  selectedPath: "",
  selectedContent: "",
  selectedFile: null,
  lastSavedBranch: "",
  dirty: false,
  authWarning: "",
  treeExpanded: true,
  auth: null,
  runtimeAuthSource: "",
};

const elements = {
  connectGithubButton: document.getElementById("connect-github-button"),
  installGithubAppButton: document.getElementById("install-github-app-button"),
  disconnectGithubButton: document.getElementById("disconnect-github-button"),
  authStatus: document.getElementById("auth-status"),
  authPicker: document.getElementById("auth-picker"),
  installationField: document.getElementById("installation-field"),
  installationSelect: document.getElementById("installation-select"),
  repoListCaption: document.getElementById("repo-list-caption"),
  repoListSearch: document.getElementById("repo-list-search"),
  repoList: document.getElementById("repo-list"),
  repoUrl: document.getElementById("repo-url"),
  baseBranch: document.getElementById("base-branch"),
  tokenInput: document.getElementById("token-input"),
  checkTokenButton: document.getElementById("check-token-button"),
  tokenStatus: document.getElementById("token-status"),
  loadRepoButton: document.getElementById("load-repo-button"),
  repoSummary: document.getElementById("repo-summary"),
  treeCaption: document.getElementById("tree-caption"),
  treeStats: document.getElementById("tree-stats"),
  treeSourcePill: document.getElementById("tree-source-pill"),
  expandTreeButton: document.getElementById("expand-tree-button"),
  collapseTreeButton: document.getElementById("collapse-tree-button"),
  treeSearch: document.getElementById("tree-search"),
  fileTree: document.getElementById("file-tree"),
  activePath: document.getElementById("active-path"),
  refPill: document.getElementById("ref-pill"),
  dirtyPill: document.getElementById("dirty-pill"),
  editor: document.getElementById("editor"),
  statusText: document.getElementById("status-text"),
  externalLink: document.getElementById("external-link"),
  selectionCard: document.getElementById("selection-card"),
  branchSuffix: document.getElementById("branch-suffix"),
  branchPreview: document.getElementById("branch-preview"),
  commitMessage: document.getElementById("commit-message"),
  saveButton: document.getElementById("save-button"),
  prTitle: document.getElementById("pr-title"),
  prBody: document.getElementById("pr-body"),
  prButton: document.getElementById("pr-button"),
  activityLog: document.getElementById("activity-log"),
  connectionPill: document.getElementById("connection-pill"),
};

window.__DEVEX_APP_LOADED__ = true;

function slugifyBranchSuffix(value) {
  const cleaned = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._/-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-/.]+|[-/.]+$/g, "");
  return cleaned || "session";
}

function branchPreview() {
  return `devex/${slugifyBranchSuffix(elements.branchSuffix.value || "session")}`;
}

function currentRef() {
  if (state.lastSavedBranch && state.lastSavedBranch === branchPreview()) {
    return state.lastSavedBranch;
  }
  return state.repo?.base_branch || "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatCount(value, label) {
  const mod10 = value % 10;
  const mod100 = value % 100;
  let form = label[2];

  if (mod10 === 1 && mod100 !== 11) {
    form = label[0];
  } else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
    form = label[1];
  }

  return `${value} ${form}`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "Размер неизвестен";
  }
  if (bytes < 1024) {
    return `${bytes} Б`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} КБ`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function countLines(text) {
  if (!text) {
    return 0;
  }
  return text.split(/\r\n|\r|\n/).length;
}

function treeSourceLabel(source) {
  return source === "contents" ? "Через API" : "Git-дерево";
}

function sourceLabel(source) {
  switch (source) {
    case "github-app":
      return "GitHub App";
    case "override":
      return "введенный токен";
    case ".env":
      return "токен из .env";
    case "anonymous":
      return "публичный режим";
    default:
      return "неизвестно";
  }
}

function currentTokenSource() {
  return state.token ? "override" : ".env";
}

function repoIsConnectedThroughApp() {
  if (!state.repo || !state.auth?.repositories?.length) {
    return false;
  }

  return state.auth.repositories.some((repository) => repository.full_name === state.repo.full_name);
}

function activeWriteSource() {
  if (state.token) {
    return "override";
  }
  if (state.runtimeAuthSource === "github-app" || state.runtimeAuthSource === ".env") {
    return state.runtimeAuthSource;
  }
  if (state.auth?.installation && repoIsConnectedThroughApp()) {
    return "github-app";
  }
  return state.runtimeAuthSource || "anonymous";
}

function hasWriteCredentials() {
  return activeWriteSource() === "github-app" || activeWriteSource() === ".env" || activeWriteSource() === "override";
}

function setConnection(text) {
  if (!elements.connectionPill) {
    return;
  }
  elements.connectionPill.textContent = text;
}

function syncConnectionPill() {
  if (state.auth?.installation) {
    setConnection("GitHub готов");
    return;
  }
  if (state.auth?.user) {
    setConnection("GitHub подключен");
    return;
  }
  if (state.repo) {
    setConnection(state.runtimeAuthSource ? sourceLabel(state.runtimeAuthSource) : "Подключено");
    return;
  }
  setConnection("Ожидание");
}

function setStatus(text, kind = "neutral") {
  elements.statusText.className = kind === "neutral" ? "" : kind;
  elements.statusText.textContent = text;
}

function pushActivity(title, body, link) {
  if (!elements.activityLog) {
    return;
  }

  elements.activityLog.hidden = false;

  const item = document.createElement("article");
  item.className = "activity-item";

  const heading = document.createElement("strong");
  heading.textContent = title;
  item.appendChild(heading);

  const message = document.createElement("p");
  message.textContent = body;
  item.appendChild(message);

  if (link) {
    const anchor = document.createElement("a");
    anchor.className = "activity-link";
    anchor.href = link;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    anchor.textContent = link;
    item.appendChild(anchor);
  }

  const empty = elements.activityLog.querySelector(".activity-empty");
  if (empty) {
    empty.remove();
  }

  elements.activityLog.prepend(item);
}

function renderTokenStatus(data) {
  if (!elements.tokenStatus) {
    return;
  }

  if (!data) {
    elements.tokenStatus.hidden = true;
    elements.tokenStatus.innerHTML = "";
    return;
  }

  elements.tokenStatus.hidden = false;

  const displayName = data.name ? `${data.name} (@${data.login})` : `@${data.login}`;
  const scopes = data.scopes.length ? data.scopes.join(", ") : "скрыто";
  const resetText = data.rate_limit.reset_at ? new Date(data.rate_limit.reset_at).toLocaleString() : "неизвестно";
  const repoAccess = data.repository_access
    ? `Доступ к ${data.repository_access.full_name}: чтение ${data.repository_access.permissions.pull ? "да" : "нет"}, запись ${data.repository_access.permissions.push ? "да" : "нет"}`
    : "Доступ к репозиторию появится после проверки.";

  elements.tokenStatus.innerHTML = `
    <strong>${escapeHtml(displayName)}</strong>
    <span>Источник: ${escapeHtml(sourceLabel(data.token_source))}</span>
    <span>Токен: ${escapeHtml(data.token_masked || "скрыт")}</span>
    <span>Лимит: ${escapeHtml(String(data.rate_limit.remaining || "?"))}/${escapeHtml(String(data.rate_limit.limit || "?"))}</span>
    <span>Сброс: ${escapeHtml(resetText)}</span>
    <span>Права: ${escapeHtml(scopes)}</span>
    <span>${escapeHtml(repoAccess)}</span>
    <a href="${escapeHtml(data.html_url)}" target="_blank" rel="noreferrer">Профиль GitHub</a>
  `;
}

function renderTokenError(message, source = "unknown") {
  if (!elements.tokenStatus) {
    return;
  }

  elements.tokenStatus.hidden = false;
  elements.tokenStatus.innerHTML = `
    <strong class="danger">Ошибка токена</strong>
    <span>Источник: ${escapeHtml(sourceLabel(source))}</span>
    <span>${escapeHtml(message)}</span>
  `;
}

function renderAuthStatus(data) {
  const appName = data?.app?.name || "GitHub App";
  const appLink = data?.app?.page_url || data?.urls?.app_page || "";

  if (!data || !data.configured) {
    const missing = data?.missing_config?.length ? data.missing_config.join(", ") : "конфигурация";
    elements.authStatus.innerHTML = `
      <strong class="danger">GitHub App не настроен</strong>
      <span>${escapeHtml(missing)}</span>
    `;
    return;
  }

  if (!data.user) {
    elements.authStatus.innerHTML = `
      <strong>${escapeHtml(appName)}</strong>
      <span>GitHub не подключен.</span>
      ${appLink ? `<a href="${escapeHtml(appLink)}" target="_blank" rel="noreferrer">Страница приложения</a>` : ""}
    `;
    return;
  }

  const installationLine = data.installation
    ? `Установка: ${data.installation.account_login || "GitHub"}`
    : data.installations.length
      ? "Выбери установку ниже."
      : "Нужна установка GitHub App.";
  const repoLine = data.installation
    ? `${formatCount(data.repositories.length, ["репозиторий", "репозитория", "репозиториев"])}`
    : "Установка не выбрана.";
  const errorLine = data.connection_error
    ? `<span class="danger">${escapeHtml(data.connection_error)}</span>`
    : "";

  elements.authStatus.innerHTML = `
    <strong>${escapeHtml(data.user.name ? `${data.user.name} (@${data.user.login})` : `@${data.user.login}`)}</strong>
    <span>${escapeHtml(installationLine)}</span>
    <span>${escapeHtml(repoLine)}</span>
    ${errorLine}
    <a href="${escapeHtml(data.user.html_url)}" target="_blank" rel="noreferrer">Профиль GitHub</a>
    ${appLink ? `<a href="${escapeHtml(appLink)}" target="_blank" rel="noreferrer">Страница приложения</a>` : ""}
  `;
}

function renderAuthPicker(data) {
  const installations = data?.installations || [];

  elements.authPicker.hidden = !data?.configured || !data?.user || installations.length === 0;
  elements.installationField.hidden = installations.length === 0;

  if (installations.length) {
    elements.installationSelect.innerHTML = installations
      .map((installation) => {
        const label = installation.account_login
          ? `${installation.account_login} · ${installation.target_type || "аккаунт"}`
          : `Установка ${installation.id}`;
        return `<option value="${escapeHtml(String(installation.id))}">${escapeHtml(label)}</option>`;
      })
      .join("");
    const selectedInstallationId =
      data.installation?.id != null ? String(data.installation.id) : String(installations[0].id);
    elements.installationSelect.value = selectedInstallationId;
  } else {
    elements.installationSelect.innerHTML = "";
  }

  elements.installationSelect.disabled = installations.length <= 1;
}

function renderRepositoryList() {
  const repositories = state.auth?.repositories || [];
  const query = (elements.repoListSearch.value || "").trim().toLowerCase();

  if (!state.auth?.user) {
    elements.repoListCaption.textContent = "Список пуст.";
    elements.repoList.innerHTML = '<p class="empty-copy">Список пуст.</p>';
    return;
  }

  if (!repositories.length) {
    elements.repoListCaption.textContent = "Нет репозиториев.";
    elements.repoList.innerHTML = '<p class="empty-copy">Нет данных.</p>';
    return;
  }

  const filtered = repositories.filter((repository) => {
    if (!query) {
      return true;
    }
    return repository.full_name.toLowerCase().includes(query);
  });

  elements.repoListCaption.textContent = `${formatCount(repositories.length, ["репозиторий", "репозитория", "репозиториев"])} доступно.`;

  if (!filtered.length) {
    elements.repoList.innerHTML = '<p class="empty-copy">Ничего не найдено.</p>';
    return;
  }

  elements.repoList.innerHTML = filtered
    .map((repository) => {
      const activeClass = state.repo?.full_name === repository.full_name ? " active" : "";
      return `
        <button class="repo-list-item${activeClass}" type="button" data-full-name="${escapeHtml(repository.full_name)}">
          <span class="repo-list-name">${escapeHtml(repository.full_name)}</span>
          <span class="repo-list-meta">${escapeHtml(repository.default_branch)} · ${repository.private ? "приватный" : "публичный"}</span>
        </button>
      `;
    })
    .join("");

  elements.repoList.querySelectorAll(".repo-list-item").forEach((button) => {
    button.addEventListener("click", async () => {
      const repository = repositories.find((item) => item.full_name === button.dataset.fullName);
      if (!repository) {
        return;
      }

      elements.repoUrl.value = repository.html_url || repository.full_name;
      elements.baseBranch.value = repository.default_branch || "";
      await loadRepository();
    });
  });
}

function renderRepoSummary() {
  if (!elements.repoSummary) {
    return;
  }

  if (!state.repo) {
    elements.repoSummary.innerHTML = '<p class="repo-empty">Репозиторий не открыт.</p>';
    return;
  }

  const stats = state.repo.tree_stats;
  elements.repoSummary.innerHTML = `
    <strong>${escapeHtml(state.repo.full_name)}</strong>
    <span>${state.repo.private ? "Приватный" : "Публичный"}</span>
    <span>Ветка: ${escapeHtml(state.repo.base_branch)}</span>
    <span>${escapeHtml(formatCount(stats.files, ["файл", "файла", "файлов"]))} · ${escapeHtml(formatCount(stats.directories, ["папка", "папки", "папок"]))}</span>
    <span>${escapeHtml(sourceLabel(state.runtimeAuthSource || "anonymous"))}</span>
    <a href="${escapeHtml(state.repo.html_url)}" target="_blank" rel="noreferrer">Открыть репозиторий</a>
  `;
}

function renderTreeStats() {
  if (!elements.treeStats || !elements.treeSourcePill) {
    return;
  }

  if (!state.repo) {
    elements.treeSourcePill.textContent = "Дерево";
    elements.treeStats.innerHTML = `
      <span class="stat-chip">0 файлов</span>
      <span class="stat-chip">0 папок</span>
    `;
    return;
  }

  const stats = state.repo.tree_stats;
  elements.treeSourcePill.textContent = treeSourceLabel(state.repo.tree_source);
  elements.treeStats.innerHTML = `
    <span class="stat-chip">${escapeHtml(formatCount(stats.files, ["файл", "файла", "файлов"]))}</span>
    <span class="stat-chip">${escapeHtml(formatCount(stats.directories, ["папка", "папки", "папок"]))}</span>
    <span class="stat-chip">Глубина ${escapeHtml(String(stats.max_depth || 0))}</span>
  `;
}

function renderSelectionCard() {
  if (!elements.selectionCard) {
    return;
  }

  if (!state.selectedFile) {
    elements.selectionCard.innerHTML = `
      <p class="panel-label">Файл</p>
      <p class="selection-empty">Выбери файл.</p>
    `;
    return;
  }

  const liveContent = state.selectedPath ? elements.editor.value : state.selectedContent;
  const lineCount = countLines(liveContent);
  const byteSize = new TextEncoder().encode(liveContent).length;

  let writeMode = "";
  if (state.authWarning) {
    writeMode = state.authWarning;
  } else if (!hasWriteCredentials()) {
    writeMode = "Сейчас только чтение.";
  } else {
    writeMode = `Можно коммитить через ${sourceLabel(activeWriteSource())}.`;
  }

  elements.selectionCard.innerHTML = `
    <p class="panel-label">Файл</p>
    <div class="selection-grid">
      <p class="selection-path">${escapeHtml(state.selectedFile.path)}</p>
      <span>Ветка: ${escapeHtml(currentRef())}</span>
      <span>Размер: ${escapeHtml(formatBytes(byteSize || state.selectedFile.size || 0))}</span>
      <span>${escapeHtml(formatCount(lineCount, ["строка", "строки", "строк"]))}</span>
      <span>${escapeHtml(writeMode)}</span>
    </div>
  `;
}

function renderTreeNode(node, filterValue = "") {
  const query = filterValue.trim().toLowerCase();

  if (query && !node.path.toLowerCase().includes(query) && !node.name.toLowerCase().includes(query)) {
    if (node.type !== "dir") {
      return "";
    }

    const childMarkup = node.children
      .map((child) => renderTreeNode(child, filterValue))
      .filter(Boolean)
      .join("");

    if (!childMarkup) {
      return "";
    }

    return `
      <details class="tree-folder" open>
        <summary><span class="tree-label">${escapeHtml(node.name)}</span></summary>
        ${childMarkup}
      </details>
    `;
  }

  if (node.type === "file") {
    const activeClass = node.path === state.selectedPath ? " active" : "";
    return `
      <button class="tree-file${activeClass}" type="button" data-path="${escapeHtml(node.path)}">
        ${escapeHtml(node.name)}
      </button>
    `;
  }

  const childMarkup = node.children
    .map((child) => renderTreeNode(child, filterValue))
    .filter(Boolean)
    .join("");

  const openAttr = query || state.treeExpanded ? " open" : "";
  return `
    <details class="tree-folder"${openAttr}>
      <summary><span class="tree-label">${escapeHtml(node.name)}</span></summary>
      ${childMarkup}
    </details>
  `;
}

function renderTree() {
  if (!state.repo) {
    elements.fileTree.innerHTML = "";
    return;
  }

  const markup = state.repo.tree
    .map((node) => renderTreeNode(node, elements.treeSearch.value))
    .filter(Boolean)
    .join("");

  elements.fileTree.innerHTML = markup || '<p class="repo-empty">Ничего не найдено.</p>';

  elements.fileTree.querySelectorAll(".tree-file").forEach((button) => {
    button.addEventListener("click", () => {
      openFile(button.dataset.path);
    });
  });
}

function syncDirtyState() {
  state.dirty = elements.editor.value !== state.selectedContent;
  elements.dirtyPill.textContent = state.dirty ? "изменено" : "чисто";
  elements.dirtyPill.className = "meta-chip";
  elements.saveButton.disabled = !state.repo || !state.selectedPath || !state.dirty || !hasWriteCredentials();
}

function syncActionState() {
  syncDirtyState();
  const hasMatchingBranch = Boolean(state.lastSavedBranch) && state.lastSavedBranch === branchPreview();
  elements.prButton.disabled = !state.repo || !hasMatchingBranch || !hasWriteCredentials();
}

function updateBranchPreview() {
  elements.branchPreview.textContent = branchPreview();
}

function resetEditorState() {
  state.selectedPath = "";
  state.selectedContent = "";
  state.selectedFile = null;
  state.lastSavedBranch = "";
  state.dirty = false;
  elements.editor.value = "";
  elements.activePath.textContent = "Файл не выбран.";
  elements.refPill.textContent = state.repo?.base_branch || "базовая ветка";
  elements.commitMessage.value = "";
  elements.externalLink.hidden = true;
  renderSelectionCard();
  syncActionState();
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Ошибка запроса.");
  }
  return data;
}

async function getJson(path) {
  const response = await fetch(path);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Ошибка запроса.");
  }
  return data;
}

async function refreshAuthStatus(options = {}) {
  const { silent = true } = options;

  try {
    const data = await getJson("/api/auth/status");
    state.auth = data;
    renderAuthStatus(data);
    renderAuthPicker(data);
    renderRepositoryList();
    renderSelectionCard();
    renderRepoSummary();
    syncActionState();
    syncConnectionPill();

    if (!silent) {
      if (data.installation) {
        setStatus(`GitHub подключен: ${data.user.login}.`, "success");
      } else if (data.user) {
        setStatus(`GitHub подключен: ${data.user.login}.`, "success");
      } else if (data.configured) {
        setStatus("Подключи GitHub.", "warning");
      } else {
        setStatus("GitHub App не настроен.", "warning");
      }
    }
  } catch (error) {
    if (!silent) {
      setStatus(error.message, "danger");
    }
    elements.authStatus.innerHTML = `
      <strong class="danger">Ошибка GitHub</strong>
      <span>${escapeHtml(error.message)}</span>
    `;
    syncConnectionPill();
  }
}

async function selectInstallation() {
  const installationId = elements.installationSelect.value;
  if (!installationId) {
    return;
  }

  setStatus("Переключаю установку...");

  try {
    const data = await postJson("/api/auth/installation/select", {
      installation_id: Number(installationId),
    });
    state.auth = data;
    renderAuthStatus(data);
    renderAuthPicker(data);
    renderRepositoryList();
    renderSelectionCard();
    renderRepoSummary();
    syncActionState();
    syncConnectionPill();
    setStatus("Установка обновлена.", "success");
  } catch (error) {
    setStatus(error.message, "danger");
  }
}

async function disconnectGithub() {
  setStatus("Отключаю GitHub...");

  try {
    await postJson("/api/auth/disconnect", {});
    state.auth = null;
    state.runtimeAuthSource = state.runtimeAuthSource === "github-app" ? "" : state.runtimeAuthSource;
    renderRepositoryList();
    renderSelectionCard();
    syncActionState();
    await refreshAuthStatus({ silent: true });
    setStatus("GitHub отключен.", "success");
    pushActivity("GitHub отключен", "Сессия очищена.");
  } catch (error) {
    setStatus(error.message, "danger");
  }
}

async function loadRepository() {
  const repoUrl = elements.repoUrl.value.trim();
  if (!repoUrl) {
    setStatus("Вставь ссылку на репозиторий.", "warning");
    return;
  }

  setConnection("Загрузка");
  setStatus("Загружаю репозиторий...");

  try {
    state.token = elements.tokenInput.value.trim();
    const data = await postJson("/api/repository/load", {
      repo_url: repoUrl,
      base_branch: elements.baseBranch.value.trim(),
      token: state.token,
    });

    state.repo = {
      ...data.repository,
      tree: data.tree,
      tree_stats: data.tree_stats,
      tree_source: data.tree_source,
      truncated: data.truncated,
    };
    state.runtimeAuthSource = data.auth_source || "";
    state.authWarning = data.auth_warning || "";
    state.treeExpanded = true;

    elements.baseBranch.value = state.repo.base_branch;
    elements.prTitle.value = "";
    elements.prBody.value = `Изменения для ${state.repo.full_name}.`;
    resetEditorState();
    renderRepoSummary();
    renderTreeStats();
    renderTree();
    renderRepositoryList();

    if (elements.treeCaption) {
      elements.treeCaption.textContent = `${formatCount(state.repo.tree_stats.files, ["файл", "файла", "файлов"])}`;
    }

    syncConnectionPill();
    if (state.authWarning) {
      renderTokenError(state.authWarning, currentTokenSource());
      setStatus(state.authWarning, "warning");
      pushActivity("Только чтение", state.authWarning);
    } else {
      setStatus(`Загружен ${state.repo.full_name}.`, "success");
    }

    pushActivity(
      "Репозиторий загружен",
      `${state.repo.full_name}: ${state.repo.tree_stats.files} файлов, ${state.repo.tree_stats.directories} папок.`,
      state.repo.html_url,
    );

    if (!elements.branchSuffix.value.trim()) {
      elements.branchSuffix.value = `${state.repo.name}-change`;
    }
    updateBranchPreview();
    elements.prTitle.value = `Проверить ${branchPreview()}`;
    renderSelectionCard();
    syncActionState();
  } catch (error) {
    setConnection("Ошибка");
    setStatus(error.message, "danger");
  }
}

async function checkToken() {
  setConnection("Проверка");
  setStatus("Проверяю токен...");

  try {
    const data = await postJson("/api/token/check", {
      token: elements.tokenInput.value.trim(),
      owner: state.repo?.owner || "",
      repo: state.repo?.name || "",
    });
    state.token = elements.tokenInput.value.trim();
    state.authWarning = "";
    renderTokenStatus(data);
    renderSelectionCard();
    syncActionState();
    setConnection("Токен");
    setStatus(`Авторизация: ${data.login}.`, "success");
    pushActivity(
      "Токен проверен",
      `${data.login} · ${data.rate_limit.remaining}/${data.rate_limit.limit}`,
      data.html_url,
    );
  } catch (error) {
    renderTokenError(error.message, elements.tokenInput.value.trim() ? "override" : ".env");
    setConnection("Ошибка токена");
    setStatus(error.message, "danger");
    pushActivity("Ошибка токена", error.message);
  }
}

async function openFile(path) {
  if (!state.repo) {
    return;
  }

  if (state.dirty) {
    const confirmed = window.confirm("Есть несохраненные изменения. Открыть другой файл?");
    if (!confirmed) {
      return;
    }
  }

  setStatus(`Открываю ${path}...`);

  try {
    const data = await postJson("/api/file/read", {
      owner: state.repo.owner,
      repo: state.repo.name,
      path,
      ref: currentRef(),
      token: state.token,
    });

    state.selectedPath = data.path;
    state.selectedContent = data.content;
    state.selectedFile = data;
    state.dirty = false;
    state.runtimeAuthSource = data.auth_source || state.runtimeAuthSource;
    state.authWarning = data.auth_warning || state.authWarning;

    elements.editor.value = data.content;
    elements.activePath.textContent = data.path;
    elements.commitMessage.value = `Обновить ${data.path}`;
    elements.refPill.textContent = currentRef();
    elements.externalLink.href = `${state.repo.html_url}/blob/${currentRef()}/${data.path}`;
    elements.externalLink.hidden = false;
    renderTree();
    renderSelectionCard();
    syncActionState();
    syncConnectionPill();

    if (data.auth_warning) {
      renderTokenError(data.auth_warning, currentTokenSource());
      setStatus(data.auth_warning, "warning");
    } else {
      setStatus(`Открыт ${data.path}.`, "success");
    }
  } catch (error) {
    setStatus(error.message, "danger");
  }
}

async function saveFile() {
  if (!state.repo || !state.selectedPath) {
    setStatus("Сначала выбери файл.", "warning");
    return;
  }

  const branchSuffix = elements.branchSuffix.value.trim();
  if (!branchSuffix) {
    setStatus("Укажи суффикс ветки.", "warning");
    return;
  }

  setStatus(`Коммит в ${branchPreview()}...`);

  try {
    const data = await postJson("/api/file/save", {
      owner: state.repo.owner,
      repo: state.repo.name,
      path: state.selectedPath,
      content: elements.editor.value,
      base_branch: elements.baseBranch.value.trim() || state.repo.base_branch,
      branch_suffix: branchSuffix,
      commit_message: elements.commitMessage.value.trim(),
      token: state.token,
    });

    state.selectedContent = elements.editor.value;
    state.lastSavedBranch = data.branch;
    state.runtimeAuthSource = data.auth_source || state.runtimeAuthSource;
    if (state.selectedFile) {
      state.selectedFile.size = new TextEncoder().encode(elements.editor.value).length;
    }
    elements.refPill.textContent = data.branch;
    elements.externalLink.href = `${state.repo.html_url}/blob/${data.branch}/${state.selectedPath}`;
    renderSelectionCard();
    syncActionState();
    syncConnectionPill();
    elements.prTitle.value = elements.prTitle.value.trim() || `Проверить ${data.branch}`;

    setStatus(`Закоммичено в ${data.branch}.`, "success");
    pushActivity(
      "Коммит создан",
      `${state.selectedPath} -> ${data.branch}`,
      data.commit_url || null,
    );
  } catch (error) {
    setStatus(error.message, "danger");
  }
}

async function createPullRequest() {
  if (!state.repo) {
    return;
  }

  const branchSuffix = elements.branchSuffix.value.trim();
  if (!branchSuffix) {
    setStatus("Укажи суффикс ветки.", "warning");
    return;
  }

  setStatus(`Открываю PR для ${branchPreview()}...`);

  try {
    const data = await postJson("/api/pull-request", {
      owner: state.repo.owner,
      repo: state.repo.name,
      base_branch: elements.baseBranch.value.trim() || state.repo.base_branch,
      branch_suffix: branchSuffix,
      title: elements.prTitle.value.trim(),
      body: elements.prBody.value,
      token: state.token,
    });

    state.runtimeAuthSource = data.auth_source || state.runtimeAuthSource;
    syncConnectionPill();
    setStatus(`PR #${data.number} создан.`, "success");
    pushActivity("PR создан", data.title, data.url);
  } catch (error) {
    setStatus(error.message, "danger");
  }
}

elements.connectGithubButton.addEventListener("click", () => {
  const url = state.auth?.urls?.connect || "/auth/github/login";
  window.location.assign(url);
});

  elements.installGithubAppButton.addEventListener("click", () => {
  const url = state.auth?.urls?.install || "/auth/github/install";
  window.location.assign(url);
});

elements.disconnectGithubButton.addEventListener("click", disconnectGithub);
elements.installationSelect.addEventListener("change", selectInstallation);
elements.repoListSearch.addEventListener("input", renderRepositoryList);
elements.loadRepoButton.addEventListener("click", loadRepository);
elements.checkTokenButton.addEventListener("click", checkToken);
elements.saveButton.addEventListener("click", saveFile);
elements.prButton.addEventListener("click", createPullRequest);
elements.expandTreeButton.addEventListener("click", () => {
  state.treeExpanded = true;
  renderTree();
});
elements.collapseTreeButton.addEventListener("click", () => {
  state.treeExpanded = false;
  renderTree();
});
elements.treeSearch.addEventListener("input", renderTree);
elements.editor.addEventListener("input", () => {
  syncActionState();
  renderSelectionCard();
});
elements.branchSuffix.addEventListener("input", () => {
  updateBranchPreview();
  if (state.repo) {
    elements.refPill.textContent = currentRef() || state.repo.base_branch;
    if (state.selectedPath) {
      elements.externalLink.href = `${state.repo.html_url}/blob/${currentRef()}/${state.selectedPath}`;
    }
  }
  renderSelectionCard();
  syncActionState();
  if (!elements.prTitle.value.trim()) {
    elements.prTitle.value = `Проверить ${branchPreview()}`;
  }
});

updateBranchPreview();
renderRepoSummary();
renderTreeStats();
renderSelectionCard();
syncActionState();
renderTokenStatus(null);
syncConnectionPill();
renderRepositoryList();
refreshAuthStatus();
