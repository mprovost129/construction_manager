from decimal import ROUND_HALF_UP, Decimal

MONEY_QUANTUM = Decimal('0.01')


def money(value):
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
