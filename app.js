/* shared helpers: theme switcher, copy, toast, message merge, "/" to search */
(function () {
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('wh-theme', t); } catch (e) {}
    document.querySelectorAll('[data-settheme]').forEach(function (b) {
      b.classList.toggle('on', b.dataset.settheme === t);
    });
  }
  document.querySelectorAll('[data-settheme]').forEach(function (b) {
    b.onclick = function () { setTheme(b.dataset.settheme); };
  });
  setTheme(document.documentElement.dataset.theme || 'violet');

  window.whToast = function (msg) {
    var t = document.createElement('div');
    t.className = 'toast'; t.textContent = msg; document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 1600);
  };
  window.whCopy = function (text) {
    if (!text) { whToast('Nothing to copy'); return; }
    var done = function () { whToast('Copied'); };
    if (navigator.clipboard) { navigator.clipboard.writeText(text).then(done, function () { whToast('Copy failed'); }); }
    else { var a = document.createElement('textarea'); a.value = text; document.body.appendChild(a); a.select(); document.execCommand('copy'); a.remove(); done(); }
  };
  window.whFill = function (body, data) {
    return (body || '').replace(/\{(\w+)\}/g, function (m, k) { return data[k] !== undefined && data[k] !== '' ? data[k] : m; });
  };
  window.whPhone = function (p) {
    var d = (p || '').replace(/\D/g, '');
    if (d.length === 10) return '+1' + d;
    if (d.length === 11 && d[0] === '1') return '+' + d;
    return p || '';
  };
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) {
      var s = document.querySelector('input[name=q]');
      if (s) { e.preventDefault(); s.focus(); }
    }
  });
})();
