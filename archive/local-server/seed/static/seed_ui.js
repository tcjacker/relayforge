if (location.pathname === '/action') {
    const editor = document.querySelector('[data-material-editor]');
    history.replaceState(null, '', editor
        ? '/materials?area=' + encodeURIComponent(editor.querySelector('[name="area"]').value)
        : '/#stage-dialog');
}
let dirty = false;
document.addEventListener('input', () => { dirty = true; });

const empty = document.getElementById('task-empty');
const filters = {
    all: ['这里还没有任务', '创建阶段目标，或直接提交一项独立需求。'],
    active: ['当前没有正在执行的任务', 'Agent 领取任务后，进展会显示在这里。'],
    attention: ['当前无需确认', '需要你裁定或验收的任务会显示在这里。'],
    done: ['还没有启用的交付', '通过验收并启用的任务会显示在这里。'],
    queued: ['当前没有排队任务', '已授权、等待开工的任务会显示在这里。']
};
function filterTasks(name) {
    if (!empty) return;
    if (!filters[name]) name = 'all';
    let visible = 0;
    document.querySelectorAll('.task-row').forEach(row => {
        row.hidden = !row.dataset.groups.split(' ').includes(name);
        if (!row.hidden) visible++;
    });
    document.querySelectorAll('[role=tab]').forEach(tab => {
        tab.setAttribute('aria-selected', String(tab.dataset.filter === name));
    });
    empty.hidden = visible > 0;
    empty.querySelector('strong').textContent = filters[name][0];
    empty.querySelector('p').textContent = filters[name][1];
    sessionStorage.setItem('task-filter', name);
}
function openDialog(id) {
    const target = document.getElementById(id);
    if (!target || target.tagName !== 'DIALOG') return;
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    target.showModal();
    history.replaceState(null, '', '#' + id);
}
document.addEventListener('click', event => {
    const opener = event.target.closest('[data-open]');
    if (opener) openDialog(opener.dataset.open);
    const close = event.target.closest('[data-close]');
    if (close) close.closest('dialog').close();
    const filter = event.target.closest('[data-filter], [data-nav-filter]');
    if (filter) {
        filterTasks(filter.dataset.filter || filter.dataset.navFilter);
        document.getElementById('tasks').scrollIntoView({block: 'nearest'});
    }
    if (event.target.closest('[data-version]')) {
        document.querySelector('.version').open = true;
        document.querySelector('.version summary').focus();
    }
});
document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('close', () => {
        if (location.hash === '#' + dialog.id) history.replaceState(null, '', location.pathname);
    });
    dialog.addEventListener('click', event => {
        const box = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right ||
            event.clientY < box.top || event.clientY > box.bottom)) dialog.close();
    });
});
document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', () => sessionStorage.removeItem('task-filter'));
});
filterTasks(sessionStorage.getItem('task-filter') || 'all');
if (location.hash) openDialog(location.hash.slice(1));
setInterval(async () => {
    document.querySelectorAll('[data-elapsed-start]').forEach(clock => {
        clock.textContent = Math.max(0, Math.floor((Date.now() - Date.parse(clock.dataset.elapsedStart)) / 1000)) + ' 秒';
    });
    const independent = [...document.querySelectorAll('form')].find(form => form.querySelector('[name=action]')?.value === 'create');
    const pending = independent && [...independent.querySelectorAll('textarea')].some(x=>x.value);
    if (materialForm) {
        try {
            const response = await fetch('/api/revision', {cache: 'no-store'});
            if (response.ok && await response.json() !== document.body.dataset.revision) {
                document.getElementById('material-refresh').hidden = false;
            }
        } catch {}
    }
    try {
        const response = await fetch('/api/revision', {cache: 'no-store'});
        if (response.status === 401) location.assign('/login');
        else if (response.ok && await response.json() !== document.body.dataset.revision) {
            if (materialForm) document.getElementById('material-refresh').hidden = false;
            else if (!dirty && !pending && !document.querySelector('textarea:focus') &&
                !document.querySelector('dialog[open]') && !document.querySelector('details[open]')) location.reload();
            else if (!document.getElementById('live-update-notice')) {
                const notice = document.createElement('button');
                notice.id = 'live-update-notice'; notice.type = 'button';
                notice.textContent = '任务状态已更新，点击刷新（未提交输入会丢失）';
                notice.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:9999;padding:14px 20px;background:#5547d6;color:white;border:0;border-radius:10px;cursor:pointer';
                notice.addEventListener('click', () => location.reload());
                document.body.appendChild(notice);
            }
        }
    } catch { /* Preserve current inputs when the service is unavailable. */ }
}, 10000);

const materialForm = document.querySelector('[data-material-editor]');
if (materialForm) {
    const initial = new FormData(materialForm);
    function editorState() {
        const current = new FormData(materialForm);
        return [...initial.keys()].filter(k => k.startsWith('body_') || k.startsWith('section_') || k.startsWith('exists_'))
            .some(k => initial.get(k) !== current.get(k)) ||
            [...current.keys()].filter(k => k.startsWith('exists_')).some(k => initial.get(k) !== current.get(k));
    }
    function updateDirty() {
        const changed = editorState() || document.querySelector('.materials')?.dataset.unsaved === 'true';
        document.querySelectorAll('[data-publish]').forEach(button => { button.disabled = changed; });
        document.getElementById('material-dirty').hidden = !changed;
    }
    materialForm.addEventListener('input', event => {
        const name = event.target.name || '';
        const ident = name.startsWith('section_') ? 'context' : name.startsWith('body_') ? name.slice(5) : '';
        const exists = ident && materialForm.querySelector('[name="exists_' + ident + '"]');
        if (exists) exists.checked = true;
        updateDirty();
    });
    materialForm.addEventListener('change', updateDirty);
    updateDirty();
}
