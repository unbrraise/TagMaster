(function () {
  var dot = null, statusEl = null, detailEl = null;
  var dirEl = null, listEl = null, qEl = null, sendEl = null;
  var pvEl = null, pvpEl = null, pvnEl = null;
  var files = [];          // [{name, path}]
  var picked = {};         // path -> {name, path}
  var tagsByName = {};     // 新文件名 -> {景别, 主体, ...}（来自 _标签数据/tags.csv）
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
  // 关键：换文件加载新 src 后，浏览器会把 playbackRate 重置回 defaultPlaybackRate。
  // 所以速度要同时写 defaultPlaybackRate（跨加载持久）+ playbackRate（立即生效），
  // 并在 loadedmetadata 时再补一次，保证每个新视频都按当前倍速播。
  var curRate = 2;
  function setSpeed(r, btn) {
    curRate = r;
    pvEl.defaultPlaybackRate = r;
    pvEl.playbackRate = r;
    document.querySelectorAll('.spd').forEach(function (b) {
      b.classList.toggle('on', b === btn);
    });
  }
  function preview(path, name) {
    pvpEl.style.display = 'none';
    pvEl.style.display = '';
    if (selPath !== path) {
      selPath = path;
      pvEl.src = fileUrl(path);
      pvnEl.textContent = name;
      pvEl.playbackRate = curRate;
    } else {
      pvEl.currentTime = 0;  // 再点同一个文件 = 重头播
    }
    var play = pvEl.play();
    if (play && play.catch) play.catch(function () {});
  }

  // ---- 列表 ----
  function filtered() {
    var q = qEl.value.trim().toLowerCase();
    if (!q) return files;
    // 空格分隔的关键词全部命中才算匹配：对 文件名 + 全部标签 做搜索
    var kws = q.split(/\s+/).filter(Boolean);
    return files.filter(function (f) {
      var t = tagsByName[f.name] || {};
      var hay = [f.name].concat(TAG_COLS.map(function (k) { return t[k] || ''; }))
                 .join(' ').toLowerCase();
      return kws.every(function (k) { return hay.indexOf(k) >= 0; });
    });
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
      var tl = tagline(f.name);
      if (tl) {
        var tagEl = document.createElement('div');
        tagEl.className = 'tagline';
        tagEl.textContent = tl;
        row.appendChild(tagEl);
      }
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
      // 标签表跟上：有 tags.csv 就能按标签搜（少女 / 金发 / 夜晚 …）
      // 选的是总库（没有自己的 tags.csv）时，合并每个作品子目录里的标签表
      evalScript('TM_readTags(' + JSON.stringify(r.dir) + ')', function (raw2) {
        var r2 = parseResult(raw2);
        if (r2.ok && r2.csv) {
          tagsByName = buildTagIndex(r2.csv);
          renderList();   // 重新渲染：列表项补上标签摘要
        } else if (r2.ok && (r.dirs || []).length) {
          var pending = r.dirs.slice();
          var mergeNext = function () {
            if (!pending.length) { renderList(); return; }
            var sub = pending.shift();
            evalScript('TM_readTags(' + JSON.stringify(sub) + ')', function (raw3) {
              var r3 = parseResult(raw3);
              if (r3.ok && r3.csv) {
                var idx = buildTagIndex(r3.csv);
                for (var k in idx) {
                  if (!(k in tagsByName)) tagsByName[k] = idx[k];  // 重名作品间以先到为准
                }
              }
              mergeNext();
            });
          };
          mergeNext();
        }
      });
    });
  }

  // ---- 标签表：解析 tags.csv，建立 文件名 → 标签 索引 ----
  function parseCsv(text) {
    var rows = [], row = [], cur = '', inQ = false;
    for (var i = 0; i < text.length; i++) {
      var c = text.charAt(i);
      if (inQ) {
        if (c === '"') {
          if (text.charAt(i + 1) === '"') { cur += '"'; i++; }
          else inQ = false;
        } else cur += c;
      } else {
        if (c === '"') inQ = true;
        else if (c === ',') { row.push(cur); cur = ''; }
        else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; }
        else if (c !== '\r') cur += c;
      }
    }
    if (cur !== '' || row.length) { row.push(cur); rows.push(row); }
    return rows;
  }

  var TAG_COLS = ['景别', '人数', '朝向', '主体', '动作', '场景', '光线', '情绪', '风格', '色调'];

  function buildTagIndex(csvText) {
    var idx = {};
    try {
      var rows = parseCsv(csvText);
      if (!rows.length) return idx;
      var hdr = rows[0];
      var iName = hdr.indexOf('新文件名');
      if (iName < 0) return idx;
      var col = {};
      TAG_COLS.forEach(function (k) { col[k] = hdr.indexOf(k); });
      for (var i = 1; i < rows.length; i++) {
        var r = rows[i];
        var name = (r[iName] || '').trim();
        if (!name) continue;
        var tags = {};
        TAG_COLS.forEach(function (k) {
          tags[k] = col[k] >= 0 ? (r[col[k]] || '').trim() : '';
        });
        idx[name] = tags;
      }
    } catch (e) {}
    return idx;
  }

  function tagline(name) {
    var t = tagsByName[name];
    if (!t) return '';
    return TAG_COLS.map(function (k) { return t[k]; }).filter(Boolean).join(' · ');
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

    pvEl.defaultPlaybackRate = 2;  // 默认 2 倍速预览，按钮条可随时切换
    pvEl.playbackRate = 2;
    // 新视频加载完再补一次速率，防止浏览器加载时把倍速打回 1
    pvEl.addEventListener('loadedmetadata', function () {
      pvEl.playbackRate = curRate;
    });
    poll();
    window.setInterval(poll, 1000);
  });
}());
