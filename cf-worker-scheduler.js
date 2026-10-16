/**
 * Cloudflare Worker - 股票仪表盘调度器
 * 替代原腾讯云函数 SCF (kdj-watch-trigger)
 *
 * Cron (UTC): every 5 min between hour 1-7 on weekdays → 北京工作日 9:00-15:00
 * Handler 内根据北京时间分派到 3 个 GitHub Actions workflow:
 *   - 8:35           → cloud-full-scan.yml        (早盘全量扫描)
 *   - 10:10/11:10/13:10/13:30/14:10/14:45 → cloud-dashboard-update.yml  (6档仪表盘推送)
 *   - 其他 9:00-15:00 → cloud-index-kdj-watch.yml  (大盘KDJ 5min监控)
 *
 * 2026-10-16: 新增 13:30 档（用户要求午后开盘加一次更新），
 *             并对所有档位加 ±2 分钟容差，避免 cron 触发延迟导致漏档。
 *
 * 部署: Cloudflare Dashboard → Workers & Pages → Create Worker
 *       粘贴本文件 → Settings → Variables 添加 GH_TOKEN (Encrypted)
 *       Triggers → Cron Triggers 添加 every-5min 1-7 UTC weekdays
 */

export default {
  async scheduled(event, env, ctx) {
    const GH_TOKEN = env.GH_TOKEN;
    const REPO = 'Yj66668888/stock-dashboard';

    if (!GH_TOKEN) {
      console.error('[FATAL] GH_TOKEN 未配置');
      return;
    }

    // 当前北京时间
    const now = new Date();
    const beijing = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
    const hour = beijing.getHours();
    const minute = beijing.getMinutes();
    const hm = hour * 100 + minute;
    const weekday = beijing.getDay();  // 0=周日, 6=周六

    if (weekday === 0 || weekday === 6) {
      console.log(`[skip] 周末 weekday=${weekday}`);
      return;
    }

    // 按时间分派 workflow（容差 ±2 分钟，防 cron 触发抖动漏档）
    const TOL = 2;
    const near = (slot) => Math.abs(hm - slot) <= TOL;
    const UPDATE_SLOTS = [1010, 1110, 1310, 1330, 1410, 1445];

    let workflow = null;
    let action = '';
    if (near(835)) {
      workflow = 'cloud-full-scan.yml';
      action = 'full-scan';
    } else if (UPDATE_SLOTS.some(near)) {
      workflow = 'cloud-dashboard-update.yml';
      action = 'dashboard-update';
    } else if (hm >= 900 && hm < 1500) {
      // 9:00-14:55 之间的每5分钟（含 9:35, 9:40, ... 14:55 等）
      workflow = 'cloud-index-kdj-watch.yml';
      action = 'kdj-watch';
    }

    if (!workflow) {
      console.log(`[skip] hm=${hm} 非交易时段`);
      return;
    }

    // 调用 GitHub workflow_dispatch API
    const url = `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`;
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${GH_TOKEN}`,
          'Accept': 'application/vnd.github.v3+json',
          'User-Agent': 'cf-worker-stock-dashboard'
        },
        body: JSON.stringify({ ref: 'main' })
      });
      const ok = resp.status === 204;
      console.log(`[dispatch] ${action} → ${workflow} status=${resp.status} ${ok ? '✅' : '❌'}`);
      if (!ok) {
        const txt = await resp.text();
        console.error(`[error] ${txt.substring(0, 300)}`);
      }
    } catch (e) {
      console.error(`[error] ${action}: ${e.message}`);
    }
  }
};
