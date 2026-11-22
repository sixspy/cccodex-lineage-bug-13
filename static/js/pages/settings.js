import { api } from '../api.js';

export async function renderSettings(view, setMsg) {
  const j = await api('/api/settings');
  view.innerHTML = `
    <section class="card">
      <h2>台参数</h2>
      <div class="wall">
        ${(j.items || []).map((s) => `<article class="card">
          <h3>${s.label}</h3>
          <p class="muted">${s.key}</p>
          <div class="row">
            <input data-k="${s.key}" value="${s.value}" />
            <button data-save="${s.key}" class="primary" type="button">保存</button>
          </div>
        </article>`).join('')}
      </div>
    </section>`;
  view.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const input = view.querySelector('input[data-k="' + btn.dataset.save + '"]');
        await api('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
        });
        setMsg('参数已保存');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });

  try {
    const snap = await api('/api/tag_bind/snap');
    view.innerHTML += `<section class="card" style="margin-top:1rem">
      <h3>标签绑定 · 真实快照</h3>
      <span class="tag ${snap.aligned ? 'active-bind' : 'blocked'}">${snap.truth}</span>
      <p class="muted">闸门芯片 ${snap.panel_chip} · 在绑落点 ${snap.pack_count} ·
        真绑 ${snap.active_count} · 虚绑 ${snap.virtual_count} ·
        旁路放行 gate_bypass=${snap.gate_bypass}</p>
      <p class="muted">绑定/虚绑/解绑与落点行请在「标签绑定」页操作；只改芯片不落绑定行不算修复。</p>
    </section>`;
  } catch (e) { /* optional surface */ }
}
