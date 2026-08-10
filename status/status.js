const STATUS_ENDPOINT = '/api/status.json';
const SESSION_ENDPOINT = '/auth/session';
const LOGIN_ENDPOINT = '/auth/login';
const LOGOUT_ENDPOINT = '/auth/logout';
const SSH_CONFIG_ENDPOINT = '/monitor/api/ssh-config';
const AUTO_REFRESH_SECONDS = 60;
const LOCALE_STORAGE_KEY = 'locale-preference';
const THEME_STORAGE_KEY = 'theme-preference';
const COLOR_THEME_STORAGE_KEY = 'color-theme-preference';

const LOCALES = {
  zh: { htmlLang: 'zh-CN', label: '中文' },
  en: { htmlLang: 'en', label: 'English' },
  ja: { htmlLang: 'ja', label: '日本語' },
};

const TRANSLATIONS = {
  zh: {
    pageTitle: '实验室服务状态', homeAria: '返回实验室服务状态首页', title: '实验室服务状态', subtitle: 'GPU 计算节点与服务可用性',
    language: '语言', themeToggle: '切换明暗模式', colorTheme: '切换配色主题', themes: { green: '翠绿', ocean: '海蓝', violet: '紫罗兰', amber: '暖橙', anime: '星樱', fighter: '斗魂' }, logout: '退出', summaryAria: '状态摘要',
    hero: { aria: '星樱实验室主题主视觉', title: '星樱计算观测室', subtitle: '在樱色晨光中，守望每一次计算。', badge: '原创主题 · 实时状态' },
    fightHero: { aria: '斗魂街头格斗主题主视觉', title: '街头斗魂观测站', subtitle: '以拳为信号，让每一次计算正面交锋。', badge: '原创格斗主题 · 实时状态' },
    refresh: { loading: '正在获取状态', updating: '正在刷新', countdown: (seconds) => `刷新时间 ${seconds} 秒`, manualAria: '立即刷新状态并重置倒计时' },
    summary: { total: '监控节点', totalHint: '全部计算服务器', online: '正常节点', onlineHint: 'FRP 与 Agent 可连接', offline: '异常节点', offlineHint: '连接超时或中断', availability: '平均可用率', availabilityHint: '最近 30 天' },
    node: { averageLatency: '平均响应时间', last24h: '最近 24 小时', availability: '平均运行时间', cloudHistory: '最近30天', historyAria: '最近三十天服务状态', daysAgo: '30 天前', today: '今天', online: '在线', offline: '离线', checked: (time) => `检测于 ${time}`, healthy: '运行正常', unhealthy: '连接异常', failed: '连接失败', empty: '尚未配置计算节点', tooltipAvailability: (value) => `可用率：${value}` },
    resource: { cpu: 'CPU', memory: '内存', gpu: 'GPU', openAria: '打开详细资源监控' },
    ssh: { copyAria: '复制 SSH 连接指令', copied: '已复制', failed: '复制失败' },
    latency: { title: '响应时间趋势', average: '24 小时平均', openAria: '查看响应时间趋势', closeAria: '关闭响应时间趋势', chartAria: '最近二十四小时响应时间折线图', empty: '最近 24 小时暂无响应时间样本' },
    incident: { title: '故障记录', count: (value) => `${value} 条`, empty: '近期无故障记录', ongoing: (duration) => `持续中 · ${duration}` },
    auth: { loginResource: '登录查看资源占用', viewResource: '查看资源占用', secureAccess: '安全访问', title: '登录查看资源占用', close: '关闭登录框', description: '验证通过后可查看已配置计算服务器的详细资源和进程信息。', username: '用户名', password: '密码', remember: '保持登录 30 天', submit: '安全登录', submitting: '正在验证…', security: '会话保存在 HttpOnly Cookie 中，网页脚本无法读取密码或登录令牌。', invalid: '用户名或密码错误。', locked: (seconds) => `尝试次数过多，请在 ${seconds} 秒后重试。`, failed: '登录服务暂时不可用，请稍后重试。' },
    footer: { description: '实验室基础设施状态 · 详细资源信息需要登录', icp: 'ICP备案信息预留（审核中）', waiting: '等待首次检测', checked: (time) => `最后检测：${time}` },
    errors: { load: (message) => `暂时无法读取状态数据：${message}` },
    duration: { seconds: (value) => `${value} 秒`, minutes: (value) => `${value} 分钟`, hours: (value) => `${value} 小时`, days: (value) => `${value} 天` },
  },
  en: {
    pageTitle: 'Lab Service Status', homeAria: 'Return to the lab service status home page', title: 'Lab Service Status', subtitle: 'GPU compute nodes and service availability',
    language: 'Language', themeToggle: 'Toggle light and dark mode', colorTheme: 'Change color theme', themes: { green: 'Emerald', ocean: 'Ocean', violet: 'Violet', amber: 'Amber', anime: 'Starlit Sakura', fighter: 'Fighting Soul' }, logout: 'Sign out', summaryAria: 'Status summary',
    hero: { aria: 'Hoshizakura laboratory theme key visual', title: 'Hoshizakura Compute Observatory', subtitle: 'Watching over every computation in the sakura morning light.', badge: 'Original theme · Live status' },
    fightHero: { aria: 'Fighting Soul street tournament theme key visual', title: 'Fighting Soul Observatory', subtitle: 'Every signal throws a strike. Every computation enters the ring.', badge: 'Original fight theme · Live status' },
    refresh: { loading: 'Loading status', updating: 'Refreshing', countdown: (seconds) => `Refresh in ${seconds}s`, manualAria: 'Refresh status now and reset the countdown' },
    summary: { total: 'Monitored nodes', totalHint: 'All compute servers', online: 'Healthy nodes', onlineHint: 'FRP and Agent are reachable', offline: 'Affected nodes', offlineHint: 'Timed out or disconnected', availability: 'Average uptime', availabilityHint: 'Last 30 days' },
    node: { averageLatency: 'Average response time', last24h: 'Last 24 hours', availability: 'Average uptime', cloudHistory: 'Last 30 days', historyAria: 'Service status for the last thirty days', daysAgo: '30 days ago', today: 'Today', online: 'Online', offline: 'Offline', checked: (time) => `Checked ${time}`, healthy: 'Operating normally', unhealthy: 'Connection unavailable', failed: 'Connection failed', empty: 'No compute node is configured', tooltipAvailability: (value) => `Availability: ${value}` },
    resource: { cpu: 'CPU', memory: 'Memory', gpu: 'GPU', openAria: 'Open detailed resource monitoring' },
    ssh: { copyAria: 'Copy SSH connection command', copied: 'Copied', failed: 'Copy failed' },
    latency: { title: 'Response time trend', average: '24-hour average', openAria: 'View response time trend', closeAria: 'Close response time trend', chartAria: 'Response time chart for the last 24 hours', empty: 'No response time samples in the last 24 hours' },
    incident: { title: 'Incidents', count: (value) => `${value}`, empty: 'No recent incidents', ongoing: (duration) => `Ongoing · ${duration}` },
    auth: { loginResource: 'Sign in for resource usage', viewResource: 'View resource usage', secureAccess: 'SECURE ACCESS', title: 'Sign in for resource usage', close: 'Close sign-in dialog', description: 'After verification, you can view detailed resource and process information for the configured compute servers.', username: 'Username', password: 'Password', remember: 'Keep me signed in for 30 days', submit: 'Secure sign in', submitting: 'Verifying…', security: 'The session is stored in an HttpOnly cookie. Page scripts cannot read your password or session token.', invalid: 'Incorrect username or password.', locked: (seconds) => `Too many attempts. Try again in ${seconds} seconds.`, failed: 'The sign-in service is temporarily unavailable.' },
    footer: { description: 'Lab infrastructure status · detailed resource data requires sign-in', icp: 'ICP filing information reserved (under review)', waiting: 'Waiting for the first check', checked: (time) => `Last check: ${time}` },
    errors: { load: (message) => `Unable to load status data: ${message}` },
    duration: { seconds: (value) => `${value}s`, minutes: (value) => `${value}m`, hours: (value) => `${value}h`, days: (value) => `${value}d` },
  },
  ja: {
    pageTitle: 'ラボサービス状態', homeAria: 'ラボサービス状態のホームへ戻る', title: 'ラボサービス状態', subtitle: 'GPU 計算ノードとサービスの可用性',
    language: '言語', themeToggle: 'ライト・ダークモードを切り替え', colorTheme: '配色テーマを切り替え', themes: { green: 'エメラルド', ocean: 'オーシャン', violet: 'バイオレット', amber: 'アンバー', anime: '星桜', fighter: '闘魂' }, logout: 'ログアウト', summaryAria: '状態の概要',
    hero: { aria: '星桜ラボテーマのキービジュアル', title: '星桜コンピュート観測室', subtitle: '桜色の朝光の中、すべての計算を見守ります。', badge: 'オリジナルテーマ · ライブ状態' },
    fightHero: { aria: '闘魂ストリートファイトテーマのキービジュアル', title: 'ストリート闘魂観測所', subtitle: 'すべての信号が拳を放ち、すべての計算がリングへ。', badge: 'オリジナル格闘テーマ · ライブ状態' },
    refresh: { loading: '状態を取得中', updating: '更新中', countdown: (seconds) => `更新まで ${seconds} 秒`, manualAria: '状態を今すぐ更新してカウントダウンをリセット' },
    summary: { total: '監視ノード', totalHint: 'すべての計算サーバー', online: '正常ノード', onlineHint: 'FRP と Agent に接続可能', offline: '異常ノード', offlineHint: 'タイムアウトまたは切断', availability: '平均稼働率', availabilityHint: '過去 30 日間' },
    node: { averageLatency: '平均応答時間', last24h: '過去 24 時間', availability: '平均稼働時間', cloudHistory: '過去 30 日間', historyAria: '過去三十日間のサービス状態', daysAgo: '30 日前', today: '今日', online: 'オンライン', offline: 'オフライン', checked: (time) => `${time} に確認`, healthy: '正常稼働', unhealthy: '接続異常', failed: '接続失敗', empty: '計算ノードが設定されていません', tooltipAvailability: (value) => `稼働率：${value}` },
    resource: { cpu: 'CPU', memory: 'メモリ', gpu: 'GPU', openAria: '詳細なリソース監視を開く' },
    ssh: { copyAria: 'SSH 接続コマンドをコピー', copied: 'コピー済み', failed: 'コピー失敗' },
    latency: { title: '応答時間の推移', average: '24 時間平均', openAria: '応答時間の推移を表示', closeAria: '応答時間の推移を閉じる', chartAria: '過去 24 時間の応答時間グラフ', empty: '過去 24 時間の応答時間サンプルはありません' },
    incident: { title: '障害履歴', count: (value) => `${value} 件`, empty: '最近の障害はありません', ongoing: (duration) => `継続中 · ${duration}` },
    auth: { loginResource: 'ログインして使用状況を表示', viewResource: 'リソース使用状況を表示', secureAccess: '安全なアクセス', title: 'ログインしてリソースを表示', close: 'ログイン画面を閉じる', description: '認証後、設定済み計算サーバーの詳細なリソースとプロセス情報を確認できます。', username: 'ユーザー名', password: 'パスワード', remember: '30 日間ログイン状態を保持', submit: '安全にログイン', submitting: '確認中…', security: 'セッションは HttpOnly Cookie に保存され、ページのスクリプトはパスワードやトークンを読み取れません。', invalid: 'ユーザー名またはパスワードが正しくありません。', locked: (seconds) => `試行回数が多すぎます。${seconds} 秒後に再試行してください。`, failed: 'ログインサービスは一時的に利用できません。' },
    footer: { description: 'ラボ基盤の状態 · 詳細なリソース情報にはログインが必要です', icp: 'ICP 届出情報の表示欄（審査中）', waiting: '最初の確認を待っています', checked: (time) => `最終確認：${time}` },
    errors: { load: (message) => `状態データを読み込めません：${message}` },
    duration: { seconds: (value) => `${value} 秒`, minutes: (value) => `${value} 分`, hours: (value) => `${value} 時間`, days: (value) => `${value} 日` },
  },
};

const pageState = {
  locale: 'zh',
  resolvedTheme: 'light',
  colorTheme: 'green',
  refreshTimeout: null,
  countdownInterval: null,
  nextRefreshAt: 0,
  loading: false,
  authenticated: false,
  pendingDetailUrl: '',
  latestDocument: null,
  suspended: false,
  resourceRequestVersion: 0,
  latencyDialogNode: null,
  sshCommands: {},
  sshRequestVersion: 0,
};

/**
 * 根据点分隔键读取当前语言文本。
 *
 * @param {string} key - 翻译键。
 * @param {...unknown} args - 函数型翻译文本的参数。
 * @returns {string} 已翻译文本或原始键。
 */
function translate(key, ...args) {
  const locale = TRANSLATIONS[pageState.locale] || TRANSLATIONS.zh;
  const value = key.split('.').reduce((current, part) => current?.[part], locale);
  return typeof value === 'function' ? value(...args) : (value ?? key);
}

/**
 * 翻译一个根节点内带 data-i18n 标记的静态文案。
 *
 * @param {ParentNode} root - 需要更新的文档或卡片根节点。
 * @returns {void}
 */
function translateTree(root) {
  root.querySelectorAll('[data-i18n]').forEach((element) => {
    element.textContent = translate(element.dataset.i18n);
  });
  root.querySelectorAll('[data-i18n-aria]').forEach((element) => {
    element.setAttribute('aria-label', translate(element.dataset.i18nAria));
  });
}

/**
 * 把百分比格式化为适合状态卡片展示的文本。
 *
 * @param {number|null|undefined} value - 原始百分比数值。
 * @returns {string} 带百分号的文本；没有数据时返回破折号。
 */
function formatAvailability(value) {
  return Number.isFinite(value) ? `${Number(value).toFixed(2)}%` : '—';
}

/**
 * 把 Unix 时间戳或 ISO 时间格式化为当前语言的本地时间。
 *
 * @param {number|string|null|undefined} value - 秒级 Unix 时间戳或 ISO 时间。
 * @returns {string} 本地化日期时间文本。
 */
function formatTimestamp(value) {
  if (!value) return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(LOCALES[pageState.locale].htmlLang, { hour12: false });
}

/**
 * 把故障持续秒数转换为便于阅读的本地化时长。
 *
 * @param {number|null|undefined} seconds - 故障持续秒数。
 * @returns {string} 秒、分钟、小时或天形式的时长文本。
 */
function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 60) return translate('duration.seconds', Math.round(value));
  if (value < 3600) return translate('duration.minutes', Math.round(value / 60));
  if (value < 86400) return translate('duration.hours', (value / 3600).toFixed(1));
  return translate('duration.days', (value / 86400).toFixed(1));
}

/**
 * 把探测间隔转换为 PING 状态栏使用的紧凑文本。
 *
 * @param {number|null|undefined} seconds - 探测间隔秒数。
 * @returns {string} 例如 ``1m``、``5m`` 或 ``30s`` 的文本。
 */
function formatProbeInterval(seconds) {
  const value = Math.max(1, Math.round(Number(seconds) || 60));
  if (value >= 3600 && value % 3600 === 0) return `${value / 3600}h`;
  if (value >= 60 && value % 60 === 0) return `${value / 60}m`;
  return `${value}s`;
}

/**
 * 更新页面顶部的节点数量和平均可用率摘要。
 *
 * @param {object} summary - 状态接口返回的摘要对象。
 * @returns {void}
 */
function renderSummary(summary) {
  document.querySelector('#total-count').textContent = summary.total ?? '—';
  document.querySelector('#online-count').textContent = summary.online ?? '—';
  document.querySelector('#offline-count').textContent = summary.offline ?? '—';
  document.querySelector('#availability-value').textContent = formatAvailability(summary.availability_30d);
}

/**
 * 为节点卡片生成最近三十天的彩色状态条。
 *
 * @param {HTMLElement} container - 状态条容器元素。
 * @param {Array<object>} history - 按日期排序的每日状态数据。
 * @returns {void}
 */
function renderHistory(container, history) {
  container.replaceChildren();
  history.forEach((day, index) => {
    const bar = document.createElement('span');
    bar.className = `history-bar is-${day.state || 'unknown'}`;
    if (index < 3) bar.classList.add('tooltip-align-start');
    if (index >= history.length - 3) bar.classList.add('tooltip-align-end');
    const tooltip = `${day.date}\n${translate('node.tooltipAvailability', formatAvailability(day.availability))}`;
    bar.dataset.tooltip = tooltip;
    bar.setAttribute('aria-label', tooltip.replace('\n', ' · '));
    bar.tabIndex = 0;
    container.appendChild(bar);
  });
}

/**
 * 把节点的近期故障记录渲染到向上展开的气泡列表。
 *
 * @param {HTMLElement} container - 故障记录容器。
 * @param {Array<object>} incidents - 最近故障记录列表。
 * @returns {void}
 */
function renderIncidents(container, incidents) {
  container.replaceChildren();
  if (!incidents.length) {
    const empty = document.createElement('div');
    empty.className = 'incident-empty';
    empty.textContent = translate('incident.empty');
    container.appendChild(empty);
    return;
  }
  incidents.forEach((incident) => {
    const row = document.createElement('div');
    row.className = 'incident-item';
    const description = document.createElement('span');
    description.textContent = `${formatTimestamp(incident.started_at)} · ${incident.reason}`;
    const duration = document.createElement('strong');
    duration.textContent = incident.ended_at
      ? formatDuration(incident.duration_seconds)
      : translate('incident.ongoing', formatDuration(incident.duration_seconds));
    row.append(description, duration);
    container.appendChild(row);
  });
}

/**
 * 返回节点在当前语言下的显示名称。
 *
 * @param {object} node - 状态接口返回的节点对象。
 * @returns {string} 本地化节点名称。
 */
function nodeDisplayName(node) {
  const configuredName = typeof node?.name === 'string' ? node.name.trim() : '';
  return configuredName || String(node?.id || 'GPU node');
}

/**
 * 在不支持安全剪贴板 API 的 HTTP 预览地址上回退复制文本。
 *
 * @param {string} text - 需要复制的纯文本。
 * @returns {boolean} 浏览器是否报告复制成功。
 */
function copyTextFallback(text) {
  const input = document.createElement('textarea');
  input.value = text;
  input.readOnly = true;
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand('copy');
  input.remove();
  return copied;
}

/**
 * 复制节点 SSH 指令并在按钮旁显示短暂反馈。
 *
 * @param {HTMLButtonElement} button - 被点击的复制按钮。
 * @param {string} command - 完整 SSH 连接指令。
 * @returns {Promise<void>} 剪贴板操作和反馈更新完成时兑现的 Promise。
 */
async function copySshCommand(button, command) {
  let copied = false;
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(command);
      copied = true;
    } else {
      copied = copyTextFallback(command);
    }
  } catch (_) {
    copied = copyTextFallback(command);
  }
  const feedback = button.parentElement.querySelector('.copy-feedback');
  feedback.textContent = translate(copied ? 'ssh.copied' : 'ssh.failed');
  feedback.hidden = false;
  button.classList.toggle('is-copied', copied);
  window.setTimeout(() => {
    feedback.hidden = true;
    button.classList.remove('is-copied');
  }, 1800);
}

/**
 * 使用模板创建一张完整的节点状态卡片。
 *
 * @param {object} node - 状态接口返回的节点对象。
 * @returns {HTMLElement} 已填充数据的节点卡片元素。
 */
function createNodeCard(node) {
  const template = document.querySelector('#node-card-template');
  const card = template.content.firstElementChild.cloneNode(true);
  card.querySelector('.latency-metric').addEventListener('click', () => {
    if (card.statusNode) openLatencyDialog(card.statusNode);
  });
  card.querySelector('.detail-link').addEventListener('click', handleDetailLinkClick);
  const copyButton = card.querySelector('.copy-ssh-button');
  copyButton.addEventListener('click', () => {
    const currentCommand = pageState.authenticated ? pageState.sshCommands[card.dataset.nodeId] : '';
    if (currentCommand) void copySshCommand(copyButton, currentCommand);
  });
  updateNodeCard(card, node);
  return card;
}

/**
 * 原位更新一张已经存在的节点卡片，避免刷新时销毁资源数据和重播入场动画。
 *
 * @param {HTMLElement} card - 需要更新的现有节点卡片。
 * @param {object} node - 状态接口返回的最新节点对象。
 * @returns {void}
 */
function updateNodeCard(card, node) {
  card.statusNode = node;
  card.dataset.nodeId = String(node.id || '');
  translateTree(card);
  card.classList.toggle('is-online', Boolean(node.online));
  card.classList.toggle('is-offline', !node.online);
  card.querySelector('.node-name').textContent = nodeDisplayName(node);
  card.querySelector('.status-text').textContent = translate(node.online ? 'node.online' : 'node.offline');
  const averageLatencyValue = node.latency_24h?.average_ms;
  const averageLatency = averageLatencyValue === null || averageLatencyValue === undefined
    ? Number.NaN
    : Number(averageLatencyValue);
  card.querySelector('.latency-value').textContent = Number.isFinite(averageLatency)
    ? `${averageLatency.toFixed(1)} ms`
    : '—';
  card.querySelector('.uptime-value').textContent = formatAvailability(node.availability_30d);
  card.querySelector('.history-caption').textContent = translate(node.online ? 'node.healthy' : 'node.unhealthy');
  card.querySelector('.ping-interval').textContent = `PING / ${formatProbeInterval(pageState.latestDocument?.check_interval_seconds)}`;
  card.querySelector('.ping-state').textContent = translate(node.online ? 'node.online' : 'node.offline');
  renderHistory(card.querySelector('.history-bars'), node.history || []);
  renderIncidents(card.querySelector('.incident-list'), node.incidents || []);
  card.querySelector('.incident-count').textContent = translate('incident.count', (node.incidents || []).length);
  const detailLink = card.querySelector('.detail-link');
  detailLink.href = node.detail_url || '/monitor/';
  detailLink.hidden = pageState.authenticated;
  detailLink.querySelector('.detail-link-label').textContent = translate('auth.loginResource');
  const resourcePreview = card.querySelector('.resource-preview');
  resourcePreview.href = node.detail_url || '/monitor/';
  resourcePreview.hidden = !pageState.authenticated;
  const copyButton = card.querySelector('.copy-ssh-button');
  const sshCommand = pageState.authenticated ? pageState.sshCommands[node.id] : '';
  copyButton.hidden = !sshCommand;
  copyButton.title = sshCommand ? `${translate('ssh.copyAria')}：${sshCommand}` : translate('ssh.copyAria');
}

/**
 * 渲染全部节点，并在没有节点时显示明确提示。
 *
 * @param {Array<object>} nodes - 状态接口返回的节点数组。
 * @returns {void}
 */
function renderNodes(nodes) {
  const grid = document.querySelector('#node-grid');
  if (!nodes.length) {
    grid.replaceChildren();
    const empty = document.createElement('div');
    empty.className = 'loading-card';
    empty.textContent = translate('node.empty');
    grid.appendChild(empty);
    return;
  }
  grid.querySelector('.loading-card')?.remove();
  const existingCards = new Map(
    [...grid.querySelectorAll('.node-card[data-node-id]')].map((card) => [card.dataset.nodeId, card]),
  );
  const activeNodeIds = new Set();
  const orderedCards = nodes.map((node) => {
    const nodeId = String(node.id || '');
    activeNodeIds.add(nodeId);
    const card = existingCards.get(nodeId) || createNodeCard(node);
    if (existingCards.has(nodeId)) updateNodeCard(card, node);
    return card;
  });
  existingCards.forEach((card, nodeId) => {
    if (!activeNodeIds.has(nodeId)) card.remove();
  });
  let insertionPoint = grid.firstElementChild;
  orderedCards.forEach((card) => {
    if (card !== insertionPoint) grid.insertBefore(card, insertionPoint);
    insertionPoint = card.nextElementSibling;
  });
  if (pageState.authenticated) void loadResourcePreviews();
}

/**
 * 将一份完整状态文档渲染到页面。
 *
 * @param {object} documentData - 状态探测器生成的公开 JSON 文档。
 * @returns {void}
 */
function renderStatusDocument(documentData) {
  renderSummary(documentData.summary || {});
  renderNodes(Array.isArray(documentData.nodes) ? documentData.nodes : []);
  document.querySelector('#footer-time').textContent = translate('footer.checked', formatTimestamp(documentData.generated_at));
}

/**
 * 显示或隐藏状态接口加载失败提示。
 *
 * @param {string} message - 错误说明；空字符串表示隐藏提示。
 * @returns {void}
 */
function setNotice(message) {
  const notice = document.querySelector('#global-notice');
  notice.hidden = !message;
  notice.textContent = message;
}

/**
 * 安排下一次自动刷新，并重置倒计时基准。
 *
 * @returns {void}
 */
function scheduleNextRefresh() {
  if (pageState.refreshTimeout !== null) window.clearTimeout(pageState.refreshTimeout);
  pageState.refreshTimeout = null;
  pageState.nextRefreshAt = Date.now() + AUTO_REFRESH_SECONDS * 1000;
  if (!pageState.suspended) armRefreshTimeout(AUTO_REFRESH_SECONDS * 1000);
  updateRefreshLabel();
}

/**
 * 按给定延迟创建唯一的自动刷新定时器。
 *
 * @param {number} delayMs - 距离下次刷新的毫秒数。
 * @returns {void}
 */
function armRefreshTimeout(delayMs) {
  if (pageState.refreshTimeout !== null) window.clearTimeout(pageState.refreshTimeout);
  pageState.refreshTimeout = window.setTimeout(() => {
    pageState.refreshTimeout = null;
    void loadStatus();
  }, Math.max(0, delayMs));
}

/**
 * 确保页面只运行一个倒计时显示定时器。
 *
 * @returns {void}
 */
function startCountdownInterval() {
  if (pageState.countdownInterval !== null) window.clearInterval(pageState.countdownInterval);
  pageState.countdownInterval = window.setInterval(updateRefreshLabel, 1000);
}

/**
 * 页面进入后台或离开时暂停定时器，同时保留原刷新截止时间。
 *
 * @returns {void}
 */
function suspendAutoRefresh() {
  pageState.suspended = true;
  if (pageState.refreshTimeout !== null) window.clearTimeout(pageState.refreshTimeout);
  if (pageState.countdownInterval !== null) window.clearInterval(pageState.countdownInterval);
  pageState.refreshTimeout = null;
  pageState.countdownInterval = null;
}

/**
 * 浏览器后退恢复页面时重新启动定时器，过期则立即刷新。
 *
 * @returns {void}
 */
function resumeAutoRefresh() {
  pageState.suspended = false;
  startCountdownInterval();
  if (pageState.loading) return;
  const remainingMs = pageState.nextRefreshAt - Date.now();
  if (!pageState.nextRefreshAt || remainingMs <= 0) {
    void loadStatus();
    return;
  }
  armRefreshTimeout(remainingMs);
  updateRefreshLabel();
}

/**
 * 从云端状态 JSON 获取数据并重新安排下一次刷新。
 *
 * @returns {Promise<void>} 页面刷新和下一轮调度完成时兑现的 Promise。
 */
async function loadStatus() {
  if (pageState.loading) return;
  pageState.loading = true;
  const refreshButton = document.querySelector('#refresh-button');
  refreshButton.disabled = true;
  refreshButton.classList.add('is-loading');
  refreshButton.setAttribute('aria-busy', 'true');
  document.querySelector('#refresh-label').textContent = translate('refresh.updating');
  try {
    const response = await fetch(`${STATUS_ENDPOINT}?t=${Date.now()}`, { cache: 'no-store', credentials: 'same-origin' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    pageState.latestDocument = await response.json();
    renderStatusDocument(pageState.latestDocument);
    setNotice('');
  } catch (error) {
    setNotice(translate('errors.load', error.message));
  } finally {
    pageState.loading = false;
    refreshButton.disabled = false;
    refreshButton.classList.remove('is-loading');
    refreshButton.removeAttribute('aria-busy');
    scheduleNextRefresh();
  }
}

/**
 * 响应刷新按钮点击，立即拉取数据并从完整周期重新倒计时。
 *
 * @returns {void}
 */
function refreshNow() {
  if (pageState.loading) return;
  if (pageState.refreshTimeout !== null) window.clearTimeout(pageState.refreshTimeout);
  pageState.refreshTimeout = null;
  pageState.nextRefreshAt = 0;
  void loadStatus();
}

/**
 * 更新倒计时，并在浏览器定时器被节流后执行兜底刷新。
 *
 * @returns {void}
 */
function updateRefreshLabel() {
  if (pageState.loading) {
    document.querySelector('#refresh-label').textContent = translate('refresh.updating');
    return;
  }
  const remaining = Math.max(0, Math.ceil((pageState.nextRefreshAt - Date.now()) / 1000));
  document.querySelector('#refresh-label').textContent = translate('refresh.countdown', remaining);
  if (remaining === 0) {
    if (pageState.refreshTimeout !== null) window.clearTimeout(pageState.refreshTimeout);
    pageState.refreshTimeout = null;
    void loadStatus();
  }
}

/**
 * 应用并持久化与 GPU 页面共享的明暗主题偏好。
 *
 * @param {'auto'|'light'|'dark'} theme - 需要启用的主题偏好。
 * @param {boolean} persist - 是否写入共享的 localStorage。
 * @returns {void}
 */
function applyTheme(theme, persist = true) {
  const normalized = ['auto', 'light', 'dark'].includes(theme) ? theme : 'auto';
  pageState.resolvedTheme = normalized === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : normalized;
  document.documentElement.dataset.theme = pageState.resolvedTheme;
  document.querySelector('meta[name="theme-color"]').content = pageState.resolvedTheme === 'dark' ? '#0e1522' : '#f4f7fb';
  if (persist) localStorage.setItem(THEME_STORAGE_KEY, normalized);
}

/**
 * 应用可跨首页和 GPU 页面共享的强调配色。
 *
 * @param {'green'|'ocean'|'violet'|'amber'|'anime'|'fighter'} theme - 需要启用的配色主题。
 * @param {boolean} persist - 是否写入共享的 localStorage。
 * @returns {void}
 */
function applyColorTheme(theme, persist = true) {
  const normalized = ['green', 'ocean', 'violet', 'amber', 'anime', 'fighter'].includes(theme) ? theme : 'green';
  pageState.colorTheme = normalized;
  document.documentElement.dataset.colorTheme = normalized;
  document.querySelectorAll('[data-color-theme]').forEach((button) => {
    const active = button.dataset.colorTheme === normalized;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-checked', String(active));
  });
  if (persist) localStorage.setItem(COLOR_THEME_STORAGE_KEY, normalized);
}

/**
 * 在浅色与深色主题之间切换。
 *
 * @returns {void}
 */
function toggleTheme() {
  applyTheme(pageState.resolvedTheme === 'dark' ? 'light' : 'dark');
}

/**
 * 应用语言并与 GPU 页面共享相同的语言偏好键。
 *
 * @param {string} locale - zh、en 或 ja。
 * @param {boolean} persist - 是否写入共享的 localStorage。
 * @returns {void}
 */
function applyLocale(locale, persist = true) {
  pageState.locale = LOCALES[locale] ? locale : 'zh';
  document.documentElement.lang = LOCALES[pageState.locale].htmlLang;
  document.title = translate('pageTitle');
  document.querySelectorAll('[data-locale]').forEach((button) => {
    const active = button.dataset.locale === pageState.locale;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  translateTree(document);
  if (pageState.latestDocument) renderStatusDocument(pageState.latestDocument);
  if (pageState.latencyDialogNode && document.querySelector('#latency-dialog').open) {
    renderLatencyChart(pageState.latencyDialogNode);
  }
  updateRefreshLabel();
  if (persist) localStorage.setItem(LOCALE_STORAGE_KEY, pageState.locale);
}

/**
 * 校验并规范化登录后的同源监控跳转地址。
 *
 * @param {string} value - 查询参数或节点卡片提供的地址。
 * @returns {string} 合法的 /monitor/ 地址；不合法时返回空字符串。
 */
function safeMonitorPath(value) {
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin && url.pathname.startsWith('/monitor/')
      ? `${url.pathname}${url.search}${url.hash}`
      : '';
  } catch (_) {
    return '';
  }
}

/**
 * 创建带属性的 SVG 元素，供无外部图表依赖的趋势图使用。
 *
 * @param {string} tagName - SVG 标签名。
 * @param {Record<string, string|number>} attributes - 需要设置的属性。
 * @returns {SVGElement} 新创建的 SVG 元素。
 */
function createSvgElement(tagName, attributes = {}) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tagName);
  Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, String(value)));
  return element;
}

/**
 * 将时间戳格式化为趋势图横轴使用的时分文本。
 *
 * @param {number} timestamp - 秒级 Unix 时间戳。
 * @returns {string} 当前语言下的 24 小时时分文本。
 */
function formatChartTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString(
    LOCALES[pageState.locale].htmlLang,
    { hour: '2-digit', minute: '2-digit', hour12: false },
  );
}

/**
 * 根据节点历史数据绘制最近二十四小时响应时间折线图。
 *
 * @param {object} node - 状态文档中的节点对象。
 * @returns {void}
 */
function renderLatencyChart(node) {
  const chart = document.querySelector('#latency-chart');
  const empty = document.querySelector('#latency-chart-empty');
  const profile = node?.latency_24h || {};
  const averageValue = profile.average_ms;
  const average = Number.isFinite(Number(averageValue)) && averageValue !== null
    ? Number(averageValue)
    : null;
  document.querySelector('#latency-dialog-average').textContent = average === null
    ? '—'
    : `${average.toFixed(1)} ms`;
  document.querySelector('#latency-dialog-node').textContent = `${nodeDisplayName(node)} · ${translate('node.last24h')}`;
  chart.replaceChildren();

  const points = (Array.isArray(profile.points) ? profile.points : [])
    .map((point) => ({ timestamp: Number(point.timestamp), latency: Number(point.latency_ms) }))
    .filter((point) => Number.isFinite(point.timestamp) && Number.isFinite(point.latency) && point.latency >= 0)
    .sort((first, second) => first.timestamp - second.timestamp);
  empty.hidden = points.length > 0;
  chart.hidden = points.length === 0;
  if (!points.length) return;

  const width = 820;
  const height = 340;
  const padding = { top: 22, right: 24, bottom: 46, left: 62 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const endTimestamp = Number(pageState.latestDocument?.generated_at_unix) || Math.floor(Date.now() / 1000);
  const startTimestamp = endTimestamp - 86400;
  const maximumLatency = Math.max(10, ...points.map((point) => point.latency));
  const yMaximum = Math.ceil(maximumLatency / 10) * 10;
  const xFor = (timestamp) => padding.left + Math.min(Math.max((timestamp - startTimestamp) / 86400, 0), 1) * plotWidth;
  const yFor = (latency) => padding.top + (1 - Math.min(latency / yMaximum, 1)) * plotHeight;

  for (let index = 0; index <= 5; index += 1) {
    const ratio = index / 5;
    const y = padding.top + ratio * plotHeight;
    const value = Math.round(yMaximum * (1 - ratio));
    chart.appendChild(createSvgElement('line', { x1: padding.left, y1: y, x2: width - padding.right, y2: y, class: 'chart-grid-line' }));
    const label = createSvgElement('text', { x: padding.left - 12, y: y + 4, class: 'chart-axis-label', 'text-anchor': 'end' });
    label.textContent = `${value} ms`;
    chart.appendChild(label);
  }

  for (let index = 0; index <= 6; index += 1) {
    const ratio = index / 6;
    const timestamp = startTimestamp + ratio * 86400;
    const label = createSvgElement('text', {
      x: padding.left + ratio * plotWidth,
      y: height - 16,
      class: 'chart-axis-label',
      'text-anchor': index === 0 ? 'start' : (index === 6 ? 'end' : 'middle'),
    });
    label.textContent = formatChartTime(timestamp);
    chart.appendChild(label);
  }

  const bucketSeconds = Math.max(Number(profile.bucket_seconds) || 900, 60);
  let pathData = '';
  let previousTimestamp = null;
  points.forEach((point) => {
    const command = previousTimestamp !== null && point.timestamp - previousTimestamp <= bucketSeconds * 2.5 ? 'L' : 'M';
    pathData += `${command}${xFor(point.timestamp).toFixed(1)},${yFor(point.latency).toFixed(1)} `;
    previousTimestamp = point.timestamp;
  });
  chart.appendChild(createSvgElement('path', { d: pathData.trim(), class: 'latency-chart-line' }));

  points.forEach((point) => {
    const marker = createSvgElement('circle', {
      cx: xFor(point.timestamp),
      cy: yFor(point.latency),
      r: 2.5,
      class: 'latency-chart-point',
    });
    const title = createSvgElement('title');
    title.textContent = `${formatTimestamp(point.timestamp)} · ${point.latency.toFixed(1)} ms`;
    marker.appendChild(title);
    chart.appendChild(marker);
  });
}

/**
 * 打开指定节点的响应时间趋势弹窗。
 *
 * @param {object} node - 用户点击的节点对象。
 * @returns {void}
 */
function openLatencyDialog(node) {
  pageState.latencyDialogNode = node;
  renderLatencyChart(node);
  const dialog = document.querySelector('#latency-dialog');
  if (!dialog.open) dialog.showModal();
}

/**
 * 把资源占用数值限制到 0 到 100，并生成百分比文本。
 *
 * @param {unknown} value - Agent 返回的资源占用值。
 * @returns {{numberValue: number|null, label: string}} 规范化数值及展示文本。
 */
function normalizeUsage(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return { numberValue: null, label: '—' };
  const bounded = Math.min(Math.max(numeric, 0), 100);
  return { numberValue: bounded, label: `${bounded.toFixed(1)}%` };
}

/**
 * 更新迷你资源面板中的一个指标和进度条。
 *
 * @param {HTMLElement} card - 节点卡片。
 * @param {'cpu'|'memory'|'gpu'} metric - 指标标识。
 * @param {unknown} value - Agent 返回的占用百分比。
 * @returns {void}
 */
function renderResourceMetric(card, metric, value) {
  const usage = normalizeUsage(value);
  card.querySelector(`.resource-${metric}`).textContent = usage.label;
  card.querySelector(`.resource-${metric}-meter`).style.width = `${usage.numberValue ?? 0}%`;
}

/**
 * 把 Agent 状态响应压缩展示为 CPU、内存和平均 GPU 占用。
 *
 * @param {HTMLElement} card - 节点卡片。
 * @param {object|null} status - Agent 状态响应；为空表示暂不可用。
 * @returns {void}
 */
function renderResourcePreview(card, status) {
  const preview = card.querySelector('.resource-preview');
  if (!status) {
    if (!preview.classList.contains('has-resource-data')) {
      renderResourceMetric(card, 'cpu', null);
      renderResourceMetric(card, 'memory', null);
      renderResourceMetric(card, 'gpu', null);
    }
    preview.classList.add('is-unavailable');
    return;
  }
  const payload = status?.data ?? status;
  renderResourceMetric(card, 'cpu', payload?.system?.cpu?.percent);
  renderResourceMetric(card, 'memory', payload?.system?.memory?.percent);
  renderResourceMetric(card, 'gpu', payload?.gpu?.summary?.avg_gpu_utilization);
  preview.classList.add('has-resource-data');
  preview.classList.remove('is-unavailable');
}

/**
 * 获取一台节点的受保护资源摘要，并忽略已经过期的并发请求。
 *
 * @param {HTMLElement} card - 需要更新的节点卡片。
 * @param {number} requestVersion - 本轮请求版本号。
 * @returns {Promise<void>} 单卡资源摘要请求完成时兑现的 Promise。
 */
async function loadResourcePreview(card, requestVersion) {
  const nodeId = card.dataset.nodeId || '';
  if (!/^[a-zA-Z0-9_-]+$/.test(nodeId)) {
    renderResourcePreview(card, null);
    return;
  }
  try {
    const response = await fetch(`/monitor/api/nodes/${encodeURIComponent(nodeId)}/status?t=${Date.now()}`, {
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (response.status === 401) {
      if (requestVersion === pageState.resourceRequestVersion) applyAuthenticationState(false);
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    if (pageState.authenticated && requestVersion === pageState.resourceRequestVersion) renderResourcePreview(card, status);
  } catch (_) {
    if (pageState.authenticated && requestVersion === pageState.resourceRequestVersion) renderResourcePreview(card, null);
  }
}

/**
 * 并发刷新当前页面全部节点的迷你资源占用面板。
 *
 * @returns {Promise<void>} 所有节点请求结束时兑现的 Promise。
 */
async function loadResourcePreviews() {
  if (!pageState.authenticated) return;
  const requestVersion = ++pageState.resourceRequestVersion;
  const cards = [...document.querySelectorAll('.node-card[data-node-id]')];
  await Promise.allSettled(cards.map((card) => loadResourcePreview(card, requestVersion)));
}

/**
 * 根据当前登录态和受保护配置更新全部 SSH 复制按钮。
 *
 * @returns {void}
 */
function updateSshButtons() {
  document.querySelectorAll('.node-card[data-node-id]').forEach((card) => {
    const command = pageState.authenticated ? pageState.sshCommands[card.dataset.nodeId] : '';
    const button = card.querySelector('.copy-ssh-button');
    button.hidden = !command;
    button.title = command ? `${translate('ssh.copyAria')}：${command}` : translate('ssh.copyAria');
  });
}

/**
 * 登录后读取受保护的 SSH 指令配置，未授权时立即清空。
 *
 * @returns {Promise<void>} 配置请求与按钮更新结束时兑现的 Promise。
 */
async function loadSshCommands() {
  if (!pageState.authenticated) return;
  const requestVersion = ++pageState.sshRequestVersion;
  try {
    const response = await fetch(SSH_CONFIG_ENDPOINT, { cache: 'no-store', credentials: 'same-origin' });
    if (response.status === 401) {
      if (requestVersion === pageState.sshRequestVersion) applyAuthenticationState(false);
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const documentData = await response.json();
    const commands = documentData?.commands && typeof documentData.commands === 'object'
      ? documentData.commands
      : {};
    const normalizedCommands = {};
    Object.entries(commands).forEach(([nodeId, command]) => {
      if (/^[a-zA-Z0-9_-]+$/.test(nodeId) && typeof command === 'string' && command.length <= 512) {
        normalizedCommands[nodeId] = command.trim();
      }
    });
    if (pageState.authenticated && requestVersion === pageState.sshRequestVersion) {
      pageState.sshCommands = normalizedCommands;
      updateSshButtons();
    }
  } catch (_) {
    if (requestVersion === pageState.sshRequestVersion) {
      pageState.sshCommands = {};
      updateSshButtons();
    }
  }
}

/**
 * 更新登录状态相关按钮和卡片文案。
 *
 * @param {boolean} authenticated - 当前浏览器是否拥有有效会话。
 * @returns {void}
 */
function applyAuthenticationState(authenticated) {
  pageState.authenticated = authenticated;
  pageState.resourceRequestVersion += 1;
  pageState.sshRequestVersion += 1;
  if (!authenticated) pageState.sshCommands = {};
  document.querySelector('#logout-button').hidden = !authenticated;
  document.querySelectorAll('.node-card').forEach((card) => {
    card.querySelector('.detail-link').hidden = authenticated;
    card.querySelector('.resource-preview').hidden = !authenticated;
  });
  updateSshButtons();
  if (authenticated) {
    void loadResourcePreviews();
    void loadSshCommands();
  }
}

/**
 * 查询 HttpOnly Cookie 对应的服务端登录状态。
 *
 * @returns {Promise<void>} 登录状态更新完成时兑现的 Promise。
 */
async function loadSession() {
  try {
    const response = await fetch(SESSION_ENDPOINT, { cache: 'no-store', credentials: 'same-origin' });
    const documentData = response.ok ? await response.json() : { authenticated: false };
    applyAuthenticationState(documentData.authenticated === true);
  } catch (_) {
    applyAuthenticationState(false);
  }
}

/**
 * 打开自定义登录框并把焦点放到用户名输入框。
 *
 * @returns {void}
 */
function openLoginDialog() {
  const dialog = document.querySelector('#login-dialog');
  document.querySelector('#login-message').hidden = true;
  if (!dialog.open) dialog.showModal();
  window.setTimeout(() => document.querySelector('#login-username').focus(), 0);
}

/**
 * 在节点资源链接点击时决定直接跳转还是要求登录。
 *
 * @param {MouseEvent} event - 资源按钮点击事件。
 * @returns {void}
 */
function handleDetailLinkClick(event) {
  if (pageState.authenticated) return;
  event.preventDefault();
  pageState.pendingDetailUrl = safeMonitorPath(event.currentTarget.href) || '/monitor/';
  openLoginDialog();
}

/**
 * 提交账号密码并接收服务端签发的 HttpOnly 会话。
 *
 * @param {SubmitEvent} event - 登录表单提交事件。
 * @returns {Promise<void>} 登录请求完成时兑现的 Promise。
 */
async function submitLogin(event) {
  event.preventDefault();
  const submitButton = document.querySelector('#login-submit');
  const message = document.querySelector('#login-message');
  const username = document.querySelector('#login-username').value;
  const passwordInput = document.querySelector('#login-password');
  submitButton.disabled = true;
  submitButton.textContent = translate('auth.submitting');
  message.hidden = true;
  try {
    const response = await fetch(LOGIN_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: passwordInput.value, remember: document.querySelector('#login-remember').checked }),
    });
    const documentData = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 429) throw new Error(translate('auth.locked', documentData.retry_after || response.headers.get('Retry-After') || 60));
      if (response.status === 401) throw new Error(translate('auth.invalid'));
      throw new Error(translate('auth.failed'));
    }
    applyAuthenticationState(true);
    document.querySelector('#login-dialog').close();
    const destination = safeMonitorPath(pageState.pendingDetailUrl) || '/monitor/';
    window.location.assign(destination);
  } catch (error) {
    message.textContent = error.message || translate('auth.failed');
    message.hidden = false;
  } finally {
    passwordInput.value = '';
    submitButton.disabled = false;
    submitButton.textContent = translate('auth.submit');
  }
}

/**
 * 主动清除服务端会话并恢复未登录页面状态。
 *
 * @returns {Promise<void>} 退出请求完成时兑现的 Promise。
 */
async function logout() {
  try {
    await fetch(LOGOUT_ENDPOINT, { method: 'POST', credentials: 'same-origin' });
  } finally {
    applyAuthenticationState(false);
  }
}

/**
 * 初始化主题、语言、事件、会话检查和可靠自动刷新。
 *
 * @returns {Promise<void>} 页面首次状态和会话检查完成时兑现的 Promise。
 */
async function initializePage() {
  const browserLocale = (navigator.language || '').toLowerCase();
  const defaultLocale = browserLocale.startsWith('en') ? 'en' : (browserLocale.startsWith('ja') ? 'ja' : 'zh');
  applyTheme(localStorage.getItem(THEME_STORAGE_KEY) || 'auto', false);
  applyColorTheme(localStorage.getItem(COLOR_THEME_STORAGE_KEY) || 'green', false);
  applyLocale(localStorage.getItem(LOCALE_STORAGE_KEY) || defaultLocale, false);
  document.querySelectorAll('[data-locale]').forEach((button) => button.addEventListener('click', () => applyLocale(button.dataset.locale)));
  document.querySelectorAll('[data-color-theme]').forEach((button) => button.addEventListener('click', () => {
    applyColorTheme(button.dataset.colorTheme);
    document.querySelector('#color-theme-picker').open = false;
  }));
  document.querySelector('#refresh-button').addEventListener('click', refreshNow);
  document.querySelector('#theme-button').addEventListener('click', toggleTheme);
  document.querySelector('#logout-button').addEventListener('click', () => void logout());
  document.querySelector('#login-close').addEventListener('click', () => document.querySelector('#login-dialog').close());
  document.querySelector('#login-form').addEventListener('submit', (event) => void submitLogin(event));
  document.querySelector('#login-dialog').addEventListener('click', (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
  const latencyDialog = document.querySelector('#latency-dialog');
  document.querySelector('#latency-dialog-close').addEventListener('click', () => latencyDialog.close());
  latencyDialog.addEventListener('click', (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
  latencyDialog.addEventListener('close', () => {
    pageState.latencyDialogNode = null;
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if ((localStorage.getItem(THEME_STORAGE_KEY) || 'auto') === 'auto') applyTheme('auto', false);
  });
  window.addEventListener('storage', (event) => {
    if (event.key === LOCALE_STORAGE_KEY && event.newValue) applyLocale(event.newValue, false);
    if (event.key === THEME_STORAGE_KEY && event.newValue) applyTheme(event.newValue, false);
    if (event.key === COLOR_THEME_STORAGE_KEY && event.newValue) applyColorTheme(event.newValue, false);
  });
  document.addEventListener('click', (event) => {
    const picker = document.querySelector('#color-theme-picker');
    if (!picker.contains(event.target)) picker.open = false;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') document.querySelector('#color-theme-picker').open = false;
  });
  startCountdownInterval();
  await Promise.all([loadStatus(), loadSession()]);
  const parameters = new URLSearchParams(window.location.search);
  const requestedNext = safeMonitorPath(parameters.get('next') || '');
  if (parameters.get('login') === '1') {
    pageState.pendingDetailUrl = requestedNext || '/monitor/';
    if (pageState.authenticated) window.location.replace(pageState.pendingDetailUrl);
    else openLoginDialog();
  }
  window.addEventListener('pageshow', () => {
    applyTheme(localStorage.getItem(THEME_STORAGE_KEY) || 'auto', false);
    applyColorTheme(localStorage.getItem(COLOR_THEME_STORAGE_KEY) || 'green', false);
    applyLocale(localStorage.getItem(LOCALE_STORAGE_KEY) || defaultLocale, false);
    resumeAutoRefresh();
    void loadSession();
  });
  window.addEventListener('pagehide', suspendAutoRefresh);
}

void initializePage();
