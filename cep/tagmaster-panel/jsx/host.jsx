// TagMaster 素材桥 · Premiere 导入脚本（由 CEP 面板加载执行）
// 命令桥的接口设计参考 TuanToolKit（原作者）的文件轮询方案，本文件为独立实现。
// 命令文件：%APPDATA%\TagMaster\bin\tm_cmd.json，由 TagMaster 主程序写入，
// 面板每秒轮询一次，读到后导入并删除。支持的操作：importMany。
(function () {
  var DEFAULT_BIN_NAME = '片段';

  function commandFilePath() {
    return Folder.userData.fsName + '/TagMaster/bin/tm_cmd.json';
  }

  function quote(s) {
    return '"' + String(s)
      .replace(/\\/g, '\\\\')
      .replace(/"/g, '\\"')
      .replace(/\r/g, '\\r')
      .replace(/\n/g, '\\n') + '"';
  }

  function result(ok, msg, count) {
    var out = '{"ok":' + (ok ? 'true' : 'false') + ',"msg":' + quote(msg || '');
    if (count !== undefined) out += ',"count":' + Number(count);
    return out + '}';
  }

  function readText(file) {
    file.encoding = 'UTF-8';
    if (!file.open('r')) throw new Error('命令文件打不开。');
    var text = file.read();
    file.close();
    return text;
  }

  function parseCommand(text) {
    if (typeof JSON !== 'undefined' && JSON.parse) return JSON.parse(text);
    return eval('(' + text + ')');
  }

  function safeBinName(name) {
    var clean = String(name || '').replace(/[\\\/:*?"<>|]/g, '_').replace(/^\s+|\s+$/g, '');
    return clean || DEFAULT_BIN_NAME;
  }

  function childCount(parent) {
    try {
      if (parent.children && typeof parent.children.numItems !== 'undefined') return parent.children.numItems;
      if (parent.children && typeof parent.children.length !== 'undefined') return parent.children.length;
    } catch (e) {}
    return 0;
  }

  function isBin(item) {
    if (!item) return false;
    try {
      if (typeof ProjectItemType !== 'undefined' && typeof ProjectItemType.BIN !== 'undefined') {
        return item.type === ProjectItemType.BIN;
      }
    } catch (e) {}
    try { return item.children && typeof item.children.numItems !== 'undefined'; } catch (e) {}
    return false;
  }

  function findChildBin(parent, name) {
    var n = childCount(parent);
    for (var i = 0; i < n; i++) {
      var item = null;
      try { item = parent.children[i]; } catch (e) { item = null; }
      if (item && item.name === name && isBin(item)) return item;
    }
    return null;
  }

  function getOrCreateBin(parent, name) {
    var bin = findChildBin(parent, name);
    if (bin) return bin;
    try { parent.createBin(name); } catch (e) {}
    bin = findChildBin(parent, name);
    if (!bin) throw new Error('建不了 bin：' + name);
    return bin;
  }

  function importOne(path, binName) {
    var source = File(path);
    if (!source.exists) return '文件不存在：' + path;
    if (!app.project) return '没有打开的 Premiere 工程。';
    // 直接放进以作品名命名的 bin（项目根目录），不再套一层「TagMaster」
    var target = getOrCreateBin(app.project.rootItem, safeBinName(binName));
    var ok = false;
    try {
      ok = app.project.importFiles([source.fsName], true, target, false);
    } catch (e) {
      return '导入出错：' + (e && e.message ? e.message : String(e));
    }
    if (!ok) return 'Premiere 拒绝导入：' + source.fsName;
    return '';
  }

  function importMany(items) {
    if (!items || !items.length) return result(false, '没有要导入的片段。', 0);
    var done = 0;
    var errors = [];
    var bins = {};   // ES3：不能用 Object.keys，汇总 bin 名用 for-in
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (!item || !item.path) { errors.push('缺路径'); continue; }
      var bn = safeBinName(item.bin || DEFAULT_BIN_NAME);
      bins[bn] = 1;
      var err = importOne(item.path, bn);
      if (err) errors.push(err);
      else done++;
    }
    if (errors.length) {
      return result(done > 0, '导入 ' + done + ' 个，失败 ' + errors.length + ' 个：' + errors.slice(0, 3).join('；'), done);
    }
    var where = '';
    for (var k in bins) { if (where) where += '、'; where += k; }
    return result(true, '已导入 ' + done + ' 个片段到 bin：' + where + '。', done);
  }

  // ---------- 面板内浏览：选文件夹 → 列视频 → 勾选导入 ----------

  var VIDEO_EXTS = '|mp4|mov|avi|mkv|m4v|ts|wmv|flv|webm|';

  function listResult(items, dirPath) {
    var parts = [];
    for (var i = 0; i < items.length; i++) {
      parts.push('{"name":' + quote(items[i].name) + ',"path":' + quote(items[i].path) + '}');
    }
    return '{"ok":true,"dir":' + quote(dirPath) + ',"items":[' + parts.join(',') + ']}';
  }

  $.global.TM_selectFolder = function () {
    try {
      var f = Folder.selectDialog('选择素材文件夹');
      if (!f) return result(true, 'cancel');
      return '{"ok":true,"dir":' + quote(f.fsName) + '}';
    } catch (e) {
      return result(false, e && e.message ? e.message : String(e));
    }
  };

  $.global.TM_list = function (dirPath) {
    try {
      var folder = Folder(dirPath);
      if (!folder.exists) return result(false, '目录不存在：' + dirPath);
      var files = folder.getFiles();
      var items = [];
      for (var i = 0; i < files.length; i++) {
        var f = files[i];
        if (!(f instanceof File)) continue;
        var dot = String(f.name).lastIndexOf('.');
        if (dot < 0) continue;
        var ext = String(f.name).substr(dot + 1).toLowerCase();
        if (VIDEO_EXTS.indexOf('|' + ext + '|') < 0) continue;
        var nm = String(f.name);
        try { nm = decodeURIComponent(nm); } catch (e) {}  // ExtendScript 的 name 是 URI 编码名，解码回中文
        items.push({name: nm, path: f.fsName});
      }
      items.sort(function (a, b) {
        return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
      });
      return listResult(items, folder.fsName);
    } catch (e) {
      return result(false, e && e.message ? e.message : String(e));
    }
  };

  // 面板勾选导入：复用 importMany（items: [{path, bin?}]）
  $.global.TM_import = function (items) {
    try { return importMany(items); }
    catch (e) { return result(false, e && e.message ? e.message : String(e)); }
  };

  // ---------- 选文件夹走 TagMaster 代理 ----------
  // ExtendScript 的 Folder.selectDialog 不置顶，容易被 PR 主窗口压住；
  // 所以面板发请求文件，由 TagMaster 弹 tkinter 的目录框（正常置顶），结果回传。

  function pickReqPath() {
    return Folder.userData.fsName + '/TagMaster/bin/tm_pick.json';
  }
  function pickResultPath() {
    return Folder.userData.fsName + '/TagMaster/bin/tm_pick_result.json';
  }

  $.global.TM_requestPick = function () {
    try {
      var f = new File(pickReqPath());
      f.encoding = 'UTF-8';
      if (!f.open('w')) return result(false, '写不了请求文件。');
      f.write('{"req":true,"createdAt":' + new Date().getTime() + '}');
      f.close();
      return result(true, 'ok');
    } catch (e) {
      return result(false, e && e.message ? e.message : String(e));
    }
  };

  // 面板轮询结果；拿到后就删结果文件。{"ok":true,"dir":"..."} / {"ok":true,"cancel":true}
  $.global.TM_pollPick = function () {
    try {
      var f = new File(pickResultPath());
      if (!f.exists) return result(true, 'pending');
      var text = readText(f);
      try { f.remove(); } catch (e) {}
      var parsed = parseCommand(text);
      if (parsed && parsed.dir) return '{"ok":true,"dir":' + quote(parsed.dir) + '}';
      return result(true, 'cancel');
    } catch (e) {
      return result(false, e && e.message ? e.message : String(e));
    }
  };

  // 读当前目录的标签表原文（面板解析）；没有就返回 no-csv
  $.global.TM_readTags = function (dirPath) {
    try {
      var f = new File(dirPath + '/_标签数据/tags.csv');
      if (!f.exists) return result(true, 'no-csv');
      var text = readText(f);
      if (text.length > 3000000) return result(false, '标签表过大（>3MB）。');
      return '{"ok":true,"csv":' + quote(text) + '}';
    } catch (e) {
      return result(false, e && e.message ? e.message : String(e));
    }
  };

  // 面板每秒调用一次；没有命令时返回 idle
  $.global.TM_poll = function () {    var file = File(commandFilePath());
    try {
      if (!file.exists) return result(true, 'idle');
      var text = readText(file);
      if (!text || !/\S/.test(text)) {
        try { file.remove(); } catch (e) {}
        return result(true, 'empty');
      }
      // 【先消费，后导入】立刻把命令从队列里拿走：删文件；删不掉（被占用）就把
      // 内容清空。这样无论后面解析/导入怎么出错、面板怎么重载，同一命令都
      // 不可能被第二次执行 —— 否则一处运行时错误就会变成无限循环导入。
      try { file.remove(); } catch (e) {}
      if (file.exists) {
        try {
          file.encoding = 'UTF-8';
          if (file.open('w')) { file.write(''); file.close(); }
        } catch (e2) {}
      }
      var parsed = parseCommand(text);
      var command = parsed && parsed.tm_cmd ? parsed.tm_cmd : null;
      if (!command || (command.action !== 'importMany' && command.action !== 'import')) {
        return result(false, '不支持的命令，已忽略。');
      }
      // 注意：不做「早于面板启动就丢弃」的判断 —— PR 没开时浏览器发来的
      // 命令也要在打开 PR 后照常执行；命令文件已在上面的先消费步骤拿走。
      var r;
      if (command.action === 'importMany') r = importMany(command.items);
      else r = importMany([command]);
      return r;
    } catch (e) {
      try { if (file.opened) file.close(); } catch (e2) {}
      return result(false, e && e.message ? e.message : String(e));
    }
  };
}());
