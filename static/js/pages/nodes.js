import { api } from '../api.js';

function bindTag(n) {
  if (!n.bound) {
    return '<span class="tag unbound">未绑定</span>';
  }
  const cls = n.virtual_bound ? 'virtual-bind' : 'active-bind';
  const drift = n.binding_drift ? ' <span class="tag drift">指纹/状态漂移</span>' : '';
  return `<span class="tag ${cls}">${n.binding_source} · ${n.tag_code}</span>${drift}`;
}

export async function renderNodes(view, setMsg) {
  const [j, tagsJ] = await Promise.all([api('/api/nodes'), api('/api/tag_bind/tags')]);
  const tagOpts = (tagsJ.items || [])
    .map((t) => `<option value="${t.id}">${t.tag_code} · ${t.tag_name}</option>`).join('');
  view.innerHTML = `
    <section class="card">
      <h2>节点目录</h2>
      <div class="row">
        <label>编号<input id="code" value="N-NEW-1" /></label>
        <label>表名<input id="table" value="dwd_sample" /></label>
        <label>层级<select id="layer"><option>ODS</option><option selected>DWD</option><option>DWS</option><option>ADS</option></select></label>
        <label>责任组<input id="team" value="仓建组" /></label>
        <button class="primary" id="create" type="button">登记节点</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(j.items || []).map((n) => `<article class="card">
        <h3>${n.node_code}</h3>
        <span class="tag">${n.status}</span> ${bindTag(n)}
        <span class="tag">${n.layer}</span>
        <p>${n.table_name}</p>
        <p class="muted">上游 ${n.upstream_count} · 下游 ${n.downstream_count} · ${n.owner_team}</p>
        ${n.bound ? `<p class="muted">凭证指纹 ${n.binding_fingerprint} · 绑定快照 ${n.node_status_snapshot}</p>` : ''}
        <div class="row">
          <button data-act="${n.id}" type="button">启用</button>
          <button data-ret="${n.id}" type="button">停用</button>
          ${!n.bound && n.status !== '停用' ? `<button data-bind="${n.id}" class="primary" type="button">真绑</button>` : ''}
          ${!n.bound ? `<button data-virt="${n.id}" type="button">虚绑(只读历史)</button>` : ''}
          ${n.bound ? `<button data-release="${n.id}" type="button">解绑</button>` : ''}
        </div>
        ${!n.bound ? `<div class="row" style="margin-top:.4rem"><select data-tag="${n.id}">${tagOpts}</select></div>` : ''}
      </article>`).join('') || '<p class="muted">暂无节点</p>'}
    </section>`;

  const pickTag = (nid) => Number(view.querySelector(`select[data-tag="${nid}"]`).value);
  const refresh = () => renderNodes(view, setMsg);

  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/nodes', {
        method: 'POST',
        body: JSON.stringify({
          node_code: document.getElementById('code').value,
          table_name: document.getElementById('table').value,
          layer: document.getElementById('layer').value,
          owner_team: document.getElementById('team').value,
        }),
      });
      setMsg('节点已登记');
      await refresh();
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-act]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/nodes/' + btn.dataset.act + '/activate', { method: 'POST', body: '{}' });
        setMsg('已启用');
        await refresh();
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-ret]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/nodes/' + btn.dataset.ret + '/retire', { method: 'POST', body: '{}' });
        setMsg(r.item.demoted_to_virtual ? '已停用，真绑整笔转只读虚绑' : '已停用');
        await refresh();
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-bind]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const nid = Number(btn.dataset.bind);
        const r = await api('/api/tag_bind/bind', {
          method: 'POST',
          body: JSON.stringify({ tag_id: pickTag(nid), node_id: nid, note: '页面真绑' }),
        });
        setMsg(r.message + ' #' + r.item.id);
        await refresh();
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-virt]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const nid = Number(btn.dataset.virt);
        const r = await api('/api/tag_bind/virtual', {
          method: 'POST',
          body: JSON.stringify({ tag_id: pickTag(nid), node_id: nid, note: '页面虚绑' }),
        });
        setMsg(r.message + ' #' + r.item.id);
        await refresh();
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-release]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/tag_bind/release', {
          method: 'POST',
          body: JSON.stringify({ node_id: Number(btn.dataset.release) }),
        });
        setMsg('已解绑，落点行保留历史');
        await refresh();
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
