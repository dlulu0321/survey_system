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
  next.type = 'button'; 
next.className = 'button'; 
next.textContent = '下一题';
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
  function show(index) {
  current = index;
  visited.add(index);

  panels.forEach((panel, i) => {
    const active = i === current;
    panel.hidden = !active;
    panel.querySelectorAll('input,textarea,select').forEach(control => {
      control.disabled = !active;
    });
  });

  previous.hidden = current === 0;
  progress.textContent = `第${current + 1} / ${panels.length} 题`;

  next.hidden = false;
  next.textContent = nextIndex(current) < 0 ? '保存回答' : '下一题';
  submit.hidden = true;
}
  previous.addEventListener('click', () => { if (current > 0) show(current - 1); });
  next.addEventListener('click', () => {
  const active = panels[current];
  const controls = Array.from(
    active.querySelectorAll('input,textarea,select')
  ).filter(x => !x.disabled);

  // 检查当前题目中的必答字段
  for (const control of controls) {
    if (control.required && !control.checkValidity()) {
      control.reportValidity();
      return;
    }
  }

  const target = nextIndex(current);

  // 已经到达最后一题，或跳转规则要求结束：直接提交
  if (target < 0) {
    panels.forEach((panel, i) => {
      panel.querySelectorAll('input,textarea,select').forEach(control => {
        control.disabled = !visited.has(i);
      });
    });

    form.requestSubmit();
    return;
  }

  // 还有下一题，继续答题
  show(target);
});
  show(0);
})();
