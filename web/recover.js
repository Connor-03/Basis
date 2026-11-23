// The recovery page: lists the Customize chat's recent changes and undoes one. Kept separate from app.js on purpose.
(() => {
  const out = document.getElementById('out');
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; };
  const post = (body) => fetch('/api/agent', { method: 'POST', headers: { 'X-Requested-With': 'finance', 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json());
  const waitBack = async () => {
    out.replaceChildren(el('p', 'Restarting. This takes up to a minute.'));
    await new Promise((r) => setTimeout(r, 4000));
    for (let i = 0; i < 100; i++) {
      try { const r = await fetch('/api/status', { headers: { 'X-Requested-With': 'finance' } }); if (r.ok) { load(); return; } } catch (e) { /* still down */ }
      await new Promise((r) => setTimeout(r, 2000));
    }
    out.replaceChildren(el('p', 'The server did not come back. On the machine, run: python3 -m fin.agent undo <id> in the program folder.'));
  };
  async function load() {
    let st;
    try { st = await (await fetch('/api/agent/changes', { headers: { 'X-Requested-With': 'finance' } })).json(); } catch (e) { out.replaceChildren(el('p', 'Could not reach the server: ' + e.message)); return; }
    const ch = (st.changes || []).slice(0, 8);
    out.replaceChildren();
    if (!ch.length) { out.append(el('p', 'No changes to undo.')); return; }
    for (const m of ch) {
      const row = el('div', '', 'row');
      const left = el('div', '', 'l');
      left.append(el('div', new Date(m.at * 1000).toLocaleString() + (m.chat ? ' · ' + m.chat : '')), el('div', m.changed.map((c) => c.file).join(', '), 'faint'));
      const btn = el('button', 'Undo this and everything after it', 'btn');
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        const r = await post({ action: 'undo', id: m.snap });
        if (r.error) { out.prepend(el('p', r.error, 'down')); return; }
        if (r.restart) { await post({ action: 'apply', id: m.snap }); waitBack(); } else load();
      });
      row.append(left, btn);
      out.append(row);
    }
  }
  load();
})();
