'use strict';

const $ = id => document.getElementById(id);
const BRIDGE_UNAVAILABLE_MESSAGE = '请在 AstrBot 插件管理页中打开本页面。普通浏览器预览只能查看 UI，不能上传、保存或试听。';
let bridge = null;

function fallbackBridge() {
  return {
    ready: async () => ({}),
    apiGet: async () => ({ success: false, error: BRIDGE_UNAVAILABLE_MESSAGE }),
    apiPost: async () => ({ success: false, error: BRIDGE_UNAVAILABLE_MESSAGE }),
    upload: async () => ({ success: false, error: BRIDGE_UNAVAILABLE_MESSAGE }),
  };
}

function getAstrBotBridge() {
  return window.AstrBotPluginPage || null;
}

function isUsableBridge(candidate) {
  return Boolean(
    candidate &&
    typeof candidate.apiGet === 'function' &&
    typeof candidate.apiPost === 'function' &&
    typeof candidate.upload === 'function'
  );
}

async function waitForAstrBotBridge(timeoutMs = 2000, intervalMs = 50) {
  const startedAt = Date.now();
  return new Promise(resolve => {
    const tick = () => {
      const candidate = getAstrBotBridge();
      if (isUsableBridge(candidate)) {
        resolve(candidate);
        return;
      }
      if (Date.now() - startedAt >= timeoutMs) {
        resolve(null);
        return;
      }
      setTimeout(tick, intervalMs);
    };
    tick();
  });
}

async function resolveBridge() {
  return await waitForAstrBotBridge() || fallbackBridge();
}

let state = {
  config: {},
  voices: [],
  defaults: {},
  emotions: ['happy', 'sad', 'angry', 'neutral'],
  providers: [],
  readiness: {},
  accessControl: {},
};
let lastUploadedVoiceId = '';

function toast(message, type = 'ok') {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast ${type}`;
  el.style.display = 'block';
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.style.display = 'none'; }, 3000);
}

function extractErrorMessage(error, fallback = '操作失败') {
  const data = error && error.response && error.response.data;
  if (data && data.error) return data.error;
  if (data && data.message) return data.message;
  if (error && error.data && error.data.error) return error.data.error;
  return String((error && error.message) || error || fallback);
}

function setActionState(message, type = 'idle') {
  const el = $('save-state');
  el.textContent = message;
  el.className = `action-state ${type}`;
}

function markDirty() {
  setActionState('有未保存更改', 'is-dirty');
}

function markClean() {
  setActionState('配置已同步', 'is-clean');
}

function setUploadHint(message, type = 'info') {
  const el = $('upload-hint');
  el.textContent = message;
  el.className = `field-hint ${type}`;
}

function setPreviewHint(message, type = 'info') {
  const el = $('preview-hint');
  el.textContent = message;
  el.className = `field-hint ${type}`;
}

function setBusy(button, busy, busyText = '处理中...') {
  if (!button) return;
  if (busy) {
    button.dataset.idleText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
    button.classList.add('is-busy');
    button.setAttribute('aria-busy', 'true');
    return;
  }
  button.textContent = button.dataset.idleText || button.textContent;
  button.disabled = false;
  button.classList.remove('is-busy');
  button.removeAttribute('aria-busy');
}

async function runAction(button, busyText, handler) {
  setBusy(button, true, busyText);
  try {
    await handler();
  } catch (error) {
    toast(extractErrorMessage(error), 'err');
  } finally {
    setBusy(button, false);
    updateActionAvailability();
  }
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function configPayload() {
  return {
    api_key: $('api-key').value.trim(),
    base_url: $('base-url').value.trim(),
    model: $('model').value.trim(),
    default_context: $('default-context').value,
    max_text_chars: Number($('max-text-chars').value || 500),
    max_concurrency: Number($('max-concurrency').value || 1),
    max_voice_file_mb: Number($('max-voice-file-mb').value || 10),
    reply_mode: $('reply-mode').value,
    auto_tts_enabled: $('auto-tts-enabled').checked,
    auto_tts_probability: Number($('auto-tts-probability').value || 0),
    auto_tts_group_whitelist: $('auto-tts-group-whitelist').value,
    auto_tts_group_blacklist: $('auto-tts-group-blacklist').value,
    auto_tts_private_whitelist: $('auto-tts-private-whitelist').value,
    auto_tts_private_blacklist: $('auto-tts-private-blacklist').value,
    admin_users: $('admin-users').value,
    file_fallback_enabled: $('file-fallback-enabled').checked,
    output_retention_days: Number($('output-retention-days').value || 0),
    output_max_files: Number($('output-max-files').value || 0),
    emotion_routing_enabled: $('emotion-routing-enabled').checked,
    ai_style_director_enabled: $('ai-style-director-enabled').checked,
    ai_style_director_provider_id: $('ai-style-director-provider-id').value.trim(),
    ai_style_director_prompt: $('ai-style-director-prompt').value,
    ai_style_director_mode: $('ai-style-director-mode').value,
    ai_style_director_max_chars: Number($('ai-style-director-max-chars').value || 120),
    ai_style_director_optimize_text: $('ai-style-director-optimize-text').checked,
    ai_style_director_fallback_to_emotion: $('ai-style-director-fallback').checked,
    ai_style_director_debug_log: $('ai-style-director-debug-log').checked,
    segment_enabled: $('segment-enabled').checked,
    segment_threshold_chars: Number($('segment-threshold-chars').value || 180),
    segment_max_segments: Number($('segment-max-segments').value || 6),
  };
}

function fillEmotionSelect(select, includeAuto = false) {
  select.innerHTML = '';
  if (includeAuto) {
    const auto = document.createElement('option');
    auto.value = '';
    auto.textContent = '自动';
    select.appendChild(auto);
  }
  state.emotions.forEach(emotion => {
    const option = document.createElement('option');
    option.value = emotion;
    option.textContent = emotion;
    select.appendChild(option);
  });
}

function renderProviderSelect() {
  const select = $('ai-style-director-provider-select');
  const hint = $('ai-provider-hint');
  const current = $('ai-style-director-provider-id').value.trim();
  select.innerHTML = '<option value="">当前默认 LLM</option>';
  state.providers.forEach(provider => {
    const option = document.createElement('option');
    option.value = provider.id;
    option.textContent = provider.label || provider.name || provider.id;
    select.appendChild(option);
  });
  if (current && !state.providers.some(provider => provider.id === current)) {
    const custom = document.createElement('option');
    custom.value = current;
    custom.textContent = `手填：${current}`;
    select.appendChild(custom);
  }
  select.value = current;
  if (state.providers.length) {
    hint.textContent = `已读取到 ${state.providers.length} 个 AstrBot AI 服务商；留空则使用当前默认 LLM。`;
    hint.className = 'field-hint ok';
  } else {
    hint.textContent = '未读取到 AstrBot AI 服务商。可先确认 AstrBot 已启用聊天模型，或在右侧手填 provider id。';
    hint.className = 'field-hint warn';
  }
}

function updateStatus() {
  $('model-status').textContent = state.config.model || 'voiceclone';
  $('emotion-status').textContent = state.config.emotion_routing_enabled === false ? 'OFF' : 'ON';
  $('segment-status').textContent = state.config.segment_enabled === false ? 'OFF' : 'ON';
  $('hero-voice-count').textContent = String(state.voices.length);
}

function renderReadiness() {
  const readiness = state.readiness || {};
  const enabledVoices = state.voices.filter(voice => voice.enabled !== false).length;
  const hasPreviewText = Boolean($('preview-text').value.trim());
  const hasPreviewVoice = Boolean($('preview-voice').value);
  const previewReady = Boolean(enabledVoices && hasPreviewText && hasPreviewVoice);
  const items = [
    {
      title: 'API Key',
      ok: readiness.api_key,
      detail: readiness.api_key ? '已保存，可请求 MiMo。' : '未保存，试听和自动语音会失败。',
    },
    {
      title: '音色库',
      ok: readiness.voices,
      detail: enabledVoices ? `${enabledVoices} 个可用音色。` : '先上传并启用至少一个授权音色。',
    },
    {
      title: 'AI 导演',
      ok: !state.config.ai_style_director_enabled || readiness.ai_director,
      warn: state.config.ai_style_director_enabled && !readiness.ai_director,
      detail: state.config.ai_style_director_enabled
        ? (readiness.ai_director ? '已启用，会生成隐藏风格和朗读文本。' : '已启用，但未识别到可用 AI 服务商。')
        : '未启用，使用情绪/音色风格。',
    },
    {
      title: '试听',
      ok: previewReady,
      warn: !previewReady,
      detail: previewReady ? '已就绪，可以生成预览音频。' : previewDisabledReason(),
    },
  ];

  $('readiness-list').innerHTML = items.map(item => `
    <article class="readiness-item ${item.ok ? 'is-ok' : item.warn ? 'is-warn' : ''}">
      <strong>${escapeHtml(item.title)}</strong>
      <span>${escapeHtml(item.detail)}</span>
    </article>
  `).join('');
}

function renderAccessControl() {
  const access = state.accessControl || {};
  const items = [
    {
      title: '管理员',
      detail: (access.admins && access.admins.detail) || '未配置管理员',
      count: access.admins ? access.admins.count : 0,
    },
    {
      title: '群聊',
      detail: (access.group && access.group.detail) || '未设置群聊名单，默认放行',
      count: ((access.group && access.group.whitelist_count) || 0) + ((access.group && access.group.blacklist_count) || 0),
    },
    {
      title: '私聊',
      detail: (access.private && access.private.detail) || '未设置私聊名单，默认放行',
      count: ((access.private && access.private.whitelist_count) || 0) + ((access.private && access.private.blacklist_count) || 0),
    },
  ];

  $('access-summary').innerHTML = `
    <div class="access-summary-core">
      <strong>当前规则预览</strong>
      <span>${escapeHtml(access.summary || '管理员优先放行；黑名单优先于白名单；白名单留空表示不限制。')}</span>
    </div>
    <div class="access-summary-list">
      ${items.map(item => `
        <article class="${item.count ? 'is-active' : ''}">
          <b>${escapeHtml(item.title)}</b>
          <span>${escapeHtml(item.detail)}</span>
        </article>
      `).join('')}
    </div>
  `;
}

function previewDisabledReason() {
  if (!state.voices.some(voice => voice.enabled !== false)) return '需要先上传并启用音色。';
  if (!$('preview-voice').value) return '请选择一个试听音色。';
  if (!$('preview-text').value.trim()) return '请输入试听文本。';
  return '准备中，请稍候。';
}

function updateActionAvailability() {
  const uploadFile = $('voice-file').files[0];
  const hasName = Boolean($('voice-name').value.trim());
  const hasConsent = $('voice-consent').checked;
  $('upload-voice').disabled = !(uploadFile && hasName && hasConsent);

  if (!uploadFile) {
    setUploadHint('先选择一段已授权的 mp3 / wav 参考音频。');
  } else if (!hasName) {
    setUploadHint(`已选择 ${uploadFile.name}，请填写音色名称，方便后续在指令和情绪路由里识别。`, 'warn');
  } else if (!hasConsent) {
    setUploadHint(`已选择 ${uploadFile.name}，请确认已获得声音使用授权后再上传。`, 'warn');
  } else {
    const sizeMb = (uploadFile.size / 1024 / 1024).toFixed(2);
    setUploadHint(`准备就绪：${uploadFile.name}，${sizeMb}MB。上传后会自动加入音色库并选中用于试听。`, 'ok');
  }

  const canPreview = Boolean(
    state.voices.some(voice => voice.enabled !== false) &&
    $('preview-text').value.trim() &&
    $('preview-voice').value
  );
  $('preview-btn').disabled = !canPreview;
  setPreviewHint(
    canPreview
      ? '试听已就绪；开启 AI 导演时，这次试听也会走隐藏导演链路。'
      : previewDisabledReason(),
    canPreview ? 'ok' : 'warn'
  );
  renderReadiness();
}

function applyState(payload) {
  state.config = payload.config || {};
  state.voices = payload.voices || [];
  state.defaults = payload.defaults || {};
  state.emotions = payload.emotions || state.emotions;
  state.readiness = payload.readiness || {};
  state.accessControl = payload.access_control || {};

  $('api-key').value = state.config.api_key || '';
  $('base-url').value = state.config.base_url || 'https://api.xiaomimimo.com/v1';
  $('model').value = state.config.model || 'mimo-v2.5-tts-voiceclone';
  $('default-context').value = state.config.default_context || '';
  $('max-text-chars').value = state.config.max_text_chars || 500;
  $('max-concurrency').value = state.config.max_concurrency || 1;
  $('max-voice-file-mb').value = state.config.max_voice_file_mb || 10;
  $('reply-mode').value = state.config.reply_mode || 'audio_only';
  $('auto-tts-enabled').checked = state.config.auto_tts_enabled === true;
  $('auto-tts-probability').value = state.config.auto_tts_probability ?? 0;
  $('auto-tts-group-whitelist').value = (state.config.auto_tts_group_whitelist || []).join('\n');
  $('auto-tts-group-blacklist').value = (state.config.auto_tts_group_blacklist || []).join('\n');
  $('auto-tts-private-whitelist').value = (state.config.auto_tts_private_whitelist || []).join('\n');
  $('auto-tts-private-blacklist').value = (state.config.auto_tts_private_blacklist || []).join('\n');
  $('admin-users').value = (state.config.admin_users || []).join('\n');
  $('file-fallback-enabled').checked = state.config.file_fallback_enabled !== false;
  $('output-retention-days').value = state.config.output_retention_days ?? 7;
  $('output-max-files').value = state.config.output_max_files ?? 100;
  $('emotion-routing-enabled').checked = state.config.emotion_routing_enabled !== false;
  $('ai-style-director-enabled').checked = state.config.ai_style_director_enabled === true;
  $('ai-style-director-provider-id').value = state.config.ai_style_director_provider_id || '';
  renderProviderSelect();
  $('ai-style-director-prompt').value = state.config.ai_style_director_prompt || '';
  $('ai-style-director-mode').value = state.config.ai_style_director_mode || 'direct';
  $('ai-style-director-max-chars').value = state.config.ai_style_director_max_chars || 120;
  $('ai-style-director-optimize-text').checked = state.config.ai_style_director_optimize_text !== false;
  $('ai-style-director-fallback').checked = state.config.ai_style_director_fallback_to_emotion !== false;
  $('ai-style-director-debug-log').checked = state.config.ai_style_director_debug_log !== false;
  $('segment-enabled').checked = state.config.segment_enabled !== false;
  $('segment-threshold-chars').value = state.config.segment_threshold_chars || 180;
  $('segment-max-segments').value = state.config.segment_max_segments || 6;

  fillEmotionSelect($('voice-emotion'), true);
  fillEmotionSelect($('preview-emotion'), true);
  updateStatus();
  renderEmotionDefaults();
  renderVoices();
  renderReadiness();
  renderAccessControl();
  markClean();
  updateActionAvailability();
}

function renderEmotionDefaults() {
  const wrap = $('emotion-defaults');
  wrap.innerHTML = '';
  const defaults = state.defaults.emotion_defaults || {};

  state.emotions.forEach(emotion => {
    const selected = defaults[emotion] || '';
    const options = ['<option value="">未设置</option>'].concat(
      state.voices.map(voice => (
        `<option value="${voice.id}" ${voice.id === selected ? 'selected' : ''}>${escapeHtml(voice.name)}</option>`
      ))
    ).join('');

    const card = document.createElement('div');
    card.className = 'emotion-card';
    card.innerHTML = `
      <strong>${emotion}</strong>
      <span class="voice-meta">${selected ? '已绑定情绪默认音色' : '跟随用户/群/全局默认'}</span>
      <select data-emotion="${emotion}">${options}</select>
    `;
    wrap.appendChild(card);
  });
}

function renderVoices() {
  $('voice-count').textContent = `${state.voices.length} 个音色`;
  const list = $('voice-list');
  const select = $('preview-voice');
  list.innerHTML = '';
  select.innerHTML = '';

  if (!state.voices.length) {
    list.innerHTML = '<div class="empty-state">暂无音色。上传已授权的 mp3 / wav 样本后，MiMo 会按该样本进行 voiceclone。</div>';
    select.innerHTML = '<option value="">暂无音色</option>';
    renderEmotionDefaults();
    return;
  }

  state.voices.forEach(voice => {
    const isDefault = voice.id === state.defaults.global_default_voice_id;
    const disabled = !voice.enabled;
    const emotionDefaults = Object.entries(state.defaults.emotion_defaults || {})
      .filter(([, voiceId]) => voiceId === voice.id)
      .map(([emotion]) => emotion);

    const card = document.createElement('div');
    card.className = `voice-card${disabled ? ' is-disabled' : ''}`;
    card.innerHTML = `
      <div>
        <div class="voice-title">
          <span>${escapeHtml(voice.name)}</span>
          ${isDefault ? '<span class="tag">全局默认</span>' : ''}
          ${disabled ? '<span class="tag">已禁用</span>' : ''}
        </div>
        <div class="voice-meta">${escapeHtml(voice.description || '无说明')} · ${escapeHtml(voice.id)}</div>
      </div>
      <div class="tag-row">
        <span class="tag">建议情绪：${escapeHtml(voice.emotion || '未设置')}</span>
        <span class="tag">情绪默认：${escapeHtml(emotionDefaults.join(', ') || '无')}</span>
        ${voice.style_tags ? `<span class="tag">标签：${escapeHtml(voice.style_tags)}</span>` : ''}
      </div>
      <div class="voice-meta">风格指令：${escapeHtml(voice.style_context || '无')}</div>
      <div class="voice-actions">
        <button data-action="default" data-id="${voice.id}">设为默认</button>
        <button data-action="toggle" data-id="${voice.id}">${disabled ? '启用' : '禁用'}</button>
        <button class="danger" data-action="delete" data-id="${voice.id}">删除</button>
      </div>
    `;
    list.appendChild(card);

    if (!disabled) {
      const option = document.createElement('option');
      option.value = voice.id;
      option.textContent = voice.name;
      select.appendChild(option);
    }
  });
  if (lastUploadedVoiceId && state.voices.some(voice => voice.id === lastUploadedVoiceId)) {
    select.value = lastUploadedVoiceId;
    lastUploadedVoiceId = '';
  }
  updateActionAvailability();
}

async function refresh() {
  const [payload, providersPayload] = await Promise.all([
    bridge.apiGet('get_config'),
    bridge.apiGet('list_ai_providers').catch(() => ({ success: false, providers: [] })),
  ]);
  if (!payload.success) throw new Error(payload.error || '加载配置失败');
  state.providers = providersPayload && providersPayload.success ? providersPayload.providers || [] : [];
  applyState(payload);
}

async function saveConfig() {
  const res = await bridge.apiPost('save_config', configPayload());
  if (!res.success) throw new Error(res.error || '保存失败');
  state.config = res.config || state.config;
  updateStatus();
  markClean();
  if (res.warning) {
    setActionState('配置已保存到插件本地文件', 'is-dirty');
    toast(res.warning, 'warn');
    return;
  }
  toast('配置已保存');
}

function validateVoiceUpload() {
  const file = $('voice-file').files[0];
  const name = $('voice-name').value.trim();
  if (!file) throw new Error('请选择音频文件');
  if (!/\.(mp3|wav)$/i.test(file.name)) throw new Error('只支持 mp3 / wav 音频');
  if (file.size > Number($('max-voice-file-mb').value || 10) * 1024 * 1024) {
    throw new Error('音频文件超过大小限制');
  }
  if (!name) throw new Error('请填写音色名称');
  if (!$('voice-consent').checked) throw new Error('请先确认已获得声音使用授权');
  return { file, name };
}

function voiceMetadataPayload(name) {
  return {
    name,
    description: $('voice-desc').value.trim(),
    emotion: $('voice-emotion').value,
    style_tags: $('voice-style-tags').value.trim(),
    style_context: $('voice-style-context').value.trim(),
    consent_confirmed: $('voice-consent').checked ? 'true' : 'false',
  };
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      resolve(result.includes(',') ? result.split(',', 2)[1] : result);
    };
    reader.onerror = () => reject(new Error('读取音频文件失败'));
    reader.readAsDataURL(file);
  });
}

async function uploadVoiceSample(file, metadata) {
  try {
    return await bridge.upload('upload_voice_sample', file);
  } catch (error) {
    setUploadHint('常规上传遇到网络错误，正在切换兼容上传...', 'warn');
    const audioBase64 = await readFileAsBase64(file);
    return await bridge.apiPost('upload_voice_sample_json', {
      filename: file.name,
      audio_base64: audioBase64,
      ...metadata,
    });
  }
}

async function syncVoiceMetadata(voiceId, metadata) {
  const res = await bridge.apiPost('update_voice', {
    voice_id: voiceId,
    ...metadata,
  });
  if (!res.success) throw new Error(res.error || '元数据同步失败');
}

async function uploadVoice() {
  const { file, name } = validateVoiceUpload();
  const metadata = voiceMetadataPayload(name);

  const res = await uploadVoiceSample(file, metadata);
  if (!res.success || !res.voice) throw new Error(res.error || '上传失败');
  lastUploadedVoiceId = res.voice.id;

  try {
    await syncVoiceMetadata(res.voice.id, metadata);
  } catch (error) {
    toast(`音色已上传，但元数据同步失败：${error.message || error}`, 'warn');
  }

  ['voice-file', 'voice-name', 'voice-desc', 'voice-style-tags', 'voice-style-context'].forEach(id => {
    $(id).value = '';
  });
  $('voice-emotion').value = '';
  $('voice-consent').checked = false;
  try {
    await refresh();
    setUploadHint('音色已上传，并已自动选中用于试听。', 'ok');
    toast('音色已上传');
  } catch (error) {
    setUploadHint('音色已上传，但刷新列表失败；请手动刷新页面查看。', 'warn');
    toast(`音色已上传，但刷新列表失败：${error.message || error}`, 'warn');
  }
}

function resetDeleteConfirmation(button) {
  if (!button) return;
  clearTimeout(button._confirmTimeout);
  button.dataset.confirming = 'false';
  button.textContent = button.dataset.originalText || '删除';
  button.classList.remove('confirming');
}

async function voiceAction(action, id, button = null) {
  const voice = state.voices.find(item => item.id === id);
  if (!voice) return;

  if (action === 'default') {
    const res = await bridge.apiPost('set_default_voice', { scope: 'global', voice_id: id });
    if (!res.success) throw new Error(res.error || '设置默认失败');
  } else if (action === 'toggle') {
    const res = await bridge.apiPost('update_voice', { voice_id: id, enabled: !voice.enabled });
    if (!res.success) throw new Error(res.error || '更新失败');
  } else if (action === 'delete') {
    if (!button) throw new Error('删除按钮状态不可用，请刷新页面后重试');
    if (button.dataset.confirming !== 'true') {
      button.dataset.confirming = 'true';
      button.dataset.originalText = button.textContent;
      button.textContent = '确定删除？';
      button.classList.add('confirming');
      button._confirmTimeout = setTimeout(() => resetDeleteConfirmation(button), 3000);
      toast(`再次点击确认删除「${voice.name}」`, 'warn');
      return;
    }
    resetDeleteConfirmation(button);
    const res = await bridge.apiPost('delete_voice', { voice_id: id });
    if (!res.success) throw new Error(res.error || '删除失败');
  }

  await refresh();
}

async function setEmotionDefault(emotion, voiceId) {
  const res = await bridge.apiPost('set_emotion_voice', { emotion, voice_id: voiceId });
  if (!res.success) throw new Error(res.error || '设置情绪默认音色失败');
  await refresh();
  toast(voiceId ? `${emotion} 默认音色已更新` : `${emotion} 默认音色已清空`);
}

async function preview() {
  const text = $('preview-text').value.trim();
  const voiceId = $('preview-voice').value;
  if (!text) throw new Error('请输入试听文本');
  if (!voiceId) throw new Error('请选择音色');

  let res;
  try {
    res = await bridge.apiPost('synthesize_preview', {
      text,
      voice_id: voiceId,
      emotion: $('preview-emotion').value,
      context: $('preview-context').value,
    });
  } catch (error) {
    throw new Error(extractErrorMessage(error, '试听失败'));
  }
  if (!res.success || !res.audio_data) throw new Error(res.error || '试听失败');

  $('preview-audio').src = res.audio_data;
  $('preview-audio').play().catch(() => {});
  toast(`试听生成成功，情绪：${res.emotion || 'neutral'}`);
}

async function testConnection() {
  let res;
  try {
    res = await bridge.apiPost('test_connection', {
      voice_id: $('preview-voice').value,
      text: '连接测试，声音工作正常。',
    });
  } catch (error) {
    $('test-hint').textContent = extractErrorMessage(error, '连接诊断失败');
    $('test-hint').className = 'field-hint warn';
    throw error;
  }
  if (!res.success) {
    $('test-hint').textContent = res.error || '连接诊断失败';
    $('test-hint').className = 'field-hint warn';
    throw new Error(res.error || '连接诊断失败');
  }
  $('test-hint').textContent = `${res.message || '连接测试成功'} 耗时 ${res.elapsed_ms || 0}ms。`;
  $('test-hint').className = 'field-hint ok';
  toast('连接诊断通过');
}

function bind(id, handler, busyText = '处理中...') {
  $(id).addEventListener('click', event => {
    runAction(event.currentTarget, busyText, handler);
  });
}

function bindConfigDirtyState() {
  [
    'api-key',
    'base-url',
    'model',
    'default-context',
    'max-text-chars',
    'max-concurrency',
    'max-voice-file-mb',
    'reply-mode',
    'auto-tts-enabled',
    'auto-tts-probability',
    'auto-tts-group-whitelist',
    'auto-tts-group-blacklist',
    'auto-tts-private-whitelist',
    'auto-tts-private-blacklist',
    'admin-users',
    'file-fallback-enabled',
    'output-retention-days',
    'output-max-files',
    'emotion-routing-enabled',
    'ai-style-director-enabled',
    'ai-style-director-provider-select',
    'ai-style-director-provider-id',
    'ai-style-director-prompt',
    'ai-style-director-mode',
    'ai-style-director-max-chars',
    'ai-style-director-optimize-text',
    'ai-style-director-fallback',
    'ai-style-director-debug-log',
    'segment-enabled',
    'segment-threshold-chars',
    'segment-max-segments',
  ].forEach(id => {
    const el = $(id);
    el.addEventListener('input', markDirty);
    el.addEventListener('change', markDirty);
  });
}

function bindActionAvailability() {
  [
    'voice-file',
    'voice-name',
    'voice-consent',
    'preview-voice',
    'preview-text',
  ].forEach(id => {
    const el = $(id);
    el.addEventListener('input', updateActionAvailability);
    el.addEventListener('change', updateActionAvailability);
  });
}

function bindProviderSelect() {
  $('ai-style-director-provider-select').addEventListener('change', event => {
    $('ai-style-director-provider-id').value = event.target.value;
    markDirty();
  });
  $('ai-style-director-provider-id').addEventListener('input', renderProviderSelect);
}

async function init() {
  bridge = await resolveBridge();
  await bridge.ready();
  bind('save-config', saveConfig, '保存中...');
  bind('upload-voice', uploadVoice, '上传中...');
  bind('preview-btn', preview, '生成中...');
  bind('test-connection', testConnection, '诊断中...');
  bindConfigDirtyState();
  bindActionAvailability();
  bindProviderSelect();
  updateActionAvailability();

  $('voice-list').addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    try {
      await voiceAction(button.dataset.action, button.dataset.id, button);
    } catch (error) {
      toast(extractErrorMessage(error), 'err');
    }
  });

  $('emotion-defaults').addEventListener('change', async event => {
    const select = event.target.closest('select[data-emotion]');
    if (!select) return;
    try {
      await setEmotionDefault(select.dataset.emotion, select.value);
    } catch (error) {
      toast(extractErrorMessage(error), 'err');
    }
  });

  await refresh();
}

init().catch(error => toast(extractErrorMessage(error), 'err'));
