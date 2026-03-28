#!/usr/bin/env python3
"""
Simplified Trading Bot for Binance Futures Testnet (USDT-M)
Complete single-file implementation for Python Developer Task
"""

import argparse
import hashlib
import hmac
import json
import logging
import sys
import time
import math
import os
from typing import Dict, Optional, Any

import requests


# ------------------------------
# Logging Configuration
# ------------------------------
def setup_logging(log_file: str = "trading_bot.log") -> None:
    """Configure logging to file and console."""
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # File handler - detailed logs
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(file_formatter)

    # Console handler - info and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Suppress noisy external logs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ------------------------------
# Binance Futures Client
# ------------------------------
class BinanceFuturesClient:
    """Wrapper for Binance Futures Testnet API."""

    BASE_URL = "https://testnet.binancefuture.com"
    API_VERSION = "fapi"

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/json",
            }
        )

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature for signed endpoints."""
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _request(
        self, method: str, endpoint: str, params: Optional[Dict] = None, signed: bool = False
    ) -> Dict:
        """Make API request with logging and error handling."""
        url = f"{self.BASE_URL}/{self.API_VERSION}/{endpoint}"
        params = params or {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._generate_signature(params)

        logger.debug(f"Request: {method} {url} | Params: {params}")

        try:
            response = self.session.request(method, url, params=params)
            response.raise_for_status()
            data = response.json()
            logger.debug(f"Response: {data}")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response body: {e.response.text}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response: {e}")
            raise

    def get_exchange_info(self, symbol: str) -> Dict:
        """Get exchange info for a symbol to validate quantity/price filters."""
        params = {"symbol": symbol}
        return self._request("GET", "exchangeInfo", params)

    def get_account_balance(self) -> Dict:
        """Get account balance."""
        return self._request("GET", "account", signed=True)

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> Dict:
        """Place an order on Binance Futures."""
        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
        }
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("Price is required for LIMIT orders")
            params["price"] = price
            params["timeInForce"] = "GTC"

        logger.info(f"Placing order: {params}")
        return self._request("POST", "order", params, signed=True)


# ------------------------------
# Input Validation
# ------------------------------
def validate_input(
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float],
    client: BinanceFuturesClient,
) -> Dict[str, Any]:
    """Validate user input against exchange filters."""
    # Basic validation
    if side.upper() not in ["BUY", "SELL"]:
        raise ValueError("side must be BUY or SELL")
    if order_type.upper() not in ["MARKET", "LIMIT"]:
        raise ValueError("order_type must be MARKET or LIMIT")
    if order_type.upper() == "LIMIT" and price is None:
        raise ValueError("price is required for LIMIT orders")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    # Fetch symbol info from exchange
    try:
        exchange_info = client.get_exchange_info(symbol.upper())
        symbol_info = None
        for s in exchange_info.get("symbols", []):
            if s["symbol"] == symbol.upper():
                symbol_info = s
                break

        if not symbol_info:
            raise ValueError(f"Symbol {symbol} not found")

        # Quantity filters
        for filt in symbol_info["filters"]:
            if filt["filterType"] == "LOT_SIZE":
                min_qty = float(filt["minQty"])
                max_qty = float(filt["maxQty"])
                step_size = float(filt["stepSize"])

                if quantity < min_qty:
                    raise ValueError(f"Quantity {quantity} below minimum {min_qty}")
                if quantity > max_qty:
                    raise ValueError(f"Quantity {quantity} above maximum {max_qty}")

                # Round to step size precision
                precision = int(round(-math.log10(step_size)))
                quantity = round(quantity, precision)
                logger.debug(f"Adjusted quantity to {quantity} based on step size {step_size}")

            if order_type.upper() == "LIMIT" and filt["filterType"] == "PRICE_FILTER":
                min_price = float(filt["minPrice"])
                max_price = float(filt["maxPrice"])
                tick_size = float(filt["tickSize"])

                if price < min_price:
                    raise ValueError(f"Price {price} below minimum {min_price}")
                if price > max_price:
                    raise ValueError(f"Price {price} above maximum {max_price}")

                # Round to tick size
                precision = int(round(-math.log10(tick_size)))
                price = round(price, precision)
                logger.debug(f"Adjusted price to {price} based on tick size {tick_size}")

    except Exception as e:
        logger.error(f"Validation error: {e}")
        raise

    return {
        "symbol": symbol.upper(),
        "side": side.upper(),
        "order_type": order_type.upper(),
        "quantity": quantity,
        "price": price,
    }


# ------------------------------
# Order Execution & Output
# ------------------------------
def execute_order(client: BinanceFuturesClient, validated_params: Dict) -> Dict:
    """Execute the order and return response."""
    try:
        response = client.place_order(
            symbol=validated_params["symbol"],
            side=validated_params["side"],
            order_type=validated_params["order_type"],
            quantity=validated_params["quantity"],
            price=validated_params.get("price"),
        )
        logger.info(f"Order placed successfully: {response}")
        return response
    except Exception as e:
        logger.error(f"Failed to place order: {e}")
        raise


def print_order_summary(request_params: Dict, response: Dict) -> None:
    """Print clear order summary and response details."""
    print("\n" + "=" * 50)
    print("ORDER REQUEST SUMMARY")
    print("=" * 50)
    print(f"Symbol:     {request_params['symbol']}")
    print(f"Side:       {request_params['side']}")
    print(f"Type:       {request_params['order_type']}")
    print(f"Quantity:   {request_params['quantity']}")
    if request_params.get("price"):
        print(f"Price:      {request_params['price']}")

    print("\n" + "=" * 50)
    print("ORDER RESPONSE DETAILS")
    print("=" * 50)
    print(f"Order ID:   {response.get('orderId')}")
    print(f"Status:     {response.get('status')}")
    print(f"Exec Qty:   {response.get('executedQty')}")
    print(f"Avg Price:  {response.get('avgPrice') or 'N/A'}")
    print(f"Client ID:  {response.get('clientOrderId')}")
    print("=" * 50)

    if response.get("status") in ["NEW", "FILLED", "PARTIALLY_FILLED"]:
        print("✅ SUCCESS: Order placed successfully.")
    else:
        print("⚠️  WARNING: Order status may require further check.")


# ------------------------------
# CLI Entry Point
# ------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Simplified Trading Bot for Binance Futures Testnet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Market BUY order
  python trading_bot.py --api-key YOUR_KEY --api-secret YOUR_SECRET --symbol BTCUSDT --side BUY --type MARKET --quantity 0.001

  # Limit SELL order
  python trading_bot.py --api-key YOUR_KEY --api-secret YOUR_SECRET --symbol ETHUSDT --side SELL --type LIMIT --quantity 0.01 --price 2000

  # Using environment variables (recommended):
  export BINANCE_API_KEY=your_key
  export BINANCE_API_SECRET=your_secret
  python trading_bot.py --symbol BTCUSDT --side BUY --type MARKET --quantity 0.001
        """,
    )
    parser.add_argument(
        "--api-key",
        help="Binance Futures Testnet API Key (can also use env BINANCE_API_KEY)",
    )
    parser.add_argument(
        "--api-secret",
        help="Binance Futures Testnet API Secret (can also use env BINANCE_API_SECRET)",
    )
    parser.add_argument(
        "--symbol", required=True, help="Trading pair symbol (e.g., BTCUSDT)"
    )
    parser.add_argument(
        "--side", required=True, choices=["BUY", "SELL"], help="Order side"
    )
    parser.add_argument(
        "--type", required=True, choices=["MARKET", "LIMIT"], help="Order type"
    )
    parser.add_argument(
        "--quantity", required=True, type=float, help="Order quantity"
    )
    parser.add_argument(
        "--price", type=float, help="Price for LIMIT orders (required for LIMIT)"
    )
    parser.add_argument(
        "--log-file", default="trading_bot.log", help="Log file path"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_file)

    # Get API credentials from args or environment
    api_key = args.api_key or os.environ.get("BINANCE_API_KEY")
    api_secret = args.api_secret or os.environ.get("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        logger.error("API key and secret must be provided via args or environment")
        print(
            "Error: API credentials missing. Use --api-key/--api-secret or set BINANCE_API_KEY/BINANCE_API_SECRET env vars."
        )
        sys.exit(1)

    # Initialize client
    client = BinanceFuturesClient(api_key, api_secret)

    try:
        # Validate and adjust inputs
        validated = validate_input(
            symbol=args.symbol,
            side=args.side,
            order_type=args.type,
            quantity=args.quantity,
            price=args.price,
            client=client,
        )

        # Execute order
        response = execute_order(client, validated)

        # Print summary
        print_order_summary(validated, response)

        logger.info("Order flow completed successfully")

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        print(f"\n❌ Validation failed: {e}")
        sys.exit(2)
    except requests.exceptions.RequestException as e:
        logger.error(f"Network/API error: {e}")
        print(f"\n❌ Network/API error: {e}")
        sys.exit(3)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(4)


if __name__ == "__main__":
    main()
