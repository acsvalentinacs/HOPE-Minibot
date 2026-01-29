# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:50:00 UTC
# Purpose: Verify PriceFeed V1 Contract compliance
# Contract: Strict validation of all V1 invariants
# === END SIGNATURE ===
"""
PRICEFEED V1 CONTRACT VERIFIER

Проверяет соответствие ответа /price-feed/prices контракту V1:

Invariants:
1. Response contains: count, subscribed_count, subscribed, prices
2. subscribed_count == len(subscribed)
3. Each price entry has: price, last_update, age_sec, stale, subscribed
4. stale == True if price is None OR age_sec > MAX_AGE_SEC
5. All subscribed symbols exist in prices dict

Usage:
    python tools/verify_pricefeed_v1.py http://127.0.0.1:8100/price-feed/prices
    python tools/verify_pricefeed_v1.py  # Uses default URL
"""

import argparse
import json
import sys
import urllib.request
from typing import Any, Dict, List, Tuple

# Contract constants
MAX_AGE_SEC = 60.0
REQUIRED_ROOT_FIELDS = ["count", "subscribed_count", "subscribed", "prices"]
REQUIRED_PRICE_FIELDS = ["price", "last_update", "age_sec", "stale", "subscribed"]


class ContractViolation:
    """Single contract violation."""

    def __init__(self, code: str, message: str, severity: str = "ERROR"):
        self.code = code
        self.message = message
        self.severity = severity  # ERROR, WARNING, INFO

    def __str__(self):
        return f"[{self.severity}] {self.code}: {self.message}"


def fetch_pricefeed(url: str, timeout: float = 10.0) -> Tuple[Dict, str]:
    """
    Fetch PriceFeed data from URL.

    Returns:
        Tuple of (data, error_message)
    """
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, ""
    except urllib.error.URLError as e:
        return {}, f"Connection failed: {e}"
    except json.JSONDecodeError as e:
        return {}, f"Invalid JSON response: {e}"
    except Exception as e:
        return {}, f"Fetch error: {e}"


def verify_v1_contract(data: Dict[str, Any]) -> List[ContractViolation]:
    """
    Verify PriceFeed V1 contract.

    Returns list of violations (empty = PASS).
    """
    violations = []

    # === Check 1: Required root fields ===
    for field in REQUIRED_ROOT_FIELDS:
        if field not in data:
            violations.append(ContractViolation(
                "MISSING_ROOT_FIELD",
                f"Required field '{field}' not found in response"
            ))

    if violations:
        return violations  # Can't continue without root fields

    # === Check 2: subscribed_count == len(subscribed) ===
    subscribed_count = data.get("subscribed_count", 0)
    subscribed_list = data.get("subscribed", [])

    if subscribed_count != len(subscribed_list):
        violations.append(ContractViolation(
            "SUBSCRIBED_COUNT_MISMATCH",
            f"subscribed_count={subscribed_count} but len(subscribed)={len(subscribed_list)}"
        ))

    # === Check 3: count consistency ===
    prices = data.get("prices", {})
    if data.get("count", 0) != len(prices):
        violations.append(ContractViolation(
            "COUNT_MISMATCH",
            f"count={data.get('count')} but len(prices)={len(prices)}",
            "WARNING"
        ))

    # === Check 4: All subscribed symbols in prices ===
    for symbol in subscribed_list:
        if symbol not in prices:
            violations.append(ContractViolation(
                "SUBSCRIBED_NOT_IN_PRICES",
                f"Subscribed symbol '{symbol}' not found in prices dict"
            ))

    # === Check 5: Each price entry has required fields ===
    for symbol, price_data in prices.items():
        if not isinstance(price_data, dict):
            violations.append(ContractViolation(
                "INVALID_PRICE_STRUCTURE",
                f"Price data for '{symbol}' is not a dict: {type(price_data)}"
            ))
            continue

        for field in REQUIRED_PRICE_FIELDS:
            if field not in price_data:
                violations.append(ContractViolation(
                    "MISSING_PRICE_FIELD",
                    f"Symbol '{symbol}' missing field '{field}'"
                ))

        # === Check 6: stale invariant ===
        price = price_data.get("price")
        age_sec = price_data.get("age_sec")
        stale = price_data.get("stale")

        if stale is not None:
            # stale should be True if price is None OR age > MAX_AGE_SEC
            should_be_stale = (price is None) or (
                age_sec is not None and age_sec > MAX_AGE_SEC
            )

            if stale != should_be_stale:
                violations.append(ContractViolation(
                    "STALE_INVARIANT_VIOLATED",
                    f"Symbol '{symbol}': stale={stale} but should be {should_be_stale} "
                    f"(price={price}, age_sec={age_sec})"
                ))

        # === Check 7: subscribed field consistency ===
        is_subscribed = price_data.get("subscribed")
        should_be_subscribed = symbol in subscribed_list

        if is_subscribed != should_be_subscribed:
            violations.append(ContractViolation(
                "SUBSCRIBED_FIELD_MISMATCH",
                f"Symbol '{symbol}': subscribed={is_subscribed} but should be {should_be_subscribed}",
                "WARNING"
            ))

    return violations


def print_report(data: Dict, violations: List[ContractViolation]) -> None:
    """Print verification report."""
    print("=" * 60)
    print("PRICEFEED V1 CONTRACT VERIFICATION")
    print("=" * 60)
    print()

    # Summary
    subscribed = data.get("subscribed", [])
    prices = data.get("prices", {})

    print(f"Subscribed symbols: {len(subscribed)}")
    print(f"Price entries: {len(prices)}")

    # Price status breakdown
    valid_prices = 0
    stale_prices = 0
    null_prices = 0

    for symbol, pdata in prices.items():
        if isinstance(pdata, dict):
            if pdata.get("price") is None:
                null_prices += 1
            elif pdata.get("stale"):
                stale_prices += 1
            else:
                valid_prices += 1

    print(f"Valid prices: {valid_prices}")
    print(f"Stale prices: {stale_prices}")
    print(f"Null prices: {null_prices}")
    print()

    # Violations
    errors = [v for v in violations if v.severity == "ERROR"]
    warnings = [v for v in violations if v.severity == "WARNING"]

    if not violations:
        print("RESULT: PASS (no contract violations)")
    else:
        print(f"RESULT: {'FAIL' if errors else 'WARN'}")
        print(f"Errors: {len(errors)}, Warnings: {len(warnings)}")
        print()

        if errors:
            print("ERRORS:")
            for v in errors:
                print(f"  - {v}")
            print()

        if warnings:
            print("WARNINGS:")
            for v in warnings:
                print(f"  - {v}")
            print()

    # Sample prices
    print("-" * 60)
    print("SAMPLE PRICES:")
    for symbol, pdata in list(prices.items())[:5]:
        if isinstance(pdata, dict):
            price = pdata.get("price")
            stale = pdata.get("stale")
            age = pdata.get("age_sec", "?")
            status = "STALE" if stale else "OK"
            if price is not None:
                print(f"  {symbol}: {price:.6f} | age={age}s | {status}")
            else:
                print(f"  {symbol}: NULL | {status}")


def main():
    parser = argparse.ArgumentParser(description="Verify PriceFeed V1 Contract")
    parser.add_argument(
        "url",
        nargs="?",
        default="http://127.0.0.1:8100/price-feed/prices",
        help="PriceFeed URL (default: http://127.0.0.1:8100/price-feed/prices)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    args = parser.parse_args()

    # Fetch data
    data, error = fetch_pricefeed(args.url)

    if error:
        if args.json:
            print(json.dumps({"result": "FAIL", "error": error}))
        else:
            print(f"FAIL: {error}")
        sys.exit(2)

    # Verify contract
    violations = verify_v1_contract(data)

    if args.json:
        result = {
            "result": "PASS" if not any(v.severity == "ERROR" for v in violations) else "FAIL",
            "subscribed_count": data.get("subscribed_count", 0),
            "prices_count": len(data.get("prices", {})),
            "violations": [{"code": v.code, "message": v.message, "severity": v.severity} for v in violations]
        }
        print(json.dumps(result, indent=2))
    else:
        print_report(data, violations)

    # Exit code
    has_errors = any(v.severity == "ERROR" for v in violations)
    sys.exit(2 if has_errors else 0)


if __name__ == "__main__":
    main()
