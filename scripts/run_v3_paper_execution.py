from bp_engine.execution.cli import main

if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "--prediction-version",
                "v3-frozen-paper-v1",
                "--execution-version",
                "paper-execution-v3-frozen-v1",
                "--starting-cash-usd",
                "100.00",
                "--target-notional-usd",
                "5.00",
                "--latency-ms",
                "250",
                "--order-ttl-ms",
                "2000",
                "--share-precision",
                "6",
                "--poll-seconds",
                "5",
            ]
        )
    )
