#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股盘前简报 · 数据抓取
数据源：腾讯行情(指数/VIX/K线) · CBOE(VIX 历史/实时) · 蛋卷(PE 分位) · frankfurter(汇率)
输出：data.json（供 index.html 读取）
"""
import requests, json, csv, io, os, sys, datetime, time
from urllib.parse import quote

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'}
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data.json')


def get(url, enc='utf-8', tries=3, timeout=20, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=h, timeout=timeout)
            if r.status_code == 200:
                return r.content.decode(enc, 'ignore')
            last = f'HTTP {r.status_code}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
        time.sleep(1.5 * (i + 1))
    print(f'[warn] 抓取失败 {url[:80]} -> {last}', flush=True)
    return None


# ---------- 1. 腾讯实时行情：纳指100 / 标普500 ----------
def fetch_quotes():
    txt = get('https://qt.gtimg.cn/q=usNDX,us.INX', enc='gbk')
    out = {}
    if not txt:
        return out
    for line in txt.split(';'):
        line = line.strip()
        if not line.startswith('v_'):
            continue
        key = line[2:line.index('=')]
        f = line[line.index('"') + 1:line.rindex('"')].split('~')
        try:
            rec = {
                'name': f[1], 'price': float(f[3]), 'prev': float(f[4]),
                'chg': float(f[31]), 'pct': float(f[32]),
                'date': f[30][:10],
            }
            out['ndx' if key == 'usNDX' else 'spx'] = rec
        except (ValueError, IndexError) as e:
            print(f'[warn] 解析行情 {key} 失败 {e}', flush=True)
    return out


# ---------- 2. CBOE VIX 实时（延迟报价），失败则走 Yahoo/FRED 备用链 ----------
def _vix_out(cur, prev):
    if prev <= 0:
        prev = cur
    return {'value': cur, 'chg': round(cur - prev, 2),
            'pct': round((cur / prev - 1) * 100, 2) if prev else 0}


def fetch_vix_now():
    # 2a. CBOE 官方延迟报价
    txt = get('https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json')
    if txt:
        try:
            d = json.loads(txt)['data']
            cur = float(d['current_price'])
            chg = float(d.get('price_change') or 0)
            prev = float(d.get('prev_day_close') or (cur - chg))
            if prev <= 0:
                prev = cur - chg
            return {'value': cur, 'chg': chg, 'pct': chg / prev * 100 if prev else 0}
        except Exception as e:
            print(f'[warn] VIX 实时解析失败 {e}', flush=True)
    # 2b. Yahoo chart 兜底
    for host in ('query1', 'query2'):
        txt = get(f'https://{host}.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=1d')
        if not txt:
            continue
        try:
            j = json.loads(txt)['chart']['result'][0]
            closes = [float(x) for x in j['indicators']['quote'][0]['close'] if x]
            cur = float(j['meta']['regularMarketPrice'])
            if cur and closes:
                prev = closes[-1] if abs(closes[-1] - cur) > 1e-6 else (closes[-2] if len(closes) > 1 else cur)
                return _vix_out(cur, prev)
        except Exception as e:
            print(f'[warn] Yahoo VIX ({host}) 失败 {e}', flush=True)
    return None


# ---------- 3. VIX 历史（1990 至今）→ 分位；CBOE → Yahoo → FRED 三级备用 ----------
def _vix_rows_from_fred():
    txt = get('https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS')
    rows = []
    if txt:
        for r in csv.DictReader(io.StringIO(txt)):
            d = (r.get('DATE') or '').strip()
            v = (r.get('VIXCLS') or '').strip()
            if not d or v in ('', '.'):
                continue
            try:
                rows.append((datetime.date.fromisoformat(d), float(v)))
            except ValueError:
                continue
    return rows


def _vix_rows_from_yahoo():
    txt = get('https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=10y&interval=1d')
    rows = []
    if txt:
        try:
            j = json.loads(txt)['chart']['result'][0]
            ts = j['timestamp']
            closes = j['indicators']['quote'][0]['close']
            for t, c in zip(ts, closes):
                if not c:
                    continue
                d = datetime.date.fromtimestamp(t, datetime.timezone.utc)
                rows.append((d, float(c)))
        except Exception as e:
            print(f'[warn] Yahoo VIX 历史失败 {e}', flush=True)
    return rows


def fetch_vix_history():
    txt = get('https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv')
    rows = []
    if txt:
        for r in csv.DictReader(io.StringIO(txt)):
            try:
                d = datetime.datetime.strptime(r['DATE'], '%m/%d/%Y').date()
                rows.append((d, float(r['CLOSE'])))
            except (ValueError, KeyError):
                continue
    if len(rows) > 500:
        return rows
    print('[warn] CBOE VIX 历史不可用，尝试 Yahoo…', flush=True)
    rows = _vix_rows_from_yahoo()
    if len(rows) > 500:
        return sorted(rows)
    print('[warn] Yahoo VIX 历史不可用，尝试 FRED…', flush=True)
    rows = _vix_rows_from_fred()
    return sorted(rows)


def pct_rank(series, value):
    """value 在 series 中的百分位（<= value 的占比）"""
    if not series:
        return 0
    return sum(1 for x in series if x <= value) / len(series) * 100


def vix_level(p10):
    """按近 10 年分位分 5 档：10/30/70/90 边界"""
    if p10 < 10:
        return '极度乐观', 0
    if p10 < 30:
        return '偏乐观', 1
    if p10 < 70:
        return '正常', 2
    if p10 < 90:
        return '谨慎', 3
    return '恐慌', 4


# ---------- 4. 蛋卷：PE(TTM) + 近 5 年分位 ----------
def fetch_pe(code):
    detail = get(f'https://danjuanfunds.com/djapi/index_eva/detail/{code}')
    pe = None
    if detail:
        try:
            pe = float(json.loads(detail)['data']['pe'])
        except Exception:
            pass
    hist = get(f'https://danjuanfunds.com/djapi/index_eva/pe_history/{code}?day=5y')
    pes = []
    if hist:
        try:
            arr = json.loads(hist)['data']['index_eva_pe_growths']
            pes = [x['pe'] for x in arr if x.get('pe')]
        except Exception as e:
            print(f'[warn] PE 历史 {code} 解析失败 {e}', flush=True)
    if pe is None and pes:
        pe = pes[-1]
    if not pe:
        return None
    return {'pe': round(pe, 2), 'pct': round(pct_rank(pes, pe)) if pes else None,
            'count': len(pes)}


def pe_level(p):
    if p is None:
        return '—'
    if p < 20:
        return '偏便宜'
    if p < 70:
        return '中性'
    if p < 85:
        return '偏高'
    return '偏贵'


# ---------- 5. 腾讯 K 线：近 10 日 / 回撤 ----------
def fetch_kline(code, n=420):
    url = (f'https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get?'
           f'param={quote(code)},day,,,{n},qfq')
    txt = get(url)
    if not txt:
        return []
    try:
        j = json.loads(txt)['data'][code]
        k = j.get('day') or j.get('qfqday') or []
        return [(x[0], float(x[2])) for x in k]  # (date, close)
    except Exception as e:
        print(f'[warn] K线解析 {code} 失败 {e}', flush=True)
        return []


def _ddate(s):
    return datetime.date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def analyze_drawdown(closes, threshold=-0.05):
    """运行峰值状态机：返回当前未修复回撤 + 上一轮已修复回撤
    当前回撤天数按交易日计；上轮修复天数按自然日计（与截图口径一致）"""
    res = {'cur': None, 'peak_date': None, 'days': None,
           'prev': None, 'prev_range': None, 'prev_fix': None}
    if len(closes) < 30:
        return res

    peak, peak_d = closes[0][1], closes[0][0]
    trough, trough_d = None, None
    in_dd = False
    events = []
    for i, (d, v) in enumerate(closes[1:], 1):
        if not in_dd:
            if v >= peak:
                peak, peak_d = v, d
            elif (v / peak - 1) <= threshold:
                in_dd, trough, trough_d = True, v, d
        else:
            if v < trough:
                trough, trough_d = v, d
            if v >= peak:  # 修复：重新站上前高
                events.append({
                    'dd': round((trough / peak - 1) * 100, 2),
                    'peak': peak_d, 'trough': trough_d, 'fix': d,
                    'fix_days': (_ddate(d) - _ddate(trough_d)).days,
                })
                in_dd, peak, peak_d = False, v, d
                trough, trough_d = None, None

    # 当前（未修复）回撤
    if in_dd:
        res['cur'] = round((closes[-1][1] / peak - 1) * 100, 2)
        res['peak_date'] = peak_d
        res['days'] = sum(1 for d, _ in closes if d > peak_d)
    else:
        res['cur'] = 0.0
        res['peak_date'] = peak_d
        res['days'] = 0

    # 上一轮已修复回撤（取最近一次）
    if events:
        e = events[-1]
        res['prev'] = e['dd']
        res['prev_range'] = f"{e['peak'][2:4]}-{e['peak'][5:7]} → {e['trough'][2:4]}-{e['trough'][5:7]}"
        res['prev_fix'] = e['fix_days']
    return res


# ---------- 6. 汇率 ----------
def fetch_fx():
    # 取最近 30 天区间（避免跨年后固定起始日期失效）
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
    start = today - datetime.timedelta(days=30)
    url = (f'https://api.frankfurter.app/{start.isoformat()}..{today.isoformat()}'
           f'?from=USD&to=CNY')
    txt = get(url)
    if not txt:
        return None
    try:
        rates = json.loads(txt)['rates']
        days = sorted(rates.keys())
        last, prev = days[-1], days[-2]
        v, pv = rates[last]['CNY'], rates[prev]['CNY']
        return {'value': round(v, 4), 'pct': round((v / pv - 1) * 100, 2),
                'date': last}
    except Exception as e:
        print(f'[warn] 汇率解析失败 {e}', flush=True)
        return None


# ---------- 主流程 ----------
def main():
    print('抓取中...', flush=True)
    q = fetch_quotes()
    print('  行情:', {k: (v['price'], v['pct']) for k, v in q.items()}, flush=True)

    vix = fetch_vix_now()
    vh = fetch_vix_history()
    print(f'  VIX 实时: {vix} | 历史 {len(vh)} 条', flush=True)

    # 实时源全挂时，用历史最后一日收盘兜底（通常为上一交易日）
    if vix is None and len(vh) >= 2:
        last, prev = vh[-1], vh[-2]
        vix = {'value': last[1], 'chg': round(last[1] - prev[1], 2),
               'pct': round((last[1] / prev[1] - 1) * 100, 2),
               'src': f'历史收盘 {last[0].isoformat()}'}
        print(f'  [fallback] VIX 改用历史收盘 {last[0]} = {last[1]}', flush=True)

    # VIX 分位（把当前值并入历史最后）
    if vh and vix:
        cutoff10 = vh[-1][0] - datetime.timedelta(days=3653)
        cutoff3 = vh[-1][0] - datetime.timedelta(days=1096)
        cutoff1 = vh[-1][0] - datetime.timedelta(days=365)
        s10 = [v for d, v in vh if d >= cutoff10]
        s3 = [v for d, v in vh if d >= cutoff3]
        s1 = [v for d, v in vh if d >= cutoff1]
        p10 = round(pct_rank(s10, vix['value']))
        vix['pct10y'] = p10
        vix['pct3y'] = round(pct_rank(s3, vix['value']))
        vix['pct1y'] = round(pct_rank(s1, vix['value']))
        vix['level'], vix['level_idx'] = vix_level(p10)
        # 用 CBOE 历史前一交易日校准涨跌（实时接口的 prev_day_close 有时缺失）
        if len(vh) >= 2 and abs(vh[-1][1] - vix['value']) < 0.01:
            vix['chg'] = round(vh[-1][1] - vh[-2][1], 2)
            vix['pct'] = round(vix['chg'] / vh[-2][1] * 100, 2)
        elif len(vh) >= 1 and vh[-1][0] < datetime.date.today():
            vix['chg'] = round(vix['value'] - vh[-1][1], 2)
            vix['pct'] = round(vix['chg'] / vh[-1][1] * 100, 2)

    pe_ndx, pe_spx = fetch_pe('NDX'), fetch_pe('SP500')
    print('  PE:', pe_ndx, pe_spx, flush=True)

    kndx, kspx = fetch_kline('usNDX'), fetch_kline('us.INX')
    dd_ndx = analyze_drawdown(kndx)
    dd_spx = analyze_drawdown(kspx)
    print('  回撤:', dd_ndx, dd_spx, flush=True)

    fx = fetch_fx()
    print('  汇率:', fx, flush=True)

    # 日期标签
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    wk = '一二三四五六日'[now.weekday()]
    date_label = f'{now.month} 月 {now.day} 日 周{wk}'
    data_date = (q.get('ndx') or {}).get('date', '')[-5:] or now.strftime('%m-%d')

    series = {
        'ndx': [{'d': d[5:].replace('-', '-'), 'v': round(v)} for d, v in kndx[-10:]],
        'spx': [{'d': d[5:].replace('-', '-'), 'v': round(v)} for d, v in kspx[-10:]],
    }

    data = {
        'generated_at': now.strftime('%Y-%m-%d %H:%M'),
        'date_label': date_label,
        'data_date': data_date.replace('-', '-'),
        'ndx': q.get('ndx'), 'spx': q.get('spx'),
        'vix': vix, 'fx': fx,
        'pe': {
            'ndx': {**(pe_ndx or {}), 'level': pe_level((pe_ndx or {}).get('pct'))},
            'spx': {**(pe_spx or {}), 'level': pe_level((pe_spx or {}).get('pct'))},
        },
        'dd': {'ndx': dd_ndx, 'spx': dd_spx},
        'series': series,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'✅ 已写出 {OUT}', flush=True)
    print(json.dumps({k: data[k] for k in ('date_label', 'data_date', 'ndx', 'spx', 'vix', 'fx', 'pe', 'dd')},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
