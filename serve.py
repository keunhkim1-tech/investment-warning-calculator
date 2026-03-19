#!/usr/bin/env python3
"""
투자경고 해제일 계산기 - 프록시 서버
- 정적 파일 서빙 (index.html)
- KRX KIND 투자경고 현황 프록시 (/api/warn-search)
- 네이버 종목코드 검색 (/api/stock-code)
- 네이버 일별 주가 + 기준가 계산 (/api/stock-price)
"""
import http.server
import socketserver
import os
import re
import json
import urllib.request
import urllib.parse
import urllib.error
from xml.etree import ElementTree as ET

PORT = 5173
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

HEADERS_COMMON = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
}


# ── KRX KIND ──────────────────────────────────────────────

def fetch_kind_page(menu_index: str, page: int = 1) -> str:
    from datetime import date, timedelta
    end_date   = date.today().strftime('%Y%m%d')
    start_date = (date.today() - timedelta(days=365)).strftime('%Y%m%d')

    params = urllib.parse.urlencode({
        'method': 'investattentwarnriskySub', 'menuIndex': menu_index,
        'marketType': '', 'searchCorpName': '',
        'startDate': start_date, 'endDate': end_date,
        'pageIndex': str(page), 'currentPageSize': '100',
        'orderMode': '3', 'orderStat': 'D',
    })
    url = f'https://kind.krx.co.kr/investwarn/investattentwarnrisky.do?{params}'
    headers = {**HEADERS_COMMON, 'Accept': 'text/html,application/xhtml+xml',
               'Referer': 'https://kind.krx.co.kr/'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_kind_html(html: str, level_name: str) -> list:
    results = []
    tbody_m = re.search(r'<tbody>(.*?)</tbody>', html, re.DOTALL)
    if not tbody_m:
        return results
    tbody = tbody_m.group(1)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody, re.DOTALL)
    for row in rows:
        name_m = re.search(r'<td\s+title="([^"]+)"', row)
        if not name_m:
            continue
        stock_name = name_m.group(1).strip()
        if not stock_name:
            continue
        dates = re.findall(
            r'<td[^>]*class="[^"]*txc[^"]*"[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</td>', row)
        if not dates:
            continue
        results.append({
            'level': level_name,
            'stockName': stock_name,
            'designationDate': dates[0],
        })
    return results


def search_kind(stock_name: str) -> list:
    level_map = {'2': '투자경고', '3': '투자위험'}
    all_results = []
    for menu_idx in ['2', '3']:
        try:
            html = fetch_kind_page(menu_idx)
            rows = parse_kind_html(html, level_map[menu_idx])
            if stock_name:
                rows = [r for r in rows if stock_name in r['stockName']]
            all_results.extend(rows)
        except Exception as e:
            print(f'[WARN] KIND menu={menu_idx}: {e}')
    all_results.sort(key=lambda x: x.get('designationDate', ''), reverse=True)
    return all_results


# ── 네이버 주가 ────────────────────────────────────────────

def naver_stock_code(name: str) -> list:
    """종목명 → [{code, name, market}, ...] (네이버 자동완성)"""
    params = urllib.parse.urlencode({'q': name, 'target': 'stock'})
    url = f'https://ac.stock.naver.com/ac?{params}'
    req = urllib.request.Request(url, headers=HEADERS_COMMON)
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    items = data.get('items', [])
    return [
        {'code': it['code'], 'name': it['name'], 'market': it.get('typeName', '')}
        for it in items
    ]


def naver_daily_prices(code: str, count: int = 20) -> list:
    """
    네이버 fchart API → 일별 종가 리스트 (최신순)
    반환: [{'date': 'YYYY-MM-DD', 'close': 12345}, ...]
    """
    url = (f'https://fchart.stock.naver.com/sise.nhn'
           f'?symbol={code}&timeframe=day&count={count}&requestType=0')
    headers = {**HEADERS_COMMON, 'Referer': 'https://finance.naver.com/'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode('euc-kr', errors='replace')

    root = ET.fromstring(raw)
    prices = []
    for item in root.iter('item'):
        parts = item.get('data', '').split('|')
        if len(parts) < 5:
            continue
        date_raw, close_raw = parts[0], parts[4]
        if not close_raw or close_raw == '0':
            continue
        d = date_raw  # YYYYMMDD
        formatted = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        prices.append({'date': formatted, 'close': int(close_raw)})

    # 오래된 순 → 최신 순으로 역정렬
    prices.reverse()
    return prices


def calc_thresholds(prices: list) -> dict:
    """
    prices: 최신순 리스트 [0]=T, [1]=T-1, ... [5]=T-5, ... [15]=T-15
    반환: 3가지 기준가 + 현재 충족 여부
    """
    if len(prices) < 16:
        return {'error': f'데이터 부족 ({len(prices)}일치, 최소 16일 필요)'}

    t_close   = prices[0]['close']
    t_date    = prices[0]['date']
    t5_close  = prices[5]['close']
    t5_date   = prices[5]['date']
    t15_close = prices[15]['close']
    t15_date  = prices[15]['date']

    # 최근 15일(T~T-14) 최고가
    recent15  = prices[:15]
    max15     = max(p['close'] for p in recent15)
    max15_date = next(p['date'] for p in recent15 if p['close'] == max15)

    # 기준가 계산
    thresh1 = round(t5_close  * 1.45)   # T-5 × 145%
    thresh2 = round(t15_close * 1.75)   # T-15 × 175%
    thresh3 = max15                      # 최근 15일 최고가

    # 조건 충족 여부 (True = 경고 유지 중)
    cond1 = t_close >= thresh1
    cond2 = t_close >= thresh2
    cond3 = t_close >= thresh3

    return {
        'tClose':   t_close,   'tDate':   t_date,
        't5Close':  t5_close,  't5Date':  t5_date,  'thresh1': thresh1, 'cond1': cond1,
        't15Close': t15_close, 't15Date': t15_date, 'thresh2': thresh2, 'cond2': cond2,
        'max15':    max15,     'max15Date': max15_date, 'thresh3': thresh3, 'cond3': cond3,
        'allMet':  cond1 and cond2 and cond3,
    }


# ── HTTP 핸들러 ────────────────────────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, fmt, *args):
        print(f'[{self.address_string()}] {fmt % args}')

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        # ── /api/warn-search?name= ─────────────────────────
        if parsed.path == '/api/warn-search':
            name = qs.get('name', [''])[0].strip()
            if not name:
                self.send_json({'error': '종목명을 입력하세요.'}, 400); return
            try:
                results = search_kind(name)
                self.send_json({'results': results, 'query': name})
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        # ── /api/stock-code?name= ──────────────────────────
        if parsed.path == '/api/stock-code':
            name = qs.get('name', [''])[0].strip()
            if not name:
                self.send_json({'error': '종목명을 입력하세요.'}, 400); return
            try:
                items = naver_stock_code(name)
                self.send_json({'items': items})
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        # ── /api/stock-price?code= ─────────────────────────
        if parsed.path == '/api/stock-price':
            code = qs.get('code', [''])[0].strip()
            if not code:
                self.send_json({'error': '종목코드를 입력하세요.'}, 400); return
            try:
                prices  = naver_daily_prices(code, count=20)
                thresholds = calc_thresholds(prices)
                self.send_json({'prices': prices[:16], 'thresholds': thresholds})
            except Exception as e:
                self.send_json({'error': str(e)}, 500)
            return

        # ── 정적 파일 서빙 ──────────────────────────────────
        super().do_GET()


if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), Handler) as httpd:
        print(f'✅ 서버 실행: http://localhost:{PORT}')
        httpd.serve_forever()
