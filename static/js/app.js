(function () {
  const body = document.body;
  // ---- sidebar ----
  const KEY = 'vimal.sidebar';
  if (localStorage.getItem(KEY) === '1' && window.innerWidth >= 992) body.classList.add('sidebar-collapsed');
  document.querySelectorAll('[data-sidebar-toggle]').forEach(b => b.addEventListener('click', () => {
    if (window.innerWidth < 992) { body.classList.toggle('sidebar-open'); return; }
    body.classList.toggle('sidebar-collapsed');
    localStorage.setItem(KEY, body.classList.contains('sidebar-collapsed') ? '1' : '0');
  }));
  document.addEventListener('click', e => { if (body.classList.contains('sidebar-open') && !e.target.closest('.sidebar,[data-sidebar-toggle]')) body.classList.remove('sidebar-open'); });

  // ---- loading overlay ----
  const loading = document.getElementById('loading');
  window.showLoading = () => loading && loading.classList.add('show');
  window.hideLoading = () => loading && loading.classList.remove('show');
  window.addEventListener('pageshow', hideLoading);

  // ---- toasts ----
  window.showToast = function (msg, type) {
    const c = document.getElementById('toasts'); if (!c) return;
    const colors = {success: 'text-bg-success', error: 'text-bg-danger', danger: 'text-bg-danger', warning: 'text-bg-warning', info: 'text-bg-primary'};
    const el = document.createElement('div');
    el.className = 'toast align-items-center border-0 ' + (colors[type] || 'text-bg-primary');
    el.setAttribute('role', 'alert');
    el.innerHTML = '<div class="d-flex"><div class="toast-body"></div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
    el.querySelector('.toast-body').textContent = msg;
    c.appendChild(el);
    const t = new bootstrap.Toast(el, {delay: type === 'error' || type === 'danger' ? 8000 : 5000}); t.show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
  };
  document.querySelectorAll('[data-toast]').forEach(el => { showToast(el.dataset.toast, el.dataset.level); el.remove(); });

  // ---- confirmation dialogs: <form data-confirm="..."> or <button data-confirm="..." form=...> ----
  const modalEl = document.getElementById('confirmModal');
  let pending = null;
  function askConfirm(message, onOk, okLabel, danger) {
    modalEl.querySelector('.modal-body').textContent = message;
    const ok = modalEl.querySelector('[data-ok]');
    ok.textContent = okLabel || 'Confirm';
    ok.className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary');
    pending = onOk;
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }
  modalEl && modalEl.querySelector('[data-ok]').addEventListener('click', () => {
    bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    if (pending) { const f = pending; pending = null; f(); }
  });
  document.addEventListener('submit', e => {
    const form = e.target;
    if (form.dataset.confirm && !form.dataset.confirmed) {
      e.preventDefault();
      askConfirm(form.dataset.confirm, () => { form.dataset.confirmed = '1'; showLoading(); form.submit(); },
                 form.dataset.confirmLabel, form.dataset.confirmDanger === '1');
      return;
    }
    if (!e.defaultPrevented && !form.hasAttribute('data-noload')) showLoading();
  });
  window.askConfirm = askConfirm;

  // ---- fetch helper with CSRF ----
  window.csrfToken = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || document.querySelector('[name=csrfmiddlewaretoken]')?.value;
  window.postJSON = async function (url, data) {
    const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken(), 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
      body: JSON.stringify(data)});
    if (r.status === 401) { showToast('Session expired. Redirecting to login…', 'error'); setTimeout(() => location.reload(), 1200); throw new Error('401'); }
    if (r.status === 403) throw new Error('You do not have permission for this action.');
    return r.json();
  };
  // ---- populate-and-open modal helpers: <button data-bs-target="#payModal" data-action="/..." data-title="..."> ----
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-modal-action]'); if (!b) return;
    const m = document.querySelector(b.dataset.bsTarget); if (!m) return;
    const f = m.querySelector('form'); if (f) f.action = b.dataset.modalAction;
    m.querySelectorAll('[data-fill]').forEach(el => el.textContent = b.dataset[el.dataset.fill] || '');
    const amt = m.querySelector('[name=amount_paid]'); if (amt && b.dataset.balance) amt.value = b.dataset.balance;
  });
})();
