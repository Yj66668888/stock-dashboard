# -*- coding: utf-8 -*-
"""把「日K(5日)」+「隔夜」两列写进仪表盘双名单表（2026-11-03）

⚠️ 重要：**deploy/index.html 才是源**，云端的 cloud_pipeline.py 每次都执行
   shutil.copy2(deploy/index.html -> docs/index.html)（line 340 / 464）。
   只推 docs 不改 deploy → 下一次云端跑就被覆盖回老版本（2026-11-03 已踩过：
   上一轮加的「日K」列整列消失）。所以本脚本同时改两个文件，推送时也要两个都推。
"""
import io

FILES = ['deploy/index.html', 'docs/index.html']

# ---------- 1. CSS：16 列的对齐 + 新单元格配色 ----------
OLD_CSS = """  .dual-table th:nth-child(1),.dual-table th:nth-child(2),.dual-table th:nth-child(5),.dual-table th:nth-child(9),.dual-table th:nth-child(14){text-align:left}"""
NEW_CSS = """  .dual-table th:nth-child(1),.dual-table th:nth-child(2),.dual-table th:nth-child(4),.dual-table th:nth-child(6),.dual-table th:nth-child(10),.dual-table th:nth-child(15),.dual-table th:nth-child(16){text-align:left}"""

OLD_CSS2 = """  .dual-table td:nth-child(1),.dual-table td:nth-child(2),.dual-table td:nth-child(5),.dual-table td:nth-child(9),.dual-table td:nth-child(14){text-align:left}"""
NEW_CSS2 = """  .dual-table td:nth-child(1),.dual-table td:nth-child(2),.dual-table td:nth-child(4),.dual-table td:nth-child(6),.dual-table td:nth-child(10),.dual-table td:nth-child(15),.dual-table td:nth-child(16){text-align:left}"""

OLD_CSS3 = """  .ph-hi{color:#d85a30;font-weight:600}"""
NEW_CSS3 = """  .ph-hi{color:#d85a30;font-weight:600}
  .dt-up{color:#1d9e75;font-weight:600}
  .dt-flat{color:#6b7280}
  .dt-weak{color:#d97706}
  .dt-down{color:#d85a30;font-weight:600}
  .gq-ok{color:#1d9e75}
  .gq-warn{color:#d97706;font-weight:600}
  .gq-bad{color:#d85a30;font-weight:600}"""

# ---------- 2. 表头（两个表各一处，内容相同）----------
OLD_TH = """            <th>代码</th><th>名称</th><th>现价</th><th>活跃度</th><th>档位</th>
            <th>beta60</th><th>vol20</th><th>amt20</th><th>创新低</th>
            <th>位置P</th><th>K值</th><th>DIF</th><th>volr20</th><th>MACD信号</th>"""
NEW_TH = """            <th>代码</th><th>名称</th><th>现价</th><th>日K(5日)</th><th>活跃度</th><th>档位</th>
            <th>beta60</th><th>vol20</th><th>amt20</th><th>创新低</th>
            <th>位置P</th><th>K值</th><th>DIF</th><th>volr20</th><th>隔夜</th><th>MACD信号</th>"""

# ---------- 3. 渲染函数 ----------
OLD_RENDER = """function renderDualTable(tid, rows) {
  var tb = document.querySelector('#' + tid + ' tbody');
  if (!tb) return;
  tb.innerHTML = '';
  if (!rows || !rows.length) {
    tb.innerHTML = '<tr><td colspan="14" style="text-align:center;color:#999;padding:18px">暂无数据</td></tr>';
    return;
  }
  rows.forEach(function (r) {
    var tr = document.createElement('tr');
    var cls = (r.act >= 0.2) ? 'act-hot' : ((r.act <= -0.2) ? 'act-cold' : '');
    var cells = [
      (r.code || '-'), (r.name || '-'), dnum(r.close, 2),
      '<span class="' + cls + '">' + dnum(r.act, 3) + '</span>',
      (r.tag || '-'), dnum(r.beta60, 3), dnum(r.vol20, 4), dnum(r.amt20, 2),
      (r.newlow20 ? '是' : '否'),
      (r.P60 >= 75
        ? '<span class="ph-hi" title="P60≥75：位置过高，按铁律应回避">' + dnum(r.P60, 1) + '▲</span>'
        : dnum(r.P60, 1)),
      dnum(r.K, 1),
      dnum(r.DIF, 3), dnum(r.volr, 3), ((r.sigs || []).join('、') || '-')
    ];
    tr.innerHTML = '<td>' + cells.join('</td><td>') + '</td>';
    tb.appendChild(tr);
  });
}"""

NEW_RENDER = """// 日K档 → 配色：在涨/刚金叉=绿，横盘=灰，金叉·仍弱=橙，下坡=红
function dtClass(s) {
  if (s === 'gold' || s === 'up') return 'dt-up';
  if (s === 'gold_weak') return 'dt-weak';
  if (s === 'down') return 'dt-down';
  return 'dt-flat';
}
function dtCell(r) {
  var lbl = r.day || '-';
  var ret = (r.ret5 === null || r.ret5 === undefined) ? '' : (' ' + dnum(r.ret5, 1) + '%');
  return '<span class="' + dtClass(r.day_state) + '">' + lbl + ret + '</span>';
}
// 隔夜列：5 日内低开次数（越小越好）。命中排除规则的票本来就不进名单，这里只做展示
function gqCell(r) {
  if (!r.gq || r.gq === '-') return '-';
  var bad = (r.gq_bad === null || r.gq_bad === undefined) ? 0 : r.gq_bad;
  var cls = (bad >= 2) ? 'gq-bad' : (bad === 1 ? 'gq-warn' : 'gq-ok');
  return '<span class="' + cls + '">' + r.gq + '</span>';
}

function renderDualTable(tid, rows) {
  var tb = document.querySelector('#' + tid + ' tbody');
  if (!tb) return;
  tb.innerHTML = '';
  if (!rows || !rows.length) {
    tb.innerHTML = '<tr><td colspan="16" style="text-align:center;color:#999;padding:18px">暂无数据</td></tr>';
    return;
  }
  rows.forEach(function (r) {
    var tr = document.createElement('tr');
    var cls = (r.act >= 0.2) ? 'act-hot' : ((r.act <= -0.2) ? 'act-cold' : '');
    var cells = [
      (r.code || '-'), (r.name || '-'), dnum(r.close, 2), dtCell(r),
      '<span class="' + cls + '">' + dnum(r.act, 3) + '</span>',
      (r.tag || '-'), dnum(r.beta60, 3), dnum(r.vol20, 4), dnum(r.amt20, 2),
      (r.newlow20 ? '是' : '否'),
      (r.P60 >= 75
        ? '<span class="ph-hi" title="P60≥75：位置过高，按铁律应回避">' + dnum(r.P60, 1) + '▲</span>'
        : dnum(r.P60, 1)),
      dnum(r.K, 1),
      dnum(r.DIF, 3), dnum(r.volr, 3), gqCell(r), ((r.sigs || []).join('、') || '-')
    ];
    tr.innerHTML = '<td>' + cells.join('</td><td>') + '</td>';
    tb.appendChild(tr);
  });
}"""

# ---------- 4. 空数据提示 ----------
OLD_EMPTY = """        tb.innerHTML = '<tr><td colspan="14" style="text-align:center;color:#999;padding:18px">' +"""
NEW_EMPTY = """        tb.innerHTML = '<tr><td colspan="16" style="text-align:center;color:#999;padding:18px">' +"""

# ---------- 5. 说明文字 ----------
OLD_NOTE_UP = """      <div class="db-note">涨日实测：全市场基准红盘率 <b>67.06%</b>，活跃度高端组明显跑赢（bias60 低值组 75.3%）。
        大盘上涨时买活跃股，弹性兑现得快。</div>"""
NEW_NOTE_UP = """      <div class="db-note">涨日实测：全市场基准红盘率 <b>67.06%</b>，活跃度高端组明显跑赢（bias60 低值组 75.3%）。
        大盘上涨时买活跃股，弹性兑现得快。
        <br>排序：<b>先挑日K在涨的（✦刚金叉 / ↑上涨），再在同级里取活跃度最高的</b>。</div>"""

OLD_NOTE_DN = """      <div class="db-note">跌日实测：全市场基准红盘率只有 <b>25.77%</b>，活跃度低端组抗跌（beta60 低值组 32.5%）。
        大盘下跌时买冷门股，跌得少。</div>"""
NEW_NOTE_DN = """      <div class="db-note">跌日实测：全市场基准红盘率只有 <b>25.77%</b>，活跃度低端组抗跌（beta60 低值组 32.5%）。
        大盘下跌时买冷门股，跌得少。
        <br>排序：<b>先挑日K在涨的（✦刚金叉 / ↑上涨），再在同级里取活跃度最低的</b>。</div>"""

OLD_FOOT = """      · 名单只是排序结果，不是买卖指令 —— 买不买你自己定。"""
NEW_FOOT = """      · <b>日K(5日)</b>：日线方向轴，只吃日线、与 30 分钟 MACD 无关。✦刚金叉（近5根内金叉且趋势分≥2）＞ ↑上涨（站上MA10且MA10上行、5日正收益）＞ →横盘 ＞ ✦金叉·仍弱（有金叉但趋势分≤1，属下跌途中反抽）＞ ↓下坡。它是排序优先级，不是硬门槛。<br>
      · <b>隔夜</b>：近 5 个交易日「低开 ≤ −1%」的次数。命中<b>反复低开（≥2 次）</b>或<b>单次跳空 ≤ −3% 且当天没摸回昨收</b>的票，<b>直接不进名单</b>。依据：3046 只 × 732 天实测，这类票次日跳空 ≤ −1% 占 24.2%，通过筛选的只有 8.2%；T收→T+1收 差 +0.19%/天（t=5.0）。<br>
      · 名单只是排序结果，不是买卖指令 —— 买不买你自己定。"""


def patch(path):
    s = io.open(path, encoding='utf-8').read()
    orig = s
    stats = []
    for old, new, tag in [
        (OLD_CSS, NEW_CSS, 'css-th'),
        (OLD_CSS2, NEW_CSS2, 'css-td'),
        (OLD_CSS3, NEW_CSS3, 'css-color'),
        (OLD_TH, NEW_TH, 'thead'),
        (OLD_RENDER, NEW_RENDER, 'render'),
        (OLD_EMPTY, NEW_EMPTY, 'empty'),
        (OLD_NOTE_UP, NEW_NOTE_UP, 'note-up'),
        (OLD_NOTE_DN, NEW_NOTE_DN, 'note-dn'),
        (OLD_FOOT, NEW_FOOT, 'foot'),
    ]:
        c = s.count(old)
        stats.append('%s=%d' % (tag, c))
        if c:
            s = s.replace(old, new)
    if s != orig:
        io.open(path, 'w', encoding='utf-8').write(s)
    print(path, ' | '.join(stats), 'changed=%s' % (s != orig))


if __name__ == '__main__':
    for f in FILES:
        patch(f)
