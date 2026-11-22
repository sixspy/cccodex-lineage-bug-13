import { api } from '../api.js';

export async function renderCoverage(view, setMsg) {
  const [reports, progress] = await Promise.all([
    api('/api/coverage/reports'),
    api('/api/coverage/binding'),
  ]);
  view.innerHTML = `
    <section class="card">
      <h2>覆盖对账 · 标签绑定覆盖进度</h2>
      <div class="kpis">
        <div class="kpi"><b>${progress.live_node_count}</b><span>在用节点</span></div>
        <div class="kpi"><b>${progress.active_bound_count}</b><span>真绑·现行</span></div>
        <div class="kpi"><b>${progress.virtual_bound_count}</b><span>虚绑(在用)</span></div>
        <div class="kpi"><b>${progress.retired_virtual_count}</b><span>停用虚绑(历史轨)</span></div>
        <div class="kpi"><b>${progress.unbound_count}</b><span>未绑定</span></div>
      </div>
      <p class="muted">真绑覆盖率 ${progress.coverage_pct}%</p>
      <div class="progress"><i style="width:${progress.coverage_pct}%"></i></div>
      <div class="row" style="margin-top:.75rem">
        <button class="primary" id="go" type="button">跑覆盖对账</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(reports.items || []).map((r) => `<article class="card">
        <h3>报告 #${r.id}</h3>
        <p>${r.summary}</p>
        <p class="muted">节点 ${r.node_count} · 已发布边 ${r.edge_count} · 孤儿 ${r.orphan_count} · 草稿边 ${r.uncovered_edges}</p>
      </article>`).join('') || '<p class="muted">暂无对账报告</p>'}
    </section>`;
  document.getElementById('go').onclick = async () => {
    try {
      const r = await api('/api/coverage/reports', { method: 'POST', body: '{}' });
      setMsg(r.report.summary);
      await renderCoverage(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
}
