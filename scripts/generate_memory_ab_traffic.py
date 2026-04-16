import argparse
import json
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple


BASE_QUERIES = [
    "我上次的退款工单处理到哪一步了？",
    "我之前偏好电子发票，继续用这个",
    "我的收货地址还是上海浦东那个",
    "上次你建议我先提交售后单，现在进度呢",
    "我的订单为什么还没发货",
    "退款审核通过后多久到账",
    "帮我总结下我们刚刚确认过的处理方案",
    "我想继续沿用之前确认的售后处理流程",
    "我的默认联系人信息还是之前那个吗",
    "请按我上次偏好给我推荐处理路径",
]

SCENARIOS = [
    "支付失败",
    "退款进度",
    "发票开具",
    "物流延迟",
    "地址变更",
    "售后退货",
    "换货申请",
    "优惠券异常",
    "订单取消",
    "工单升级",
]

STYLES = [
    "请给我明确下一步",
    "尽量简短一点",
    "给我一个可执行清单",
    "按之前约定来",
    "先告诉我当前状态",
]

CITY_FACTS = [
    ("上海浦东新区世纪大道88号", "张三", "13800001234"),
    ("北京朝阳区建国路99号", "李四", "13900005678"),
    ("深圳南山区科技园1号", "王五", "13700004567"),
    ("杭州西湖区文三路66号", "赵六", "13600007890"),
]

JOURNEY_TEMPLATES = [
    "请记住：我的收货地址是{address}。",
    "请记住：我偏好电子发票，默认抬头是个人。",
    "请记住：我的联系人是{name}，手机号是{phone}。",
    "根据我刚才提供的信息，帮我确认地址和发票偏好。",
    "我刚刚告诉你的联系人手机号是什么？",
    "按我之前确认的信息，给我本次售后处理建议。",
]


def build_queries(scale: int) -> List[str]:
    queries: List[str] = list(BASE_QUERIES)
    for s in SCENARIOS:
        for st in STYLES:
            queries.append(f"{s}这个问题按我之前偏好怎么处理？{st}")
            queries.append(f"{s}我上次咨询过，当前进展是什么？{st}")
    random.shuffle(queries)
    if scale <= 0:
        return queries
    return queries[:scale]


def build_journey_query(session_id: int, step: int) -> str:
    address, name, phone = CITY_FACTS[session_id % len(CITY_FACTS)]
    tpl = JOURNEY_TEMPLATES[step % len(JOURNEY_TEMPLATES)]
    return f"[session={session_id} step={step}] " + tpl.format(address=address, name=name, phone=phone)


def post_chat(url: str, payload: Dict, timeout_seconds: float, retries: int) -> Tuple[bool, float, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    max_attempts = max(1, retries + 1)
    last_err = ""
    start = time.perf_counter()
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=max(1.0, timeout_seconds)) as resp:
                _ = resp.read()  # skip unnecessary parsing for speed
                latency_ms = (time.perf_counter() - start) * 1000
                return True, latency_ms, ""
        except Exception as exc:
            last_err = str(exc)
            if attempt < max_attempts:
                time.sleep(min(1.5, 0.2 * attempt))
                continue
            break
    latency_ms = (time.perf_counter() - start) * 1000
    return False, latency_ms, last_err


def make_payload(
    query: str,
    memory_enabled: bool,
    idx: int,
    tenant: str,
    user_prefix: str,
    session_pool_size: int,
) -> Dict:
    mode = "on" if memory_enabled else "off"
    sid = idx % max(1, session_pool_size)
    return {
        "conversation_id": f"exp_mem_{mode}_s{sid}",
        "user_id": f"{user_prefix}_{idx % 50}",
        "tenant_id": tenant,
        "actor_type": "user",
        "channel": "web",
        "query": query,
        "history": [],
        "memory_enabled": memory_enabled,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ON/OFF memory traffic quickly.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/chat")
    parser.add_argument("--count", type=int, default=200, help="Total ON+OFF request pairs.")
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--query-scale", type=int, default=100, help="How many unique queries to sample.")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=120,
        help="Split requests into batches to avoid overload.",
    )
    parser.add_argument(
        "--batch-sleep-ms",
        type=int,
        default=800,
        help="Sleep between batches.",
    )
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--user-prefix", default="u_ab")
    parser.add_argument(
        "--pattern",
        choices=["random", "journey"],
        default="journey",
        help="journey: memory-friendly conversational turns; random: mixed random queries",
    )
    parser.add_argument(
        "--journey-steps",
        type=int,
        default=6,
        help="Turns per session in journey mode.",
    )
    parser.add_argument(
        "--session-pool-size",
        type=int,
        default=20,
        help="Reuse conversation ids to increase chance of memory hit.",
    )
    args = parser.parse_args()

    tasks: List[Tuple[str, Dict]] = []
    if args.pattern == "random":
        queries = build_queries(args.query_scale)
        if not queries:
            raise RuntimeError("No queries generated.")
        for i in range(args.count):
            q = queries[i % len(queries)]
            tasks.append(
                (
                    "on",
                    make_payload(
                        q,
                        True,
                        i,
                        args.tenant,
                        args.user_prefix,
                        int(args.session_pool_size),
                    ),
                )
            )
            tasks.append(
                (
                    "off",
                    make_payload(
                        q,
                        False,
                        i,
                        args.tenant,
                        args.user_prefix,
                        int(args.session_pool_size),
                    ),
                )
            )
        random.shuffle(tasks)
    else:
        session_pool = max(1, int(args.session_pool_size))
        journey_steps = max(2, int(args.journey_steps))
        for i in range(args.count):
            sid = i % session_pool
            step = (i // session_pool) % journey_steps
            q = build_journey_query(sid, step)
            tasks.append(
                (
                    "on",
                    make_payload(
                        q,
                        True,
                        i,
                        args.tenant,
                        args.user_prefix,
                        session_pool,
                    ),
                )
            )
            tasks.append(
                (
                    "off",
                    make_payload(
                        q,
                        False,
                        i,
                        args.tenant,
                        args.user_prefix,
                        session_pool,
                    ),
                )
            )
    start_all = time.perf_counter()
    stats = {
        "on_ok": 0,
        "off_ok": 0,
        "on_fail": 0,
        "off_fail": 0,
        "on_latencies": [],
        "off_latencies": [],
    }

    batch_size = max(1, int(args.batch_size))
    for bstart in range(0, len(tasks), batch_size):
        batch = tasks[bstart : bstart + batch_size]
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            future_to_mode = {
                pool.submit(
                    post_chat,
                    args.url,
                    payload,
                    float(args.timeout_seconds),
                    int(args.retries),
                ): mode
                for mode, payload in batch
            }
            for fut in as_completed(future_to_mode):
                mode = future_to_mode[fut]
                ok, latency, err = fut.result()
                if mode == "on":
                    if ok:
                        stats["on_ok"] += 1
                    else:
                        stats["on_fail"] += 1
                    stats["on_latencies"].append(latency)
                else:
                    if ok:
                        stats["off_ok"] += 1
                    else:
                        stats["off_fail"] += 1
                    stats["off_latencies"].append(latency)
                if (not ok) and err:
                    print(f"[warn] {mode} request failed: {err}")
        if bstart + batch_size < len(tasks):
            time.sleep(max(0.0, args.batch_sleep_ms / 1000.0))

    elapsed = time.perf_counter() - start_all

    def _avg(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    print("=== Memory A/B traffic done ===")
    print(f"total_requests={len(tasks)} elapsed_s={round(elapsed, 2)}")
    print(f"on_ok={stats['on_ok']} on_fail={stats['on_fail']} on_avg_ms={_avg(stats['on_latencies'])}")
    print(f"off_ok={stats['off_ok']} off_fail={stats['off_fail']} off_avg_ms={_avg(stats['off_latencies'])}")
    print("Now open Grafana panels and compare memory_enabled=true vs false.")


if __name__ == "__main__":
    main()
