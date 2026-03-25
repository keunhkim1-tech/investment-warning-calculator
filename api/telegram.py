"""
투자경고 해제일 계산기 — 텔레그램 봇 (Vercel Webhook)
종목명을 보내면 투자경고/위험 지정일, 해제 예상일, 기준가를 알려줍니다.
"""
from http.server import BaseHTTPRequestHandler
import json, os, urllib.request, urllib.parse, re
from datetime import date, timedelta
from xml.etree import ElementTree as ET

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TG_API    = f'https://api.telegram.org/bot{BOT_TOKEN}'

# ── 한국 공휴일 ──────────────────────────────────────────────
HOLIDAYS = {
    '2024-01-01','2024-02-09','2024-02-10','2024-02-11','2024-02-12',
    '2024-03-01','2024-04-10','2024-05-01','2024-05-05','2024-05-06',
    '2024-05-15','2024-06-06','2024-08-15','2024-09-16','2024-09-17',
    '2024-09-18','2024-10-03','2024-10-09','2024-12-25',
    '2025-01-01','2025-01-28','2025-01-29','2025-01-30',
    '2025-03-01','2025-03-03','2025-05-01','2025-05-05','2025-05-06',
    '2025-06-03','2025-06-06','2025-08-15','2025-10-03','2025-10-05',
    '2025-10-06','2025-10-07','2025-10-08','2025-10-09','2025-12-25',
    '2026-01-01','2026-02-16','2026-02-17','2026-02-18','2026-02-19',
    '2026-03-01','2026-03-02','2026-05-01','2026-05-05','2026-05-25',
    '2026-06-06','2026-08-17','2026-09-24','2026-09-25',
    '2026-10-03','2026-10-05','2026-10-09','2026-12-25',
    '2027-01-01','2027-02-06','2027-02-07','2027-02-08','2027-02-09',
    '2027-03-01','2027-05-05','2027-05-13','2027-06-06','2027-08-16',
    '2027-10-03','2027-10-04','2027-10-05','2027-10-09','2027-12-25',
}

def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.strftime('%Y-%m-%d') not in HOLIDAYS

def add_trading_days(start: date, n: int) -> date:
    cur, count = start, 0
    while count < n:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            count += 1
    return cur

def count_trading_days(start: date, end: date) -> int:
    count, cur = 0, start
    while cur <= end:
        if is_trading_day(cur):
            count += 1
        cur += timedelta(days=1)
    return count

# ── KRX KIND ────────────────────────────────────────────────
HEADERS_HTML = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Accept': 'text/html,application/xhtml+xml',
    'Referer': 'https://kind.krx.co.kr/',
}

def search_kind(stock_name: str) -> list:
    all_results = []
    for idx, level in [('2', '투자경고'), ('3', '투자위험')]:
        try:
            end_d   = date.today().strftime('%Y%m%d')
            start_d = (date.today() - timedelta(days=365)).strftime('%Y%m%d')
            params  = urllib.parse.urlencode({
                'method': 'investattentwarnriskySub', 'menuIndex': idx,
                'marketType': '', 'searchCorpName': '',
                'startDate': start_d, 'endDate': end_d,
                'pageIndex': '1', 'currentPageSize': '100',
                'orderMode': '3', 'orderStat': 'D',
            })
            req = urllib.request.Request(
                f'https://kind.krx.co.kr/investwarn/investattentwarnrisky.do?{params}',
                headers=HEADERS_HTML)
            with urllib.request.urlopen(req, timeout=8) as r:
                html = r.read().decode('utf-8', errors='replace')
            tbody_m = re.search(r'<tbody>(.*?)</tbody>', html, re.DOTALL)
            if not tbody_m:
                continue
            for row in re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_m.group(1), re.DOTALL):
                nm = re.search(r'<td\s+title="([^"]+)"', row)
                if not nm or not nm.group(1).strip():
                    continue
                if stock_name and stock_name not in nm.group(1):
                    continue
                dates = re.findall(
                    r'<td[^>]*class="[^"]*txc[^"]*"[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</td>', row)
                if dates:
                    all_results.append({
                        'level': level,
                        'stockName': nm.group(1).strip(),
                        'designationDate': dates[0],
                    })
        except Exception as e:
            print(f'KIND error idx={idx}: {e}')
    all_results.sort(key=lambda x: x.get('designationDate', ''), reverse=True)
    return all_results

# ── 네이버 주가 ──────────────────────────────────────────────
HEADERS_NAVER = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Referer': 'https://finance.naver.com/',
}

def naver_stock_code(name: str) -> list:
    params = urllib.parse.urlencode({'q': name, 'target': 'stock'})
    req = urllib.request.Request(
        f'https://ac.stock.naver.com/ac?{params}',
        headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read())
    return [{'code': it['code'], 'name': it['name']} for it in data.get('items', [])]

def fetch_prices(code: str, count: int = 20) -> list:
    url = (f'https://fchart.stock.naver.com/sise.nhn'
           f'?symbol={code}&timeframe=day&count={count}&requestType=0')
    req = urllib.request.Request(url, headers=HEADERS_NAVER)
    with urllib.request.urlopen(req, timeout=7) as r:
        raw = r.read().decode('euc-kr', errors='replace')
    root   = ET.fromstring(raw)
    prices = []
    for item in root.iter('item'):
        parts = item.get('data', '').split('|')
        if len(parts) < 5 or not parts[4] or parts[4] == '0':
            continue
        d = parts[0]
        prices.append({'date': f'{d[:4]}-{d[4:6]}-{d[6:8]}', 'close': int(parts[4])})
    prices.reverse()
    return prices

def calc_thresholds(prices: list) -> dict:
    if len(prices) < 16:
        return None
    t_c   = prices[0]['close'];  t_d   = prices[0]['date']
    t5_c  = prices[5]['close'];  t5_d  = prices[5]['date']
    t15_c = prices[15]['close']; t15_d = prices[15]['date']
    recent15   = prices[:15]
    max15      = max(p['close'] for p in recent15)
    max15_date = next(p['date'] for p in recent15 if p['close'] == max15)
    th1 = round(t5_c * 1.45)
    th2 = round(t15_c * 1.75)
    th3 = max15
    c1, c2, c3 = t_c >= th1, t_c >= th2, t_c >= th3
    return {
        'tClose': t_c, 'tDate': t_d,
        't5Date': t5_d, 'thresh1': th1, 'cond1': c1,
        't15Date': t15_d, 'thresh2': th2, 'cond2': c2,
        'max15Date': max15_date, 'thresh3': th3, 'cond3': c3,
        'allMet': c1 and c2 and c3,
    }

# ── 메시지 포맷 ──────────────────────────────────────────────
def sd(d: date) -> str:
    """date → M/D 형식"""
    return f'{d.month}/{d.day}'

def build_message(stock_name: str, warn: dict, thresholds: dict | None) -> str:
    d_str   = warn['designationDate']
    d_date  = date.fromisoformat(d_str)
    today   = date.today()
    release = add_trading_days(d_date, 10)
    elapsed = count_trading_days(d_date, today) - 1
    diff    = (release - today).days

    if diff > 0:
        dday = f'D-{diff}'
    elif diff == 0:
        dday = 'D-Day'
    else:
        dday = f'D+{abs(diff)}'

    level_emoji = '🟠' if warn['level'] == '투자경고' else '🔴'

    # ── 헤더 ─────────────────────────────────────────────────
    lines = [f'{level_emoji} {stock_name} {warn["level"]}  |  {dday}', '']

    # ── 코드블록 (모노스페이스 정렬) ─────────────────────────
    if thresholds and 'error' not in thresholds:
        t_d   = date.fromisoformat(thresholds['tDate'])
        cur   = thresholds['tClose']
        c1, c2, c3 = thresholds['cond1'], thresholds['cond2'], thresholds['cond3']
        ci    = lambda c: '✅' if c else '❌'

        p1 = f"{thresholds['thresh1']:,}원"
        p2 = f"{thresholds['thresh2']:,}원"
        p3 = f"{thresholds['thresh3']:,}원"
        # 가격 우측 정렬 기준폭
        pw = max(len(p1), len(p2), len(p3))
        # 라벨 명시적 고정 (한글 2바이트 보정: 고점=4자지만 시각폭=T-15와 동일)
        L1 = '① T-5 '   # 6자 → T-5(3) 보정용 공백 포함
        L2 = '② T-15'   # 6자
        L3 = '③ 고점'   # 4자지만 한글 시각폭으로 T-15와 유사

        block = '\n'.join([
            f'현재가  {cur:,}원  ({sd(t_d)})',
            f'지정일  {sd(d_date)}  →  해제가능  {sd(release)}',
            f'경과    {elapsed} / 10 거래일',
            '',
            f'조건      {"기준가":>{pw}}   결과',
            '─' * (8 + pw + 5),
            f'{L1}  {p1:>{pw}}   {ci(c1)}',
            f'{L2}  {p2:>{pw}}   {ci(c2)}',
            f'{L3}  {p3:>{pw}}   {ci(c3)}',
        ])
        lines.append(f'```\n{block}\n```')
        lines.append('')

        # ── 요약 ─────────────────────────────────────────────
        unmet = sum(1 for c in [c1, c2, c3] if not c)
        if thresholds['allMet']:
            lines.append('→ 3가지 모두 해당 · 경고 유지 중 🔴')
        else:
            lines.append(f'→ {unmet}가지 미해당 · {sd(release)} 해제 가능 🟢')
    else:
        block = '\n'.join([
            f'지정일  {sd(d_date)}  →  해제가능  {sd(release)}',
            f'경과    {elapsed} / 10 거래일',
        ])
        lines.append(f'```\n{block}\n```')
        if thresholds and 'error' in thresholds:
            lines.append(f'⚠️ 주가 조회 불가: {thresholds["error"]}')

    return '\n'.join(lines)

# ── Telegram API ─────────────────────────────────────────────
def tg_send(chat_id: int, text: str):
    body = json.dumps({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown',
    }).encode('utf-8')
    req = urllib.request.Request(
        f'{TG_API}/sendMessage',
        data=body,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def tg_send_plain(chat_id: int, text: str):
    body = json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8')
    req  = urllib.request.Request(
        f'{TG_API}/sendMessage', data=body,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

# ── 업데이트 처리 ────────────────────────────────────────────
def do_search(chat_id: int, query: str):
    """종목 검색 공통 로직"""
    if not query:
        tg_send_plain(chat_id, '종목명을 입력해주세요.\n예: /warning 코셈')
        return

    try:
        tg_send_plain(chat_id, f'🔍 "{query}" 검색 중...')
    except Exception:
        pass

    try:
        results = search_kind(query)
    except Exception as e:
        tg_send_plain(chat_id, f'❌ KRX 조회 오류: {e}')
        return

    if not results:
        tg_send_plain(chat_id,
            f'"{query}"에 대한 투자경고/위험 종목을 찾을 수 없습니다.\n'
            '현재 지정된 종목이 없거나 종목명을 확인해주세요.')
        return

    for warn in results[:3]:
        stock_name = warn['stockName']
        thresholds = None
        try:
            codes = naver_stock_code(stock_name)
            if codes:
                prices = fetch_prices(codes[0]['code'], count=20)
                thresholds = calc_thresholds(prices)
        except Exception as e:
            print(f'주가 조회 실패: {e}')

        try:
            tg_send(chat_id, build_message(stock_name, warn, thresholds))
        except Exception:
            try:
                tg_send_plain(chat_id, build_message(stock_name, warn, thresholds))
            except Exception as e:
                tg_send_plain(chat_id, f'⚠️ 결과 전송 오류: {e}')

    if len(results) > 3:
        tg_send_plain(chat_id,
            f'검색 결과 {len(results)}개 중 상위 3개만 표시했습니다.\n'
            '더 정확한 종목명으로 다시 검색해주세요.')


def process_update(update: dict):
    msg = update.get('message') or update.get('edited_message')
    if not msg:
        return
    chat_id = msg['chat']['id']
    text    = msg.get('text', '').strip()

    if not text:
        return

    # 봇 username 제거 (그룹 채팅 대응: /검색@khkimbot → /검색)
    text = re.sub(r'@\w+', '', text).strip()

    # ── /start ─────────────────────────────────────────────
    if text.startswith('/start'):
        tg_send(chat_id,
            '📈 *투자경고 해제일 계산기*\n\n'
            '투자경고/위험 종목의 해제 예상일과 기준가를 알려드립니다.\n\n'
            '*명령어*\n'
            '/warning `종목명` — 종목 투자경고 조회\n'
            '/warning\_all — 전체 투자경고/위험 종목 목록\n'
            '/help — 사용법 안내\n\n'
            '또는 종목명을 바로 입력해도 됩니다.\n'
            '예: `코셈`, `레이저쎌`'
        )
        return

    # ── /도움말 ─────────────────────────────────────────────
    if text.startswith('/help') or text.startswith('/도움말'):
        tg_send(chat_id,
            '📖 *사용법*\n\n'
            '*1. 종목 검색*\n'
            '`/warning 종목명` 또는 종목명을 직접 입력\n'
            '예: `/warning 코셈` 또는 `코셈`\n\n'
            '*2. 전체 목록 조회*\n'
            '`/warning_all` — 현재 투자경고/위험 지정 종목 전체\n\n'
            '*해제 조건 안내*\n'
            '아래 3가지 중 하나라도 미해당 시 다음 거래일 해제:\n'
            '① 현재가 ≥ T\\-5 종가의 145%\n'
            '② 현재가 ≥ T\\-15 종가의 175%\n'
            '③ 현재가 ≥ 최근 15일 최고가\n\n'
            '📊 데이터 출처: KRX KIND, 네이버 금융'
        )
        return

    # ── /warning 종목명 ──────────────────────────────────────
    if text.startswith('/warning') and not text.startswith('/warning_'):
        query = re.sub(r'^/\S+\s*', '', text).strip()
        do_search(chat_id, query)
        return

    # ── /warning_all ─────────────────────────────────────────
    if text.startswith('/warning_all'):
        try:
            tg_send_plain(chat_id, '📋 전체 투자경고/위험 종목 조회 중...')
        except Exception:
            pass
        try:
            results = search_kind('')
        except Exception as e:
            tg_send_plain(chat_id, f'❌ KRX 조회 오류: {e}')
            return

        if not results:
            tg_send_plain(chat_id, '현재 투자경고/위험 지정 종목이 없습니다.')
            return

        warning = [r for r in results if r['level'] == '투자경고']
        risk    = [r for r in results if r['level'] == '투자위험']

        lines = [f'📋 *투자경고/위험 전체 목록* ({date.today().strftime("%m/%d")} 기준)\n']
        if risk:
            lines.append('🔴 *투자위험*')
            for r in risk:
                lines.append(f'• {r["stockName"]} ({r["designationDate"]})')
        if warning:
            lines.append('\n🟠 *투자경고*')
            for r in warning:
                lines.append(f'• {r["stockName"]} ({r["designationDate"]})')

        lines.append(f'\n총 {len(results)}개 종목 | /검색 종목명 으로 상세 조회')
        try:
            tg_send(chat_id, '\n'.join(lines))
        except Exception:
            tg_send_plain(chat_id, '\n'.join(lines))
        return

    # ── 알 수 없는 명령어 ────────────────────────────────────
    if text.startswith('/'):
        tg_send_plain(chat_id, '알 수 없는 명령어입니다.\n/help 로 사용법을 확인하세요.')
        return

    # ── 일반 텍스트: 개인 채팅만 종목 검색, 그룹은 무시 ─────
    chat_type = msg['chat'].get('type', 'private')
    if chat_type == 'private':
        do_search(chat_id, text)

# ── Vercel Handler ───────────────────────────────────────────
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        try:
            update = json.loads(body)
            process_update(update)
        except Exception as e:
            print(f'Update error: {e}')
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Telegram bot webhook is active.')

    def log_message(self, *args):
        pass
