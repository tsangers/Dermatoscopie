const MODULES = {
  mel_vs_nevus: { title: 'Melanoom vs Naevi' },
  bcc_vs_sh: { title: 'BCC vs Talgklierhyperplasie' },
  ak_vs_bowen: { title: 'AK vs Bowen' }
};

const LABEL_TEXT = {
  melanoma: 'Melanoom',
  nevus: 'Naevus',
  bcc: 'Basaalcelcarcinoom (BCC)',
  sebaceous_hyperplasia: 'Talgklierhyperplasie',
  actinic_keratosis: 'Actinische keratose (AK)',
  bowen: 'Bowen (SCC in situ)'
};

const els = {
  moduleSelect: document.getElementById('moduleSelect'),
  modules: document.getElementById('modules'),
  quizView: document.getElementById('quizView'),
  summaryView: document.getElementById('summaryView'),
  quizTitle: document.getElementById('quizTitle'),
  quizProgress: document.getElementById('quizProgress'),
  scoreNow: document.getElementById('scoreNow'),
  lesionImage: document.getElementById('lesionImage'),
  answers: document.getElementById('answers'),
  feedback: document.getElementById('feedback'),
  nextBtn: document.getElementById('nextBtn'),
  stopBtn: document.getElementById('stopBtn'),
  summaryText: document.getElementById('summaryText'),
  motivation: document.getElementById('motivation'),
  retryBtn: document.getElementById('retryBtn'),
  backBtn: document.getElementById('backBtn')
};

let db = null;
let session = null;

function labelsForQuestions(questions) {
  return [...new Set(questions.map(q => q.diagnosis))];
}

function startModuleSet(moduleKey, setIndex) {
  const moduleSets = db.modules[moduleKey] || [];
  const questions = moduleSets[setIndex] || [];
  if (!questions.length) {
    alert('Deze quizset is nog niet beschikbaar.');
    return;
  }

  session = {
    moduleKey,
    setIndex,
    title: `${MODULES[moduleKey].title} · Set ${setIndex + 1}`,
    labels: labelsForQuestions(questions),
    questions,
    i: 0,
    score: 0,
    answered: false
  };

  els.moduleSelect.hidden = true;
  els.summaryView.hidden = true;
  els.quizView.hidden = false;
  renderQuestion();
}

function renderQuestion() {
  const q = session.questions[session.i];
  session.answered = false;
  els.nextBtn.disabled = true;
  els.feedback.textContent = '';

  els.quizTitle.textContent = session.title;
  els.quizProgress.textContent = `Vraag ${session.i + 1}/${session.questions.length}`;
  els.scoreNow.textContent = String(session.score);

  els.lesionImage.src = q.imageUrl;
  els.lesionImage.alt = `ISIC ${q.id}`;

  els.answers.innerHTML = '';
  session.labels.forEach(label => {
    const btn = document.createElement('button');
    btn.className = 'answer';
    btn.textContent = LABEL_TEXT[label] || label;
    btn.onclick = () => answer(label, btn);
    els.answers.appendChild(btn);
  });
}

function answer(selected, btn) {
  if (session.answered) return;
  session.answered = true;
  const q = session.questions[session.i];
  const correct = q.diagnosis;

  if (selected === correct) {
    session.score += 1;
    btn.classList.add('correct');
    els.feedback.textContent = '✅ Correct';
  } else {
    btn.classList.add('wrong');
    [...els.answers.querySelectorAll('.answer')].forEach(b => {
      if (b.textContent === (LABEL_TEXT[correct] || correct)) b.classList.add('correct');
    });
    els.feedback.textContent = `❌ Niet correct. Juiste diagnose: ${LABEL_TEXT[correct] || correct}`;
  }

  els.scoreNow.textContent = String(session.score);
  els.nextBtn.disabled = false;
}

function nextQuestion() {
  session.i += 1;
  if (session.i >= session.questions.length) return finish();
  renderQuestion();
}

function finish() {
  const total = session.questions.length;
  const pct = Math.round((session.score / total) * 100);

  els.quizView.hidden = true;
  els.summaryView.hidden = false;
  els.summaryText.textContent = `Score: ${session.score}/${total} (${pct}%).`;

  if (pct >= 85) els.motivation.textContent = 'Sterk werk. Je patroonherkenning is scherp.';
  else if (pct >= 65) els.motivation.textContent = 'Goed bezig. Met nog een set word je direct consistenter.';
  else els.motivation.textContent = 'Mooie leerronde. Herhaal deze set of kies een andere set.';
}

function backToModules() {
  els.quizView.hidden = true;
  els.summaryView.hidden = true;
  els.moduleSelect.hidden = false;
}

function renderModules() {
  els.modules.innerHTML = '';

  Object.entries(MODULES).forEach(([key, m]) => {
    const sets = db.modules[key] || [];
    const counts = (db.meta?.counts) || {};

    const card = document.createElement('div');
    card.className = 'module-btn';

    const previewSrc = sets?.[0]?.[0]?.imageUrl;
    if (previewSrc) {
      const img = document.createElement('img');
      img.className = 'module-preview';
      img.src = previewSrc;
      img.alt = `${m.title} preview`;
      card.appendChild(img);
    }

    const title = document.createElement('strong');
    title.textContent = m.title;
    card.appendChild(title);

    const meta = document.createElement('span');
    meta.textContent = `${sets.length} vaste sets beschikbaar`;
    card.appendChild(meta);

    const labels = [...new Set(sets.flat().map(x => x.diagnosis))];
    if (labels.length) {
      const labelMeta = document.createElement('span');
      labelMeta.style.display = 'block';
      labelMeta.style.marginTop = '4px';
      labelMeta.textContent = labels.map(l => `${LABEL_TEXT[l]}: ${counts[l] || 0}`).join(' · ');
      card.appendChild(labelMeta);
    }

    const setBar = document.createElement('div');
    setBar.className = 'set-row';

    if (!sets.length) {
      const disabledBtn = document.createElement('button');
      disabledBtn.className = 'ghost';
      disabledBtn.disabled = true;
      disabledBtn.textContent = 'Nog geen set';
      setBar.appendChild(disabledBtn);
    } else {
      sets.forEach((_, i) => {
        const b = document.createElement('button');
        b.className = 'ghost';
        b.textContent = `Set ${i + 1}`;
        b.onclick = () => startModuleSet(key, i);
        setBar.appendChild(b);
      });
    }

    card.appendChild(setBar);
    els.modules.appendChild(card);
  });
}

async function boot() {
  const res = await fetch('./data/isic_quiz_sets.json');
  db = await res.json();
  renderModules();
}

els.nextBtn.onclick = nextQuestion;
els.stopBtn.onclick = backToModules;
els.retryBtn.onclick = () => startModuleSet(session.moduleKey, session.setIndex);
els.backBtn.onclick = backToModules;

boot();