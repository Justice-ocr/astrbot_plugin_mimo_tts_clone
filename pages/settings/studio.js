'use strict';

let studioCatalog = {styles: {}, sessions: {}};
let historyPage = 1;
let historyPages = 1;
let historyRequest = 0;
let studioJobId = '';
let studioJobIds = [];
let studioCancelled = false;
let studioPlayer = null;
let lyricsVersions = [];
let lyricsParent = '';
let bindingRows = [];
const comparisonItems = new Map();

function renderComparison() {
  const host = $('history-comparison');
  host.replaceChildren();
  for (const [id, item] of comparisonItems) {
    const column = document.createElement('article');
    const title = document.createElement('strong');
    title.textContent = `${item.voice_name} · ${id.slice(0, 8)}`;
    const description = document.createElement('p');
    description.textContent = item.context || item.text.slice(0, 80);
    const player = document.createElement('audio');
    player.controls = true;
    player.src = item.audio_data;
    player.addEventListener('play', () => {
      for (const other of host.querySelectorAll('audio')) if (other !== player) other.pause();
    });
    const remove = document.createElement('button');
    remove.textContent = '移出对比';
    remove.addEventListener('click', () => { comparisonItems.delete(id); renderComparison(); });
    column.append(title, description, player, remove);
    host.append(column);
  }
}

async function refreshLyrics(selected = '') {
  const result = await bridge.apiGet('lyrics');
  if (!result.success) throw new Error(result.error || '歌词版本加载失败');
  lyricsVersions = result.items;
  studioOptions('lyrics-version', lyricsVersions.map(item => [
    item.id, `${item.title} · ${new Date(item.created_at * 1000).toLocaleString()}`,
  ]), '新歌词');
  if (selected) $('lyrics-version').value = selected;
}

async function studioPost(endpoint, data) {
  const result = await bridge.apiPost(endpoint, data);
  if (!result.success) throw new Error(result.error || '操作失败');
  return result;
}

function studioOptions(id, items, empty) {
  const select = $(id);
  const previous = select.value;
  select.replaceChildren();
  if (empty !== undefined) select.add(new Option(empty, ''));
  for (const [value, name] of items) select.add(new Option(name, value));
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

async function studioRefresh() {
  const result = await bridge.apiGet('studio_catalog');
  if (!result.success) throw new Error(result.error || '加载工作台失败');
  studioCatalog = result;
  $('history-max-records').value = result.history_policy.max_records;
  $('history-retention-days').value = result.history_policy.retention_days;
  studioOptions('studio-builtin', result.builtin_voices.map(v => [v, v]));
  const voiceTypes = {builtin: '内置', design: '设计', clone: '克隆'};
  const voices = state.voices.filter(isVoiceUsable).map(v => [v.id, `${v.name} · ${voiceTypes[v.type || 'clone'] || v.type}`]);
  studioOptions('studio-voice', voices);
  studioOptions('studio-session-voice', voices, '跟随默认');
  studioOptions('binding-voice', voices, '解除绑定');
  const styles = Object.entries(result.styles).map(([key, value]) => [key, value.name]);
  studioOptions('studio-style', styles, '音色默认');
  studioOptions('studio-style-edit', styles, '新增风格');
  studioOptions('studio-session-style', styles, '跟随默认');
  $('studio-session-list').replaceChildren(...Object.keys(result.sessions).map(key => new Option(key, key)));
  studioCapabilities();
  await refreshBindings();
}

async function refreshBindings() {
  const result = await bridge.apiGet('voice_bindings');
  if (!result.success) throw new Error(result.error || '绑定加载失败');
  const rows = [['global', '', result.bindings.global_default_voice_id]];
  for (const scope of ['user', 'group', 'emotion']) {
    for (const [key, value] of Object.entries(result.bindings[`${scope}_defaults`] || {})) rows.push([scope, key, value]);
  }
  bindingRows = rows;
  syncBindingEditor();
  const host = $('binding-list');
  host.replaceChildren();
  for (const [scope, key, id] of rows) {
    if (!id) continue;
    const row = document.createElement('div');
    row.className = 'binding-row';
    const label = document.createElement('span');
    const name = state.voices.find(v => v.id === id)?.name || `不可用 (${id})`;
    label.textContent = `${{global: '全局', user: '用户', group: '会话', emotion: '情绪'}[scope]} ${key} → ${name}`;
    const edit = document.createElement('button');
    edit.textContent = '编辑';
    edit.addEventListener('click', () => {
      $('binding-scope').value = scope;
      $('binding-key').value = key;
      syncBindingEditor();
    });
    row.append(label, edit);
    host.append(row);
  }
}

function syncBindingEditor() {
  const scope = $('binding-scope').value;
  const global = scope === 'global';
  $('binding-target').hidden = global;
  $('binding-key').disabled = global;
  const key = global ? '' : $('binding-key').value.trim();
  const binding = bindingRows.find(row => row[0] === scope && row[1] === key);
  $('binding-voice').value = binding?.[2] || '';
}

function studioDraft() {
  return {
    type: $('studio-type').value, name: $('studio-name').value,
    builtin_voice: $('studio-builtin').value, design_prompt: $('studio-design').value,
    style_context: $('studio-voice-style').value,
  };
}

function updateSingDefaultAvailability() {
  const button = $('studio-set-sing');
  if (button.classList.contains('is-busy')) return;
  const isSongDefault = studioCatalog.defaults?.sing?.voice_id === $('studio-voice').value;
  button.disabled = isSongDefault;
  button.textContent = isSongDefault ? '默认唱歌音色' : '设为默认唱歌音色';
}

function studioCapabilities() {
  const type = $('studio-draft').checked ? $('studio-type').value
    : state.voices.find(v => v.id === $('studio-voice').value)?.type;
  $('studio-mode').options[1].disabled = type !== 'builtin';
  if (type !== 'builtin') $('studio-mode').value = 'speech';
  $('studio-director').disabled = $('studio-mode').value === 'sing';
  $('studio-director').closest('label').hidden = $('studio-mode').value === 'sing';
  $('studio-set-sing').hidden = type !== 'builtin' || $('studio-draft').checked;
  updateSingDefaultAvailability();
  $('studio-builtin').disabled = $('studio-type').value !== 'builtin';
  $('studio-design').disabled = $('studio-type').value !== 'design';
  $('studio-design').closest('label').hidden = $('studio-type').value !== 'design';
  $('studio-builtin').closest('label').hidden = $('studio-type').value !== 'builtin';
  $('studio-stream').disabled = type !== 'builtin';
  $('studio-candidates').disabled = type !== 'design';
  $('studio-candidates').closest('label').hidden = type !== 'design';
  if (type !== 'design') $('studio-candidates').value = '1';
  if (type !== 'builtin') $('studio-stream').checked = false;
}

async function loadHistory(page = historyPage) {
  const requestId = ++historyRequest;
  $('history-prev').disabled = true;
  $('history-next').disabled = true;
  try {
    const result = await bridge.apiGet('generation_history', {page});
    if (requestId !== historyRequest) return;
    if (!result.success) throw new Error(result.error || '历史加载失败');
    historyPage = result.page;
    historyPages = result.pages;
    $('history-page').textContent = `${historyPage} / ${historyPages}`;
    $('history-total').textContent = `${result.total} 条`;
    const list = $('history-list');
    list.replaceChildren();
    for (const item of result.items) {
      const row = document.createElement('article');
      row.className = 'generation-row';
      const heading = document.createElement('strong');
      heading.textContent = `${item.voice_name} · ${item.mode === 'sing' ? '唱歌' : '朗读'}`;
      const meta = document.createElement('p');
      meta.textContent = `${new Date(item.created_at * 1000).toLocaleString()} · ${item.model} · ${item.elapsed_ms} ms`;
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = item.text.slice(0, 100);
      const full = document.createElement('p');
      full.textContent = item.text;
      details.append(summary, full);
      const audio = document.createElement('audio');
      audio.controls = true;
      audio.hidden = true;
      const actions = document.createElement('div');
      actions.className = 'voice-actions';
      function action(label, fn, disabled = false) {
        const button = document.createElement('button');
        button.textContent = label;
        button.disabled = disabled;
        button.addEventListener('click', () => runAction(button, '处理中...', fn));
        actions.append(button);
      }
      async function getAudio() {
        const result = await bridge.apiGet('history_audio', {id: item.id});
        if (!result.success) throw new Error(result.error);
        return result.audio_data;
      }
      action(item.available ? '播放' : '音频已过期', async () => {
        audio.src = await getAudio();
        audio.hidden = false;
        await audio.play();
      }, !item.available);
      action('下载', async () => {
        const link = document.createElement('a');
        link.href = await getAudio();
        link.download = `${item.id}.wav`;
        link.click();
      }, !item.available);
      action('加入对比', async () => {
        if (comparisonItems.size >= 3 && !comparisonItems.has(item.id)) throw new Error('最多同时对比 3 个候选');
        comparisonItems.set(item.id, {...item, audio_data: await getAudio()});
        renderComparison();
      }, !item.available);
      action('载入文本', async () => {
        $('studio-text').value = item.text;
        $('studio-voice').value = item.voice_id;
        $('studio-mode').value = item.mode;
        studioCapabilities();
        $('multimodel-studio').scrollIntoView({behavior: 'smooth'});
      });
      action('发送音频', async () => {
        const session = $('history-target').value.trim() || item.session || '';
        if (!session) throw new Error('请填写重发目标会话');
        if (!await askConfirmation(`将此音频发送到 ${session}？`)) return;
        await studioPost('resend_history', {id: item.id, session, confirm: true});
        toast('已发送');
      }, !item.available);
      action('另存为克隆音色', async () => {
        if (!await askConfirmation('确认拥有此音频的声音使用授权，并将其保存为克隆参考？')) return;
        await studioPost('history_to_clone', {id: item.id, name: `${item.voice_name} 克隆`, consent_confirmed: true});
        await refresh();
        await studioRefresh();
      }, !item.available);
      action('删除记录', async () => {
        if (!await askConfirmation('删除这条生成记录？音频仍按保留策略清理。')) return;
        await studioPost('delete_history', {id: item.id});
        await loadHistory();
      });
      row.append(heading, meta, details, actions, audio);
      list.append(row);
    }
    if (!result.items.length) {
      const empty = document.createElement('p');
      empty.className = 'history-empty';
      empty.textContent = '暂无生成历史';
      list.append(empty);
    }
  } finally {
    if (requestId === historyRequest) {
      $('history-prev').disabled = historyPage <= 1;
      $('history-next').disabled = historyPage >= historyPages;
    }
  }
}

async function initStudio() {
  $('studio-import-file').addEventListener('change', () => {
    $('studio-import-name').textContent = $('studio-import-file').files[0]?.name || '未选择文件';
  });
  $('binding-scope').addEventListener('change', () => {
    $('binding-key').value = '';
    syncBindingEditor();
  });
  $('binding-key').addEventListener('input', syncBindingEditor);
  bind('binding-save', async () => {
    await studioPost('save_voice_binding', {
      scope: $('binding-scope').value, key: $('binding-key').value, voice_id: $('binding-voice').value,
    });
    await refresh();
    await studioRefresh();
  });
  bind('lyrics-save', async () => {
    const result = await studioPost('save_lyrics', {
      text: $('studio-text').value, title: $('lyrics-title').value, parent_id: lyricsParent,
    });
    lyricsParent = result.item.id;
    await refreshLyrics(lyricsParent);
    toast('歌词版本已保存');
  });
  bind('lyrics-load', async () => {
    const item = lyricsVersions.find(value => value.id === $('lyrics-version').value);
    if (!item) { lyricsParent = ''; return; }
    if ($('studio-text').value.trim() && !await askConfirmation('载入版本将替换当前正文，继续？')) return;
    $('studio-text').value = item.text;
    $('lyrics-title').value = item.title;
    $('lyrics-prompt').value = item.prompt || '';
    lyricsParent = item.id;
    $('studio-mode').value = 'sing';
    studioCapabilities();
  });
  bind('lyrics-draft', async () => {
    if (!await askConfirmation('调用已配置的 AI 服务商创作歌词？这可能产生费用，结果将保存为新版本。')) return;
    const result = await studioPost('draft_lyrics', {
      text: $('studio-text').value, prompt: $('lyrics-prompt').value,
      title: $('lyrics-title').value, parent_id: lyricsParent,
    });
    lyricsParent = result.item.id;
    $('studio-text').value = result.item.text;
    await refreshLyrics(lyricsParent);
  }, '创作中...');
  bind('lyrics-delete', async () => {
    const id = $('lyrics-version').value;
    if (!id || !await askConfirmation('删除这个歌词版本？')) return;
    await studioPost('delete_lyrics', {id});
    if (lyricsParent === id) lyricsParent = '';
    await refreshLyrics();
  });
  await refreshLyrics();
  async function exportStudio(voiceIds) {
    const hasClone = state.voices.some(v => voiceIds.includes(v.id) && (v.type || 'clone') === 'clone');
    if (hasClone && !await askConfirmation('导出包将包含克隆参考音频。确认允许导出这些声音样本？')) return;
    const result = await studioPost('export_studio', {voice_ids: voiceIds, include_audio: hasClone});
    const blob = new Blob([JSON.stringify(result.package, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'mimo-studio.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  bind('studio-export', () => exportStudio(state.voices.map(v => v.id)));
  bind('studio-export-styles', () => exportStudio([]));
  bind('studio-import', async () => {
    const file = $('studio-import-file').files[0];
    if (!file || file.size > 24 * 1024 * 1024) throw new Error('请选择不超过 24 MB 的 JSON 文件');
    const data = JSON.parse(await file.text());
    const hasClone = Array.isArray(data.voices) && data.voices.some(v => (v.type || 'clone') === 'clone');
    if (hasClone && !await askConfirmation('确认拥有导入包中所有克隆音频的使用授权？')) return;
    await studioPost('import_studio', {package: data, consent_confirmed: hasClone});
    await refresh();
    await studioRefresh();
    toast('导入完成');
  });
  for (const id of ['studio-type', 'studio-draft', 'studio-voice', 'studio-mode']) {
    $(id).addEventListener('change', studioCapabilities);
  }
  bind('studio-save-voice', async () => {
    await studioPost('create_voice', studioDraft());
    await refresh();
    await studioRefresh();
  }, '保存中...');
  bind('studio-generate', async () => {
    studioCancelled = false;
    const candidates = Number($('studio-candidates').value);
    if (candidates > 1 && !await askConfirmation(`生成 ${candidates} 个独立设计候选？每个候选都会调用模型并可能产生费用。`)) return;
    const streaming = $('studio-stream').checked;
    let cursor = 0;
    let playAt = 0;
    if (streaming) {
      studioPlayer = new AudioContext({sampleRate: 24000});
      await studioPlayer.resume();
    }
    try {
      const result = await studioPost('studio_generate', {
      text: $('studio-text').value, mode: $('studio-mode').value,
      emotion: $('preview-emotion').value,
      voice_id: $('studio-voice').value, style_id: $('studio-style').value,
      context: $('studio-context').value,
      draft: $('studio-draft').checked ? studioDraft() : null,
      director: $('studio-mode').value !== 'sing' && $('studio-director').checked,
      stream: streaming,
      candidates,
    });
      studioJobIds = result.job_ids || [result.job_id];
      $('studio-cancel').disabled = false;
      for (const id of studioJobIds) {
      if (studioCancelled) break;
      studioJobId = id;
      cursor = 0;
      while (studioJobId) {
        const job = await bridge.apiGet('studio_job', {id: studioJobId, cursor});
        if (!job.success) throw new Error(job.error || '读取试听任务失败');
        cursor = job.cursor;
        $('studio-progress').textContent = {
          queued: '排队中', running: streaming ? '流式生成中' : '生成中',
          completed: '已完成', failed: '生成失败', cancelled: '已取消',
        }[job.status] || job.status;
        if (studioPlayer && studioPlayer.state !== 'closed' && job.status !== 'cancelled') {
          for (const encoded of job.chunks) {
            const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
            const view = new DataView(bytes.buffer);
            const buffer = studioPlayer.createBuffer(1, bytes.length / 2, 24000);
            const samples = buffer.getChannelData(0);
            for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
            const source = studioPlayer.createBufferSource();
            source.buffer = buffer;
            source.connect(studioPlayer.destination);
            playAt = Math.max(playAt, studioPlayer.currentTime + 0.04);
            source.start(playAt);
            playAt += buffer.duration;
          }
        }
        if (job.status === 'failed') throw new Error(job.error);
        if (job.status === 'cancelled') break;
        if (job.status === 'completed' && !job.has_more && job.audio_data) {
          $('studio-audio').src = job.audio_data;
          $('studio-audio').hidden = false;
          await loadHistory(1);
          if (studioPlayer) {
            const player = studioPlayer;
            setTimeout(() => { if (player.state !== 'closed') player.close(); },
              Math.max(0, (playAt - player.currentTime + 0.2) * 1000));
            studioPlayer = null;
          }
          break;
        }
        await new Promise(resolve => setTimeout(resolve, job.has_more ? 10 : 350));
      }
      }
    } catch (error) {
      await Promise.all(studioJobIds.map(id => studioPost('cancel_studio_job', {id}).catch(() => {})));
      $('studio-progress').textContent = '生成失败';
      throw error;
    } finally {
      studioJobId = '';
      studioJobIds = [];
      $('studio-cancel').disabled = true;
      if (studioPlayer && studioPlayer.state !== 'closed') await studioPlayer.close();
      studioPlayer = null;
    }
  }, '生成中...');
  bind('studio-cancel', async () => {
    studioCancelled = true;
    if (studioPlayer && studioPlayer.state !== 'closed') await studioPlayer.close();
    await Promise.all(studioJobIds.map(id => studioPost('cancel_studio_job', {id})));
  });
  bind('studio-set-sing', async () => {
    await studioPost('save_studio_setting', {category: 'defaults', id: 'sing', value: {voice_id: $('studio-voice').value}});
    await studioRefresh();
    toast('默认唱歌音色已保存');
  });
  $('studio-style-edit').addEventListener('change', () => {
    const style = studioCatalog.styles[$('studio-style-edit').value] || {};
    $('studio-style-name').value = style.name || '';
    $('studio-style-context').value = style.context || '';
  });
  bind('studio-save-style', async () => {
    await studioPost('save_studio_setting', {
      category: 'styles', id: $('studio-style-edit').value || crypto.randomUUID(),
      value: {name: $('studio-style-name').value, context: $('studio-style-context').value},
    });
    await studioRefresh();
  });
  bind('studio-delete-style', async () => {
    if (!$('studio-style-edit').value || !await askConfirmation('删除此风格？')) return;
    await studioPost('save_studio_setting', {category: 'styles', id: $('studio-style-edit').value, value: null});
    await studioRefresh();
  });
  $('studio-session').addEventListener('change', async () => {
    const setting = studioCatalog.sessions[$('studio-session').value] || {};
    $('studio-session-voice').value = setting.voice_id || '';
    $('studio-session-style').value = setting.style_id || '';
    $('studio-session-auto').value = setting.auto_tts_enabled === undefined ? '' : String(setting.auto_tts_enabled);
    $('studio-session-probability').value = setting.auto_tts_probability ?? '';
    $('studio-session-director').value = setting.director === undefined ? '' : String(setting.director);
    const session = $('studio-session').value;
    try {
      const result = await bridge.apiGet('session_effective', {session});
      if (session !== $('studio-session').value) return;
      if (!result.success) throw new Error(result.error);
      const e = result.effective;
      $('studio-session-effective').textContent = `当前生效：${e.voice_name}（${e.voice_source}） · ${e.style} · 自动语音${e.auto_tts_enabled ? '开' : '关'} · 概率 ${e.auto_tts_probability} · 导演${e.director ? '开' : '关'}`;
    } catch (error) {
      $('studio-session-effective').textContent = error.message;
    }
  });
  bind('studio-save-session', async () => {
    const value = {voice_id: $('studio-session-voice').value, style_id: $('studio-session-style').value};
    if ($('studio-session-auto').value) value.auto_tts_enabled = $('studio-session-auto').value === 'true';
    if ($('studio-session-probability').value !== '') value.auto_tts_probability = Number($('studio-session-probability').value);
    if ($('studio-session-director').value) value.director = $('studio-session-director').value === 'true';
    await studioPost('save_studio_setting', {category: 'sessions', id: $('studio-session').value, value});
    await studioRefresh();
    $('studio-session').dispatchEvent(new Event('change'));
  });
  bind('studio-reset-session', async () => {
    await studioPost('save_studio_setting', {category: 'sessions', id: $('studio-session').value, value: null});
    await studioRefresh();
    $('studio-session').dispatchEvent(new Event('change'));
  });
  bind('history-prev', () => loadHistory(historyPage - 1));
  bind('history-save-policy', async () => {
    if (!await askConfirmation('保存后将清理超出期限或数量的历史记录，继续？')) return;
    await studioPost('save_studio_setting', {category: 'defaults', id: 'history', value: {
      max_records: Number($('history-max-records').value),
      retention_days: Number($('history-retention-days').value),
    }});
    await loadHistory(1);
  });
  bind('history-next', () => loadHistory(historyPage + 1));
  bind('history-clear', async () => {
    if (!await askConfirmation('清空全部生成历史记录？音频仍按保留策略清理。')) return;
    await studioPost('delete_history', {all: true, confirm: true});
    await loadHistory(1);
  });
  await studioRefresh();
  await loadHistory(1);
}
