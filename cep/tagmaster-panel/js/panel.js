(function () {
  var dot = null;
  var statusEl = null;
  var detailEl = null;

  function setState(kind, status, detail) {
    dot.className = 'dot' + (kind ? ' ' + kind : '');
    statusEl.textContent = status || '';
    detailEl.textContent = detail || '';
  }

  function parseResult(raw) {
    try { return JSON.parse(raw); }
    catch (e) { return { ok: false, msg: raw || '没有收到脚本返回。' }; }
  }

  function evalScript(script, callback) {
    if (!window.__adobe_cep__ || !window.__adobe_cep__.evalScript) {
      callback('{"ok":false,"msg":"CEP evalScript 不可用。"}');
      return;
    }
    window.__adobe_cep__.evalScript(script, callback);
  }

  function poll() {
    var script = '(typeof TM_poll === "function") ? TM_poll() : "{\\"ok\\":false,\\"msg\\":\\"桥脚本未加载。\\"}"';
    evalScript(script, function (raw) {
      var r = parseResult(raw);
      if (r.ok && (r.msg === 'idle' || r.msg === 'empty' || r.msg === 'duplicate' || r.msg === 'stale')) {
        setState('idle', '监听中', '等待 TagMaster 发送片段。');
        return;
      }
      if (r.ok) setState('', '已导入', r.msg || '导入完成。');
      else setState('err', '桥出错', r.msg || '未知错误。');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    dot = document.getElementById('dot');
    statusEl = document.getElementById('status');
    detailEl = document.getElementById('detail');
    poll();
    window.setInterval(poll, 1000);
  });
}());
