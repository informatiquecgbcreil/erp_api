(function(){
  const viewport = document.getElementById('toast-viewport');
  const srStatus = document.getElementById('sr-status');
  const srAlert = document.getElementById('sr-alert');

  function iconFor(tone){
    return ({success:'✓', warning:'!', danger:'×', info:'i'})[tone] || 'i';
  }
  function announce(message, tone){
    const region = tone === 'danger' ? srAlert : srStatus;
    if(!region) return;
    region.textContent='';
    setTimeout(()=>{ region.textContent = message; }, 40);
  }
  function toast(message, opts={}){
    if(!viewport || !message) return;
    const tone = opts.tone || opts.type || 'info';
    const timeout = typeof opts.timeout === 'number' ? opts.timeout : 4200;
    const el = document.createElement('div');
    el.className = `toast toast--${tone}`;
    el.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    el.innerHTML = `<div class="toast__icon" aria-hidden="true">${iconFor(tone)}</div><div class="toast__message"></div><button type="button" class="toast__close" aria-label="Fermer">×</button>`;
    el.querySelector('.toast__message').textContent = message;
    const remove = ()=>{
      el.classList.remove('show');
      setTimeout(()=>el.remove(), 180);
    };
    el.querySelector('.toast__close').addEventListener('click', remove);
    viewport.prepend(el);
    requestAnimationFrame(()=>el.classList.add('show'));
    announce(message, tone);
    if(timeout > 0) setTimeout(remove, timeout);
  }
  window.uiToast = toast;

  document.querySelectorAll('[data-flash]').forEach((flash, idx)=>{
    const tone = flash.dataset.flashTone || 'info';
    const icon = flash.querySelector('.flash__icon');
    if(icon && !icon.textContent.trim()) icon.textContent = iconFor(tone);
    const close = flash.querySelector('[data-flash-close]');
    if(close) close.addEventListener('click', ()=> flash.remove());
    if(idx === 0){
      const msg = flash.querySelector('.flash__body')?.textContent?.trim();
      if(msg && msg.length <= 180) toast(msg, {tone, timeout: tone === 'danger' ? 5200 : 3200});
    }
  });

  document.addEventListener('submit', (e)=>{
    const form = e.target;
    if(!(form instanceof HTMLFormElement)) return;

    // Bouton réellement cliqué (SubmitEvent.submitter, null si soumission
    // programmatique). On préserve son name/value : le désactiver ci-dessous
    // l'exclurait de l'envoi, ce qui perdrait l'action portée par le bouton
    // (symptôme : HTTP 400 côté serveur).
    const clicked = (e.submitter && e.submitter.tagName) ? e.submitter : null;
    form.querySelectorAll('input[data-submit-keeper="1"]').forEach(n => n.remove());
    if(clicked && clicked.name){
      const keeper = document.createElement('input');
      keeper.type = 'hidden';
      keeper.name = clicked.name;
      keeper.value = clicked.value;
      keeper.dataset.submitKeeper = '1';
      form.appendChild(keeper);
    }

    // Décor « en cours » sur le bouton cliqué (sinon le premier bouton de soumission).
    const submit = clicked || form.querySelector('[type="submit"]');
    if(!submit || submit.dataset.pendingApplied === '1') return;
    submit.dataset.pendingApplied = '1';
    submit.dataset.originalText = submit.innerHTML;
    submit.classList.add('is-pending');
    submit.disabled = true;
    const pendingText = submit.getAttribute('data-pending-text') || 'Enregistrement…';
    submit.innerHTML = pendingText;
    form.setAttribute('aria-busy', 'true');
  }, true);

  function buildErrorSummary(form){
    const invalids = Array.from(form.querySelectorAll('[aria-invalid="true"], .is-invalid, .field-error'))
      .filter(el => el.offsetParent !== null);
    const existing = form.querySelector('.error-summary[data-generated="1"]');
    if(existing) existing.remove();
    if(!invalids.length) return;
    const box = document.createElement('div');
    box.className = 'error-summary';
    box.dataset.generated = '1';
    const ul = document.createElement('ul');
    invalids.forEach((input, i)=>{
      if(!input.id) input.id = `invalid-${Date.now()}-${i}`;
      const li = document.createElement('li');
      const a = document.createElement('a');
      const label = document.querySelector(`label[for="${CSS.escape(input.id)}"]`)?.textContent?.trim() || input.name || 'Champ à corriger';
      a.href = `#${input.id}`;
      a.textContent = label;
      a.addEventListener('click', (ev)=>{
        ev.preventDefault();
        input.focus({preventScroll:false});
        input.scrollIntoView({behavior:'smooth', block:'center'});
      });
      li.appendChild(a);
      ul.appendChild(li);
    });
    box.innerHTML = '<h3>Des champs demandent une correction</h3>';
    box.appendChild(ul);
    form.prepend(box);
  }
  document.querySelectorAll('form').forEach((form)=>{
    buildErrorSummary(form);
    form.addEventListener('input', ()=> buildErrorSummary(form));
    form.addEventListener('change', ()=> buildErrorSummary(form));
  });

  const normalize = (s)=> (s || '').toString().trim().toLowerCase();
  document.querySelectorAll('[data-auto-status], .js-auto-status').forEach((el)=>{
    const v = normalize(el.getAttribute('data-auto-status') || el.textContent);
    if(v) el.setAttribute('data-status', v);
  });

  async function copyText(text){
    await navigator.clipboard.writeText(text);
  }
  function markCopied(el){
    if(!el) return;
    el.classList.add('is-copied');
    setTimeout(()=> el.classList.remove('is-copied'), 1300);
  }
  document.querySelectorAll('[data-copy-text], [data-copy-target]').forEach((el)=>{
    el.classList.add('copy-feedback');
    el.addEventListener('click', async ()=>{
      const targetSel = el.getAttribute('data-copy-target');
      const target = targetSel ? document.querySelector(targetSel) : null;
      const text = el.getAttribute('data-copy-text') || target?.textContent?.trim() || '';
      if(!text) return;
      try{
        await copyText(text);
        markCopied(el);
        toast('Copié dans le presse-papiers', {tone:'success', timeout:2200});
      }catch(_e){
        toast('Impossible de copier', {tone:'danger', timeout:2800});
      }
    });
  });

  window.uiHighlight = function(selectorOrEl){
    const el = typeof selectorOrEl === 'string' ? document.querySelector(selectorOrEl) : selectorOrEl;
    if(!el) return;
    el.classList.remove('is-updated');
    requestAnimationFrame(()=> el.classList.add('is-updated'));
  };

  const firstInvalid = document.querySelector('form [aria-invalid="true"], form .is-invalid, form .field-error');
  if(firstInvalid && typeof firstInvalid.focus === 'function'){
    setTimeout(()=>{
      try{ firstInvalid.focus({preventScroll:true}); firstInvalid.scrollIntoView({behavior:'smooth', block:'center'}); }catch(_e){}
    }, 120);
  }

  document.querySelectorAll('a[href^="#"]').forEach((a)=>{
    a.addEventListener('click', (ev)=>{
      const id = a.getAttribute('href');
      if(!id || id === '#') return;
      const target = document.querySelector(id);
      if(!target) return;
      ev.preventDefault();
      target.scrollIntoView({behavior:'smooth', block:'center'});
      if(typeof target.focus === 'function') target.focus({preventScroll:true});
    });
  });

  const params = new URLSearchParams(window.location.search);
  const highlightSelector = params.get('highlight');
  const hashTarget = window.location.hash && window.location.hash !== '#' ? document.querySelector(window.location.hash) : null;
  if(highlightSelector){
    const el = document.querySelector(highlightSelector.startsWith('#') || highlightSelector.startsWith('.') ? highlightSelector : `[data-highlight="${highlightSelector}"]`);
    if(el) window.uiHighlight(el);
  } else if(hashTarget){
    window.uiHighlight(hashTarget.closest('tr, .card, .section-card, .form-card, .recent-item') || hashTarget);
  }
})();
