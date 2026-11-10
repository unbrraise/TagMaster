(function () {
  var dot = null, statusEl = null, detailEl = null;
  var dirEl = null, listEl = null, qEl = null, sendEl = null;
  var pvEl = null, pvpEl = null, pvnEl = null;
  var files = [];          // [{name, path}]
  var picked = {};         // path -> {name, path}
  var selPath = '';        // 当前预览的文件
  var picking = false;     // 浏览请求发出、等待 TagMaster 弹窗

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

  function fileUrl(p) {
    return 'file:///' + encodeURI(String(p).replace(/\\/g, '/')).replace(/#/g, '%23');
  }

  // ---- 视频预览 ----
  function setSpeed(r, btn) {
    pvEl.playbackRate = r;
    document.querySelectorAll('.spd').forEach(function (b) {
      b.classList.toggle('on', b === btn);
    });
  }
  function preview(path, name) {
    if (selPath === path) return;
    selPath = path;
    pvpEl.style.display = 'none';
    pvEl.style.display = '';
    pvEl.src = fileUrl(path);
    pvnEl.textContent = name;
    var play = pvEl.play();
    if (play && play.catch) play.catch(function () {});
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
      var row = document.createElement('div');
      row.className = 'item';
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = !!picked[f.path];
      cb.onchange = function () {
        if (cb.checked) picked[f.path] = f; else delete picked[f.path];
        row.classList.toggle('sel', cb.checked || selPath === f.path);
        updateCount();
      };
      var span = document.createElement('span');
      span.textContent = f.name;
      span.title = '点击预览\n' + f.path;
      span.onclick = function () { preview(f.path, f.name); refreshSelRows(); };
      row.appendChild(cb);
      row.appendChild(span);
      frag.appendChild(row);
    });
    listEl.appendChild(frag);
  }

  function refreshSelRows() {
    var rows = listEl.querySelectorAll('.item');
    var list = filtered();
    rows.forEach(function (row, i) {
      var f = list[i];
      if (!f) return;
      row.classList.toggle('sel', !!picked[f.path] || selPath === f.path);
    });
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
      selPath = '';
      pvEl.style.display = 'none'; pvpEl.style.display = '';
      pvnEl.textContent = '未选择';
      renderList(); updateCount();
      setState('', '已载入', r.dir + '（' + files.length + ' 个视频）');
    });
  }

  // ---- 浏览：走 TagMaster 代理（ExtendScript 的系统对话框不置顶，tkinter 的会置顶） ----
  function browse() {
    if (picking) return;
    picking = true;
    evalScript('TM_requestPick()', function (raw) {
      var r = parseResult(raw);
      if (!r.ok) { picking = false; setState('err', '请求失败', r.msg || ''); return; }
      setState('idle', '请在 TagMaster 弹出的窗口里选择…', '如果没弹出来，确认 TagMaster 已打开。');
      var waited = 0;
      var t = window.setInterval(function () {
        waited += 400;
        evalScript('TM_pollPick()', function (raw2) {
          var r2 = parseResult(raw2);
          if (r2.ok && r2.dir) {
            window.clearInterval(t); picking = false;
            loadDir(r2.dir);
          } else if (r2.ok && r2.cancel) {
            window.clearInterval(t); picking = false;
            setState('idle', '已取消', '');
          } else if (waited > 15000) {
            window.clearInterval(t); picking = false;
            setState('err', '没有响应', 'TagMaster 好像没开 —— 直接在路径框输入，或先打开 TagMaster。');
          }
        });
      }, 400);
    });
  }

  // ---- 轮询：接收 TagMaster GUI 发来的导入命令 ----
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
    pvEl = $('pv'); pvpEl = $('pvp'); pvnEl = $('pvn');

    $('browse').onclick = browse;
    $('load').onclick = function () { loadDir(dirEl.value); };
    dirEl.onkeydown = function (e) { if (e.key === 'Enter') loadDir(dirEl.value); };
    qEl.oninput = renderList;
    document.querySelectorAll('.spd').forEach(function (b) {
      b.onclick = function () { setSpeed(parseFloat(b.dataset.r), b); };
    });
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

    pvEl.playbackRate = 2;  // 默认 2 倍速预览，按钮条可随时切换
    poll();
    window.setInterval(poll, 1000);
  });
}());
