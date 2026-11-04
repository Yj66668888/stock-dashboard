# -*- coding: utf-8 -*-
"""把「60分K」列写进仪表盘双名单表（2026-11-04，16列 → 17列）

⚠️ deploy/index.html 才是源（云端每次 copy2 覆盖 docs），两个文件都要改、都推。
"""
import io

FILES = ['deploy/index.html', 'docs/index.html']

# ---------- 1. CSS：左对齐列 1,2,4,6,10,15,16 → 1,2,4,5,7,11,16,17 ----------
OLD_CSS_TH = '.dual-table th:nth-child(1),.dual-table th:nth-child(2),.dual-table th:nth-child(4),.dual-table th:nth-child(6),.dual-table th:nth-child(10),.dual-table th:nth-child(15),.dual-table th:nth-child(16){text-align:left}'
NEW_CSS_TH = '.dual-table th:nth-child(1),.dual-table th:nth-child(2),.dual-table th:nth-child(4),.dual-table th:nth-child(5),.dual-table th:nth-child(7),.dual-table th:nth-child(11),.dual-table th:nth-child(16),.dual-table th:nth-child(17){text-align:left}'
OLD_CSS_TD = '.dual-table td:nth-child(1),.dual-table td:nth-child(2),.dual-table td:nth-child(4),.dual-table td:nth-child(6),.dual-table td:nth-child(10),.dual-table td:nth-child(15),.dual-table td:nth-child(16){text-align:left}'
NEW_CSS_TD = '.dual-table td:nth-child(1),.dual-table td:nth-child(2),.dual-table td:nth-child(4),.dual-table td:nth-child(5),.dual-table td:nth-child(7),.dual-table td:nth-child(11),.dual-table td:nth-child(16),.dual-table td:nth-child(17){text-align:left}'

OLD_CSS3 = '  .gq-bad{color:#d85a30;font-weight:600}'
NEW_CSS3 = """  .gq-bad{color:#d85a30;font-weight:600}
  .h60-up{color:#1d9e75;font-weight:600}
  .h60-near{color:#0e7490;font-weight:600}
  .h60-bad{color:#d85a30;font-weight:600}
  .h60-na{color:#9ca3af}"""

# ---------- 2. 表头：日K(5日) 后插 60分K ----------
OLD_TH = '<th>现价</th><th>日K(5日)</th><th>活跃度</th>'
NEW_TH = '<th>现价</th><th>日K(5日)</th><th>60分K</th><th>活跃度</th>'

# ---------- 3. 渲染函数 ----------
OLD_COLSPAN = 'colspan="16"'
NEW_COLSPAN = 'colspan="17"'

OLD_CELLS = """    var cells = [
      (r.code || '-'), (r.name || '-'), dnum(r.close, 2), dtCell(r),
      '<span class="' + cls + '">' + dnum(r.act, 3) + '</span>',"""
NEW_CELLS = """    var cells = [
      (r.code || '-'), (r.name || '-'), dnum(r.close, 2), dtCell(r), h60Cell(r),
      '<span class="' + cls + '">' + dnum(r.act, 3) + '</span>',"""

OLD_DTCELL = "function dtCell(r) {"
NEW_DTCELL = """// 60分KDJ：✦刚金叉/↑上行/⚡将金叉=准入（绿/绿/青）；↘高位回落/↓下行=被排除层挡下（红）
function h60Cell(r) {
  var m = { '✦': 'h60-up', '↑': 'h60-up', '⚡': 'h60-near',
            '↘': 'h60-bad', '↓': 'h60-bad' };
  var v = r.h60 || '—';
  var cls = m[v] || 'h60-na';
  var tip = { '✦': '60分KDJ刚金叉（准入）', '↑': '60分KDJ上行（准入）',
              '⚡': '60分KDJ快要金叉（准入）', '↘': '60分KDJ高位回落（已被排除层挡下）',
              '↓': '60分KDJ下行（已被排除层挡下）', '—': '数据获取失败/不足（fail-open 不拦截）' }[v] || '';
  return '<span class="' + cls + '"' + (tip ? ' title="' + tip + '"' : '') + '>' + v + '</span>';
}

function dtCell(r) {"""

# ---------- 4. fillDualMeta 加 60 分剔除提示 ----------
OLD_META = """  if (st.gq_drop) {
    parts.push('本次已剔除 <b>' + st.gq_drop + '</b> 只「近5日反复低开 / 深坑未补」的票，候选池缩到 <b>' +
      st.gq_keep + '</b> 只');
  }"""
NEW_META = """  if (st.gq_drop) {
    parts.push('隔夜层剔除 <b>' + st.gq_drop + '</b> 只（近5日反复低开/深坑未补），候选剩 <b>' +
      st.gq_keep + '</b> 只');
  }
  if (st.h60_drop) {
    parts.push('60分KDJ剔除 <b>' + st.h60_drop + '</b> 只（下行/高位回落），候选剩 <b>' +
      st.h60_keep + '</b> 只');
  }"""

for p in FILES:
    s = io.open(p, encoding='utf-8').read()
    n = []
    for old, new in ((OLD_CSS_TH, NEW_CSS_TH), (OLD_CSS_TD, NEW_CSS_TD),
                     (OLD_CSS3, NEW_CSS3), (OLD_TH, NEW_TH),
                     (OLD_CELLS, NEW_CELLS), (OLD_DTCELL, NEW_DTCELL),
                     (OLD_META, NEW_META)):
        c = s.count(old)
        n.append(c)
        s = s.replace(old, new)
    c = s.count(OLD_COLSPAN)
    n.append(c)
    s = s.replace(OLD_COLSPAN, NEW_COLSPAN)
    io.open(p, 'w', encoding='utf-8').write(s)
    print(p, 'th_css/td_css/css3/th/cells/dtcell/meta/colspan =', n)
