#!/usr/bin/env python3
"""多链资金观察 / 已覆盖桥资金流。Python 3.10+，仅标准库。"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DAY = 86400
FREE = 'https://api.llama.fi'
STABLE = 'https://stablecoins.llama.fi'


class DataError(Exception):
    pass


def number(value):
    if value is None or isinstance(value, bool):
        raise DataError('缺失数值')
    try:
        n = float(value)
    except (ValueError, TypeError):
        raise DataError('无效数值') from None
    if not math.isfinite(n) or n < 0:
        raise DataError('数值非有限数或为负')
    return n


def get_json(url, payload=None):
    """错误不输出 URL，避免泄漏路径里的 API Key / bot token。"""
    last = 'request_failed'
    for attempt in range(3):
        try:
            body = None if payload is None else json.dumps(payload).encode()
            req = urllib.request.Request(url, data=body, headers={
                'User-Agent': 'chain-flow-monitor/1.0', 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last = f'HTTP_{exc.code}'
            if exc.code not in (429, 500, 502, 503, 504):
                break
            try:
                delay = min(45, max(1, float(exc.headers.get('Retry-After', 2 ** attempt))))
            except (ValueError, TypeError):
                delay = 2 ** attempt
        except (urllib.error.URLError, TimeoutError, OSError):
            last, delay = 'network_error', 2 ** attempt
        except (ValueError, UnicodeError):
            raise DataError('invalid_json') from None
        if attempt < 2:
            time.sleep(delay)
    raise DataError(last)


def rows(data):
    if not isinstance(data, list):
        raise DataError('expected_list')
    return data


def named(data, metric):
    result, errors, seen = {}, {}, set()
    for item in rows(data):
        name = item.get('name') if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise DataError('invalid_or_duplicate_chain')
        seen.add(name)
        try:
            if metric == 'tvl':
                value = number(item.get('tvl'))
            else:
                # 只取美元锚定稳定币的 USD 估值，不能混加其他币种原始单位。
                value = number(item.get('totalCirculatingUSD', {}).get('peggedUSD'))
            result[name] = value
        except (DataError, AttributeError):
            errors[name] = '字段缺失或无效，未计为0'
    return result, errors


def bridge_chains(data):
    if not isinstance(data, dict):
        raise DataError('invalid_bridge_catalog')
    names = set()
    for bridge in rows(data.get('bridges')):
        chains = bridge.get('chains')
        if not isinstance(chains, list) or not all(isinstance(c, str) for c in chains):
            raise DataError('invalid_bridge_chains')
        names.update(chains)
        dest = bridge.get('destinationChain')
        if isinstance(dest, str) and dest.lower() not in ('false', 'null', ''):
            names.add(dest)
    return sorted(names)


def daily_flow(data, now, deposit_direction=None):
    """同一 UTC 已完成自然日比较；缺日/过期不填 0，不冒充滚动24小时。"""
    target = int(now // DAY) * DAY - DAY
    by_date = {}
    for item in rows(data):
        stamp = int(number(item.get('date')))
        if stamp % DAY:
            raise DataError('non_daily_timestamp')
        if stamp in by_date:
            raise DataError('duplicate_day')
        by_date[stamp] = item
    if target not in by_date:
        return {'status': 'missing_completed_day', 'day': target,
                'latest_day': max(by_date, default=None)}
    item = by_date[target]
    deposit, withdraw = number(item.get('depositUSD')), number(item.get('withdrawUSD'))
    raw = {'day': target, 'raw_depositUSD': deposit, 'raw_withdrawUSD': withdraw}
    # 原始“deposit”可能以桥合约而非目的链为视角。未完成实测核对前不猜方向。
    if deposit_direction not in ('inflow', 'outflow'):
        return dict(raw, status='direction_unverified')
    incoming, outgoing = (deposit, withdraw) if deposit_direction == 'inflow' else (withdraw, deposit)
    return {'status': 'ok', 'day': target, 'in_usd': incoming,
            'out_usd': outgoing, 'net_usd': incoming - outgoing,
            'raw_depositUSD': deposit, 'raw_withdrawUSD': withdraw,
            'deposit_direction': deposit_direction}


def open_db(path):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY, ts INTEGER NOT NULL)')
    return db


def collect(now, only=None):
    result = {'collected_at': int(now), 'sources': {}, 'chains': {}, 'bridge_enabled': False}
    sources = {'tvl': FREE + '/v2/chains', 'stable_usd': STABLE + '/stablecoinchains',
               'dex': FREE + '/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true'}
    for metric, url in sources.items():
        try:
            data = get_json(url)
            errors = {}
            if metric == 'dex':
                result['dex_total24h'] = number(data.get('total24h'))
                result['dex_chains'] = data.get('allChains', [])
            else:
                values, errors = named(data, metric)
                for name, val in values.items():
                    if only and name.casefold() not in only:
                        continue
                    result['chains'].setdefault(name, {})[metric] = val
                for name, reason in errors.items():
                    if not only or name.casefold() in only:
                        result['chains'].setdefault(name, {}).setdefault('metric_errors', {})[metric] = reason
            result['sources'][metric] = {'status': 'partial' if errors else 'ok', 'fetched_at': int(time.time()),
                                         'source': url, 'upstream_updated_at': None,
                                         'reason': f'{len(errors)}条链字段缺失或无效' if errors else '',
                                         'invalid_rows': errors}
        except (DataError, AttributeError, TypeError) as exc:
            result['sources'][metric] = {'status': 'error', 'reason': str(exc) if isinstance(exc, DataError) else 'schema_error'}

    key = os.environ.get('DEFILLAMA_API_KEY', '').strip()
    if not key:
        result['sources']['bridges'] = {'status': 'not_connected', 'reason': '需要授权桥数据 API；未使用免费指标替代'}
        return result
    base = 'https://pro-api.llama.fi/' + urllib.parse.quote(key, safe='') + '/bridges'
    try:
        catalog = get_json(base + '/bridges')
        names = bridge_chains(catalog)
        if only:
            names = [n for n in names if n.casefold() in only]
        result['bridge_enabled'] = True
        result['sources']['bridges'] = {'status': 'ok', 'listed_chains': len(names),
                                        'upstream_updated_at': catalog.get('dataUpdatedAt')}
    except (DataError, AttributeError, TypeError) as exc:
        result['sources']['bridges'] = {'status': 'error', 'reason': str(exc) if isinstance(exc, DataError) else 'schema_error'}
        return result

    def one(name):
        try:
            data = get_json(base + '/bridgevolume/' + urllib.parse.quote(name, safe=''))
            return name, daily_flow(data, now, os.environ.get('BRIDGE_DEPOSIT_DIRECTION'))
        except (DataError, AttributeError, TypeError, OverflowError) as exc:
            return name, {'status': 'error', 'reason': str(exc) if isinstance(exc, DataError) else 'schema_error'}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for name, flow in pool.map(one, names):
            result['chains'].setdefault(name, {})['bridge'] = flow
    return result


def add_changes(current, previous):
    if not previous:
        current['comparison'] = '首次采样：还没有可比基线'
        return
    elapsed = current['collected_at'] - previous['collected_at']
    current['comparison'] = f'与上次快照相隔 {elapsed / 3600:.2f} 小时'
    if elapsed <= 0 or elapsed > 172800:
        current['comparison'] += '；间隔无效或超过48小时，不计算变化'
        return
    for name, metrics in current['chains'].items():
        old = previous.get('chains', {}).get(name, {})
        for field in ('tvl', 'stable_usd'):
            if field in metrics and field in old:
                metrics[field + '_change'] = metrics[field] - old[field]


def money(value):
    if value is None:
        return '未覆盖'
    return f'${value:,.0f}'


def render(data, limit=10):
    stamp = dt.datetime.fromtimestamp(data['collected_at'], dt.timezone.utc).isoformat()
    valid_count = sum(any(f in m for f in ('tvl', 'stable_usd', 'bridge')) for m in data['chains'].values())
    lines = ['多链资金观察', f'采集时间：{stamp}',
             f'本次目录 {len(data["chains"])} 条链，含指标或桥响应 {valid_count} 条（不代表全网所有链）',
             data.get('comparison', ''),
             '来源更新时间未提供的指标：仅能确认采集时间，不能保证实时。']
    for name, status in data['sources'].items():
        if status['status'] != 'ok':
            lines.append(f'数据状态 {name}：{status["status"]} / {status.get("reason", "")}')
    flows = [(n, m['bridge']) for n, m in data['chains'].items()
             if m.get('bridge', {}).get('status') == 'ok']
    if flows:
        date = dt.datetime.fromtimestamp(flows[0][1]['day'], dt.timezone.utc).date()
        lines += ['', f'跨链桥净流向：{date} UTC 已完成自然日',
                  '仅已覆盖桥；日历结束不代表供应商采集完整，数值可能修订。']
        for title, positive in [('净流入', True), ('净流出', False)]:
            candidates = [(n, f) for n, f in flows if (f['net_usd'] > 0 if positive else f['net_usd'] < 0)]
            candidates.sort(key=lambda x: abs(x[1]['net_usd']), reverse=True)
            lines.append(title + '排行：')
            lines.extend(f'{n}：净额 {money(f["net_usd"])}；入 {money(f["in_usd"])} / 出 {money(f["out_usd"])}'
                         for n, f in candidates[:limit])
            if not candidates:
                lines.append('无符合条件的有效记录')
        problems = [(n, m['bridge']['status']) for n, m in data['chains'].items()
                    if 'bridge' in m and m['bridge']['status'] != 'ok']
        lines.append(f'有效桥日数据 {len(flows)} 条链；缺日或错误 {len(problems)} 条链（完整清单见 report.json）。')
    else:
        lines += ['', '跨链净流入/净流出：暂无有效数据，不生成排名。']
    unverified = sum(m.get('bridge', {}).get('status') == 'direction_unverified' for m in data['chains'].values())
    if unverified:
        lines.append(f'{unverified}条链已读取桥原始数据，但流入/出方向尚未核验，不生成方向排名。')
    for field, title in [('tvl', 'TVL'), ('stable_usd', '美元稳定币')]:
        changes = [(n, m[field + '_change']) for n, m in data['chains'].items() if field + '_change' in m]
        if changes:
            lines += ['', title + '变化（非净流入，按变化绝对值排序）：']
            nonzero = [(n, v) for n, v in changes if abs(v) >= 1]
            lines.extend(f'{n}：{money(v)}' for n, v in sorted(nonzero, key=lambda x: abs(x[1]), reverse=True)[:limit])
            if not nonzero:
                lines.append('未观察到至少1美元的快照变化；可能仍为同一缓存，不代表没有资金活动。')
        else:
            values = [(n, m[field]) for n, m in data['chains'].items() if field in m]
            lines += ['', title + '规模排行（非资金流）：']
            lines.extend(f'{n}：{money(v)}' for n, v in sorted(values, key=lambda x: x[1], reverse=True)[:limit])
            if not values:
                lines.append('无有效数据')
    if 'dex_total24h' in data:
        lines += ['', f'已覆盖 DEX 全局24h交易量：{money(data["dex_total24h"])}（非净流入）']
    lines += ['', '重点链覆盖（名称精确匹配，不合并 Hyperliquid L1 与 HyperEVM）：']
    for name in ['Ethereum', 'Solana', 'BSC', 'Base', 'Hyperliquid L1', 'HyperEVM', 'Robinhood Chain', 'Arc']:
        metrics = data['chains'].get(name, {})
        lines.append(f'{name}：TVL {money(metrics.get("tvl"))}；稳定币 {money(metrics.get("stable_usd"))}；桥 {metrics.get("bridge", {}).get("status", "未覆盖/未接入")}')
    lines += ['', 'TVL受价格影响；稳定币规模受铸造/销毁、桥接、价格及统计范围影响。',
              '不含全网钱包逐笔追踪、交易所内部转账或链A→链B配对路径。',
              '数据：DefiLlama。完整数据和错误清单保存在 report.json。']
    return '\n'.join(lines)


def atomic_write(path, text):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def send_report(text, db, now):
    token, chat = os.environ.get('FLOW_TELEGRAM_BOT_TOKEN', ''), os.environ.get('FLOW_TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        raise DataError('缺少 FLOW_TELEGRAM_BOT_TOKEN 或 FLOW_TELEGRAM_CHAT_ID')
    # 以 UTF-16 单元保守分段，避免 Telegram 长消息限制。
    parts, part, size = [], '', 0
    for ch in text:
        units = len(ch.encode('utf-16-le')) // 2
        if size + units > 3300:
            parts.append(part)
            part, size = '', 0
        part += ch
        size += units
    if part:
        parts.append(part)
    for index, part in enumerate(parts):
        ident = hashlib.sha256((chat + '\n' + str(index) + '\n' + part).encode()).hexdigest()
        if db.execute('SELECT 1 FROM sent WHERE id=?', (ident,)).fetchone():
            continue
        answer = get_json(f'https://api.telegram.org/bot{token}/sendMessage',
                          {'chat_id': chat, 'text': part, 'disable_web_page_preview': True})
        if not isinstance(answer, dict) or answer.get('ok') is not True:
            raise DataError('Telegram 未确认发送成功')
        db.execute('INSERT INTO sent VALUES (?,?)', (ident, int(now)))
        db.commit()
        time.sleep(1)


def cycle(directory, send=False, only=None):
    now = int(time.time())
    with open_db(directory / 'monitor.sqlite3') as db:
        row = db.execute('SELECT body FROM samples ORDER BY ts DESC LIMIT 1').fetchone()
        previous = json.loads(row[0]) if row else None
        data = collect(now, only)
        add_changes(data, previous)
        text = render(data)
        body = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        atomic_write(directory / 'report.json', body)
        atomic_write(directory / 'report.txt', text)
        # 完全失败不污染后续的比较基线。
        if data['chains']:
            db.execute('INSERT OR REPLACE INTO samples VALUES (?,?)', (now, body))
            db.execute('DELETE FROM samples WHERE ts < ?', (now - 30 * DAY,))
            db.execute('DELETE FROM sent WHERE ts < ?', (now - 30 * DAY,))
            db.commit()
        print(text, flush=True)
        if send:
            send_report(text, db, now)
            health = {'last_sent_at': int(time.time()), 'collected_at': now,
                      'tvl_chains': sum('tvl' in v for v in data['chains'].values()),
                      'stablecoin_chains': sum('stable_usd' in v for v in data['chains'].values()),
                      'bridges_status': data['sources']['bridges']['status']}
            atomic_write(directory / 'health.json', json.dumps(health))
            print('TELEGRAM_REPORT_OK', json.dumps(health), flush=True)
        return 0 if data['chains'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true', help='明确启用电报发送；默认仅本地输出')
    parser.add_argument('--loop', action='store_true', help='持续运行')
    parser.add_argument('--interval', type=int, default=3600, help='轮询间隔秒，默认1小时')
    parser.add_argument('--data-dir', default=str(Path(__file__).resolve().parent / 'data'))
    parser.add_argument('--only', help='仅诊断指定名称的链，逗号分隔；默认不限制链')
    args = parser.parse_args()
    if args.send and not all(os.environ.get(k, '').strip() for k in ('FLOW_TELEGRAM_BOT_TOKEN', 'FLOW_TELEGRAM_CHAT_ID')):
        parser.error('发送模式需要独立的 FLOW_TELEGRAM_BOT_TOKEN 和 FLOW_TELEGRAM_CHAT_ID；不会使用旧机器人配置')
    if args.interval < 300:
        parser.error('最低间隔300秒，聚合数据不适合逐秒轮询')
    directory = Path(args.data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # Linux 服务器单实例锁，避免重复采样和重复推送。
    import fcntl
    with (directory / 'monitor.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DataError('该数据目录已有运行中的监控实例') from None
        only = {v.strip().casefold() for v in args.only.split(',')} if args.only else None
        while True:
            start = time.monotonic()
            try:
                code = cycle(directory, args.send, only)
            except (DataError, OSError, sqlite3.Error) as exc:
                print('监控失败：' + (str(exc) if isinstance(exc, DataError) else type(exc).__name__), file=sys.stderr)
                code = 2
            if not args.loop:
                return code
            time.sleep(max(5, args.interval - (time.monotonic() - start)))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
