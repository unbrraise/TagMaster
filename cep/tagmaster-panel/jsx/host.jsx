// TagMaster 素材桥 · Premiere 导入脚本（由 CEP 面板加载执行）
// 命令桥的接口设计参考 TuanToolKit（原作者）的文件轮询方案，本文件为独立实现。
// 命令文件：%APPDATA%\TagMaster\bin\tm_cmd.json，由 TagMaster 主程序写入，
// 面板每秒轮询一次，读到后导入并删除。支持的操作：importMany。
(function () {
  var ROOT_BIN_NAME = 'TagMaster';
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
    var root = app.project.rootItem;
    var target = getOrCreateBin(getOrCreateBin(root, ROOT_BIN_NAME), safeBinName(binName));
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
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (!item || !item.path) { errors.push('缺路径'); continue; }
      var err = importOne(item.path, item.bin || DEFAULT_BIN_NAME);
      if (err) errors.push(err);
      else done++;
    }
    if (errors.length) {
      return result(done > 0, '导入 ' + done + ' 个，失败 ' + errors.length + ' 个：' + errors.slice(0, 3).join('；'), done);
    }
    return result(true, '已导入 ' + done + ' 个片段到「' + ROOT_BIN_NAME + '」bin。', done);
  }

  // 面板每秒调用一次；没有命令时返回 idle
  $.global.TMBridge_startedAt = new Date().getTime();
  $.global.TMBridge_lastCommand = '';
  $.global.TM_poll = function () {
    var file = File(commandFilePath());
    try {
      if (!file.exists) return result(true, 'idle');
      var text = readText(file);
      if (!text) { try { file.remove(); } catch (e) {} return result(true, 'empty'); }
      if (text === $.global.TMBridge_lastCommand) {
        try { file.remove(); } catch (e) {}
        return result(true, 'duplicate');
      }
      var parsed = parseCommand(text);
      var command = parsed && parsed.tm_cmd ? parsed.tm_cmd : null;
      if (!command || (command.action !== 'importMany' && command.action !== 'import')) {
        try { file.remove(); } catch (e) {}
        return result(false, '不支持的命令，已忽略。');
      }
      if (!command.createdAt || command.createdAt < $.global.TMBridge_startedAt) {
        try { file.remove(); } catch (e) {}
        return result(true, 'stale');
      }
      var r;
      if (command.action === 'importMany') r = importMany(command.items);
      else r = importMany([command]);
      $.global.TMBridge_lastCommand = text;
      try { file.remove(); } catch (e) {}
      return r;
    } catch (e) {
      try { if (file.opened) file.close(); } catch (e2) {}
      return result(false, e && e.message ? e.message : String(e));
    }
  };
}());
