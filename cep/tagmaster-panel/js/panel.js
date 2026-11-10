(function () {
  var dot = null, statusEl = null, detailEl = null;
  var dirEl = null, listEl = null, qEl = null, sendEl = null;
  var files = [];          // [{name, path}]
  var picked = {};         // path -> {name, path}

  function $(id) { return document.getElementById(id); }

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

  // ---- 列表 ----
  function filtered() {
    var q = qEl.value.trim().toLowerCase();
    if (!q) return files;
    return files.filter(function (f) { return f.name.toLowerCase().indexOf(q) >= 0; });
  }

  function renderList() {
    var list = filtered();
    listEl.innerHTML = '';
    if (!files.length) {
      listEl.innerHTML = '<div style="color:#5f6b7d;padding:18px;text-align:center">先在上方选一个素材文件夹</div>';
      return;
    }
    if (!list.length) {
      listEl.innerHTML = '<div style="color:#5f6b7d;padding:18px;text-align:center">没有匹配的文件</div>';
      return;
    }
    var frag = document.createDocumentFragment();
    list.forEach(function (f) {
      var row = document.createElement('label');
      row.className = 'item';
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = !!picked[f.path];
      cb.onchange = function () {
        if (cb.checked) picked[f.path] = f; else delete picked[f.path];
        updateCount();
      };
      var span = document.createElement('span');
      span.textContent = f.name;
      span.title = f.path;
      row.appendChild(cb);
      row.appendChild(span);
      frag.appendChild(row);
    });
    listEl.appendChild(frag);
  }

  function updateCount() {
    var n = Object.keys(picked).length;
    sendEl.textContent = '导入选中（' + n + '）';
    sendEl.disabled = n === 0;
  }

  function loadDir(dir) {
    dir = String(dir || '').trim();
    if (!dir) return;
    evalScript('TM_list(' + JSON.stringify(dir) + ')', function (raw) {
      var r = parseResult(raw);
      if (!r.ok) { setState('err', '载入失败', r.msg || ''); return; }
      dirEl.value = r.dir;
      try { localStorage.setItem('tm_last_dir', r.dir); } catch (e) {}
      files = r.items || [];
      picked = {};
      renderList(); updateCount();
      setState('', '已载入', r.dir + '（' + files.length + ' 个视频）');
    });
  }

  // ---- 轮询：接收 TagMaster GUI 发来的命令 ----
  function poll() {
    var script = '(typeof TM_poll === "function") ? TM_poll() : "{\\"ok\\":false,\\"msg\\":\\"桥脚本未加载。\\"}"';
    evalScript(script, function (raw) {
      var r = parseResult(raw);
      if (r.ok && (r.msg === 'idle' || r.msg === 'empty' || r.msg === 'duplicate' || r.msg === 'stale')) {
        setState('idle', '监听中', '等待 TagMaster 发送，或在上方选文件夹。');
        return;
      }
      if (r.ok) setState('', '已导入', r.msg || '导入完成。');
      else setState('err', '桥出错', r.msg || '未知错误。');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    dot = $('dot'); statusEl = $('status'); detailEl = $('detail');
    dirEl = $('dir'); listEl = $('list'); qEl = $('q'); sendEl = $('send');

    $('browse').onclick = function () {
      evalScript('TM_selectFolder()', function (raw) {
        var r = parseResult(raw);
        if (r.ok && r.dir) loadDir(r.dir);
      });
    };
    $('load').onclick = function () { loadDir(dirEl.value); };
    dirEl.onkeydown = function (e) { if (e.key === 'Enter') loadDir(dirEl.value); };
    qEl.oninput = renderList;
    $('all').onclick = function () {
      filtered().forEach(function (f) { picked[f.path] = f; });
      renderList(); updateCount();
    };
    $('none').onclick = function () { picked = {}; renderList(); updateCount(); };
    sendEl.onclick = function () {
      var dn = (dirEl.value || '').replace(/[\\\/]$/, '');
      var bin = dn.substr(Math.max(dn.lastIndexOf('\\'), dn.lastIndexOf('/')) + 1) || '面板导入';
      var items = Object.keys(picked).map(function (p) { return { path: p, bin: bin }; });
      sendEl.disabled = true;
      evalScript('TM_import(' + JSON.stringify(items) + ')', function (raw) {
        var r = parseResult(raw);
        if (r.ok) setState('', '已导入 ' + (r.count || 0) + ' 个', r.msg || '');
        else setState('err', '导入失败', r.msg || '');
        updateCount();
      });
    };

    try {
      var last = localStorage.getItem('tm_last_dir');
      if (last) { dirEl.value = last; loadDir(last); }
    } catch (e) {}

    poll();
    window.setInterval(poll, 1000);
  });
}());
