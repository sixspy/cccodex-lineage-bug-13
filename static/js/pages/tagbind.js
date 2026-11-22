import { api } from '../api.js';

function trackTag(b) {
  if (b.state !== 'bound') return `<span class="tag unbound">已解绑·历史</span>`;
  if (b.track === 'virtual') return '<span class="tag virtual-bind">虚绑·只读历史</span>';
  return '<span class="tag active-bind">真绑·现行</span>';
}

export async function renderTagBind(view, setMsg) {
  const [dash, nodesJ] = await Promise.all([api('/api/tag_bind/dash'), api('/api/nodes')]);
  const tagOpts = (dash.tags || []).map((t) => `<option value="${t.id}">${t.tag_code} · ${t.tag_name}</option>`).join('');
  const freeNodes = (nodesJ.items || []).filter((n) => !n.bound);
  const nodeOpts = freeNodes.map((n) => `<option value="${n.id}">${n.node_code}（${n.status}）</option>`).join('');

  view.innerHTML = `
    <section class="card">
      <h2>标签绑定 · 双写两轨</h2>
      <p class="muted">现行管道只读真绑；虚绑推进只读历史轨，二者禁止并存。整笔提交，失败回白话。</p>
      <div class="kpis">
        <div class="kpi"><b>${dash.bound_count}</b><span>在绑落点</span></div>
        <div class="kpi"><b>${dash.active_count}</b><span>真绑·现行</span></div>
        <div class="kpi"><b>${dash.live_virtual_count}</b><span>在用虚绑(违规)</span></div>
        <div class="kpi"><b>${dash.retired_virtual_count}</b><span>停用虚绑(历史轨)</span></div>
        <div class="kpi"><b>${dash.drift_count}</b><span>指纹/状态漂移</span></div>
      </div>
      <p style="margin-top:.6rem">
        对齐状态：${dash.aligned
          ? '<span class="tag active-bind">已对齐 · 来源与虚绑数一致且表内有落点</span>'
          : '<span class="tag blocked">未对齐 · 存在虚绑/漂移或无落点</span>'}
      </p>
      <div class="row" style="margin-top:.5rem">
        <label>标签<select id="tag_sel">${tagOpts}</select></label>
        <label>节点<select id="node_sel">${nodeOpts || '<option value="">无空闲节点</option>'}</select></label>
        <button class="primary" id="do_bind" type="button">真绑（现行）</button>
        <button id="do_virt" type="button">虚绑（只读历史）</button>
      </div>
      <p class="err" id="tb_err"></p>
    </section>

    <section class="card" style="margin-top:1rem">
      <h3>绑定落点行（凭证表）</h3>
      <table class="grid">
        <thead><tr><th>#</th><th>标签</th><th>节点</th><th>轨</th><th>状态</th><th>节点快照</th><th>标签指纹</th><th>凭证指纹</th><th>漂移</th><th>操作人</th></tr></thead>
        <tbody>
          ${(dash.rows || []).map((b) => `<tr>
            <td>${b.id}</td><td>${b.tag_code}</td><td>${b.node_code}</td>
            <td>${trackTag(b)}</td><td>${b.state}</td><td>${b.node_status_snapshot}</td>
            <td>${b.tag_fingerprint}</td><td>${b.credential_fingerprint}</td>
            <td>${b.drift ? '<span class="tag drift">漂移</span>' : '一致'}</td>
            <td>${b.operator}</td>
          </tr>`).join('') || '<tr><td colspan="10" class="muted">暂无落点行</td></tr>'}
        </tbody>
      </table>
    </section>

    <section class="card" style="margin-top:1rem">
      <h3>绑定台账（只追加）</h3>
      <table class="grid">
        <thead><tr><th>动作</th><th>轨</th><th>标签</th><th>节点</th><th>节点状态</th><th>操作人</th><th>备注</th></tr></thead>
        <tbody>
          ${(dash.ledger || []).map((l) => `<tr>
            <td>${l.action}</td><td>${l.track || '—'}</td><td>${l.tag_code || '—'}</td>
            <td>${l.node_code || '—'}</td><td>${l.node_status || '—'}</td>
            <td>${l.operator || '—'}</td><td>${l.note || ''}</td>
          </tr>`).join('') || '<tr><td colspan="7" class="muted">暂无台账</td></tr>'}
        </tbody>
      </table>
    </section>`;

  const ids = () => ({
    tag_id: Number(document.getElementById('tag_sel').value),
    node_id: Number(document.getElementById('node_sel').value),
  });
  const run = async (path, okMsg) => {
    try {
      const body = ids();
      if (!body.tag_id || !body.node_id) { setMsg('请选择标签与节点', true); return; }
      const r = await api(path, { method: 'POST', body: JSON.stringify(body) });
      setMsg(r.message || okMsg);
      await renderTagBind(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  document.getElementById('do_bind').onclick = () => run('/api/tag_bind/bind', '真绑已落台');
  document.getElementById('do_virt').onclick = () => run('/api/tag_bind/virtual', '虚绑已入历史轨');
}
