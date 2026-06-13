'use strict';

const $ = id => document.getElementById(id);
const BRIDGE_UNAVAILABLE_MESSAGE = '请在 AstrBot 插件管理页中打开本页面。普通浏览器预览只能查看 UI，不能上传、保存或试听。';
const bridge = window.AstrBotPluginPage || {
  ready: async () => ({}),
  apiGet: async () => ({ success: false, error: BRIDGE_UNAVAILABLE_MESSAGE }),
  apiPost: async () => ({ success: false, error: BRIDGE_UNAVAILABLE_MESSAGE }),
  upload: async () => ({ success: false, error: BRIDGE_UNAVAILABLE_MESSAGE }),
};

let state = { config: {}, voices: [], defaults: {}, emotions: ['happy', 'sad', 'angry', 'neutral'] };

function toast(message, type = 'ok') {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast ${type}`;
  el.style.display = 'block';
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.style.display = 'none'; }, 3000);
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
    emotion_routing_enabled: $('emotion-routing-enabled').checked,
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

function updateStatus() {
  $('model-status').textContent = state.config.model || 'voiceclone';
  $('emotion-status').textContent = state.config.emotion_routing_enabled === false ? 'OFF' : 'ON';
  $('segment-status').textContent = state.config.segment_enabled === false ? 'OFF' : 'ON';
  $('hero-voice-count').textContent = String(state.voices.length);
}

function applyState(payload) {
  state.config = payload.config || {};
  state.voices = payload.voices || [];
  state.defaults = payload.defaults || {};
  state.emotions = payload.emotions || state.emotions;

  $('api-key').value = state.config.api_key || '';
  $('base-url').value = state.config.base_url || 'https://api.xiaomimimo.com/v1';
  $('model').value = state.config.model || 'mimo-v2.5-tts-voiceclone';
  $('default-context').value = state.config.default_context || '';
  $('max-text-chars').value = state.config.max_text_chars || 500;
  $('max-concurrency').value = state.config.max_concurrency || 1;
  $('max-voice-file-mb').value = state.config.max_voice_file_mb || 10;
  $('emotion-routing-enabled').checked = state.config.emotion_routing_enabled !== false;
  $('segment-enabled').checked = state.config.segment_enabled !== false;
  $('segment-threshold-chars').value = state.config.segment_threshold_chars || 180;
  $('segment-max-segments').value = state.config.segment_max_segments || 6;

  fillEmotionSelect($('voice-emotion'), true);
  fillEmotionSelect($('preview-emotion'), true);
  updateStatus();
  renderEmotionDefaults();
  renderVoices();
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

    const option = document.createElement('option');
    option.value = voice.id;
    option.textContent = voice.name;
    select.appendChild(option);
  });
}

async function refresh() {
  const payload = await bridge.apiGet('get_config');
  if (!payload.success) throw new Error(payload.error || '加载配置失败');
  applyState(payload);
}

async function saveConfig() {
  const res = await bridge.apiPost('save_config', configPayload());
  if (!res.success) throw new Error(res.error || '保存失败');
  state.config = res.config || state.config;
  updateStatus();
  toast('配置已保存');
}

async function uploadVoice() {
  const file = $('voice-file').files[0];
  if (!file) throw new Error('请选择音频文件');
  if (!$('voice-consent').checked) throw new Error('请先确认已获得声音使用授权');

  const res = await bridge.upload('upload_voice_sample', file);
  if (!res.success || !res.voice) throw new Error(res.error || '上传失败');

  await bridge.apiPost('update_voice', {
    voice_id: res.voice.id,
    name: $('voice-name').value.trim() || res.voice.name,
    description: $('voice-desc').value.trim(),
    emotion: $('voice-emotion').value,
    style_tags: $('voice-style-tags').value.trim(),
    style_context: $('voice-style-context').value.trim(),
  });

  ['voice-file', 'voice-name', 'voice-desc', 'voice-style-tags', 'voice-style-context'].forEach(id => {
    $(id).value = '';
  });
  $('voice-emotion').value = '';
  $('voice-consent').checked = false;
  await refresh();
  toast('音色已上传');
}

async function voiceAction(action, id) {
  const voice = state.voices.find(item => item.id === id);
  if (!voice) return;

  if (action === 'default') {
    const res = await bridge.apiPost('set_default_voice', { scope: 'global', voice_id: id });
    if (!res.success) throw new Error(res.error || '设置默认失败');
  } else if (action === 'toggle') {
    const res = await bridge.apiPost('update_voice', { voice_id: id, enabled: !voice.enabled });
    if (!res.success) throw new Error(res.error || '更新失败');
  } else if (action === 'delete') {
    if (!confirm(`删除音色「${voice.name}」？`)) return;
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

  const res = await bridge.apiPost('synthesize_preview', {
    text,
    voice_id: voiceId,
    emotion: $('preview-emotion').value,
    context: $('preview-context').value,
  });
  if (!res.success || !res.audio_data) throw new Error(res.error || '试听失败');

  $('preview-audio').src = res.audio_data;
  $('preview-audio').play().catch(() => {});
  toast(`试听生成成功，情绪：${res.emotion || 'neutral'}`);
}

function bind(id, handler) {
  $(id).addEventListener('click', async () => {
    try {
      await handler();
    } catch (error) {
      toast(String(error.message || error), 'err');
    }
  });
}

async function init() {
  await bridge.ready();
  bind('save-config', saveConfig);
  bind('upload-voice', uploadVoice);
  bind('preview-btn', preview);

  $('voice-list').addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    try {
      await voiceAction(button.dataset.action, button.dataset.id);
    } catch (error) {
      toast(String(error.message || error), 'err');
    }
  });

  $('emotion-defaults').addEventListener('change', async event => {
    const select = event.target.closest('select[data-emotion]');
    if (!select) return;
    try {
      await setEmotionDefault(select.dataset.emotion, select.value);
    } catch (error) {
      toast(String(error.message || error), 'err');
    }
  });

  await refresh();
}

init().catch(error => toast(String(error.message || error), 'err'));
