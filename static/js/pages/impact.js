import { api } from '../api.js';

function gateTag(j) {
  if (j.status === '已拦停') {
    return `<span class="tag blocked">拦停·虚绑 ${j.virtual_blocked}</span>`;
  }
  if (j.virtual_blocked > 0) {
    return `<span class="tag blocked">虚绑 ${j.virtual_blocked}</span>`;
  }
  return '<span class="tag active-bind">可通·真绑</span>';
}

export async function renderImpact(view, setMsg) {
  const nodes = await api('/api/nodes');
  const jobs = await api('/api/impact/jobs');
  const opts = (nodes.items || []).map((n) => `<option value="${n.id}">${n.node_code}</option>`).join('');
  view.innerHTML = `
    <section class="card">
      <h2>影响分析</h2>
      <p class="muted">根节点为虚绑/停用/指纹漂移时，解析管道直接停（不建作业）；下游虚绑节点只阻断不展开。</p>
      <div class="row">
        <label>根节点<select id="root">${opts}</select></label>
        <button class="primary" id="go" type="button">启动影响分析</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(jobs.items || []).map((j) => `<article class="card">
        <h3>${j.job_code}</h3>
        <span class="tag">${j.status}</span> ${gateTag(j)}
        <p>根 ${j.node_code} · 有效命中 ${j.affected_count}</p>
        <p class="muted">${j.message}</p>
        ${(j.blocked_nodes_list || []).length ? `<div class="muted">拦停节点：${
          j.blocked_nodes_list.map((b) => `${b.node_code}（${b.source}）`).join('、')
        }</div>` : ''}
        <button data-d="${j.id}" type="button">详情</button>
        <pre class="muted" id="j${j.id}"></pre>
      </article>`).join('') || '<p class="muted">尚无影响作业</p>'}
    </section>`;
  document.getElementById('go').onclick = async () => {
    try {
      const r = await api('/api/impact/start', {
        method: 'POST',
        body: JSON.stringify({ root_node_id: Number(document.getElementById('root').value) }),
      });
      setMsg('影响作业 ' + r.job.job_code + ' · ' + r.job.status);
      await renderImpact(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-d]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/impact/jobs/' + btn.dataset.d);
        document.getElementById('j' + btn.dataset.d).textContent = JSON.stringify(r.hits, null, 2);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
