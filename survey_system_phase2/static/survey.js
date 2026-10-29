/* Client-side one-question-at-a-time navigation driven by stored jump_rules. */
(() => {
  const form = document.getElementById('survey-form');
  if (!form) return;
  const panels = Array.from(form.querySelectorAll('.question'));
  if (!panels.length) return;
  const byId = new Map(panels.map((p, i) => [p.dataset.qid, i]));
  let current = 0;
  const visited = new Set();
  const nav = document.createElement('div');
  nav.className = 'wizard-nav';
  const previous = document.createElement('button');
  previous.type = 'button'; previous.className = 'button secondary'; previous.textContent = '上一题';
  const next = document.createElement('button');
  next.type = 'button'; next.className = 'button'; next.textContent = '下一题';
  const progress = document.createElement('span'); progress.className = 'hint';
  nav.append(previous, progress, next);
  const submit = form.querySelector('button[type="submit"]');
  submit.hidden = true;
  form.insertBefore(nav, submit);

  function valueFor(panel) {
    const controls = Array.from(panel.querySelectorAll('input,textarea,select')).filter(x => !x.disabled);
    if (!controls.length) return null;
    if (controls[0].type === 'radio') return controls.find(x => x.checked)?.value ?? null;
    if (controls[0].type === 'checkbox') return controls.filter(x => x.checked).map(x => x.value);
    return controls[0].value;
  }
  function matches(answer, rule) {
    const expected = rule.value;
    switch (rule.operator) {
      case 'eq': return Array.isArray(answer) ? answer.includes(expected) : String(answer) === String(expected);
      case 'contains': return Array.isArray(answer) ? answer.includes(expected) : String(answer ?? '').includes(String(expected));
      case 'gt': return answer !== null && answer !== '' && Number(answer) > Number(expected);
      case 'gte': return answer !== null && answer !== '' && Number(answer) >= Number(expected);
      case 'lt': return answer !== null && answer !== '' && Number(answer) < Number(expected);
      case 'lte': return answer !== null && answer !== '' && Number(answer) <= Number(expected);
      default: return false;
    }
  }
  function nextIndex(index) {
    const panel = panels[index];
    let rules = [];
    try { rules = JSON.parse(panel.dataset.jumpRules || '[]'); } catch (_) {}
    const answer = valueFor(panel);
    for (const rule of rules) {
      if (matches(answer, rule)) {
        if (rule.target_question_id == null) return -1;
        return byId.has(rule.target_question_id) ? byId.get(rule.target_question_id) : -1;
      }
    }
    return index + 1 < panels.length ? index + 1 : -1;
  }
  function activePathIndices() {
    const path = new Set();
    let index = 0;
    while (index >= 0 && index < panels.length && !path.has(index)) {
      path.add(index);
      const target = nextIndex(index);
      index = target < 0 ? -1 : target;
    }
    return path;
  }
  function show(index) {
    current = index;
    visited.add(index);
    panels.forEach((panel, i) => {
      const active = i === current;
      panel.hidden = !active;
      panel.querySelectorAll('input,textarea,select').forEach(control => { control.disabled = !active; });
    });
    previous.hidden = current === 0;
    progress.textContent = `第 ${current + 1} / ${panels.length} 题`;
    next.hidden = false;
    submit.hidden = true;
    updateNextLabel();
  }
  function updateNextLabel() {
    next.textContent = nextIndex(current) < 0 ? '保存回答' : '下一题';
  }
  previous.addEventListener('click', () => { if (current > 0) show(current - 1); });
  form.addEventListener('input', updateNextLabel);
  form.addEventListener('change', updateNextLabel);
  next.addEventListener('click', () => {
    const active = panels[current];
    const controls = Array.from(active.querySelectorAll('input,textarea,select')).filter(x => !x.disabled);
    // Multiple-choice min/max rules are checked on submit by the server; required fields are checked here.
    for (const control of controls) {
      if (control.required && !control.checkValidity()) { control.reportValidity(); return; }
    }
    const target = nextIndex(current);
    if (target < 0) {
      // The current button is the final '保存回答' action: submit immediately.
      const activePath = activePathIndices();
      panels.forEach((panel, i) => panel.querySelectorAll('input,textarea,select').forEach(control => { control.disabled = !activePath.has(i); }));
      nav.hidden = true;
      submit.hidden = false;
      form.requestSubmit();
      return;
    }
    show(target);
  });
  show(0);
})();
