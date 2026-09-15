#!/usr/bin/env python3
"""Simple health check for payment_server.py

Usage:
  python3 check_payment_server.py [--host 127.0.0.1] [--port 8402]

Verifies:
  - Server is reachable on the network
  - / endpoint returns server info (status 200)
  - /activity endpoint returns stats (status 200)
  - /task endpoint accepts prompts and returns payment info (status 402)

Exit code 0 = all checks pass, 1 = any check failed.
"""
import argparse
import json
import sys
from urllib.error import URLError
from urllib.request import urlopen, Request


def check_server(host: str, port: int) -> bool:
    base_url = f"http://{host}:{port}"
    checks = [
        ("GET /", f"{base_url}/", "GET", None),
        ("GET /activity", f"{base_url}/activity", "GET", None),
        ("POST /task (payment required)", f"{base_url}/task", "POST", '{"prompt": "test"}'),
    ]

    all_passed = True
    for name, url, method, body in checks:
        try:
            req = Request(url, method=method)
            req.add_header("Content-Type", "application/json")
            with urlopen(req, data=body.encode() if body else None, timeout=5) as resp:
                status = resp.status
                data = json.loads(resp.read().decode())

                # / and /activity should return 200; /task should return 402
                expected_status = 402 if "POST /task" in name else 200
                if status == expected_status:
                    print(f"✓ {name}: {status} OK")
                    # Verify required fields
                    if "service" not in data and "activity" not in url:
                        print(f"  Warning: response missing 'service' field")
                else:
                    print(f"✗ {name}: expected {expected_status}, got {status}")
                    all_passed = False
        except URLError as e:
            print(f"✗ {name}: network error: {e}")
            all_passed = False
        except json.JSONDecodeError as e:
            print(f"✗ {name}: invalid JSON response: {e}")
            all_passed = False
        except Exception as e:
            print(f"✗ {name}: {e}")
            all_passed = False

    return all_passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Health check for payment_server.py",
        epilog="Exit code 0 if all checks pass, 1 if any fail.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8402, help="Server port (default: 8402)")
    args = parser.parse_args()

    print(f"Checking payment server at {args.host}:{args.port}...")
    if check_server(args.host, args.port):
        print("\nAll checks passed! Payment server is reachable and accepting payments.")
        sys.exit(0)
    else:
        print("\nSome checks failed. See errors above.")
        sys.exit(1)
