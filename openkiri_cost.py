"""Long-position cost scenarios, never a broker fee or execution guarantee."""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from fastapi import HTTPException


def value(payload, key, default=None, *, maximum=None, positive=False):
    raw = payload.get(key)
    raw = default if raw is None or raw == "" else raw
    try:
        result = Decimal(str(raw))
        if not result.is_finite() or result < 0 or (positive and result == 0) or (maximum is not None and result > Decimal(str(maximum))):
            raise ValueError
        return result
    except (ValueError, ArithmeticError):
        raise HTTPException(422, detail=f"Invalid cost input: {key}") from None


def price_tick(price, market="TW", instrument="EQUITY", tick=None):
    if tick is not None:
        return Decimal(str(tick))
    price = Decimal(str(price))
    if market == "US":
        raise HTTPException(422, detail="US tick_size must be supplied for the scenario")
    if instrument == "ETF":
        return Decimal("0.01") if price < 50 else Decimal("0.05")
    for ceiling, step in [(10, "0.01"), (50, "0.05"), (100, "0.1"), (500, "0.5"), (1000, "1")]:
        if price < ceiling:
            return Decimal(step)
    return Decimal("5")


def rounded_price(price, mode="ceil", market="TW", instrument="EQUITY", tick=None):
    price = Decimal(str(price))
    step = price_tick(price, market, instrument, tick)
    rounding = {"ceil": ROUND_CEILING, "floor": ROUND_FLOOR, "round": ROUND_HALF_UP}[mode]
    return max(step, (price / step).to_integral_value(rounding=rounding) * step)


def calculate_cost(payload, market):
    instrument = str(payload.get("instrument_type") or "EQUITY").upper()
    if instrument not in {"EQUITY", "ETF"}:
        raise HTTPException(422, detail="Only equity/ETF long-position scenarios are supported")
    custom = market == "US" or instrument == "ETF"
    if custom and payload.get("cost_confirmed") is not True:
        raise HTTPException(422, detail="Confirm the broker cost scenario first")
    buy = value(payload, "buy_price", positive=True, maximum=1000000000)
    sell = value(payload, "sell_price", buy, positive=True, maximum=1000000000)
    shares = value(payload, "shares", 1000, maximum=100000000, positive=True)
    if shares != shares.to_integral_value():
        raise HTTPException(422, detail="Whole shares are required for this scenario")
    rate = value(payload, "fee_rate", None if market == "US" else "0.001425", maximum="0.25")
    discount = value(payload, "fee_discount", 1, maximum=1)
    minimum = value(payload, "min_fee", None if market == "US" else 20, maximum=1000000000)
    # The reduced Taiwan stock tax is a conditional scenario, not eligibility approval.
    default_tax = "0.0015" if payload.get("is_daytrade", True) is True else "0.003"
    tax_rate = value(payload, "tax_rate", None if custom else default_tax, maximum="0.1")
    extra = value(payload, "sell_extra_fee", 0, maximum=1000000000)
    tick = value(payload, "tick_size", positive=True, maximum=100) if market == "US" else None
    rounding = str(payload.get("rounding_mode") or "floor")
    if rounding not in {"floor", "round", "ceil", "cent"}:
        raise HTTPException(422, detail="Invalid fee rounding")
    quantum = Decimal("0.01") if market == "US" else Decimal(1)
    if minimum % quantum or extra % quantum:
        raise HTTPException(422, detail="Minimum/extra fees must use the currency's minor unit")
    fee_rounding = {"floor": ROUND_FLOOR, "ceil": ROUND_CEILING, "round": ROUND_HALF_UP, "cent": ROUND_CEILING}[rounding]
    money_rounding = ROUND_HALF_UP
    buy_amount = buy * shares
    buy_fee = max(minimum, (buy_amount * rate * discount).quantize(quantum, rounding=fee_rounding))

    def amounts(price):
        sold = price * shares
        sell_fee = max(minimum, (sold * rate * discount).quantize(quantum, rounding=fee_rounding))
        other = (sold * tax_rate + extra).quantize(quantum, rounding=fee_rounding)
        return sold, sell_fee, other, sold - buy_amount - buy_fee - sell_fee - other

    sold, sell_fee, other, net = amounts(sell)
    step = price_tick(buy, market, instrument, tick)

    def candidate(index):
        return rounded_price(buy + step * index, "ceil", market, instrument, tick)

    # Monotone integer search avoids thousands of per-cent recalculations and
    # preserves graduated Taiwan price grids at a band boundary.
    low, high = 0, 1
    while amounts(candidate(high))[3] < 0:
        high *= 2
    while low < high:
        middle = (low + high) // 2
        if amounts(candidate(middle))[3] >= 0:
            high = middle
        else:
            low = middle + 1
    break_even = candidate(low)
    total = buy_fee + sell_fee + other

    def money(number):
        return float(number.quantize(quantum, rounding=money_rounding))

    currency = "USD" if market == "US" else "TWD"
    return {"buy_amount": money(buy_amount), "buy_fee": money(buy_fee), "sell_amount": money(sold),
            "sell_fee": money(sell_fee), "tax": money(other), "total_cost": money(total),
            "gross_profit": money(sold - buy_amount), "net_profit": money(net),
            "net_return_pct": float(net / buy_amount * 100), "per_share_actual_cost": float((buy_amount + total) / shares),
            "break_even_sell_price": float(break_even), "min_profit_ticks": int(((break_even - buy) / step).to_integral_value(rounding=ROUND_CEILING)),
            "tick_size": float(step), "market": market, "currency": currency, "instrument_type": instrument,
            "profile": "broker_scenario" if custom else "tw_equity_daytrade" if payload.get("is_daytrade", True) else "tw_equity",
            "assumptions": {"fee_rate": float(rate), "fee_discount": float(discount), "min_fee": float(minimum),
                            "sell_rate": float(tax_rate), "sell_extra_fee": float(extra), "rounding_mode": rounding},
            "spread_text": f"{currency} scenario: gross {money(sold - buy_amount):,.2f}, costs {money(total):,.2f}, net {money(net):,.2f}.",
            "warning": "成本情境，不含未輸入的券商、借券、融資、匯兌或其他費用；跳數以起始價格級距作參考。",
            "disclaimer": "非投資建議。台股優惠稅率須符合當沖資格；美股帳戶、交割與日內交易權限由券商確認。"}
