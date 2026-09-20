from bp_engine.execution.cli import main

if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "--prediction-version",
                "v3-frozen-paper-v1",
                "--execution-version",
                "paper-execution-v3-frozen-v1",
                "--poll-seconds",
                "5",
            ]
        )
    )
