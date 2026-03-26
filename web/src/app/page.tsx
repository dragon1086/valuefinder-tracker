import { readFileSync } from 'fs';
import { join } from 'path';

interface Report {
  wr_id: number;
  company: string;
  ticker: string;
  title: string;
  author: string;
  report_date: string;
  url: string;
  price_on_date: number | null;
  latest_price: number | null;
  pct_change: number | null;
  peak_price: number | null;
  peak_date: string | null;
  peak_pct: number | null;
}

interface ReportsData {
  updated_at: string;
  reports: Report[];
}

function loadReports(): ReportsData {
  try {
    const filePath = join(process.cwd(), 'public', 'reports.json');
    const raw = readFileSync(filePath, 'utf-8');
    return JSON.parse(raw);
  } catch {
    return { updated_at: '', reports: [] };
  }
}

function formatPrice(price: number | null): string {
  if (price == null) return '-';
  return price.toLocaleString('ko-KR') + '원';
}

function formatPct(pct: number | null): string {
  if (pct == null) return '-';
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

function formatUpdatedAt(iso: string): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    return d.toLocaleString('ko-KR', {
      timeZone: 'Asia/Seoul',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

export default function Home() {
  const data = loadReports();
  const reports = [...data.reports].sort((a, b) => b.report_date.localeCompare(a.report_date));

  const totalCount = reports.length;
  const profitList = reports.filter(r => (r.pct_change ?? 0) > 0);
  const lossList   = reports.filter(r => (r.pct_change ?? 0) < 0);
  const avgProfit =
    profitList.length > 0
      ? profitList.reduce((s, r) => s + (r.pct_change ?? 0), 0) / profitList.length
      : 0;
  const avgLoss =
    lossList.length > 0
      ? lossList.reduce((s, r) => s + (r.pct_change ?? 0), 0) / lossList.length
      : 0;

  return (
    <main className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-8">
      {/* 헤더 */}
      <div className="mb-8">
        <h1 className="text-2xl md:text-3xl font-bold text-white mb-1">
          ValueFinder 수익률 트래커
        </h1>
        <p className="text-gray-400 text-sm">
          마지막 업데이트: {formatUpdatedAt(data.updated_at)}
        </p>
      </div>

      {/* 요약 카드 */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
          <p className="text-gray-400 text-xs mb-1">추적 종목</p>
          <p className="text-2xl font-bold text-white">{totalCount}</p>
        </div>
        <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
          <p className="text-gray-400 text-xs mb-1">수익 종목</p>
          <p className="text-2xl font-bold text-emerald-400">{profitList.length}</p>
        </div>
        <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
          <p className="text-gray-400 text-xs mb-1">평균 수익</p>
          <p className="text-2xl font-bold text-emerald-400">
            {formatPct(avgProfit)}
          </p>
        </div>
        <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
          <p className="text-gray-400 text-xs mb-1">평균 손실</p>
          <p className="text-2xl font-bold text-red-400">
            {formatPct(avgLoss)}
          </p>
        </div>
      </div>

      {/* 테이블 */}
      {reports.length === 0 ? (
        <div className="text-center text-gray-500 py-16">데이터 없음</div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-900 text-gray-400 text-xs uppercase tracking-wider">
                <th className="px-4 py-3 text-left">종목명</th>
                <th className="px-4 py-3 text-left">티커</th>
                <th className="px-4 py-3 text-left">작성일</th>
                <th className="px-4 py-3 text-right">작성일가</th>
                <th className="px-4 py-3 text-right">현재가</th>
                <th className="px-4 py-3 text-right">수익률</th>
                <th className="px-4 py-3 text-right">최고가</th>
                <th className="px-4 py-3 text-right">최고가일</th>
                <th className="px-4 py-3 text-right">최대수익</th>
                <th className="px-4 py-3 text-center">리포트</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r, i) => {
                const pct = r.pct_change ?? 0;
                const isPositive = pct > 0;
                const isNegative = pct < 0;
                return (
                  <tr
                    key={r.wr_id}
                    className={`border-t border-gray-800 hover:bg-gray-900/50 transition-colors ${
                      i % 2 === 0 ? 'bg-gray-950' : 'bg-gray-900/20'
                    }`}
                  >
                    <td className="px-4 py-3 font-medium text-white">{r.company || '-'}</td>
                    <td className="px-4 py-3 text-gray-400 font-mono">{r.ticker || '-'}</td>
                    <td className="px-4 py-3 text-gray-400">{r.report_date}</td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">
                      {formatPrice(r.price_on_date)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">
                      {formatPrice(r.latest_price)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span
                        className={`font-bold font-mono ${
                          isPositive
                            ? 'text-emerald-400'
                            : isNegative
                            ? 'text-red-400'
                            : 'text-gray-400'
                        }`}
                      >
                        {r.pct_change != null ? formatPct(r.pct_change) : '-'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300 font-mono">
                      {formatPrice(r.peak_price)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400 text-xs">
                      {r.peak_date ? r.peak_date.slice(2) : '-'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <span className="font-bold font-mono text-yellow-400">
                        {r.peak_pct != null ? formatPct(r.peak_pct) : '-'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      <a
                        href={r.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-400 hover:text-blue-300 text-xs underline"
                      >
                        보기
                      </a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
