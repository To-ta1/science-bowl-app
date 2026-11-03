(() => {
  const $ = id => document.getElementById(id);
  async function api(path, options={}) {
    const response = await fetch(path, { ...options, credentials: 'same-origin' });
    const text = await response.text(); let data={};
    try { data=text?JSON.parse(text):{}; } catch { data={detail:text}; }
    if (response.status === 401) { location.href='/admin'; throw new Error('Please sign in again.'); }
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }
  function fmtMs(ms) {
    if (ms == null) return '—';
    return `${(Number(ms)/1000).toFixed(2)} s`;
  }
  function fmtDate(s) { return s ? new Date(s).toLocaleString() : '—'; }
  function addCell(row, value) { const td=document.createElement('td'); td.textContent=value ?? '—'; row.append(td); return td; }

  async function load() {
    $('admin-message').textContent='Loading…';
    try {
      const [summary, quizzes, attempts] = await Promise.all([
        api('/api/admin/summary'), api('/api/quizzes'), getAttempts()
      ]);
      renderSummary(summary);
      populateQuizFilter(quizzes.quizzes);
      renderAttempts(attempts.attempts);
      $('admin-message').textContent=`Updated ${new Date().toLocaleTimeString()}`;
    } catch (error) { $('admin-message').textContent=error.message; }
  }
  async function getAttempts() {
    const quiz = $('quiz-filter').value;
    const status = $('status-filter').value;
    const params = new URLSearchParams();
    if (quiz) params.set('quiz_slug', quiz);
    if (status) params.set('status', status);
    return api(`/api/admin/attempts?${params.toString()}`);
  }
  function renderSummary(s) {
    const items=[['Participants',s.participants],['Attempts',s.attempts],['Completed',s.completed],['Question records',s.answers]];
    $('summary').replaceChildren();
    items.forEach(([label,value])=>{const box=document.createElement('div');box.className='summary-box';const span=document.createElement('span');span.textContent=label;const strong=document.createElement('strong');strong.textContent=value;box.append(span,strong);$('summary').append(box);});
  }
  function populateQuizFilter(quizzes) {
    const select=$('quiz-filter'); const previous=select.value;
    select.replaceChildren(); const all=document.createElement('option'); all.value='';all.textContent='All quizzes';select.append(all);
    quizzes.forEach(q=>{const o=document.createElement('option');o.value=q.slug;o.textContent=q.title;select.append(o);});
    if ([...select.options].some(o=>o.value===previous)) select.value=previous;
  }
  function renderAttempts(attempts) {
    const body=$('attempt-rows'); body.replaceChildren();
    if (!attempts.length) { const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=6;td.textContent='No attempts yet.';tr.append(td);body.append(tr);return; }
    attempts.forEach(a=>{
      const tr=document.createElement('tr');
      addCell(tr,a.student_name);addCell(tr,a.quiz_title);
      const status=addCell(tr,'');const pill=document.createElement('span');pill.className='pill';pill.textContent=a.status==='completed'?'Completed':'In progress';status.append(pill);
      addCell(tr,a.status==='completed'?`${a.question_count} questions`:`Question ${a.current_question} of ${a.question_count}`);
      addCell(tr,fmtDate(a.created_at));
      const cell=addCell(tr,'');const button=document.createElement('button');button.className='link-button';button.textContent='View answers';button.addEventListener('click',()=>loadDetail(a.id));cell.append(button);
      body.append(tr);
    });
  }
  function appendText(container, tag, value, className) {
    const node=document.createElement(tag); if(className)node.className=className;node.textContent=value;container.append(node);return node;
  }
  async function loadDetail(id) {
    $('admin-message').textContent='Loading attempt…';
    try {
      const data=await api(`/api/admin/attempts/${encodeURIComponent(id)}`);
      const panel=$('detail');panel.classList.remove('hidden');panel.replaceChildren();
      appendText(panel,'p',`${data.attempt.student_name} — ${data.attempt.quiz_title}`,'eyebrow');
      appendText(panel,'h2',`Attempt details (${data.attempt.status})`);
      appendText(panel,'p',`Started ${fmtDate(data.attempt.created_at)}${data.attempt.completed_at?` · Completed ${fmtDate(data.attempt.completed_at)}`:''}`,'muted small');
      data.results.forEach(r=>{
        const row=document.createElement('div');row.className='detail-row';
        appendText(row,'strong',`Question ${r.question_number}: ${r.prompt}`);
        const outcome = r.status === 'no_buzz' ? 'No buzz — pre-buzz timer expired'
          : r.status === 'answer_timeout' ? 'Answer window expired'
          : r.status === 'answered' ? 'Answer submitted'
          : r.status === 'buzzed' ? 'Buzz recorded; answer still in progress'
          : r.status;
        appendText(row,'p',`${outcome}${r.status !== 'no_buzz' ? ` · Answer: ${r.answer_text || '(blank)'}` : ''}`,'answer-text');
        const meta=[];
        if(r.time_to_buzz_ms!=null)meta.push(`Time to buzz: ${fmtMs(r.time_to_buzz_ms)}`);
        if(r.answer_time_ms!=null)meta.push(`Answer time: ${fmtMs(r.answer_time_ms)}`);
        if(r.total_question_time_ms!=null)meta.push(`Total: ${fmtMs(r.total_question_time_ms)}`);
        if(r.audio_duration_ms!=null && r.audio_remaining_at_buzz_ms!=null) {
          if(r.buzz_before_audio_end) meta.push(`${fmtMs(r.audio_remaining_at_buzz_ms)} before audio ended`);
          else meta.push('Buzz after audio ended');
          if(r.buzz_within_500ms_before_audio_end) meta.push('Within 0.5 s of audio end');
        }
        if(r.audio_stop_delay_ms!=null)meta.push(`Audio stop delay: ${r.audio_stop_delay_ms} ms`);
        appendText(row,'div',meta.join(' · ') || 'No timing data','detail-meta');
        panel.append(row);
      });
      $('admin-message').textContent='';
      panel.scrollIntoView({behavior:'smooth',block:'start'});
    } catch(error){$('admin-message').textContent=error.message;}
  }
  $('refresh').addEventListener('click',load);
  $('quiz-filter').addEventListener('change',async()=>{try{const data=await getAttempts();renderAttempts(data.attempts);}catch(e){$('admin-message').textContent=e.message;}});
  $('status-filter').addEventListener('change',async()=>{try{const data=await getAttempts();renderAttempts(data.attempts);}catch(e){$('admin-message').textContent=e.message;}});
  $('logout').addEventListener('click',async()=>{await api('/api/admin/logout',{method:'POST'});location.href='/admin';});
  $('backup-now').addEventListener('click',async()=>{const button=$('backup-now');button.disabled=true;button.textContent='Backing up…';try{const r=await api('/api/admin/backup-now',{method:'POST'});$('admin-message').textContent=r.message;}catch(e){$('admin-message').textContent=e.message;}finally{button.disabled=false;button.textContent='Back up now';}});
  api('/api/admin/me').then(load).catch(()=>{location.href='/admin';});
})();
